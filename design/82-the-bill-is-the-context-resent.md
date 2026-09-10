# The bill is the context, resent

Status: **PROPOSED**, 2026-09-09. The **total is measured**: the console (UTC)
reports **$89.27 on 2026-09-08 and $17.08 on 2026-09-09**, of which ~$4 that
second day was other use of the key, so the five sessions cost **$102.35**. A
credit balance that went from ~$104 to −$0.47 corroborates it to $2. Nothing
about the *distribution* is measured, because **no microclaw session has ever recorded
its own token usage** — that is finding F0 and it is why this notebook starts
with an instrument.

The histories are archived at
`~/Documents/Documents - Beyonce/Projects/Micro-Claw/nestor-expensive-sessions`.
This document reconstructs where that went, and separates the causes that are
defects from the two that are simply the shape of an agentic session.

Two denominator checks were needed to get to $102.35. The console day is
**UTC**, not the operator's local time, and getting that wrong reassigns the
single most expensive session across the boundary. And $4 of 2026-09-09's
$17.08 was other use of the key — design/79 block 79b's replay pilot ran that
day on `claude-opus-4-8` at $2.85 plus samples at $0.114 each, which is most of
it. Both confirmed by the operator, 2026-09-09.

**There is no single expensive operation.** The cost is `API calls × context per
call`, and both were large: **556 calls carrying 120k–208k tokens each.** Every
lever below is a lever on one of those two numbers.

## What the instrument is, and what it cannot say

Read this before quoting any number below.

A session writes `*_microclaw_history.jsonl` (messages), `_acquisitions.jsonl`
and `_confirmations.jsonl`. **None of them records `response.usage`.** The
console gives a per-day total and nothing finer: it cannot say which session,
which turn, or which tool result spent it, it arrives a day late, and — as the
reconciliation shows — it does not even reliably say which *day*. Every line
item below was reconstructed off-line:

- Every table below is produced by `design/82-session-cost-reconstruction.py`,
  which carries the literal invocation used here. It replays each history
  through the real `ConversationStore.model_messages` (`conversation.py`), so
  compaction behaviour is the code's, not an assumption, and each assistant
  message is treated as one API call.
- Tokens counted with `cl100k_base` as a **proxy** tokenizer. It is not Claude's.
  Expect ±10–15% on every dollar figure, and treat *ratios* between line items
  as much sounder than the absolute total.
- Priced at Opus 4.8 — `agent.py`'s `DEFAULT_MODEL` — $5.00/$25.00 per MTok,
  cache read $0.50, 5-minute cache write $6.25. **The model actually used is not
  recorded either** (F0 again).
- The rig knowledge base is prepended to `system` on every call
  (`_system_blocks`) and is **not** in these figures; `get_knowledge` returned
  ~30k chars that session, so add roughly 10k tokens per call to everything
  below.

So: the shape is evidence, the total is a model. Fixing F0 first is not
ceremony — it is the only way any later claim here becomes checkable.

## Evidence

| session (local start) | calls | user turns | compactions | avg context | max context | modelled |
|---|---|---|---|---|---|---|
| 19:27 | 43 | 10 | 0 | 92k | 169k | $3.26 |
| 22:05 (abandoned) | 3 | 1 | 0 | 32k | 37k | $0.29 |
| **22:07 → 01:37** | **404** | **103** | **17** | **161k** | **208k** | **$56.63** |
| 01:37 (abandoned) | 23 | 8 | 0 | 39k | 49k | $0.88 |
| 01:50 | 83 | 15 | 2 | 139k | 195k | $9.78 |
| | 556 | 137 | 19 | | | **$70.84** |

Split by kind: cache read $38.61 (54%), cache write $25.45 (36%), output $6.79
(10%). One 3½-hour session is 80% of the bill.

