# Block 82a gate — does a session record what it cost, and does the 1h TTL hold?

**Demo machine. About 30 minutes, of which ~20 is you driving a session.
DemoCamera, so no dose that matters.** Firefox is this machine's browser.

**This one spends your API key: about $2–4.** That is the point of it — design/82
exists because five sessions cost $102.35 and nothing recorded where it went.
82a adds `*_microclaw_usage.jsonl` (D1) and moves all four cache breakpoints to
the one-hour TTL (D5). A short session cannot show the thing that matters, so
this drives one long enough to **compact at least twice**.

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

What is **not** settled, and is the whole reason for this trip: whether a real
session's records agree with the real API's cache behaviour — one record per
call, a compaction visibly invalidating the prefix, and **no cache miss the
session start or a compaction cannot explain**. That last limb is D5's payoff:
before 82a, every one of 137 turn boundaries risked a 5-minute expiry, worth
$18–29 of the $102.

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

## 3 — drive it until it compacts twice

The history has to reach roughly 500 KB for the context guard to compact, and
the cheapest honest way there is design/82's own finding F2: `read_hook_log`
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
(Get-ChildItem *_microclaw_history.jsonl | Select-Object -Last 1).Length
Get-Content (Get-ChildItem *_microclaw_usage.jsonl | Select-Object -Last 1) | Select-Object -Last 1
```

The first number is the history size; you are heading for **> 500000**. The
second line is the newest usage record — watch `compaction_count`. **Keep
repeating the turn-2 prompt until `compaction_count` reads 2, then do two more
turns and stop.** Those last two are what the post-compaction limbs read; a
session that stops *at* the compaction has nothing after it to score.

If a turn takes more than about 3 minutes, or the reply says it will not read the
log again, say so in your own words in the next prompt — an ordinary
conversational nudge is fine and does not spoil anything.

**If turn 1 fails** (the timelapse refuses, or no hook log is written), do not
debug it — that is not what this gate is for. Use this filler instead, which
needs nothing but the repo, and repeat it the same way:

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

Two lines say `REPORT` rather than PASS or FAIL, deliberately: the output-token
rate and the store's estimate against the tokens actually billed. Those are
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
