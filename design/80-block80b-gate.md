# Block 80b gate — does the emitted hooked script actually run?

**Demo machine. About ten minutes. No booked session, no dose that matters —
DemoCamera, and its trigger drives nothing.**

Block 80b makes the fixed-plan multiposition emitter render a hooked
acquisition for four built-ins: `autofocus_per_position`, `focus_feedback`,
`intensity_adaptive`, `position_filter`. Nearly all of it is settled off-rig and
was verified by independent watch-it-fail and probe during review — see the run
ledger in `design/80-hooked-autofocus-session-export.md`.

One thing is not settled and cannot be: **an exported script that compiles is
not an exported script that works.** Block 52b spent three M5 trips on exactly
that, and two of those defects survived compilation *and* every grep in the
runbook. Limbs C and F are the whole reason for this trip: they run the emitted
script in a child process against this bridge and compare it with the live run.

Two things the gate deliberately does **not** test, because they were read off
the installed dependency and the hook source on 2026-09-07 and a rig cannot say
it better: that `post_hardware_hook_fn` is AcqEngJ's `AFTER_HARDWARE_HOOK`
(`java_backend_acquisitions.py:464-470`), and that
`AutofocusHook.post_hardware_hook_fn` already handles a batched event list
(`hooks.py:272-279`). Both are recorded in design/80.

## 0. Prerequisites

**Micro-Manager open**, this machine's usual demo config loaded, and the
pycro-manager bridge running — "Run server on port 4827" checked in
Tools → Options. Same link every probe on this machine uses.

The config needs a **camera, an XY stage and a focus device**. If any is
missing, limb 0 reports NOT EXERCISED and names which. That is a real answer and
**not a pass**.

**Do not pass `--safety-config`.** This gate requires no configuration the
product does not require (60b, whose gate made the operator edit a production
safety config and then reported six limbs NOT EXERCISED for a reason that said
nothing about the code). It uses this machine's own config, and saves under its
configured `workspace_dir` when there is one.

## 1. Pin the tree

```powershell
cd D:\Code\microclaw
git fetch origin
git checkout design80/hooked-multiposition-export
git pull
git merge-base --is-ancestor 248302e HEAD
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
New-Item -ItemType Directory -Force block80b-evidence | Out-Null
uv run python design\80-block80b-demo-gate.py --out block80b-evidence --log block80b-evidence\score.json
"exit: $LASTEXITCODE"
```

It runs **two** live hooked acquisitions of two fields × one frame at 10 ms, then
runs each exported script once more — eight camera exposures plus the autofocus
sweeps, which are ~5 snaps per position at the default 4 µm / 1 µm. The XY stage
moves 20 µm between the two fields; Z sweeps ±2 µm around wherever it finds
focus. Nothing is written to Micro-Manager on any exit path.

**The gate chooses its own sweep** and prints what it chose. Round 1 stood down
here: the stage sat at Z=1.0 with `z_min = 0.0`, the default 4 µm sweep reached
-1.0, and seven limbs reported NOT EXERCISED for a reason that was the gate's,
not the code's. It now reads this machine's Z envelope and the current Z, picks
a centre and range that fit with a margin clear of each bound, and shrinks the
range on a narrow envelope. `--z-range-um` is an upper bound, not an
instruction. Only a rig whose whole envelope cannot hold a sweep is NOT
EXERCISED, and limb 0 then prints the envelope and the floor it needed.

(Round 1's runbook told the operator to "re-run with `--z-range-um 2`". That was
wrong: at Z=1.0 a 2 µm sweep lands exactly on the inclusive bound, so it would
have "passed" while leaving the hook no room. The gate deriving the number is
the fix.)

### What each limb settles

- **E — this build emits a hooked fixed plan. The control, and it fires.** It
  needs no bridge and is scored first. On a pre-80b tree this emitter *refuses*,
  so E fails and every later limb reports NOT EXERCISED rather than a cascade of
  failures that say nothing about the build. A limb that cannot fail is not a
  criterion (58a).
