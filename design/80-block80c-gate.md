# Block 80c gate — does the emitted plugin script actually run?

**Demo machine. About ten minutes. No booked session, no dose that matters —
DemoCamera, and its trigger drives nothing.** Firefox is this machine's browser;
nothing here needs one.

Block 80c makes the fixed-plan multiposition emitter render `autofocus_mm_plugin`
— a hook that delegates focus to Micro-Manager's own OughtaFocus plugin.
`mm_plugin_analyzer` stays refused, with a corrected reason.

**This machine was chosen before any instrument time was booked**, by running
`design/80-block80c-oughtafocus-probe.py` here on 2026-09-07: it answered
`settings_readable`, OughtaFocus present among seven autofocus methods, 13 of 13
settings read. That is why this is a demo-machine gate and not an M2 trip.

Nearly everything is settled off-rig and was verified by independent mutation
during review — see the run ledger in
`design/80-hooked-autofocus-session-export.md`. **One thing is not settled and
cannot be: an exported script that compiles is not an exported script that
works.** Block 52b spent three M5 trips on exactly that, and two of those
defects survived compilation *and* every grep in the runbook. **Limb C is the
whole reason for this trip**: it runs the emitted script in a child process
against this bridge and this OughtaFocus, and compares it with the live run.

Two things the gate deliberately does **not** test, because they were measured
on two machines already and a rig cannot say it better:

* That a Java `String[]` needs `java.lang.reflect.Array` — the collection drain
  raises `AttributeError: '[Ljava_lang_String;' object has no attribute
  'iterator'` and `Arrays.asList` raises `Incorrect arguments. Expected
  java.lang.Object[]`. Both reproduced verbatim on M2 and here, n=2.
* That the snapshot values are opaque text. This machine reports
  `FFTLowerCutoff(%)` as `'2,5'` where M2 reports `'2.5'` — same setting, two
  JVM locales — so nothing coerces them to numbers.

## 0. Prerequisites

**Micro-Manager open**, this machine's usual demo config loaded, and the
pycro-manager bridge running — "Run server on port 4827" checked in
Tools → Options. Same link every probe on this machine uses.

The config needs a **camera, an XY stage and a focus device**. If any is
missing, limb 0 reports NOT EXERCISED and names which. That is a real answer and
**not a pass**.

**OughtaFocus must be installed.** It is, as of 2026-09-07 — the probe found it.
If limb 0 says otherwise, re-run the probe (`--plugin OughtaFocus`) and send its
output; that is the reusable form of the question.

**`plugins.allow_hardware_motion` must be true in this machine's safety
config.** This is the **product's** own requirement, not the gate's:
`MMAutofocusPluginHook` refuses on it before it resolves any plugin at all. If
your config has no `plugins:` block, it already defaults to true and there is
nothing to do. If limb 0 reports NOT EXERCISED naming this, add:

```yaml
plugins:
  allow_hardware_motion: true
```

to that file and re-run. **The gate will not edit a production safety config**
(60b, whose gate made an operator do exactly that and then reported six limbs
NOT EXERCISED for a reason that said nothing about the code).

**Do not pass `--safety-config`.** The gate requires no configuration the
product does not require. It uses this machine's own config and saves under its
configured `workspace_dir` when there is one.

**What moves, and what it costs.** The gate runs one live two-field
acquisition and then the exported script once more: four camera exposures at
10 ms, plus OughtaFocus's own search at each of four positions. The plugin
searches over **its own `SearchRange_um`**, which read 10 µm here, at **its own
`Exposure`**, which read 100 ms — the gate reads both from the plugin and prints
them, and never overrides them. The XY stage moves 20 µm between the two fields.
**The plugin moves Z itself**, which is the point: microclaw guards the result
passively and never re-drives Z. Nothing is written to Micro-Manager on any exit
path, and the gate prints the entry XYZ at the end so you can check the stage
before the next user.

**The gate chooses its own Z centre** and prints what it chose, derived from this
machine's Z envelope and the plugin's real search range with a margin clear of
each bound. 80b round 1 stood its whole gate down because it *guessed* a sweep;
this one cannot. If the plugin's own range does not fit the envelope, limb 0
reports NOT EXERCISED and says so — it will not narrow the plugin's setting to
make room.

## 1. Pin the tree

```powershell
cd D:\Code\microclaw
git fetch origin
git checkout design80/standalone-plugin-capability
git pull
git merge-base --is-ancestor bfbdb3b HEAD
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
New-Item -ItemType Directory -Force block80c-evidence | Out-Null
uv run python design\80-block80c-demo-gate.py --out block80c-evidence --log block80c-evidence\score.json
"exit: $LASTEXITCODE"
```

That is the whole gate. It prints one line per limb as it goes, writes its own
log to `block80c-evidence\gate.txt`, and **exits nonzero on any FAIL or any
NOT EXERCISED**. Expect `exit: 0` and `8/8 PASS`.

## 3. Send back

The whole `block80c-evidence` folder. It holds `gate.txt` (the run log),
`score.json` (every limb with its status, detail and what would have failed it,
plus the two passenger observations), `C-exported.py` (the emitted script),
`C-stdout.txt` / `C-stderr.txt` (the child process), `A-live-hook.json` and the
standalone hook log beside the script, and `G-hookless.py`.

