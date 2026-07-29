# Block 15 — MMDemo gate runbook (design/32 Finding 5)

Branch under test: `design32/context-audit-store` (`ebd7c9d`).
Baseline: 1159 passed / 99 skipped off-rig.

Block 15's ledger row says no rig evidence is required — the block is
off-hardware by design. This gate runs anyway because four of its claims are
only observable in a live process, and the off-rig suite mocks the Anthropic
client entirely:

1. the API accepts a compacted history (a checkpoint `user` message immediately
   followed by a real `user` prompt — two consecutive same-role messages),
2. the JSONL audit is written and flushed per message on a real turn,
3. paging, artifact download, and the confirmation audit work through the real
   `serve` app rather than the test fixture's compatibility fallback,
4. an interrupted session still leaves a readable transcript.

Run by hand in **PowerShell** on the demo machine. Keep every step's output in
one dated directory. Commit nothing: gate output files are never committed;
findings get folded into this file and design/32 by the coordinator.

Set once per session:

```powershell
cd C:\path\to\microclaw
git fetch origin
git checkout design32/context-audit-store
git log --oneline -1        # expect ebd7c9d
pip install -e .            # a stale editable install is the usual cause of
                            # confusing import/plugin errors here
$CFG = "C:\path\to\your\demo_safety_config.yaml"
$OUT = "gate-block15-$(Get-Date -Format yyyyMMdd)"
New-Item -ItemType Directory -Force $OUT | Out-Null
cd $OUT
```

Start Micro-Manager with the **demo** config and enable the ZMQ server
(Tools → Options) before G1.

**API key.** G1 drives the agent directly, so it needs a key. It resolves
env → keyring → config file, the same order `serve` uses, and prints which
source won. If you have only ever entered the key in the browser it is in the
keyring and will be found. Otherwise `$env:ANTHROPIC_API_KEY = "sk-ant-..."`
for this session.

**Capture output as UTF-8.** Windows PowerShell 5.1 writes `*>` redirects as
UTF-16, which is unreadable when the file is shared. Use
`2>&1 | Out-File -Encoding utf8 <name>.txt` throughout, as the commands below do.

---

## G1 — a compacted history survives a real API round trip

