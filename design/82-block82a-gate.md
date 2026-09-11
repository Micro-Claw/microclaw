# Block 82a gate — does a session record what it cost, and does the 1h TTL hold?

**Demo machine. About 30 minutes, of which ~20 is you driving a session.
DemoCamera, so no dose that matters.** Firefox is this machine's browser.

**This one spends your API key.** That is the point of it — design/82 exists
because five sessions cost $102.35 and nothing recorded where it went. 82a adds
`*_microclaw_usage.jsonl` (D1) and moves all four cache breakpoints to the
one-hour TTL (D5). A short session cannot show the thing that matters, so this
drives one long enough to compact.

## Round 1 ran on 2026-09-11 — read this before re-running anything

It cost **$9.58 over 63 calls in 9.9 minutes** (the session says so itself now,
which is F0 closed) and it **passed 11/11 after one correction to the gate**.
Nothing below is outstanding work; it is kept so the run is repeatable.

Three of round 1's problems were this document's, not the product's:

* The limb `a compaction invalidates the prefix` asserted `cache_read == 0` and
  **failed on correct behaviour**. `tools` and `system` carry their own
  breakpoints ahead of the messages, so a compaction rewrites the message part
  and the ~50k static head survives as a read — measured, read 50,377 against a
  rewrite of 133,774. design/82 F6 says the prefix is "invalid from byte one";
  the rig says otherwise, and the limb now checks the real mechanism.
* The progress meter in step 3b was the history **file size**, which grows
  forever and is not what compacts. Fixed below.
* The filler drifted onto `inspect_artifacts` (23 calls returning almost
  nothing), so the session reached one compaction rather than two and the
  operator, reasonably, stopped.

One compaction is n=1 and is recorded as such. A second round was **not**
requested: what it would add is repeatability of a limb that now passes and a
mechanism that is now understood.

## What is already settled, so you do not pay for it again

* **The API accepts our four breakpoints at `ttl: "1h"`, with no beta header.**
  Measured live off-rig on 2026-09-10 against `claude-opus-4-8`:
  `design/82-block82a-ttl-probe.py` wrote 49,901 tokens with
  `ephemeral_1h_input_tokens=49901` and `ephemeral_5m_input_tokens=0`, and read
  all 49,901 back on the second call. **Do not run that probe here** — it is not
  machine-dependent and it would cost another $0.50 to learn nothing.
* **The record's shape, both wiring sites, the retry accounting, and the
  failure isolation** are covered by unit tests that were each watched to fail
  under an independent mutation.
* **Microclaw's fixed prefix is 49,901 tokens** — tools + system prompt + a
  stub knowledge base, by Claude's own tokenizer, 2.75 chars/token. The context
  guard meters the *history* only, so every call carries about 50k more than the
  high-water mark says.

What is **not** settled off-rig, and is the reason for the trip: whether a real
session's records agree with the real API's cache behaviour — one record per
call, and what a compaction actually does to the prefix.

Note what this gate **cannot** show, whatever it prints: a session with no idle
gap over five minutes would have kept a 5-minute entry warm too, so its
"no unexplained miss" limb discriminates nothing. The scorer says so in its own
`could this session tell a 1h TTL from a 5m one` line. What proves D5 shipped is
the 1h/5m split on the writes.

## 0 — pin the tree

```powershell
cd D:\Code\microclaw
git fetch origin
git checkout design82/82a-instrument
git pull
git merge-base --is-ancestor d2d8217 HEAD
if ($LASTEXITCODE -eq 0) { "PIN OK - the implementation is in this tree" }
else { "STOP - wrong tree, do not run the gate" }
```

Warm uv once, unredirected. A freshly checked-out branch leaves uv a rebuild and
it writes `Building microclaw @ file:///...` to stderr; red text here is expected
and harmless because nothing is redirected.

```powershell
uv run python -c "print('uv warm')"
```

Check the instrument before spending any money on the engine:

```powershell
uv run python -m pytest -q design\82-block82a-gate-selftest.py
```

Expect `13 passed`. Every graded limb of the scorer is exercised there against a
session produced by the real `run_agent_iter`, real `ConversationStore` and real
`AuditLog`, **and** against a mutant that breaks exactly that limb — so a green
selftest means the limbs discriminate. If it fails, stop and send me the output;
do not start the session.

## 1 — Micro-Manager

