# Block 57a demo gate — the stage checks fail closed

**Machine: the demo machine, with stock `MMConfig_demo.cfg`. Nothing else is
required.** No sample, no laser, no focus lock, no booked rig time. If M2 or M5
happens to be free, Step 3 there costs nothing and adds a second export — it is
not a precondition of merging, and this gate closes without it.

Implementation ancestor: cad3869

PowerShell throughout. `uv` is the single launcher for every Python and project
command; do not substitute bare `python` for one line. Git commands only
establish the checkout. Every command block below runs **unedited** — nothing in
it is a placeholder you are meant to substitute, and a step that prints nothing
where a match is required has **failed**, not passed.

## What this gate can and cannot settle

## Round 1 result — 2026-08-24, demo machine (`block57a-2026-08-24`)

**Steps 0–6 PASS, scored from the artifacts.** Step 6 raised
`SafetyViolation: X bounds are incomplete in the recorded stage envelope` at
`guard.check_xy(0, 0)` before the acquisition opened, which is this block's new
behaviour on hardware. The standalone hook log matched the live one record for
record across all five positions.

**Two steps were repaired afterwards and are the only ones worth re-running:**
Step 4's finder anchored on `workspace_dir`, which is absent from this machine's
schema-3 minimal document, so it died and took Step 5's evidence file with it
(see Step 5's `Test-Path` note); and Step 7 tested nothing twice over (see its
own preamble). Both are fixed above. **Re-run Step 4, Step 5 and Step 7 only.**

**What it settles.** That the ordinary path still works after the guard was made
to fail closed: a real session on a real MMCore opens, runs a real adaptive
acquisition, exports a standalone script whose recorded stage envelope is finite,
and that script runs to completion with Microclaw shut down. That is the one
thing this block could plausibly have broken, and block 43h's demo rounds already
proved the demo core exercises the whole of it — real bridge, real acquisition
engine, real generator and terminator.

Step 6 additionally settles the new refusal itself, at the portable guard, by
handing the emitted script an envelope with a missing edge.

Step 7 settles the regression the design names as the one this item can break: a
config with **no `camera:` section** — the minimal document every in-app setup
writes — still exports, and the emitted script's exposure checks still pass.

**What it cannot settle, and does not need to.** The live-rig case the design
opens with. `validate_live_rig` refuses a missing or open range policy on a
reachable axis before either entry point exposes a tool, so **no live session can
reach the state Step 6 constructs by hand.** That is why Step 6 edits an exported
file instead of a config: the refusal is real and worth proving, and no rig can
be persuaded into the state that triggers it.

---

## Step 0 — pin, install, and run the full suite

```powershell
$Repo     = "$HOME\Code\microclaw"
$Evidence = "$HOME\Documents\microclaw-gates\block57a-$(Get-Date -Format yyyy-MM-dd)"
New-Item -ItemType Directory -Force $Evidence | Out-Null
Set-Location $Repo
git fetch origin
git checkout design57/fail-closed-guard
git pull
git merge-base --is-ancestor cad3869 HEAD
if ($LASTEXITCODE -eq 0) { Write-Output "IMPLEMENTATION PRESENT" } else { Write-Output "WRONG TREE - STOP" }
uv pip install -e .
uv run pytest -q > "$Evidence\suite.txt" 2>&1
Select-String -Path "$Evidence\suite.txt" -Pattern "passed|failed" | Select-Object -Last 1
(Select-String -Path "$Evidence\suite.txt" -Pattern "^FAILED" | Measure-Object).Count
```

**Required:** `IMPLEMENTATION PRESENT`, and the last line prints **`0`**. The
branch point measured 2090 passed / 99 skipped on macOS; this branch adds tests,
so expect more than 2090 and a Windows skip split near 124. **The criterion is
zero failures, not the count.**

## Step 1 — what this machine's config actually declares

