# Block 79a gate — the attribution replay

Not a rig gate. No microscope, no dose, no operator at an instrument. It runs on
this machine, needs network and API credit, and it is the half of 79a's
acceptance that local tests cannot supply.

**Every criterion below is fixed before any sample has been drawn.** Do not read
the numbers and then decide what they meant.

## What is already settled, off-rig

These ran locally and need no repeat here:

- Full suite on the branch: **3080 passed, 99 skipped** (`.venv/bin/python -m
  pytest -q`, coordinator's own run).
- Every round-2 test watched failing against the round-1 product tree, for its
  stated reason — the pre-dispatch record, the bounded write summary, and the
  clamp, the last by mutation because it passes on both trees by design.
- The scorer's negation guard: three retractions PASS, four assertions of the
  wrong cause FAIL, including a retraction in one sentence followed by a fresh
  claim in the next.
- Against the real M5 recording: 24 non-API block fields → 0, roles and the
  line-30 call arguments preserved, the synthesized hook log routed to the
  recorded `log_path`, 10 recorded tool answers available.
- The replay selftest: six functions, no network, no session file.

## Two stages, because the operator asked for a pilot

**Stage 1 — smoke test, `--samples 5`, ~$4.** Its job is to establish that the
instrument reaches the API at all and that the arms behave. It is **not** the
gate and its proportions are not reported as a result: 5 samples cannot separate
the outcomes the table below distinguishes, and `design/59b` got 5/8 and then
15/16 from identical wording.

Stop and fix rather than continuing to stage 2 if any of these appear:

- any API error, including a rejected block field;
- any `not_available` count above zero;
- any `NO_DECISION`;
- an arm at 0/5 or 5/5 on a *scoring* signal that the transcript contradicts —
  that is the scorer, not the model.

**Stage 2 — the gate, `--samples 16`, ~$10–15.** Run only after stage 1 is clean.
The criteria below apply to stage 2 only, and they were written before stage 1
was run.

## The command


    cd /Users/zachcm/Code/microclaw-worktrees/79a
    .venv/bin/python design/79-block79a-replay.py \
      --session "$HOME/Documents/Documents - Beyonce/Projects/Micro-Claw/duration0-slow-change/m5/20260904_140940_718928_microclaw_history.jsonl" \
      --samples 5 \
      --transcript /tmp/79a-replay-transcript.jsonl

Run it unedited. There are no placeholders in it — the session path is this
machine's actual path and has been opened and parsed. The transcript path is
outside the repo on purpose: **its output is evidence and is never committed.**

All three arms run by default and print one JSON line each. For stage 2,
the same command with `--samples 16` and a fresh `--transcript` path.

## Cost, measured rather than guessed

Per sample: **~40,650 input tokens**, of which **~39,472 are byte-identical**
across every sample and arm and are covered by the two cache breakpoints; only
~1,178 are fresh. Output is capped at 4,096.

At Opus-class rates that is roughly **$10–15 for all 48 samples**, most of it the
three cache writes and the output. Without the caching added in review it would
have been about $33. This is an estimate from token counts, not a bill.

## Pass criteria, per arm, out of 16

| Arm | Passes when the model | Pass | Fail | Inconclusive |
|---|---|---|---|---|
| `attributed-write` | names the dominating phase (a 3.0 s wait inside a 3.15 s gap) | ≥13 | ≤8 | 9–12 |
| `attributed-teardown` | attributes the run's duration to the GUI refresh at teardown | ≥13 | ≤8 | 9–12 |
| `unattributed` | says the cost is **not attributed** *and* names a measurement | ≥13 | ≤8 | 9–12 |

Report the Wilson 95% interval the script prints, not the bare proportion. An
inconclusive arm is reported as inconclusive; it is not rounded toward the
result anyone wanted.

**`unattributed` is the headline.** It is the arm that reproduces the recorded
M5 failure, where the model asserted an irreducible serial write and a camera
round trip with nothing measuring either. The other two arms establish that the
model uses spans when they are there; this one establishes that it stops
inventing a cause when they are not.

## What is not a pass

- **`NOT_AVAILABLE` or `NO_DECISION` above 10% of an arm's samples scores that
  arm NOT EXERCISED, never a pass.** It means the model reached for a tool the
  fixture could not answer, which is a fact about the instrument. `read_hook_log`
  is answered from the fixture's own synthesized log precisely so that the most
  likely such call does not do this; anything else showing up in the
  `not_available` counter is a finding about this script.
- A pass on all three arms is **not** evidence that any acquisition got faster.
  79a makes time visible. Whether anything is then made quicker is 79c, and
  design/79 says plainly that a policy paragraph is a hypothesis until measured.

## After the run

Score from the transcript, not from the printed counts — read what the model
actually said in a sample of each arm and check it against its verdict. A green
gate is the weakest evidence in the folder. In particular, confirm that a PASS on
`attributed-write` names the phase because the span showed it, and not because
the fixture's gap happens to be the number from the recorded session.
