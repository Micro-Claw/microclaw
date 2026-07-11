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
explanation for an ordinal sequence.

**Correction (review, 2026-07-10).** An earlier draft called that "a failure
the previous phrasing had no reason to anticipate, and does not cover." That is
wrong, and the truth is worse. The bullet that shipped with design/20 F3 names
this exact fingerprint — *"A metric that cycles with the call count rather than
with the stage position means the frame is not coming from where you think"*
(`agent.py:91`, landed in 153ccfc, in the system prompt throughout this run).
The prompt anticipated the failure, described it precisely, and the agent —
holding both the sentence and the numbers — matched the cycle to the wrong axis
anyway. The gap is not coverage. Naming a pattern does not make the model
recognise an instance of it. Weigh F5 accordingly.

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
positional summary today, and six test stubs are `lambda s: ...` /
`lambda summary: ...` (`test_tools.py:351, 359, 1801, 1816, 1850, 1865` — an
earlier draft counted five and missed 1865). Adding `kind` means updating all
six. The alternative — string-matching `"ENABLE ILLUMINATION"` in the frontend —
puts a safety-relevant branch on a prose prefix that safety.py is free to
reword. Pay the six lines.

Two requirements the sketch hides, found on review. `CONFIRM_TIMEOUT_S` and the
clock must be reachable by tests (module attributes on `webserve`, read at call
time), or the "deadline denies" test in §Tests cannot exist without a `sleep`.
And the seam itself is sound only because every callsite reads the module
global at call time — verified: `CONFIRM_FN` appears exactly at `tools.py:47,
365, 1937, 2053`, never imported by value into another module. A future
`from microclaw.tools import CONFIRM_FN` would silently disconnect the browser
gate; the `tools.CONFIRM_FN is session.confirm` assertion in §Tests is what
notices.

### F1 in the field (2026-07-10 evening run) — shown is not seen

First real run of F1, on the demo rig
(`20260710_204516_microclaw_history.json`). The operator approved saving a
`devices/MM_demo_camera` entry; `save_knowledge` blocked on the confirmation;
nothing appeared to happen; the operator pressed Stop after a few minutes. The
serve terminal shows the flow the server ran: the summary printed (record, not
prompt — F1 as designed), then "Turn stopped; confirmation declined."

The server side is not the defect. `confirm()` emitted `confirm_request` over
the same stream that had just delivered the `save_knowledge` tool cards, and
the Stop button — same page, same server — worked, so the stream and the page
were both live. The browser received the event and `applyEvent` unhid
`#confirm-banner` (`serve.html:334`, `344`). The defect is where that banner
is: the first child of `<main>` (`serve.html:147`), in normal document flow,
above the entire transcript. This section told the frontend to build "a banner
it can copy from the key and model banners" — banners that only ever appear on
a fresh, empty page, where the top of the document *is* the viewport. Mid-turn,
after two nine-row tables and a dozen tool cards, the top of the page is
several screens above the fixed composer where the operator is watching.
`showConfirm` neither scrolls it into view nor toasts, and `setBusy` keeps
"Microclaw is working…" (`serve.html:165`) on screen — the visible UI asserts
the turn is working at the exact moment it is waiting on the operator.

So the gate reached the operator's *machine* and not the operator. F1's title
names the requirement — "where the operator is" — and the implementation read
that as "in the browser" when the operator is not at a browser, they are at a
scroll position.

Default deny held throughout: the 300 s deadline would have declined it, the
operator declined it sooner via Stop, and nothing persisted. The gate failed
closed — correct, and still a failure, because the cost was the save the
operator had just said yes to.

**Fix (2026-07-11): inline at the end of the transcript, plus stick-to-bottom
autoscroll.** Frontend-only, `serve.html`; no server change, no new tests — the
Python suite has no surface here.

The operator's first instinct was right: the approval should appear in
conversation flow, right before the tool that requires it. The strict version —
anchoring the card to a specific tool card — would mean threading `tool_use_id`
through `CONFIRM_FN`, whose three callsites deliberately know nothing about the
transcript. It is also unnecessary: the turn thread is *blocked inside the tool
being confirmed*, so the tool card awaiting approval is always the last card in
the transcript, and "at the end of the conversation" is the same place, every
time. So the fix is to move `#confirm-banner` below `#transcript` (above the
spinner) — same element, same illumination styling, and it survives repaints
because `render` clears only `#transcript`'s children (`transcript.js:164`).

Three companions, each doing a distinct job:

* **Stick-to-bottom autoscroll.** Each repaint keeps the newest message in view
  *iff* the operator was already at the bottom; one who scrolled up to read is
  never yanked. This is its own fix, not just support for the banner: the
  operator was chasing the stream by scrolling manually, which is exactly how a
  top-of-page banner went unseen. (The unconditional turn-end scroll this
  replaces had the opposite politeness bug — it yanked readers.)
* **`showConfirm` scrolls unconditionally.** The follow rule respects a reader;
  an approval request is allowed to interrupt one.
* **The spinner tells the truth.** While a confirm is pending, `#pending` reads
  "Waiting for your confirmation." — "Microclaw is working…" under a blocked
  turn is how this defect got diagnosed as a hang.

The fixed-overlay alternative (position the banner like the toast, above the
composer) was rejected: its one advantage — visible regardless of scroll
position — is covered by the unconditional scroll in `showConfirm`, and inline
placement reads in conversation flow where an approval belongs.

## F2 — `_shutter_state` must not offer `open` as an answer to a question it cannot answer

`tools.py:478`. design/20 F2 already decided the shape of this problem one level
out: the absent case needed three values, not one, because *"there is no shutter
device"* is a fact and is not *"the light is off."* The same distinction applies
one level in. **"The shutter is closed right now" is a fact, and it is not "the
shutter was closed during your last exposure."**

Keep `open` — it is a true reading — and add the thing the agent actually
wanted. The rule has one hinge: a **manual** shutter's resting state is its
exposure state, because nothing toggles it between the read and the snap; under
**autoshutter** the resting read carries no information at all, because MM
toggles it per exposure. Which means four cases, not the two an earlier draft
tabled:

| `auto` | `open` | `open_during_exposure` |
|---|---|---|
| `true` | anything | `"unknown (autoshutter opens the shutter for each exposure)"` |
| `false` | `false` | `false` — the light path really is shut |
| `false` | `true` | `true` — held open |
| `"unknown"` | — | `"unknown"` |

Note the first row stays `"unknown"` rather than `true`. The tempting reading —
autoshutter is on, therefore the shutter *did* open during the last exposure —
is a present read standing in for a past event, which is the exact move S2 is
about, pointed the other way. Microclaw can attest what MM is configured to do,
not what the hardware did; the parenthetical carries the configuration, the
value refuses the claim.

```python
# tools.py, _shutter_state, after the open/auto loop
# "Closed right now" is not "closed during your last exposure" (S2). A manual
# shutter's resting state is its exposure state; under autoshutter the resting
# read says nothing — refuse to let `open` answer a question about the past.
if entry.get("auto") is True:
    entry["open_during_exposure"] = (
        "unknown (autoshutter opens the shutter for each exposure)"
    )
elif entry.get("auto") is False and isinstance(entry.get("open"), bool):
    entry["open_during_exposure"] = entry["open"]
else:
    entry["open_during_exposure"] = "unknown"
```

An agent reading the autoshutter row has nothing on which to build a
closed-shutter theory. That is the entire point of the field.

## F3 — `get_system_state` should name the camera

`tools.py:537`. The session was about a camera. The state block reports stage,
exposure, live view, shutter and lasers.

One line is not enough, though — an earlier draft said "one line" and review
says two. `core.get_camera_device()` returns the config's **label**, a
user-chosen string that is `"Camera"` in the stock demo config and anything at
all on a real rig. A label cannot verify "this is the demo camera", and it
cannot anchor F4's condition — rename the device and the condition detaches,
which is F4's defect reproduced one layer down. The hardware's identity is the
**adapter** name, `core.get_device_name(label)` — `"DCam"` for the demo camera —
which no rename touches. Report both, in the same `try`/`except` shape as the
neighbours, `"unknown"` rather than a dropped key — design/20 F2's rule, which
exists so an omitted field cannot be filled from imagination.

```python
# tools.py, get_system_state, beside shutter/lasers
try:
    label = str(ctrl.core.get_camera_device())
    state["camera"] = {
        "label": label or "unknown",
        # The label is whatever the config author typed; the adapter is the
        # hardware. F4 keys knowledge entries on the adapter for that reason.
        "adapter": str(ctrl.core.get_device_name(label)) if label else "unknown",
    }
except Exception:
    state["camera"] = "unknown"
```

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
the condition holds rather than trusting the prompt. "Device name" means the
**adapter**, not the label, for the reason F3 gives: a label is a rename away
from detaching the condition again.

```python
# tools.py, save_knowledge, before the confirmation
if category == "devices" and "observed_on" not in value:
    return {"error":
            "A devices/ entry must carry observed_on: the camera adapter it "
            "was observed with (the 'adapter' field of get_system_state's "
            "camera block, e.g. 'DCam'). An entry that suppresses an alarm "
            "must name the condition it holds under."}
```

