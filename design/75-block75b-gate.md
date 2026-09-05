# Block 75b gate — the supervised-runtime bound

**Demo machine. About fifteen minutes. No rig time, no dose, no booked
session.**

Block 75a's M2 arm already measured the healthy one-frame supervised window
(n=20, maximum **446 ms**) and this block's two constants were chosen from it at
11.2x headroom — `SHORT_FIXED_QUIET_FLOOR_S = 5.0` and
`SHORT_FIXED_RUNTIME_SLACK_S = 5.0`. Nothing left needs a camera trigger. What
does need a machine is an *injected* teardown hang, and design/60 gated the same
class of bound here for the same reason.

What the block changed, in one sentence: a standalone one-frame timelapse that
hangs in pycro-manager teardown now returns a typed failure in about ten
seconds instead of fifteen minutes, and says which bound expired, how many
frames were accounted, and whether the record reached disk.

Two parts. **Part 1 is a program** — every limb only computes or drives an
acquisition the program owns, so it scores itself, one FAIL cannot hide the
rest, it owns its log, and it exits nonzero on any FAIL *or* NOT EXERCISED
(58a). **Part 2 is the one thing a program cannot judge**: what an operator
actually sees in the browser.

## 0. Prerequisites

**Micro-Manager open**, with this machine's usual demo config loaded and the
pycro-manager bridge running — "Run server on port 4827" checked in
Tools → Options. Same link every probe on this machine uses.

**Do not pass `--safety-config`.** This gate deliberately requires no
configuration the product does not require (design/60 block 60b's gate made the
operator edit a production safety config to run it, and reported six limbs NOT
EXERCISED for a reason that said nothing about the code).

**The browser on this machine is Firefox** — the desktop shortcut's default
(`design/69a-gate.md:70`). Part 2 assumes it.

## 1. Pin the tree

```powershell
cd D:\Code\microclaw
git fetch origin
git checkout design75/supervised-runtime-bound
git pull
git merge-base --is-ancestor 76cc353 HEAD
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

## 2. Run the gate program

It takes a minute or two. Two of its limbs deliberately hang an acquisition and
hold it for up to 25 seconds before letting go, so a long quiet pause partway
through is the gate working, not the gate stuck.

```powershell
New-Item -ItemType Directory -Force block75b-evidence | Out-Null
uv run python design\75-block75b-demo-gate.py --out block75b-evidence --log block75b-evidence\score.json
"exit: $LASTEXITCODE"
```

Expected: `exit: 0` and eight PASS. **Send `block75b-evidence\gate.txt` and
`block75b-evidence\score.json` back whatever the result** — a FAIL is scored
from the artifacts, not from the verdict, and so is a PASS.

The eight limbs, and what each one would be telling you if it failed:

| limb | it fails if |
|---|---|
| E — the build carries 75b's bound | you are on the wrong tree. Everything else stands down. |
| 0 — bridge, camera, save root | no ZMQ bridge, no camera, or a pycro-manager whose `__exit__` is no longer `mark_finished` + `await_completion`, which would mean the injection below reproduces nothing |
| A — a real healthy one-frame run selects the short policy | **the constants are wrong for this machine** — a healthy call slower than its own bound |
| B — blocked teardown, frame accounted | a hang whose typed result never arrives, or one that reports the acquisition complete |
| **C — blocked teardown, no frame accounted** | **the mandatory limb.** This is the 2026-09-04 shape and the case design/75's first draft would have hung on |
| D — the session refuses a second acquisition, then lifts | a refusal that never fires, or one that needs a restart to clear |
| F — the timed-out dataset opens | data the operator cannot read after the bound fired |
| G — the timeout is in D4's file | a bound that fires and leaves no record — the 2026-09-04 failure one layer down |

## 3. The control arm

Confirm the gate discriminates on *this machine* rather than passing whatever it
is pointed at. `main` does not have the gate file, so copy it out first.

```powershell
Copy-Item design\75-block75b-demo-gate.py block75b-evidence\gate-copy.py
git checkout main
uv run python -c "print('uv warm')"
uv run python block75b-evidence\gate-copy.py --out block75b-evidence\control --log block75b-evidence\control-score.json
"exit: $LASTEXITCODE"
git checkout design75/supervised-runtime-bound
uv run python -c "print('uv warm')"
```

Expected: `exit: 1`, limb E **FAIL** naming what is missing, and every other limb
**NOT EXERCISED**. Send `block75b-evidence\control-score.json`.

Note the last two lines: **the control arm leaves you on `main`**, and part 2
must run on the branch.

## 4. Part 2 — what the operator sees (the human step)

This is the only step a program cannot do for you, and it is the block's whole
purpose in one screen. Five minutes.

**Close any running MicroClaw first.** Then, in a *new* PowerShell window:

```powershell
cd D:\Code\microclaw
uv run python design\75-block75b-blocking-serve.py
```

It prints a warning and opens Firefox. **Every acquisition in this session will
hang** — that is the injection, and it is why this is a throwaway window.

Ask MicroClaw, in your own words, for **one single frame** — for example:

> Take one 50 ms frame and save it under a new name.

Then watch the status line under the composer, and write down what you see:

1. Does it say something like **`frames 1 / 1 · finalizing dataset`**? For
   roughly ten seconds? (On a *healthy* run this phase lasts about 3 ms and you
   would never catch it — the injected hang is what makes it visible.)
2. Does a **structured failure** then arrive, rather than the spinner
   continuing? Roughly how many seconds after you sent the request? A stopwatch
   guess is fine; the gate measured it precisely in part 1.
3. Does the failure say **which bound expired** and **how many frames were
   accounted** — and does it avoid claiming the acquisition completed?
4. Ask for **a second single frame straight away.** It should be *refused*,
   naming the previous acquisition's teardown, without you restarting anything.
5. Wait about a minute and ask again. It should now **run normally**.

**Then close that window.** Nothing persists, but nothing else should use it.

Send back: your answers to 1–5 in a sentence each, and a screenshot of the
status line at step 1 if it is easy. If the status line never showed a phase,
say so — that is a finding about D3, not about you.

## What to send back

- `block75b-evidence\gate.txt`
- `block75b-evidence\score.json`
- `block75b-evidence\control-score.json`
- your five answers from part 2
- the `exit:` line printed after each run

If any limb reports NOT EXERCISED, that is never a pass — send it exactly as
printed and say what this machine could not do.