**$70.84 is a floor, and the sessions cost $102.35.** The floor assumes the
prompt cache was warm on every call after the first. Within a turn it was: the
acquisition timestamps put successive calls 30–50 s apart, well inside the
5-minute TTL. The exposed points are the **137 turn boundaries**, where a human
is reading a reply and typing the next one. One call at a 160k prefix costs
**$0.08 warm and $1.01 cold**, a 12.5× step, and each cold boundary adds ~$0.92.

### Reconciliation — the residual is the cache

In UTC the sessions split cleanly across the console's day boundary: sessions
1–4 ran 19:36→23:46 on Sept 8, and session 5 ran 23:59→01:26, so all but its
first minute is Sept 9. That gives **two independent samples of the same
mechanism**, each with its own reported total. Only the tokenizer scale is left
free — the floors below are the reconstruction multiplied by it:

| tokenizer scale | Sept 8 floor / residual / cold | Sept 9 floor / residual / cold | cold-cache term |
|---|---|---|---|
| **1.00 (proxy as-is)** | $63.43 / $25.84 / **25%** | $10.19 / $2.88 / **24%** | $28.73 — 28% of the bill |
| 1.05 | $66.60 / $22.67 / 21% | $10.70 / $2.38 / 19% | $25.05 — 24% |
| 1.10 | $69.77 / $19.50 / 17% | $11.21 / $1.87 / 14% | $21.37 — 21% |
| 1.15 | $72.94 / $16.33 / 14% | $11.72 / $1.36 / 10% | $17.69 — 17% |

**The two days agree at every scale** — 25/24, 21/19, 17/14 — which is the check
that matters: it says the mechanism is the same in a 404-call session and an
83-call one, and that the reconstruction's *structure* is sound. It deliberately
does **not** discriminate the scale, because both days move together with it.
That one number is what D1 measures.

So: **$18–29 of the $102.35, between a sixth and a quarter, was paid for cache
entries that expired while the operator was reading a reply** — 20 to 31 of the
137 turn boundaries. Larger than F1's duplicated payload, and fixed by changing
one dict.

Two bounds worth stating rather than leaving implicit. The residual cannot be
zero: **five idle gaps over 300 s were measured directly** between successive
acquisition calls in session 3, so a scale above ~1.33 is arithmetically
excluded. And it is still a residual, so it absorbs anything the reconstruction
omits — including the possibility that a session ran on a model other than
`claude-opus-4-8`, which is not recorded anywhere (F0).

### What the money was re-sending

Additive, warm-cache, as a share of the $70.84 floor:

| | | $ | % |
|---|---|---|---|
| 1 | `run_analysis_on_saved_dataset` results | 16.84 | 26% |
| 2 | The 81 tool schemas | 9.32 | 15% |
| 3 | Compaction checkpoint blocks | 7.84 | 12% |
| 4 | Output tokens generated (271k) | 6.79 | 11% |
| 5 | `read_hook_log` results | 6.26 | 10% |

Tail: assistant prose 5.5%, system prompt 4.3%, `generate_and_save_hook`
arguments 2.0%, `snap_and_analyze` 1.9%, `run_timelapse` 1.4%.

That table sums to the $70.84 floor. Two **multipliers** sit on top of it rather
than in it, and they are why the same bytes cost 12.5× more on some calls than on
others:

- **Compaction invalidation: $17.02**, inside the floor. A compaction replaces
  the head of the context with a fresh checkpoint, which destroys the cache
  prefix. 24 full invalidations (19 compactions + 5 session starts), average
  123k tokens, each billed at 6.25× instead of 0.5×.
- **Cold turn boundaries: $18–29**, the residual above, and *not* in the floor.

Put together: **$35–46 of the $102.35 — between a third and a half — is the
cache being rebuilt**: $17 because compaction moves the prefix, $18–29 because
the prefix expired. The bytes were
not the problem so much as how often they had to be paid for at write price.

## Findings

### F0 — A session cannot say what it cost

