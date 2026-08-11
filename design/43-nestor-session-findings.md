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

> **Shipped and gated in block 43f (merged 2026-08-11), over two demo rounds and
> one M5 round.** The decision holds and the stubs are right about the shape.
> Seven corrections and additions.
>
> **`CATEGORIES` is not the only list of categories**, and this was found by
> reading the code at assignment rather than on a rig. Three tool schemas
> enumerate `["samples", "devices", "strategies"]` independently, so the stub as
> written adds a category the agent cannot write. A test now pins the three enums
> to `CATEGORIES`. This is 43e's "correct and unreachable" one layer down.
>
> **The interview prompt's trigger is load-bearing, and this section's own stub
> is the only place it survived.** The stub opens *"Before the first task of this
> session, interview the operator…"*. The implementation rewrote the heading —
> correctly, because the block also renders for a *partial* profile, so "This rig
> has no stored profile yet" was false — and the when-clause and the imperative
> went with it. What shipped described *how* to interview and never said when,
> and its most forceful sentence was the negative *"never block a task"*. On the
> demo machine the agent opened two sessions and asked nothing, with the block
> present and five topics open. A prompt that describes an activity without
> commanding it does not run.
>
> **`rig/` keys are the profile's topics, and anything else is refused.** The
> stub is silent on keys, and free keys defeat the design quietly: asked to
> "store this ROI as my permanent crop", the agent invented `saved_roi`, which
> closed no topic — so the interview would re-ask `illuminated_field` forever —
> and never reached `get_roi`. In the same reply the agent recited
> `illuminated_field` as still open without connecting it to what it had just
> saved, so the open-topics list in the payload is necessary and not sufficient.
> The refusal is the same shape as the `observed_on` refusal already in that
> function, and fires before the confirmation. **On M5 it never fired**: the
> agent read the schema's key description, said `rig` takes only the five topics,
> and filed the fact correctly — the cheapest place to stop a bad call is the
> schema the model reads before making it.
>
> **The reach is three tools, not one.** `set_roi` and `clear_roi` carry
> `illuminated_field` too. `clear_roi` is this finding's harm executed rather
> than proposed.
>
> **Five topics cost five confirmations.** `save_knowledge` is gated
> `kind="knowledge"`, which block 43c made permanently non-grantable, and one
> entry per topic was chosen deliberately so a skipped topic stays independently
> open.
>
> **The interview must not name an EMU tool unconditionally** — most rigs have
> none, and on the demo machine `get_emu_configuration` answers with a request
> for an app directory. Made conditional; M5's agent reached for
> `check_emu_installed` first, which is what the condition intends.
>
> **What M5 measured that no demo could.** `get_roi` carried
> `illuminated_field`, and the agent planned a 9×9 grid inside a 33 × 34 µm field
> rather than widening; asked outright whether to widen the ROI it declined, on
> the physical ground that the extra pixels sit outside the illuminated cone —
> the inverse of the two unprompted widening offers this finding was written
> about. With `camera_triggers_lasers` stored under `illumination_path`, it
> warned before a 16-tile survey and again before a live view that both are
> 640 nm dose. **That is F3's `SYSTEM_PROMPT` sentence firing for the first time
> since block 43a shipped it on 2026-08-09**, having read a key nothing wrote
> until now: a prompt conditioned on a fact no code produces is dormant, not
> shipped, and it took two blocks to make one sentence real.
>
> **One finding this gate produced that belongs to F3, not here.** Before the
> trigger fact was stored, the agent started live view unprompted — *"so you can
> watch the survey"* — with 640 enabled. F3's rule says *do not start it "so the
> user can see"*, and that is the sentence it used. Carried forward rather than
> folded into a gated branch.

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

