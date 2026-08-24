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
import sys
from pathlib import Path
from microclaw.paths import default_safety_config
from microclaw.safety import ParsedSafetyConfig
root = ParsedSafetyConfig.from_yaml(str(default_safety_config())).constraints.workspace_dir
# An export is identified by its own provenance line, not by being newest: a
# hand-written script and a saved hook are both .py files in the same tree, and
# a previous gate lost a round to exactly that confusion.
cands = [p for p in Path(root).rglob("*.py")
         if "# RECORDED TOOL:" in p.read_text(encoding="utf-8", errors="ignore")]
if not cands:
    print("NO EXPORTED SCRIPT FOUND - STOP"); sys.exit(1)
newest = max(cands, key=lambda p: p.stat().st_mtime)
print("script:", newest)
print("candidates seen:", len(cands))
Path(sys.argv[1]).write_text(str(newest), encoding="utf-8")
'@ | Set-Content "$Evidence\find-script.py" -Encoding UTF8
uv run python "$Evidence\find-script.py" "$Evidence\script-path.txt"
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

1. `SCRIPT:` names a `.py` file inside `workspace_dir`. If the finder printed
   `NO EXPORTED SCRIPT FOUND - STOP`, Step 3 did not export and this step cannot
   run. The finder selects on the `# RECORDED TOOL:` provenance line rather than
   on mtime, so a hand-written script cannot be picked up by accident; if
   `candidates seen` is greater than 1, say which file was chosen in the
   results.
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
uv run python $Script > "$Evidence\standalone.txt" 2>&1
Write-Output "EXIT: $LASTEXITCODE"
Get-Content "$Evidence\standalone.txt"
```

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

## Step 7 — the minimal document: no `camera:` section

The config `_schema_3_document` writes carries no `camera` section at all, so
`camera.max_exposure_ms` is `None` and every emitted `guard.check_exposure(...)`
runs against an open ceiling. This step is the regression the design names.

```powershell
$NoCam = Join-Path $Evidence "safety_config_no_camera.yaml"
@'
import sys
from pathlib import Path
from microclaw.paths import default_safety_config
src = Path(str(default_safety_config())).read_text(encoding="utf-8").splitlines()
out, skip = [], False
for line in src:
    if line.startswith("camera:"):
        skip = True
        continue
    if skip and (line.startswith(" ") or line.startswith("\t") or not line.strip()):
        continue
    skip = False
    out.append(line)
Path(sys.argv[1]).write_text("\n".join(out) + "\n", encoding="utf-8")
print("wrote", sys.argv[1])
'@ | Set-Content "$Evidence\strip-camera.py" -Encoding UTF8
uv run python "$Evidence\strip-camera.py" $NoCam
Select-String -Path $NoCam -Pattern "^camera:","max_exposure_ms"
```

**Required: the last command prints nothing.** If it prints anything, the strip
did not work — STOP and report it.

```powershell
Set-Location $Repo
uv run microclaw --safety-config $NoCam serve
```

**Required:** the session **opens**. A refusal here is a FAIL — the camera
section is optional and this is the document the in-app setup writes.

In that session, repeat Step 3's request (3a's wording; 3b if it does not land).
A two-position, two-frame run is enough — this step is about the export, not
about the acquisition. Then close Microclaw and run Step 4 and Step 5 again
against the new export.

**Required:**

- Step 4's checks all pass on the new script, and its `_LIMITS` shows
  `'exposure_ms': (0.0, None)` — the open ceiling, which is **correct and
  expected here**, and which Step 4's `None`-hunting pattern deliberately does
  not match.
- Step 5's standalone run exits 0. The emitted `guard.check_exposure(...)` calls
  pass against that open ceiling rather than raising.

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
| 7 no-camera document | | second export + `standalone.txt` |
