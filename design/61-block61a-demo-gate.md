# Block 61a demo gate — skills are files, and one `load_skill` replaces five tools

Branch `design61/skills-are-files`. Two artifacts: `61-block61a-demo-gate.py`
beside this file runs every limb that only computes, and this document holds the
two limbs that need a person driving a session and reading what the agent chose
to do.

Run the program first. If G1 fails, the build did not ship its skills and the
driven session below would measure nothing.

## Step 0 — confirm you are on the right code

```powershell
$repo = "$HOME\Code\microclaw"
cd $repo
git fetch origin
git checkout design61/skills-are-files
git pull
git merge-base --is-ancestor 26cae61 HEAD
if ($LASTEXITCODE -eq 0) { "IMPLEMENTATION PRESENT" } else { "WRONG TREE - STOP" }
```

Expect `IMPLEMENTATION PRESENT`. Anything else, stop and report.

## Step 1 — install this branch the way the machine normally installs

```powershell
cd $repo
.\install.bat
```

This is the limb that matters most, and it is why the gate exists. The suite
proves the skills are in the source tree and in a built wheel; only
`install.bat` proves that the artifact this machine actually runs carries
`microclaw/skills/*/SKILL.md`. `pyproject.toml` listed package-data files
individually before this block and now carries a pattern instead.

## Step 2 — run the gate program against the *installed* build

Every command below is literal. Nothing needs substituting.

```powershell
$slot = (Get-Content "$env:LOCALAPPDATA\microclaw\active-slot.txt" -Raw).Trim()
$py = "$env:LOCALAPPDATA\microclaw\env-$slot\Scripts\python.exe"
cd $HOME
& $py "$repo\design\61-block61a-demo-gate.py" --output "$HOME\block61a-demo-evidence"
"exit code: $LASTEXITCODE"
```

`cd $HOME` is not decoration: the program scores whatever `microclaw` its
interpreter imports, and it refuses outright if that turns out to be the
checkout. Its first line prints which `microclaw` it imported — check that it is
under `$env:LOCALAPPDATA\microclaw` before reading any limb.

Expect three `PASS` lines, `BLOCK 61a DEMO GATE PASSED`, and `exit code: 0`.

Send `$HOME\block61a-demo-evidence\gate.txt` and `results.json`.

## Step 3 — G4, the reach limb: does a triggered session load the skill?

**Do not name any tool in what you type.** The whole question is whether the
agent gets to the skill on its own; naming `load_skill` answers it for the
agent.

Start a session (`microclaw serve`, or the desktop shortcut) and type exactly
this, and nothing else:

```
I need to run dSTORM on this sample. What acquisition parameters should I use?
```

Read the transcript and record **the order of the tool calls**. Score it on the
trace, not on whether the answer sounds good — an agent can produce plausible
dSTORM numbers from memory, and that is the failure being measured.

- **PASS** if a `load_skill` call with `name` = `smlm` appears **before** the
  assistant proposes any acquisition parameter (an exposure, a frame count, a
  laser power).
- **FAIL** if it proposes a parameter first, or never loads the skill.

Answer both, in what you send back:

1. Did `load_skill(name="smlm")` appear at all? Yes / No.
2. If yes, did it come before the first proposed number? Yes / No.

**If G4 fails, do not stop. Record it, then ask directly in a NEW session**, so
one round yields both answers instead of neither:

```
Load the smlm skill, then tell me what acquisition parameters to use for dSTORM.
```

If the direct ask works and the unprompted one did not, that is a *reach*
finding and not a mechanism finding — the code works and nothing routes to it.
Say which of the two prompts you ran, and send both transcripts if you ran both.

## Step 4 — G5, the export limb

**Stay in the Step 3 session.** A fresh session exports a 13-line stub, so there
has to be a run in front of the export. Type exactly:

```
Run a 2-frame timelapse at 10 ms on the current position, then export this session as a standalone script. Tell me the absolute path of the script you wrote.
```

The agent reports the path. Put it in `$script` and run these two checks — they
print words, so a typo cannot read as a pass:

```powershell
$script = "PASTE THE ABSOLUTE PATH THE AGENT REPORTED"
if (-not (Test-Path $script)) { "STOP - no file at that path" } else { "found: $script" }
$hits = Select-String -Path $script -Pattern "NOT EMITTED"
if ($hits) { "FAIL: $($hits.Count) NOT EMITTED line(s)"; $hits } else { "PASS: no NOT EMITTED lines" }
& $py -c "import ast,sys; ast.parse(open(sys.argv[1],encoding='utf-8').read()); print('PASS: script compiles')" $script
```

- **PASS** requires `PASS: no NOT EMITTED lines` **and** `PASS: script compiles`.
- **FAIL** on any `# NOT EMITTED:` line. `load_skill` is decorated
  `@emits_nothing`; an undecorated tool plants a `raise RuntimeError` in the
  exported script, and that has shipped three times before (43h
  `generate_and_save_hook`, 47 `set_roi`/`clear_roi`, 52a `move_named_stage`).

Send the emitted script itself.

## What to send back

- `gate.txt` and `results.json` from Step 2, and the printed exit code
- the Step 3 transcript, or the two Yes/No answers plus which prompt you ran
- the two `PASS`/`FAIL` lines from Step 4, and the emitted script

## If G4 fails and you want it measured rather than argued

`design/61-skill-routing-spike.py` replays Step 3's opening against the live
model over N samples and reports how often the skill is loaded before the first
parameter. It costs API credit, so running it is the coordinator's call and it
is not part of this gate. One session is a data point, not a rate.