```powershell
@'
import sys
from microclaw.paths import default_safety_config
from microclaw.safety import ParsedSafetyConfig
p = default_safety_config()
print("config:", p)
c = ParsedSafetyConfig.from_yaml(str(p)).constraints
s = c.stage
print("x_min", s.x_min, "x_max", s.x_max)
print("y_min", s.y_min, "y_max", s.y_max)
print("z_min", s.z_min, "z_max", s.z_max)
print("max_exposure_ms", c.camera.max_exposure_ms)
print("workspace_dir", c.workspace_dir)
edges = [s.x_min, s.x_max, s.y_min, s.y_max, s.z_min, s.z_max]
print("SIX FINITE EDGES" if all(e is not None for e in edges) else "INCOMPLETE ENVELOPE - STOP")
'@ | Set-Content "$Evidence\show-config.py" -Encoding UTF8
uv run python "$Evidence\show-config.py" > "$Evidence\config.txt" 2>&1
Get-Content "$Evidence\config.txt"
```

**Required:** the last line reads **`SIX FINITE EDGES`**. If it reads
`INCOMPLETE ENVELOPE - STOP`, this machine's config is missing a stage edge —
add both keys for the named axis, record that you did, and rerun this step. Do
not continue with an incomplete envelope; Steps 3–5 would be measuring the wrong
thing.

**Record `workspace_dir` and `max_exposure_ms` from this output.** Later steps
read them back from the same source, so nothing here needs transcribing by hand.

## Step 2 — the two offline suites, on this machine

```powershell
uv run pytest -q tests/test_safety.py tests/test_session_script_export.py > "$Evidence\offline.txt" 2>&1
Select-String -Path "$Evidence\offline.txt" -Pattern "passed|failed" | Select-Object -Last 1
(Select-String -Path "$Evidence\offline.txt" -Pattern "^FAILED" | Measure-Object).Count
```

**Required:** the count prints **`0`**. These are the two files the block
changes; running them alone on the gate machine costs seconds and separates a
platform problem from a block problem before any hardware is involved.

## Step 3 — one real adaptive session, and an export

Start Micro-Manager with the stock demo config and the ZMQ server enabled, then
launch Microclaw **from the repository directory** — the transcript is written to
the server's working directory, and Step 4 looks for it there:

```powershell
Set-Location $Repo
uv run microclaw serve
```

**Known defect that will bite you.** An error that aborts a turn is shown in the
browser and is **not written to the transcript**. If any prompt below errors,
copy the message out of the browser immediately; it will not be in the log
afterwards.

### 3a — reach: ask at the operator level, naming no tool

Give the agent a small, safe plan over a handful of positions, then, verbatim:

> Go through these positions one at a time and have it stop itself once it has
> seen enough — I don't want to sit and watch it. And give me a script that
> makes that call on its own when I rerun it next week.

This wording is block 43h's and is known to work: it reached an adaptive survey
with a saved hook, and an export, unprompted. **Pass when the agent reaches an
adaptive acquisition and exports a script.**

### 3b — mechanism: only if 3a did not land

Ask plainly for an adaptive survey over a handful of positions with a hook that
stops the run once it has seen two frames, and let the agent write and save the
hook. Record 3a as FAILED and 3b as the route taken.

**The stop must come from metadata, not from pixels.** Every demo frame is
bit-identical, so a content threshold either fires at tile 1 or never — a
criterion that cannot fail is not a criterion. A frame counter or the metadata
axes is the right shape here.

### Before you close the session — check the export produced something

**The tool result carries `emitted_calls`. Anything but a positive number means
Step 3 FAILED**, whatever the file looks like, and Steps 4–6 have nothing to run
against. Record the refusal verbatim; an empty export is the most valuable
finding this gate can produce and is invisible unless you look for it.

**If the agent also wrote a script by hand**, archive it under a name that cannot
be confused with the export and do not run it as part of this gate. It exercises
none of this code. Two `.py` files in one directory look identical at a glance,
and that is how a previous gate's evidence became unreadable.

## Step 4 — the emitted envelope is finite

