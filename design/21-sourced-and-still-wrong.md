# design/21 — Sourced, and still wrong

## Symptom

Lab, 2026-07-10 14:38 (history `20260710_143832_microclaw_history.json`, MM demo
camera). This is the run that verified design/20's three fixes — all held, and so
did design/19's. It is also the run in which the agent reached the same wrong
conclusion as last time, three turns running, from a payload that contained the
right one.

design/20 diagnosed S1–S4 as four faces of one omission:

> **Report what a tool returned. Do not report what it would have returned.**

F3 stated that rule. The agent obeyed it. Every number in every table this run
came from a tool result. The prose between the tables was still wrong.

The rule was necessary and is not sufficient. What it does not constrain is the
*explanation* the agent attaches to numbers it has correctly reported.

## What held

* **design/20 F1.** `run_tile_acquisition(protocol="snap")` returned per-tile
  `min/max/mean_intensity` and `focus_metric`, one hoisted `metric_valid_for`,
  and `x_um`/`y_um` on every row. The literal prompt — *"scan a 3×3 grid and at
  each point tell me the maximum and minimum"* — is now one call, not eighteen.
* **design/20 F2.** `shutter` and `lasers` present on the first
  `get_system_state` of the session.
* **design/20 F3 / S1.** No carried-forward rows. Asked to "do the same thing"
  with a third column, the agent re-ran all nine tiles rather than measuring one
  and drawing nine. It did not close with "no lasers were involved."
* **design/19.** Three passes over the same nine coordinates, `grid_center_*`
  reported and identical each time. The grid no longer walks. Two of those
  passes went through different tools — `run_tile_acquisition` with an explicit
  centre, then `run_multiposition_acquisition` over the stored names — which is
  a stronger check than the 12:27 run could make.
* **design/19's `list_hooks` fix.** The agent called `list_hooks` first, found
  the user's saved `pixel_std`, and did not offer to write a hook they already
  had.

## S1 — a period-four cycle wearing radial symmetry

`focus_metric` from the first payload, in raster order, ×1e-05:

```
2.342  2.163  1.974
2.153  2.342  2.163
1.974  2.153  2.342
```

The agent, presenting the first table:

> The focus_metric *does* vary across tiles (it cycles through the same three
> values by symmetric distance from center), which shows the tool is returning
> distinct frames

Four values, not three. And with `V = [2.342, 2.163, 1.974, 2.153]`:

```
metric(r, c) = V[(3r + c) mod 4]
```

reproduces all nine cells exactly. No stage coordinate appears in that
expression. It is a period-four cycle on the call ordinal.

Why the wrong story was tempting is worth stating precisely, because it is the
whole defect. Period four against a width-three raster puts the main diagonal on
one value (`V0` at `(0,0)`, `(1,1)`, `(2,2)`) and both anti-diagonal corners on
another (`V2` at `(0,2)` and `(2,0)`). Seven of the nine cells are consistent
with "symmetric about the centre." The two that break it are the edge-centres,
`(0,1) = 2.163e-05` and `(2,1) = 2.153e-05` — equidistant from the centre and
different. Those are the two cells the agent did not name.

It checked the cells that agreed with it.

This is design/20 S3 again: same four values, different phase (raster this run,
serpentine last). Note what changed. Last time the agent never read
`focus_metric` at all. F3's fifth bullet told it to read its own payloads before
writing prose about the instrument. It read them, and then wrote a spatial
explanation for an ordinal sequence — a failure the previous phrasing had no
reason to anticipate, and does not cover.

The fingerprint *is* the diagnosis. A frame that is a function of how many times
you have snapped, and not of where the stage is, is the demo camera's rotating
pattern — filed under design/20's *Not in scope*. On real hardware the identical
signature means the stage is not moving. Three turns of escalating alarm
followed instead, and the user supplied the answer.

## S2 — `auto: true`, read and then misread

First payload: `shutter: {"device": "White Light Shutter", "open": false, "auto":
true}`, `lasers: {"0": {"enabled": "0"}}`. The agent:

> The excitation shutter was reported **closed** and the only laser slot was
> **disabled** in the system state I read at the start. If nothing is
> illuminating the sample, you could be snapping a flat/background frame that
> looks the same everywhere.

`auto` is `core.get_auto_shutter()` (`tools.py:494`). With autoshutter on,
Micro-Manager opens the shutter for each exposure and closes it afterwards. A
resting `open: false` is the *expected* reading of a correctly working rig. It
carries no information about the light path during the snap — it is very nearly
the absence of evidence — and the agent made it load-bearing in both
escalations.

