# A local model behind the same loop

Status: **PROPOSED**, 2026-10-01. Nothing implemented. The evidence below is a
three-request spike, not a session.

## Problem

Microclaw can only think through the Anthropic API. An operator wants to run it
against a local Qwen served by llama.cpp on another machine — no per-call bill
(design/82: $102 over five sessions), and no dependence on an external service.
Microclaw must not anchor on that machine, that model or that server: the
target is "any OpenAI-compatible `/v1/chat/completions` endpoint".

The seam is one function. `agent._stream_one_round` is the only SDK call site;
it returns a `Message` whose `content` blocks (`text`, `tool_use`) and
`stop_reason` drive `run_agent_iter`. Everything downstream — the history
JSONL, `ConversationStore`, `export_session_script`, the viewer — reads the
Anthropic-shaped history, not the SDK.

## Evidence (spike, 2026-10-01)

Server: llama.cpp `b11260-e904318a2`, `Qwen3.8-27B-UD-Q4_K_XL.gguf`,
`-c 65536 -np 1 --jinja`, `n_ctx_train 262144`, `modalities.vision: false`,
chat template supports tools and `<think>`. Request: `SYSTEM_PROMPT` alone (no
knowledge base, no rig interview) plus all 83 `TOOLS` translated to
`{"type":"function","function":{name, description, parameters}}`, non-streamed.
Main at `9e9271a`. No tool was executed; round 3 answered with a **synthetic**
payload that said so in a `note` field.

| request | prompt tokens | new / cached | prefill | wall | result |
|---|---|---|---|---|---|
| cold "stage position?" | 33,592 | 33,592 / 0 | 37.3 s | 44.0 s | `get_xy_position` + `get_z_position` |
| warm "50 ms, DAPI" | 33,600 | 26 / 33,574 | 0.17 s | 4.6 s | `set_exposure {"ms":50}` + `get_available_channels` |
| tool result → reply | 33,688 | 114 / 33,574 | 0.27 s | 11.8 s | `end_turn` prose |

Prefill ~900 tok/s, generation ~30 tok/s. Every tool name called exists and
every argument matched its schema. The same three requests on the older
`128c478` tree (81 tools, 32,499 tokens) gave the same shape.

**One behavioural observation, n=2.** On `128c478` the reply relayed the
payload's "synthetic" note; on `9e9271a` it reported the synthetic numbers as
"Current stage position" with no caveat. That is the *say only what a tool told
you* rule, and it is what the gate scores — not a finding about the model.

**At `-c 131072 -fa on --cache-type-k q8_0 --cache-type-v q8_0`** (operator
restart, 2026-10-01; ~3 GB VRAM still free, KV size not printed): the same
three calls with the same arguments, cold prefill 37.9 s, warm 0.19 / 0.29 s,
generation **26.4 tok/s against 30.0** — 12 % slower, not attributed between
the quantised KV, flash attention and the larger context. The reply relayed the
synthetic note again, so that rule held in **2 of 3** runs.

What the spike did **not** measure: streaming, a session near the context
limit, compaction, the knowledge-base and skill-discovery blocks, images, and
any prompt-level rule over a real session.

## Decisions

**D1 — One backend, translated at the seam.** The canonical history stays
Anthropic-shaped. A second branch of `_stream_one_round` translates the request
out and the reply back, and returns a real `anthropic.types.Message` built with
`Message.model_validate` — so `run_agent_iter` (which reads `block.type`,
`block.id`, `response.stop_reason` by attribute), the history JSONL and the
exporter cannot tell which backend produced it. Checked: a translated
`tool_use` reply validates and round-trips `id`, `stop_reason` and
`cache_read_input_tokens`. No provider registry, no abstract base class: two
branches of one function.

**D2 — Translation.**