```powershell
@'
import json, sys
from pathlib import Path
# The export's own path, read out of the session transcript. workspace_dir is
# absent from the schema-3 minimal document every in-app setup writes, so it
# cannot be the anchor; and a hand-written script must never be scored as this
# block's evidence, so mtime cannot be either.
here = Path(sys.argv[1])
histories = sorted(here.glob("*_microclaw_history.jsonl"), key=lambda p: p.stat().st_mtime)
if not histories:
    print("NO SESSION TRANSCRIPT IN THIS DIRECTORY - STOP"); sys.exit(1)
print("transcript:", histories[-1])
exports = []
for line in histories[-1].read_text(encoding="utf-8").splitlines():
    if not line.strip():
        continue
    content = json.loads(line).get("content")
    for block in content if isinstance(content, list) else []:
        if block.get("type") != "tool_result":
            continue
        body = block.get("content")
        if isinstance(body, list):
            body = "".join(b.get("text", "") for b in body if isinstance(b, dict))
        if not isinstance(body, str) or "Session script exported" not in body:
            continue
        try:
            exports.append(json.loads(body))
        except ValueError:
            pass
if not exports:
    print("NO EXPORT IN THIS SESSION - STOP"); sys.exit(1)
last = exports[-1]
print("emitted_calls:", last.get("emitted_calls"))
print("script:", last["output_path"])
if not last.get("emitted_calls"):
    print("EXPORT EMITTED NOTHING - STOP"); sys.exit(1)
Path(sys.argv[2]).write_text(last["output_path"], encoding="utf-8")
'@ | Set-Content "$Evidence\find-script.py" -Encoding UTF8
uv run python "$Evidence\find-script.py" $Repo "$Evidence\script-path.txt"
Write-Output "FIND EXIT: $LASTEXITCODE"
$Script = (Get-Content "$Evidence\script-path.txt" -Raw).Trim()
Write-Output "SCRIPT: $Script"
@'
import ast, re, sys
from pathlib import Path
text = Path(sys.argv[1]).read_text(encoding="utf-8")
m = re.search(r"^_LIMITS = (\{.*\})$", text, re.M)
if not m:
    print("NO _LIMITS IN SCRIPT - STOP"); sys.exit(1)
limits = ast.literal_eval(m.group(1))
print("_LIMITS:", limits)
bad = [axis for axis in ("x_um", "y_um", "z_um")
       if limits.get(axis) is None or any(e is None for e in limits[axis])]
print("exposure ceiling:", limits.get("exposure_ms", (None, None))[1], "(None is allowed)")
print("INCOMPLETE STAGE ENVELOPE: " + ", ".join(bad) + " - FAIL" if bad
      else "SIX FINITE STAGE EDGES - PASS")
sys.exit(1 if bad else 0)
'@ | Set-Content "$Evidence\check-limits.py" -Encoding UTF8
uv run python "$Evidence\check-limits.py" $Script
Write-Output "LIMITS EXIT: $LASTEXITCODE"
Select-String -Path $Script -Pattern "_survey_event_stream","SurveyProgress","RECORDED TOOL: run_adaptive"
Select-String -Path $Script -Pattern "import microclaw","from microclaw"
Select-String -Path $Script -Pattern "NOT EMITTED"
```

**Required, in order:**

1. `FIND EXIT: 0`, and `emitted_calls:` prints a **positive** number.
   **`emitted_calls: 0` means Step 3 FAILED**, whatever the file looks like —
   the finder stops on it rather than handing Steps 5–6 an empty script to run.
   The path comes from the transcript's own export record, so a hand-written
   script cannot be picked up by accident, and `workspace_dir` — absent from the
   minimal document in-app setup writes — is not needed.
2. The checker prints **`SIX FINITE STAGE EDGES - PASS`** and
   **`LIMITS EXIT: 0`**. Its printed `_LIMITS` must carry the same six numbers
   Step 1 read out of the config. `INCOMPLETE STAGE ENVELOPE` is a FAIL and is
   exactly what this block was written to prevent. The `exposure ceiling` line
   is informational: `None` there is correct and expected on a config with no
   `camera` section, and is what Step 7 comes back to.
