# Block 41d — rig gate runbook

Branch `design41/path-expansion`. Read this on the rig, on this branch.

Block 41d fixes one thing: microclaw never expanded `~`. `~/microclaw_data/x`
was joined as an ordinary path segment, so 41b's M5 gate got a real directory
named `~` (`C:\Users\ries\AppData\Local\microclaw\~\microclaw_data\multipos_3sites`).
Now a leading `~` is **expanded, then confined**; an expansion that escapes a
configured workspace, or that cannot resolve at all, is **refused** rather than
written somewhere invisible.

Five steps, ~30 minutes. Any rig, including Demo. **Step 0 and G0 are not
optional — G0 is what proves the rest of this runbook can detect a failure.**

## Running this alongside 41c

41c gates on M5 in the same window and touches nothing here (`authorization.py`
and the channel surface). One session can serve both: do **G0 first, before any
41c step**, so the `~` inventory is taken on a disk 41c has not written to, and
do **G3 last**. G1/G2 use their own session and their own config copy, so they
do not disturb a 41c session that is already open. Nothing in this runbook
changes the rig's real `safety_config.yaml`.

## Before you start — confirm you are on the right code

```powershell
cd C:\path\to\microclaw
git merge-base --is-ancestor 63ddd2b HEAD
if ($?) { "PIN OK - implementation is present" } else { "PIN FAIL - stop, wrong branch" }
```

`$?` rather than `$LASTEXITCODE`: a cmdlet in between silently stales that
variable. Read the printed words.

```powershell
pip install -e .
python -c "import microclaw; print('LOADED FROM', microclaw.__file__)"
```

---

## Step 0 — which case is this rig in? (prediction, **not** the gate)

Whether a `~` path now *writes* or *refuses* depends on whether this rig
configures `workspace_dir`. Find out before you run anything, so you know which
result G1 is supposed to give.

```powershell
@'
from microclaw.config import load_safety_config
from microclaw.safety import SafetyGuard, SafetyViolation
c = load_safety_config().constraints
print("WORKSPACE_ROOT:", c.workspace_dir)
g = SafetyGuard(c)
for p in ("~/microclaw_data/gate41d", "a/~b/c.json"):
    try:
        print("RESOLVED:", p, "->", g.resolve_in_workspace(p))
    except SafetyViolation as e:
        print("REFUSED:", p, "->", e)
'@ | Set-Content -Encoding utf8 gate41d_probe.py
python gate41d_probe.py > gate41d-step0.txt 2>&1
Get-Content gate41d-step0.txt
```

Read it as follows, and **paste the file into the results**:

- `WORKSPACE_ROOT: None` → this rig confines nothing. Expect G1 to **write**
  under `C:\Users\<you>\microclaw_data`.
- `WORKSPACE_ROOT: <some path>` and the `~` line says `REFUSED:` → expect G1 to
  **refuse**, naming that root. Check the message also names what the `~`
  expanded to; if it does not, that is a FAIL on its own.
- `a/~b/c.json` must always come back **RESOLVED**, with `~b` still in it. A `~`
  that is not the first component is an ordinary directory name and must not be
  touched.

This probe runs the real resolver against the rig's real config, but it is a
prediction only. It calls no tool and writes no data, so it cannot stand in for
G1 — a resolver that is right in isolation and a tool that never calls it would
both pass here.

---

## G0 — the `~` inventory, taken **before** you run anything

*This is the instrument the rest of the gate depends on, so it is validated
first, on evidence that already exists.*

```powershell
$roots = @("$env:LOCALAPPDATA\microclaw", "$env:USERPROFILE")
# add the workspace root from Step 0 here too, if there was one
$found = foreach ($r in $roots) {
  Get-ChildItem -LiteralPath $r -Recurse -Directory -Depth 4 -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -eq '~' } |
    Select-Object -ExpandProperty FullName
}
(@($found) -join "`r`n") | Set-Content gate41d-tilde-before.txt
Get-Content gate41d-tilde-before.txt
"COUNT: $(@($found).Count)"
```

(`Where-Object`, not `-Filter '~'`: the Windows filter API also matches 8.3
short names, and this must match the literal name only. The `-join` is so the
file exists even when nothing is found — `Compare-Object` later needs both
files to be readable.)

Paste `gate41d-tilde-before.txt` and the COUNT line.

**This step must FIND something on M5.** 41b's gate left
`...\AppData\Local\microclaw\~\microclaw_data\multipos_3sites` on that machine
on 2026-08-06. That directory is this block's known-bad evidence, and a finder
that does not report it is broken — in which case G1 and G3's "no `~` directory
was created" mean nothing and the gate **fails as unvalidated**, whatever the
rest of it says.

