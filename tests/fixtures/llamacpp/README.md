Raw HTTP responses recorded on 2026-10-01 from a real llama.cpp server
(build b11260-e904318a2, Qwen3.8-27B-UD-Q4_K_XL.gguf, `-c 131072 -np 1 --jinja
-fa on --cache-type-k q8_0 --cache-type-v q8_0`, text-only) for design/84.

Each `.http` file: `HTTP <status>`, a JSON dict of response headers, a blank
line, then the exact body bytes. Chat requests carried Microclaw's
SYSTEM_PROMPT and all TOOLS, streamed with `stream_options.include_usage`,
except `error_bad_key` (wrong key) and `error_context_exceeded` (a 140k-token
user message, no tools). `get_props` / `get_v1_models` are the GET endpoints.
Replay these; do not hand-write SSE.

`post_tokenize` is `POST /tokenize` with body `{"content": "Microclaw measures the focus_metric."}`.
