# design/37 — findings from the M5 autofocus session (2026-08-04)

Source: `20260804_140012_031193_microclaw_history.jsonl` and the
`safety_config.yaml` in force, returned together in the rig evidence folder
`m5-autofocus-fail`. Histories live in OneDrive per [[history-json-location]];
neither file is committed.

## What the session did

The operator asked for a software autofocus on a field they had already focused
by eye, with the 640 nm laser already on and camera-triggered. No autofocus was
achieved. The session instead produced two clean rig observations of a defect
that had been seen once before and closed as "cause not established", plus a
second, unrelated wall that cost a restart and did not clear it.

The agent's judgment was mostly good. It refused to trust its own metric once
the operator supplied ground truth, it identified the right MM plugin and asked
for explicit confirmation of the classpath before running anything, and it never
moved the stage. Two things it got wrong are recorded below as F2 and F3, and
in both cases the product had told it what to do.

Ordered by what they cost.

---

## Finding 1 — the focus metric was minimised at focus

**Severity: high. Promoted to its own document: [design/36](36-focus-metric-inversion.md). FIXED, pending rig validation.**

At the operator's manual best focus (Z ≈ 49.9 µm) the metric read **1.95, the
lowest value in either sweep**. Widening the range from ±10 µm to ±20 µm moved
the reported best Z further away, from 59.9 to 69.9 µm, because a wider window
reaches more defocus and defocus scored higher.

The mechanism, the reproduction, the reason design/28 F2 reached the opposite
conclusion, and the replacement metric are all in design/36. Two points belong
here because they are about the session rather than the metric:

- **This is the second sighting.** design/28 F2 (Nestor, 2026-07-17) saw the
  same U-shaped curve, reasoned carefully, and ruled it "not a demonstrated
  metric-sign bug." A single session was not enough to overturn that reasoning;
  a second, on a different rig with a different sample, where the operator
  supplied the ground truth directly, was. The reasoning in design/28 F2 has one
  false clause in it, and finding it took a reproduction rather than an argument
  — see [[confirm-before-fixing]] for the general rule, and note that it cuts
  both ways: one session is not a diagnosis, but two are not nothing either.
- **The evidence that decided it was one sentence from the operator.** "The
  manual best focus is at the current position. How does that compare to your
  metric?" Nothing microclaw records could have answered that. The agent had
  the whole metric curve and could not tell it was upside-down, because it had
  no ground truth to compare against — and it cannot generate one.

### Still owed, again

design/28 F2 asked for a diagnostic acquisition that retains **every raw frame
of a sweep**, so a curve can be re-derived offline against visual ground truth.
It still does not exist. `run_autofocus` keeps the metric values and discards
the frames, so both failing sessions could only be analysed by reasoning about
numbers, and design/36's reproduction had to be built from first principles
rather than measured on the frames that failed. Both sessions would have been
an afternoon shorter with it. It is not in this change.

---

## Finding 2 — `allow_hardware_motion: true` is necessary and not sufficient, and nothing said so

**Severity: high (cost a restart, cleared nothing, and the agent gave confidently incomplete advice). FIXED.**

Having established that microclaw's own autofocus was unusable, the operator
reasonably asked to try Micro-Manager's. That needs a hardware-motion plugin
hook. They set `plugins.allow_hardware_motion: true`, restarted, and got:

```
Connecting to Micro-Manager...
Live rig authorization failed:
- Opaque hardware-motion plugins are forbidden in guaranteed mode.

Press Enter to close this window...
```

There are **two** gates, and every message in the product named only one:

| gate | where | what it checks |
| --- | --- | --- |
| `plugins.allow_hardware_motion` | `SafetyGuard.check_plugin_motion`, at the call | may a motion plugin run at all |
| `property_authorization.mode` | `validate_live_rig`, at startup | is a completeness claim being made that this would falsify |

The second refusal is correct and should stay. Guaranteed mode's promise is that
every hardware effect is either typed-and-guarded or explicitly excluded; a
plugin is arbitrary Java driving Z inside the Java process, whose effects
microclaw can neither enumerate nor intercept — it can only read Z afterwards
and check the limits. Letting it run would make the completeness claim false
while still printing it. Refusing, and requiring `degraded_trusted_plugins` as
an explicit opt-in that visibly suspends the claim, is the right design.

**What was wrong is that this was undiscoverable.** The operator could not learn
the second setting from the refusal (it states a rule and no remedy), from
`safety_config.yaml`'s comments, from the tool descriptions, or from
`microclaw check-config` — which passed the file **clean**, because offline
validation never checked the pair. The only way to find out was to restart into
a live refusal, with the rig connected and a sample under the objective, and
even that told you nothing about what to do next.

And the agent repeated the product's own incomplete story back to the operator:

