"""`microclaw serve` — the interactive web GUI (design/15).

One process owns one live `MicroscopeController` + `SafetyGuard` + history, the
same three objects the terminal REPL builds. The browser POSTs a prompt and the
server streams that turn's events back on the POST response as Server-Sent
Events (design/16 §3); `/api/history` stays the source of truth and reconciles
the page once the stream ends.

Three properties are load-bearing, because this endpoint moves real hardware:

* **Authenticated remote access.** Binding beyond 127.0.0.1 requires both
  `--allow-remote` and an explicitly trusted TLS-terminating proxy. Remote API
  calls require a bearer token or an in-memory paired-browser session.
* **One operator.** The controller and history are shared mutable state and the
  microscope is physically single-user, so a turn holds `session.lock` and a
  second prompt is refused (409) rather than interleaved.
* **State-changing endpoints stay on POST.** Streaming a turn from a GET would
  let any page the operator has open drive the stage with an `<img src=...>`:
  a GET is not preflighted and carries no `Origin` for the middleware to refuse.
  That is why this is POST-SSE rather than an `EventSource`.
"""
import asyncio
from collections import OrderedDict, deque
import datetime
import hmac
import json
import os
import queue
import secrets
import socket
import shutil
import sys
import tempfile
import threading
import time
import uuid
import webbrowser
from enum import Enum
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    Response,
    StreamingResponse,
)
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from microclaw import config, credentials, shortcut, tools, updates
from microclaw.authorization import RigAuthorizationError, validate_live_rig
from microclaw.conversation import AuditLog, ConversationStore, prune_transcripts
from microclaw.agent import (
    DEFAULT_MODEL,
    known_models,
    resolve_model,
    run_agent_iter,
    set_api_key,
)
from microclaw.assets import icon_bytes, load_page
from microclaw.config import ConfigValidationResult, ParsedSafetyConfig
from microclaw.controller import MicroscopeController
from microclaw.rig_inventory import enumerate_rig
from microclaw.setup_tools import (
    SETUP_TOOL_REGISTRY, SETUP_TOOL_SCHEMAS, SetupDraft, SetupWriteCapability,
)
from microclaw.safety import SafetyGuard, SafetyViolation
from microclaw.tools_schema import TOOLS_CACHED


class SessionMode(Enum):
    SETUP = "setup"
    NORMAL = "normal"


SETUP_TOOL_NAMES = frozenset({
    "list_stage_axes", "read_stage_positions", "record_proposed_stage_bound",
    "set_proposed_acquisition_prompts", "review_security_config",
    "write_security_config",
})
SETUP_FIRST_MESSAGE = (
    "Security bounds are not set. Before Microclaw can control hardware, we need to "
    "record safe travel bounds for every stage and choose large-acquisition warning "
    "thresholds. I can guide you through it; acquisition and hardware-write tools "
    "stay unavailable until setup is complete and Microclaw is restarted."
)

# Loopback names. Anything else needs --allow-remote.
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

# Sentinel: the turn thread is finished and the SSE stream should end.
_TURN_DONE = object()

# A confirmation nobody answers must never become a yes. Module attributes,
# read at call time, so tests can exercise the deadline with an injected clock
# and a short poll rather than a sleep.
CONFIRM_TIMEOUT_S = 300.0
CONFIRM_POLL_S = 0.5
_monotonic = time.monotonic

REMOTE_TOKEN_MIN_CHARS = 32
GLOBAL_JSON_LIMIT = 64 * 1024
PROMPT_BODY_LIMIT = 256 * 1024
PAIR_TTL_S = 15 * 60
SESSION_TTL_S = 12 * 60 * 60
RATE_WINDOW_S = 60.0
RATE_MAX_FAILURES = 10
RATE_MAX_PAIR_ATTEMPTS = 10
RATE_MAX_UPDATE_CHECKS = 3
RATE_CLIENTS_MAX = 256
SESSIONS_MAX = 1024
SESSION_COOKIE = "microclaw_session"


class _RateLimiter:
    """Bounded per-client sliding-window limiter."""

    def __init__(self, limit: int):
        self.limit = limit
        self.clients: OrderedDict[str, deque[float]] = OrderedDict()

    def allow(self, client: str) -> bool:
        now = _monotonic()
        hits = self.clients.pop(client, deque())
        while hits and now - hits[0] >= RATE_WINDOW_S:
            hits.popleft()
        if len(hits) >= self.limit:
            self.clients[client] = hits
            return False
        hits.append(now)
        self.clients[client] = hits
        while len(self.clients) > RATE_CLIENTS_MAX:
            self.clients.popitem(last=False)
        return True


class RemoteAuth:
    """Process-local bearer, pairing codes, and opaque browser sessions."""

    def __init__(self, token: str):
        self.token = token
        self.codes: list[tuple[str, float]] = []
        self.sessions: OrderedDict[str, tuple[str, float]] = OrderedDict()
        self.failures = _RateLimiter(RATE_MAX_FAILURES)
        self.pair_attempts = _RateLimiter(RATE_MAX_PAIR_ATTEMPTS)

    def mint_code(self) -> str:
        now = _monotonic()
        self.codes = [(c, expiry) for c, expiry in self.codes if expiry > now][-4:]
        code = secrets.token_urlsafe(16)
        self.codes.append((code, now + PAIR_TTL_S))
        return code

    def consume_code(self, supplied: str) -> bool:
        now = _monotonic()
        matched = False
        remaining = []
        for code, expiry in self.codes:
            valid = expiry > now and hmac.compare_digest(code, supplied)
            if valid and not matched:
                matched = True
            elif expiry > now:
                remaining.append((code, expiry))
        self.codes = remaining
        return matched

    def valid_bearer(self, header: str | None) -> bool:
        if not header or not header.startswith("Bearer "):
            return False
        supplied = header[7:]
        return bool(supplied) and hmac.compare_digest(supplied, self.token)

    def mint_session(self) -> tuple[str, str]:
        value = secrets.token_urlsafe(32)
        identity = secrets.token_hex(8)
        self.sessions[value] = (identity, _monotonic() + SESSION_TTL_S)
        while len(self.sessions) > SESSIONS_MAX:
            self.sessions.popitem(last=False)
        return value, identity

    def valid_session(self, supplied: str | None) -> str | None:
        if not supplied:
            return None
        now = _monotonic()
        found = None
        expired = []
        for value, (identity, expiry) in self.sessions.items():
            if expiry <= now:
                expired.append(value)
            elif hmac.compare_digest(value, supplied):
                found = identity
        for value in expired:
            self.sessions.pop(value, None)
        return found