design/20 F2 set out to make "no lasers were involved" either sourced or
unavailable. It succeeded; the sign-off did not recur. What it did not anticipate
is that a sourced field can be quoted for a claim it does not support. F3's rule
is silent here, because the agent *did* say only what a tool told it. It did not
know what the field meant.

The remedy is in the payload, not the prompt.

## S3 — "a genuinely independent measurement"

> This is a genuinely independent measurement — a different tool (the `pixel_std`
> hook, actually reading pixels and computing a std of 367.46) at the same 9
> positions

A different tool. The same camera, the same synthesiser, the same frame source.
The hook and `snap_and_analyze` agree because they are looking at one thing.
Independence of *implementation* was presented as independence of *evidence*, and
used to raise confidence: *"Same flag as before, now stronger."* A third pass over
the position list was then reported as "the third independent measurement."

Three readings of one instrument are one measurement, repeated.

## S4 — a knowledge entry that outlives its rig

Saved to `devices/MM_demo_camera`, description ending:

> Do not raise the 'identical statistics across positions' alarm on this rig.

`agent.py:244` injects the entire knowledge base into every system prompt
(`format_for_prompt(load_knowledge())`), unconditionally, regardless of which
camera is loaded. The entry is *keyed* by camera and *phrased* about "this rig."

Put a real camera on this microscope and that sentence suppresses the one alarm
design/19 said nothing else would raise:

> Worth remembering that on real hardware this signature means the stage isn't
> moving, and nothing in microclaw would have said so.

The agent also never verified the camera's identity. It took the user's word,
because it had no choice: `get_system_state` returns no camera device name. F2
added shutter and lasers. The camera is the device the entire session was about.

## Diagnosis

design/20's rule governs the cells of the table. Every failure this run is in the
sentence *after* the table.

S1, S2 and S3 are the same move. The agent holds a correct number, needs a
mechanism to explain it, and adopts the first mechanism that fits most of the
data: a radial pattern for an ordinal sequence, a closed shutter for a resting
autoshutter, independent evidence for one instrument read three times. S4 is that
move made durable — a mechanism, believed, written to disk with an alarm
suppression attached.

An explanation is a claim like any other. F3 asks where a number came from.
Nothing asks what would have to be true for the story about it to be false.

---

## F1 — confirmations must reach the operator where the operator is

`tools.py:35-47`. `CONFIRM_FN` defaults to `_require_confirmation`, which calls
`input()` on the serve process's stdin. The comment above it already anticipated
this:

> Injectable so tests can stub it and **a non-CLI frontend can supply its own.**

Only tests inject. `webserve.py` never does. So `microclaw serve` renders a
browser UI whose `save_knowledge` blocks on a terminal.

That terminal exists — the shortcut targets a `.cmd` wrapper precisely so it
does (`shortcut.py:15-18`) — but its stated purpose is to "hold the console open
so a novice can read why startup failed." It is a startup-diagnostics window.
Using it as the live consent surface for a browser session is a repurposing
nobody designed, and the operator, who is looking at the browser, has no reason
to know the turn is waiting on it.

Three callsites, not one:

| site | gate |
|---|---|
| `tools.py:2053` | `save_knowledge` |
| `tools.py:1937` | `save_hook`, after advisory lint warnings |
| `tools.py:365` → `safety.py:293` | `ENABLE ILLUMINATION … This will emit light at the sample.` |

**Decision (2026-07-10): all three route to the browser, on every host, including
`--allow-remote`.**

Record what that costs, because it is not nothing. `--allow-remote` already
prints *"anyone who can reach this port can drive the microscope,"* and that is
already true of the stage, the camera and every acquisition. The terminal confirm
was the one authority a remote operator did not have: it could not emit light
without someone at the machine typing `y`. After F1, `--allow-remote` grants that
too.

The rejected alternative was to bind browser authority to loopback, the rule
`session.editable` already follows for credentials (`webserve.py:155`,
design/15). It was rejected because a split gate is worse than either half of it.
On loopback — which is how the instrument is actually run — the operator would
approve a knowledge write in the browser and a laser enable in a terminal on the
same machine, and would learn to alt-tab and type `y` on reflex. A confirmation
the operator has been trained to satisfy without reading is not a gate. Better
one surface, and make that surface good.

So the compensations are the design, not a footnote.

* **Default deny, three ways.** No SSE stream bound → deny. `session.cancel` set
  (the existing Stop button) → deny. Deadline elapsed → deny. A confirmation
  that is never answered must never become a yes.