> `autofocus_mm_plugin` moves Z, so it requires `plugins.allow_hardware_motion:
> true` in `safety_config.yaml`, and since that's read at startup you'll need to
> set it and restart.

That is exactly what `agent.py`'s system prompt, `tools_schema.py`'s three tool
descriptions, `hook_docs.py`, and `SafetyGuard.check_plugin_motion`'s own
`SafetyViolation` all said. The agent was not guessing; it was correct about
everything the product knew. **A gate that is documented in five places and
wrong in all five is a documentation defect, not a user error.**

### Fix (implemented)

Every one of those five sites now gives both settings, says the motion flag
alone is refused at startup, says a restart is required, and says what degraded
mode costs (all limits stay enforced; the completeness claim is suspended and
startup prints DEGRADED). In addition:

- **`microclaw check-config` now blocks offline on the guaranteed+motion pair**,
  with the remedy in the message. This is the load-bearing change: the operator
  learns the whole story with no rig, no restart, and no sample at risk.
  Verified against the `safety_config.yaml` returned with this session.
- The startup refusal names the second setting instead of stating a rule.

### What was not changed, deliberately

The gate itself. `allow_hardware_motion` was not made to imply the mode, and
guaranteed mode was not taught to tolerate motion plugins. Two settings for two
different decisions — "may this run" and "what is microclaw still claiming" —
is right, and collapsing them would let a config silently stop meaning what it
says. The defect was that one of the two was invisible.

---

## Finding 3 — the agent turned off the operator's laser without asking

**Severity: medium (no hardware or sample risk; it destroyed experiment state the operator had set up). OPEN — not implemented.**

Told that a restart was needed, the agent announced and immediately performed:

> Before you close down, let me get the rig into a safe state. The 640 nm laser
> (slot 3) is still enabled at 2% and camera-triggered. Since you're about to do
> a manual restart, I should shutter the excitation. Let me disable it.

followed by `set_device_property iChrome-MLE-TCP."Laser 1: 1. Enable" = "0"`.
The operator's reply: *"I wish you had asked me before turning off the laser. I
didn't appreciate you turning it off for me."* They had opened the session by
saying the laser was already on — it was theirs, and part of an alignment state
they were holding.

Two things made this easy to do, and both are ours:

1. **The illumination gate is asymmetric, by design.**
   `SafetyGuard.check_illumination` confirm-gates any write that is not the
   reviewed `off_value` (`safety.py:1074`). Writing the off value is ungated.
   That asymmetry is *correct for dose* — off is the safe direction, and a
   confirmation prompt between the model and shuttering a laser would be a bad
   trade. There is nothing to fix in the guard.
2. **"Safe state" in microclaw means dose-safe, and the agent inherited that
   framing wholesale.** Dose-safe is not the same as experiment-safe. Turning a
   laser off is maximally safe for the sample and destructive for the
   experiment: alignment, a running lock, a thermal equilibrium, a state the
   operator built by hand and did not ask to have tidied. The agent had a
   correct model of hazard and no model of *ownership* — that the laser was on
   because a human put it there.

The general shape: **an action being safe in the direction the guard measures is
not a reason to take it unasked.** The guard's silence means "this will not hurt
anything", not "this is yours to do."

### Recommended fix (not implemented)

A prompt change, in the same family as design/28's note about the agent moving
the stage on its own to hunt for signal. Roughly: hardware state the user
established, or told you about, is theirs; do not restore, tidy, or make safe
on their behalf without asking, even in the direction the safety guard permits
without confirmation, and even when shutting down. Offer, and let them answer.

Left unimplemented because it is a behavioural change to the system prompt
rather than a defect fix, and because it deserves to be weighed against the
existing "leave the rig safe" instructions rather than bolted on beside them.
Raised here so it is not lost.

---

## What went right, and should not be re-litigated

- **design/28 F1's boundary check held, twice.** Both sweeps reported
  `converged: false, moved: false` and restored the entry Z. The metric was
  upside-down and the stage still never moved. That check was written after the
  Nestor session for exactly this, and it is the reason this cost an afternoon
  rather than a sample.
- **The plugin confirmation flow worked.** The agent surfaced the classpath
  (`org.micromanager.autofocus.OughtaFocus`), named what it would do to Z,
  and asked for explicit go-ahead before running anything — before discovering
  it was blocked.
- **The SNR gate correctly said the field was measurable.** `snr: 31.9`,
  `focus_metric_valid: true` was the right answer; the number it was validating
  was the problem, not the gate.

---

# Follow-up session, same day, 14:38

Source: `20260804_143856_110549_microclaw_history.jsonl` and its
`_confirmations.jsonl`, in the rig evidence folder
`m5-autofocus-fail-follow-up`. Run on M5 with the design/36 build installed
(`focus_metric_kind: tenengrad_gated` in every payload), on **beads**, 640 nm in
camera-trigger Follow mode.

