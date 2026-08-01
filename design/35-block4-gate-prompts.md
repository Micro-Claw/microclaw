# design/35 Block 4 — first-launch setup rig gate

Gate for `design33/first-launch-setup`, implementation commits `fb04a8e` and
`e9fa769`. The branch is pushed at `origin/design33/first-launch-setup`; **do
not merge until this gate passes and the coordinator reviews the evidence.** Do
not open a PR.

This gate is unusual in that **the connection itself is the thing under test.**
`microclaw first-launch-setup` contacts hardware before any safety config
exists to gate it. On a rig whose emission path is undeclared, enumeration may
initialize and emit. G1–G3 run on the demo core, where that risk is nil. G4 runs
on M5, where it is not: a qualified M5 operator must be present, the rig's
normal optical containment and emergency-stop procedure must be established
first, and the run must be treated as potentially emissive from the moment the
acknowledgement is typed. Stop immediately on anything unexpected and put the
rig in its operator-approved safe state.

The generated profile is **never** written over the deployed M5 config. Every
`--out` in this document points inside the evidence directory. `--force` is
never used on the deployed path.

Commands are PowerShell-safe and run from the repository root. The global
`--port` and `--safety-config` options come **before** the subcommand. Keep
commands and output in one dated evidence directory. Do not use PowerShell
`Start-Transcript` for child processes: Windows PowerShell 5.1 records its own
output stream, not a child's console writes, and produced empty evidence in
round 1. Non-interactive commands use `> file.txt 2>&1`. Interactive setup must
remain attached to the console so prompts are visible; Microclaw itself writes
and flushes a timestamped `first-launch-transcript-*.txt` under each
`--evidence-out` directory. Timestamping is deliberate: a later refused or
aborted attempt must never truncate evidence from an earlier run.
That application-owned file, not selected/copy-pasted terminal text, is the
evidence of prompts, answers, refusals, deferrals, and final outcome.

## What this gate settles

1. A complete setup pass on a real Micro-Manager produces a profile that block
   3's offline validator accepts, and that starts a session once a human sets
   `reviewed: true`.
2. **An unresolved choice cannot be silently accepted by a human at the
   keyboard.** This is the item Phase 3's gate never established, and it is the
   reason automated input fixtures do not discharge it.
3. The hardware-contact acknowledgement exits before connecting when it is not
   given exactly.
4. On a real rig with real hazards, setup produces a profile whose differences
   from the deployed M5 config are enumerable and explainable.
5. Refusals leave no file at `--out`, and unenumerable classification
   coordinates stop generation entirely.

## What this gate does not settle

It does not prove heuristic discovery found every physical emission path — the
setup text says so itself, and the operator classification step exists because
of it. It does not close the pre-validation enumeration window; it documents
it. It does not exercise continuous focus, which Block 4 hard-excludes.

## Which machine runs what, and what already exists on it

G1–G3 run on the **demo machine**. G4 runs on **M5**. They are different
machines, so G0 runs once on each, into its own evidence directory, and the two
bundles come back separately.

**There is no pre-existing safety config on the demo machine, and none is
needed.** That is the point of the block: `microclaw first-launch-setup` runs
before any config exists — `main()` dispatches it without loading one — and G1
generates the machine's first profile. Nothing in G1–G3 reads a deployed config,
and `$Deployed` below does not exist there. If the demo machine happens to have
an old hand-authored config lying around, ignore it; do not use it as a
reference and do not let setup overwrite it (`--out` always points inside the
evidence directory).

**M5 is different: it already runs under a hand-authored reviewed config.** That
file is `$Deployed`. It was not produced by setup — it predates this command and
was written by hand from `safety_config.example.yaml`, which is why Block 5
exists to fix the fictional acquisition budgets it inherited. G4's comparison is
therefore *generated profile vs. the existing hand-authored one*, which is a
real comparison precisely because the two were produced by different means. It
is the only place in this gate where a deployed config is read, it is read
read-only, and nothing in this gate writes to it.

If you cannot locate a deployed M5 config, stop and tell me rather than
substituting the example file — a comparison against `safety_config.example.yaml`
would compare the generated profile against fiction and prove nothing.

## G0 — setup and identity (run on each machine)

