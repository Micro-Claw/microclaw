"""`microclaw serve` — the interactive web GUI (design/15).

One process owns one live `MicroscopeController` + `SafetyGuard` + history, the
same three objects the terminal REPL builds. The browser POSTs a prompt, the
server runs `run_agent` for that turn, and the page re-reads `/api/history`.

Two properties are load-bearing, because this endpoint moves real hardware:

* **Localhost only.** Binding beyond 127.0.0.1 requires `--allow-remote`; on a
  lab network a stray bind means anyone can drive the stage.
* **One operator.** The controller and history are shared mutable state and the
  microscope is physically single-user, so a turn holds `session.lock` and a
  second prompt is refused (409) rather than interleaved.
"""
import asyncio
import datetime
import json
import socket
import sys
import threading
import time
import webbrowser

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from microclaw import credentials
from microclaw.agent import run_agent, set_api_key
from microclaw.assets import load_page
from microclaw.config import load_safety_config
from microclaw.controller import MicroscopeController
from microclaw.safety import SafetyGuard

# Loopback names. Anything else needs --allow-remote.
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class Prompt(BaseModel):
    message: str


class Key(BaseModel):
    key: str
    persist: bool = True


def _jsonable(history: list[dict]) -> list[dict]:
    """History holds Anthropic SDK content blocks, which json can't encode.

    Round-trip through the same `default` hook `write_history` uses, so the API
    and the saved JSON agree block for block.
    """
    from microclaw.__main__ import json_default

    return json.loads(json.dumps(history, default=json_default))


class Session:
    """One live microscope + conversation, shared across requests."""

    def __init__(self, args):
        guard = SafetyGuard(load_safety_config(args.safety_config))
        print("Connecting to Micro-Manager...")
        ctrl = MicroscopeController(port=args.port, guard=guard)
        if not ctrl.is_connected():
            sys.exit(
                "Could not connect to Micro-Manager. "
                "Is the ZMQ server enabled in Tools → Options?"
            )
        self.ctrl = ctrl
        self.guard = guard
        self.model = args.model
        self.history: list[dict] = []
        self.history_fn = (
            f"{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}_microclaw_history.json"
        )
        self.save = args.save_history
        # Editing credentials from a browser is offered only on loopback, the
        # same rule design/15 sets for the safety-config editor (v2).
        self.editable = args.host in LOCAL_HOSTS
        self.lock = asyncio.Lock()  # one operator at a time

        # env > keyring > file; a key found in a store is pushed into the
        # environment now so the first turn doesn't have to look for it.
        key, source = credentials.load_api_key()
        if key:
            set_api_key(key)
            print(f"Anthropic API key: {credentials.mask(key)} (from {source})")
        else:
            print("No Anthropic API key found — set one from the browser.")


def build_app(session) -> FastAPI:
    from microclaw.__main__ import write_history

    app = FastAPI(title="Microclaw")
    page = load_page("serve.html")

    @app.middleware("http")
    async def block_cross_origin(request: Request, call_next):
        """Any page the operator visits can POST to a localhost server.

        Browsers preflight a cross-origin JSON POST and we send no CORS headers,
        so those are already refused — but a form post with a simple content
        type is not preflighted. Reject anything carrying a foreign Origin so a
        stray tab cannot drive the microscope.
        """
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            return JSONResponse({"detail": "Cross-origin request refused."}, status_code=403)
        return await call_next(request)

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return page

    @app.get("/api/history")
    async def get_history():
        return JSONResponse(_jsonable(session.history))

    @app.post("/api/prompt")
    async def post_prompt(p: Prompt):
        msg = p.message.strip()
        if not msg:
            raise HTTPException(400, "Empty message.")
        if credentials.load_api_key()[0] is None:
            raise HTTPException(400, "No Anthropic API key is set.")
        if session.lock.locked():
            raise HTTPException(409, "A turn is already in progress.")
        async with session.lock:
            # run_agent is synchronous and spends its time in Anthropic HTTP and
            # ZMQ round-trips — off the event loop, or uvicorn stops answering.
            reply, history = await run_in_threadpool(
                run_agent, msg, session.ctrl, session.guard, session.history,
                session.model,
            )
            session.history = history
            write_history(session.history_fn, history, session.save)
            return JSONResponse({"reply": reply})

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


def _open_when_ready(host: str, port: int, url: str, timeout: float = 15.0) -> None:
    """Open `url` in a browser once the server is accepting connections.

    uvicorn.run() blocks, and a browser fired before the socket is listening
    lands on a connection-refused page. Poll the port from a daemon thread
    instead of hooking the ASGI lifespan, so a browser that never opens (headless
    box, no BROWSER) can't wedge the server.
    """

    def wait():
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with socket.create_connection((host, port), timeout=0.5):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            return  # never came up; the traceback uvicorn prints is the real story
        try:
            webbrowser.open(url)
        except Exception:
            pass  # no browser here — the URL is already printed

    threading.Thread(target=wait, daemon=True).start()


def serve(args):
    """Entry point for the `serve` subcommand."""
    if args.host not in LOCAL_HOSTS and not args.allow_remote:
        sys.exit(
            f"Refusing to bind {args.host}: this endpoint moves real hardware. "
            "Pass --allow-remote if you truly mean to expose it."
        )
    if not args.safety_config:
        sys.exit(
            "A session requires --safety-config PATH. Copy "
            "safety_config.example.yaml and edit it for THIS rig; the example's "
            "limits match no real hardware."
        )
    import uvicorn

    from microclaw.__main__ import write_history

    session = Session(args)
    app = build_app(session)

    if args.allow_remote and args.host not in LOCAL_HOSTS:
        print(
            f"\n!! Microclaw is reachable at http://{args.host}:{args.web_port} — "
            "anyone who can reach this port can drive the microscope.\n"
        )
    url = f"http://{args.host}:{args.web_port}"
    print(f"Microclaw GUI: {url}  (Ctrl-C to stop)")
    if not args.no_browser:
        # A wildcard bind is not an address a browser (or Windows' connect())
        # can reach; the loopback the server is also listening on is.
        visit = "127.0.0.1" if args.host == "0.0.0.0" else args.host
        _open_when_ready(visit, args.web_port, f"http://{visit}:{args.web_port}")

    # Mirrors run_session: every exit path — Ctrl-C, a crash in a turn — writes
    # the history and shutters known illumination (design/14 §3).
    try:
        uvicorn.run(app, host=args.host, port=args.web_port, log_level="warning")
    finally:
        write_history(session.history_fn, session.history, session.save)
        shuttered = session.guard.shutter_all(session.ctrl.core)
        if shuttered:
            print(f"[microclaw] Illumination off: {', '.join(shuttered)}")