> **Shipped and gated in block 43c (merged 2026-08-09), over three M5 rounds.
> The stub above is wrong in one word: a grant is keyed on `kind` AND
> `subject`, not `kind`.** Five call sites reach `CONFIRM_FN`, and only two are
> the decision this finding's operator made seventeen times. Grantable:
> `illumination/enable` and `acquisition/threshold`. Subject-less and therefore
> permanently one-shot: the `Core.Shutter` retarget, the unattended hook
> illumination envelope, and MMStudio's current MDA.
>
> **M5 proved why.** With an `illumination/enable` grant active,
> `AUTHORIZE UNATTENDED HOOK ILLUMINATION` prompted **twice** — each one handing
> generated hook code a power ceiling for a whole unattended run. Under the stub
> as written, both would have been silent. In the same session a `knowledge`
> save and a 5000-frame `acquisition/threshold` plan also prompted, and both were
> declined; 110% and a 1%→50% step were both **refused** by the guard with the
> grant active, because a grant answers the question the guard asks a human and
> removes no limit.
>
> **The audit log came out richer than this section describes.** It is not one
> row per auto-approval: the grant *lifecycle* is recorded too, at the point it
> happens — `granted:<id>` at creation, `revoked:<id>` at revocation — and the
> grant is rolled back if its creation row cannot be written, so prompts can
> never be off under a grant whose origin is absent from the log. M5's first
> session reads: `granted` → `approved:session` → 5 × `auto-approved` →
> `revoked` → a plain `approved` → `granted` → `approved:session` →
> `auto-approved`. Nine enables, three prompts, twelve rows, every row carrying
> the summary that names the device, property and value.
>
> **Revocation is asymmetric between frontends** and that is documented rather
> than hidden: the browser can revoke mid-turn, the terminal's `grants` command
> only between turns, because the REPL is blocked inside the turn where
> auto-approvals happen.
>
> **On the intent case (43b's gate).** The block's position is that a grant
> cannot infer intent and the audit row is the only backstop. G6 tested it and
> **the hazard did not reproduce**: three plain "turn it off" requests all
> produced a direct write of the enable property to `0`, never `set_channel`.
> That is a negative result, not a proof — 43b's case came from different
> phrasing. Separately, and unprompted, the agent *did* stop to ask when a
> request was ambiguous in the other direction ("turn on 640" while 488 was on,
> which `set_channel` would have silently turned off), which suggests the
> agent's own clarification may be a better backstop than a prompt was.
>
> **One finding this gate produced that belongs with F2's authority questions
> rather than inside it.** Told three times to step power 1%→50% in a single
> write, while the operator was explicitly trying to observe a tool limit, the
> agent refused and substituted its own ramp — *"the gradual step-up is a safety
> rule I follow … not a limitation I can waive just because it was requested."*
> It is not a rule in the code; it is a habit taught by the ratchet's own "Step
> up gradually" message. One authorized write became three, and the limit stayed
> hidden until the operator insisted a fourth time. **A model-invented rule must
> not override an explicit instruction**, and a decline should say which of the
> two it is.

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

> **Measured 2026-08-10 (block 43g), and the paragraph above does not hold.**
> The 36-tile raster is `mt_search_561/filament_raster.json` + `mt_raster_1`;
> control exact. `structure_coverage` is **not** high on those tiles. It is
> **0.0000** on all six tiles with real material, while all 27 bare-glass tiles
> read `signal_coverage` 0.0031–0.0049 — a complete **inversion**, glass above
> sample on every one of the three statistics.
>
> The cause is not tuning. Those tiles are **20× brighter** than glass (median
> 3963 vs 220); their `snr` is low because a frame uniformly full of signal has
> an enormous MAD. The signal has become the background, and no threshold of the
> form `background + k · noise` can see it. Blur does not help: blurring
> something already uniform changes nothing.
>
> **The premise "an out-of-focus cell is not bright" is backwards for this
> data** — it is bright, and *flat*, which is a different problem needing a
> different measurement.
>
> **`ridge_coverage` is not the answer either, though it looked like it.** Its
> separation on these tiles is circular — the material/glass labels were defined
> with it. Spearman against median intensity is 0.649. The only tile pair where
> it disagrees with plain intensity (frames 12 and 21 of the stack) the operator
> **could not tell apart by eye**, and the two tiles that are confirmed cells
> (frames 20 and 17) are the two brightest in the raster, so intensity finds
> them. One reframing worth keeping, the operator's: Sato flagging beads as well
> as filaments is the hook's *name* being wrong, not its measurement — as a
> general "is there structure here" detector it was right on both sample classes.
>
> **So this finding's mechanism is its own answer.** What identified cells in
> both F5 and F6 was the focus response: F6's tile was exposed by autofocus
> refusing it (contrast 0.124), and these tiles were confirmed by hand-refocus
> converging at 9.6–42.8. **F5 is not blocked on a measurement; F5 is the
> measurement.** What it needs from 43g is only a cheap trigger — is there more
> light here than background, worth spending a sweep on — and the test is
> asymmetric, which is what makes a budget affordable: convergence does not prove
> cells, but failure to converge disproves them.

**On export.** Adaptive runs refuse to emit today. That refusal is wrong and F14
replaces it, so this finding adds no new non-emittable surface — it adds
behaviour that F14 has to carry. Build them in that order if you can: an
adaptive runner that refocuses and re-judges is exactly the script a user wants
to keep, and there is no point making it better at something it cannot hand
over.

> **Implemented and gated, block 43i, merged 2026-08-11 (`dd63070`).** M5 PASS at
> round 5 of five. Six corrections to the above.
>
> **The stub's dispatch is wrong and would have made the branch unemittable.** It
> calls `run_autofocus`, the *tool*, which reaches `_pause_live`,
> `get_focus_lock_state`, `_sweep_payload` and thumbnails — none of which exist
> standalone. What shipped calls `_run_autofocus_passes`, which
> `_analysis_source(include_autofocus=True)` **already inlined into every emitted
> adaptive script** before this block existed; the script already binds
> `mm = SimpleNamespace(core=core)`, and the sweep touches only `ctrl.core`. The
> export cost was therefore close to zero and **43i added no new `CannotEmit`** —
> the ordering advice in the paragraph above was right for a better reason than
> it gave.
>
> **"The re-exposure is a frame the reservation must already cover" is false.**
> `candidates.put()` is the only path that increments `emitted` against
> `max_events`, so a re-queue outside it is an exposure outside the committed
> reservation (design/27). The authorized budget widens the cap by its own
> maximum; the *completion total* is a different quantity and rises only as
> re-exposures are really queued. Sizing those two with one number failed twice on
> the rig — first dropping a refocus granted at the last tile, then stalling every
> survey that did not spend its budget.
>
> **`configure_adaptive` had neither `ctrl` nor `current_event`**, and `cursor`
> is not the tile just imaged (`AcquireAt` breaks that relation). The current tile
> is resolved from MM-stamped metadata through `HookBase.where`.
>
> **`microclaw_refocused` in image metadata was an assumption with no precedent.**
> Nothing in the codebase put a custom key into an event and read it back off the
> image. The flag is carried **parent-side** and injected into the copy handed to
> `analyze_frame`, so it never depends on Micro-Manager propagating anything.
>
> **The second look needs its own axis, and this is the finding the log could not
> see.** NDTiff keys frames by their exact axis set: re-using the first look's
> axes replaced it in the readable index, and stamping `refocus=1` on the second
> look alone left the axis *ragged*, so every reader enumerating the axis product
> — `export_dataset_as_tiff` does — generated only `refocus=1` cells and dropped
> every first look (measured: 4 real frames in, 1 out). The plan now carries
> `refocus=0`, and a survey with no authorized budget keeps its original axes
> exactly. **The hook log recorded success in both defects**; only the dataset
> showed them.
>
> **`RequestAutofocus` can be granted and still queue nothing**, and the asymmetry
> this finding is built on guarantees it: a non-converging sweep is the normal
> outcome that disproves cells, and it re-queues nothing. A hook returning
> `RequestAutofocus` alone therefore ends its own survey by omission, on the idle
> watchdog. `hook_docs` prescribes `(RequestAutofocus(), ContinueSurvey())`; a
> refused or non-converged refocus lets the routing action behind it through,
> a granted one defers it and re-asks at the second look.
>
> The gate criterion this finding's blockquote set — spend a refocus on a tile
> like frames 20/17 and report whether it converged, and report that it did not on
> a tile like `scan300_488_r12_c15` — was met on beads rather than on those tiles:
> convergence with a second look at the last planned tile (round 3), and
> non-convergence with `Z restored` and the survey carrying on (round 4).

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

> **Measured, and this finding's account of its own evidence is wrong.**
> Block 43g, merged 2026-08-10. The three statistics were computed over the 324
> saved tiles of this session's own raster, joined to the observation records the
> shipped code wrote on the day (control exact: recomputed `snr` reproduced
> logged `snr` to 0.0000).
>
> **`scan300_488_r12_c15` is not a bright corner.** It reads `signal_coverage`
> **0.148** — a broad bright region covering 15% of the field — and
> `signal_concentration` **0.137**, sitting inside the 0.09–0.14 band that every
> good tile occupies. The paragraph above reasons from "1% of the frame is enough
> to define p99.5", and that is true of p99.5 but not true of this tile. **The
> statistic added to catch this case does not flag it**, and coverage moves the
> tile only from rank #1 to rank #2 of 324.
>
> **What actually disqualified it is in this session's own record**: autofocus
> refused it, coarse curve monotone to the sweep edge, fine contrast 0.124 — the
> quote at `[85]` calls it "a diffuse bright gradient, not a sharp focusable
> feature". That is a sharpness property, and no extent statistic separates a
> large diffuse glow from a field of cells, because on extent they are alike.
>
> **A texture measure was proposed as the fix and withdrawn the same day.** See
> F5's note below and the checklist's carried-forward register: its apparent
> separation was circular, and the one tile pair where it disagrees with plain
> intensity is not distinguishable by eye.
>
> **What 43g does deliver** is a better gate, which is a smaller claim than this
> finding makes: a coverage threshold of 0.05 holds 5–6 tiles of 324 across
> `min_snr` 2.5–3.5, where `min_snr` itself moves 13→60 tiles in one step from
> 3.1 to 2.8. On **beads** — six fields, join verified — coverage is the *only*
> ranking signal available, because every bead field clips a few bead centres and
> `snr` is refused on all of them. Coverage carries its own clipping limit (1%,
> 100× looser than snr's) for exactly that reason.
>
> **This finding is not closed.** What it asks for needs the focus response, not
> a still-frame statistic; that is F5's mechanism, and the two findings have the
> same answer. See `design/43-block43g-gate.md` for the full study and
> `design/43-block43g-offline-study.py` to re-run it.

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

> **Shipped and gated in block 43j (merged 2026-08-11), with one correction the
> stub above needs.** `list_hooks` marks `resolvable` inline off the same
> `describe_saved_hook` the exporter already consults, and the demo gate produced
> the behaviour this finding exists for: the agent reported the hook unusable
> *before* attempting a run and offered re-review rather than writing a third
> hook.
>
> **The remedy is not always the remedy, and attaching it to every refusal made
> the payload lie by omission.** Round 1: told a hook was refused for a hash
> mismatch **and** for subclassing `HookBase` **and** for taking `log_path`, the
> agent called the last two *"just describing its structure, not faults"* and
> offered a re-review that could not have worked — re-saving the same bytes
> reproduces both. **A pin refusal is about this file's provenance and re-review
> clears it; a source refusal is a property of the source and needs the source
> changed.** The remedy now carries `insufficient_for` and says so, and round 2
> measured the agent making exactly that distinction unprompted.
>
> **What this gate found upstream of F9, and did not fix:**
> `generate_and_save_hook` saved that hook in the first place.
> `validate_hook_contract` checks for `analyze_frame` only when
> `runner_contract="adaptive"`, so the default path validates against a weaker
> contract than `_resolve_hook` enforces — a hook can be saved, reported
> successful, recommended for a timelapse, and be **dead on arrival**. F9 made
> that visible where the hook is chosen; it is preventable one step earlier, and
> `describe_saved_hook` already computes the whole refusal set. Carried forward
> in the checklist's open register.

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

> **Extended after block 43e's M5 gate (2026-08-09).** 43d mapped `KeyError` to a
> lookup hint, and the first two calls of 43e's gate session still collected
> `_HARDWARE_HINT`: `NotADirectoryError` (a `.tif` passed where a dataset
> directory belongs) and `FileExistsError` (the output directory already
> existed). Both are `OSError` *siblings*, not `FileNotFoundError` subclasses, so
> they fell past the path branch — on a tool documented as zero-hardware-action.
> Each now says the useful thing instead. **Fixing a hint taxonomy by exception
> type leaves the siblings behind**; the general rule this suggests, and which is
> not yet implemented, is that a tool which touches no hardware should not be
> able to emit a hardware hint at all.

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

> **Half retired by block 43e (merged 2026-08-09), after the fact rather than
> during the run.** `frame_statistics` scores a completed dataset's frames
> offline, and M5's gate answered exactly this question over two of the nine
> kinesin timelapses at zero exposure — *"no reliable evidence of anything
> visibly present"*, with the reasoning and the caveats. **What remains owed is
> the during-the-run half**: the operator still learns nothing until the
> acquisition is over and someone thinks to ask.

> **CLOSED by block 43j (merged 2026-08-11) — and the fix above is not the fix
> that shipped, because the tool this finding asks for already existed.**
> `run_adaptive_timelapse` took `hook_strategy` / `hook_params` / `log_path` over
> the same events `run_timelapse` builds, and listed `snr_observer` in its own
> schema. Adding the trio to `run_timelapse` as written would have shipped a
> third overlapping timelapse surface.
>
> **What was actually wrong was the description and two missing arguments.** The
> twin was advertised as being *"for adaptive behaviour — the hook adapts settings
> (exposure, focus) between frames"*, so **observation — this finding's entire
> request — was nowhere named**, and the Nestor agent said *"`run_timelapse`
> returns no per-frame image statistics"* nine times rather than reach for it.
> And it took neither `exposure_ms` nor `laser_slot`, so it could not serve the
> SMLM path this finding invokes as its reason for naming `run_timelapse` at all.
> **43e's lesson, arriving from a third direction: a capability nothing names is
> not shipped.**
>
> **What shipped is the fold.** One `run_timelapse` and one `run_zstack`, each
> with an optional hook; both `run_adaptive_*` twins deleted, no shim. The Z-stack
> pair went with it by a second operator ruling the same day: folding one pair
> alone leaves a surface where a hook attaches to a timelapse and not to a
> Z-stack, and the hazard is not the rejected argument — a loud `TypeError` — but
> **inference from absence**, an agent concluding from a tool list that timelapse
> observation is unsupported. That is this finding's own failure recreated by its
> fix. `run_adaptive_survey` is not folded: early stopping, a seed position list
> and a generator runner make it a different tool.
>
> **The demo gate measured the reach on first contact.** From an operator sentence
> naming no tool — one that 43e's offline `frame_statistics` could have answered
> without touching the block — the agent replied *"a timelapse with per-frame
> signal logging is exactly the `snr_observer` hook"* and called
> `run_timelapse(hook_strategy=…)` then `read_hook_log`. 20 records for 20 frames,
> 10 for 10 planes; the hooked result a strict superset of the plain one; the
> hookless result carrying no hook fields at all.
>
> **M5 closed the limb no demo could**: 200 frames at 20 ms on `laser_slot=3` with
> the hook attached returned `trigger_preflight: trigger line is armed` **and** a
> 200-record log, and with the trigger gated off the same call was refused by
> name with nothing acquired. **F12 is closed in both halves** — offline by 43e,
> during-the-run by 43j.

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

> **Designed by block 43k, merged 2026-08-11 (`f3e19ee`), and the sentence above
> is wrong about which half was missing.** Selecting a tile is already solved:
> `AcquireAt` resolves a planned index or unique label, guards XY/Z, charges the
> adaptive cap and queues the event (`hook_decisions.py:634`–`668`). What is
> missing is applying a **different channel, exposure and acquisition shape** to
> that selection. The capability is real but narrower than "a second acquisition
> at tiles the hook selected" implies.
>
> **The mechanism is one optional `acquire_on_hit` argument on
> `run_adaptive_survey`** — no new tool, no new hook action. With it present,
> `AcquireAt` records a bounded, deduplicated hit together with the parent's
> current focus Z; after the search stream closes the parent switches channel
> once, restores each hit's Z, and runs one batched timelapse or relative Z-stack
> over the hits. Both channels are **parent-applied phase settings over
> channel-less events**, because `_build_acquisition_events` expresses a channel
> only as Micro-Manager's hard-coded `"Channel"` axis and **M5 has no such
> group**. The refusal that enforces this had already written the design into its
> own error message: *"Call `set_channel` first and run the acquisition without a
> channel argument — once per channel if the run needs more than one"*
> (`tools.py:1911`–`1933`).
>
> **Two of F13's premises changed under it before the design was written.** 43i's
> accounting rule means a 488 burst needs its own planned reservation rather than
> an event derived after authorization — search dose and worst-case acquire dose
> are reserved separately, before the first exposure. And 43j's fold means
> `run_timelapse` and `run_zstack` already take hooks, so the composite is built
> from tools that exist rather than from adaptive twins that no longer do.
>
> **The design's own hole was focus, and it is the half this finding's session
> actually performed.** A survey supports no per-position Z and `RequestAutofocus`
> moves Z globally, so a second pass would image **every hit at whatever Z the
> last refocus left** — while the Nestor liturgy autofocused at each tile before
> its 488 burst. Hits therefore carry the converged plane, reusing the
> `{x_um, y_um, name, z_um?}` position shape `run_multiposition_acquisition` and
> its emitter already support. A composite that could not do this would not
> replace the nine sequences it exists to replace.
>
> **Two open findings stay inputs rather than guarantees.** `laser_slot` proves
> only that a trigger line is armed — not that the laser's enable or emission path
> is live — so the design relies on verified channel-plan writes instead; and the
> runner must not start live view, which on a camera-triggered-laser rig is dose
> outside both reservations.
>
> The specification is `design/44-two-channel-search-and-acquire.md`; block 43n
> implements it.

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

> **Shipped and gated in block 43h (merged 2026-08-11), over four review rounds,
> three demo gate rounds and three M5 rounds.** The argument above holds and the
> analysis under it is accurate — including the dependency audit, which was
> checked line by line at assignment. Six corrections and additions this section
> does not say:
>
> **"A named position the record cannot resolve (already handled)" was false.**
> `_resolve_recorded_position_names` short-circuited on the tool name, so
> `positions` and `_position_resolution_error` — the two fields the stub reads —
> were never populated for an adaptive run at all. Extending the resolver was not
> enough either: on M5 a session that called `get_position_list`,
> `validate_positions` **and** `mark_position` still could not re-derive `pos_1`,
> and the whole export came back `emitted_calls: 0`. The answer is that the run
> already recorded the coordinates it resolved — `tiles_planned` — which is the
> same result-derived route `_emit_multiposition` takes.
>
> **The stub is one tool of three.** `run_adaptive_zstack` and
> `run_adaptive_timelapse` have no position list at all; their seed plan is a Z
> range or an `(n_frames, interval)` pair, and the whole difficulty is the hook
> and the loop.
>
> **The loop's `hook` is the `UntrustedHookAdapter`, not the user's hook.**
> `_survey_event_stream` calls `note_stalled`, `note_aborted` and
> `note_budget_exhausted`, and `_dispatch` is what turns a `ContinueSurvey` into
> the next event. "Inline the file verbatim" emits a runner calling methods
> nothing defines; the adapter, `HookBase.where` and
> `analysis_observation_record` are inlined too.
>
> **Emitting standalone code is not the same as emitting microclaw-free code.**
> Inlined source keeps its own `from microclaw...` imports, and the first
> implementation satisfied them by faking four modules into `sys.modules` — which
> ran, read as a dependency, and left `microclaw.image_analysis` unshimmed so a
> hook importing `snr` would `ImportError` beside the inlined `snr`. The imports
> are now removed rather than shimmed, and the emitted script is parsed before it
> is written.
>
> **Saving a hook halted the script that inlines it.** `generate_and_save_hook`
> carried no export decoration, so a session that wrote the hook it then used —
> the exact workflow this finding is about — planted a `raise` three lines before
> its own adaptive program. A tool with no emitter is not a neutral omission.
>
> **The gate found a pre-existing defect that only a rerun could expose.**
> `run_adaptive_survey` counted completion in positions against a plan in events,
> so a multi-frame survey truncated on a race: live 5 frames, exported 12, same
> program, both reporting `stopped_early: false`. Fixed in runner and emitter
> together and measured at 9 of 9 on M5. **Being able to run the same program
> twice is itself a diagnostic** — which is what this finding buys, beyond
> portability.
>
> **What the rig measured.** M5's 50 ms survey reproduced to every digit with
> Microclaw closed: identical saturation statistics, identical Continue/Stop
> decisions, matching frame counts. A second survey in the same session used a
> zero saturation tolerance and diverged — but two runs of the *same emitted
> script* disagreed with each other, which puts that nondeterminism in the
> specimen rather than the program.

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

> **Shipped and gated in block 43e (merged 2026-08-09), over three M5 rounds.**
> `connected_components` and `frame_statistics` resolve before the saved
> manifest, with no manifest, hash pin, lint or confirmation; every design/26
> gate on saved adapters is intact and now regression-pinned. Four corrections
> and two carried-forward findings this section does not say:
>
> **1. Shipping the adapters did not make them reachable, and that was the whole
> of round 1.** Asked three times, in the operator's own words, whether positions
> belonged to the same cell, microclaw never called `connected_components`. It
> guessed `frame_stats`, read a refusal naming both real built-ins, and answered
> from a mosaic it opened and interpreted by eye. The tool description said *"one
> reviewed, hash-pinned offline adapter"* — true of the saved path, false of
> these — and `SYSTEM_PROMPT` had a branch for *writing* an adapter and none for
> using the ones that ship. **A built-in library is a prompt-and-schema change as
> much as a code change**; F15's stub is silent on this and it is the single
> most important thing the block learned.
>
> **2. The threshold's provenance is part of the measurement.** F15's stub takes
> `min_snr` as a plain parameter. It is resolved at the runner boundary through
> `resolve_min_snr` (explicit → `guard.analysis_min_snr` → labelled fallback) and
> recorded as `min_snr_source` beside the value, because a manifest reading
> `min_snr: 3.1` cannot otherwise distinguish an operator's choice from the
> package guessing. Both branches were measured on M5.
>
> **3. A built-in has no manifest entry, and the runner needs one.** `entry` and
> `source` are read in four places, one of which (`_sha(source)`) raises on the
> stub's `None`. A built-in's `analyzer` block records `source: "builtin"`, the
> package version, and the sha256 of `inspect.getsource` of the class — the same
> discipline the exporter uses for `snr`, and it makes the record pin the exact
> code that ran.
>
> **4. Built-ins emit `observed`**; saved adapters stay restricted to
> `unverified`/`provisional`. The status is about scientific verification, and
> package code has the same standing here that `image_analysis` has live.
>
> **The measurement is right and its default threshold is not calibrated, which
> changed the answer.** At `min_snr` 3.1 it returned 49 objects and split one
> cell across the tile grid — *"not all the same cell"*. The operator looked at
> the mosaic and said one cell; at `min_snr` 2.0 it returned a single 700.8 µm²
> object and a Fiji overlay bounded it. Nothing failed: the `package_default_
> uncalibrated` label did exactly its job. **But the first real use of a built-in
> measurement produced a wrong biological conclusion from an uncalibrated
> placeholder**, which is F6's argument arriving from a second direction and
> raises the priority of the coverage-statistics work.
>
> **Two things F15 asked for that this block did not ship**, both the same
> asymmetry seen from a new angle, both deliberately left rather than folded into
> a gated block:
>
> - **The result cannot be seen.** `connected_components` returns areas,
>   centroids and boxes as numbers and writes only the plain mosaic. The operator
>   said *"I don't see any segmentation draw on the image"* and the session ended
>   with a hand-written Fiji macro. The runner already hands every adapter an
>   artifact directory; a label map or outline TIFF belongs there.
> - **There is no offline blob detector.** Asked whether *localized* features are
>   present, microclaw correctly said it would have to write an adapter:
>   `detect_features` is live-only. So F15's closing question answers itself in a
>   way this section did not anticipate — the live/offline split does not need
>   `find_features` unified with connected components (they measure different
>   things), it needs `detect_features` to have an offline caller.

---

## Suggested order

1. ~~**F3** (live mode) and **F7** (TIFF prose)~~ — **DONE, block 43a, merged
   2026-08-09, M2 gate PASS.** Prompt and payload text plus one keyword argument
   on `_pause_live`; both stopped active harm.
2. ~~**F4** (refresh_gui)~~ — **DONE, block 43b, merged 2026-08-09, M5 gate
   PASS.** A helper and **five** callsites, not the three estimated here: F4's
   table missed `set_channel`'s second route. The MM method name was settled
   off-rig by `javap`; only the pyjavaz shadow needed the rig.
3. ~~**F2** (session grant)~~ — **DONE, block 43c, merged 2026-08-09, M5 gate
   PASS over three rounds.** "Small and self-contained" was right about the code
   and wrong about the questions: the grant key needed a subject, the audit log
   needed the grant's *lifecycle* and not just its uses, and revocation had to
   reach the terminal. The rig gate did record every event — and showed why the
   subject matters, by prompting twice for an unattended hook envelope that the
   stub's kind-only grant would have approved silently.
4. ~~**F8 / F10 / F11** (report shapes and hints)~~ — **DONE, block 43d, merged
   2026-08-09, M5 gate PASS.** "Text in payloads, cheap to land together" was
   half right: no behaviour changed, and it still took two review rounds plus a
   coordinator fix, because a field that reports a *measurement* has to
   distinguish an absent one from a zero. F10's refusal message is the base
   block 43e extends.
5. ~~**F15** (built-in offline adapters)~~ — **DONE, block 43e, merged
   2026-08-09, M5 gate PASS over three rounds.** "The missing last step" was
   accurate about the code and wrong about the work: the adapters were correct
   and unreachable until the tool description and system prompt named them.
   F10's error text is extended and half of F12 is retired offline. Two
   successors are owed — a visual artifact for the measurement, and an offline
   caller for `detect_features`.
6. ~~**F1** (rig profile + interview)~~ — **DONE, block 43f, merged 2026-08-11,
   demo rounds 1–2 and M5 round 3 PASS.** "One knowledge category and one prompt
   block" was right about the code and wrong about where the difficulty was:
   both defects were in prose. The prompt described how to interview and never
   said when, and the knowledge key was free-form, so a rig fact could be stored
   under a name that closed no topic and reached no tool. It does ship the fact
   F3's rule conditions on — and F3's sentence, shipped in 43a on 2026-08-09,
   had been reading a key nothing wrote until this block.
7. ~~**F6** (coverage statistics)~~ — **DONE, block 43g, merged 2026-08-10, and
   NOT closed.** "It needs rig calibration" was wrong twice over: the calibration
   was a computation over data already on disk, and what it measured is that
   these statistics do not answer F6 or F5. They ship as a **gate** — stable
   where `min_snr` is on a cliff, and the only ranking signal that survives on
   beads. The finding itself needs the focus response, which is F5.
8. ~~**F14** (emit adaptive runs)~~ — **DONE, block 43h, merged 2026-08-11, M5
   gate PASS over three rounds.** "Larger" was right and understated it: four
   review rounds, eight coordinator fixes, six gate rounds. "Independent of F5"
   holds. The refusal's reasoning was sound and its stubs were wrong in six
   places — most consequentially that named-position resolution was described as
   "already handled" when it did not exist, which on M5 produced an export of
   `emitted_calls: 0` and sent the agent off to hand-write a script that hung the
   console. Also fixed a pre-existing runner defect that only became visible once
   the same program could be run twice.
9. ~~**F5** (RequestAutofocus in the survey)~~ — **DONE, block 43i, merged
   2026-08-11 (`dd63070`), M5 gate PASS at round 5 of five.** Its promotion of
   2026-08-10 held: F5 needed no verdict statistic from F6, only a cheap trigger,
   because the focus response *is* the measurement. Landing after F14 was right
   for a better reason than the note gave — the sweep stack was already inlined
   into every emitted adaptive script, so the block added **no new `CannotEmit`**.
   See F5's own reconciliation blockquote for the six corrections the gate forced,
   chiefly that the re-exposure was *not* covered by the reservation and that the
   second look needs its own dataset axis, made dense, or a TIFF export drops
   every first look while the hook log reports success.
10. ~~**F9 / F12** — hook usability and timelapse observation~~ — **DONE, block
    43j, merged 2026-08-11, demo rounds 1–2 and M5 rounds 1–2 PASS.** "Whenever
    their files are next open" was the right instinct for the wrong reason: F12's
    fix cost almost nothing to build because **the tool it asked for already
    existed**, and the block's real work was a description, two missing
    arguments, and deleting the twin. The gate ran six of seven steps on the demo
    machine — a feature that *records* frames rather than deciding between them
    is indifferent to the demo camera's identical frames — and needed M5 only for
    the EMU trigger pre-flight. Both defects of consequence were in what the fold
    touched afterwards, not in the fold: a hook smuggled through
    `protocol_params` with its dose discarded, and an emitter fallback that named
    the standalone script's dataset differently from the live run's.
11. ~~**F13** — design first.~~ — **DESIGNED, block 43k, merged 2026-08-11.**
    "A genuine new capability" was right and mislocated: tile selection was
    already solved by `AcquireAt`, and what was missing is per-tile channel,
    exposure and shape. The decision is one optional `acquire_on_hit` argument
    with a parent-side second pass — no new tool, no new action — because the
    obvious design, one seed plan carrying both channels as event axes, cannot
    run on a rig with no `Channel` config group, which is the rig this session
    was recorded on. See F13's own reconciliation blockquote, and
    `design/44-two-channel-search-and-acquire.md`. Block 43n implements it.

F14 and F15 are the two that change what Microclaw *is* rather than how well it
behaves: one makes the adaptive work portable, the other makes the standard
analyses available without a hook-writing project. Everything above them is
friction removal.