class _Pending:
    """One confirmation waiting on the operator, readable by /api/confirm."""

    def __init__(self, id: str, summary: str, kind: str, subject: str | None):
        self.id = id
        self.summary = summary
        self.kind = kind
        self.subject = subject
        self.grant_id: str | None = None
        # threading queue, not asyncio: confirm() blocks on the turn thread
        # while /api/confirm answers from the event loop.
        self.reply: queue.Queue = queue.Queue(maxsize=1)


class Prompt(BaseModel):
    message: str


class Confirm(BaseModel):
    id: str
    approve: bool | str


class Key(BaseModel):
    key: str
    persist: bool = True


class Model(BaseModel):
    model: str


class UpdateDismissal(BaseModel):
    action: str
    commit: str


def _jsonable(history: list[dict]) -> list[dict]:
    """History holds Anthropic SDK content blocks, which json can't encode.

    Round-trip through the same `default` hook `write_history` uses, so the API
    and the saved JSON agree block for block.
    """
    from microclaw.__main__ import json_default

    return json.loads(json.dumps(history, default=json_default))


def _declared_artifacts(history: list[dict]) -> set[str]:
    """Every path a tool in this session declared as an artifact.

    The allowlist behind `/api/artifact`. Tools that write a file return
    `{"artifact": {"kind": ..., "path": ...}}`; those dicts are built by
    microclaw's own code from the path the tool actually wrote, so the model
    cannot name a file here that no tool produced.
    """
    paths: set[str] = set()
    for message in history:
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if not (isinstance(block, dict) and block.get("type") == "tool_result"):
                continue
            payload = block.get("content")
            if isinstance(payload, list):
                # Image-returning tools send [text block, image block]; the JSON
                # payload, if any, is in the text block.
                payload = next(
                    (b.get("text") for b in payload
                     if isinstance(b, dict) and b.get("type") == "text"),
                    None,
                )
            if not isinstance(payload, str):
                continue
            try:
                result = json.loads(payload)
            except ValueError:
                continue
            artifact = result.get("artifact") if isinstance(result, dict) else None
            if isinstance(artifact, dict) and isinstance(artifact.get("path"), str):
                paths.add(artifact["path"])
    return paths


def _durable_history(session) -> list[dict]:
    """Return the full audit record, with a fallback for legacy/test sessions."""
    store = getattr(session, "store", None)
    return store.audit.records if store is not None else _jsonable(session.history)


def _add_audit_secret(session, secret: str | None) -> None:
    """Register a credential with every audit owned by a session."""
    store = getattr(session, "store", None)
    if store is not None:
        store.audit.add_secret(secret)
    confirmation_audit = getattr(session, "confirmation_audit", None)
    if confirmation_audit is not None:
        confirmation_audit.add_secret(secret)


def _sse(event: dict) -> str:
    """One agent event as an SSE frame.

    `json.dumps` never emits a raw newline, so a single `data:` line is safe.
    """
    from microclaw.__main__ import json_default

    return f"data: {json.dumps(event, default=json_default)}\n\n"


