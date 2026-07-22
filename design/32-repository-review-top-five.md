# Repository review: top five issues

Date: 2026-07-21

## Scope and prioritization

This review covered the application entry points, agent loop, web server,
hardware tools, safety configuration/guard, generated-hook lifecycle, package
metadata, and the non-integration tests. The issues below are ranked primarily
by possible harm to the microscope, sample, operator, or credentials, and then
by likelihood and maintainability cost. They are proposals, not implemented
changes; the snippets are deliberately small design stubs.

The test baseline was 769 passed, 1 skipped, and 2 failed. Both failures were
caused by this review environment refusing local socket binds
(`tests/test_webserve.py:749` and `:737`), rather than by an observed application
regression. The 97 hardware integration tests were excluded.

## 1. A `reviewed: true` safety file can still contain ineffective limits

**Priority: P0 — hardware safety / validation**

`load_safety_config` treats the single boolean as sufficient approval and then
constructs dataclasses with mostly optional values (`microclaw/config.py:38-42`,
`microclaw/safety.py:16-31`). There is no semantic validation that minima are
below maxima, required rig limits exist, exposure is positive, values are
finite, device/property entries are unique, or unknown/misspelled keys are
rejected. The runtime comparisons at `microclaw/safety.py:193-228` also allow
`NaN` through because every comparison with it is false. Thus a typo such as
`max_exposure_ms: .nan`, an omitted `camera` section, or inverted bounds can be
human-marked reviewed while providing no useful safety boundary.

The safety configuration is the wrong place for permissive parsing. It should
be a strict, versioned schema that fails startup with all validation errors and
requires finite bounds for every core actuator exposed by the tool set.

The codebase currently uses plain dataclasses plus `yaml` and takes no Pydantic
dependency. Everything this finding needs — finite bounds, `min < max`, rejecting
unknown keys, requiring the `camera`/`stage` sections — is achievable in the
existing `from_yaml` path without a new dependency, and that is the preferred
route unless we adopt Pydantic deliberately for other reasons. The stub below is
illustrative of the *validation*, not a recommendation to add the library.

```python
# microclaw/safety_config.py (stub; Pydantic shown only to illustrate the checks)
from math import isfinite
from pydantic import BaseModel, ConfigDict, model_validator

class StageLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x_min: float
    x_max: float
    y_min: float
    y_max: float
    z_min: float
    z_max: float

    @model_validator(mode="after")
    def valid_ranges(self):
        values = (self.x_min, self.x_max, self.y_min,
                  self.y_max, self.z_min, self.z_max)
        if not all(isfinite(v) for v in values):
            raise ValueError("all stage limits must be finite")
        if not (self.x_min < self.x_max and self.y_min < self.y_max
                and self.z_min < self.z_max):
            raise ValueError("each stage minimum must be below its maximum")
        return self

class SafetyFileV1(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = 1
    reviewed: bool
    stage: StageLimits
    camera: CameraLimits  # requires finite max_exposure_ms > 0

def load_safety_config(path: Path) -> SafetyConstraints:
    parsed = SafetyFileV1.model_validate(yaml.safe_load(path.read_text()))
    if not parsed.reviewed:
        raise UnreviewedSafetyConfig(path)
    return parsed.to_constraints()
```

Tests should cover missing sections, unknown keys, booleans used as numbers,
inverted/equal ranges, infinities, and NaN both at configuration load and at
every public `SafetyGuard.check_*` boundary.

## 2. Safety limits individual actions, not cumulative exposure or acquisition size

**Priority: P0 — sample safety / denial of service**

The exposure guard only checks one frame's exposure (`microclaw/safety.py:223-228`).
`run_timelapse` accepts any frame count and interval and passes them directly to
the acquisition engine (`microclaw/tools.py:755-784`); `run_zstack` similarly
checks only its endpoints and per-frame exposure (`microclaw/tools.py:682-709`).
There is no maximum frame count, plane count, acquisition duration, estimated
illuminated time, output size, or cumulative session dose. One valid tool call
can consequently bleach a sample for hours, fill a disk, or make the cooperative
Stop button ineffective until the blocking acquisition returns. The agent's
50-round cap does not bound work inside a single tool call.