- **0 — bridge, camera, XY, focus, and a reachable field pair.** Refuses to
  proceed rather than measure the hook's own skip path.
- **A — a live hooked autofocus sweep runs at every position.** One completed
  sweep per position, from the hook log's `best_z_um`. It says *runs*, not
  *focuses*, deliberately: DemoCamera's frames carry no Z-dependent contrast, so
  the sweep finds no interior maximum and reports `converged: false` with a
  warning. That is the honest outcome here and **convergence cannot be exercised
  on this machine at all** — the limb requires only that a non-convergence is
  never silent. A record saying `autofocus: "skipped"` is the hook refusing its
  own Z bounds, reported separately, and **not** a pass.
- **B — the export is standalone.** Parses, imports nothing from `microclaw`,
  and carries the inlined `AutofocusHook`, the real
  `coarse_then_fine_autofocus`, its recorded `_LIMITS`, and the seed preflight.
- **C — the exported script RUNS and agrees.** The point of the trip. Child
  process, its own directory, and the standalone hook log compared with the live
  one **field by field** — position, coordinates, best Z, convergence and the
  warning text, which carries the computed argmax. Not `best_z_um` alone: on a
  flat field that is the restored entry Z in both arms, so it would agree even
  if the two sweeps had computed different curves.
- **D — `post_hardware_hook_fn` really fired under real AcqEngJ.** Read from the
  script's own printed `HOOK ACQUISITION COUNTS`, and scored on
  `hook_exposures`, which the *hook* increments — never on `saved_frames`, which
  arrives on the callback CLAUDE.md's eighth contract says a predicate must not
  be gated on. **`hook_exposures = 0` with frames saved is the defect this block
  exists to prevent**: the run reports success and the frames are unfocused.
- **F — an image hook round-trips too.** `intensity_adaptive`, live and
  standalone, exposure changes compared. No Z motion.
- **H — live and standalone datasets carry the same frame identity.** Every
  frame's `axes` read through ndstorage, checked for collisions and compared
  between the two runs. `axes` is the one identity the engine must preserve
  because the dataset is indexed by it, and a hooked multi-position run is
  exactly where a collision would hide.
- **G — the hookless grid is byte-identical.** A plain SMLM grid must not start
  emitting an adaptive runner. It reaches no microscope, so it is scored even
  when the control stands the gate down — round 1 lost it to a coupling that has
  since been removed.

## 3. Send back

```powershell
Get-Content block80b-evidence\gate.txt -Tail 40
Get-Content block80b-evidence\score.json
```

Send the whole `block80b-evidence` folder if you can — `gate.txt`, `score.json`,
the two exported scripts, both stdout captures, and every `*-hook*.json`. The
hook logs and the printed counts are what the result is scored from, not the
exit code: **a passing gate is a place to look for defects, not a reason to stop
looking**, and block 52a's third gate passed every stated limb while carrying a
defect that would have moved the stage mid-sweep.

## What the selftest already covered, and what it could not

`design/80-block80b-gate-selftest.py` runs the gate's harness against the
suite's dispatching fake engine:

```powershell
uv run python -m pytest -q design\80-block80b-gate-selftest.py
```

It found **four defects in this gate before it shipped, and three more after
round 1 came back** — limb B exported a
`call_input` limb A never recorded; limb B carried dead scaffolding; limb G
checksummed the emitter's body against a constant measured over the whole file,
so it could never have matched; and the teardown called a
`MicroscopeController.close()` that does not exist. Round 2 added the sweep
derivation, limb G's independence, and survival of a missing safety config —
`load_safety_config_or_exit` raises `SystemExit` by design, and the first cut of
the G fix let it through, which would have produced no `score.json` at all on a
machine without a config.

It discriminates in both directions: against a pre-80b *product* it fails
exactly the cases that assert 80b behaviour, and against the pre-round-2 *gate*
all eight new cases fail.

**It cannot cover limbs C and F**, because no fake can be a real AcqEngJ in a
child process. That is precisely what this trip is for.