If the datasets are small, include them; if not, `NDTiff.index` from each is
enough — that is how block 60b's one FAIL was settled off-rig when the `.tif`s
were too large to copy.

## The limbs, and what each would fail on

| limb | needs the bridge | fails if |
| --- | --- | --- |
| **E** (control) | no | a pre-80c tree, where the emitter refuses both plugin hooks. **It fires**: run against a pre-80c checkout it FAILs with the old refusal text and every rig limb reports NOT EXERCISED. |
| **0** | yes | no bridge, camera, focus device or plugin; motion disabled; the plugin's own range not fitting this envelope |
| **A** | yes | the plugin not running once per position, or its settings never reaching the tool result the emitter must render from |
| **B** | no (needs A's record) | an export that imports `microclaw`, hand-writes the accessor, drops the pinned token, prompts, or omits any recorded setting |
| **C** | yes | **an exported script that compiles but does not work.** This is the trip. |
| **D** | yes (needs C) | frames saved while the plugin never ran, or an envelope that prints no disclosure |
| **H** | yes (needs C) | the two runs writing different frame identities |
| **G** | no | 80c having changed a plain multiposition export (`sha256 9a56d7a9…`, 460 lines) |

**G is deliberately outside the stand-down list.** A checksum over an emitted
file reaches no microscope, and 80b round 1 lost exactly that evidence because a
stand-down for an unrelated reason took it along. If limb E or limb 0 stands the
gate down, G still scores.

**NOT EXERCISED is never a pass.** Every one names what was missing. Do not tick
it, and do not read the summary line as a verdict — the artifacts are the
evidence.

## Two passengers, both free

Reported as OBSERVATIONs rather than limbs, because a limb that cannot fail is
not a criterion (58a). They are readings, and the number is the point:

1. **`FocusDrive` and `Channel` with a config loaded.** M2's round-2 snapshot had
   both empty with that rig's hardware off, and could not tell "empty because
   nothing was loaded" from "empty in OughtaFocus's saved profile". This machine
   has a config loaded, so it is a third reading either way.
2. **The exact settings the emitted script will disclose**, as JSON, so the
   recorded snapshot can be reconciled against what the standalone run prints.

**R101 — convergence on a real focus curve — is not in this gate and cannot be.**
DemoCamera's frames carry no Z-dependent contrast, so OughtaFocus will search
and land somewhere without ever having a peak to find. That is a passenger for
the next M2 or Nikon trip, and it does not gate this block.

## Round 1, 2026-09-07: 6/8, and **both non-PASS rows were the instrument's**

The block's central claim passed. Limb C ran the exported script against this
bridge and this OughtaFocus and the two hook logs were **identical field by
field**; the envelope printed the disclosure and both snapshots, with
`FFTLowerCutoff(%)` surviving end to end as `'2,5'` — the locale value that a
single coercion anywhere would have crashed on.

Two limbs misread their own evidence, and both criteria were satisfied by the
artifacts that came back. Neither needed a second trip; both were settled
off-rig, as block 60b's false-negative was from the `NDTiff.index` an operator
sent.

* **Limb D FAILed on `hook_exposures= 0`.** That counter is incremented through
  the reservation by a hook that snaps its own sweep (`AutofocusHook`,
  `hooks.py:299`). `MMAutofocusPluginHook` snaps nothing — OughtaFocus searches
  inside Java and microclaw never sees those frames — so it is 0 in both arms
  and always will be. The predicate was copied from 80b, whose hook has a
  different mechanism, and it **scored this block's own central workflow as a
  failure**. The standalone hook log carried 2 records, one per position,
  written from inside `post_hardware_hook_fn` — which is the honest proof the
  callback fired, and is what the limb now scores. Both counters are reported,
  neither is scored.
* **Limb H reported NOT EXERCISED over a directory that was sitting there.** It
  globbed `run*`, which was 80b's dataset name; this run's was `live_plugin_1`.
  That is 60b's defect exactly — a glob written from the previous gate instead
  of from what this one produces. The prefix now comes from the recorded call.
  Re-scored off-rig from the two datasets: **2 uniquely indexed frames each,
  `{time, z, position}` keyed `gateA`/`gateB`, identical**.

## Selftest, already run

```
.venv/bin/python -m pytest -q design/80-block80c-gate-selftest.py
29 passed, 1 skipped
```

Three of those 29 are the round-1 corrections and all three were watched failing
against the pre-fix gate. Two of them did **not** discriminate when first
written — one walked `Raise` nodes when the refusal lives in the `if`, and one
substring-matched `"focused"` against a message reading *"the frames are
unfocused"* — so they passed against the very gate they were written to reject,
and were sharpened until they failed.

The skip is the pre-80c stand-down case, which needs a pre-80c checkout; the
coordinator ran that separately against a real one on 2026-09-07 and the control
FAILed with the actual old refusal text. The selftest's `String[]` fake is
array-shaped — no `iterator()`, no `__len__`, no `__getitem__`, and `list()`
raises — reused from the probe's selftest, which was written from `bridge.py`
rather than from our caller. That mistake is what cost this notebook a rig trip
in the first place.
