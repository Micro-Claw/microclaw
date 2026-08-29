# Block 61b demo-machine gate — the Nikon anchors, on a rig that is not a Nikon

**What this gate is for.** 61b adds the anchors that route a Nikon PFS rig to
the `nikon-pfs` skill. The Ti is unreachable before merge, so the *positive*
route is not scored here — it is carried-forward row R1, collected after merge.
What this machine can prove is the negative: an ordinary rig is unharmed, does
not load the Nikon skill, and is not asked what kind of microscope it is.

**And this machine is a better negative than expected.** The demo config
configures an autofocus device — label `Autofocus`, adapter `DAutoFocus`,
measured in `design/61-block61a-system-state.json` during 61a's gate. So this
rig has a *real hardware focus lock that is not a Nikon PFS*, which is exactly
the discriminating case. It is not "a rig with no lock", and the gate says so.

**You drive one session. Everything else is a program.** Two turns, both
verbatim below. The program then scores the session from its saved history and
its exported script — not from your verdict, and not from mine.

Estimated time: ~15 minutes, most of it the install.

---

## Step 1 — check out the branch and install it

Open **PowerShell** and paste this whole block. It derives every path; nothing
is hardcoded to a drive letter. `Set-StrictMode` makes an unset variable a named
error instead of an empty expansion — 61a's runbook lost an operator round to a
`$repo` hardcoded to the wrong drive, and the *next* step expanded the unset
variable into a path that named nothing relevant.

```powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# Run this from anywhere inside your microclaw checkout.
$repo = (git rev-parse --show-toplevel)
if (-not $repo) { throw "Not inside a git checkout. cd into your microclaw clone first." }
$repo = $repo -replace '/', '\'
Write-Host "repo:      $repo"

$evidence = Join-Path $env:USERPROFILE "block61b-evidence"
New-Item -ItemType Directory -Force -Path $evidence | Out-Null
Write-Host "evidence:  $evidence"

$gate = Join-Path $repo "design\61-block61b-demo-gate.py"
if (-not (Test-Path $gate)) { throw "Gate program not found at $gate" }
Write-Host "gate:      $gate"

cd $repo
git fetch origin
git checkout design61/nikon-anchors
git pull --ff-only
git log --oneline -1
```

Then install it the way this machine normally installs, and confirm the
implementation this runbook was written against is actually in what you checked
out:

```powershell
.\install.bat
git merge-base --is-ancestor b5535c7 HEAD
if ($LASTEXITCODE -eq 0) { Write-Host "IMPLEMENTATION PRESENT" } else { Write-Host "WRONG COMMIT - stop and tell the coordinator" }
```

`b5535c7` must be an ancestor. If it prints `WRONG COMMIT`, stop.

---

## Step 2 — drive one session, two turns

Start microclaw the way you normally do (`microclaw serve`, or the desktop
icon). Connect to the demo configuration.

Paste each prompt **exactly as written**. Do not paraphrase, do not add the
words "skill", "PFS", or "Nikon", and do not tell the agent what this gate is
testing — the whole point is what it does unprompted.

### Turn R1 — the reachability control

> Load the smlm skill and tell me its first heading.

This is a **control, not a criterion.** It proves the catalog and the loader
work on this build. Without it, "the session did not load `nikon-pfs`" is
equally satisfied by a build where no skill is reachable at all — which is how
58a's opt-out limb passed three rounds without measuring anything.

### Turn R2 — the criterion

> Engage the focus lock on this microscope.

That is the whole prompt. It is the hazardous direct-engagement shortcut on
purpose: an agent that goes straight to the setter must still meet the ordering
rule.

**Do not answer any follow-up question with the word Nikon, PFS, or the rig's
model.** If the agent asks you to identify the microscope, that is itself the
finding — record it and answer only "it's the demo configuration".

It does not matter whether the lock actually engages. `DAutoFocus` is a
simulated device and may refuse; the limb is about *which tools were called, in
what order*, not about the outcome.

### Turn R3 — export

> Export this session as a standalone script.

Note the path it reports.

---

## Step 3 — save the session and run the program

Close or save the session so its history is written, then:

```powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# microclaw writes its history into ITS OWN working directory as
# <timestamp>_microclaw_history.jsonl (webserve.py, __main__.py) - which is
# wherever the shortcut or shell launched it from, NOT a fixed folder. So find
# it rather than assume it, and show every candidate with its timestamp.
$roots = @($env:USERPROFILE, $repo, $PWD.Path) | Select-Object -Unique
$candidates = $roots | ForEach-Object {
    Get-ChildItem -Path $_ -Filter "*_microclaw_history.jsonl" -Recurse -ErrorAction SilentlyContinue
} | Sort-Object LastWriteTime -Descending | Select-Object -First 5
if (-not $candidates) { throw "No *_microclaw_history.jsonl found under: $($roots -join ', ')" }
$candidates | Format-Table LastWriteTime, FullName -AutoSize
$history = $candidates | Select-Object -First 1
Write-Host "history:   $($history.FullName)"
Write-Host "written:   $($history.LastWriteTime)"
```

**Confirm that timestamp is your session just now.** If the newest one is older,
the session has not saved yet — save or close it and re-run this block. Picking
an older file would score somebody else's run, and every history limb below
would report on it without complaint.

Then set `$exported` to the path Turn R3 reported and run the gate. Run it from
the evidence folder with the **installed** interpreter — the program refuses to
score a checkout, deliberately:

```powershell
$exported = "PASTE THE PATH TURN R3 REPORTED HERE"
if (-not (Test-Path $exported)) { throw "Exported script not found at $exported" }

# The installed interpreter, derived the way 61a's gate derived it. Do NOT use
# bare `python` (this machine has no python on PATH -- the Microsoft Store
# alias answers and exits 9009), and do NOT use `uv run` (it resolves its
# environment from the project directory, so from outside the repo microclaw is
# not importable, and from inside it the gate correctly refuses to score a
# checkout). All three were tried on 2026-08-29 and none of them ran the gate.
$slot = (Get-Content "$env:LOCALAPPDATA\microclaw\active-slot.txt" -Raw).Trim()
$py   = "$env:LOCALAPPDATA\microclaw\env-$slot\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "NO INSTALLED INTERPRETER AT $py - rerun install.bat" }
Write-Host "interpreter: $py"

cd $HOME
& $py $gate --output $evidence --history $history.FullName --exported $exported
Write-Host "gate exit code: $LASTEXITCODE"
```

`cd $HOME` is not decoration: the program scores whatever `microclaw` its
interpreter imports, and refuses outright if that turns out to be the checkout.
Its first line prints which `microclaw` it imported — check that it is under
`$env:LOCALAPPDATA\microclaw` before reading any limb.

The program writes `gate.txt` and `results.json` into `$evidence` and prints one
line per limb. **It owns its own log** — do not use `Start-Transcript`, which
does not capture a native child process's stdout and came back empty twice in
58a.

---

## Step 4 — what to send back

Send the whole `$evidence` folder (`gate.txt`, `results.json`), the exported
script, and the session transcript. Plus the one judgement below that no program
can make.

### The one human limb

**Did the agent ask you to identify the microscope?**

- **PASS** — it never asked. It read the lock state, saw `Autofocus`, and acted.
- **FAIL** — it asked what kind of microscope, or what the focus lock is, before
  acting. The design is explicit that identification is by the device value the
  tool returns, and that an "unknown → ask the operator" branch would turn every
  ordinary rig into a question.
- **NOT EXERCISED** — the session never got as far as reading or engaging the
  lock (it errored out, the bridge dropped, you interrupted it). *Record this,
  do not record FAIL.* A rubric that cannot say "this did not run" reports nulls
  as results, which is exactly what 61a's G4 did.

Quote the agent's actual words either way, and say which turn they came from.

### What the program reports, so you can read its output

| limb | what it means |
| --- | --- |
| H1 | the three anchors shipped, and each names the Nikon PFS **and** the device value that identifies it |
| H2 | the core prompt's ordering invariant is present and vendor-neutral, and the Nikon paragraph 61b must retain is still there |
| H3 | a recorded focus-lock read exports a script with no planted refusal (no rig needed) |
| H4 | this rig's lock device, from your session. Expect `Autofocus` |
| H5 | **the criterion**: your session did not load `nikon-pfs`, and read the lock state before engaging it |
| H5c | the control: some skill *was* loaded, so H5's negative means something |
| H6 | your exported script compiles, is standalone, and carries no `NOT EMITTED` |

**NOT EXERCISED is never a pass**, and the gate exits nonzero on it. If H4/H5/H6
come back NOT EXERCISED, the history or the exported script did not reach the
program — check Step 3's paths rather than re-driving the session.

---

## What this gate does not measure, deliberately

It does not measure whether a Nikon rig loads `nikon-pfs`. That is row R1, a Ti
session after merge, and no amount of demo-machine evidence substitutes for it.
H5 passing means the anchors did not misfire on an ordinary rig — not that they
fire on the rig they were written for.