**RESULT: PASS, 2026-07-29** (4th attempt; the first three were defective
prompts and parameters of this spike's, not the code under test). 5 compactions,
4 turns sent to the real API with a checkpoint prefix and
`max_leading_consecutive_user_messages: 2`, no API errors across 8 turns, the
two-complete-turn floor held, and `g1_export.tiff` was still in the durable
allowlist after its declaring turn had been compacted out of the model view
(`artifacts_still_in_model_view: []`). The Messages API accepts a checkpoint
`user` message immediately followed by a real `user` prompt.

**Open observation, not a defect.** Under compaction the model twice answered
"which values came from a tool call this turn?" by naming tools from an earlier
turn; the same prompt on an uncompacted run answered "none of them" correctly.
The first hypothesis — that the checkpoint listed tool names without a temporal
anchor — is **refuted**: on the passing run the window was checkpoint + turn 5 +
turn 6 + prompt, so turn 5's `tool_use` blocks were visible verbatim and the
model still called them current. Adding temporal framing to the checkpoint
changed nothing. Compaction plausibly makes the oldest *visible* turn read as
current. The probe is also ambiguous ("values you reported" points at the prior
turn's summary). Settle it with a sharper probe — "did you make any tool calls
in this turn, yes or no?" — before drawing a conclusion, and record whatever
holds in design/32 as a stated limitation rather than a fix.

**Run this first. If it fails, stop and report — nothing else matters.**

Every compaction test on this branch mocks the client, so the checkpoint has
never been sent to the real API. A 400 here invalidates the compaction design,
not a parameter.

```powershell
python ..\design\32-block15-compaction-live-spike.py --config $CFG `
    --save-dir g1_timelapse 2>&1 | Out-File -Encoding utf8 g1.txt
Get-Content g1.txt -Tail 40
```

The water marks now default to 1500/800, calibrated against a real 7-turn demo
run that peaked at ~3000 estimated tokens and never tripped an earlier 6000
mark. `--save-dir` must be a path the guard will accept — inside the configured
workspace, if the demo safety config sets one.

The first line of `g1.txt` must read `API key: …xxxx (from env|keyring|file)`.
A `TypeError: Could not resolve authentication method` on turn 0 means no key
was found — that is a setup failure, not a gate result. Fix the key and re-run;
nothing about compaction has been tested at that point.

Spends real tokens and drives the demo stage. The water marks are deliberately
far below the shipped 120k/90k so compaction fires within a handful of turns.

PASS requires, from the final JSON block:

- `"verdict": "PASS"`;
- `compaction_count` at least 1, and `turns_sent_with_a_checkpoint_prefix`
  at least 1 — a compacted history actually went over the wire;
- `max_leading_consecutive_user_messages` is 2 — the checkpoint-then-prompt
  shape was really sent, and the API accepted it;
- `final_verbatim_messages_in_model_view` greater than 0 — the floor that keeps
  recent turns verbatim held on live data;
- the per-turn `reply_head` after compaction is still coherent and still refers
  to earlier findings, rather than the model acting as if the session restarted.

Also record `artifacts_compacted_away_but_still_downloadable`. A non-empty list
is the security property proved on real session data: an artifact whose
declaring turn is gone from the model view is still in the durable allowlist
that `/api/artifact` consults. Empty means nothing got compacted away this run —
inconclusive for that property, not a failure.

FAIL: any exception, or a `"verdict": "FAIL"` block naming the API error.

INCONCLUSIVE means the run proved nothing and must be repeated — it is not a
partial pass. The final block carries `next_step` and `artifact_note` saying
what to change. Two ways it happens, both seen on the first live runs:

- **the session never reached the high-water mark**, so no checkpoint was ever
  built. Lower `--high-water` / `--low-water` as `next_step` suggests, or add
  `--prompt` turns;
- **no artifact was declared.** The allowlist property is lost; compaction can
  still be proved without it. Note `run_timelapse` alone never declares an
  artifact — it returns `dataset_path` with no `artifact` block, so the *export*
  turn is what has to succeed. Check its `reply_head` for a guard refusal on the
  output path.

---

## G2–G5 — one live `serve` session

Leave this running in **window A**:

```powershell
microclaw serve --port 4827 --safety-config $CFG 2>&1 | Out-File -Encoding utf8 g2-serve.txt
```

Run three or four turns in the browser that call tools, including one that
writes a dataset (a 2-frame timelapse) so an artifact is declared, and one that
asks to save something to the knowledge base (that triggers the confirmation
gate for G5). Then, **without stopping the server**, in window B:

### G2 — the durable audit is written and flushed per message

```powershell
Get-ChildItem *_microclaw_history.jsonl, *_microclaw_history.json
$H = (Get-ChildItem *_microclaw_history.jsonl | Select-Object -First 1).Name
(Get-Content $H | Measure-Object -Line).Lines
Get-Content $H | ForEach-Object { $_ | ConvertFrom-Json | Out-Null }; "all lines parse"
```

PASS: exactly one `.jsonl` exists; **no** `*_microclaw_history.json` array file
was written; every line parses independently; and the line count grows when you
run another turn and re-check (flushed per message, not per session).

### G3 — paging over the real store

The browser uses `limit=500`, which one demo session will not exceed, so force
more than one page by hand:

```powershell
$p1 = Invoke-RestMethod "http://127.0.0.1:4827/api/history?limit=2"
$p2 = Invoke-RestMethod "http://127.0.0.1:4827/api/history?limit=2&cursor=$($p1.next_cursor)"
$p1 | ConvertTo-Json -Depth 6 > g3-page1.json
$p2 | ConvertTo-Json -Depth 6 > g3-page2.json
"total p1=$($p1.total) p2=$($p2.total)  next1=$($p1.next_cursor) next2=$($p2.next_cursor)"
"items p1=$($p1.items.Count) p2=$($p2.items.Count)  jsonl lines=$((Get-Content $H | Measure-Object -Line).Lines)"
```

PASS: `total` identical across pages and equal to the JSONL line count;
`next_cursor` advances (`2` then `4`) and eventually goes null on the last page;
concatenated `items` match the JSONL records in order. Then reload the browser
tab — the transcript must still render the whole session, since `serve.html`
walks the cursor itself.

Also confirm bad input is refused rather than silently coerced:

```powershell
try { Invoke-RestMethod "http://127.0.0.1:4827/api/history?cursor=-1" } catch { $_.Exception.Response.StatusCode.value__ }
try { Invoke-RestMethod "http://127.0.0.1:4827/api/history?limit=0" }  catch { $_.Exception.Response.StatusCode.value__ }
```

PASS: `400` for both.

### G4 — artifact download from an early turn

In the browser, download the artifact declared in your **first** turn, after
several later turns have run. PASS: the file downloads. A
`403 Not an artifact produced by this session` is a defect — the allowlist is
required to read the full durable record. (The compacted variant of this
property is proved by G1's `artifacts_compacted_away_but_still_downloadable`;
the shipped 120k/90k thresholds are unreachable by hand in one session.)

### G5 — the confirmation audit is durable

Approve one confirmation in the browser and decline a second.

```powershell
Get-ChildItem *_confirmations.jsonl
Get-Content (Get-ChildItem *_confirmations.jsonl | Select-Object -First 1)
```

PASS: one record per decision, each carrying `decision`, `kind`, `identity`, and
a timestamp; the approve and the decline both present and distinguishable.

Then **Ctrl-C the server mid-turn** (start a longer turn and interrupt it) —
that sets up G6.

---

## G6 — an interrupted session is still readable

```powershell
microclaw view-history .\$H --no-browser 2>&1 | Out-File -Encoding utf8 g6-view.txt
Get-Content g6-view.txt
```

PASS: the viewer writes an HTML file and renders the completed turns. If the
interrupt landed mid-write, it prints the incomplete-final-record warning rather
than failing. Force that path deliberately too:

```powershell
Copy-Item $H torn.jsonl
$lines = Get-Content torn.jsonl
$lines[0..($lines.Count-2)] + '{"role":"assistant"' | Set-Content -NoNewline torn.jsonl
microclaw view-history .\torn.jsonl --no-browser 2>&1 | Out-File -Encoding utf8 g6-torn.txt
Get-Content g6-torn.txt
```

PASS: complete records recovered, warning printed on stderr, no exception.

Finally, open one **old** array-format history from
`OneDrive\Microclaw\microclaw-json-histories`:

```powershell
microclaw view-history "C:\path\to\an_old_microclaw_history.json" --no-browser 2>&1 | Out-File -Encoding utf8 g6-legacy.txt
```

PASS: it still renders. Legacy `.json` histories must keep working — that
compatibility is why the loader sniffs the first character.

---

## G7 — retention deletes nothing by default

The safety property. A default that prunes a scientific record is a stop-ship.

```powershell
New-Item -ItemType Directory -Force retention | Out-Null
cd retention
'{"role":"user","content":"old"}' | Set-Content old_microclaw_history.jsonl
(Get-Item old_microclaw_history.jsonl).LastWriteTime = (Get-Date).AddDays(-30)

# default: no flag
microclaw serve --port 4828 --safety-config $CFG 2>&1 | Out-File -Encoding utf8 ..\g7-default.txt
# (Ctrl-C once it is up)
Test-Path old_microclaw_history.jsonl      # expect True

microclaw serve --port 4828 --safety-config $CFG --history-retention-days 1 2>&1 | Out-File -Encoding utf8 ..\g7-prune.txt
# (Ctrl-C once it is up)
Test-Path old_microclaw_history.jsonl      # expect False
Select-String -Path ..\g7-prune.txt -Pattern "Pruned transcript"
cd ..
```

PASS: untouched with no flag; deleted and announced with
`--history-retention-days 1`.

---

## G8 — clean exit

From `g2-serve.txt`: confirm the session still shutters known illumination on
exit. The demo core has no real lasers, so this is a regression check on
ordering, not a safety measurement.

---

## Recording the result

Per step: PASS / FAIL / BLOCKED, the exact command, and the evidence that
decided it. Hash the transcripts you keep:

```powershell
Get-FileHash *.jsonl, *.txt | Format-Table Hash, Path
```

Do not upgrade "the file exists" into "the audit is correct" — say which records
you actually opened and read. If a step cannot run on the demo core, mark it
BLOCKED with the reason rather than passed.
