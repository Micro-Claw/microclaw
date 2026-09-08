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

## Pilot record, and why stage 2 must use fresh samples

**Pilot 1 — 5/arm, ~$4. Found the fixture was answering a different call.**
13 of 15 samples `NOT_AVAILABLE`; the counter was the symptom. The fixture
hard-coded `interval_s=0.01`, `/replay`, `restore='leave'` and values 0,1,2,3,4
against a recorded call asking for 0.05, `D:/SSD/...`, `entry` and
0,100,200,300,400. The model correctly refused a payload contradicting its own
request and never reached the attribution question. Fixed by deriving every
argument from the recording.

**Pilot 2 — 3/arm, ~$2.50. Found the scorer, again.** All nine samples were the
target behaviour and all nine scored FAIL, on vocabulary alone: the scorer wanted
`dominates` and the model wrote `dominated`; it wanted `wait span` and the model
wrote `` `wait` phase `` and `wait mean = 3.004 s`; it wanted the literal
`not attributed` and the model wrote *I have not isolated exactly which*. Two
further scorer defects surfaced in the fix: a `refusal` guard that punished the
pilot's single best answer for the phrase *"what I can and can't attribute
here"*, and a stem `dominat` that cannot match **dominant** — d-o-m-i-n-a-**n**-t.

The scorer's vocabulary is now derived from those 15 real responses, and the
selftest pins the exact forms that defeated the first version.

**So pilot 2's numbers are a development set, not a result.** The scorer was
tuned on them. Stage 2 must draw fresh samples, and its numbers are the only ones
that get reported as the gate.

For the record, pilot 2 rescored under the current scorer:

| Arm | Development set |
|---|---|
| `attributed-write` | 3/3 |
| `attributed-teardown` | **1/3** |
| `unattributed` | 3/3 |

**The teardown result is a product finding, not a scorer one, and it has
deliberately not been tuned away.** The two failing responses never mention the
teardown refresh at all — they report the per-write phases from
`hardware_write_timing_summary` and read straight past `teardown_timing`, while
an 11.87 s refresh accounts for 11.87 s of a 12.07 s run. A span the model does
not read is not "surfaced", which is item 2's actual requirement. Settle whether
to make it discoverable before stage 2, or stage 2 will measure a known gap.

The recorded failure — line 32 of the M5 session, the turn this block exists to
prevent — scores FAIL in all three arms, on `irreducible`, `serial link` and
`camera round-trip`. That control is now in the selftest.

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