```python
# knowledge_manager.py, format_for_prompt — one header per devices/ entry,
# rendered above the YAML, so the condition travels with the instruction
cond = entry.get("observed_on") if isinstance(entry, dict) else None
header = (
    f"applies ONLY while get_system_state reports camera.adapter == {cond!r}; "
    f"verify before relying on it"
    if cond else
    "recorded without a device condition — verify the hardware before "
    "relying on this entry"
)
```

The `else` branch is not decoration: entries already on disk have no
`observed_on`, and they must render with the second header rather than silently
as today — the user's `MM_demo_camera` entry is one of them. Rejecting legacy
entries outright would be wrong (they are the user's data); rendering them as
unconditioned-and-say-so keeps them useful while stripping their authority.

If that is too much machinery for now, the smaller true statement is this:
nothing currently stops a *model* from writing a permanent instruction that
disables a safety observation. Which alarms a knowledge entry may switch off
should be a list a human maintains.

**Action item, not code:** the entry as saved is in the user's live knowledge
base. It should be rewritten or deleted before the next run on real hardware.

## F5 — extend the rule from the cells to the sentence after the table

`agent.py`, the `Reporting — say only what a tool told you` section. It has five
bullets and every one is about a *value*. Two corrections from review, before
the text, because they change what F5 is worth:

* The ordinal clause as first drafted — "if a quantity varies with the order in
  which you called the tool rather than with the thing you changed, say so" —
  is not missing from the prompt. It is **in** the prompt (`agent.py:91`), it
  was in the prompt during this run, and the agent explained an ordinal cycle
  spatially anyway (see the S1 correction). Restating it costs nothing and is
  worth doing for precision, but it is a sentence with a measured failure rate
  of one for one. Nothing may be counted on it.
* S3 has no fix anywhere in this document unless F5 carries one. The
  falsification clause does not carry it: "name the cells that would falsify
  your explanation" governs a pattern claim, and S3's defect was an
  *independence* claim.

So the bullet gains three clauses, one per S:

> When you explain a pattern in a result, name the cells that would falsify your
> explanation, and check them — a story that fits most of the data is not a
> finding. If a quantity varies with the order in which you called the tool
> rather than with the thing you changed, say so: that is a fact about the
> instrument, not noise. And call two measurements independent only if they
> could have disagreed — different tools reading one camera through one frame
> source are one measurement, repeated; say what the readings share before
> saying what they confirm.

Whether prompt text can carry this is no longer genuinely open — it has now
been tested once, and it failed: the ordinal-cycle sentence was in-context
while the agent wrote the radial story. The honest position has hardened. F5
is cheap, and the only clauses that might pay are the two that are new
(falsification, independence). F2 and F3 are the ones that change what the
agent is *able* to conclude, and they should not wait on it; the reconsidered
`detect_static_field` below is the fix that does not depend on the model
noticing anything.

## F6 — why the design/19 log_path fix did not work

The loose end at the bottom of this document, promoted on review: it is not
loose, it is a fifth instance of the document's theme — a fix built on a stated
belief nobody checked.

What the payload showed (message 10 of the history, verified):

```
"dataset_path": "C:\\tmp\\grid_scan_2\\tile_1",
"log_path":     "\\tmp\\grid_pixel_std.json"
```

design/19's fix routed `log_path` through `resolve_in_workspace`, whose
unconfined branch is `os.path.normpath` (`safety.py:382`). The fix **ran**, and
did everything it says on the tin: the model's `/tmp/grid_pixel_std.json` came
out as `\tmp\grid_pixel_std.json`, separators respelled, doubles collapsed. It
could not have worked anyway, for two stacked reasons.

**normpath is lexical.** It rewrites the string and never consults the
filesystem, the CWD, or the current drive. On Windows it turns a POSIX-absolute
`/tmp/x` into `\tmp\x` — a *drive-relative* path, anchored to whatever the
process's current drive happens to be at each use. It cannot add `C:` because
it does not know there is a `C:`.

**dataset_path never passes through the normaliser at all.** The string in the
payload is read back from pycro-manager after the acquisition —
`_acq_dataset_path` returns `acq._dataset_disk_location` (`tools.py:629`) — a
path the Java side has already resolved against the process's current drive.
The payload itself proves the provenance: no microclaw code ever produced the
substring `tile_1`; the `_1` is AcqEngJ's dedup rename, reported back from
disk. (The fallback branch of `_acq_dataset_path` would have said
`\tmp\grid_scan_2\tile` — drive-relative and unsuffixed — so the drive is
positive evidence of the Java round trip, not an ambiguity.)

So design/19 normalised one side of an equation whose other side is produced by
a different resolver in a different process. The belief that made that look
sufficient is written down verbatim in `test_safety.py:237-240`: *"The
acquisition normalises its own dataset_path."* It does not — the OS resolves
it. A lexical fix can never converge with a filesystem-resolved string; the
only spelling that matches the OS's resolution is the OS's resolution:

```python
# safety.py, resolve_in_workspace, the unconfined branch
if root is None:
    # abspath, not normpath. normpath is lexical: it respells separators and
    # leaves '/tmp/x' as the drive-relative '\tmp\x'. dataset_path does not
    # come through here at all — it comes back from pycro-manager's Java side
    # already anchored ('C:\tmp\...', with AcqEngJ's _1 rename); matching a
    # filesystem-resolved string takes filesystem resolution, not respelling.
    return os.path.abspath(path)
```

`abspath` is `normpath` plus anchoring, so everything the design/19 fix bought
is kept, and the workspace-configured branch already `realpath`s and needs
nothing. The change also makes a *relative* `log_path` ("results/log.json")
report the file actually written — the CWD-anchored one — rather than a string
whose meaning floats with the CWD of whoever resolves it later.

Cost: the two tests that encode the old belief change with it.
`test_unconfigured_confines_nothing` (`test_safety.py:228`) becomes an
assertion against `os.path.abspath`, and
`test_an_unconfined_path_is_still_normalised` keeps its separator cases with
anchored expectations — and loses the comment this section corrects.

The original loose-end judgement — "nothing broke and nothing will, until
something compares them as strings" — also needs a correction: something
already does. The artifact allowlist is an exact string match against the
payload's own spelling (`_declared_artifacts`, `webserve.py:343`). It holds
today only because both sides of that comparison quote the same payload string;
it is not a comparison the current spelling regime is entitled to survive.

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

One word in step 1 is doing too much work, though: "differ". On real hardware
two snaps of a static scene *always* differ — shot noise — and a naive pixel
comparison would call every working camera a synthesiser. The discriminator is
the summary statistics, and this run hands us the exact signature: the demo
camera's per-frame std is **byte-for-byte identical** across snaps
(367.4638333527376, all nine tiles) while its focus_metric cycles; a real
camera is the mirror image — statistics that wobble at the noise floor, nothing
cycling. So the decision table for the no-move pair is three-way, not two:

| statistics | focus_metric | verdict |
|---|---|---|
| byte-identical | cycles | synthesiser (demo camera's rotating pattern) |
| byte-identical | constant | the same buffer re-served — stale frame |
| differ within noise | — | proceed to step 2; the no-move pair's own difference is the noise floor the moved pair must exceed |

A `detect_static_field` that snaps twice and diffs pixels is the wrong tool
wearing the right name.

The signature has now appeared in two consecutive runs and the agent has failed
it both times, once by not reading the numbers and once by reading them and
inventing a story — and the second failure happened with a prompt sentence
describing the fingerprint in-context (see the S1 correction). On that evidence
it is not the kind of thing a prompt bullet fixes.

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
* **`get_system_state`** names the camera — label *and* adapter — and reports
  `"unknown"` rather than dropping the key when the read raises, including the
  half-failure where the label reads but `get_device_name` raises.
* **`resolve_in_workspace`**, unconfined, returns `os.path.abspath` of its
  input — asserted with an `os.sep`-rooted input so the test measures
  anchoring rather than the platform. The two existing sandbox tests that
  encode "normalisation is enough" change with F6, comment included.
* **`save_knowledge`** refuses a `devices/` entry without `observed_on`, and
  `format_for_prompt` renders a legacy entry (no `observed_on`) under the
  verify-first header rather than bare — mutation-check by dropping the header
  and watching the test fail.
* **Host isolation.** F3 adds a `core.get_camera_device()` read to a function
  that twelve `test_agent.py` tests already execute. design/20's sweep found F2
  doubling `find_mm_app_dir`'s blast radius in exactly this way — twelve new
  tests reaching the host through a function they only meant to call. Run the
  probe before landing F3, not after.

## Loose ends

* The `log_path` spelling mismatch was first filed here as a loose end. Review
  promoted it to F6: the design/19 fix ran and could not have worked, and the
  "nothing will break" judgement was wrong — see F6.
* The agent again ends replies by asking the user to choose an approach rather
  than committing. design/20 filed stalling as a prompt-shape question,
  orthogonal to everything else. It is still both of those things.