Add a common acquisition-plan object. Materialize/estimate the complete event
plan before hardware motion, enforce configured budgets, and require a separate
human confirmation above a lower warning threshold. Track a conservative
session dose ledger rather than relying only on model instructions.

The confirmation path is not new infrastructure: illumination already routes a
blocking human decision through `confirm_fn(..., kind=...)`
(`microclaw/safety.py:295-316`). Reuse that mechanism with a new `kind` rather
than inventing a parallel one; the bulk of the work is the planner and the
ledger, not the prompt plumbing.

```python
@dataclass(frozen=True)
class AcquisitionPlan:
    frames: int
    exposure_ms_per_frame: float
    estimated_duration_s: float
    estimated_bytes: int

    @property
    def illuminated_ms(self) -> float:
        return self.frames * self.exposure_ms_per_frame

def authorize_acquisition(guard, plan, ledger, confirm):
    guard.check_acquisition(
        frames=plan.frames,
        duration_s=plan.estimated_duration_s,
        bytes_=plan.estimated_bytes,
        illuminated_ms=plan.illuminated_ms,
        session_illuminated_ms=ledger.illuminated_ms,
    )
    if plan.illuminated_ms > guard.confirm_above_illuminated_ms:
        if not confirm(render_plan(plan), kind="acquisition"):
            raise SafetyViolation("acquisition declined")
    return ledger.reserve(plan)  # commit actual usage as frames complete

def run_timelapse(...):
    plan = plan_timelapse(n_frames, interval_s, exposure_ms, camera_shape(ctrl))
    reservation = authorize_acquisition(guard, plan, ctrl.dose_ledger, CONFIRM_FN)
    with reservation:
        return acquire_interruptibly(...)
```

The same planner must cover adaptive acquisitions and MMStudio MDA, not just the
two simple runners. Prefer chunked acquisitions so cancellation can be checked
between bounded batches.

One constraint the chunking design must state explicitly: pyjavaz holds a single
lock across every bridge round trip, so no thread can preempt an in-flight
core/acquisition call — a software kill switch *mid-acquisition* is impossible,
not merely unimplemented (see the pyjavaz-serialization note and design/12).
"Check cancellation between batches" therefore only works if one long
acquisition is decomposed into a *sequence of separate short acquire calls*; the
interruption granularity equals the batch size, and there is real per-batch
setup/teardown overhead. This is a design trade-off (responsiveness vs.
throughput), not a free improvement, and the doc should size the batch
accordingly rather than implying a running acquisition can be interrupted.

## 3. Remote mode exposes hardware-control endpoints without authentication

**Priority: P0 — security / authorization**

The server intentionally requires `--allow-remote` before a non-loopback bind,
but after that opt-in any reachable client can call `/api/prompt`, `/api/stop`,
and `/api/confirm`. The middleware checks a supplied `Origin` only
(`microclaw/webserve.py:247-259`); non-browser clients can omit or forge it.
The prompt endpoint itself has no authentication check
(`microclaw/webserve.py:275-283`). Disabling browser key/model editing remotely
does not protect microscope control. On a shared or accidentally routed lab
network, discovering the port is enough to operate the instrument or approve a
pending illumination action.

Remote mode should require authentication independent of `Origin`: generate a
high-entropy bearer/session secret, compare it in constant time, protect every
`/api/*` route (including confirmation), and refuse cleartext non-loopback use
unless it sits behind an explicitly trusted TLS proxy. Origin validation remains
useful CSRF defense but is not identity.

```python
def build_app(session, *, remote: bool, api_token: str | None):
    if remote and not api_token:
        raise RuntimeError("remote mode requires an API token")

    @app.middleware("http")
    async def authenticate(request, call_next):
        if request.url.path.startswith("/api/"):
            supplied = request.headers.get("authorization", "")
            expected = f"Bearer {api_token}"
            if not secrets.compare_digest(supplied, expected):
                return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        return await call_next(request)

# Prefer an HttpOnly, Secure, SameSite=Strict session cookie after a one-time
# pairing code, so the long-lived bearer token is not stored in browser JS.
```

Also add request-body limits, confirmation audit records (identity, time,
decision), rate limiting, and tests using requests with no `Origin` header.

## 4. Confirmed hooks execute arbitrary Python inside the hardware-control process