If someone has already deleted it (or you are on a different rig), prove the
finder works before continuing:

```powershell
New-Item -ItemType Directory -Path "$env:LOCALAPPDATA\microclaw" -Name '~' -Force | Out-Null
# re-run the finder above: it MUST list that path. Then:
Remove-Item -LiteralPath "$env:LOCALAPPDATA\microclaw\~" -Recurse
```

(`-Name '~'` and `-LiteralPath`, never a bare `-Path "...\~"`: PowerShell
expands `~` itself in some provider paths, and this whole block is about not
letting a `~` mean something you did not ask for.)

Also look inside what you found and say what is in it:

```powershell
Get-ChildItem -LiteralPath "$env:LOCALAPPDATA\microclaw\~" -Recurse |
  Select-Object FullName, Length
```

**What to do with it — your call, not the runbook's.** The proposal is: it holds
real data from 41b's gate that the operator asked to be written to
`~/microclaw_data`, so **move it** to where it was meant to go rather than
deleting it —

```powershell
Move-Item -LiteralPath "$env:LOCALAPPDATA\microclaw\~\microclaw_data" `
          -Destination "$env:USERPROFILE\microclaw_data_from_41b"
Remove-Item -LiteralPath "$env:LOCALAPPDATA\microclaw\~" -Recurse  # once it is empty
```

— and delete only if you have looked and it is worthless. Either way, record
what you decided. If you leave it in place, that is fine too: G1 and G3 compare
the *new* inventory against `gate41d-tilde-before.txt`, so a pre-existing `~`
does not confuse them.

---

## G1 — a tool given `~/...` writes under the real home, or refuses naming the root

*Checklist criterion 1: **no directory named `~` is created anywhere.***

Open microclaw normally (the rig's own config, no flags) and ask it, in these
words or close to them:

> Save a single snap to `~/microclaw_data/gate41d_g1`.

Any tool that takes a path is fine — a timelapse of one frame, a log export, a
TIFF export. The point is that the path came from you, through the model, into
a real tool.

**PASS is one of these two, matching Step 0's prediction:**

1. **It wrote.** The data is under `C:\Users\<you>\microclaw_data\gate41d_g1`.
   Confirm with `Test-Path` and list the files:
   ```powershell
   Test-Path "$env:USERPROFILE\microclaw_data\gate41d_g1"
   Get-ChildItem -Recurse "$env:USERPROFILE\microclaw_data\gate41d_g1" | Select-Object FullName, Length
   ```
   `True` and a non-empty listing. `True` alone is not enough — an empty
   directory means the path resolved and the write did not.

2. **It refused**, and the refusal names **both** the configured workspace root
   **and** what the `~` expanded to. Paste the message verbatim. A refusal that
   says only "escapes the configured workspace directory" without the expansion
   is a FAIL: the operator cannot tell why their own home directory was
   rejected.

   **If G1 refused, run it once more with a root that *contains* home**, or the
   successful-expansion path is never seen on this rig at all:

   ```powershell
   $dst2 = "$env:USERPROFILE\Desktop\safety_config_41d_home.yaml"
   Copy-Item "$env:APPDATA\microclaw\safety_config.yaml" $dst2 -Force
   (Get-Content $dst2) -notmatch '^\s*workspace_dir\s*:' | Set-Content $dst2
   Add-Content $dst2 "workspace_dir: $env:USERPROFILE"
   microclaw --safety-config $dst2 serve
   ```

   Ask for `~/microclaw_data/gate41d_g1b` and confirm it **writes** to
   `C:\Users\<you>\microclaw_data\gate41d_g1b`. Then close that session.

**Then re-run G0's finder**, save as `gate41d-tilde-after-g1.txt`, and diff:

```powershell
Compare-Object @(Get-Content gate41d-tilde-before.txt) `
               @(Get-Content gate41d-tilde-after-g1.txt)
```

(The `@( )` matter: an empty file gives `$null`, and `Compare-Object` refuses a
null argument rather than reporting "no difference".)

**PASS is no `=>` lines** — nothing new named `~` anywhere. Any new one is an
outright FAIL of the block.

Record the shell's working directory too (`Get-Location`), because with no
workspace root configured an unexpanded `~` lands relative to it — that is how
41b's ended up under `AppData\Local\microclaw`, which is the desktop shortcut's
working directory.

---

## G2 — the refusal path, forced

*Run this even if Step 0 said `WORKSPACE_ROOT: None`. Otherwise the refusal
branch is never exercised on any rig, and a gate where nothing can fail is the
defect this checklist keeps producing.*

