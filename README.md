# Microclaw

<!-- The icon floats beside the description, never beside the <h1>: GitHub gives
     h1/h2 a full-width bottom border, and that rule would cut across the image. -->
<img src="docs/microclaw-icon.png" alt="" width="120" align="left" hspace="16" vspace="4">

An AI agent for [Micro-Manager](https://micro-manager.org) microscopy control. Describe your acquisition protocol in plain language; Microclaw translates it into Micro-Manager tool calls while the GUI responds in real time.

<br clear="left">


> [!CAUTION]
> The hardware safety features are not comprehensive. Always be mindful of what your microscope is doing. Use at your own risk.

> [!IMPORTANT]
> **PREVIEW ONLY** This package is provided as a preview for feedback only. 
> Specifications (e.g. safety_config.yaml) are unstable and the design is subject to change.
>
> Suitable for experiments, exploration and prototypes. It is NOT suitable for production use at this time.

## Architecture

```
User (natural language) → AgentLoop (Anthropic API) → ToolRegistry → SafetyGuard → MicroscopeController → pycro-manager ZMQ → MM GUI
```

- **Agent**: `claude-opus-4-8` via Anthropic API with tool use and prompt caching.
- **Safety**: User-defined `safety_config.yaml` enforced as a hard gate before every hardware call. The AI cannot override these limits.
- **Backend**: pycro-manager (ZMQ on port 4827). Open Micro-Manager normally; Microclaw connects to the running instance.

## Install (Windows)

You do not need Python; the installer brings its own. Have your microscope in
front of you: after installing the files, `install.bat` walks you through opening
Micro-Manager and the restricted first-launch setup in the same terminal.

**1. Install Micro-Manager.**
Install [Micro-Manager 2.0](https://micro-manager.org/Download_Micro-Manager_Latest_Release)
(a build later than 2026.06.26, for full functionality). You can leave it closed
until the Microclaw installer asks you to open it.

**2. Download Microclaw.**
On this repository's page, click **Code → Download ZIP**, then right-click the
downloaded file and choose **Extract All**.

**3. Double-click `install.bat`** inside the extracted folder.

It installs everything into `%LOCALAPPDATA%\microclaw` — no administrator rights,
nothing else on your machine is touched — and puts a **Microclaw** icon on your
desktop. It takes a few minutes. Then it asks you to open Micro-Manager and tick
**Tools → Options → Run pycro-manager server on port 4827**. After you confirm,
the installer checks that the bridge really answers. It gives you three attempts;
when the bridge is ready, first-launch setup continues in the same terminal.

> Windows may show a blue **"Windows protected your PC"** banner, because the file
> came from the internet. Click **More info → Run anyway**.

> If the bridge is still unavailable after three checks, installation remains
> complete and the installer prints the manual setup command. Until setup has
> run, the desktop icon opens a console that says `No safety config` and tells
> you to run `microclaw init`.

**4. Generate and review your safety profile.**
The installer starts setup automatically after its bridge check. Type the exact
hardware-contact acknowledgement when asked — it must match exactly, and you get
three attempts, so a typo costs nothing. Setup then connects **read-only**,
enumerates your devices, disconnects, and writes an unreviewed profile to
`%APPDATA%\microclaw\safety_config.yaml`. It asks you to confirm the hazardous
limits — stage travel, exposure, acquisition budgets — and it never invents one.
It starts no agent and exposes no tools that move anything.

If you need to run setup by hand, keep Micro-Manager open with its ZMQ server
enabled and use this full path in PowerShell or Command Prompt:

```
"%LOCALAPPDATA%\microclaw\env\Scripts\microclaw.exe" init
```

The quotes matter, and the full path is needed because the installer does not put
`microclaw` on your `PATH`. The manual command offers to run setup; answer **y**.

Then **open that file and read it**. Every limit in it is a claim about your
microscope that only you can check. When you are satisfied, change
`reviewed: false` to `reviewed: true` at the top. Confirm it is valid:

```
"%LOCALAPPDATA%\microclaw\env\Scripts\microclaw.exe" check-config
```

It reports every problem at once, needs no microscope connection, and exits `0`
when the file is ready. A note that some limits still match the packaged example
is a prompt to double-check those values, not a refusal.

**5. Double-click the Microclaw icon.**
A console window opens — that is the server; closing it stops Microclaw — and a
browser window follows. It will ask for an Anthropic API key the first time.

To upgrade, download the ZIP again and double-click `install.bat` again. It is
safe to re-run: it upgrades in place, and leaves your safety limits and API key
alone. When it finds the existing safety profile it skips first-launch setup; you
do **not** repeat step 4 on an upgrade. **Close Microclaw first** —
while its console window is open, Windows holds the installed files locked and
the upgrade will fail.

### Running it later

The desktop icon is the whole interface, and you should not need a terminal
again. If you'd rather use one, note the installer does not put `microclaw` on
your `PATH`, so spell out the same path as step 4:

```
"%LOCALAPPDATA%\microclaw\env\Scripts\microclaw.exe" serve   # browser GUI
"%LOCALAPPDATA%\microclaw\env\Scripts\microclaw.exe"         # REPL
```

See [CLI options](#cli-options) for the flags, which go *before* the subcommand.

## Install from source (developers)

[`uv`](https://docs.astral.sh/uv/) is the supported toolchain; it downloads a
CPython for you, so nothing needs to be installed first.

```bash
uv venv --python 3.12
uv pip install -e ".[serve,test]"
uv run pytest
```

An existing conda environment works fine too — `pip install -e ".[serve,test]"`
inside it does the same thing.

Under `uv`, prefix the commands below with `uv run` (`uv run microclaw ...`,
`uv run pytest`). In an activated conda environment they work as written.

**Then build a safety profile for this rig.** Micro-Manager must be running with
**Tools → Options → Run pycro-manager server on port 4827** ticked. To check that
without starting anything:

```bash
microclaw check-bridge          # exits 0 only when the bridge answers a real request
```

The short path is then one command:

```bash
microclaw first-launch-setup --out safety_config.yaml   # read-only; disconnects before the interview
# Open safety_config.yaml in your editor. Review every limit, then set `reviewed: true`.
microclaw check-config safety_config.yaml               # offline; exits 0 when it is ready
microclaw --safety-config safety_config.yaml serve      # top-level flags go BEFORE the subcommand
```

To keep the raw enumeration as evidence — useful when you want to diff what the
rig reported against what you declared — split it in two. With `--inventory`,
setup consumes the saved file and does not connect at all:

```bash
microclaw inspect-rig --out rig-inventory
microclaw first-launch-setup --inventory rig-inventory/inventory.json --out safety_config.yaml
```

### Make it the default, and get the desktop icon

The commands above pass `--safety-config` every time. Once the profile is
reviewed and you want the same zero-argument launch the Windows installer
produces, move it to the per-user path — the file every command loads when you
pass no `--safety-config`:

```powershell
# Windows (PowerShell)
New-Item -ItemType Directory -Force "$env:APPDATA\microclaw" | Out-Null
Move-Item safety_config.yaml "$env:APPDATA\microclaw\safety_config.yaml"
```

```bash
# macOS / Linux
mkdir -p ~/.config/microclaw
mv safety_config.yaml ~/.config/microclaw/safety_config.yaml
```

Confirm it landed where Microclaw looks, then launch with no flags:

```bash
microclaw check-config      # no path argument: validates the per-user profile
microclaw serve             # browser GUI
microclaw                   # REPL
```

`check-config` prints the path it resolved on its first line, which is the real
check — that is also how to find the right directory if `XDG_CONFIG_HOME` is set
on Linux and `~/.config` is not where Microclaw looks.

Finally, put the same icon on your desktop that the Windows installer creates:

```bash
microclaw install-shortcut
```

**The shortcut points at the environment you run this from.** It resolves
*this* interpreter's `microclaw`, so a shortcut made inside `.venv` breaks if you
delete or rebuild that venv — re-run `install-shortcut` after moving the
environment. `--dry-run` prints the target without writing anything. On macOS and
Linux the command exits cleanly and tells you desktop shortcuts are Windows only;
use `microclaw serve` from a terminal there.

### Two shortcuts worth knowing

`microclaw init` runs the same setup and writes straight to that per-user path,
so it skips the move entirely. Use the explicit `--out` path above when you
juggle several rigs and want the profile beside the checkout; use `init` when
this machine drives one microscope.

`microclaw init --from-example` copies the packaged fictional example for
deliberate hand-authoring. Its limits match no real microscope, so `check-config`
flags any you leave untouched.

You will also need an `ANTHROPIC_API_KEY`: set it in the environment, or let the
browser GUI collect and store it (see [Browser GUI](#browser-gui)). A key stored
from the browser is visible to the REPL too — resolution is environment variable,
then keyring, then `config.toml`, for both.

### Where things live

| What | Where |
|---|---|
| Safety limits | `%APPDATA%\microclaw\safety_config.yaml` |
| API key | your OS credential store, else `%APPDATA%\microclaw\config.toml` |
| The installed program | `%LOCALAPPDATA%\microclaw\env` |
| Desktop shortcut + its icon | your desktop, and `%LOCALAPPDATA%\microclaw` |
| Saved conversations | `*_microclaw_history.json`, in the folder Microclaw ran from |

On macOS and Linux, substitute `~/.config/microclaw` and `~/.local/share/microclaw`.

### CLI options

| Flag | Default | Purpose |
|---|---|---|
| `--safety-config PATH` | the reviewed per-user profile | Hardware-limits file enforced before every tool call. Must carry `reviewed: true`, whether it's the default or an explicit path. |
| `--port N` | `4827` | ZMQ port to reach the running Micro-Manager instance. Match the port set in **Tools → Options**. |
| `--model ID` | `$MICROCLAW_MODEL` or `claude-opus-4-8` | Anthropic model id. The `MICROCLAW_MODEL` environment variable overrides the built-in default; `--model` overrides both. |
| `--profile` / `--no-profile` | off | cProfile the session and print stats on exit. |
| `--save-history` / `--no-save-history` | on | Write the conversation to a timestamped `*_microclaw_history.json` file. |

### Viewing a saved history

Saved `*_microclaw_history.json` files are raw Anthropic messages — readable but
noisy. Render one as a browser transcript (user prompts, the agent's replies, and
every microscope tool call with its result, collapsed by default):

```
microclaw view-history 20260707_143437_microclaw_history.json
```

This writes a self-contained HTML file to your temp directory and opens it. Pass
`--no-browser` to just print the path. The viewer runs entirely locally — nothing
is uploaded. Snap thumbnails are rendered inline; tool calls are collapsed by
default.

### Browser GUI

Drive a session from a chat window instead of the terminal REPL. Same transcript,
plus a composer:

```bash
pip install -e ".[serve]"
microclaw serve                                  # → http://127.0.0.1:8000
```

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

### Desktop shortcut (Windows)

```
microclaw install-shortcut
```

Puts a **Microclaw** icon on the desktop that launches the browser GUI — no
terminal, no flags. The console window it opens *is* the server: it shows the
connection status and any startup error, and closing it stops Microclaw.

The shortcut runs `serve` and nothing else. It is always loopback-only, and it
loads the reviewed safety profile at the per-user path — which must say
`reviewed: true`, or it refuses to start and tells you so.

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
stray browser tab cannot drive the stage. History is written after every turn, and
illumination is shuttered on shutdown, exactly as in the REPL.

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

## Safety configuration

Start with `microclaw inspect-rig` and `microclaw first-launch-setup`, or run
`microclaw init` and accept its setup offer. Restricted setup writes an unreviewed
rig-specific draft. Human-review every declaration and limit, set `reviewed: true`,
run `microclaw check-config PATH` for offline validation, then restart Microclaw.
These limits are enforced before every tool call and cannot be overridden by the
AI. `microclaw init --from-example` is only for deliberate hand-authoring from the
packaged fictional reference.

Safety files use `schema_version: 2`. Stage and named-stage ranges declare the
actuators the profile covers. Every declared axis needs two finite bounds in
guaranteed mode; an explicit `{unbounded: true, reason: "..."}` is retained for
audit or degraded operation but does not provide guaranteed containment.

The file starts with a gate. Nothing runs until a human has read the limits and flipped it:

```yaml
# Microclaw REFUSES TO START until you have gone through this file, set each
# limit for THIS instrument, and changed the line below to `reviewed: true`.
schema_version: 2
reviewed: false
property_authorization:
  mode: guaranteed
  allowed_categorical: []
  denied: []
```

`allowed_categorical` is the reviewed list of discrete (non-continuous)
device properties the AI may write directly. Filter wheels, sliders and turrets
do not belong on it: any device Micro-Manager types as a **StateDevice** has its
own `Label`/`State` auto-classified as categorical at startup, and nothing else
on that device. Shutters are never auto-classified — `Core.Shutter`, an MM
`ShutterDevice`, and anything declared under `illumination` stay on the
illumination gate, which is where a confirmation is required before light
reaches the sample. `microclaw --safety-config … authorization-map` prints the
effective map; auto-classified entries carry `"source": "auto:state-device"`,
declared ones `"source": "declared"`.

Auto-classification fills vacuums only. Naming a device's `Label` **or** `State`
in `allowed_categorical` or `denied` means you own both: declare
the one you will actually write, and the other stays refused. So if you are not
sure whether a driver takes `Label` (string) or `State` (int), declaring one does
not quietly hand you the other.

The following fragments show the limits and a separate worked channel profile
(the shipped values are examples, not defaults):

```yaml
stage:
  x_min: -5000.0
  x_max:  5000.0
  y_min: -5000.0
  y_max:  5000.0
  z_min:  0.0
  z_max:  200.0

camera:
  max_exposure_ms: 5000.0

# Required section; all nine finite positive values are required. Counts are
# frames, times are seconds/ms as named, and bytes are raw
# camera payload estimates. Hard maxima refuse work; confirm_above_* values
# invoke the existing blocking acquisition confirmation below those maxima.
acquisition:
  max_frames: 10000
  max_duration_s: 3600
  max_bytes: 50000000000
  max_illuminated_ms: 600000
  max_session_illuminated_ms: 1800000
  confirm_above_frames: 500
  confirm_above_duration_s: 300
  confirm_above_bytes: 5000000000
  confirm_above_illuminated_ms: 60000

List-backed pycro-manager acquisitions pass their event lists directly to the
engine. They cannot be cancelled mid-run; this is not new, because they never
could be. Generator feeding was measured at 3.14x the list cost and removed;
the evidence and decision are recorded in design/32 §2. Adaptive surveys
still require generators because later events do not exist until a hook
produces them. `max_duration_s` bounds a known-low preflight estimate: exposure
and scheduled start times are included, but unmeasured readout, stage,
autofocus, and filter switching overhead is not. MMStudio MDA is planned and
confirmed from its current settings, and its opaque `run_acquisition()` call
runs to completion.

Adaptive survey reservations cover exactly the planned grid size. A hook may
choose or revisit events within that allowance, but it cannot add an extra
derived revisit beyond the planned frame count; budget exhaustion is logged and
reported as an early stop.

# In guaranteed mode, a channel preset is authorized by both its name and every
# device/property effect Micro-Manager expands it to. Filter wheels, sliders and
# turrets need no declaration — see the note below — so if DAPI only moves those,
# the name is enough. Declare any other discrete effect, e.g. a laser selector:
# Replace the empty property_authorization fragment above with:
property_authorization:
  mode: guaranteed
  allowed_categorical:
    - {device: LaserSelector, property: Label}
  denied: []
channels:
  allowed: [DAPI]

# A missing preset, an unlisted effect, or a preset effect requiring a deferred
# typed executor is refused at startup. Leave `channels` absent until the names
# and all effects have been reviewed on this rig.

# Optional filesystem boundary for paths microclaw writes or serves (acquisition
# data, logs, position-list saves, TIFF exports, and artifact downloads). Unset =
# unrestricted. When set, writes and served files are confined to this directory;
# `..` and symlink escapes are rejected. Local reads remain unrestricted, so this
# is not a sandbox for hook code or plugins.
# workspace_dir: /data/microclaw

# Micro-Manager plugin hooks run arbitrary Java that bypasses the checks above,
# so they have their own two gates.
plugins:
  # Fully-qualified classpaths to forbid. Analyzer (read-only) plugins are
  # allowed by default; list only the ones to block. Normally empty.
  blocked: []
  # Hardware-motion plugins (e.g. autofocus) are gated behind this single flag,
  # not a per-plugin list. Default off; a human flips it to opt in.
  allow_hardware_motion: false
```

## Testing

Unit and agent tests run without Micro-Manager:

```bash
pytest
```

Integration tests require a running MM instance with the Demo configuration:

1. Open Micro-Manager and load `MMConfig_demo.cfg`.
2. Set `MM_RUNNING=1` (and optionally `MM_PORT` if not using the default 4827).
3. Run:

```bash
MM_RUNNING=1 pytest -m integration
```
