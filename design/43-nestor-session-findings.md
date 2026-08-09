# design/43 — the Nestor microtubule/kinesin session: fifteen findings

Session `20260806_152935_790472` on M5 (2026-08-06, 13:35–14:45 UTC). Evidence:
`Micro-Claw/nestor-06082026/` — the history JSONL (262 messages) and the
confirmations JSONL (17 rows). The task was: raster in 561 for microtubules, and
at each positive tile run a 150-frame 488 timelapse for kinesins.

**It worked.** Nine 488 timelapses were acquired at nine verified tiles, nothing
was faked, and the one wrong turn (judging tiles at a single locked Z) was
recovered as soon as the operator said so. What follows is not a failure report;
it is the list of places the session was harder than the science.

Findings F1–F7 are the operator's, in their order. F8–F15 are additional, found
by reading the transcript against the code. F14 and F15 came out of reviewing
this document with the operator and are the two largest items in it.

---

## F1 — a cropped ROI is a rig fact, and Microclaw has nowhere to keep it

**What happened.** The camera ROI is `(1364, 1044, 184×164)` — 19.4 × 17.3 µm at
0.1056 µm/px, because on M5 and M2 only a small part of the chip is illuminated
and the operator crops to it deliberately. Microclaw read that as a problem and
said so, twice:

> `[5]` **This is a very cropped ROI** (19×17 µm) … I just want to confirm you
> don't actually want the full sensor frame.

> `[49]` **do you want me to widen the camera ROI** … so 300 µm needs far fewer
> tiles and less bleaching?

On most rigs that is good advice. Here it costs a turn every time, and the
operator had to spend one (`[50]` "dont lose the current ROI. proceed").

**Why it recurs.** Microclaw stores per-sample and per-device notes
(`knowledge_manager.CATEGORIES = ("samples", "devices", "strategies")`) but has
no place for *this installation*: what fraction of the chip is lit, whether the
TTL lines shutter the lasers, whether the pixel size has been calibrated, what
the unlabelled single-axis stage is for. `first_launch` produces a
`safety_config.yaml` — limits and authorization, not intent. So every session
re-derives the rig from device names, and re-asks.

**Decision.** After first-launch setup, on the first session that has a working
hardware map and no stored rig profile, Microclaw opens with a **rig interview**
and stores the answers. This is a conversation, not a wizard: the agent already
has `list_devices`, `get_emu_configuration`, `get_roi`, `get_pixel_size`, so it
brings a filled-in draft and asks about the gaps.

Detection is the absence of the profile, not a flag file: `rig` missing from
`~/.microclaw/knowledge.yaml`.

The agenda, minimally:

- **Illuminated field.** How much of the sensor is lit; is the current ROI the
  deliberate crop. → stops F1 recurring, and gates F3's live-mode rule.
- **Illumination topology.** Are lasers shuttered by TTL from the camera; is
  there both a global and a per-line enable. → *this is what makes live view a
  dose*, so F3 depends on it.
- **Pixel size.** Calibrated? If not, walk them through MM's Pixel Size
  Calibration (Automatic — never Manual-Simple, which never measures scale;
  design/29).
- **Unexplained hardware.** Every loaded device with no obvious role, named back
  to the operator one at a time: "what is `PIZStage` used for?"
- **Emission filters.** Which wheel position is which band, when EMU's
  `parameters` block does not already name them (design/39 fills most of this in
  now).

**Stubs.**

`microclaw/knowledge_manager.py` — one more category, and it renders first:

```python
CATEGORIES = ("rig", "samples", "devices", "strategies")

# `rig` describes THIS installation and is not camera-adapter-conditioned the
# way `devices` is (design/21 F4): a devices/ entry can suppress an alarm, so it
# must name the hardware it was observed on. A rig/ entry describes the room.
RIG_TOPICS = ("illuminated_field", "illumination_path", "calibration",
              "device_roles", "emission_filters")


def rig_profile_gaps(knowledge: dict) -> list[str]:
    """Topics with no stored answer. Empty list == the interview is done."""
    stored = knowledge.get("rig") or {}
    return [t for t in RIG_TOPICS if t not in stored]
```

`microclaw/agent.py` — the interview block, present only while gaps remain:

```python
def _system_blocks() -> list[dict[str, Any]]:
    blocks = [{"type": "text", "text": SYSTEM_PROMPT,
               "cache_control": {"type": "ephemeral"}}]
    knowledge = load_knowledge()
    kb_text = format_for_prompt(knowledge)          # now renders rig/ first
    if kb_text:
        blocks.append({"type": "text", "text": kb_text,
                       "cache_control": {"type": "ephemeral"}})
    gaps = rig_profile_gaps(knowledge)
    if gaps:
        blocks.append({"type": "text", "text": RIG_INTERVIEW_PROMPT.format(
            topics="\n".join(f"  - {t}" for t in gaps))})
    return blocks


RIG_INTERVIEW_PROMPT = """## This rig has no stored profile yet

Before the first task of this session, interview the operator about the
microscope and save what you learn with save_knowledge(category="rig"). Bring a
draft: read the rig first (get_roi, get_pixel_size, list_devices,
get_emu_configuration) and ask only about what you could not determine. Ask a
few questions at a time, in the operator's language, and let them skip any of
them — an unanswered topic stays open and you may ask again next session.

Topics still open:
{topics}

If the operator wants to start working immediately, do the task and ask
afterwards. Never block a task on the interview, and never re-ask a topic that
is already stored."""
```

An example stored profile — the entry that would have prevented both quotes
above:

```yaml
rig:
  illuminated_field:
    illuminated_roi: [1364, 1044, 184, 164]
    deliberate: true
    note: >-
      Only a small central region of the chip is illuminated. The crop is the
      operator's, not an accident. Do not propose widening the ROI; a wider ROI
      buys dark pixels, not field of view.
  illumination_path:
    camera_triggers_lasers: true
    note: Lasers fire on the camera trigger (MicroFPGA "Follow"), so any running
      camera sequence — including live view — is exposing the sample.
```

`microclaw/tools.py` — and it has to reach the point of use, not just the
preamble:

```python
@emits_nothing
def get_roi(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
    x, y, w, h = ...
    result = {"x": x, "y": y, "width": w, "height": h}
    # calibration.py already reads the knowledge base directly; same pattern.
    field = (load_knowledge().get("rig") or {}).get("illuminated_field")
    if field:
        result["illuminated_field"] = field
    return result
```

---

## F2 — one illumination approval per session, not one per switch

**What happened.** 17 illumination confirmations in 50 minutes
(13:55:56 → 14:45:33), every one approved, every one for the same two
properties:

```
9 × ENABLE ILLUMINATION: iChrome-MLE-TCP.Laser 3: 1. Enable = '1'   (488)
8 × ENABLE ILLUMINATION: iChrome-MLE-TCP.Laser 2: 1. Enable = '1'   (561)
```

Zero declines. The workflow *is* alternating between two lasers — search in 561,
acquire in 488, repeat — so the gate fires once per half-cycle and asks a
question whose answer was settled at the first tile.

**Decision.** The confirmation answer set grows from {yes, no} to {yes, no, **yes
for this session**}. The grant is per *kind*, created only by a human answering
the prompt (or by the browser's "approve this kind for the session" control),
lives in process memory, is never persisted, and is revocable at any time.

What does **not** change, and must be said in the same breath:

- `illumination.max_power_percent` and `max_power_step_factor` are unaffected —
  they are refusals, not confirmations, and `check_illumination` reaches them
  after the enable gate either way (`safety.py:1122–1157`).
- The authorization map, XY/Z limits, exposure caps and acquisition ledger are
  unaffected.
- `knowledge` and `hook` confirmations are **not** grantable. Those gate
  self-modification, they fire a handful of times per session, and the design/14
  argument for them is unrelated to workflow friction.
- Every auto-approval still writes a row to the confirmations JSONL, marked as
  granted. A silent audit log would be a worse bug than the nagging.

**Stub** — the grant belongs at the `CONFIRM_FN` seam (`tools.py:545–564`), which
is the one place both frontends already share:

```python
class SessionGrants:
    """Kinds the operator has pre-approved for the life of this process.

    Never persisted: a grant that survived a restart would approve illumination
    for a session whose operator never saw the prompt. Not a safety constraint —
    SafetyGuard's limits and refusals are untouched; this only answers the
    question the guard asks a human.
    """
    GRANTABLE = frozenset({"illumination", "acquisition"})

    def __init__(self) -> None:
        self._granted: dict[str, dict] = {}

    def granted(self, kind: str) -> dict | None:
        return self._granted.get(kind)

    def grant(self, kind: str, summary: str, identity: str) -> dict:
        if kind not in self.GRANTABLE:
            raise ValueError(
                f"{kind!r} confirmations cannot be granted for a session; they "
                "gate self-modification, not workflow.")
        record = {"id": uuid.uuid4().hex, "kind": kind, "identity": identity,
                  "granted_at": _now(), "granted_on": summary}
        self._granted[kind] = record
        return record

    def revoke(self, kind: str) -> bool:
        return self._granted.pop(kind, None) is not None


SESSION_GRANTS = SessionGrants()


def _require_confirmation(summary: str, kind: str = "action") -> bool:
    if SESSION_GRANTS.granted(kind):
        print(f"\n[microclaw] Auto-approved under this session's {kind} grant:\n{summary}")
        return True
    print(f"\n[microclaw] Confirmation required:\n{summary}")
    prompt = ("Proceed? [y/N, or 's' for the rest of this session] "
              if kind in SessionGrants.GRANTABLE else "Proceed? [y/N] ")
    answer = input(prompt).strip().lower()
    if answer in {"s", "session"} and kind in SessionGrants.GRANTABLE:
        SESSION_GRANTS.grant(kind, summary, identity="stdin")
        return True
    return answer in {"y", "yes"}
```

`webserve.Session.confirm` (`webserve.py:332`) checks the same registry before it
emits, and records the auto-approval so the JSONL keeps one row per event:

```python
def confirm(self, summary: str, kind: str = "action") -> bool:
    grant = tools.SESSION_GRANTS.granted(kind)
    if grant is not None:
        # Same row shape as a human decision; `decision` distinguishes them.
        return decided("auto-approved", grant_id=grant["id"])
    ...
    # answers from the browser are now "approve" | "approve_session" | "deny"
```

**UI.** The confirmation banner gets a third button, *Approve `<kind>` for this
session*, and the header shows an active grant as a persistent chip with a
Revoke control — an operator must be able to see, at a glance, that prompts are
off and turn them back on. That visible-and-revocable pair is what makes this
safe to ship; a hidden grant is not.

The operator asked for a blanket "deactivate session safety guards" button. This
is deliberately narrower: it turns off *prompts* for the kinds where repetition
is the whole problem, and it never touches a limit. If it turns out
`acquisition` and `illumination` do not cover the friction, widen the grantable
set — do not widen what a grant means.

---

## F3 — live view is a dose, and it is being switched on around the actions

**What happened.** Microclaw started live view on its own initiative at `[11]`
and `[51]`, then every camera operation paused and restored it:

```json
"live_view": "paused for the snap; camera sequence restart verified",
"live_view_restore": {"requested": true, "sequence_running": true}
```

and after a 9-tile acquisition finished, `[14]`:

```json
"live_view_restore": {"requested": true, "sequence_running": true}
```

On this rig the camera trigger fires the lasers (`Mode1 = "4 - Follow"`,
`Sequence1 = 65535` — the trigger pre-flights say so in the same session). So
"restore live view" means "resume illuminating the sample" — before the
acquisition, between the acquisition and the analysis, and after the run is over
while the model writes prose. The sample is bleached on both sides of every
action, which is the exact opposite of what the instruction was for.

**Cause.** `SYSTEM_PROMPT` line 76:

> When it does not interfere with your acquisition, put the camera in live mode
> so the user can see what you are doing.

plus `_pause_live` (`tools.py:717`), which restores unconditionally — correct for
a snap inside a live session the operator started, wrong as a post-acquisition
default.

**Decision.** Three changes, smallest first.

1. **Never leave live running after an acquisition.** Frame-producing entry
   points stop live and do not restore it, and say so in the payload so the agent
   tells the operator rather than leaving them to notice.
2. **The agent does not start live view on its own.** It starts it when asked,
   or when the operator is about to watch something specific (a focus sweep) —
   and on a rig whose profile says `camera_triggers_lasers: true`, it says what
   that costs before starting it.
3. `_pause_live` keeps restoring for *interactive* snaps, which is the case it
   was written for: the operator had live on, we borrowed the camera, we give it
   back.

```python
@contextmanager
def _pause_live(ctrl: MicroscopeController, *, restore: bool = True):
    """Stop live mode for a camera op; restore it only if this was a borrow.

    restore=False for acquisition entry points. On a rig where the camera
    trigger fires the lasers, restoring live after a run means the sample is
    exposed for as long as it takes the model to write its summary — the
    acquisition ends, the bleaching does not (design/43 F3).
    """
    live = ctrl.studio.live()
    was_on = bool(live.is_live_mode_on())
    if was_on:
        live.set_live_mode_on(False)
    state = {"was_on": was_on, "restored": restore and was_on,
             "restore_observed": None}
    try:
        yield state
    finally:
        if was_on and restore:
            ...   # unchanged: set on, then verify via core.is_sequence_running()


def _live_restore_report(state: dict) -> dict | None:
    if not state.get("was_on"):
        return None
    if not state.get("restored"):
        return {"requested": False, "left_off": True, "reason":
                "Live view was running when this acquisition started and was "
                "left off, so the camera is not exposing the sample after the "
                "run. Restart it with start_live_view if you want it back."}
    return {"requested": True, "sequence_running": state.get("restore_observed"), ...}
```

`SYSTEM_PROMPT` line 76 becomes:

> Live view is for the operator's eyes, not yours — you read images through
> `snap_and_analyze` and hooks, never off the live canvas. Start it when they ask,
> or when they are about to watch something worth watching (a focus sweep, a
> navigation), and stop it when that is over. Do not start it "so the user can
> see", and never leave it running after an acquisition finishes. On a rig whose
> profile records `camera_triggers_lasers: true`, a running live view is
> continuous exposure: say so before you start one, and account for it the way
> you account for any other dose.

**Measured 2026-08-09 on M2 (block 43a's gate).** The Preview window stays
open displaying the last frame acquired before live stopped, and does not read
as live. That is the good outcome and the report does not need to be louder, so
this paragraph's contingency never had to be exercised.

**One correction to the decision above, found while implementing it.** The
replacement prompt text named *a focus sweep* as something the operator is about
to watch. `run_autofocus`'s sweep is headless in this codebase — live is paused
for its whole duration and `test_the_sweep_never_touches_the_viewer` pins that —
so the example told the model to start a continuous exposure for an event that
shows nothing. The shipped text keeps only the navigation example.

**Also settled by the implementation:** `find_features` is a borrow, not a run.
It is one on-demand snap, the same shape as `snap_and_analyze`, and
`center_feature` loops over it — so leaving live off there both took over the
operator's session for a single frame and swallowed the explanation, because
`center_feature` returns its own payload. Decision 3's "interactive snaps keep
restoring" covers it. `calibrate_stage_to_camera` and `run_autofocus` do not:
four exposures with stage moves, and 20–60 exposures, are runs.

---

## F4 — the GUI stops tracking after a channel switch

**What happened.** `set_channel` writes four laser enables through
`execute_channel_plan` and the Micro-Manager GUI does not update. The operator
remembers this working, and it did: `set_device_property` calls
`ctrl.studio.app().refresh_gui()` (`tools.py:1103`), and so does the EMU power
setter (`tools.py:5383`). Nothing else does.

Missing it today:

| write path | file:line | refreshes |
|---|---|---|
| `set_device_property` | `tools.py:1103` | yes |
| `set_emu_laser_power_percentage` | `tools.py:5383` | yes |
| `execute_channel_plan` (`set_channel`) | `authorization.py:1409–1474` | **no** |
| `set_focus_lock` | `tools.py:5227` | **no** |

> **This table is one row short, found while implementing (block 43b).**
> `set_channel` has **two** routes, not one: `execute_channel_plan` when the
> session has an authorization map, and `core.set_config` + `wait_for_config`
> when it does not. Only the first is listed above, and it is the route M5
> happens to take — so a block implementing this table exactly would have fixed
> the EMU path and shipped the plain Micro-Manager path still going stale. Five
> callsites shipped. The subject of this finding is the *tool*; the mechanism
> named in a row is how one rig reaches it.

The channel plan is the one that hurts: it is the single most frequent hardware
write in this workflow (17 switches), it changes four properties at once, and it
also runs a rollback path whose whole purpose is restoring state the GUI is
then still showing wrongly.

**Decision.** One helper on the controller, called once per mutating tool call —
including on the rollback path, where the state the operator sees matters most.

**Do not add a debounce timer.** The operator raised it and answered it
correctly ("this might be over-optimization"), and there is a second reason:
pyjavaz holds one lock across every bridge round trip, so a timer thread firing a
refresh cannot preempt an in-flight core call and would simply contend for that
lock at an unpredictable moment. A refresh at the end of the tool call is the
same number of refreshes for a channel plan (one, for four writes) with none of
the concurrency.

```python
# microclaw/controller.py
def refresh_gui(self) -> None:
    """Repaint MM's GUI after microclaw wrote a property.

    From the cache, not from hardware: MMCore's property cache is already
    current — we just wrote through it — and a full hardware re-poll on a rig
    with a flaky serial link is a needless round of reads that can itself time
    out (this session saw two iChrome serial timeouts). Never raises: a GUI
    repaint failing must not fail the write that succeeded.
    """
    try:
        self.studio.app().refresh_gui_from_cache()
    except Exception:
        pass
```

Then in `execute_channel_plan`, after the write loop *and* in the rollback
`except`, and in `set_focus_lock` after its `set_property`. Migrate the two
existing `ctrl.studio.app().refresh_gui()` callsites to the helper so there is
one definition.

**Verify before implementing:** that `Application.refreshGUIFromCache()` exists
on the deployed MM build and shadows as `refresh_gui_from_cache` over the bridge
— `javap` on the local `MMJ_.jar` rather than trusting this paragraph. If only
`refreshGUI()` is available, use it and drop the cache rationale; the fix stands
either way.

> **Answered in full. Off-rig 2026-08-09:** `javap` on the local MM
> 2.0.3-20260625 `MMJ_.jar` reports both `refreshGUI()` and
> `refreshGUIFromCache()` on `org.micromanager.Application`, so the from-cache
> rationale stands and the fallback is not needed. **On M5, 2026-08-09:**
> `callable(getattr(c.studio.app(), 'refresh_gui_from_cache', None))` returned
> `True` on the deployed Windows build, which is the bridge half `javap` could
> not reach. Note the split — `javap` proves a Java method exists; only a live
> call proves pyjavaz shadows it. **Measured beyond the question asked:** EMU's
> own plugin panel repainted alongside the Property Browser, so
> `refreshGUIFromCache` reaches plugin windows and not just the browser.

**Shipped and gated in block 43b (merged 2026-08-09).** M5 observed the Property
Browser repainting after a channel switch, a focus-lock toggle and a direct
property write, with no manual Refresh. The rollback limb is **not** rig-gated:
provoking a plan that fails *after* a write lands would mean manufacturing an
uncontrolled hardware fault, so it is covered by three unit tests over the three
rollback exception exits plus a repaint failure that must not replace them.

One implementation note worth keeping. The rollback refresh is wrapped in its
own `try/except` even though `refresh_gui` never raises, and the success-path
call is not. That asymmetry is deliberate: a repaint error must never replace
the exception that describes the state of the rig.

---

## F5 — "when you see a tile with higher signal, use it to focus" is not expressible

**What happened.** Twice.

`[72]`: *"scan with the current focus but when you see a tile with higher signal
then use it to focus"* — Microclaw could not do that mid-scan. It ran all 324
tiles at a fixed Z with `snr_observer`, then read the log, then moved to the best
tile, then autofocused. Correct outcome, four round trips and a full-area
exposure at the wrong Z.

`[178]`: *"But I saw you pass over cells this time. you didnt recognized the
cells because they were out of focus"* — the 36-tile raster returned
`would_keep: false` everywhere, and the operator was right: the log shows tiles
with `ridge_coverage` 0.17–0.30 and `snr` 1.4–2.2. Cell material, out of focus,
below a gate that only recognises *bright* structure. When each of those tiles
was later refocused by hand, they converged with contrast 9.6–42.8 and were
unambiguous microtubule fields. The hook was not wrong about brightness; it was
asked the wrong question.

**Why it cannot be expressed.** `RequestAutofocus` exists as a typed hook action
(`hook_decisions.py:54`) and `run_adaptive_survey` refuses it
(`hook_decisions.py:450`):

```python
if isinstance(action, (MoveStage, SetExposure, RequestAutofocus)):
    self._refuse(metadata, action, "unsupported-by-run_adaptive_survey")
```

So a survey hook's whole vocabulary is Continue or Stop within a fixed plan. It
cannot say "hold on — refocus here and show me this field again", which is
exactly the sentence the operator said twice.

**Decision.** Make `RequestAutofocus` supported in `run_adaptive_survey`, with a
budget, and give the hook a second look at the refocused field.

Dose is the reason this needs a budget rather than a flag: an autofocus sweep is
20–60 extra exposures, and the survey's reservation was sized for one frame per
tile. The pattern already exists twice in this runner — `illumination_envelope`
and `artifact_limits` are authorized-or-refused capabilities — so this is a third
of the same kind, not a new mechanism.

```python
# tools.py — run_adaptive_survey gains one optional argument
autofocus_budget: dict | None = None,
    # {"max_events": 3, "z_range_um": 30, "z_step_um": 0.5}
    # Absent → RequestAutofocus is refused exactly as it is today.

# hook_decisions.py — _dispatch
if isinstance(action, RequestAutofocus):
    ctx = self._autofocus_context
    if ctx is None:
        self._refuse(metadata, action,
                     "no autofocus budget was authorized for this run")
        return
    if ctx["remaining"] <= 0:
        self._refuse(metadata, action, "authorized autofocus budget exhausted")
        return
    if ctx["refocused_this_tile"]:
        # One refocus per tile. Otherwise a hook that keeps asking walks Z on
        # its own metric with no human in the loop.
        self._refuse(metadata, action, "this tile has already been refocused")
        return
    result = run_autofocus(ctx["ctrl"], ctx["guard"],
                           z_range_um=ctx["z_range_um"], z_step_um=ctx["z_step_um"])
    ctx["remaining"] -= 1
    ctx["refocused_this_tile"] = True
    if not result.get("converged"):
        # run_autofocus already restores Z on a flat or boundary curve
        # (design/28 F1). Record and carry on; do not widen and retry.
        self._accept(metadata, action, "autofocus ran and did not converge; Z unmoved",
                     autofocus=result)
        return
    # Re-expose the same tile and hand the hook the in-focus field. The
    # re-exposure is a frame the reservation must already cover.
    ctx["candidates"].put(ctx["current_event"] | {"microclaw_refocused": True})
    self._accept(metadata, action, "refocused and re-queued this tile",
                 autofocus=result)
```

The hook then sees the same position twice, the second time with
`metadata["microclaw_refocused"] is True`, and answers Continue / Stop / AcquireAt
on evidence instead of on a fixed plane.

**The measurement that makes it usable** is F6's: a hook cannot ask for a refocus
on brightness, because an out-of-focus cell is not bright. It needs "there is
structure here, and it is not sharp" — `structure_coverage` high, focus invalid.
That is precisely the `ridge_coverage 0.30 / snr 1.46` signature this session
threw away.

**On export.** Adaptive runs refuse to emit today. That refusal is wrong and F14
replaces it, so this finding adds no new non-emittable surface — it adds
behaviour that F14 has to carry. Build them in that order if you can: an
adaptive runner that refocuses and re-judges is exactly the script a user wants
to keep, and there is no point making it better at something it cannot hand
over.

---

## F6 — one bright corner beat the whole field

**What happened.** `[79]`, ranking 324 tiles by `snr`:

> **Highest-signal tile: `scan300_488_r12_c15`** — SNR **26.21**,
> focus_metric_valid **true**, mean 1737, max 11885, saturated_fraction 0.0.
> This is far above everything else that's clean.

Microclaw moved there, and autofocus refused: coarse curve monotone to the sweep
edge, fine contrast 0.124. `[85]`:

> the "signal" at this tile is a diffuse bright gradient, not a sharp focusable
> feature.

The tile was a bright blob in a corner. It won because `snr` is a tail
statistic — `(p99.5 − median) / (1.4826 · MAD)` (`image_analysis.py:90`) — and
1% of the frame is enough to define p99.5. The metric is right; it just does not
answer "is this field full of cells?"

The mirror-image error appeared at `[205]`, where `find_features` reported SNR
2.03 on a field of unmistakable in-focus microtubules, because blob detection
looks for puncta and microtubules are extended. Microclaw explained the
disagreement correctly and then had to argue the operator into ignoring a number
its own tool returned.

**Decision.** Add extent to `ImageStats`, next to intensity — one computation, in
the one place statistics are defined (design/23 F7), so every observation record
and therefore `rank_hook_log(metric=…)` gets it with no other change.

```python
@dataclass
class ImageStats:
    ...
    signal_coverage: float       # fraction of pixels above bg + min_snr*noise
    structure_coverage: float    # same, after a σ≈2 px blur: catches diffuse,
                                 # out-of-focus material that per-pixel misses
    signal_concentration: float  # share of total above-background signal held by
                                 # the brightest 1% of pixels; ~1.0 == one corner


def coverage_stats(image, background, noise, min_snr) -> tuple[float, float, float]:
    """How much of the field has signal, and how evenly it is spread.

    snr answers "is the brightest thing here well above noise?" — a tail
    statistic that one bright corner satisfies (design/43 F6). These answer "how
    much of this field is sample?", which is the question a survey is actually
    asking. structure_coverage is deliberately blur-then-threshold: an
    out-of-focus cell is spread and dim, so it fails a per-pixel test while
    still being obviously present (design/43 F5).
    """
```

Then:

- Survey ranking prefers `signal_coverage`, or `snr` gated by it. State it in the
  `rank_hook_log` schema rather than leaving the model to invent a composite in
  prose.
- `SYSTEM_PROMPT`'s image-analysis section gains: *snr is a tail statistic — it
  says the brightest thing in the field is above noise, not that the field has
  content. Never rank tiles by snr alone; use signal_coverage, or say which one
  you ranked on and why.*
- `find_features`'s payload names its own scope: *puncta detector; an extended or
  filamentous field can be strong and score low here.*

Whether these three numbers are the right three is a rig question — check on
beads, on the diffuse field design/36 still owes, and against this session's
saved 561 tiles, which are a free labelled set: six known-positive fields with
`ridge_coverage`/`snr` already logged.

---

## F7 — stop offering the TIFF export, and stop giving the wrong reason

**What happened.** Offered **seven** times — `[121] [123] [127] [145] [221]
[223] [261]`. This document originally said six and listed six; `[223]`, *"(If
you'd rather I also export each to a plain .tiff, say so.)"*, was missed by the
hand-read and found in block 43a's gate when the match pattern was validated
against this session. Most of them carried the same rationale:

> **Export to TIFF** (`export_dataset_as_tiff`) so you can scrub it in FIJI and
> look for moving spots

NDTiff datasets open in Fiji as they are. The export is a real option for
software that needs one file — ThunderSTORM, SMAP, Picasso — and a bad default
answer to "let me look at this".

**Cause.** `SYSTEM_PROMPT:85` and `:149–150` both name the export with no
statement of when it is *not* needed, so the model reaches for it whenever
looking-at-data comes up. And the thing it should reach for instead is
design/42's `open_artifact`.

> **Dependency cleared 2026-08-09.** `open_artifact` shipped with block 42b and
> is demo-gated: a file opens in the running ImageJ, and a dataset directory
> opens the TIFF stack files inside it — including `stitch_test_1`, which
> Micro-Manager's own dataset reader could never read. **F7 is assignable.**
> The claim it rests on is now measured rather than assumed: NDTiff opens in
> ImageJ as-is, because an NDTiff dataset *is* TIFF stack files plus an index
> sidecar. One caveat to carry into F7's wording — opening the stack files does
> not reconstruct multi-channel axis structure (channels are planes, not named
> channels), a known limitation tabled in design/42.

**Decision.** Correct the fact and name the alternative.

```
- Acquisition outputs are pycro-manager NDTiff datasets. NDTiff opens directly
  in Fiji/ImageJ — "so you can open it in Fiji" is never a reason to export.
  export_dataset_as_tiff is for software that requires a single OME/TIFF file
  (ThunderSTORM, SMAP, Picasso, DECODE). Offer it when the user names such a
  tool, once, and not again for the same dataset. When the user wants to *see*
  what was written, open it — do not convert it.
```

and in the SMLM section, keep the export line but attach the reason ("external
localization software requires a single-file TIFF"), so the rule generalises
instead of reading as a habit.

**Shipped and gated in block 43a (merged 2026-08-09).** M2 measured both
directions in one session: *"let me look at what you just acquired"* produced
four `open_artifact` calls and no export offer, and *"I want to run this through
ThunderSTORM"* produced the export with its single-file reason. The second is
the limb that matters as much as the first — a fix that had made microclaw
reluctant to export would have traded one wrong default for another.

**A note for anyone writing a gate criterion against this finding.** The obvious
pattern, `export_dataset_as_tiff`, reads **1** on this session: six of the seven
offers were prose and never named the tool. Use `export.{0,40}tiff`,
case-insensitive, which reads 7 and does not match the mosaic filenames at
`[43]`/`[45]`.

This raises the priority of design/42: F7 and F12 both end at "the operator wants
to look at what we just wrote", and `open_artifact` is the answer to both.

---

# Additional findings

## F8 — `stopped_early: false` was read as "nothing was found"

`[105]`, after a 3-tile adaptive survey:

> The survey completed but **did not stop early** — all 3 tiles were acquired,
> meaning **no tile passed the filament gate**.

Wrong, and self-corrected two turns later at `[107]`: tile `t_c0` scored
`filament_score 0.175, snr 107.3, would_keep: true`. The hook was
observation-only by design and always returns `ContinueSurvey`, so
`stopped_early` says nothing whatsoever about content — but it is the only
content-shaped field in the result, and the measurements live in a separate file
behind `read_hook_log`.

**Fix.** The survey result must not invite a verdict it cannot support:

```python
result["stopped_early"] = stopped
result["hint"] = (
    "stopped_early describes the hook's control decisions, not what was found. "
    "Per-tile measurements are in log_path; call read_hook_log before saying "
    "anything about content."
)
```

and, since the parent already sees every dispatched action, it can state the
fact plainly rather than leaving it to be inferred:

```python
result["hook_actions"] = {"ContinueSurvey": 3, "StopSurvey": 0}
```

> **Shipped and gated in block 43d (merged 2026-08-09), with one correction the
> stub above needs.** `hook_actions` is emitted **only when typed actions were
> actually observed at the parent dispatch, and omitted otherwise** — the stub
> shows it unconditionally, and that is wrong twice over. `_resolve_hook` wraps
> only *saved* hooks in `UntrustedHookAdapter`, so a precoded hook has no counts
> at all; and `HookResult.actions` defaults to `()`, so a saved hook that only
> records measurements has an empty count dict. In both cases an emitted
> `{"ContinueSurvey": 0, "StopSurvey": 0}` states, in the one content-shaped
> field this result has, that the hook decided nothing on a run where it
> continued at every tile. **An empty observation is not a measurement of zero**,
> and emitting it as one recreates this very finding in the field added to retire
> it. Both halves were caught in review; the second only after the first was
> fixed.
>
> M5 measured the working case: a saved hook over three tiles returned
> `{"ContinueSurvey": 3, "StopSurvey": 0}`, and the agent called `read_hook_log`
> before saying anything about content — which is the behaviour this finding
> exists to produce.
>
> **Still owed, and deliberately not widened mid-block:** the payload projects
> only `ContinueSurvey` and `StopSurvey` out of a dict that observes every kind,
> so a hook dispatching only `DiscardFrame` still reads as two zeros. That is a
> narrower instance of the same defect.

## F9 — two hooks were unusable for the whole session, with no remedy offered

`mosaic_cell_counter` and `mosaic_cell_counter_v2` both failed their integrity
check (`matches_manifest: false`, legacy newline pin). Microclaw discovered this
twice, planned around it twice, and offered to *write a new hook* rather than
re-review the existing ones — which is the smaller action and the one the
operator would probably have picked.

`describe_hook` reports the refusal correctly:

```json
"resolve_refusal": {"would_refuse": true, "reasons": [
  "saved hook uses a legacy newline-normalized hash; review and re-save it"]}
```

**Fix.** Two small ones.

1. `list_hooks` marks unusable saved hooks inline, so a hook is never chosen and
   then discovered to be dead: `"resolvable": false` beside the description.
2. `resolve_refusal` carries the remedy as a call, not as prose:
   `"remedy": {"tool": "read_hook_from_file", "path": "…", "then":
   "generate_and_save_hook(source='user_provided')", "reexposes": false}`.
   Re-review costs one read and a human yes; the agent should be able to see
   that.

## F10 — an unknown adapter name was reported as a possible hardware fault

`[40]`:

```json
{"error": "KeyError: \"No saved hook named 'connected_components'.\"",
 "hint": "This may be a hardware error (device busy, stage at limit, device not found) or a connection problem."}
```

`hint_for_error` (`errors.py:52–120`) handles path, permission, argument and
connection errors, then falls through to `_HARDWARE_HINT`. A `KeyError` from the
adapter registry lands in the fallthrough, and the hint sends the reader to the
stage. `errors.py:53` says this exactly: *"A hint that names the wrong subsystem
is worse than none."*

**Fix.** Do not guess from the exception type when the callee knows the answer.
`run_analysis_on_saved_dataset` should refuse by name and list what exists —
`{"error": "No adapter named 'connected_components'.", "available_adapters":
[...]}` — and `hint_for_error` should map `KeyError` to a lookup hint rather than
a hardware one.

The absence behind the error is F15, and it is the larger half.

## F11 — a 25-tile grid was exposed at the wrong place because the center defaulted

`[33]`, caught after the fact:

> **The 75 µm grid did not center where the previous scans were.** … the stage
> was not at (169.3, −1) when this started; it was at (186, 15.9). So this 75 µm
> grid is offset by ~(+17, +17) µm from your original area.

`run_tile_acquisition` defaults the center to the live stage position
(`tools.py:3267`). Its docstring already warns about the *repeat-run diagonal
walk*; this was a different route to the same place — the operator had moved the
stage between requests, and "scan a larger area" meant the earlier area.

**Fix.** Two cheap ones; neither adds an argument.

1. Report the provenance of the center, so an off-target grid is visible in the
   result rather than requiring arithmetic: `"grid_center_source":
   "current_stage_position" | "explicit"`.
2. `SYSTEM_PROMPT`, position-list section: *when a request refers to a previous
   scan's area ("the same area", "a larger area around that"), pass `center_x_um`
   / `center_y_um` from the earlier result. The default center is wherever the
   stage happens to be, which is not the same thing and may have moved.*

> **Shipped and gated in block 43d (merged 2026-08-09). `grid_center_source` has
> three values, not the two above.** One coordinate can be supplied while the
> other defaults — `run_tile_acquisition` already branches on exactly that case
> for its bounds check — and such a grid is neither `explicit` nor
> `current_stage_position`. Calling it either is a false provenance claim, which
> is the class of defect this finding is about. It reports `partially_explicit`.
>
> M5 measured the intended case: asked to scan a larger area around the previous
> scan, the agent passed the earlier centre and said so — *"pinned to that center
> rather than to wherever the stage sits now"* — and the result read
> `grid_center_source: "explicit"`. The criterion discriminated by 40 µm, more
> than one full FOV, because the preceding survey had left the stage on a
> different tile.

## F12 — nine timelapses, and no way to say whether anything was in them

Said after every one of the nine acquisitions, in near-identical words (`[121]`,
`[145]`, `[221]`, `[261]`):

> `run_timelapse` returns no per-frame image statistics, so I can't tell you from
> this result whether kinesin puncta are visibly present.

True: `run_timelapse` (`tools.py:1597`) takes no `hook_strategy`, unlike
`run_tile_acquisition` and `run_multiposition_acquisition`. So the tool the SMLM
and single-molecule paths depend on is the one tool that cannot observe itself,
and its operator gets a completion status where they wanted a first look. This is
also what drove F7's six TIFF offers.

**Fix.** Give `run_timelapse` the `hook_strategy` / `hook_params` / `log_path`
trio the other acquisition entry points already have; `snr_observer` over 150
frames is exactly the "did anything happen" summary that was missing, at zero
extra exposure. If that is more than it should carry, the alternative is a
documented pointer to `run_analysis_on_saved_dataset` in the result's `hint` —
but the trio is the smaller surface, because it is the shape callers already
know.

## F13 — the workflow the operator wanted was nine hand-driven sequences

Each acquired tile cost the same seven-call liturgy:
`set_channel(561)` → `set_device_property(filter, 1)` → `set_focus_lock(off)` →
`move_stage_xy` → `move_stage_z` → `run_autofocus` → `find_features` →
`run_timelapse(561 record)` → `set_focus_lock(on)` → `set_channel(488)` →
`set_device_property(filter, 0)` → `run_timelapse(150, 488)`. Nine times, each
step a model round trip, each illumination change a confirmation (F2).

The composite the session was asking for, from `[90]`, is one sentence:

> the search should be in the 561 chanel and only change to 488 for the time
> acquisition when the tile has microtubules detected in the 561.

Microclaw named the gap itself at `[91]`: *"`run_adaptive_survey` runs one hook
with one channel/exposure for the whole run — it can't itself search in 561, then
switch to 488 and acquire."*

**Not a fix yet — a scope note.** F5 supplies the missing half (refocus and
re-judge mid-survey). The other half is a second acquisition, in a different
channel, at tiles the hook selected. That is a genuine new capability. Worth
designing deliberately, after F5 lands and on the evidence of how it behaves —
not folded into F5 as an extra.

What makes it worth building at all is F14. A "search 561, acquire 488 on hits"
runner that only exists inside a Microclaw session saves this operator nine
sequences once. The same runner, emittable, is the thing they run on every
coverslip for the next year without an agent in the room — which is the whole
premise of the project.

---

## F14 — adaptive runs must be emittable, and the standing refusal is a category error

Three tools refuse to export (`tools.py:3683`, `:3749`, `:4224`), with one reason:

> an adaptive run's events are chosen at runtime by its hook, so there is no
> static event list to render; emitting the positions it happened to visit would
> silently turn an adaptive run into a fixed one

Every clause is true, and the conclusion does not follow. It rules out emitting
the **trace** — the tiles this run happened to visit — and then treats that as
proof that the **program** cannot be emitted. Those are different artifacts:

| | what it is | what it does on the next sample |
|---|---|---|
| the trace | the 3 tiles the hook chose on *this* coverslip | images 3 fixed coordinates, wherever the cells are |
| the program | plan + hook + decision loop | searches, and finds *that* sample's cells |

The trace is what the refusal correctly rejects, and the operator who genuinely
wants it already has a route that emits today: `validate_positions` +
`run_multiposition_acquisition`. The program is the artifact the project exists
to produce — a fixed grid is five minutes of pycro-manager anyone can write, and
"scan until you find a cell, refocus, then acquire" is not. Refusing to emit the
second while emitting the first has it backwards.

**Decision.** `run_adaptive_survey` / `run_adaptive_timelapse` /
`run_adaptive_zstack` emit the adaptive program: the seed plan (static — it is
the recorded `positions`/shape argument), the hook's exact source, and the
runner loop. The `@refuses` decorators come off; `CannotEmit` stays for the
cases that genuinely cannot be rendered (an unresolvable named position, a hook
whose source is not on disk).

The precedent for a design doc overturning an earlier conclusion is design/42,
which retired design/10's "static IJ1 methods are not callable over ZMQ" after
finding the evidence had been misattributed. Same shape here: the refusal
generalised one artifact's problem into a rule about all of them.

### What actually blocks it, honestly

The refusal reason is not the obstacle. These four are, and none is
architectural:

**1. The hook's source.** Two cases, and they are not equally hard.

*Saved hooks* — the easy one, and the one this session used. They already live
as standalone `.py` files in `~/.microclaw/hooks`, they must **not** inherit
`HookBase` (Block 7), and their contract is one function,
`analyze_frame(image, metadata) -> HookResult`. Inline the file verbatim. The
manifest sha256 goes in as a provenance comment, which makes the emitted script
*more* traceable than the analysis inlining is today.

*Precoded hooks* — `HookBase` subclasses. The refusal in `_emit_multiposition`
says inlining them "would import microclaw safety and hook decisions"
(`tools.py:152`). Measured rather than assumed, that dependency is:

```
hooks.py:9    from microclaw import __version__            → a string literal
hooks.py:10   from microclaw.image_analysis import ...     → already inlined by _analysis_source
hooks.py:13   from microclaw.safety import SafetyViolation → one exception class
```

and `hook_decisions.py` has **no** microclaw imports at module level at all —
only two function-local ones. The typed actions are frozen dataclasses over
stdlib and numpy. So the barrier is a version string, an exception class, and a
module that is already standalone. That is a paragraph of emitter work, not a
rewrite.

**2. The runner loop.** `_survey_event_stream` (`tools.py:3975`) is the most
intricate code in the package — a generator that holds pycro-manager's event
source open, an idleness watchdog rather than a deadline, put-before-image_done
ordering, terminator-in-the-finally. It has design/24 and design/27 written into
it, and every one of those subtleties is a bug someone already paid for.

The consequence is a rule, not a blocker: **emit it with
`inspect.getsource`, never by re-writing it in the emitter.** That is already
the exporter's discipline — "the emitted `snr()` *is* the one that ran" — and
this is the case where it matters most, because a hand-copied event loop that
drifts from the real one reintroduces ghost exposures on a rig, silently. The
loop's reach into `acq._event_queue` and `acq._acq.is_finished()` is fine: those
are pycro-manager attributes, and the emitted script has pycro-manager.

**3. The safety dispatch — the one real design question.**
`UntrustedHookAdapter._dispatch` is what makes a saved hook's proposal safe: it
runs `guard.check_xy` on every derived event, enforces the reservation, the
illumination envelope, the artifact budget. A standalone script has no
`SafetyGuard`, so what happens to those checks?

Emit the limits as literals and keep the check. The guard's XY/Z/exposure bounds
come from `safety_config.yaml` — static values, known at export time:

```python
# Rendered from the safety config in force when this script was exported.
_LIMITS = {"x_um": (-5000.0, 5000.0), "y_um": (-5000.0, 5000.0),
           "z_um": (0.0, 200.0), "exposure_ms": (1.0, 500.0)}

def _check_xy(x, y):
    """The bounds Microclaw enforced when this script was recorded.

    A literal, not a live guard: editing this dict edits the limits. It is here
    so a hook's derived coordinate is still bounded by something, and so the
    numbers travel with the script instead of living only in a config file on
    one machine.
    """
```

This does not create a new hole. Emitted scripts *already* write laser enables
with a bare `core.set_property` and no confirmation gate — the exporter's
existing, accepted position is that an exported script is the operator's own
script, run under their supervision, on their rig. Adaptive emission inherits
that position; it does not widen it. The script header should say so in one
sentence rather than leaving it implicit.

**4. The refusal must get narrower, not disappear.** Cases that still cannot
emit: a hook whose source is not recoverable, a named position the record cannot
resolve (already handled), a hook that reached a capability the script has no
equivalent for. Those keep `CannotEmit` with a specific reason. What goes away
is the blanket `@refuses` on the tool itself.

### Stubs

```python
def _emit_adaptive_survey(params: RecordedParams) -> str:
    """Render the adaptive program: seed plan, hook source, decision loop.

    NOT the trace. The positions this run visited are an outcome of one sample;
    re-running them on the next coverslip images bare glass. What travels is the
    rule that chose them (design/43 F14).
    """
    positions = params.get("positions")
    if positions is None:
        if params.get("_position_resolution_error"):
            raise CannotEmit(params["_position_resolution_error"])
        raise CannotEmit("the record contains no resolved seed positions")
    hook_source, provenance = _hook_source_for_export(params["hook_strategy"])
    return "\n".join([
        provenance,                       # path + manifest sha256, as a comment
        hook_source,
        _runner_source(),                 # getsource of the real loop, not a copy
        f"_positions = {positions!r}",
        f"_hook = {_hook_constructor(params)}",
        f"run_adaptive_survey_standalone(mm, _positions, _hook, "
        f"name={params.get('name', 'survey')!r}, **{_shape(params)!r})",
    ])
```

`_runner_source()` follows `_analysis_source()` exactly: `inspect.getsource` over
the real functions, and the test that guards it is the same class as
`test_emitted_analysis_defines_every_name_it_uses` — parse the emitted script,
walk it, and assert every free name is defined. That test exists because block 13
and block 41b were each green alone and broke together on a rig; an adaptive
emitter has strictly more surface for the same failure, so extend the test to
the adaptive path before writing the emitter, not after.

### What this changes elsewhere

- **`CLAUDE.md` needs amending** when this lands. Its export paragraph lists
  adaptive runs as one of three things that "refuse with a reason rather than
  guessing". After F14 the list is two, and the reason text for adaptive runs
  becomes the narrow `CannotEmit` cases. Do not edit it before the code changes —
  the paragraph is accurate about what ships today.
- **F5 stops being a trade-off.** Its "this widens the non-emittable surface"
  caveat is void; refocus-and-re-judge becomes another thing the emitted program
  does.
- **F13 becomes worth building.** A two-channel search/acquire composite that
  cannot leave the session is a convenience; the same composite as a script the
  operator runs unattended on ten coverslips is the product.

---

## F15 — offline analysis ships with no analyses in it

At `[43]`, having failed to run connected components on the mosaic, Microclaw
offered the operator three options, the recommended one being:

> **Option A — I write a small offline connectivity adapter (recommended).**

The operator's reaction is the right one: connected components on a stitched
mosaic should work. It is not a custom integration. It is the same class of
thing as `snr` or blob detection, both of which ship in the box.

**Why it offered to write one.** Because writing one is the only route the
architecture provides. `run_analysis_on_saved_dataset` resolves its `adapter`
argument against the **user's saved-hook manifest and nothing else**
(`completed_dataset.py:66–70`):

```python
manifest = json.loads(MANIFEST.read_text(...)) if MANIFEST.exists() else {}
if name not in manifest:
    raise KeyError(f"No saved hook named {name!r}.")
```

`PRECODED_HOOK_REGISTRY` is not consulted. `image_analysis` is not consulted.
There is no built-in adapter library, so **every** offline analysis — including
the most standard ones — must first be written, linted, reviewed, confirmed and
hash-pinned by the operator. The agent's offer was accurate about its options.

**What makes this sharper is how nearly finished it is.** The mosaic path is
fully plumbed (`completed_dataset.py:337–352`): the runner builds the mosaic,
hashes it, records the calibration identity, reads it into a numpy array, and
hands `(image, metadata, context)` straight to `analyze_saved_frame`. Everything
up to the analysis is done. The last step is missing, and it is the only step
that has a textbook answer.

**The asymmetry.** Live analysis has a package-level library every tool can call
with no hook and no review — `snr`, `tenengrad`, `compute_stats`,
`detect_features`, surfaced as `snap_and_analyze` and `find_features`. Offline
analysis has none of it. So the same field measured live gets a spot count for
free, and measured from a saved mosaic gets a hook-writing project. The untrusted
adapter machinery exists for the case design/26 built it for — ilastik, Cellpose,
somebody's lab script — not for counting blobs.

**And user-supplied answers to standard questions rot.** This operator has two
near-duplicate hooks for this one question, `mosaic_cell_counter` and
`mosaic_cell_counter_v2`, both of which failed their integrity check when needed
(F9). A built-in would not have a hash to mismatch, would not need re-review a
month later, and would not have been written twice.

**Decision.** Ship a small set of reviewed built-in offline adapters, resolved
before the saved manifest, implemented over `image_analysis` so there is one
definition of every measurement (design/23 F7). Start with what this session
asked for and could not get:

- `connected_components` — threshold above the SNR floor, label, filter by
  area in µm² using the mosaic manifest's pixel size, report per-object area,
  centroid in **stage** coordinates, and bounding box. That is the literal answer
  to `[22]`, *"identify if the positive positions belong to the same cell or
  not"*: two positions land on the same cell when they fall in the same label.
- `frame_statistics` — `compute_stats` over saved frames, so a completed
  dataset can be scored without re-imaging. This is also F12's answer for the
  nine timelapses.

```python
# microclaw/completed_dataset.py
BUILTIN_ADAPTERS = {"connected_components": ConnectedComponents,
                    "frame_statistics": FrameStatistics}


def _load_adapter(name: str):
    """Built-ins first, then the operator's saved hooks.

    A built-in is trusted code in this package: no manifest, no hash pin, no
    lint, no confirmation — the same standing image_analysis already has on the
    live path. Saved adapters keep every one of those gates; nothing about
    design/26's untrusted-adapter contract changes (design/43 F15).
    """
    builtin = BUILTIN_ADAPTERS.get(name)
    if builtin is not None:
        return builtin, _verb_of(builtin), {"source": "precoded"}, None
    return _load_saved_adapter(name)
```

and the refusal path from F10 gets something worth printing:

```python
raise KeyError(
    f"No adapter named {name!r}. Built-in: {sorted(BUILTIN_ADAPTERS)}. "
    f"Saved: {sorted(manifest)}. Write one with generate_and_save_hook only if "
    "none of these fits."
)
```

**Where the line goes.** A built-in adapter must be a *measurement*, not a
biological judgement: connected components and per-frame statistics, not "count
the cells with microtubules". The moment an adapter needs a concept from the
user's biology, it is a custom adapter and belongs in the reviewed, hash-pinned
path where design/26 put it. `connected_components` qualifies because the
question it answers — which regions of thresholded signal are contiguous — has
no biology in it; the operator supplies the meaning by choosing the area bounds.

**Also worth checking while in here:** whether a built-in should be reachable
from `find_features`' offline twin at all, or whether the honest fix is that
`find_features` and the offline adapters are the same function with two callers.
The live/offline split currently duplicates the *question* and not the code, and
this session had a field where the two would have disagreed (F6).

---

## Suggested order

1. ~~**F3** (live mode) and **F7** (TIFF prose)~~ — **DONE, block 43a, merged
   2026-08-09, M2 gate PASS.** Prompt and payload text plus one keyword argument
   on `_pause_live`; both stopped active harm.
2. ~~**F4** (refresh_gui)~~ — **DONE, block 43b, merged 2026-08-09, M5 gate
   PASS.** A helper and **five** callsites, not the three estimated here: F4's
   table missed `set_channel`'s second route. The MM method name was settled
   off-rig by `javap`; only the pyjavaz shadow needed the rig.
3. **F2** (session grant) — small, self-contained, needs a UI change and a rig
   gate that the audit log still records every event.
4. ~~**F8 / F10 / F11** (report shapes and hints)~~ — **DONE, block 43d, merged
   2026-08-09, M5 gate PASS.** "Text in payloads, cheap to land together" was
   half right: no behaviour changed, and it still took two review rounds plus a
   coordinator fix, because a field that reports a *measurement* has to
   distinguish an absent one from a zero. F10's refusal message is the base
   block 43e extends.
5. **F15** (built-in offline adapters) — the mosaic path is already plumbed to
   the analysis boundary; this is the missing last step, and it retires F10's
   error and half of F12.
6. **F1** (rig profile + interview) — one knowledge category and one prompt
   block; ships the fact F3's rule wants to condition on.
7. **F6** (coverage statistics) — one place, but it needs rig calibration before
   anything ranks on it.
8. **F14** (emit adaptive runs) — larger, and the highest-value item here: it is
   what turns a session into something the operator keeps. Independent of F5, and
   better landed first so F5 is built inside a runner that already exports.
9. **F5** (RequestAutofocus in the survey) — depends on F6 for the measurement
   that makes it worth asking for, and on F14 for being worth keeping.
10. **F9 / F12** — hook usability and timelapse observation, whenever their files
    are next open.
11. **F13** — design first, after F5 and F14 have run on a rig.

F14 and F15 are the two that change what Microclaw *is* rather than how well it
behaves: one makes the adaptive work portable, the other makes the standard
analyses available without a hook-writing project. Everything above them is
friction removal.