3. The next `Select-String` prints matches for all three patterns — otherwise the session never
   reached an adaptive program and Steps 5–6 prove nothing about this block.
4. The next prints **nothing**: the script imports nothing from `microclaw`.
   Two `microclaw` strings may appear as *values*
   (`microclaw.analysis-observation/v1`, `microclaw.image_analysis.compute_stats`);
   neither is an import and neither is matched by this pattern.
5. The last prints **nothing**. A `# NOT EMITTED` line here means some step
   refused; record it verbatim, because after this block one of the reasons a
   step can refuse is an incomplete recorded envelope — and on a config that
   Step 1 confirmed complete, that would be a defect in this block.

## Step 5 — close Microclaw, run the script

Exit the Microclaw server completely and confirm no Microclaw process remains.
Leave Micro-Manager and the bridge running.

```powershell
if (-not (Test-Path $Script)) { Write-Output "NO SCRIPT PATH - STOP"; return }
uv run python $Script > "$Evidence\standalone.txt" 2>&1
Write-Output "EXIT: $LASTEXITCODE"
Get-Content "$Evidence\standalone.txt"
```

**The `Test-Path` line is not decoration.** On round 1 Step 4's finder died, left
`$Script` empty, and `uv run python` with no argument read EOF from stdin and
exited **0** — writing an empty `standalone.txt` that looked like a quiet
success. A step whose evidence file is empty has not passed.

**Required:** `EXIT: 0`, and the run completes the acquisition and writes its
dataset and its hook log **beside the script**. Confirm the dataset from the
acquisition display or the filesystem — an empty terminal is not evidence that
frames were taken. Compiling or importing the file is not evidence either.

## Step 6 — the refusal, at the portable guard

This is the new behaviour itself. It edits a **copy** of the script; the original
from Step 5 is untouched and stays in the evidence directory.

```powershell
$Bad = Join-Path $Evidence "edited-envelope.py"
Copy-Item $Script $Bad
(Get-Content $Bad -Raw) -replace "('x_um': \()[^,]+", '${1}None' | Set-Content $Bad -Encoding UTF8
Select-String -Path $Bad -Pattern "'x_um': \(None,"
```

**Required: that last command prints one match.** If it prints nothing the edit
did not apply, and everything below would "pass" while testing nothing — **STOP
and report it** rather than continuing.

```powershell
uv run python $Bad > "$Evidence\edited-envelope-run.txt" 2>&1
Write-Output "EXIT: $LASTEXITCODE"
Get-Content "$Evidence\edited-envelope-run.txt"
Select-String -Path "$Evidence\edited-envelope-run.txt" -Pattern "SafetyViolation"
```

**Required:**

- `EXIT:` prints a **non-zero** value.
- The output contains a `SafetyViolation` whose message names **X** and says the
  recorded envelope is incomplete. Quote it verbatim in the results.
- **No frames were acquired.** The seed-plan `guard.check_xy(...)` calls run
  before the `Acquisition` opens, so the refusal must arrive with no new dataset
  on disk and nothing on the acquisition display. Check the filesystem, not the
  terminal.

**Before this block, this same edited script would have run to completion with
no X bound at all.** That is the whole of what Step 6 measures.

## Step 7 — the exposure check, against an open ceiling

`_schema_3_document` writes no `camera` section at all
(`setup_tools.py:277-289`), so `camera.max_exposure_ms` is `None` and every
emitted `guard.check_exposure(...)` runs against an open ceiling. That is the
regression this block can break.

**Round 1 could not test it, and the reason is worth reading before you start.**
This machine's config *is* the minimal document — Step 1 printed
`max_exposure_ms None` and there is no `camera:` section to remove — so round
1's strip-the-section step was a no-op whose verification could not fail. Worse,
the agent recorded its survey with no exposure argument, so the exported script
contained **zero** `guard.check_exposure` calls and the limb had nothing to
check. Both halves are fixed below: the open ceiling is confirmed from Step 4's
own output, and this step now forces an exposure into the recorded call.

