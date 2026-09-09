# Block 81a-2 gate — does a required autofocus failure stop the run before it exposes?

**Demo machine. About fifteen minutes. DemoCamera, so no dose that matters.**
Firefox is this machine's browser; nothing here needs one.

This gate is a **program**, not a list of steps to judge. Every limb here is a
literal command, and block 58a's gate went out as seven pasted PowerShell blocks
and came back reporting PASSED over five failed limbs — pasted interactively, a
`throw` ends the pipeline, not the session. So it runs all limbs, reports each
**independently**, owns its own log, and exits nonzero on any FAIL **or** any
NOT EXERCISED. **NOT EXERCISED is never a pass.**

## What is being tested

Block 81a-2 makes a per-position autofocus hook **fail closed**. Before it, a
hook whose sweep window fell outside the Z guard logged `autofocus: skipped`,
returned the event unmodified, and the field was exposed at whatever plane the
stage held — while the tool reported
`Acquisition complete across 9 position(s).` That is the 2026-09-09 bead
incident, and nine of your fields were spent on it.

The refusal itself is settled off-rig: 3219 tests pass, and the reach
composition and target validation were verified by mutation. **Three things
are not settled and are why this trip exists:**

- a **real focus curve** converging through the new path, with its recorded
  numbers agreeing with each other (register row `R101` says the emitted
  autofocus hook has never converged on a real curve);
- the refusal reaching the engine's **hook thread** and stopping the run before
  the frame is written — a fake cannot prove that, because design/27's ghost
  exposure *is* a fake-shaped assumption about hook returns;
- that the **exported script still runs**. This is also 81a-1's owed export
  evidence (your decision, 2026-09-09). Block 52b spent three M5 trips on the
  export alone, and two of those defects survived compilation *and* every grep
  in the runbook.

**What it deliberately does not test:** forcing a selected measured coordinate
outside its sweep window (D3(d)). Hardware will not produce that reading on
demand, and inventing it here would be a fake wearing a rig's clothes — the 16
local combinations cover it. The gate collects the positive control instead, as
an observation rather than a verdict.

## 0. Prerequisites

Micro-Manager open, this machine's usual demo config loaded, and the
pycro-manager bridge running — **"Run server on port 4827"** in
Tools → Options. Same link every probe on this machine uses.

The config needs a **camera, an XY stage and a focus device**, and the safety
config needs **finite `stage.z_min` and `stage.z_max`** — the reach check is a
comparison against them, so the *product* needs them too; the gate is not asking
for extra configuration. Anything missing is reported NOT EXERCISED naming
which, which is a real answer and **not a pass**.

The gate changes no durable state: it narrows a **copy** of the constraints for
limb B, never your config, and writes only under `--out`.

## 1. Run it

```powershell
cd <your microclaw checkout>
git fetch origin
git checkout design81/81a2-runtime-refusal
uv run python -c "print('uv warm')"
uv run python design\81-block81a2-demo-gate.py --out block81a2-evidence
```

The warm-up line is there because `uv run` prints resolution output on its
first call, and mixing that into the gate's own log has confused two earlier
rounds. Expect the run to print each limb as it completes.

**Confirm this branch contains the implementation** (an ancestor check, not an
exact tip, so amending this runbook cannot invalidate it):

```powershell
git merge-base --is-ancestor 8608dd4 HEAD; if ($LASTEXITCODE -eq 0) { "IMPLEMENTATION PRESENT" } else { "WRONG TREE - STOP" }
```

## 2. Send back

The **whole `block81a2-evidence` directory**, not the summary line. It holds
`gate.txt` (the full log), `score.json`, both result payloads, the emitted
script and its stdout/stderr, and the hook logs. This gate is scored from its
artifacts: a passing gate is a place to look for defects, not a reason to stop
looking, and 52a's third gate passed every stated limb while carrying a defect
whose only tell was a reported position disagreeing with the log's own final
value.

## The limbs

| limb | fails if |
|---|---|
| **CONTROL** | a pre-81a-2 tree, where the hook logs `skipped` and returns the event. Off-bridge and deliberately first, so a bridge or config problem cannot take the block's central claim with it. |
| **0** | camera, XY stage, focus device, or finite Z bounds missing |
| **A** | autofocus not converging on this machine, or a log whose numbers disagree |
| **B** | a frame written for the refused field, or the run reporting success |
| **C** | the focus axis left parked where the refused sweep put it |
| **D** | an emitted script that compiles and then dies, or disagrees with the live run |

The CONTROL limb has been checked on both trees before shipping: on
`b1c6d54` it fails with *"the hook returned `{'axes': {'position': 0}}` instead
of raising"*, and on this branch it raises. A limb that cannot fail is not a
criterion (58a), so that check is the gate's own evidence that it measures
something.

`design/81-block81a2-gate-selftest.py` audits the gate itself and was run on
both trees. It caught three real defects in the gate's first draft before any
operator saw it: every `execute_tool` call had the wrong argument order and
treated its JSON string as a dict, and the frame counter called an `ndstorage`
method that does not exist. All three would have died at limb A after spending
your setup time.