| Anthropic (history) | chat completions (wire) |
|---|---|
| `system` blocks | one `system` message, blocks joined; `cache_control` dropped |
| `tools[i]` | `{"type":"function","function":{name, description, parameters: input_schema}}`; `cache_control` dropped |
| assistant `text` + `tool_use` blocks | one assistant message: `content` + `tool_calls[{id, function:{name, arguments: json}}]` |
| user `tool_result` blocks | one `tool` message per block, `tool_call_id`, text content; `is_error` prefixed into the text |
| `image` block in a tool result | text-only model: replaced by `{"image_omitted": "this model does not accept images"}` — never dropped silently. Vision model: a following `user` message with `image_url` data URIs |
| `finish_reason` `stop` / `tool_calls` / `length` | `stop_reason` `end_turn` / `tool_use` / `max_tokens` |
| `delta.content` | `text_delta` event |
| `delta.reasoning_content` | not streamed as prose, not stored in history (the template strips it on replay); its length goes to `usage_sink` |
| `usage.prompt_tokens`, `prompt_tokens_details.cached_tokens` | `input_tokens`, `cache_read_input_tokens` in the existing `usage_sink` record |

Tool-call arguments arrive as JSON *text*; an unparsable one becomes a
`tool_result` error naming the parse failure, so the model sees and corrects it.

**D3 — Selection is one setting.** `--llm-base-url` / `MICROCLAW_LLM_BASE_URL`
set means the local backend; unset means Anthropic, unchanged. The model id
defaults to the server's single `/v1/models` entry, so the operator names
nothing else; the existing model picker lists that endpoint's models. The key,
when the server has one, goes through `credentials.py` under its own name —
never the Anthropic one — and the startup checks (`webserve.py`'s "No Anthropic
API key", `__main__`) accept a configured base URL instead. The URL is
operator configuration, never a constant in `microclaw/`.

**D4 — Vision is read, not assumed.** If the server answers llama.cpp's
`/props`, `modalities.vision` decides. Otherwise text-only, the safe direction:
an omitted-image note is honest, an image sent to a model that cannot read it
is a 400 or a hallucination.

**D5 — Errors map onto the existing two.** Connection refused/reset, 503 and
429 → the transient retry path, message naming the base URL. 404 on the model
→ `_BadModel`. A 400 whose body reports the context exceeded → an error that
states the server's `n_ctx` and the request's token count, not a generic
failure. `TRANSIENT_API_ERROR_MESSAGE` stops saying "Anthropic" on this path.

**D6 — The context budget comes from the server.** `ConversationStore`'s
120k/90k watermarks are Anthropic-sized; at `n_ctx 65536` a session would
overflow long before it compacted. Read `n_ctx` (llama.cpp `/props` or
`/v1/models` `meta.n_ctx`; otherwise a stated default) and set
`high = n_ctx − fixed_prefix − max_output`, low at 75 % of high. At the spike's
spike's numbers: at `n_ctx 65536` that is ≈ 23.7k tokens of history; at the
operator's chosen `131072`, **≈ 89.3k**, low water ≈ 67k.

A larger window is not free, and the cost moves rather than vanishing:
compaction becomes rarer, but the round after it re-prefills the whole
low-water prompt — ≈ 100k tokens, **≈ 110 s at the measured 900 tok/s**, and
prefill rate falls with length, so likely more. 84b must measure that round
rather than this estimate, and the operator should see it coming: a progress
event saying the context is being rebuilt, not a silent two-minute pause.

**D7 — Timing contract.** Prefix reuse is what makes this usable: 37 s cold
against 0.2 s warm per round. Anything that rewrites the prompt's front pays the
cold cost again — compaction, a changed knowledge base or skill-discovery block,
another client on a one-slot server. The local branch must keep the prefix
byte-stable between rounds (same system string, same tool order, same
serialisation of earlier turns), and `usage_sink` records `prompt_n`,
`cache_n` and `prompt_ms` so a cold hit is visible per round rather than
inferred. Not attributed yet: how often a real session goes cold.

**D8 — Export is untouched.** `export_session_script` reads recorded tool calls
and results; the history shape does not change, so no emitter changes. A test
asserts a session driven through the local branch exports the same script as the
same calls driven through the Anthropic one.

## Blocks

**84a — the backend.** D1–D5 and D8. Streaming chat-completions client over
`httpx` (already a direct dependency; no new package), translation
both ways, selection, credentials, startup checks, `usage_sink` mapping.
Tests: a fake server written from llama.cpp's actual SSE shape (`tool_calls`
deltas split across chunks, `reasoning_content` deltas, the final `timings`
object), not from our caller. Watched-fail evidence on each translation row.

