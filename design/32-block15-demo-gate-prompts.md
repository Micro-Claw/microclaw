# Block 15 — MMDemo gate prompts (design/32 Finding 5)

Branch under test: `design32/context-audit-store` (`dacab18`).
Baseline: 1159 passed / 99 skipped off-rig.

Block 15's ledger row says "no rig evidence required" — the block is off-hardware
by design. This gate runs anyway, on the demo core, because four of its claims
are only observable in a live process:

1. the Anthropic API accepts a compacted history (a checkpoint `user` message
   immediately followed by a real `user` prompt — two consecutive same-role
   messages),
2. the JSONL audit is actually written and flushed per message on a real turn,
3. paging, artifact download, and the confirmation audit work through the real
   `serve` app rather than the test fixture's compatibility fallback,
4. an interrupted session leaves a readable transcript.

Every command is PowerShell/cmd-safe. Redirect with `> out.txt 2>&1`; do not use
Unix pipelines. Keep each step's stdout/stderr in one dated directory. Do not
commit output files — fold findings into this file and design/32.

## G1 — a compacted history survives a real API round trip

The one thing no test on either side of the branch can prove. `tests/` mocks the
client, so the checkpoint has never been sent to the real API.

Run `design/32-block15-compaction-live-spike.py` (see below) against the demo
core with a real key. It drives several real `run_agent` turns through a
`ConversationStore` whose water marks are low enough to force compaction mid-run.

PASS requires all of:

- no `400` from the API on any turn after `compaction_count` first goes above 0;
- the assistant still answers coherently on the turn immediately after
  compaction, and its answer reflects the checkpoint (it should still know the
  artifact paths and decisions from the compacted region);
- `store.compaction_count >= 1` and the post-compaction estimate is below the
  pre-compaction estimate;
- the two most recent complete turns are still present verbatim in
  `store.model_messages(history)` — the D1 floor holding on live data.

FAIL and stop if the API rejects the checkpoint shape. That would invalidate the
whole compaction design, not just a parameter.

## G2 — live `serve` writes the durable audit

    microclaw serve --port 4827 > g2-serve.txt 2>&1

Run three or four turns that call tools (`get_system_state`, a snap, a small
timelapse so an artifact is declared). Then, without stopping the server:

- confirm exactly one `*_microclaw_history.jsonl` exists in the launch directory
  and that its line count grows as turns complete — the audit is supposed to
  flush per message, not per session;
- confirm **no** `*_microclaw_history.json` array file was written;
- confirm each line is independently valid JSON.

## G3 — paging over the real store

With the same server still up, walk the cursor with a deliberately small limit
so more than one page is exercised (the browser uses 500, which one demo session
will not exceed):

    Invoke-WebRequest "http://127.0.0.1:4827/api/history?limit=2" -OutFile g3-page1.json
    Invoke-WebRequest "http://127.0.0.1:4827/api/history?limit=2&cursor=2" -OutFile g3-page2.json

PASS requires: `total` identical across pages; `next_cursor` advancing then
going null on the last page; and the concatenated `items` equal to the JSONL
file's records in order. Then reload the browser tab and confirm the transcript
still renders the whole session — `serve.html` loops the cursor itself.

## G4 — artifact download after the declaring turn is old

Click through to download an artifact declared in the **first** turn of the
session, after several later turns. This is the security-relevant path: the
allowlist reads the full durable record, so compaction must not narrow it.
A `403 Not an artifact produced by this session` here is a defect.

## G5 — confirmation audit is durable

Trigger a confirmation gate (ask the agent to save something to the knowledge
base) and answer it in the browser. Confirm a `*_confirmations.jsonl` sits
beside the history file with one record carrying the decision and identity.
Decline a second one and confirm both outcomes are recorded.

## G6 — an interrupted session is still readable

Ctrl-C the server mid-turn, then:

    microclaw view-history <the .jsonl> --no-browser > g6-view.txt 2>&1

PASS: the viewer opens, renders the completed turns, and — if the kill landed
mid-write — prints the incomplete-final-record warning rather than failing.
Then copy the file, truncate the last line by hand, and re-open the copy to
force the warning path deliberately.

Also open one **old** `*_microclaw_history.json` from
`OneDrive/Microclaw/microclaw-json-histories`. Legacy array histories must still
render; that compatibility is the reason the loader sniffs the first character.

## G7 — retention deletes nothing by default

In a scratch directory holding a deliberately old `*_microclaw_history.jsonl`
(set its timestamp back), start a session with no retention flag and confirm the
old file is untouched. Then start one with `--history-retention-days 1` and
confirm it is pruned and the prune is printed. Default-keeps-everything is the
safety property — a default that deletes a scientific record is a stop-ship.

## G8 — clean exit

Confirm the session still shutters known illumination on exit and that no
history write happens in the `finally` block (the audit already flushed). The
demo core has no real lasers; this is a regression check on the ordering, not a
safety measurement.

## Recording the result

For each step: PASS/FAIL, the command, and the evidence that decided it. Hash
the transcripts you retain. Do not upgrade "the file exists" to "the audit is
correct" — say which records you actually read.
