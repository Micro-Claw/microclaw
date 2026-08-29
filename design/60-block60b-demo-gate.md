# Block 60b demo-machine gate — progress, disclosure, and the bounded grant

Two parts: a program you run (about 10 minutes, mostly the burst) and one short
driven session (about 5 minutes). Run them in order, on the **demo machine**.

The program covers everything that only computes. The driven session covers the
half a program cannot: whether the operator actually *sees* the disclosure and
whether the browser actually re-asks. Do not paste the program's steps by hand —
that is what block 58a did, and a `throw` in an interactive paste ends the
pipeline, not the session, so five failed limbs reported PASSED.

## Before you start

Check out this branch and confirm the tree carries the implementation:

    cd C:\Users\<you>\Code\microclaw
    git fetch origin
    git checkout design60/progress-and-disclosure
    git pull
    git merge-base --is-ancestor f1b184b HEAD
    if ($LASTEXITCODE -eq 0) { "IMPLEMENTATION PRESENT" } else { "WRONG TREE - STOP" }

Micro-Manager must be running with the demo config and the ZMQ server enabled,
as for the 60a gate. Nothing else needs to be set up. The gate writes to
`<workspace_dir>/block60b-gate` if your safety config has a `workspace_dir`, and
to `.\block60b-gate` under the current directory if it does not — **you do not
need to configure one**, and if you added one for round 1 you can take it back
out. Pass `--save-root <path>` to put the data somewhere else. It checks free
disk itself and needs about 6 GB.

## Part 1 — the program

    cd C:\Users\<you>\Code\microclaw
    uv run python design\60-block60b-demo-gate.py > block60b-console.txt 2>&1
    if ($LASTEXITCODE -eq 0) { "GATE PASSED" } else { "GATE FAILED - exit $LASTEXITCODE" }

It writes `block60b-demo-evidence\` (`gate.txt`, `results.json`, `run.json`, and
`setup-error.txt` or `burst-error.txt` if something went wrong). The gate owns
its own log; do not wrap it in `Start-Transcript`, which does not capture a
native child process's stdout.

It will write about 5 GB and then leave it. Delete the dataset afterwards; the
path is in `results.json` under `measured.dataset_path`.

Eight limbs, each reporting independently:

1. the tree under test carries block 60b
2. this machine's crossing bound is computed from its own geometry
3. the D6 crossing disclosure fires just over the bound and not just under
4. the D5 burst clause fires for a multi-frame burst and not a single frame
5. a session grant does not carry a larger plan
6. the burst actually crossed 4 GiB and NDTiff rolled to a second file
7. progress events arrived during the burst, rate-limited
8. the production safety document is untouched

**A `NOT EXERCISED` is never a pass.** If limb 6 or 7 says the disk was too
small, that is a fact about the disk and the gate has not been run — free some
space and run it again.

**Limb 6 is the one the rig is for.** It is the only thing here no fake can
answer: whether NDTiff's rollover past 4 GiB is clean on a camera that is not
M2's. If it fails, or if the run hangs the way the M2 dSTORM run did, stop and
send everything — that is a much larger finding than this block.

## Part 2 — the driven session

Start the browser session as usual:

    cd C:\Users\<you>\Code\microclaw
    uv run microclaw serve

Ask the agent these two things, **verbatim**. Answer any confirmation the way
the step says, and copy the confirmation text you were shown into your notes.

**Prompt A** — paste exactly:

> Run a timelapse of 200 frames with interval_s of 0 and 10 ms exposure into the
> demo workspace. When the confirmation appears, read the confirmation text back
> to me word for word before I answer it.

Expected: the confirmation names **"one hardware-sequenced burst of 200 frames"**
and says the **Stop button** and **engine abort** cannot be relied on to stop it
promptly. **Approve it for the session** (the "approve for this session" choice,
not the one-time yes). Note whether the agent read the clause back correctly.

**Prompt B** — paste exactly, in the same session, after A has finished:

> Now run a timelapse of 100000 frames with interval_s of 0 and 10 ms exposure
> into the demo workspace. Do not answer any confirmation yourself; tell me
> whether Microclaw asked again or ran without asking.

Expected: Microclaw **asks again**, even though you granted the session in
prompt A, because 100,000 frames exceeds the 200-frame plan the grant was given
for. **Decline it** — do not run a 100,000-frame burst on the demo machine. The
question this step answers is only whether it re-asked.

Record for prompt B: did it re-ask (yes/no), and did the confirmation carry the
4 GiB crossing sentence naming a frame number?

Watch the pending line in the browser while prompt A's 200-frame run is going
and note whether it shows `frames N / 200` counting up.

## Round 2 note (2026-08-29)

Round 1's program failed two limbs and **both were defects in the gate, not the
product**: it demanded a `workspace_dir` the product does not require, and it
looked for `NDTiffStack*.tif` when NDTiff writes `<name>_NDTiffStack*.tif`, so
limb 6 reported FAIL on a crossing that was actually clean. Both are fixed.

**Limb 6 is already answered** from round 1's `NDTiff.index`: all 8,256 frames
present, 8,114 in the first file and 142 in the second, largest file 5.1 MB
under the 4 GiB limit, rollover at frame 8,114 against a disclosed bound of
8,192. You do not need to prove that again — but the program re-runs it anyway
as part of a clean pass, and it is only ~2 minutes of burst.

**Part 2 was not run in round 1 and is the part still owed.**

## What to send back

- `block60b-demo-evidence\` (the whole folder) and `block60b-console.txt`
- your notes from prompts A and B, including the confirmation text you were shown
- whether the browser showed a counting `frames N / 200` line

Do **not** score the gate yourself beyond reading the PASSED/FAILED line — a
passing gate is a place to look for defects, not a reason to stop looking, and
the numbers get compared against each other back in the repo.

## A note on this runbook

The two prompts above were **not** dry-run against a recorded payload. design/60
asks for that and then says to weigh it against the operator's time; 59b built a
replay harness to avoid asking for one three-minute session and it cost more
than the run it replaced. Two prompts, one short session, and the mechanisms
they exercise — the grant lookup and the clause text — are already covered
programmatically by limbs 4 and 5. What the session adds is whether a human sees
it, which is why they name the mechanism (`interval_s`, frame counts, "approve
for this session") rather than an outcome a better route could satisfy.