class Session:
    """One live microscope + conversation, shared across requests."""

    def __init__(self, args, parsed_safety: ParsedSafetyConfig):
        self.parsed_safety = parsed_safety
        guard = SafetyGuard(self.parsed_safety.constraints)
        print("Connecting to Micro-Manager...")
        ctrl = MicroscopeController(port=args.port, guard=guard)
        if not ctrl.is_connected():
            sys.exit(
                "Could not connect to Micro-Manager. "
                "Is the ZMQ server enabled in Tools → Options?"
            )
        try:
            validate_live_rig(ctrl, self.parsed_safety, guard=guard)
        except RigAuthorizationError as exc:
            sys.exit(str(exc))
        self.ctrl = ctrl
        self.guard = guard
        self.mode = SessionMode.NORMAL
        self.tool_schemas = TOOLS_CACHED
        self.tool_registry = tools.TOOL_REGISTRY
        self._initialize(args)

    def _initialize(self, args):
        self.model = args.model
        self.history: list[dict] = []
        self.history_fn = (
            f"{datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_microclaw_history.jsonl"
        )
        self.save = args.save_history
        removed = prune_transcripts(".", getattr(args, "history_retention_days", None))
        for path in removed:
            print(f"[microclaw] Pruned transcript: {path}")
        self.store = ConversationStore(AuditLog(self.history_fn, enabled=self.save))
        # Editing credentials from a browser is offered only on loopback, the
        # same rule design/15 sets for the safety-config editor (v2).
        self.editable = args.host in LOCAL_HOSTS
        self.lock = asyncio.Lock()  # one operator at a time
        # Set by POST /api/stop, polled by run_agent_iter at round and tool
        # boundaries. threading.Event, not asyncio: the turn runs on a thread.
        self.cancel = threading.Event()
        # The browser confirmation gate (design/21 F1). _emit is set per turn
        # by run_turn; pending is read by GET /api/confirm so a page reload can
        # re-surface a banner the one-shot stream already delivered.
        self._emit = None
        self.pending: _Pending | None = None
        self.audit_records: list[dict] = []
        confirmation_path = self.history_fn.replace("_history.jsonl", "_confirmations.jsonl")
        self.confirmation_audit = AuditLog(confirmation_path, enabled=self.save)
        self.current_identity = "loopback"

        # env > keyring > file; a key found in a store is pushed into the
        # environment now so the first turn doesn't have to look for it.
        key, source = credentials.load_api_key()
        if key:
            _add_audit_secret(self, key)
            set_api_key(key)
            print(f"Anthropic API key: {credentials.mask(key)} (from {source})")
        else:
            print("No Anthropic API key found — set one from the browser.")


    def _audit_confirmation(
        self, *, summary: str, kind: str, subject: str | None,
        decision: str, confirmation_id: str, identity: str,
        grant_id: str | None = None, grant_identity: str | None = None,
    ) -> bool:
        """Append one human, automatic, or revocation confirmation event."""
        record = {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "identity": identity,
            "confirmation_id": confirmation_id,
            "kind": kind,
            "decision": decision,
            "summary": summary,
        }
        if subject is not None:
            record["subject"] = subject
        if grant_id is not None:
            record["grant_id"] = grant_id
        if grant_identity is not None:
            record["grant_identity"] = grant_identity
        # Redact once, then use that one copy everywhere. AuditLog.append
        # returns a redacted copy and leaves its argument untouched.
        confirmation_audit = getattr(self, "confirmation_audit", None)
        if confirmation_audit is not None:
            record = confirmation_audit.append(record)
        self.audit_records.append(record)
        print("[microclaw] Confirmation audit: " + json.dumps(record, sort_keys=True))
        return decision.startswith("approved") or decision.startswith("auto-approved")

    def confirm(
        self, summary: str, kind: str = "action", subject: str | None = None
    ) -> bool:
        """Route a confirmation to the browser. Runs on the turn thread.

        Installed as tools.CONFIRM_FN by serve(), because the operator of a
        browser session is looking at the browser — a blocking input() on the
        serve process's stdin waits on a console nobody is watching
        (design/21 F1). Default deny, three ways: no stream bound, the Stop
        button, or the deadline. stdout keeps the summary and the decision —
        under --allow-remote it is the only record the person standing at the
        microscope can see.
        """
        emit = self._emit
        confirmation_id = uuid.uuid4().hex
        identity = self.current_identity

        def decided(
            decision: str, decided_by: str = identity, grant_id: str | None = None,
            grant_identity: str | None = None,
        ) -> bool:
            return self._audit_confirmation(
                summary=summary, kind=kind, subject=subject,
                decision=decision, confirmation_id=confirmation_id,
                identity=decided_by, grant_id=grant_id,
                grant_identity=grant_identity,
            )

        grant = tools.SESSION_GRANTS.granted(kind, subject)
        if grant is not None:
            # `identity` is the operator whose turn caused this action;
            # `grant_identity` separately preserves who authored the standing
            # approval. Post-incident review needs both when remote operators
            # hand a session over.
            return decided(
                f"auto-approved:{grant['id']}", identity, grant["id"],
                grant_identity=grant["identity"],
            )

        if emit is None:
            return decided("declined:no-stream")           # no stream: deny
        p = _Pending(confirmation_id, summary, kind, subject)
        self.pending = p
        print(f"\n[microclaw] Confirmation required ({kind}):\n{summary}")
        emit({"type": "confirm_request", "id": p.id,
              "summary": summary, "kind": kind, "subject": subject,
              "grantable": tools.SessionGrants.is_grantable(kind, subject)})
        try:
            deadline = _monotonic() + CONFIRM_TIMEOUT_S
            while _monotonic() < deadline:
                if self.cancel.is_set():
                    print("[microclaw] Turn stopped; confirmation declined.")
                    return decided("declined:stopped")     # Stop button: deny
                try:
                    answer, responder = p.reply.get(timeout=CONFIRM_POLL_S)
                except queue.Empty:
                    continue
                if answer == "session":
                    grant = tools.SESSION_GRANTS.granted(kind, subject)
                    if grant is None:
                        raise RuntimeError("Browser session grant disappeared before audit.")
                    print("[microclaw] Approved for this session from browser.")
                    return decided(
                        f"approved:session:{grant['id']}", responder, grant["id"],
                        grant_identity=grant["identity"],
                    )
                approved = answer is True
                print(f"[microclaw] {'Approved' if approved else 'Declined'}"
                      f" from browser.")
                return decided("approved" if approved else "declined", responder)
            print("[microclaw] Confirmation timed out; declined.")
            return decided("declined:timeout")             # deadline: deny
        finally:
            self.pending = None
            emit({"type": "confirm_resolved", "id": p.id})


class SetupSession(Session):
    """A connected session whose dispatcher exposes no normal capabilities."""

    def __init__(self, args, config_result: ConfigValidationResult):
        print("Connecting to Micro-Manager in setup mode...")
        ctrl = MicroscopeController(port=args.port, guard=None)
        if not ctrl.is_connected():
            sys.exit(
                "Could not connect to Micro-Manager. "
                "Is the ZMQ server enabled in Tools → Options?"
            )
        self.ctrl = ctrl
        self.guard = None
        self.parsed_safety = None
        self.config_result = config_result
        self.mode = SessionMode.SETUP
        may_write = getattr(args, "setup_write_security_config", False)
        self.tool_schemas = (
            SETUP_TOOL_SCHEMAS if may_write else
            [schema for schema in SETUP_TOOL_SCHEMAS
             if schema["name"] != "write_security_config"]
        )
        self.tool_registry = SETUP_TOOL_REGISTRY
        self.inventory = enumerate_rig(ctrl.core)
        self.setup_draft = SetupDraft(self.inventory)
        ctrl._microclaw_setup_draft = self.setup_draft
        ctrl._microclaw_setup_write_capability = SetupWriteCapability(
            enabled=may_write,
        )
        self._initialize(args)
        content = SETUP_FIRST_MESSAGE
        if config_result.classification == "blocked":
            content += (
                f"\n\nAn existing security config at {config_result.path} is invalid or "
                "unreviewed. Setup cannot overwrite or delete it. Move it aside or "
                "repair it deliberately, then restart this one-time setup command."
            )
        message = {"role": "assistant", "content": content}
        self.history.append(message)
        self.store.append(message)