Build a throwaway config with a workspace root that does **not** contain your
home directory. This does not touch the rig's real config.

```powershell
$src = "$env:APPDATA\microclaw\safety_config.yaml"
$dst = "$env:USERPROFILE\Desktop\safety_config_41d.yaml"
New-Item -ItemType Directory -Path "C:\microclaw_gate41d" -Force | Out-Null
Copy-Item $src $dst -Force
(Get-Content $dst) -notmatch '^\s*workspace_dir\s*:' | Set-Content $dst
Add-Content $dst 'workspace_dir: C:\microclaw_gate41d'
Select-String -Path $dst -Pattern 'workspace_dir|reviewed'
```

Both `workspace_dir: C:\microclaw_gate41d` and `reviewed: true` must print.
Then:

```powershell
microclaw --safety-config "$env:USERPROFILE\Desktop\safety_config_41d.yaml" serve
```

(Session flags go **before** the subcommand; drop `serve` if you drive microclaw
from the terminal instead of the browser.)

Ask for **two** saves in that one session, in this order:

1. > Save a snap to `~/microclaw_data/gate41d_g2`.
2. > Save a snap to `C:\microclaw_gate41d\gate41d_g2_inside`.

**PASS requires both directions:**

- (1) is **refused**, and the message names `C:\microclaw_gate41d` *and* the
  expansion (`C:\Users\<you>\microclaw_data\gate41d_g2`). Paste it verbatim.
- (2) **succeeds** and the data is there:
  ```powershell
  Get-ChildItem -Recurse "C:\microclaw_gate41d\gate41d_g2_inside" | Select-Object FullName, Length
  ```

If both refuse, confinement is over-broad. If both succeed, the confinement
check is not running and (1) proves nothing. Either is a FAIL.

Re-run G0's finder against `C:\microclaw_gate41d` as well:

```powershell
Get-ChildItem -Path "C:\microclaw_gate41d" -Recurse -Directory |
  Where-Object { $_.Name -eq '~' } | Select-Object -ExpandProperty FullName
```

**PASS is no output.** A `~` directory here is exactly the 41b defect and is the
single most important negative result in this gate.

Close that session. Delete `C:\microclaw_gate41d` and the two
`safety_config_41d*.yaml` copies when you are done with the whole runbook, not
before. The rig's own `safety_config.yaml` was never touched.

---

## G3 — 41b's acquisition, with a `~` path

*Checklist criterion 2: re-run the acquisition from block 41b's gate with a `~`
path and confirm the dataset lands where the operator expects.*

Back on the rig's own config (no `--safety-config`). Run the same shape of
session 41b's G1 used — move the stage, set an exposure, snap and analyze, mark
two or three positions, then a small multi-position acquisition — but give the
acquisition **`~/microclaw_data/gate41d_multipos`** as its save directory.

**PASS when all four hold:**

1. The acquisition runs and the dataset exists at
   `C:\Users\<you>\microclaw_data\gate41d_multipos` (AcqEngJ's `_1` suffix is
   normal), with one folder per what you asked for and non-zero file sizes:
   ```powershell
   Get-ChildItem -Recurse "$env:USERPROFILE\microclaw_data\gate41d_multipos*" |
     Measure-Object -Property Length -Sum
   ```
2. **Or** — if this rig configures a `workspace_dir` that excludes your home —
   it refuses before any hardware moves, naming the root and the expansion, and
   **no partial dataset** is left anywhere. Then re-run it with a path inside the
   root and confirm *that* dataset lands, so the acquisition itself is still
   shown to work.
3. Re-run G0's finder, save as `gate41d-tilde-after-g3.txt`, `Compare-Object`
   against the before file: **no new `~` anywhere.**
4. Ask microclaw where it saved the data, and check that what it *told* you
   matches what is on disk. A tool that writes correctly and reports the
   unexpanded `~/...` back to the operator is a partial failure — record it.

---

## Recording the result

For Step 0, G0, G1, G2, G3 write **PASS**, **FAIL**, or **SKIPPED (reason)**,
and paste:

- `gate41d-step0.txt`;
- `gate41d-tilde-before.txt`, `-after-g1.txt`, `-after-g3.txt`, and the
  `Compare-Object` output for each;
- every refusal message **verbatim** — the wording is half of what this block
  changed;
- what you found in 41b's leftover `~` directory and what you decided to do
  with it;
- the working directory each session ran from.

Say which rig each step ran on. A step you could not run is not a pass.

**If a step fails, leave the artifact where it is.** A `~` directory produced by
this gate is the evidence; do not tidy it away before it is reported, and run any
re-test into a new folder name (`gate41d_g1_round2`) rather than over the top of
the failure.
