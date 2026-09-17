# MicroClaw

<!-- The icon floats beside the description, never beside the <h1>: GitHub gives
     h1/h2 a full-width bottom border, and that rule would cut across the image. -->
<img src="docs/microclaw-icon.png" alt="" width="120" align="left" hspace="16" vspace="4">

An AI agent for [Micro-Manager](https://micro-manager.org) microscopy control. 
Describe your acquisition protocol in plain language; Microclaw translates it 
into [Pycro-Manager](https://github.com/micro-manager/pycro-manager) tool 
calls while the GUI responds in real time. Acquisition routines can be compiled 
to standalone Pycro-Manager scripts for re-use outside of the agent.

<br clear="left">


> [!CAUTION]
> The hardware safety features are not comprehensive. Always be mindful of what 
> your microscope is doing. Use at your own risk.

> [!IMPORTANT]
> **PREVIEW ONLY** This package is provided as a preview for feedback only. 
> Specifications (e.g. safety_config.yaml) are unstable and the design is subject to change.
>
> Suitable for experiments, exploration and prototypes. It is NOT suitable for production use at this time.

## Architecture

```
User (natural language) → Agent → Tools → Safety Guard → Microscope Controller → PM ZMQ → MM GUI
```

- **Agent**: `claude-opus-4-8` via Anthropic API with tool use and prompt caching.
- **Safety**: User-defined `safety_config.yaml` enforced as a hard gate before every hardware call. The AI cannot override these limits.
- **Backend**: Pycro-Manager (ZMQ on port 4827). Open Micro-Manager normally. Microclaw connects to the running instance.

## Documentation

| | |
|---|---|
| [Installing on Windows](docs/install-windows.md) | The installer, security-bounds setup, automatic updates, upgrading, uninstalling |
| [Using Microclaw](docs/using-microclaw.md) | What a session is, what leaves the machine, skills, hooks, compiling a session to a standalone script |
| [Safety configuration](docs/safety-config.md) | The `safety_config.yaml` schema, every gate, and what a section's absence means |
| [The browser GUI](docs/browser-gui.md) | The chat interface, the desktop shortcut, API keys, remote access |
| [CLI reference](docs/cli-reference.md) | Flags and subcommands, the history viewer, where files live |
| [Developing Microclaw](docs/development.md) | Source install, the test suite, building a safety profile by hand |

## Quick start (Windows)

**Windows 10 or newer.** Microclaw has been tested on Windows 10 and Windows 11
only. `uv`, which the installer uses, documents support back to Windows 8, but
nothing older than Windows 10 has been tried here, so that is all this project
guarantees.

You do not need Python; the installer brings its own.

1. Install [Micro-Manager 2.0](https://micro-manager.org/Download_Micro-Manager_Latest_Release)
   (a build later than 2026.06.26, for full functionality).
2. On this repository's page, click **Code → Download ZIP**, then right-click the
   download and choose **Extract All**.
3. Double-click **`install.bat`** in the extracted folder. It installs into
   `%LOCALAPPDATA%\microclaw` without administrator rights and puts a
   **Microclaw** icon on your desktop.
4. When it asks, open Micro-Manager and tick **Tools → Options → Run
   pycro-manager server on port 4827**. The installer checks the bridge, then
   opens restricted setup in your browser, where you record this rig's security
   bounds. You will need an Anthropic API key from console.anthropic.com.
5. Double-click the **Microclaw** icon.

**When you are finished, press Ctrl+C in the console window to stop the server.**
Closing the browser tab stops nothing: the server keeps running, keeps its
connection to Micro-Manager, and keeps the installed files locked against an
upgrade.

[Installing on Windows](docs/install-windows.md) has the full walkthrough,
including what to do when the bridge does not answer, when security bounds
already exist, and how to run setup by hand.

Microclaw keeps itself up to date after that — see
[Automatic updates](docs/install-windows.md#automatic-updates).

## Running from source

[`uv`](https://docs.astral.sh/uv/) is the supported toolchain; it downloads a
CPython for you, so nothing needs to be installed first.

```bash
uv venv --python 3.12
uv pip install -e ".[serve,test,ilastik]"
uv run pytest
```

Then build a safety profile for this rig and start the GUI:

```bash
microclaw check-bridge                        # exits 0 only when the bridge answers
microclaw --setup-write-security-config serve # one-time, records security bounds
microclaw serve                               # browser GUI, Ctrl+C to stop
```

[Developing Microclaw](docs/development.md) covers the rest — conda environments,
the ilastik extra, the desktop shortcut, and the integration tests.

## Citation

If Microclaw is useful in your work, please cite the preprint:

> Marin Z, Abouelezz A, Castillo Duque de Estrada NM, Schweighofer SV, Hauser F,
> Koestinger LT, Manjunath A, Stuurman N, Schueder F, Ries J.
> *Microscope control with a natural language agent.* bioRxiv 2026.
> doi:[10.64898/2026.09.15.751723](https://doi.org/10.64898/2026.09.15.751723)

## Contributing

Contributions are welcome. Every commit needs a `Signed-off-by` trailer
certifying the [Developer Certificate of Origin](https://developercertificate.org/),
which `git commit --signoff` adds for you. Contributors keep copyright in their
own work — the collective notice in `LICENSE` is not an assignment. Do not add
third-party code, data, papers or images unless their license permits
redistribution here and the attribution is recorded in
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full text.

## License

Microclaw is released under the [BSD 3-Clause license](LICENSE).

Dependencies are separate works under their own licenses and are installed from
PyPI rather than vendored here; the declared license of each direct dependency
is listed in [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md). A redistributor
of a binary or a packaged environment — including the `install.bat` environment —
must comply with the notices of the complete resolved dependency set, not only
the direct ones recorded there.