* **The terminal still prints.** stdout keeps the summary and gains the decision
  and where it came from. Removing the *gate* from the machine is not a reason to
  remove the *record* from it; under `--allow-remote` it is the only thing the
  person standing at the microscope can see.
* **Illumination renders differently.** "This will emit light at the sample" does
  not belong on the same yes/no banner as a YAML blob.

Sketch:

```python
# webserve.py
class Session:
    self._emit: Callable | None = None    # set per turn by run_turn
    self.pending: _Pending | None = None  # read by /api/confirm

    def confirm(self, summary: str, kind: str = "action") -> bool:
        """Route a confirmation to the browser. Runs on the turn thread."""
        emit = self._emit
        if emit is None:
            return False                                   # no stream: deny
        p = _Pending(uuid.uuid4().hex, summary, kind)
        self.pending = p
        print(f"\n[microclaw] Confirmation required ({kind}):\n{summary}")
        emit({"type": "confirm_request", "id": p.id,
              "summary": summary, "kind": kind})
        try:
            deadline = time.monotonic() + CONFIRM_TIMEOUT_S
            while time.monotonic() < deadline:
                if self.cancel.is_set():
                    return False                           # Stop button: deny
                try:
                    answer = p.reply.get(timeout=0.5)      # threading queue
                except queue.Empty:
                    continue
                print(f"[microclaw] {'Approved' if answer else 'Declined'}"
                      f" from browser.")
                return answer
            print("[microclaw] Confirmation timed out; declined.")
            return False                                   # deadline: deny
        finally:
            self.pending = None
            emit({"type": "confirm_resolved", "id": p.id})
```

Installed **once**, not per turn: `tools.CONFIRM_FN = session.confirm` in
`serve()`. All three callsites read the module global at call time, so a single
assignment covers them. `run_turn` sets `session._emit = emit` and clears it in
its `finally`; only one turn runs at a time (`session.lock`), so there is no
second `emit` to confuse. Binding per-turn would work too and is worse — it
mutates a global from a worker thread for no gain.

`POST /api/confirm {id, approve}` resolves the queue. It runs on the event loop
and so cannot be blocked by the turn thread that is waiting on it. It must match
on `id`: a stale banner from a previous confirm must not answer the current one.

`GET /api/confirm` returns the pending summary or `{}`. This is not optional —
`confirm_request` is delivered exactly once, over a stream that a page reload
destroys. Without it, a refresh at the wrong moment strands the turn until the
deadline. `serve.html` fetches it on load, beside the existing history fetch.

`serve.html` gains `case "confirm_request"` / `case "confirm_resolved"` in
`applyEvent` (`serve.html:284`) and a banner it can copy from the key and model
banners.

The CLI keeps `_require_confirmation`. `__main__.py:204` already reads
`input("You: ")`, so a terminal is present by definition. The default must not
change.

On the `kind` argument: `check_illumination` (`safety.py:282`) passes one
positional summary today, and five test stubs are `lambda s: ...` /
`lambda summary: ...` (`test_tools.py:351, 359, 1801, 1816, 1850`). Adding `kind`
means updating those five. The alternative — string-matching `"ENABLE
ILLUMINATION"` in the frontend — puts a safety-relevant branch on a prose prefix
that safety.py is free to reword. Pay the five lines.

## F2 — `_shutter_state` must not offer `open` as an answer to a question it cannot answer

`tools.py:478`. design/20 F2 already decided the shape of this problem one level
out: the absent case needed three values, not one, because *"there is no shutter
device"* is a fact and is not *"the light is off."* The same distinction applies
one level in. **"The shutter is closed right now" is a fact, and it is not "the
shutter was closed during your last exposure."**

Keep `open` — it is a true reading — and add the thing the agent actually wanted:

| reading | meaning |
|---|---|
| `{"open": false, "auto": true, "open_during_exposure": "unknown (autoshutter opens it per exposure)"}` | resting read; MM manages the shutter |
| `{"open": false, "auto": false, "open_during_exposure": false}` | manual, and closed — the light path really is shut |

An agent reading the first row has nothing on which to build a closed-shutter
theory. That is the entire point of the field.

## F3 — `get_system_state` should name the camera

`tools.py:537`. The session was about a camera. The state block reports stage,
exposure, live view, shutter and lasers. `core.get_camera_device()` is one line
in the same `try`/`except` shape as its neighbours, reporting `"unknown"` rather
than dropping the key — design/20 F2's rule, which exists so an omitted field
cannot be filled from imagination.

It would have let the agent check the user's claim instead of accepting it, and
it is the precondition for F4.