### 7a — the ceiling is already open, confirmed from Step 4

Step 4's checker printed an `exposure ceiling:` line. **Required: it reads
`None`.** If it does, this machine runs the minimal document natively and every
script in Steps 3–6 already exported and ran against an open ceiling — record
that and go straight to 7b.

If it prints a number, this machine declares a ceiling, and the open-ceiling case
needs a config without one:

```powershell
$NoCam = Join-Path $Evidence "safety_config_no_camera.yaml"
@'
import sys
from pathlib import Path
from microclaw.paths import default_safety_config
src = Path(str(default_safety_config())).read_text(encoding="utf-8").splitlines()
out, skip, removed = [], False, False
for line in src:
    if line.startswith("camera:"):
        skip = True
        removed = True
        continue
    if skip and (line.startswith(" ") or line.startswith("\t") or not line.strip()):
        continue
    skip = False
    out.append(line)
Path(sys.argv[1]).write_text("\n".join(out) + "\n", encoding="utf-8")
print("REMOVED A CAMERA SECTION" if removed else "NOTHING TO REMOVE - THIS STEP IS A NO-OP")
'@ | Set-Content "$Evidence\strip-camera.py" -Encoding UTF8
uv run python "$Evidence\strip-camera.py" $NoCam
```

**Required: `REMOVED A CAMERA SECTION`.** `NOTHING TO REMOVE` means you are in
the 7a case above and should not pretend this step tested anything. Then launch
with `uv run microclaw --safety-config $NoCam serve` instead of the plain
`serve` in 7b, and require the session to **open** — a refusal here is a FAIL,
because this is the document in-app setup writes.

### 7b — force an exposure into the recorded call

Start a fresh session from `$Repo` as in Step 3. Then, verbatim:

> Run that same five-position SNR survey again, and set the camera exposure to
> 20 ms for it. Then export it as a standalone script.

**Name the exposure, do not describe an outcome.** The mechanism under test is
the *emitted* `guard.check_exposure(20.0)` line executing against a `None`
ceiling. A request phrased as "make sure the exposure is safe" gets satisfied by
the agent reasoning about exposure and never recording one, which is round 1's
failure in a different costume.

Close Microclaw, then run Step 4 and Step 5 again against the new export, plus:

```powershell
Select-String -Path $Script -Pattern "guard.check_exposure"
```

**Required:**

- That command prints **at least one match**, and the value in it is the 20 ms
  you asked for. **Nothing printed is a FAIL for this step** — it means the
  exposure never reached the recorded call and the limb is untested again.
- Step 4's checker still prints `SIX FINITE STAGE EDGES - PASS`, and its
  `exposure ceiling:` line still reads `None`.
- Step 5's standalone run exits 0. The emitted `guard.check_exposure(20.0)`
  passes against the open ceiling rather than raising — that is the whole point
  of the step, and it is only evidence if the line is actually in the file.

---

## Results template

Record, per step: PASS / FAIL / NOT RUN, plus the artifact that proves it. Attach
the evidence directory. Quote refusal messages verbatim — a paraphrase of a
refusal is not evidence of which refusal fired.

| Step | Verdict | Evidence |
| --- | --- | --- |
| 0 pin + suite | | `suite.txt` |
| 1 config | | `config.txt` |
| 2 offline suites | | `offline.txt` |
| 3 adaptive session + export | | history JSONL, `emitted_calls` |
| 4 finite envelope | | terminal output, script path |
| 5 standalone run | | `standalone.txt`, dataset |
| 6 edited envelope refuses | | `edited-envelope-run.txt` |
| 7a open ceiling | | Step 4 `exposure ceiling:` line |
| 7b emitted exposure check | | `guard.check_exposure` match + `standalone.txt` |