def build_session(args, config_result: ConfigValidationResult | None = None):
    """Use a valid reviewed config, otherwise open restricted setup."""
    path = Path(args.safety_config) if args.safety_config else config.default_safety_config()
    result = config_result if config_result is not None else config.validate_safety_config(path)
    if result.can_start_live_validation:
        session = Session(args, result.parsed)
        session.safety_config_path = result.path
        return session
    if result.classification == "blocked":
        print(
            f"Existing security bounds at {result.path} are not valid and reviewed. "
            "Restricted setup will open, but it will not overwrite or delete that "
            "file. Move it aside or repair it deliberately, then restart setup."
        )
    session = SetupSession(args, result)
    session.safety_config_path = result.path
    return session


def build_app(session, *, remote: bool = False, api_token: str | None = None,
              behind_tls_proxy: bool = False, auth_state: RemoteAuth | None = None) -> FastAPI:
    app = FastAPI(title="Microclaw")
    update_job_lock = threading.Lock()
    update_job = {"running": False}
    update_checks = _RateLimiter(RATE_MAX_UPDATE_CHECKS)
    page = load_page("serve.html")
    if session.mode is SessionMode.SETUP:
        page = page.replace(
            'class="banner hidden" id="setup-banner"',
            'class="banner" id="setup-banner"',
        )
    icon = icon_bytes()
    if remote:
        if not api_token:
            raise RuntimeError("remote mode requires an API token")
        auth_state = auth_state or RemoteAuth(api_token)
        _add_audit_secret(session, api_token)

    def client_address(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    @app.middleware("http")
    async def authenticate_remote(request: Request, call_next):
        if not remote:
            request.state.identity = "loopback"
            return await call_next(request)
        assert auth_state is not None
        if behind_tls_proxy and request.headers.get("x-forwarded-proto", "").lower() != "https":
            return JSONResponse({"detail": "HTTPS required."}, status_code=403)
        path = request.url.path
        if not path.startswith("/api/"):
            return await call_next(request)

        # JSON endpoints are deliberately small. Prompts get 256 KiB for long
        # experimental context; every other JSON body gets 64 KiB.
        limit = PROMPT_BODY_LIMIT if path == "/api/prompt" else GLOBAL_JSON_LIMIT
        if request.method in {"POST", "PUT", "PATCH"}:
            length = request.headers.get("content-length")
            if length and (not length.isdigit() or int(length) > limit):
                return JSONResponse({"detail": "Request body too large."}, status_code=413)
            body = await request.body()
            if len(body) > limit:
                return JSONResponse({"detail": "Request body too large."}, status_code=413)

        client = client_address(request)
        bearer = auth_state.valid_bearer(request.headers.get("authorization"))
        paired_id = auth_state.valid_session(request.cookies.get(SESSION_COOKIE))
        if path == "/api/pair" and request.method == "POST":
            return await call_next(request)
        if path == "/api/pair/code" and request.method == "POST":
            paired_id = None  # this endpoint is bearer-only by contract
        if bearer:
            request.state.identity = "bearer"
        elif paired_id:
            request.state.identity = f"paired:{paired_id[:8]}"
        else:
            if not auth_state.failures.allow(client):
                return JSONResponse({"detail": "Too many authentication failures."}, status_code=429)
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        return await call_next(request)

    @app.middleware("http")
    async def block_cross_origin(request: Request, call_next):
        """Any page the operator visits can POST to a localhost server.

        Browsers preflight a cross-origin JSON POST and we send no CORS headers,
        so those are already refused — but a form post with a simple content
        type is not preflighted. Reject anything carrying a foreign Origin so a
        stray tab cannot drive the microscope.
        """
        origin = request.headers.get("origin")
        expected = str(request.base_url).rstrip("/")
        if remote and behind_tls_proxy:
            expected = "https://" + request.headers.get("host", "")
        if origin and origin != expected:
            return JSONResponse({"detail": "Cross-origin request refused."}, status_code=403)
        return await call_next(request)

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return page

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        # Read once at startup, not per request; the file cannot change while
        # the process lives. `image/x-icon` is what every browser expects here.
        return Response(icon, media_type="image/x-icon")

    @app.post("/api/pair")
    async def pair(request: Request):
        if not remote:
            raise HTTPException(404, "Not found.")
        assert auth_state is not None
        client = client_address(request)
        if not auth_state.pair_attempts.allow(client):
            raise HTTPException(429, "Too many pairing attempts.")
        try:
            body = await request.json()
        except ValueError:
            body = {}
        code = body.get("code") if isinstance(body, dict) else None
        if not isinstance(code, str) or not auth_state.consume_code(code):
            raise HTTPException(401, "Unauthorized")
        value, _ = auth_state.mint_session()
        _add_audit_secret(session, value)
        response = JSONResponse({"paired": True})
        response.set_cookie(
            SESSION_COOKIE, value, max_age=SESSION_TTL_S,
            expires=(datetime.datetime.now(datetime.timezone.utc)
                     + datetime.timedelta(seconds=SESSION_TTL_S)),
            path="/", secure=True, httponly=True, samesite="strict",
        )
        return response

    @app.post("/api/pair/code")
    async def mint_pair_code(request: Request):
        if not remote:
            raise HTTPException(404, "Not found.")
        assert auth_state is not None
        if not auth_state.pair_attempts.allow(client_address(request)):
            raise HTTPException(429, "Too many pairing requests.")
        code = auth_state.mint_code()
        _add_audit_secret(session, code)
        return JSONResponse({"code": code, "expires_in": PAIR_TTL_S})

    @app.get("/api/history")
    async def get_history(cursor: str | None = None, limit: int = 100):
        records = _durable_history(session)
        if limit < 1:
            raise HTTPException(400, "limit must be positive")
        limit = min(limit, 500)
        try:
            start = int(cursor) if cursor is not None else 0
        except ValueError:
            raise HTTPException(400, "cursor must be a non-negative integer") from None
        if start < 0:
            raise HTTPException(400, "cursor must be a non-negative integer")
        end = min(start + limit, len(records))
        return JSONResponse({
            "items": records[start:end],
            "next_cursor": str(end) if end < len(records) else None,
            "total": len(records),
        })

    @app.get("/api/setup-status")
    async def get_setup_status():
        if session.mode is not SessionMode.SETUP:
            raise HTTPException(404, "Not found.")
        return JSONResponse(session.setup_draft.status())

    def update_status() -> dict:
        """Project cached managed state into the browser API; never discover here."""
        path = updates.state_path()
        try:
            state = updates.load_state(path)
        except updates.UpdateError as exc:
            return {"managed": True, "last_error": str(exc), "staging": update_job["running"]}
        if state is None:
            return {"managed": False, "candidate": None, "staging": update_job["running"]}
        success = state.get("last_success")
        candidate = success.get("candidate") if isinstance(success, dict) else None
        if not isinstance(candidate, dict):
            candidate = None
        elif isinstance(candidate.get("sha"), str):
            candidate = dict(candidate)
            repo = candidate.get("canonical_repo") or updates.REPO
            candidate["url"] = f"https://github.com/{repo}/commit/{candidate['sha']}"
        dismissal = state.get("dismissal")
        now = time.time()
        suppressed = updates.candidate_is_suppressed(candidate, dismissal, now=now)
        root = path.parent
        pending = None
        try:
            pending = updates.valid_pending_slot(root, updates.installed_launcher_protocol(root))
        except (updates.UpdateError, OSError):
            pending = None
        automatic_restart = False
        if pending is not None:
            try:
                automatic_restart = updates.validate_launch_environment() is not None
            except updates.UpdateError:
                automatic_restart = False
        refusal_commit = state.get("comparison_refused_commit")
        refusal = refusal_commit == (candidate or {}).get("sha")
        return {
            "managed": True,
            "candidate": None if suppressed else candidate,
            "last_attempt": state.get("last_attempt"),
            "last_error": state.get("last_error") or state.get("build_error"),
            "staging": update_job["running"],
            "pending_staged": pending is not None,
            "comparison_refused": refusal,
            "comparison_refusal_reason": state.get("comparison_refusal_reason") if refusal else None,
            "automatic_restart": automatic_restart,
            "dismissal": dismissal,
        }

    @app.get("/api/update")
    async def get_update():
        return JSONResponse(update_status())

    @app.post("/api/update/check")
    async def post_update_check(request: Request):
        if not update_checks.allow(client_address(request)):
            raise HTTPException(429, "Too many update checks.")
        await run_in_threadpool(updates.check_for_update, state_file=updates.state_path(), force=True)
        return JSONResponse(update_status())

    @app.post("/api/update/stage")
    async def post_update_stage():
        with update_job_lock:
            if update_job["running"]:
                raise HTTPException(409, "An update staging job is already running.")
            try:
                state = updates.load_state()
            except updates.UpdateError:
                raise HTTPException(409, "Updates are unavailable in this installation.") from None
            success = state.get("last_success") if state else None
            raw = success.get("candidate") if isinstance(success, dict) else None
            if not isinstance(raw, dict):
                raise HTTPException(409, "No update candidate is cached.")
            try:
                candidate = updates.Candidate(**raw)
            except (TypeError, ValueError):
                raise HTTPException(409, "The cached update candidate is invalid.") from None
            update_job["running"] = True

        def stage():
            state_path = updates.state_path()
            root = state_path.parent
            work = None
            try:
                state = updates.load_state(state_path) or {}
                state["staging"] = {"status": "running", "commit": candidate.sha}
                updates.write_state(state, state_path)
                updates.stage_cached_candidate(
                    candidate, config_path=session.safety_config_path,
                )
            except updates.ComparisonRefused:
                # stage_inactive_slot already recorded the refusal and its
                # reason; overwriting them with a generic build error would
                # lose the sentence the banner shows.
                pass
            except Exception as exc:
                # Every other failure is recorded, unconditionally.  This used
                # to be skipped whenever `comparison_refused_commit` matched
                # this commit -- a record of *some* earlier refusal, not of this
                # attempt -- so after one legitimate refusal every later failure
                # of that commit vanished: no error, no status, `staging` stuck
                # on "running".  Block 58e's third demo gate died there.
                latest = updates.load_state(state_path) or {}
                latest["staging"] = {"status": "error", "commit": candidate.sha}
                latest["build_error"] = str(exc) or type(exc).__name__
                updates.write_state(latest, state_path)
            finally:
                if work is not None:
                    shutil.rmtree(work, ignore_errors=True)
                with update_job_lock:
                    update_job["running"] = False

        (updates.state_path().parent / "downloads").mkdir(parents=True, exist_ok=True)
        threading.Thread(target=stage, name="microclaw-update-stage", daemon=True).start()
        return JSONResponse({"staging": True}, status_code=202)

    @app.post("/api/update/dismiss")
    async def post_update_dismiss(value: UpdateDismissal):
        if value.action not in {"later", "skip"}:
            raise HTTPException(422, "action must be 'later' or 'skip'.")
        try:
            state = updates.load_state()
        except updates.UpdateError:
            raise HTTPException(409, "Updates are unavailable in this installation.") from None
        if state is None:
            raise HTTPException(409, "Updates are unavailable in this installation.")
        record = {"action": value.action, "commit": value.commit}
        if value.action == "later":
            record["until"] = time.time() + 7 * 24 * 60 * 60
        state["dismissal"] = record
        updates.write_state(state)
        return JSONResponse({"dismissed": True})

    @app.post("/api/update/restart")
    async def post_update_restart(request: Request):
        if session.lock.locked():
            raise HTTPException(409, "An agent turn is in progress.")
        ledger = tools._existing_acquisition_ledger(session.ctrl)
        if ledger is not None and ledger.in_flight:
            raise HTTPException(409, "An acquisition is in progress.")
        if session.pending is not None:
            raise HTTPException(409, "A confirmation is pending.")
        capability = getattr(session.ctrl, "_microclaw_setup_write_capability", None)
        if capability is not None and capability.in_flight:
            raise HTTPException(409, "A setup write is in progress.")
        status = update_status()
        if not status.get("automatic_restart"):
            raise HTTPException(409, "Automatic restart is not available; restart later.")
        server = getattr(request.app.state, "uvicorn_server", None)
        if server is None:
            raise HTTPException(409, "Automatic restart is not available; restart later.")
        launch = updates.validate_launch_environment()
        if launch is None:
            raise HTTPException(409, "Automatic restart is not available; restart later.")
        root, _slot, nonce = launch
        updates.write_restart_request(root, nonce)
        os.environ[shortcut.UPDATE_RESTART_ENV] = "1"
        server.should_exit = True
        return JSONResponse({"restart_requested": True})

    @app.post("/api/prompt")
    async def post_prompt(p: Prompt, request: Request):
        msg = p.message.strip()
        if not msg:
            raise HTTPException(400, "Empty message.")
        if credentials.load_api_key()[0] is None:
            raise HTTPException(400, "No Anthropic API key is set.")
        if session.lock.locked():
            raise HTTPException(409, "A turn is already in progress.")
        # Acquired here, not inside events(): the response body is not iterated
        # until after this handler returns, so a lock taken there would leave a
        # window in which a second prompt passes the check above.
        await session.lock.acquire()
        session.cancel.clear()

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        turn_identity = request.state.identity

        def emit(event):
            try:
                loop.call_soon_threadsafe(queue.put_nowait, event)
            except RuntimeError:
                pass  # Ctrl-C closed the loop mid-turn; serve()'s finally saves.

        def run_turn():
            """The whole turn, on a thread of its own.

            Deliberately NOT `iterate_in_threadpool`: that closes the generator
            when the client disconnects, raising GeneratorExit at whichever yield
            the turn had reached. Stop there mid-round and the history keeps an
            assistant message whose tool_use block has no tool_result — which the
            Messages API rejects on the *next* prompt, from a history that looks
            fine in the viewer. The stage has already moved; finish the turn.

            run_agent_iter is synchronous and blocks on Anthropic HTTP and ZMQ
            round-trips, so it must stay off the event loop regardless.
            """
            # Session.confirm routes through whichever stream the running turn
            # owns; only one turn runs at a time (session.lock), so there is no
            # second emit to confuse.
            session._emit = emit
            session.current_identity = turn_identity
            try:
                for event in run_agent_iter(
                    msg, session.ctrl, session.guard, session.history, session.model,
                    # Read these directly. A `getattr` default here would be the
                    # full hardware registry, so a session class that ever failed
                    # to set them would silently dispatch every normal tool in
                    # setup mode. Both classes set all three in __init__; a
                    # missing attribute is a bug that should raise, not fail open.
                    tool_schemas=session.tool_schemas,
                    tool_registry=session.tool_registry,
                    setup_mode=session.mode is SessionMode.SETUP,
                    cancel=session.cancel,
                    context_provider=(session.store.model_messages
                                      if hasattr(session, "store") else None),
                    on_message=(session.store.append
                                if hasattr(session, "store") else None),
                    confirmation_records=session.audit_records,
                ):
                    emit(event)
            except Exception as e:  # noqa: BLE001 — the stream is the only channel
                emit({"type": "error", "message": f"{type(e).__name__}: {e}"})
            finally:
                session._emit = None
                session.current_identity = "loopback"
                # AuditLog has already appended and flushed every message.
                if not hasattr(session, "store"):
                    # Compatibility for embedders/test sessions that have not
                    # adopted ConversationStore yet.
                    try:
                        from microclaw.__main__ import write_history
                        write_history(session.history_fn, session.history, session.save)
                    except Exception as e:  # noqa: BLE001
                        print(f"[microclaw] Could not write history: {e}", file=sys.stderr)
                emit(_TURN_DONE)
                loop.call_soon_threadsafe(session.lock.release)

        threading.Thread(target=run_turn, name="microclaw-turn", daemon=True).start()

        async def events():
            # A client that vanishes leaves this generator closed and the worker
            # thread running; it finishes the turn, saves, and releases the lock.
            while True:
                event = await queue.get()
                if event is _TURN_DONE:
                    return
                yield _sse(event)

        # No compression middleware on this app: GZipMiddleware buffers the
        # stream and the events all arrive at the end, in one lump.
        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/stop")
    async def post_stop():
        """Ask the running turn to stop at the next round or tool boundary.

        Cooperative, and honest about it: `execute_tool` blocks in Java and there
        is no interrupting `run_timelapse` halfway. The button says "Stop after
        the current step" for that reason.

        A UI endpoint, never a tool — the agent must not be able to call it, and
        it is not in TOOL_REGISTRY. There is deliberately no hardware-halt
        companion: pyjavaz serializes every bridge call, so a halt would queue
        behind the tool call it means to interrupt (design/16 §6).
        """
        if not session.lock.locked():
            raise HTTPException(409, "No turn is running.")
        session.cancel.set()
        return JSONResponse({"stopping": True})

    @app.get("/api/confirm")
    async def get_confirm():
        """The pending confirmation, if any — or {}.

        Not optional: confirm_request is delivered exactly once, over a stream
        a page reload destroys. Without this, a refresh at the wrong moment
        strands the turn until the deadline. serve.html fetches it on load.
        """
        p = session.pending
        grants = tools.SESSION_GRANTS.active()
        if p is None:
            return JSONResponse({"grants": grants})
        return JSONResponse({"id": p.id, "summary": p.summary, "kind": p.kind,
                             "subject": p.subject,
                             "grantable": tools.SessionGrants.is_grantable(p.kind, p.subject),
                             "grants": grants})

    @app.post("/api/confirm")
    async def post_confirm(c: Confirm, request: Request):
        """Answer the pending confirmation.

        Runs on the event loop, so it cannot be blocked by the turn thread that
        is waiting on it. Matches on id: a stale banner from a previous confirm
        must not answer the current one.
        """
        if c.approve == "revoke":
            pending = session.pending
            if pending is not None and pending.grant_id == c.id:
                raise HTTPException(
                    409, "The session approval is still being recorded; retry revoke."
                )
            revoked = tools.SESSION_GRANTS.revoke(c.id)
            if revoked is None:
                raise HTTPException(409, "No session grant with this id is active.")
            session._audit_confirmation(
                summary=(
                    f"SESSION GRANT REVOKED: "
                    f"{revoked['kind']}/{revoked['subject']}"
                ),
                kind=revoked["kind"], subject=revoked["subject"],
                decision=f"revoked:{revoked['id']}",
                confirmation_id=uuid.uuid4().hex,
                identity=request.state.identity, grant_id=revoked["id"],
                grant_identity=revoked["identity"],
            )
            return JSONResponse({"resolved": True, "grants": tools.SESSION_GRANTS.active()})
        if c.approve not in (True, False, "session"):
            raise HTTPException(422, "approve must be true, false, 'session', or 'revoke'.")
        p = session.pending
        if p is None or p.id != c.id:
            raise HTTPException(409, "No confirmation with this id is pending.")
        if c.approve == "session" and not tools.SessionGrants.is_grantable(
            p.kind, p.subject
        ):
            raise HTTPException(422, "This confirmation cannot be granted for the session.")
        if c.approve == "session":
            grant = tools.SESSION_GRANTS.grant(
                p.kind, p.subject, p.summary, identity=request.state.identity
            )
            p.grant_id = grant["id"]
            # Grant lifecycle is committed here, beside creation and
            # revocation. The turn thread separately audits whether this
            # particular action completed as an approved session decision.
            # Roll back if the durable row cannot be written: prompts must not
            # turn off under a grant whose origin is absent from the log.
            try:
                session._audit_confirmation(
                    summary=(
                        f"SESSION GRANT CREATED: "
                        f"{grant['kind']}/{grant['subject']}"
                    ),
                    kind=grant["kind"], subject=grant["subject"],
                    decision=f"granted:{grant['id']}",
                    confirmation_id=p.id, identity=request.state.identity,
                    grant_id=grant["id"], grant_identity=grant["identity"],
                )
            except Exception:
                tools.SESSION_GRANTS.revoke(grant["id"])
                p.grant_id = None
                raise
        p.reply.put((c.approve, request.state.identity))
        return JSONResponse({"resolved": True, "grants": tools.SESSION_GRANTS.active()})

    @app.get("/api/model")
    async def get_model():
        # known_models() is a blocking HTTP call the first time it is asked, and
        # this endpoint runs on page load — off the event loop.
        return JSONResponse({
            "model": resolve_model(session.model),
            "default": DEFAULT_MODEL,
            "available": await run_in_threadpool(known_models),
            "editable": session.editable,
        })

    @app.post("/api/model")
    async def post_model(m: Model):
        if not session.editable:
            raise HTTPException(
                403, "Bound beyond localhost: set --model or MICROCLAW_MODEL and restart."
            )
        name = m.model.strip()
        if not name:
            raise HTTPException(400, "Empty model id.")
        if session.lock.locked():
            raise HTTPException(409, "A turn is in progress.")
        # Never swap models mid-turn. Session-only: --model and MICROCLAW_MODEL
        # stay the durable knobs.
        async with session.lock:
            session.model = name
        return JSONResponse({"model": resolve_model(session.model)})

    @app.get("/api/artifact")
    async def get_artifact(path: str):
        """Download a file a tool wrote, and nothing else.

        The allowlist is *capability-based, not location-based*: a path is
        servable iff some tool in this session declared it as an artifact. Those
        declarations are built by our own code from the path the tool actually
        wrote, live in `session.history`, and the model cannot forge one.

        This is tighter than the directory sandbox it replaces. A workspace root
        would let the browser fetch any file beneath it, including ones no tool
        ever touched; an exact match lets it fetch only what microclaw just
        wrote. And it needs no configuration, so downloading an artifact never
        constrains where the operator may save data.
        """
        if not session.editable:  # loopback only
            raise HTTPException(403, "Not available when bound beyond localhost.")
        if path not in _declared_artifacts(_durable_history(session)):
            raise HTTPException(403, "Not an artifact produced by this session.")
        try:
            # A configured workspace still applies — this endpoint may not be a
            # way around it — but it is no longer what authorises the download.
            if session.guard is None:
                raise HTTPException(403, "Artifacts are unavailable in setup mode.")
            resolved = session.guard.resolve_in_workspace(path)
        except SafetyViolation as e:
            raise HTTPException(403, str(e))
        if not Path(resolved).is_file():
            raise HTTPException(404, "No such artifact.")
        name = Path(resolved).name
        # Always octet-stream + attachment, never a sniffed type: an artifact
        # that happens to be HTML, served inline from http://127.0.0.1:8000, is
        # same-origin script execution against the endpoint driving the stage.
        return FileResponse(
            resolved,
            media_type="application/octet-stream",
            filename=name,
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    @app.get("/api/key")
    async def get_key():
        key, source = credentials.load_api_key()
        return JSONResponse(
            {
                "has_key": key is not None,
                "suffix": credentials.mask(key) if key else None,
                "source": source,
                "editable": session.editable,
            }
        )

    @app.post("/api/key")
    async def post_key(k: Key):
        if not session.editable:
            raise HTTPException(
                403, "Bound beyond localhost: set ANTHROPIC_API_KEY and restart."
            )
        key = k.key.strip()
        if not key:
            raise HTTPException(400, "Empty key.")
        # Overwrites whatever was set, so a key can be swapped mid-session.
        _add_audit_secret(session, key)
        set_api_key(key)
        stored_in = stored_at = None
        stale_store = False
        if k.persist:
            stored_in, stored_at = credentials.store_api_key(key)
        else:
            # A key held for this process only doesn't displace an older one in
            # the credential store — that one comes back on the next start.
            stored, _ = credentials.load_stored_key()
            stale_store = stored is not None and stored != key
        # Never echo the key: a suffix is enough to confirm which one is set.
        return JSONResponse(
            {
                "has_key": True,
                "suffix": credentials.mask(key),
                "source": "env",
                "editable": True,
                "stored_in": stored_in,
                "stored_at": stored_at,
                "stale_store": stale_store,
            }
        )

    return app


def _open_when_ready(
    host: str, port: int, url: str, timeout: float = 15.0, *,
    clock=time.monotonic, sleep=time.sleep,
) -> threading.Thread:
    """Open `url` in a browser once the server is accepting connections.

    uvicorn.run() blocks, and a browser fired before the socket is listening
    lands on a connection-refused page. Poll the port from a daemon thread
    instead of hooking the ASGI lifespan, so a browser that never opens (headless
    box, no BROWSER) can't wedge the server.

    Returns the daemon thread. serve() ignores it; tests join it, because a
    poll thread cannot be observed by sleeping for a fixed interval and hoping
    it was scheduled — under load on a busy box, it is not.
    """

    def wait():
        deadline = clock() + timeout
        while clock() < deadline:
            try:
                with socket.create_connection((host, port), timeout=0.5):
                    break
            except OSError:
                sleep(0.1)
        else:
            return  # never came up; the traceback uvicorn prints is the real story
        try:
            webbrowser.open(url)
        except Exception:
            pass  # no browser here — the URL is already printed

    thread = threading.Thread(target=wait, daemon=True)
    thread.start()
    return thread


def serve(args):
    """Entry point for the `serve` subcommand."""
    if getattr(args, "setup_write_security_config", False):
        if args.host not in LOCAL_HOSTS:
            sys.exit(
                "--setup-write-security-config is available only on a loopback bind."
            )
        if args.safety_config is not None:
            sys.exit(
                "--setup-write-security-config targets only the per-user default; "
                "do not pass --safety-config."
            )
    if args.host not in LOCAL_HOSTS and not args.allow_remote:
        sys.exit(
            f"Refusing to bind {args.host}: this endpoint moves real hardware. "
            "Pass --allow-remote if you truly mean to expose it."
        )
    remote = args.host not in LOCAL_HOSTS
    behind_tls_proxy = args.behind_tls_proxy
    if behind_tls_proxy and not args.allow_remote:
        sys.exit("--behind-tls-proxy requires --allow-remote.")
    if remote and not behind_tls_proxy:
        sys.exit(
            "Refusing cleartext remote HTTP. Put Microclaw behind a TLS-terminating "
            "proxy and pass --behind-tls-proxy."
        )
    token = None
    auth_state = None
    pairing_code = None
    if remote:
        token = os.environ.get("MICROCLAW_REMOTE_TOKEN")
        if token is not None and len(token) < REMOTE_TOKEN_MIN_CHARS:
            sys.exit(f"MICROCLAW_REMOTE_TOKEN must be at least {REMOTE_TOKEN_MIN_CHARS} characters.")
        token = token or secrets.token_urlsafe(32)
        auth_state = RemoteAuth(token)
        pairing_code = auth_state.mint_code()
    import uvicorn

    from microclaw import tools

    path = Path(args.safety_config) if args.safety_config else config.default_safety_config()
    config_result = config.validate_safety_config(path)
    # Launcher health is the common pre-hardware boundary.  This validates the
    # nonce and executing slot metadata; direct/unmanaged launches are a no-op.
    from microclaw import updates
    try:
        updates.write_launcher_health()
    except updates.UpdateError as exc:
        sys.exit(f"Launcher startup refused: {exc}")
    # One due check per server start, never on the startup/request path.
    # check_for_update owns the interval, jitter, managed-install and opt-out rules.
    no_update_check = getattr(args, "no_update_check", False)
    updates.start_due_check(no_update_check)
    session = build_session(args, config_result=config_result)
    if token:
        _add_audit_secret(session, token)
    if pairing_code:
        _add_audit_secret(session, pairing_code)
    # Route every in-code confirmation gate to the browser, where the operator
    # is. Installed once, not per turn: all callsites read the module global at
    # call time, so a single assignment covers them (design/21 F1). The CLI keeps
    # the stdin default — a terminal is present there by definition.
    tools.CONFIRM_FN = session.confirm
    app = build_app(session, remote=remote, api_token=token,
                    behind_tls_proxy=behind_tls_proxy, auth_state=auth_state)
    server = uvicorn.Server(uvicorn.Config(
        app, host=args.host, port=args.web_port, log_level="warning",
    ))
    app.state.uvicorn_server = server

    if remote:
        print(
            f"\nListening (cleartext) on http://{args.host}:{args.web_port} — "
            "for the TLS proxy only; do not expose this port.\n"
            f"Remote bearer token: {token}\n"
            "Browser pairing: open your proxy's HTTPS URL and append "
            f"/#pair={pairing_code}\n",
            flush=True,
        )
        print("Microclaw GUI: use the operator-supplied TLS proxy URL  (Ctrl-C to stop)", flush=True)
    else:
        url = f"http://{args.host}:{args.web_port}"
        print(f"Microclaw GUI: {url}  (Ctrl-C to stop)", flush=True)
    if not remote and not args.no_browser:
        # A wildcard bind is not an address a browser (or Windows' connect())
        # can reach; the loopback the server is also listening on is.
        visit = "127.0.0.1" if args.host == "0.0.0.0" else args.host
        _open_when_ready(visit, args.web_port, f"http://{visit}:{args.web_port}")

    # Audit records are already flushed message-by-message. Every exit path
    # reports declared illumination without changing rig state (design/38 F9).
    try:
        server.run()
    finally:
        from microclaw.__main__ import report_declared_illumination_on_exit
        report_declared_illumination_on_exit(
            session.guard, session.ctrl.core, flush=True
        )
