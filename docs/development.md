# Developing Microclaw

[`uv`](https://docs.astral.sh/uv/) is the supported toolchain; it downloads a
CPython for you, so nothing needs to be installed first.

```bash
uv venv --python 3.12
uv pip install -e ".[serve,test,ilastik]"
uv run pytest
```

`ilastik` is optional for *running* Microclaw but belongs in a development
install: without `h5py`, `tests/test_ilastik_adapter.py` **skips** rather than
fails, so the suite stays green while quietly testing less. Watch the skip count,
not just the pass count.

An existing conda environment works fine too — `pip install -e
".[serve,test,ilastik]"` inside it does the same thing — provided it is Python
3.12 or newer, which `requires-python` now enforces.

If a `conda` environment is active, `uv pip install` targets **that**
environment rather than the local `.venv`; `conda deactivate` first, or use
`uv run`, which always resolves to `.venv`.

Under `uv`, prefix the commands below with `uv run` (`uv run microclaw ...`,
`uv run pytest`). In an activated conda environment they work as written.

**Then build a safety profile for this rig.** Micro-Manager must be running with
**Tools → Options → Run pycro-manager server on port 4827** ticked. To check that
without starting anything:

```bash
microclaw check-bridge          # exits 0 only when the bridge answers a real request
```

Start the same restricted in-app setup used by the installer:

```bash
microclaw --setup-write-security-config serve
```

To keep raw enumeration evidence separately, run the read-only inventory command:

```bash
microclaw inspect-rig --out rig-inventory
```

## Make it the default, and get the desktop icon

Setup writes the per-user default after showing the destination and exact YAML.
Close that one-time server and launch normally:

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

You will also need an `ANTHROPIC_API_KEY`: set it in the environment, or let the
browser GUI collect and store it (see [the browser GUI](browser-gui.md)). A key stored
from the browser is visible to the REPL too — resolution is environment variable,
then keyring, then `config.toml`, for both.

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