**Priority: P1 — security / reliability / fault isolation**

The repository accurately labels hook linting as advisory and trivially
bypassable (`microclaw/hook_manager.py:21-28`). After confirmation, loading a
hook runs its module top-level with `exec_module` in the main process
(`microclaw/hook_manager.py:128-160`). The hash prevents post-approval changes,
but it does not constrain the approved code. A generated or user-supplied hook
can read credentials, use the network, modify files, hang, crash the process,
exhaust memory, or access controller objects supplied to some hooks — and it
does all of this *inside* the single process that also drives the microscope.

The threat is not primarily a malicious operator. This is a single-operator
local tool, and confirmation remains a meaningful gate. The untrusted component
is the implementation: generated code can contain an unsafe mistake or be
influenced by prompt-injected external material even when the operator's request
is benign. Because the API key and live controller inhabit the same process,
isolation has real security value as well as the stronger day-to-day reliability
value: a hook that hangs or segfaults should not take down microscope control.

Smart microscopy still requires analysis to influence acquisition. Therefore
the boundary must not be a "data-only" contract that forbids hardware decisions.
Use a **capability-limited decision contract** instead: a generated analysis hook
may return measurements and propose typed motion/acquisition events, but it may
not execute them directly. The trusted parent validates each proposal through
`SafetyGuard`, acquisition/dose budgets, confirmation policy, cancellation, and
the audit log before placing it on the hardware event queue. This is conceptually
close to the repository's existing candidate-event pattern.

The first, relatively small change is to stop handing generated analysis hooks a
live controller and accept only typed decisions from them. This does not apply to
every hook: trusted built-in control hooks such as autofocus may legitimately
need synchronous controller access and should remain a separate, explicitly
trusted category.

Hard execution deadlines, per-hook memory limits, recovery from native crashes,
and reliable termination of infinite loops require a worker-process boundary;
they cannot be promised by an in-process thread. Run generated analysis hooks in
a separate, least-privilege worker with a narrow serialized protocol. The worker
receives image bytes plus immutable metadata and returns measurements and
proposed events; only the trusted parent authorizes and executes them. Apply
filesystem/network/process limits where the platform supports them.

```python
# Generated-hook worker: one long-lived, stateful instance per acquisition, but
# no controller, guard, credentials, or hardware event queue.
with hook_worker.session(
    hook_sha256=entry["sha256"],
    initial_state=bounded_initial_state,
) as hook_id:
    for image, metadata in acquisition_frames:
        result = hook_worker.process(
            hook_id=hook_id,
            image=share_image(image),
            metadata=json_safe(metadata),
            timeout_s=constraints.hooks.max_runtime_s,
        )
        validated = HookResult.model_validate(result)

        # Trusted parent: proposed actions are capabilities, not direct access.
        # ProposedAction is a discriminated union of MoveStage, AcquireAt,
        # SetExposure, ContinueSurvey, StopSurvey, and RequestAutofocus. Unknown
        # kinds fail schema validation and therefore never reach this loop.
        for action in validated.actions:
            event_or_command = authorize_and_convert(
                action,
                guard=guard,
                acquisition_budget=acquisition_budget,
                confirmation_policy=confirmation_policy,
            )
            audit.record_hook_decision(validated.measurements, action)
            trusted_dispatcher.submit(event_or_command)

# Worker launch sketch: a dedicated venv/container, no API key or controller,
# read-only hook/package mount, private temp directory, network disabled, and
# an authenticated length-prefixed JSON/msgpack protocol over a local pipe.
```

One non-obvious consequence of removing the live controller is that some
adaptive hooks are **stateful across frames**, not stateless per image. The
current runner preserves one hook instance throughout an acquisition and
attaches shared survey state — the planned events, candidate queue, and progress
counter — to that instance. A worker protocol modeled as independent
`(image, metadata) → actions` calls would lose visited tiles, running metrics,
and stop/jump decisions.

Keep one worker-side hook instance alive for the acquisition, keyed by an opaque
`hook_id`, rather than serializing its complete private analytical state on every
frame. This supports large models, trackers, masks, and accumulated measurements
without repeatedly copying them across the process boundary. The parent must
bound worker lifetime, memory, and IPC message size, but need not understand the
semantics of private analytical state. Optional, size-limited serialized
checkpoints can support auditing or crash recovery; they are not the primary
per-frame state mechanism and may never contain controller handles or other
capabilities.