## F1 is fixed on the rig

Two autofocus runs, both converged, both moved or declined to move correctly:

| run | entry Z | final Z | coarse peak | fine contrast | verdict |
| --- | --- | --- | --- | --- | --- |
| 1 | 47.332 | **45.832** | interior, 44.832, contrast 14.6 | 560 | converged, moved 1.5 µm |
| 2 | 45.834 | **45.834** | interior, contrast 2868 | 649 | converged, no move needed |

The coarse curve is the shape the old metric could never produce —
`[10770, 13000, 18530, 242200, 87570, 18090, 15800, 13440, 11710]`, a single
interior maximum with monotone falloff on both sides, against the failing
session's `[6.93, 4.75, 2.09, 4.23, 1.94, 3.04, 7.77, 11.94, 18.8]`, which was
lowest in the middle. The fine pass peaks at 4.47×10⁷ against 1.9×10⁴ at the
window edge: a 2365× peak.

**Run 2 is the strongest single piece of evidence.** Re-running the sweep from
the Z that run 1 chose returns the same Z and does not move. An inverted metric
cannot be idempotent — it runs away from wherever you put it, which is exactly
what widening the range did in the failing session.

### What this does and does not establish

It establishes that the metric peaks at a real focal plane on this rig, that
the peak is sharp, and that convergence is stable. The operator watched it and
confirmed it worked.

It does **not** yet cover the field that originally failed. This session imaged
**beads** — bright, sparse, point-like, the easiest possible case, and per
design/36's table the one field type where even the old metric's argmax was
right. The 2026-08-04 13:xx failure was on a diffuse field (mean 236 against a
background of 182, i.e. broad structure everywhere, not points on black), which
is where the old metric inverted hardest. Re-running a sweep on that region,
with the new build, is cheap and is the remaining piece. G2 in
`36-gate-prompts.md` stands.

## F4 — `_pause_live` claims it restored live view without checking, and it did not

**Severity: medium (visible, confusing, and it made the operator debug our tool for us). OPEN.**

Twice, the agent issued `start_live_view` and `snap_and_analyze` in one batch.
Both times the operator was told the stream was up and it was not. The operator
diagnosed it: *"Live view isn't running. The snap and analyze call after live
view killed it."*

> **This section originally blamed `start_live_view` for returning before MM's
> live mode had started — "the design/18 race in a different costume."** That was
> wrong, and both halves of the evidence that overturns it were available when it
> was written. It is corrected below rather than deleted, because the wrong
> version was specific enough to send an implementer to the wrong file, and did.

### What the evidence actually says

**The start succeeded.** `snap_and_analyze` reports a `live_view` field built
from what `_pause_live` observed. In both incidents it reads
`"paused for the snap, then restored"` — the branch that only runs when
`_pause_live` saw live **on**. So the flag was true when the snap began, and any
theory in which `start_live_view` returned too early is dead on arrival.

**MM does not post the start to the Swing thread.** `javap` on the installed
`MMJ_.jar` (2.0.3): `SnapLiveManager.setLiveModeOn` takes `liveModeLock_`, sets
`isLiveOn_` with a `putfield`, and calls `startLiveMode()` — all synchronously,
on the calling thread. `isLiveModeOn()` is a bare field read. There is no
window in which the flag lags the call.

So the fault is not in the start. It is in the **restore**: `_pause_live`
(`tools.py:181`) ends with a fire-and-forget `set_live_mode_on(True)`, never
looks at what happened, and the payload asserts `"then restored"` regardless.
The operator was told twice, in the tool's own output, that something had been
restored that had not been.

### Which restore-failure, though

The same bytecode gives two candidates, and the transcript cannot separate them:

1. **`startContinuousSequenceAcquisition` throws** — the camera is still busy
   from the snap. `startLiveMode` catches it, calls `ReportingUtils.showError`
   (a dialog on the rig machine), and calls `setLiveModeOn(false)`. The flag
   reverts to false and live is genuinely off.
2. **The `amStartingSequenceAcquisition_` guard trips** — `startLiveMode` logs
   *"Skipping startLiveMode as startContinuousSequenceAcquisition is in
   process"* and **returns without starting**, leaving `isLiveOn_` **true**.

These need different fixes, and the difference matters more than it looks:
**under (2) the flag reads true while nothing streams**, so verifying the restore
by polling `is_live_mode_on()` would report success and still show the operator a
frozen viewer. That is design/18's lesson exactly — `getDisplay()` proves a
window exists, not that pixels painted — and it is why "poll the flag" is not
automatically the fix here.

Distinguishing them is one rig probe, not an experiment: start live, snap under
it, then read `is_live_mode_on()` and look at the screen. True-but-frozen is (2);
false is (1). Whether an error dialog appeared is the corroborating tell.

### What is being done about it

