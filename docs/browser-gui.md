# The browser GUI

Drive a session from a chat window instead of the terminal REPL. Same transcript,
plus a composer.

On a managed Windows install the desktop icon opens this and nothing else. From a
source install (the `serve` extra is required — see
[Developing Microclaw](development.md)):

```bash
microclaw serve                                  # → http://127.0.0.1:8000
```

Either way, `serve` runs until you stop it with **Ctrl+C** in its console window.

Uses the reviewed per-user safety profile by default. The session flags
(`--safety-config`, `--port`, `--model`, `--save-history`) belong to the top-level
parser, so if you pass them they go *before* `serve`:
`microclaw --safety-config other.yaml serve`.

| Flag | Default | Purpose |
|---|---|---|
| `--host ADDR` | `127.0.0.1` | Bind address. Anything but loopback needs `--allow-remote`. |
| `--web-port N` | `8000` | HTTP port for the GUI. |
| `--no-browser` | off | Print the URL instead of opening a browser window. |
| `--allow-remote` | off | Permit an authenticated non-loopback bind. Requires `--behind-tls-proxy`. |
| `--behind-tls-proxy` | off | Assert a trusted TLS-terminating proxy is in front. Requests are served only with `X-Forwarded-Proto: https`; the cleartext bind port must be reachable only by that proxy. |

In loopback mode, a browser window opens once the server is accepting
connections. Remote mode never auto-opens a browser because only the operator
knows the proxy's public URL.

Remote mode refuses direct cleartext HTTP. Put the server behind a trusted
TLS-terminating proxy, pass both `--allow-remote --behind-tls-proxy`, and ensure
the proxy overwrites (rather than merely forwards) `X-Forwarded-Proto` with
`https`. Bind to a proxy-facing interface or firewall the cleartext bind port so
clients cannot reach it directly. The header is an operator-supplied transport
assumption; Microclaw cannot verify that the request actually traversed the
proxy. At startup Microclaw prints a long-lived bearer token for non-browser API
clients and a single-use browser pairing code to append to the operator's own
public HTTPS proxy URL. The pairing code is in the URL fragment, is cleared from
the address bar before exchange, expires after 15
minutes, and becomes a 12-hour HttpOnly, Secure, SameSite=Strict cookie. A code
can also be pasted into the page manually. Set `MICROCLAW_REMOTE_TOKEN` to use a
stable operator-managed token (minimum 32 characters); otherwise a token is
generated for that process and is never persisted. An authenticated bearer may
POST `/api/pair/code` to mint a replacement pairing code.

JSON request bodies are capped at 64 KiB globally, except `/api/prompt`, which
allows 256 KiB. Oversized requests receive HTTP 413.

## Desktop shortcut (Windows)

```
microclaw install-shortcut
```

Puts a **Microclaw** icon on the desktop that launches the browser GUI — no
terminal, no flags. The console window it opens *is* the server: it shows the
connection status and any startup error. Press **Ctrl+C** there to stop it when
you are done; closing the window stops it too.

The shortcut runs `serve` and nothing else. It is always loopback-only. Valid
reviewed security bounds open a normal session; missing, invalid, or unreviewed
bounds open restricted setup without write authority and name the file that must
be moved or repaired.

| Flag | Purpose |
|---|---|
| `--dry-run` | Print what would be written, and where, without writing it. |
| `--remove` | Delete a previously installed shortcut. |
| `--dest DIR` | Write to a directory other than the desktop. |

Run it again after moving or reinstalling the environment; the shortcut points at
the `microclaw` it was created from.

The server holds one microscope and one conversation. A turn takes the session
lock, so a second prompt is refused (HTTP 409) rather than interleaving tool calls
on the hardware; requests carrying a foreign `Origin` are refused outright, so a
stray browser tab cannot drive the stage. History is written as messages are
produced, and on shutdown Microclaw **reports** declared illumination state
without changing it — no exit path writes to Micro-Manager, exactly as in the
REPL.

**Stop after the current step** asks the running turn to stop at the next round or
tool boundary. It is cooperative and says so: a tool call is blocking Java, and
there is no interrupting `run_timelapse` halfway.

Illumination-enable and acquisition-threshold confirmations can be approved once,
or granted for the rest of the session. Active grants are listed in the page and
individually revocable. Nothing else is grantable — confirmations that gate
Microclaw's own configuration are always asked.

The model chip in the header switches models for this process only (`--model` and
`MICROCLAW_MODEL` stay the durable knobs). It refuses mid-turn, and is unavailable
entirely when bound beyond localhost.

If `ANTHROPIC_API_KEY` is unset, the page collects a key and (optionally) stores it
in your OS credential store via `keyring`, falling back to
`~/.config/microclaw/config.toml` (`%APPDATA%\microclaw\` on Windows). The key is
never echoed back — only a four-character suffix, to confirm which one is set — and
it is never written to `safety_config.yaml`. Resolution order is environment
variable, then keyring, then that file. Under `--allow-remote` the key cannot be
set from the browser at all.

To swap keys mid-session, click the `key …AA8f` chip in the header. Saving without
*Remember on this machine* applies the key to the running process only, so a key
already in the credential store comes back on the next start — the UI says so when
that is the case.
