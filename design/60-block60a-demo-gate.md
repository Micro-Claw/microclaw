# Block 60a demo gate — the bounded teardown wait

One program, one burst, about five minutes. Nothing here needs judgement: run
the command, send back the evidence folder.

## What this gate does and does not establish

The failure design/60 is about — pycro-manager's notification thread dying
inside a hardware-sequenced burst at a 4 GiB file boundary — is upstream, is not
reproducible on demand, and is already reproduced exactly by a blocking fake in
`tests/test_bounded_acquisition_wait.py`. **This gate does not try to induce
it,** and there is no limb pretending to.

What needs real hardware is narrower, and is all of it:

1. **D3's camera probe can answer while a burst is in flight.** pyjavaz holds one
   lock across every round trip, so it is not obvious that
   `core.is_sequence_running()` can be read while the engine is inside a burst.
   D3 reports `camera_sequence_running` at expiry and this is the only place that
   claim can be checked. If the probe blocks until the burst ends, that is a
   finding against the design, not a failed limb.
2. **The threaded teardown does not regress an ordinary run** against real
   pycro-manager and real Java teardown timing.
3. **What teardown actually costs on this machine** — the number D1's 90 s error
   grace and its runtime ceiling were chosen without.

## Before you start

- Micro-Manager open on the demo config, ZMQ server enabled (port 4827).
- No acquisition running, camera idle.
- `--save-root` must be **inside the workspace** in the machine's safety config;
  the gate writes one dataset there and leaves it for you to inspect.
- Disk: 600 frames at 512x512x16-bit is about 300 MB.

The gate reads the safety document and never writes it, and it checks the bytes
and mtime back afterwards as its own last limb.

It installs a confirmation handler that **auto-approves and records** every
confirmation into the evidence, because a program has no console to answer
`input()` on. Read `results.json`'s `confirmations` to see what it approved.

## Check you are on the right build

```powershell
cd C:\path\to\microclaw
git fetch origin
git checkout design60/bounded-wait
git pull
git merge-base --is-ancestor e5958cb HEAD
if ($LASTEXITCODE -eq 0) { "PIN OK - implementation present" } else { "PIN FAILED - wrong branch or stale checkout" }
```

Do not continue if it says `PIN FAILED`.

## Run it

```powershell
uv run python design\60-block60a-demo-gate.py --save-root D:\microclaw-data --output block60a-demo-evidence
if ($LASTEXITCODE -eq 0) { "GATE PASSED" } else { "GATE FAILED - exit $LASTEXITCODE" }
```

Substitute `D:\microclaw-data` for a directory inside your configured workspace.
Everything else runs unedited — **do not edit any other value in that command.**
A placeholder left in a literal command is a step that does not run (52c).

The burst must last a few seconds for the camera probe to sample it. If limb 4
comes back `NOT EXERCISED` saying the run was too short, re-run with more frames:

```powershell
uv run python design\60-block60a-demo-gate.py --save-root D:\microclaw-data --frames 2000 --output block60a-demo-evidence
```

## What you should see

`BLOCK 60a DEMO GATE PASSED` and eight `PASS` lines:

```
PASS  the tree under test carries block 60a
PASS  an ordinary burst completes through the threaded teardown
PASS  every planned frame reached disk
PASS  the camera probe answers while the burst is in flight
PASS  teardown is far below the bounds D1 chose
PASS  the runtime ceiling was never close to firing
PASS  no unterminated acquisition was left behind
PASS  the production safety document is untouched
```

`NOT EXERCISED` is never a pass. If any limb says it, send the evidence back
anyway — it means this machine could not run that limb, which is itself the
result.

## Send back

The whole `block60a-demo-evidence` folder: `gate.txt`, `results.json`,
`run.json`, and `setup-error.txt` if it exists. `run.json` carries every camera
probe sample with its latency, which is the measurement point 1 above turns on.

## Checked off-rig before this shipped

`design/60-block60a-gate-selftest.py` drives this whole program against a
bridge-shaped fake — Core collections as `size()`/`get(i)` vectors whose
`__iter__` raises, which is what block 59a lost a rig trip to. Run on both
trees: **8/8 PASS** on this branch, **4 limbs FAIL** on pre-60a `main`. It also
caught two defects that would have reached you: the gate would have hung on a
confirmation prompt with no console, and the camera probe's poll period was
longer than a short burst.