Be precise about which recovery the worker boundary provides, because
worker-held state and the "recovery from native crashes" justification above
address different failures. Isolation guarantees that a hook hang or segfault
does not take down the parent. The parent remains available to reject further
hook actions, initiate the acquisition's existing abort/cleanup path, and record
the failure, although already queued hardware events and bridge serialization
may delay the stop. It does **not** guarantee that opaque analytical state —
running metrics, tracker internals, loaded model state, or other worker-private
data — survives the crashed worker.

The parent can durably retain authoritative acquisition progress such as
accepted actions, visited positions, and emitted measurements without
checkpointing the worker's complete state. Reconstructing that progress is not
the same as resuming the analysis exactly where it stopped: exact analytical
resumption additionally requires sufficiently frequent worker checkpoints.
The optional checkpoints above are therefore coarse audit/partial-recovery aids,
not an exact-resume guarantee. The boundary buys process isolation and a
surviving hardware-control path; adaptive-state durability is a separate design
choice.

The action vocabulary must cover the adaptive behavior that already exists, not
only XY motion and acquisition. At minimum it needs typed variants for
`MoveStage`, `AcquireAt`, `SetExposure`, `ContinueSurvey`, `StopSurvey`, and
`RequestAutofocus`. The union is closed and discriminated: an unknown action
type, invalid payload, or action unsupported by the current runner fails closed
before dispatch. Every accepted action is converted by trusted code into a
guarded operation or acquisition event.

Hardware-motion plugins and trusted built-in control hooks are different
boundaries and should remain behind explicit gates. Generated Python analysis
hooks should never receive a live controller as a shortcut around parent-side
authorization, but they remain fully capable of adaptive, analysis-driven
microscopy through proposed events.

## 5. Conversation history grows without a context or storage policy

**Priority: P1 — functional reliability / privacy / cost**

Every turn appends user, assistant, tool, and image-result blocks to one list
(`microclaw/agent.py:304-395`), and every model round sends that history again
(`microclaw/agent.py:280-286`). The only bound is tool rounds within the current
turn. Long sessions will eventually exceed the model context window, become
increasingly slow and expensive, and repeatedly transmit old image payloads and
tool output. The web history endpoint serializes the entire list on every fetch
(`microclaw/webserve.py:271-273`), compounding browser memory and latency. There
is no redaction/retention policy for saved transcripts either.

Separate the append-only audit log from the bounded model context. Keep recent
turns verbatim, replace older turns with a structured checkpoint, retain
artifact hashes/paths and safety-relevant facts without image bytes, and page
the browser history. Compaction must never invent hardware state: stale state is
provenance only and must be re-read before action.

```python
@dataclass
class ConversationStore:
    audit: AuditLog                 # durable, append-only, redacted
    context_budget_tokens: int

    def model_messages(self) -> list[dict]:
        recent = self.audit.recent_complete_turns()
        kept = take_newest_within_budget(recent, self.context_budget_tokens)
        checkpoint = build_structured_checkpoint(
            self.audit.before(kept),
            include=("artifact_refs", "user_decisions", "completed_actions"),
            exclude=("image_base64", "credentials", "current_hardware_state"),
        )
        return [checkpoint, *kept] if checkpoint else kept

@app.get("/api/history")
async def get_history(cursor: str | None = None, limit: int = 100):
    return session.store.audit.page(cursor=cursor, limit=min(limit, 500))
```

Add token estimation before each API call, atomic transcript writes, explicit
retention controls, and tests that compact histories only at complete Anthropic
message boundaries (never between `tool_use` and `tool_result`).

## Recommended order

Implement strict configuration validation first, because all other hardware
guards depend on trustworthy configuration. Next add acquisition budgets and
interruptible batching. Authentication should be required before remote mode is
described as usable. Define the generated-hook decision schema and withhold the
live controller from that hook category next; this preserves smart microscopy
while making the trusted authorization boundary explicit. A worker process is
then required to enforce hard deadlines and memory caps—the document should not
claim those guarantees before it exists. Context/audit separation is independent
and can proceed in parallel, but should land before long-running sessions become
a supported use case.