**84b — the budget and the prefix.** D6 and D7: watermarks from `n_ctx`, a
prefix-stability test (two consecutive rounds serialise an identical leading
byte range), compaction measured as a cold hit.

**Gate 84 — one driven session against a real llama.cpp server**, from a
machine that can reach it. Score from the history and usage JSONL: per-round
`prompt_ms` and `cache_n` (how many cold rounds, and which events caused each),
whether compaction fired before overflow, and the reporting rules (numbers only
from tool results; `unknown` relayed as `unknown`; illumination state at
hand-off). Image tools exercised once to see the omitted-image note reach the
model.

## Open questions for the operator

- **Q1 — context size. Settled, 2026-10-01:** `-c 131072` with q8_0 KV and
  flash attention, ~3 GB VRAM spare. Not pushed to 262,144: a cold round after
  compaction would then cost minutes (D6).
- **Q2 — gate machine. Settled, 2026-10-01:** the llama.cpp workstation
  itself, with Micro-Manager's demo config and Microclaw installed beside the
  model (operator to install). No network path between the gate's Microclaw and
  its server, so the timings are a local deployment's, not a tunnel's. The
  earlier gate machines are at another site and are not assumed to reach it.

## Run ledger

Notebook drafted on `Qwen-local-model` from `main` `9e9271a`.

| block | branch | start | state |
|---|---|---|---|
| 84a | `design84/84a-backend` | `55ccc95` (fixtures) on `4b2e9cf` | **merged** to fork `main` `24a28b3`, 2026-10-06 |
| 84b | `design84/84b-budget` (on 84a) | `3b0f1c7` | **merged** `24a28b3`, 2026-10-06 (gate 84 round 1) |
| 84c | `design84/84c-compaction` (on 84b) | `3e778b5` | **merged** `24a28b3`, 2026-10-06 (gate 84 round 2: 9/9; F3 → 84d) |
| 84d | `design84/84d-calibrate` | `4859ce2` (fork `main` + httpx fix) | **merged** to fork `main`, 2026-10-06 (gate 84 round 3: 9/9) |
| 84e | `design84/84e-effort` | fork `main` `4a01af9` | assigned 2026-10-22 (D17) |

**84a review, 2026-10-02.** Baseline at `55ccc95`: 5512 passed, 108 skipped,
1 failed (`test_built_wheel_contains_the_source_tree_skill_catalog`, passes
alone; ran during a network drop). Three runner turns: `f6917b3` (incomplete
by its own report), `ec16e85` (round 1: eleven defects F1–F11, among them the
compaction checkpoint silently dropped and `--llm-base-url` lost before
`serve`), `64065e6` (round 2: G1–G7; turn killed by the coordinator's 2-hour
background limit during its final check, committed as found, then reviewed).
Coordinator full suite on `64065e6`: **5532 passed, 108 skipped**. Mutation
check, each D2 row and error mapping broken in turn: 11/14 caught on
`ec16e85`, **19/19** on `64065e6`. Live against the workstation through
`_stream_one_round` with `_system_blocks()` (33,889 tokens): 124 streamed
deltas, first at 8.1 s, `end_turn`; second round `get_z_position {}` with
`cache_n 33853`, `prompt_ms 174`. Known nits left: one-line `if`s and a
redundant `kind` branch in `webserve.py`'s key route.

**Runner lesson.** A revision turn outlived Claude Code's 2-hour background cap
when the network dropped mid-turn. Launch long runner turns under `nohup`
(CLAUDE.md step 2) rather than as session background tasks.