Replace every angle-bracket value before running anything. This runbook is on
the implementation branch, so the checkout below leaves it in the working tree
and it stays readable for the whole gate. What pins the implementation is the
`--is-ancestor` check, not a branch tip hash, so amending this document does not
invalidate the check inside it.

```powershell
$Repo         = "<absolute repo path>"
$Port         = 4827
$Stamp        = Get-Date -Format "yyyyMMdd-HHmmss"
$EvidenceRoot = "<absolute evidence parent OUTSIDE the repo>"
$Machine      = "<demo or m5>"
$Evidence     = Join-Path $EvidenceRoot "block4-$Machine-$Stamp"

Set-Location $Repo
New-Item -ItemType Directory -Path $Evidence | Out-Null
git fetch origin design33/first-launch-setup > "$Evidence\git-fetch.txt" 2>&1
git switch design33/first-launch-setup > "$Evidence\git-switch.txt" 2>&1
git pull --ff-only > "$Evidence\git-pull.txt" 2>&1
git rev-parse HEAD > "$Evidence\head.txt" 2>&1
git merge-base --is-ancestor e9fa769a85ef28e497c4cb25f7938018ece9867d HEAD
$LASTEXITCODE > "$Evidence\contains-implementation-tip.txt"
git status --short > "$Evidence\status.txt" 2>&1
python -V > "$Evidence\python.txt" 2>&1
pip install -e . > "$Evidence\pip-install.txt" 2>&1
Copy-Item "design\35-block4-gate-prompts.md" (Join-Path $Evidence "runbook.md")
```

A normal branch checkout, not a detached HEAD: you stay on
`design33/first-launch-setup` for the whole gate, and this runbook is at
`design\35-block4-gate-prompts.md` in front of you. The `runbook.md` copy is for
the evidence archive, so the returned bundle records which revision was run.

`contains-implementation-tip.txt` must read `0`. Reinstalling here is correct —
this is a dedicated rig machine, not a shared worktree.

## G1 — demo core, complete pass

Load `MMConfig_demo.cfg` in Micro-Manager and start its ZMQ server. Nothing else
should be running against the rig.

```powershell
$DemoOut = Join-Path $Evidence "demo-profile.yaml"
$DemoCfg = "<absolute MMConfig_demo.cfg path>"
microclaw --port $Port first-launch-setup --out $DemoOut --mm-config $DemoCfg --evidence-out (Join-Path $Evidence "demo-inventory")
```

Collect the single `$Evidence\demo-inventory\first-launch-transcript-*.txt`. It must contain
the identity header, inventory path/hash, and complete interview.

Answer every question honestly for the demo config. Where the demo hardware has
no real hazard, still enter values you would defend — this run is also a
usability observation, so note anywhere the question was unclear.

Record:

- Whether the contact warning appeared **before** the acknowledgement prompt and
  before any connection message.
- Every question asked, in order, and whether any of them offered a default.
- The disconnect message, and that it appeared before the first interview
  question.
- The generated `demo-profile.yaml` in full.

Then the validator and the review/restart cycle:

```powershell
microclaw check-config $DemoOut > "$Evidence\g1-check-config-unreviewed.txt" 2>&1
$LASTEXITCODE >> "$Evidence\g1-check-config-unreviewed.txt"
```

This must report `REVIEW REQUIRED` and exit non-zero — the generated file is
unreviewed by construction. Now read every line of the profile, edit
`reviewed: false` to `reviewed: true` **by hand**, and re-check:

```powershell
Copy-Item $DemoOut (Join-Path $Evidence "demo-profile.reviewed.yaml")
# edit reviewed: true in demo-profile.reviewed.yaml, then:
microclaw check-config (Join-Path $Evidence "demo-profile.reviewed.yaml") > "$Evidence\g1-check-config-reviewed.txt" 2>&1
$LASTEXITCODE >> "$Evidence\g1-check-config-reviewed.txt"
```

Expected: offline checks pass, exit `0`. Then start a normal session under it:

```powershell
microclaw --port $Port --safety-config (Join-Path $Evidence "demo-profile.reviewed.yaml")
```

The session must reach its prompt. Ask it one read-only question, then exit.
Keep this successful interactive session attached to the console. If it instead
dies during config validation, rerun it non-interactively to capture the error:

```powershell
microclaw --port $Port --safety-config (Join-Path $Evidence "demo-profile.reviewed.yaml") > "$Evidence\g1-session-start-error.txt" 2>&1
```

This captures the failure without hiding a prompt that needs an answer.
**If live startup refuses a config that `check-config` passed, that is a
finding and the gate stops** — that mismatch is exactly what block 3 exists to
prevent.

## G2 — the unresolved choice cannot be silently accepted

**A human types every input in this step.** Do not pipe a file, do not use a
here-string, and do not script the answers. The evidence is Microclaw's flushed
interview transcript.

```powershell
microclaw --port $Port first-launch-setup --out (Join-Path $Evidence "g2-should-not-exist.yaml") --inventory (Join-Path $Evidence "demo-inventory\inventory.json") --evidence-out (Join-Path $Evidence "g2-evidence")
```

Using `--inventory` here is deliberate: it replays the demo inventory with no
connection, so the refusal behaviour can be probed repeatedly without touching
hardware.

At the proposal, first verify that pressing Enter accepts the visibly shown
bulk default and that the next prompt lets you revisit an ordinary entry by its
exact displayed name. At a revisited property, verify that Enter accepts its
displayed classification and, for a numeric property, its displayed technical
bounds.

Then, at the first **hazard decision with no default**, attempt each of these in
turn and record the exact response to each:

1. Press Enter with nothing typed.
2. Type a single space.
3. Type a token that is not on the offered list (e.g. `yes`).
4. Type an uppercase or mixed-case form of a valid choice.

At the first required **number with no MM-derived default** (a budget or native
full scale), attempt each and record the response:

5. Press Enter with nothing typed.
6. Type `0`.
7. Type `-1`.
8. Type `abc`.
9. Type `inf`.

At a bounds pair, enter a minimum **greater than** the maximum and record the
response.

Then abandon the interview with Ctrl-C, and record both the refusal text and
that no file exists at `--out`:

```powershell
Test-Path (Join-Path $Evidence "g2-should-not-exist.yaml") > "$Evidence\g2-output-exists.txt" 2>&1
```

`Test-Path` must print `False`. Every attempt above must be refused in Phase 5's
own wording and must re-ask; **none may be accepted, and none may fall through
to a default where none is displayed. Collect
`$Evidence\g2-evidence\first-launch-transcript-*.txt`; it must remain readable
after Ctrl-C and contain every attempted answer and re-prompt.

## G3 — the acknowledgement gate exits before connecting

```powershell
microclaw --port $Port first-launch-setup --out (Join-Path $Evidence "g3-should-not-exist.yaml") --mm-config $DemoCfg --evidence-out (Join-Path $Evidence "g3-evidence")
```

At the acknowledgement prompt, type something that is not the exact phrase — a
lowercase version of it, or `yes`. Record that setup refuses, that **no
connection message was printed**, and that no file was created:

```powershell
Test-Path (Join-Path $Evidence "g3-should-not-exist.yaml") > "$Evidence\g3-output-exists.txt" 2>&1
```

Collect `$Evidence\g3-evidence\first-launch-transcript-*.txt`; it must contain the
intro, acknowledgement prompt and answer, and final setup refusal even though
no inventory was produced.

Run it once more and type the exact phrase, to confirm the accepting path still
works; use a distinct evidence directory so the refusal transcript is not
overwritten, then abandon that run at the first question with Ctrl-C:

```powershell
microclaw --port $Port first-launch-setup --out (Join-Path $Evidence "g3-accept-should-not-exist.yaml") --mm-config $DemoCfg --evidence-out (Join-Path $Evidence "g3-accept-evidence")
```

## G4 — M5, a real rig with real hazards

**Qualified M5 operator present. Containment and emergency stop established.
Treat the run as emissive from the acknowledgement onward.** Load the real M5
configuration in Micro-Manager as normal.

Before typing the acknowledgement, read the contact warning aloud and confirm it
is accurate for this rig — it claims initialization may already have occurred
when the configuration was loaded, and that emission before any config exists is
possible on an undeclared path. **Record whether the M5 devices were in fact
already initialized by the configuration load**, and capture the Micro-Manager
log for the setup window so the unavoidable-initialization list in design/33 can
be replaced with measurement rather than inference.