`_stream_one_round` returns the SDK `Message`, whose `.usage` carries
`input_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens` and
`output_tokens`. It is discarded. Nothing downstream records it, so this whole
document is a reconstruction of something the process had in hand and dropped.

This is the same shape as design/79's `duration_breakdown`: a cost nobody can
see is a cost nobody attributes. And **a zero `cache_read_input_tokens` is the
only direct evidence of the cache misses that swing this bill by $57** — no
amount of off-line replay can recover it.

### F1 — The analysis manifest carries its own payload three times

`completed_dataset.py:492` builds `scientific_payload` **in order to hash it**
(`scientific_payload_sha256`), then returns both it and `observations` inside the
manifest; `tools.py:5957` hands that manifest straight back as the tool result.
Measured on the largest one in the session (message 68, 120,279 chars →
**47,336 tokens**, one tool result):

| in one result | chars |
|---|---|
| `observations` — 81 tiles | 51,063 |
| `scientific_payload.observations` — the same 81 tiles less `observed_at` | 46,932 |
| `parameters.metrics` — the same per-tile metrics, echoed from the call | 9,773 |
| `selected_coordinates` = `selection.coordinates` = `scientific_payload.selection.coordinates` | 3,402 × 3 |

`selected_coordinates == selection["coordinates"]` and
`selection == scientific_payload["selection"]` were both verified `True`. Around
**60% of the single largest item on the bill is literal duplication**, and the
hash — not the payload — is what the model needs. 779 floats in that result carry
≥6 decimal places (`contrast: 1.8136708860759494`), another 10,764 chars.

Six such results (95k–120k chars) landed early in the session and were carried
until compaction evicted them.

### F2 — `read_hook_log` returns every entry, unconditionally

`tools.py:10185` reads the log and returns `entries` whole: 11 calls, 296,058
chars, three of them 44–45k. `rank_hook_log` already exists for the question the
model was actually asking ("which fields are worth returning to") and is
deliberately offline and deterministic. Distinct from `R98`, which is about
*how many* log paths a grid produces, not how big one answer is.

### F3 — The same dataset was re-analysed 34 extra times

45 `run_analysis_on_saved_dataset` calls over **11 distinct datasets**; three
were analysed 12, 12 and 10 times (identical `dataset_sha256`). Each re-run
re-sends the full table.

This one is **not obviously a defect**: re-running a *different* adapter over the
same pixels is legitimate and cheap in dose, which is the tool's whole point.
What is missing is that nothing in the result says "you have seen this dataset
before, here is what changed" — so the model re-reads a full table to learn a
handful of new numbers.

### F4 — `estimate_tokens` undercounts message content by ~1.4×

`conversation.py:394` uses bytes/4, commented "deliberately conservative for
ASCII-heavy JSON". Measured against session 3's actual history: **2.89
chars/token**, and **2.54** on the analysis payloads — dense numeric JSON with
sha256 hashes, `MT_scan_r0_c0`-shaped ids and 16-significant-digit floats. The
tool schemas and prose do sit at 4.2–4.8, so the *base* is fine; it is the
history the estimator is wrong about, and the history is what compaction meters.

Consequence: `DEFAULT_CONTEXT_HIGH_WATER_TOKENS = 120_000` was admitting ~166k,
and the peak call carried 208k. The guard is loose by 38% in exactly the
direction that costs money. **The comment is right about the intent and wrong
about the constant** — conservative for a context limit means *over*-counting.

### F5 — The 5-minute cache TTL does not fit a human turn boundary

`_with_cache_breakpoint` and `_system_blocks` both use
`{"type": "ephemeral"}`, the 5-minute default. Calls inside a turn are 30–50 s
apart and stay warm; the boundary between turns is a person reading a reply. The
1-hour TTL costs 2× on writes instead of 1.25× and needs three reads to pay off
— with ~4 calls per turn (556/137) and 137 boundaries, it pays off here.