**84b review, 2026-10-02.** Four runner turns: `246a72f` (budget from the
server), `1dcb5b1` (H1: fixed prompt counted through `/apply-template` +
`/tokenize` = **33,864**, equal to the server's own `prompt_tokens`; the
untemplated count was 30,118), `befe1a7` (K1: the browser locked Send without a
key on a keyless local server), `0ecbbcc` (L1–L4: with `--api-key` every
llama.cpp endpoint but `/health` 401s, the store was built before the key was
loaded, and failed probes were cached — the budget clamped to **2**).
Coordinator fixes: `55af3bb` (a stored key was no longer registered with the
history audit's redaction after that reorder), `2780191` (`keyRequired`
declared with the page state; an absent `key_required` reads as required),
`557200f` (a stale round-1 test). Live on the workstation: budget 84,920 /
63,690 with the key; without it a stated provisional 4,096 / 3,072 and every
401 named, re-read exactly when the key is entered. Prefix reuse measured over
a 5-round tool loop: each round reused all but 4–56 tokens of the previous.
Full suite on `a8c4f81`: **5547 passed, 108 skipped, 1 failed** —
`test_built_wheel_contains_the_source_tree_skill_catalog`, which passes alone
and failed the same way at 84a's baseline (2 of 6 full runs this session).

**D6 correction.** After compaction the system+tools prefix stays cached, so
the slow round re-processes the history only: ≈ low-water (63,690) at
~900 tok/s, about 70 s, not the whole prompt. Gate 84 measures it.

**For later, not a block yet.** The model's chat template renders "Reasoning
effort is set to xhigh" by default; a lower per-request effort may cut reply
latency substantially. Measure against gate 84's baseline before changing it.

## Gate 84, round 1 (workstation, 2026-10-05) — scored from artifacts

Session `20261005_153316_502746`: 14 model calls, all local; 8 tool calls, all
registered, none unparsed; the no-key turn gave the 401 error naming
`MICROCLAW_LLM_API_KEY` (O2) as a toast, Send stayed enabled with no banner
(O1); entering the key re-read the budget exactly (O3b: 33,864 / 84,920 /
63,690); the illumination answer relayed `unknown` from `get_system_state`
(O5). Seven same-turn rounds reused all but ≤ 64 tokens of the previous prompt.
The thumbnail moved the estimate by 519 tokens. Peak prompt + output reserve
120,205 of 131,072.

**F1 — a compaction that cannot get under high-water is committed anyway, and
repeats.** One `read_hook_log(limit=200)` returned 140,218 characters (~59k
tokens). `ConversationStore` could fold only the small early turns (the
two-recent-turns floor), committed a checkpoint at **87,067 > 84,920**, and
compacted again on each of the next two calls (86,773, then 67,021). Each
commit rewrites the history head, so each paid a full history re-prefill:
**134.0 s, 133.3 s, 98.7 s — 366 of the session's 443 s of prompt
processing**, the second for ~300 tokens of saving. The operator saw a
"rebuilding" toast and minutes of silence, with nothing saying it had not
helped (O4: "several minutes").

**F2 — the gate's budget limb could not fail on F1.** It excluded compaction
records, so it passed with a post-compaction estimate above high-water.

**Measured, not graded.** Prefill slows with position: 826 tok/s at 34k, 671 at
54k, 583 at 112k. Generation was 464 s of the session (more than prompt
processing), with 28,209 characters of hidden reasoning. The history estimate
ran 1.12–1.63× the server's own history count (safe direction).

## 84c — compaction only when it helps, and say so

