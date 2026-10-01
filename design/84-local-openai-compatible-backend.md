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
numbers that is 65,536 − 33,592 − 8,192 ≈ **23.7k tokens of history**, which is
small (see Q1).

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

- **Q1 — context size.** 23.7k of history at `-c 65536` means frequent
  compaction, each a ~37 s cold round. The model trained at 262,144; does the
  workstation have memory for `-c 131072` (≈ 89k of history)? Quantised KV
  (`-fa on --cache-type-k q8_0 --cache-type-v q8_0`) roughly halves the cost.
- **Q2 — gate machine.** Which machine runs gate 84: the demo machine (can it
  reach the workstation on the JHU network?), M5 or M2?

## Run ledger

Notebook drafted on `Qwen-local-model` from `main` `9e9271a`. No block assigned.
