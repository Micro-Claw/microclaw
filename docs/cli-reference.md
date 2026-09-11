# CLI reference

Every command below assumes `microclaw` resolves — from a source install, or
by the full path on a managed Windows install (see
[Installing on Windows](install-windows.md)).

## Session flags

| Flag | Default | Purpose |
|---|---|---|
| `--safety-config PATH` | the reviewed per-user profile | Hardware-limits file enforced before every tool call. Must carry `reviewed: true`, whether it's the default or an explicit path. |
| `--port N` | `4827` | ZMQ port to reach the running Micro-Manager instance. Match the port set in **Tools → Options**. |
| `--model ID` | `$MICROCLAW_MODEL` or `claude-opus-4-8` | Anthropic model id. The `MICROCLAW_MODEL` environment variable overrides the built-in default; `--model` overrides both. |
| `--profile` / `--no-profile` | off | cProfile the session and print stats on exit. |
| `--save-history` / `--no-save-history` | on | Write the conversation to a timestamped `*_microclaw_history.jsonl` file, and confirmations to its `*_confirmations.jsonl` sibling. |
| `--history-retention-days N` | keep everything | Opt in to deleting transcripts older than N days from the working directory at startup. Each deletion is printed. |
| `--setup-write-security-config` | off | Permit one confirmed write of setup security bounds. Valid only with `serve`, only on a loopback bind, and never together with `--safety-config`. |
| `--no-update-check` | off | Skip this launch's [update check](install-windows.md#automatic-updates). `MICROCLAW_UPDATE_CHECK=0` does the same permanently. Neither affects a source install, which never checks. |

`check-bridge` takes one flag of its own, `--bridge-timeout SECONDS` (default 5),
which bounds the handshake it waits on.

## Viewing a saved history

Saved `*_microclaw_history.jsonl` files are raw Anthropic messages — readable but
noisy. Render one as a browser transcript (user prompts, the agent's replies, and
every microscope tool call with its result, collapsed by default):

```
microclaw view-history 20260707_143437_123456_microclaw_history.jsonl
```

This writes a self-contained HTML file to your temp directory and opens it. Pass
`--no-browser` to just print the path. The viewer runs entirely locally — nothing
is uploaded. Snap thumbnails are rendered inline; tool calls are collapsed by
default.

## Where things live

| What | Where |
|---|---|
| Safety limits | `%APPDATA%\microclaw\safety_config.yaml` |
| API key | your OS credential store, else `%APPDATA%\microclaw\config.toml` |
| The installed program | `%LOCALAPPDATA%\microclaw\env-a` and `env-b`; `active-slot.txt` names the live one |
| Update state and its log | `update-state.json`, `launcher.log`, in `%LOCALAPPDATA%\microclaw` |
| Desktop shortcut + its icon | your desktop, and `%LOCALAPPDATA%\microclaw` |
| Saved conversations | `*_microclaw_history.jsonl`, in the folder Microclaw ran from |
| Confirmation audit | `*_microclaw_confirmations.jsonl`, beside the history |
| Token usage and acquisition diagnostics | `*_microclaw_usage.jsonl` and `*_microclaw_acquisitions.jsonl`, beside the history |
| Registered hooks + their manifest | `~/.microclaw/hooks` (`%USERPROFILE%\.microclaw\hooks`) |

On macOS and Linux, substitute `~/.config/microclaw` and `~/.local/share/microclaw`.