**This is the largest identified term in the bill** ($18–29, per the
reconciliation), and the whole downside is a 0.75× premium on writes that were
happening anyway. It is also the only finding here where the *lifetime is
measured from the start of the request that writes or reads the entry*, so a
generation that runs long spends its own TTL: a 4-minute reply leaves one minute
for the operator.

### F6 — Every compaction throws the whole prefix away: $17.02

`model_messages` returns `[self._checkpoint, *full_history[self._cut:]]`, and
`self._checkpoint = _checkpoint(full_history[:chosen])` is re-derived from the
whole elided history each time. So checkpoint *N+1* is a different string from
checkpoint *N* sitting in the same position — the cache prefix is invalid from
byte one, and the next call pays 6.25× for a ~123k-token context. 19 compactions
did that.

The 1-hour TTL does not help here; the prefix genuinely changed. What would is
making the checkpoint **append-only**: checkpoint only the newly elided slice,
`_checkpoint(full_history[prev_cut:chosen])`, and keep earlier checkpoint blocks
byte-identical ahead of it. The prefix then survives every compaction.

The tradeoff is real and is not mine to settle: the per-segment `[-200:]` /
`[-100:]` caps stop being global, so artifact and hash de-duplication happens
within a segment rather than across the session, and a long run accumulates
several checkpoint blocks instead of one. Whether that weakens what the
checkpoint is *for* is a judgement about the provenance contract — including the
temporal disclaimer whose absence made a model report four-turn-old tool calls as
current activity.

### F7 — 25,786 tokens of tool schema on every one of 556 calls

81 tools. The median schema is **133 tokens**; the ten largest are 13,953 of the
24,166, led by `run_adaptive_survey` at 2,548, `run_timelapse` 1,789,
`run_multiposition_acquisition` 1,748, `run_zstack` 1,675, `run_tile_acquisition`
1,587, `run_autofocus` 1,450.

At $9.32 it is 9% of the $102.35, which makes it the fourth-largest term
and the reason to say clearly: **do not act on this one yet.** Those
descriptions are where
`feedback_rules_belong_in_parameter_descriptions` deliberately put statically
knowable refusals, and design/62 measured that placement working. Trimming them
to save $9 risks re-buying refusals at runtime, which cost rig trips. `R101`-style
deferred loading (`defer_loading` + tool search) keeps the text and stops
re-sending it, but changes which tools the model can see without a search step.
This is a measurement, not a decision.

## Decisions

**D1 (F0) — Record `response.usage` per call, in the sidecar shape the session
already has.** One `*_microclaw_usage.jsonl` beside the history, written from
`run_agent_iter` the way acquisitions and confirmations already are
(`__main__.py:191–193`, `webserve.py:371–373`). One record per API call, every
field the API returns plus the model actually used — **no derived "cost"
field**, for design/79's reason: naming the winner turns attribution into
label-reading.

```python
# agent.py, _stream_one_round: the Message is already in hand.
response = stream.get_final_message()
usage = response.usage
yield {"type": "usage", "model": response.model, "iteration": iteration,
       "input_tokens": usage.input_tokens,
       "cache_read_input_tokens": usage.cache_read_input_tokens,
       "cache_creation_input_tokens": usage.cache_creation_input_tokens,
       "output_tokens": usage.output_tokens,
       "stop_reason": response.stop_reason}
```

`ConversationStore.last_estimated_tokens` is the store's own view of the same
context and belongs in the record too — pairing it with `input_tokens` is what
measures F4's residual on live traffic. It is not reachable from
`_stream_one_round`, so it is read where the sink is wired, not here; deciding
that seam is part of the block.

`cache_read_input_tokens == 0` on a call that is not the first and not
post-compaction **is** the cold-boundary event, and it is the number that decides
D5's value. Two things to check in review: that a compaction is distinguishable
from a TTL miss in the record, and that the usage event is emitted on the
`max_tokens` and tool-use paths too, not only `end_turn`.