Take the read-only reference copy of the existing hand-authored config first.
This is the only step that reads `$Deployed`, and it never writes to it:

```powershell
$Deployed = "<absolute deployed M5 safety YAML — the hand-authored file M5 runs under today>"
Copy-Item $Deployed (Join-Path $Evidence "deployed-m5.reference.yaml")
Get-FileHash $Deployed -Algorithm SHA256 > "$Evidence\deployed-m5.sha256.txt"
```

Re-hash `$Deployed` at the end of G4 and confirm it is unchanged — the gate must
leave the config M5 actually runs under byte-identical.

```powershell
$M5Out   = Join-Path $Evidence "m5-profile.yaml"
$M5Cfg   = "<absolute loaded M5 .cfg path>"
microclaw --port $Port first-launch-setup --out $M5Out --mm-config $M5Cfg --evidence-out (Join-Path $Evidence "m5-inventory")
```

Collect `$Evidence\m5-inventory\first-launch-transcript-*.txt` as the complete M5
interview record.

Answer as the rig's actual reviewer. Specifically record:

- Every illumination candidate surfaced and how it was classified. The five EMU
  semantic laser enables must each appear and must each be classified
  explicitly.
- Whether `TTL.State0` was recommended for exclusion and that confirmation was
  still required rather than applied automatically.
- Whether `iChrome-MLE-TCP.Label` / `State` was surfaced, and what was decided.
- Every deferral or exclusion emitted, verbatim.
- Any `ENUMERATION REVIEW NOTE:` lines in the generated header — these are the
  observational read failures setup continued past. M5 is expected to produce
  some; capture them and confirm none of them is a classification coordinate.
- Whether setup refused outright on an unenumerable classification coordinate.
  If it did, capture the full refusal and the inventory evidence and **stop** —
  that is the honest fail-closed path, and the coordinator decides what happens
  next.

Then validate and compare:

```powershell
microclaw check-config $M5Out > "$Evidence\g4-check-config.txt" 2>&1
$LASTEXITCODE >> "$Evidence\g4-check-config.txt"
```

Now the comparison the checklist asks for. This is a **review**, not a diff to
be minimised:

```powershell
git diff --no-index (Join-Path $Evidence "deployed-m5.reference.yaml") $M5Out > "$Evidence\g4-vs-deployed.diff" 2>&1
```

For **every** difference, write one line saying which side is right and why.
Differences are findings in one direction or the other — a generated value that
is more conservative than the deployed one is a finding about the deployed
config, and a deployed value setup could not reproduce is a finding about setup.
Pay particular attention to the acquisition budgets: the deployed M5 config is
already known to carry limits copied from the fictional example, which is
block 5's work, so expect differences there and do not treat them as setup
defects.

Do **not** deploy the generated profile. Block 5 owns the deployed config.

## G5 — no file survives a refusal

Already covered in G2 and G3 for the interview and acknowledgement paths. One
more, for the validator path: take the generated demo profile, hand-edit the
copy to something the strict schema rejects, and confirm `check-config` reports
it. Then confirm that a setup run that ends in refusal leaves `--out` absent —
G2's `Test-Path` result is that evidence.

Record the answer to one question explicitly: **did any setup run leave a file
at `--out` that was not a validator-accepted profile?** The expected answer is
no.

## What to return

**Two** archives — one `$Evidence` directory per machine, demo and M5 — plus a
short written summary answering:

1. Did the demo pass complete, validate, and start a session? (G1)
2. Which of the nine G2 attempts were refused, and was any accepted? (G2)
3. Did the acknowledgement gate exit before connecting? (G3)
4. What did the M5 run classify, defer, and refuse — and what did the comparison
   against the deployed config show, difference by difference? (G4)
5. Were the M5 devices already initialized by the configuration load, and what
   does the Micro-Manager log show initialized during the setup window?
6. Did any refusal leave a file behind? (G5)
6b. Is `$Deployed` byte-identical to its pre-gate hash? (G4)
7. Anything in the interview that was unclear, tedious, or that you would have
   answered wrongly without knowing the rig — this block exists to make an
   operator able to author a config, so usability observations are evidence.

Do not judge whether the evidence is sufficient; return it and the coordinator
will. A step you could not complete is a defect in this gate document, not
operator error.