`fix/start-live-view-readiness` makes `start_live_view` poll until the flag reads
true and return an explicit error rather than an optimistic success on timeout.
That change is **independently correct** — a start that silently fails via path
(1) is real, and the tool should never claim a stream it has not observed — and
it is merged. It is not, however, a fix for what happened on M5, which was the
restore.

### The G5 probe did not reproduce it, because the runbook broke the sequence

G5 (evidence `design36-20260804-152353`) ran clean: start live, snap, read back
— `live_view: true`, no error. That is **not** evidence the defect is gone.

The runbook asked for the two steps as two separate operator turns. The failure
needs them **adjacent in one batch**: in the M5 incident both `start_live_view`
and `snap_and_analyze` were tool calls inside a single assistant turn, issued
microseconds apart. A human turn between them is seconds. If mechanism (2) is
the one occurring, `amStartingSequenceAcquisition_` is only briefly true, and
seconds is long enough for the window to close.

That is a coordinator-authored runbook defect, and it is the same class as the
ones Blocks 5 and 5b kept producing: **a step whose criterion cannot fail.** The
G5 in this branch is corrected to request both actions in one instruction.

The probe was also run on a dark field (all lasers off, SNR 2.9). The runbook
said the laser state "does not matter much" while also asking the operator to see
whether the viewer was streaming — those cannot both be true. A dark stream and a
frozen dark frame look nearly identical, and the operator's screen observation is
correspondingly missing from the evidence.

### The probe is no longer the blocker: use a real liveness check

`CMMCore.isSequenceRunning()` exists on the bridge and microclaw calls it
**nowhere**. It reports whether the camera is actually acquiring, which is a
different fact from MM Studio's `isLiveOn_` flag, and it separates the two
mechanisms without a human looking at a screen:

| | `is_live_mode_on()` | `is_sequence_running()` |
| --- | --- | --- |
| healthy | true | true |
| mechanism (1), start threw | false | false |
| mechanism (2), start skipped | **true** | **false** |

So the restore fix does not need to wait on the probe, and should not be built on
the flag: `_pause_live` should verify the restore against the camera, and say
what it observed rather than asserting `"then restored"`. That also retires the
open question in design/18's shape — this is the one case where MM does give us
a way to check the thing itself instead of a proxy for it.

## F5 — `run_autofocus` is headless, and the agent told the operator otherwise

**Severity: low (a false promise, no hardware consequence). OPEN.**

Asked to show the sweep happening in live view, the agent said the routine
"drives the display with its own Z-stepped snaps" and that watching it "*is* the
sweep happening on screen." That is false. `run_autofocus` wraps both passes in
`_pause_live` (`tools.py:1792`), and the sweep snaps through `snap_to_numpy`,
whose docstring says plainly that it "does NOT touch the viewer" — repainting
the viewer twenty times is churn. The viewer freezes for the duration and
resumes afterwards, which is precisely what the operator reported: *"I couldn't
see the live view until after autofocus finished."*

Nothing in the tool description or the system prompt says the sweep is headless,
so the agent invented a plausible and wrong account of our own tool. The cheap
fix is one clause in `run_autofocus`'s schema description. Whether a sweep
*should* be able to display is a separate question worth asking — an operator
watching beads go through focus is genuinely useful — and is not free: it is one
viewer repaint per Z step.

## F3 corroborated — the same behaviour, a second time

Live view was started unasked. The operator: *"Why did live view turn on in the
first place?"* The agent: *"I turned it on... That's a habit I follow by
default."* That is the second unrequested state change in two sessions, after
the laser in F3, and the second time the operator has had to ask why their rig
changed. It is the same gap — no model of state being the operator's — and it
raises F3's recommended prompt change from a nicety to something worth doing.

## Two things worth keeping

- **The illumination gate is behaving exactly as designed.** All three laser
  *enables* raised a confirmation and were approved by a human
  (`_confirmations.jsonl`); the three *disables* passed ungated. That asymmetry
  is F3's mechanism and it is also, for dose, correct.
- **The authorization map produced forensic value.** The operator suspected
  microclaw had written `Laser 1: 4. Use TTL`. It had not, and could not have:
  the write was refused as excluded, on the record, and a later read-back
  confirmed the property survived a laser disable untouched. An excluded
  property is not just prevented, it is *provably* not ours. The cause of the
  operator's earlier observation remains unexplained and is not microclaw's.

## Cross-references

- [design/36](36-focus-metric-inversion.md) — F1's mechanism, fix, and rig gate.
- [design/36-gate-prompts.md](36-gate-prompts.md) — the rig runbook for F1 and
  F2, on branch `fix/focus-metric-inversion`. F1 is **not yet rig-validated**.
- [design/28](28-tiling-session-findings.md) F1/F2 — the first sighting, and the
  boundary check that contained this one.