**D9 — commit a compaction only if it reaches high-water or below.** Otherwise
send the current view unchanged: the prefix (and on the Anthropic path the
cache) survives, and the real limit is the server's, which the request still
fits (F1's peak: 120,205 of 131,072). Both backends; the Anthropic path gains
the same saving on cache rewrites (design/82).

**D10 — the operator sees what a compaction does and costs.** The `compaction`
event carries the estimate before and after, and an expected rebuild time from
this session's own measured prefill rate (not a constant); a declined
compaction emits an information event saying the history is above the
threshold and recent turns cannot be folded yet. Information, never a
confirmation (CLAUDE.md).

**D11 — the context window is operator configuration, measured, not coded.**
84b already reads `n_ctx`. A larger `-c` makes compaction rarer but each rebuild
longer, and slower per token (above): at `-c 262144` a low-water near 160k would
re-prefill ~125k tokens at well under 583 tok/s — several minutes per rebuild.
Gate 84c measures one larger window rather than assuming it helps.

**Gate 84c.** Re-run gate 84 on the workstation with the fixed scorer (F2: grade
prompt + reserve ≤ `n_ctx` and report every request sent above high-water; a
mutant for each), expecting one compaction-cost round where round 1 had three.

**84c review, 2026-10-05.** Three runner turns: `fa61636` (D9/D10; three
existing tests weakened until they no longer tested their purpose — 84a's F1
checkpoint-on-the-wire test among them), `ce3d2fc` (N1–N4: purposes restored at
watermarks where D9 commits; the gate shape pinned — old store 1, 2, 3 commits;
rebuild estimate text-only; declined notice once per stretch), `4c99b81` (six
more fixtures outside the targeted files resized; the design/82 R63 probe now
says it replays under the current policy, so a pre-84c session's partition is
not historical proof). Coordinator: `f26df3e` (gate scorer F2 — fit and D9
graded separately; re-scored on round 1's real artifacts it fails D9 at records
10 and 11), `c650837` (round-2 runbook). Replay of round 1's real history
through the final store at the gate's watermarks: old 3 commits at calls 10, 11,
12; new: declined once at 10, sent as is at 11, **one** commit at 12. Full suite
on `4c99b81`: **5551 passed, 108 skipped**.

## Gate 84, round 2 (workstation, 2026-10-06) — scored from artifacts

Session `20261006_090604_910375`, 9/9 graded limbs. Two compactions where round
1 had three, both committed under high-water; two over-threshold requests sent as
is, each reusing the cache (68,538 and 109,934 tokens). Rebuilds 125.3 s + 72.1 s
= 197 s against round 1's 366 s. Peak prompt + reserve 121,586 of 131,072. The
two sessions differ (Qwen read the log with `limit: 150` this time), so the
comparison is qualitative; the per-record mechanism is not.

**F3 — a fold that clears high-water by a hair buys nothing.** Compaction 1 left
84,249 (671 under 84,920); the next call was over again and compaction 2
followed two calls later. Replaying the real history with "commit only at
low-water" gives one compaction (at call 14) and saves the 125 s rebuild, at the
cost of sending call 13 about 5k tokens larger — which needs a calibrated
"would not fit" valve, because the estimator runs ~1.2× high for Qwen and in raw
estimate units that valve fires at call 12 and reproduces today's behaviour.

**F4 — the rebuild prediction.** 150 s and 111.5 s predicted against 125.3 s
and 72.1 s measured, because the estimate counts ~1.2× the server's tokens; and
the operator's 210 s to first text includes the model's hidden reasoning after
the rebuild (5,097 characters on that turn), which the toast does not mention.

**F5 — the toasts vanish before they can be read, and `serve` logs only
`compaction`.** **F6 — generation now dominates:** 728 s against 382 s of prompt
processing; one round wrote 8,656 reasoning characters before a tool call.
**F7 — the model button says `claude-opus-4-8` on the local path** until the
server answers `/v1/models`, which a keyed server does only after the key.

## 84d — compaction that buys headroom, numbers that are true

**D12 (F3) — on the local path, commit a fold only when it reaches low-water,
unless the current view would not fit the server.** Otherwise send as is (D9's
decline). The safety valve compares the *calibrated* view (D13) with what the
server can take — `n_ctx − fixed − MAX_OUTPUT_TOKENS − margin`, in server
tokens — and, when the view would not fit, commits the deepest fold at or below
high-water as 84c does. The Anthropic path keeps 84c's D9 unchanged: it has no
server-reported limit to calibrate against.

**D13 (F3, F4) — calibrate the estimate against the server's own count.** Each
local response reports `prompt_tokens`; with the fixed prompt known (84b), the
server's history tokens over the store's estimate of the history that was sent
is a measured ratio (round 1: 1.12–1.63). The valve (D12) and the rebuild
prediction use the calibrated value; before any response, the raw estimate,
which errs high (the safe direction for both).

**D14 (F4, F5) — the operator can read what happened.** The compaction and
declined notices stay up long enough to read (until dismissed or the next turn,
not a few seconds), say that the predicted time covers the rebuild only and the
model then thinks before it replies, and `serve` prints each with its numbers.

**D15 (F7) — the model button names the model that is answering.** On the local
path it shows the server's model id, refreshed when a key is entered; before the
server has answered it says so, and it never shows a Claude id.

**D16 (F6) — reasoning effort: an optional setting, default unchanged**
(operator decision, 2026-10-06). `MICROCLAW_LLM_REASONING_EFFORT`, unset by
default, is forwarded unchanged as `chat_template_kwargs.reasoning_effort`, and
the fixed-prompt count renders with it. Measured on Microclaw's real prompt and
tools, 2 runs per cell: median generation per call 16.5 s (`xhigh`), 6.0 s
(`medium`), 9.1 s (`low`); simple turns fell from 7–14 s to ~2 s, the 3×3
bead-count planning turn did not reliably fall (80 s `xhigh`, 85–93 s
`medium`, 20 s and 92 s `low`; 217 s once at `xhigh`). No quality loss visible
at that sample size, which cannot show equivalence. The operator tries it out
of band.

**84d review, 2026-10-06.** Two runner turns: `0b55c88` (D13–D15 right; D12
not — the local path returned before trying any fold whenever the calibrated
view fitted, so a low-water fold never committed and no notice fired;
`resolve_model` sent `"model": null`), `9372a89` (P1–P5: the D12 sequence,
the ceiling stated, the gate-shape test watched failing on 84c's store, a
neutral `"default"` model string, and D16). Coordinator: `cde64fe` (an empty
effort means unset). Replays of both real gate sessions through `9372a89`'s
store, calibrated from each session's own usage: round 1's history **0**
compactions (one decline), round 2's **1**, landing at 55,222; largest request
~126.6k of 131,072 with the reply reserve. Live: budget unchanged with the
setting unset; with `medium` the probe counted 33,826 fixed and the request was
33,831. Full suite on `cde64fe`: **5559 passed, 108 skipped**.

**Found on the way, fixed on `main` first** (`54e1055`): `httpx` was declared
only in the `test` extra while `agent.py` imports it; `anthropic` now depends on
`httpx2`, so a fresh `.[serve]` install could not import `microclaw.agent`. The
gates installed `.[serve,test]` and never saw it.

## Gate 84, round 3 (workstation, 2026-10-06) — scored from artifacts

Session `20261006_135341_257860`, 9/9 graded limbs, reasoning effort unset.
**One** compaction (95,316 → 6,925 estimated), its rebuild **8.6 s** (5,823
tokens processed, 33,366 reused; predicted 12.7 s) — against 366 s in round 1
and 197 s in round 2. Five requests went out above high-water without
compacting (records 16–20), each reusing the cache (0.3–7.2 s); the largest
calibrated view was 83,624 against the 84,920 ceiling, so the valve correctly
held. Peak prompt + reserve 125,680 of 131,072. O6: the model button named the
Qwen model. Operator: "no appreciable delay" on the compaction round.

Not compaction: record 16's 104 s was the server processing one 140,232-
character `read_hook_log(limit=200)` result (59,262 new tokens), and a later turn
asking for the same read overflowed the context; the server refused with its own
numbers and the turn rolled back (`R146`). Model time was 200 s of prompt
processing against **938 s of generation** — one round wrote 11,781 reasoning
characters in 187 s — which is what the D16 setting is for.

Fixed before merge (`75fe21b`): the rebuild count is whole tokens (the toast
had shown `6,106.033`); persistent notices close from their own button so their
text can be selected and copied; `serve` prints an error event's message, not
only `error`.

## Reasoning effort on a real microscope (operator, 2026-10-14) — scored from artifacts

Three sessions on a real Zeiss rig (HXP 120 V fluorescence via the HXP shutter,
reflector turret DsRed / GFP / DAPI), one per level, the same six-prompt script:
`xhigh` `20261014_132137_063462`, `medium` `20261014_140039_773457`, `low`
`20261014_141904_322634`. The sessions are not identical: `xhigh` ran first and
carried two extra turns that taught the knowledge base the light path and the
full-frame imaging, so `medium` and `low` started with that stored; `low` added
"combine into a 3 colour image and save" and "show me the final image".

The five shared turns (red snap, "is it focused?", focus sweep, 3-colour image,
refocus and repeat), each split from the usage records into model time and the
gaps between calls (tools and hardware):

| level | wall | generation | prompt processing | tools + hardware | hidden reasoning |
|---|---|---|---|---|---|
| `xhigh` | 1,064 s | 916 s (86 %) | 51 s | 97 s | 69,806 chars |
| `medium` | 832 s | 705 s (85 %) | 64 s | 63 s | 54,920 chars |
| `low` | 632 s | 483 s (76 %) | 69 s | 79 s | 28,288 chars |

**The time is the model thinking, not the microscope:** tools and hardware were
6–12 % of each session. `low` cut the shared turns by ~40 % against `xhigh`;
`medium` sat between but had the slowest single turn (3-colour, 33,040
reasoning characters, 375 s). n = 1 per level: turn-to-turn spread is as large
as the difference between levels.

**`low` reported a dataset it never acquired.** In "combine into a 3 colour
image and save", it acquired red and green (acquisition records at 18:34:34 and
18:34:51 UTC), switched the turret to DAPI, and wrote a composite script without
acquiring blue; its reply said "Saved to disk: `red_1`, `green_1`, `blue_1` …
These are the real, verified acquisitions." The refocused set later in the
session was complete (red, green, blue at 18:38–18:39). No such error was found
in `xhigh` or `medium` — which, at n = 1, is not evidence that they cannot make
it (`R148`).