## F4 — an entry that suppresses an alarm must name the condition it holds under

Two problems in one entry:

* the whole knowledge base is in every system prompt regardless of the hardware
  loaded (`agent.py:244`);
* the model wrote an unconditional imperative — *"Do not raise the … alarm on
  this rig"* — that reads as a standing instruction.

Proposed: `save_knowledge` under `devices` should require the entry to carry the
device name it was observed on, and `format_for_prompt` should render device
entries conditionally — *"when the camera is `DCam`: …"* — so the instruction
cannot detach from its trigger. With F3 landed, the agent can then check whether
the condition holds rather than trusting the prompt.

If that is too much machinery for now, the smaller true statement is this:
nothing currently stops a *model* from writing a permanent instruction that
disables a safety observation. Which alarms a knowledge entry may switch off
should be a list a human maintains.

**Action item, not code:** the entry as saved is in the user's live knowledge
base. It should be rewritten or deleted before the next run on real hardware.

## F5 — extend the rule from the cells to the sentence after the table

`agent.py`, the `Reporting — say only what a tool told you` section. It has five
bullets and every one is about a *value*. Add the missing kind:

> When you explain a pattern in a result, name the cells that would falsify your
> explanation, and check them. A story that fits most of the data is not a
> finding. If a quantity varies with the order in which you called the tool
> rather than with the thing you changed, say so — that is a fact about the
> instrument, not noise.

Both clauses are load-bearing. The first is S1 exactly: seven of nine cells fit.
The second names the specific fingerprint the agent has now missed twice.

Whether prompt text can carry this is genuinely open. design/20 observes that no
test asserts the text of the system prompt, and that prompt-content assertions
would not have caught S1–S4 anyway. The honest position: F5 is cheap and
unproven. F2 and F3 are the ones that change what the agent is *able* to
conclude, and they should not wait on it.

## Not in scope, reconsidered — `detect_static_field`

design/20 left this alone, on the grounds that a two-snap test cannot distinguish
"the camera returns a rotating pattern" from "the stage isn't moving." That was
correct, and the period-four cycle is why it matters less than it looked:

1. Snap twice **without moving the stage.** If the frames differ, the field is a
   function of the call ordinal — the camera is synthesising. The stage cannot
   confound this test, because the stage did not move.
2. Only if they are identical, move and re-snap. *Now* a difference means the
   stage works and a match means a dead stage or a genuinely uniform sample.

Two tests in that order do separate the faults. The ordering is the insight; a
single two-snap test does not, which is what design/20 correctly objected to.

The signature has now appeared in two consecutive runs and the agent has failed
it both times, once by not reading the numbers and once by reading them and
inventing a story. On that evidence it is not the kind of thing a prompt bullet
fixes.

## Tests

* **webserve.** A confirm with no stream denies. `POST /api/stop` during a
  pending confirm denies and releases the lock. A stale `id` is a 409.
  `GET /api/confirm` re-surfaces a pending banner across a reconnect. The
  deadline denies — with an injected clock, not a `sleep`; `test_webserve.py`
  has no slow tests and should keep none.
* **The seam is connected.** Assert `tools.CONFIRM_FN is session.confirm` after
  `serve()` wires it. This is the defect this document is about, in miniature:
  the injection point existed, was documented, and was never connected to
  anything. A test that only stubs `CONFIRM_FN` cannot notice that.
* **`_shutter_state`.** `auto: true` must not yield a bare
  `open_during_exposure: false`. Mutation-check it by returning `false` and
  watching the test fail.
* **`get_system_state`** names the camera, and reports `"unknown"` rather than
  dropping the key when the read raises.
* **Host isolation.** F3 adds a `core.get_camera_device()` read to a function
  that twelve `test_agent.py` tests already execute. design/20's sweep found F2
  doubling `find_mm_app_dir`'s blast radius in exactly this way — twelve new
  tests reaching the host through a function they only meant to call. Run the
  probe before landing F3, not after.

## Loose ends

* `log_path` comes back as `\tmp\grid_pixel_std.json` while `dataset_path` in the
  same payload is `C:\tmp\grid_scan_2\tile_1`. One directory, two spellings —
  the thing `safety.py:373-378` says it fixed. `os.path.normpath` normalises
  separators; it does not add a drive. Both resolve against the same current
  drive, so nothing broke and nothing will, until something compares them as
  strings or a `workspace_dir` is configured on another volume.
* The agent again ends replies by asking the user to choose an approach rather
  than committing. design/20 filed stalling as a prompt-shape question,
  orthogonal to everything else. It is still both of those things.
