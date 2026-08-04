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

## Cross-references

- [design/36](36-focus-metric-inversion.md) — F1's mechanism, fix, and rig gate.
- [design/36-gate-prompts.md](36-gate-prompts.md) — the rig runbook for F1 and
  F2, on branch `fix/focus-metric-inversion`. F1 is **not yet rig-validated**.
- [design/28](28-tiling-session-findings.md) F1/F2 — the first sighting, and the
  boundary check that contained this one.
