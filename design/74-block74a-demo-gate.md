# Block 74a demo gate — the lock is offered, the image sweep is not blocked

**Round 2.** Round 1 returned **three gate defects and zero product defects**,
all of them mine. Limbs A, B and E did their job and are unchanged: the two new
Core calls answered over the real bridge as Python `str`, the payload carried
the identity, and the arms discriminated. Limb C never reached its mechanism —
twice over — and limb D could never have run at all and is deleted. Both fixes
are now reproduced off-rig in the selftest, including this machine's own Z floor
and its engaged autofocus. Details are in the gate program's docstrings.

Run this on the **Windows demo machine**, from
`design74/lock-refuses-image-sweep`. No M5, no M2, no Nikon.

**Budget: about five minutes.** There is no browser session and no
`microclaw serve` — every limb only computes, so this gate is a program, not a
runbook of pasted steps.

## Why this machine and no other

- **The Nikon cannot be gated.** The operator has no pre-merge access to it; the
  Nikon user pulls `main` afterwards and can only run live on a sample. So the
  positive case — a disengaged `TIPFSStatus` being offered before an image
  sweep — is settled **off-rig**, by the suite, from that rig's own recorded
  payload and its own `rig_inventory` adapter identity.
- **M5 cannot reach this code.** It takes the EMU branch of
  `get_focus_lock_state`, which never returns `status_properties`, so the
  offer is structurally unreachable there. **M2** has no autofocus device
  configured at all. Do not book either for this block.
- **What is left needs a real bridge**, and only that: two Core calls this block
  newly puts on the pre-sweep path, and the negative control — that this
  machine's *software* autofocus adapter is not classified, so an ordinary image
  sweep still runs. design/59 block 59a shipped a `list()` over a Core
  collection that worked against every fake in the suite and raised
  `TypeError: 'mmcorej_StrVector' object is not iterable` on every rig; a
  `MagicMock` cannot catch that class of defect.

The gate program has already been run against a bridge-shaped fake on **both**
trees (`design/74-block74a-gate-selftest.py`), and limb E is `PASS` on the
branch and `FAIL` on `main`, naming all three absences. So it discriminates
before it reaches you.

## 0. Prerequisite

**Micro-Manager open**, with this machine's usual demo config loaded and the
pycro-manager bridge running — "Run server on port 4827" checked in
Tools → Options. That is the same link every probe on this machine uses.

**Do not pass `--safety-config`.** This gate deliberately requires no
configuration the product does not require (design/60 block 60b's gate made the
operator edit a production safety config to run it, and reported six limbs NOT
EXERCISED for a reason that said nothing about the code).

## 1. Pin the tree

```powershell
cd D:\Code\microclaw
git fetch origin
git checkout design74/lock-refuses-image-sweep
git pull
git merge-base --is-ancestor 0e0d2ca HEAD
if ($LASTEXITCODE -eq 0) { "PIN OK - the implementation is in this tree" }
else { "STOP - wrong tree, do not run the gate" }
```

**Warm uv once, unredirected.** A freshly checked-out branch leaves uv a rebuild
and it writes `Building microclaw @ file:///...` to stderr; under
`$ErrorActionPreference = 'Stop'` that terminates any *redirected* uv command.
Red build text here is expected and harmless — nothing is redirected.

```powershell
uv run python -c "print('uv warm')"
```

## 2. Run the gate

```powershell
New-Item -ItemType Directory -Force block74a-evidence | Out-Null
uv run python design\74-block74a-demo-gate.py --out block74a-evidence --log block74a-evidence\score.json
"exit: $LASTEXITCODE"
```

It scores each limb **independently** — one FAIL cannot hide the others behind a
cascade — writes its own log, and exits nonzero on any FAIL **or** any NOT
EXERCISED. Report whatever it prints, including a FAIL: the coordinator scores
from the artifacts, not from the exit code.

Its four limbs:

- **A — the two new Core calls answer over the real bridge.**
  `get_device_library` / `get_device_name` on the autofocus device must return
  `DemoCamera` / `DAutoFocus`, and must be Python `str`, not a Java shadow. The
  type check is the point: the discriminator compares against a frozenset of
  plain strings, so a shadow would never match and no rig would ever be
  classified — silently.
- **B — the payload carries the identity it read**, and its `probe_hint` does
  **not** name `nikon-pfs` on a device whose label contains no "PFS".
- **C — the negative control, live.** `run_autofocus` must run unrefused on
  this machine. If it comes back refused, the discriminator has misread a
  software autofocus adapter as a hardware lock, which is the failure this whole
  design was written to avoid. Two things it now does for itself, because round
  1 could not reach the mechanism without them: it derives the sweep window from
  **this rig's configured `stage.z_min`/`z_max`** rather than centring a fixed
  span on the current Z, and if the autofocus reports continuous focus
  **engaged** it disengages for the limb and **restores the entry state**
  afterwards — otherwise the pre-existing engaged-lock refusal returns four
  lines above the branch under test.
- **E — the control that fires.** Limbs A and C pass on `main` too: an
  unchanged tool that refuses nothing also lets an image sweep run. E is what
  makes the run a criterion rather than a formality — it asserts the *running
  build* carries the discriminator, the parameter and the schema entry.

## 3. The control arm, on this machine

Two minutes, and it is what proves the gate could have failed here.

```powershell
Copy-Item design\74-block74a-demo-gate.py block74a-evidence\gate-copy.py -Force
git checkout main
uv run python -c "print('uv warm')"
uv run python block74a-evidence\gate-copy.py --out block74a-evidence\control --log block74a-evidence\control-score.json
"exit: $LASTEXITCODE"
git checkout design74/lock-refuses-image-sweep
```

The copy is because the gate program does not exist on `main`; `uv run` still
resolves `microclaw` from the checked-out tree, which is the build under test.

**Expected on `main`: limb E FAIL and limb B FAIL, limbs A and C PASS.** Any
other shape means the gate is not measuring what it claims — report it rather
than working around it.

**Return to the branch before reporting** — once you check out `main` you are
running the other build.

## 4. What to send back

The whole `block74a-evidence` directory. It is small: four JSON artifacts and
both score files.

If a limb reports **NOT EXERCISED**, that is never a pass and I need to know
which and why — the most likely cause is that Micro-Manager had no autofocus
device loaded, which would mean the demo config is not the usual one.