**D2 (F1) — The tool result loses `scientific_payload`; the manifest on disk
keeps it.** The payload exists to be hashed, and the hash stays. `parameters` is
the caller's own input echoed back and can be replaced by its hash in the
result. This does not reduce what is recorded anywhere — the manifest is written
to `analysis-manifest.json` before the return, and `manifest_path` is in the
result.

```python
# completed_dataset.py, at the return: disk keeps everything, the model gets one copy.
manifest_path.write_text(json.dumps(manifest, indent=2, allow_nan=False), ...)
result = {k: v for k, v in manifest.items()
          if k not in ("scientific_payload", "parameters")}
return {**result,
        "parameters_sha256": _sha(_canonical_bytes(parameters)),
        "scientific_payload_sha256": manifest["scientific_payload_sha256"],
        "manifest_path": str(manifest_path), "mosaic": mosaic_result}
```

Round the per-tile floats where they are produced, not here — a metric quoted to
16 significant figures is a formatting accident, and rounding it at the boundary
would make the emitted value disagree with the hashed one.

**D3 (F2) — `read_hook_log` gains a bound and names `rank_hook_log`.** An
unbounded read of a 200-frame log is the caller asking the wrong question. The
bound must be *stated in the result*, never silent: `CLAUDE.md` forbids
relaxing analysis coverage quietly.

```python
def read_hook_log(ctrl, guard, log_path: str, limit: int = 50,
                  where: str = "last") -> dict:
    entries = json.loads(path.read_text(encoding="utf-8"))
    shown = entries[-limit:] if where == "last" else entries[:limit]
    return {"log_path": log_path, "entry_count": len(entries),
            "entries": shown, "entries_shown": len(shown),
            "entries_omitted": len(entries) - len(shown),
            "rank_this_log_instead": "rank_hook_log" if len(shown) < len(entries) else None,
            "artifact": {"kind": "hook_log", "path": log_path}}
```

`limit`/`where` are the whole surface; the parameter description carries when to
reach for `rank_hook_log` instead, per
`feedback_rules_belong_in_parameter_descriptions`.

**D4 (F4) — `estimate_tokens` divides by 3, and says what it is.** One constant,
one comment that stops claiming conservatism it does not have. Measured
2.54–2.89 chars/token on real session content; 3 keeps a small margin on the
prose-heavy end without pretending to be a tokenizer.

```python
# conversation.py
# Three UTF-8 bytes per token, measured on real session histories (design/82 F4):
# 2.89 chars/token over a whole 2.9 MB history, 2.54 on numeric analysis payloads.
# Four was optimistic in the one direction a context guard must not be.
return math.ceil(len(payload.encode("utf-8")) / 3) + 8 * len(messages)
```

The high-water and low-water constants stay at 120k/90k, which now mean what
they say. Whether they should be *lower* is a measurement D1 enables, not a
guess: the read bill is linear in the window.

**D5 (F5) — `ttl: "1h"` on all four cache breakpoints. Ships in 82a, with the
instrument.** The whole cost of being wrong is the 2× write premium on writes
that were happening anyway; the cost of being right is the entire $18–29
cold-boundary term, which the reconciliation makes the largest identified line
in the bill. The
per-block TTL rule matters here: **entries with the longer TTL must appear
before shorter ones**, so if any breakpoint stays at 5 minutes it has to be the
messages one, after `tools` and `system`. Simplest correct thing is all four at
`1h`.

```python
# agent.py — three sites: _system_blocks (both blocks), TOOLS_CACHED, _with_cache_breakpoint
{"type": "ephemeral", "ttl": "1h"}
```

**D6 (F3) — no code change; the result already carries `dataset_sha256`.** The
guidance belongs in `run_analysis_on_saved_dataset`'s description: a re-analysis
of a dataset already analysed this session should say what it is looking for
that the previous run did not answer. Revisit only if D1's records show
re-analysis is still a material share after D2 lands — D2 cuts the unit cost of
this pattern by ~60%, which may be the whole fix.