**Channel switching.** All three sessions found Micro-Manager's `Channel` config
group empty (`get_available_channels` → no preset), so every level switched the
reflector turret with `set_device_property`, which is correct for that
configuration. `xhigh` alone built the 3-colour image as one acquisition with a
per-frame filter switch (one dataset); `medium` and `low` snapped three
unsaved frames. The operator is checking the rig's config groups; a populated
`Channel` group would let `set_channel` and multi-channel acquisitions do this
in fewer calls at any level.

**No route to a colour composite.** Asked to combine the channels, `low` said
plainly it had no tool for it, wrote a script it flagged as unrun and
unverified, and later opened the three channels with Fiji steps for the overlay
(`R149`).

**Decision for now:** the default stays the template's (`xhigh`);
`MICROCLAW_LLM_REASONING_EFFORT=medium` is the faster option worth using; `low`
is not recommended for real work on this evidence. Re-test after the `Channel`
group exists, two or three sessions per level.

## 84e — change the reasoning level within a session

**D17 — a session-only reasoning-level control beside the model picker**
(operator request, 2026-10-22). On the local path the browser offers *server
default*, `low`, `medium` and `high` — the OpenAI-style vocabulary, so Microclaw
does not anchor on one template's spelling (the measured Qwen template maps
`high` to its `xhigh`). It applies to this session only; `MICROCLAW_LLM_REASONING_EFFORT`
stays the startup default. Refused while a turn runs (as a model change is) and
offered only on a loopback bind (as the key and model editors are). Operator
configuration: there is no tool through which the model changes its own level.

**What a change costs, disclosed, not confirmed.** The template writes the level
into the system prompt, so a change invalidates the server's prompt cache and
the next request re-processes the whole prompt — measured ~40 s for the fixed
prompt cold, plus the history at the session's measured prefill rate (583–826
tok/s on the workstation). The control shows that estimate as an information
notice; a "no" would change nothing, so it is not a confirmation (CLAUDE.md).
The budget is recomputed on change, because the fixed prompt's size depends on
the level (33,864 at the default, 33,826 at `medium`).

**Recorded.** Every usage record carries the level in force for that call, so a
session that switches can be analysed per level (the 2026-10-14 comparison had
to infer it from file names).