Start Micro-Manager with the **demo configuration** and the ZMQ server ("Run
server on port 4827" in Tools → Options). Nothing else: no safety-config edits,
no sample, no real stage.

## 2 — start the session

Leave this window open and **unredirected** for the whole run. `microclaw serve`
prints nothing under redirection until it dies (a standing register row from
block 4d), and its per-turn `seq` lines are useful here.

```powershell
cd D:\Code\microclaw
uv run microclaw serve
```

Open **Firefox** at `http://127.0.0.1:8000`. The session's files —
`*_microclaw_history.jsonl`, `*_microclaw_usage.jsonl` and the rest — appear in
`D:\Code\microclaw`.

## 3 — drive it until it compacts

The model context has to cross 120,000 estimated tokens for the guard to
compact, and the cheapest honest way there is design/82's own finding F2: `read_hook_log`
returns every entry, 44–45k chars at a time. So: make one log, then read it.

**Turn 1** — paste this as your first message:

> Run a 200-frame timelapse on the demo camera at 5 ms exposure with the
> snr_observer hook, saving into a folder called block82a-frames, and tell me
> the hook log path when it finishes.

**Turns 2 onward** — paste this, with the log path it just gave you, and repeat
it as many times as step 3b says:

> Read the hook log at <path> and tell me whether the SNR drifted over the run.

### 3b — the progress meter is the instrument itself

In a **second** PowerShell window, after each turn:

```powershell
cd D:\Code\microclaw
Get-Content (Get-ChildItem *_microclaw_usage.jsonl | Select-Object -Last 1) |
  Select-Object -Last 1 | ConvertFrom-Json |
  Format-List estimated_tokens, compaction_count
```

**`estimated_tokens` is the only number that matters, and it is not the file
size.** The history file grows forever — it is the full audit — while the model
context is what compacts, and after a compaction it *drops*. The first round of
this gate watched the file pass 500 KB and never understood why nothing
happened.

A compaction fires when `estimated_tokens` crosses **120,000**. So:

* drive until `estimated_tokens` is over 120,000 — `compaction_count` becomes 1
  and `estimated_tokens` falls back to roughly 78,000;
* then keep going until it climbs past 120,000 **again** — that is the second
  compaction, and it needs about another 42,000 tokens of history, roughly
  170 KB;
* then **two more turns**, so the post-compaction limbs have something to read.

If `estimated_tokens` is not moving by a few thousand per turn, the filler has
stopped working — the first round drifted onto `inspect_artifacts`, which
returns almost nothing, and spent 46 calls adding 7,000 tokens. Switch to the
fallback below rather than pushing on; it moves ~15,000 tokens a turn and cannot
drift.

One compaction is worth having if you run out of patience: say so and send what
you have. The second only buys repeatability.

If a turn takes more than about 3 minutes, or the reply says it will not read the
log again, say so in your own words in the next prompt — an ordinary
conversational nudge is fine and does not spoil anything.

**If turn 1 fails, or `estimated_tokens` stalls**, do not debug it — that is not
what this gate is for. Use this filler instead, which needs nothing but the repo,
moves ~15,000 tokens a turn, and cannot wander off onto a small tool:

```powershell
Get-Content D:\Code\microclaw\CLAUDE.md -Raw | Set-Clipboard
```

then paste into the prompt box, on its own after this line:

> Summarise this document in one sentence, then wait for the next one.

Tell me you used the fallback, and why.

## 4 — score it

Stop nothing and close nothing yet. In the second window:

```powershell
cd D:\Code\microclaw
New-Item -ItemType Directory -Force block82a-evidence | Out-Null
uv run python design\82-block82a-gate.py --session . > block82a-evidence\gate.log 2>&1
"exit: $LASTEXITCODE"
Get-Content block82a-evidence\gate.log
```

It prints one line per limb, reports each **independently** — one FAIL never
hides the limbs after it — and **exits nonzero on any FAIL or any NOT
EXERCISED**. Expect `exit: 0` and `11/11 graded limbs passed`.

`Get-Content` may show the log as spaced-out characters: PowerShell's `>` writes
UTF-16. The file is fine and I can read it; if you want it legible on screen, run
the command again without the redirect.

Four lines say `REPORT` rather than PASS or FAIL, deliberately: the output-token
rate, how far the store's estimate is from the real history, the largest context
the session paid for, and whether the session could tell the two TTLs apart.
Those are
measurements D1 exists to make, not criteria — design/79's lesson is that naming
the winner turns attribution into label-reading. Send the numbers; they are what
82b's D4 will be judged against.

## 5 — one thing only you can check

Look at the browser while the session is open, and at the transcript after:
**is there any token count, cost, or usage figure anywhere in the UI?** There
must not be. design/82 rules out showing the model or the transcript what a
session has spent, and the scorer can only check the history file, not the page.

## 6 — send back

```powershell
Copy-Item (Get-ChildItem *_microclaw_usage.jsonl | Select-Object -Last 1) block82a-evidence\
Copy-Item (Get-ChildItem *_microclaw_history.jsonl | Select-Object -Last 1) block82a-evidence\
Compress-Archive -Path block82a-evidence\* -DestinationPath block82a-evidence.zip -Force
"wrote block82a-evidence.zip"
```

Send `block82a-evidence.zip`, the `exit:` line, and your answer to step 5. Also
tell me roughly how many turns it took and what the console said if any turn
looked wrong — a passing gate is a place to look for defects, not a reason to
stop looking.