**D7 (F6) — proposed, not decided: checkpoint the new slice, not the whole
history.** Worth $17, and the only item here that could weaken what the context
guarantees, so it needs the operator's judgement first.

```python
# conversation.py, ConversationStore.model_messages — the prefix survives compaction
segment = _checkpoint(full_history[self._cut:chosen], self.audit.secrets)
self._checkpoints.append(segment)          # earlier segments stay byte-identical
self._cut = chosen
return [*self._checkpoints, *full_history[chosen:]]
```

The question to answer before writing it: the `[-200:]` / `[-100:]` caps become
per-segment, so artifact and hash de-duplication is no longer session-wide. If
that is acceptable, this is nearly free; if it is not, the fallback is to compact
*less often* by lowering the window (82c) and accept the invalidations.

**F7 — deferred deliberately.** See the finding.

## Blocks

**82a — the instrument, plus the one-line fix.** D1 and D5. D1 ships before
anything else here for design/75's reason — the bound is worthless without the
measurement, and 82b's claims are only checkable against usage records. **D5
rides along deliberately**: it is one dict, it is the biggest identified term,
and D1 is pure observation with no cost effect of its own, so pairing them
confounds nothing. What it does cost is the chance to measure the *pre-fix* miss
rate directly — the 14%-to-25% spread stays unresolved, and the reconciliation
above stands as the only estimate of it. That trade is worth naming: paying
$16–26 a session to narrow a number we do not need is the wrong way round.
Acceptance: a driven session on
the demo machine produces a `_usage.jsonl` whose `cache_read_input_tokens` is
non-zero on ordinary rounds and **zero on the round after a compaction**; a
`cache_creation_input_tokens` on that round within 10% of the store's own
context estimate; and the sum of `output_tokens` agreeing with the history's
assistant content to within the tokenizer's margin.

**82b — the three payload fixes.** D2, D3, D4. Acceptance is a **replay** of
the five archived histories, not a rig trip:
`design/82-session-cost-reconstruction.py` re-run against the changed code,
reporting the floor before and after per session. Every limb settles locally.

The instrument has one trap of its own, and it is this notebook's own F4 in
miniature: its no-tiktoken fallback needs a *different* chars-per-token for
schemas than for tool-result JSON, and a single global ratio put the tool-schema
line at 27% instead of 15%. A replay that changes payload shapes will move that
ratio, so **re-run it with tiktoken present** rather than trusting the fallback
for a before/after comparison.

Watch for: `test_emitted_inline_defines_every_name_it_uses` and the export
tests, because D2 changes the shape of a recorded tool result and
`export_session_script` renders from the record; and any test that asserts on
`read_hook_log`'s `entries` being complete, which is D3's contract change and
must be updated deliberately rather than relaxed.

**82c — the levers 82b does not pull.** D7 (the append-only checkpoint, worth
$17 and the only change here that touches a contract rather than a payload), the
context window (120k/90k), and the tool-schema base (F7) — all measured against
D1's records from real sessions rather than a replay of these five. No decision
is pre-committed, and D7 does not start until the provenance question in F6 has
an answer from the operator.

## What this notebook will not do

- **No confirmation, anywhere.** Nothing here is about to spend a dose or write
  state the user owns; a prompt asking permission to send less context is the
  `design/60` block 60b mistake with a different subject.
- **No cost display in the agent's own context.** Telling the model what it has
  spent invites it to economise on the science.
- **No truncation of a scientific record.** D2 removes a duplicate and D3 bounds
  a *view*; both leave the manifest and the log complete on disk, and both say
  in the result that they did.

## Run ledger

| Block | Branch | Start commit | Implementer | Gate | Merged |
|---|---|---|---|---|---|
| 82a | `design82/82a-instrument` | `afa15eb` | codex runner | — | — |
| 82b | — | — | — | — | — |
| 82c | — | — | — | — | — |

Rows this notebook declines to take go to `design/70`, not into this file.
