# 17 — One-click install and a desktop shortcut for `microclaw serve`

Goal: a microscopist with no Python experience installs Microclaw and launches the
browser GUI by double-clicking an icon on their desktop. No terminal, no `cd`, no
remembering that `--safety-config` goes *before* the subcommand.

## TL;DR

Four increments, shippable in order, each useful alone:

1. **v1 — the icon.** `microclaw/favicon.ico` becomes the single source of truth.
   It is package data (so it survives `pip install`), it is served at
   `GET /favicon.ico` so the browser tab shows it, and it appears at the top of
   the README. A tiny script derives the one PNG variant the README wants —
   Pillow (already a dependency) reads `.ico` and writes `.png`.
2. **v2 — `microclaw init`.** A first-run bootstrap that puts a *per-machine*
   `safety_config.yaml` in the user config directory and refuses to let a session
   start until a human has edited it. This is what makes a zero-argument launch
   possible without weakening the design/14 §6 rule that nobody drives hardware
   under the example's fictional limits.
3. **v3 — `microclaw install-shortcut`.** A subcommand that writes a Windows
   `.lnk` on the desktop, pointing at the installed `microclaw serve`, with the
   icon. No new dependencies.
4. **v4 — `install.bat`.** A downloadable bootstrap that installs Python (via
   `uv`), creates the environment, installs Microclaw, runs `init` and
   `install-shortcut`. The user's whole install experience is: download one file,
   double-click it, edit the safety limits it opens for them.

**Windows only.** Micro-Manager is used on Windows essentially exclusively, and a
macOS `.app` bundle or a Linux `.desktop` entry is a second and third code path
to maintain, test, and get wrong for zero real users. `install-shortcut` exits
with a clear message on any other platform. Developers on macOS keep using
`microclaw serve` from a terminal, which is what they were doing anyway.

The ordering matters: **v2 must land before v3.** A desktop icon that launches a
session is only safe if the "you have not reviewed your hardware limits" gate
fails closed, because after v3 the double-click *is* the entire user interface,
and nothing else stands between a novice and the stage.

---

## What a novice hits today

Reading the current README top to bottom, the user must:

1. install Micro-Manager, and find a checkbox three menus deep to enable ZMQ;
2. have a Python ≥3.10 — the README never says how to get one, and the `.bat`
   snippet quietly assumes a Miniconda install and a conda env named `microclaw`;
3. clone the repo (git! or the "Download ZIP" button and a guess about where);
4. run `pip install -e ".[serve]"` from the right working directory, with the
   right quoting (`.[serve]` unquoted is a glob in zsh);
5. copy `safety_config.example.yaml`, open it in an editor, and understand YAML
   well enough to enter real stage bounds;
6. obtain an Anthropic API key and set an environment variable, or discover that
   the browser will ask for one;
7. type `microclaw --safety-config safety_config.yaml serve`, from a directory
   where that relative path resolves, with the flag before the subcommand.

Steps 1 and 5 are irreducible: only a human can enable the ZMQ server and only a
human knows this rig's travel limits. **Everything else is ours to remove.** Step 6
is already handled by the browser key prompt (design/15 v1b). The rest is this
document.

---

## v1 — the icon, and where it lives

One file, `microclaw/favicon.ico`, checked in beside `serve.html`. It goes
*inside* the package rather than in `docs/` or the repo root because the desktop
shortcut needs to find it after `pip install` from a wheel, when the source tree
may not exist on the machine at all. `importlib.resources` is the only locator
that works in every install mode, and it only sees package data.

```toml
# pyproject.toml
[tool.setuptools.package-data]
microclaw = [
    "history_viewer.html", "serve.html", "transcript.css", "transcript.js",
    "favicon.ico",
]
```

Three consumers, three different requirements:

**The browser tab.** `serve.html` gets a `<link rel="icon" href="/favicon.ico">`
in its `<head>`, and `webserve.build_app` grows a route. Note this cannot go
through `assets.load_page` — that function inlines *text* assets so the page is
self-contained over `file://`; the icon is binary and only `serve` (which has an
HTTP origin) can use it. `history_viewer.html`, which is opened over `file://`,
keeps no favicon.

```python
# microclaw/webserve.py, inside build_app()
from fastapi.responses import Response
from microclaw.assets import icon_bytes

_ICON = icon_bytes()

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    # Immutable for the life of the process; the browser refetches on restart.
    return Response(_ICON, media_type="image/x-icon")
```

**The README.** GitHub will render an `.ico` inside an `<img>` tag, but it will
not scale it well, and Safari's rendering of multi-resolution ICO in a document
context is inconsistent. Derive a PNG instead and commit it next to the README:

```markdown
<img src="docs/microclaw-icon.png" width="96" align="left" alt="">

# Microclaw

An AI agent for Micro-Manager fluorescence microscopy control...
<br clear="left">
```

**The shortcut.** Windows reads the `.ico` directly — no derived variant needed.
It does want 16/32/48px frames inside it (taskbar, desktop, alt-tab); spike Q8
confirms all four frames are present.

> **Known and accepted: the 16px frame is illegible.** `favicon.ico` is a
> photograph, and 16×16 is 256 pixels with no edges left to hold a shape — in a
> browser tab it reads as a grey-orange smudge rather than a lobster. Verified
> this is *not* a serving bug: the route returns 200 with a valid ICO, and neither
> a tighter crop nor a flat silhouette extracted from the red channel survives the
> downscale. The 32/48px frames — the ones the desktop shortcut renders, which is
> the icon the install story actually depends on — are fine. Fixing the tab needs
> a purpose-drawn mark with no sub-pixel features (a bold claw), which is design
> work, not scripting. Deliberately deferred; do not "fix" it by re-deriving from
> the photo.

So the only derived artifact is the README's PNG. Pillow — already a hard
dependency for `image_analysis` — reads ICO and writes PNG (verified, Pillow
12.2), so generation is a committed script rather than a hand-exported file that
silently drifts from the `.ico`:

```python
# scripts/derive_icons.py — run when favicon.ico changes; the PNG is committed.
from PIL import Image

with Image.open("microclaw/favicon.ico") as src:     # largest frame wins
    src.save("docs/microclaw-icon.png")
```

If the supplied `favicon.ico` lacks a 256px frame the README image will be an
upscale; the script should warn rather than ship something soft.

The runtime side is one new helper in `assets.py`, which already owns "read a
thing out of the package":

```python
# microclaw/assets.py
from importlib import resources

def icon_bytes(name: str = "favicon.ico") -> bytes:
    return resources.files("microclaw").joinpath(name).read_bytes()

def materialize_icon(dest: Path, name: str = "favicon.ico") -> Path:
    """Copy a packaged icon to a stable filesystem path.

    A shortcut stores an absolute path to its icon and reads it years later.
    `resources.files()` may hand back a zip member with no real path, and even a
    real path inside site-packages vanishes on `pip uninstall`. So the installer
    copies the icon somewhere it owns, under the user's data directory.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(icon_bytes(name))
    return dest
```

---

## v2 — `microclaw init`: a safety config the launcher can find

### The problem the shortcut creates

`run_session` and `serve` both hard-exit without `--safety-config`, on purpose:
the repo ships only `safety_config.example.yaml`, whose limits "match no real
hardware" (design/14 §6). A desktop shortcut passes no arguments. So either the
shortcut embeds a path — brittle, and wrong the moment the user moves the file —
or we give the *absence* of a flag a safe meaning.

### The design

`--safety-config` gains a default: a per-user path, alongside the `config.toml`
that `credentials.py` already establishes.

```python
# microclaw/paths.py — one place that knows the platform conventions.
def user_config_dir() -> Path:
    """%APPDATA%\\microclaw on Windows, $XDG_CONFIG_HOME/microclaw elsewhere."""
    # Same rule credentials.config_path() applies; that function should be
    # rewritten to call this one, so the two never disagree.

def user_data_dir() -> Path:
    """%LOCALAPPDATA%\\microclaw / ~/.local/share/microclaw — icons, shortcuts."""

def desktop_dir() -> Path:
    """The desktop Explorer actually renders. NOT `Path.home() / "Desktop"`.

    Measured on the lab rig (spike Q1): the Desktop is redirected to a roaming
    profile on a network share, `\\\\isis\\roamingdata\\rieslab\\Desktop`, while
    `Path.home()/"Desktop"` is a local `C:\\Users\\rieslab\\Desktop` that Explorer
    never shows. Writing the .lnk there would report success and produce no
    visible icon — the exact silent failure this function exists to prevent.

    Note the redirect is a roaming profile, not OneDrive (that lives separately at
    C:\\Users\\rieslab\\OneDrive). Sniffing for "OneDrive" in the path, or reading
    %OneDrive%, would have missed this. Ask the shell.

    SHGetKnownFolderPath(FOLDERID_Desktop) via ctypes is authoritative — it is
    what Explorer itself calls. The registry User Shell Folders value agreed with
    it here, and `~/Desktop` did not.
    """

def default_safety_config() -> Path:
    return user_config_dir() / "safety_config.yaml"
```

`microclaw init` copies the example there and opens it in the platform editor:

```python
def init(args):
    dest = default_safety_config()
    if dest.exists() and not args.force:
        print(f"Already present: {dest}"); return dest
    text = resources.files("microclaw").joinpath("safety_config.example.yaml").read_text()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    print(f"Wrote {dest}\nEdit EVERY limit for this microscope, then set `reviewed: true`.")
    if not args.no_edit:
        _open_in_editor(dest)
    return dest


def _open_in_editor(path: Path) -> None:
    """os.startfile() RAISES if .yaml has no registered handler.

    A clean Windows box very likely has no association for .yaml — nothing in the
    base OS claims it. An unhandled OSError here aborts `init` at exactly the
    moment the user needs the file in front of them. Fall back to notepad, which
    is always present. Spike Q6 reports this machine's .yaml association.
    """
    try:
        os.startfile(path)
    except OSError:
        subprocess.Popen(["notepad.exe", str(path)])
```

This means `safety_config.example.yaml` moves into the package (it is currently
repo-root-only, so a wheel install cannot find it to copy).

### The gate

The example file grows one key at the top:

```yaml
# ── READ THIS ─────────────────────────────────────────────────────────────
# The limits below are FICTIONAL. They match no real microscope. Microclaw
# will refuse to start until you have edited them for THIS instrument and
# changed the line below to `reviewed: true`.
reviewed: false
```

`SafetyConstraints.from_yaml` ignores unknown top-level keys, so `reviewed` needs
no dataclass change; the gate lives in `config.py`, above the parser:

```python
# microclaw/config.py
def load_safety_config(path: str | None) -> SafetyConstraints:
    p = Path(path) if path else default_safety_config()
    if not p.exists():
        sys.exit(f"No safety config at {p}. Run `microclaw init` first.")
    cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    # An explicit path is a deliberate act by someone who typed it; the default
    # path is what a double-clicked icon loads, sight unseen. Gate the default.
    # (Gating both is defensible and cheap — decide before v3 ships.)
    if not cfg.get("reviewed", False):
        sys.exit(
            f"{p} has not been reviewed.\n"
            "Its limits are the example's and match no real hardware. Edit the "
            "file for THIS microscope, then set `reviewed: true` at the top."
        )
    return SafetyConstraints.from_yaml(str(p))
```

Fails closed on every path that reaches it: a missing key, a corrupt file, a
`reviewed: false` left in place. The one thing to get right is that the message
lands somewhere the user can *read* it — see the console-window discussion in v3.

Open question worth deciding explicitly: does an **explicit** `--safety-config`
also require `reviewed: true`? The stub above says no, to keep every existing
lab `safety_config.yaml` working unchanged. The alternative is a one-line edit for
existing users and a stronger invariant. My recommendation is to gate both and
take the one-line migration, because "the file I typed the path to" and "the file
the icon loaded" being governed by different rules is exactly the kind of
asymmetry that gets forgotten.

---

## v3 — `microclaw install-shortcut`

A subcommand, not an installer script, so it works identically whether the user
came through `pip`, conda, or v4's bootstrap — the shortcut points at whatever
`microclaw` is currently on `sys.path`.

### What the shortcut must launch

Not `microclaw` on `PATH` — a shortcut has no shell and no activated environment.
Resolve the console script *inside the running interpreter's* environment:

```python
def _launcher() -> tuple[str, list[str]]:
    """(target, args) for the current environment's `microclaw serve`.

    Two layouts, and they differ (spike Q4). In a Windows venv, python.exe lives
    *in* Scripts\\, so the console script is its sibling. In a conda env — what
    the lab machine runs — python.exe is at the env root and the console script is
    one level down in Scripts\\. Checking only the sibling silently falls through
    to the `-m` fallback on conda, which works but hardcodes the interpreter path
    into the shortcut and loses the console script's own error handling.
    """
    d = Path(sys.executable).parent
    for exe in (d / "microclaw.exe", d / "Scripts" / "microclaw.exe"):
        if exe.exists():
            return str(exe), ["serve"]
    # No console script (odd, but possible): drive the module directly.
    return sys.executable, ["-m", "microclaw", "serve"]
```

Measured: `...\envs\microclaw\microclaw.exe` absent, `...\envs\microclaw\Scripts\microclaw.exe`
present. `python -m microclaw --help` also works, so the fallback is viable.

`serve` and nothing else. Never `--allow-remote`; never a `--host`. The shortcut
is the loopback GUI, by construction.

### Windows (`.lnk`) — the only target

No `pywin32` dependency: shell out to PowerShell's `WScript.Shell` COM object,
which exists on every supported Windows. (Spike Q2 confirms it instantiates, and
that `powershell -Command` is not blocked by ExecutionPolicy — that policy gates
script *files*, not `-Command`, so no `-ExecutionPolicy Bypass` is needed.)

Paths are passed as **environment variables**, never interpolated into the script
text. Interpolating `'{lnk}'` into a PowerShell single-quoted string breaks on any
path containing an apostrophe (`C:\Users\O'Brien\Desktop`) and is a
command-injection sink, not merely a quoting bug. `$env:` lookups are data.
Spike Q3 builds a shortcut inside a directory named `it's a lab` both ways and
checks the round-trip.

```python
_PS = """
$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:MC_LNK)
$s.TargetPath       = $env:MC_TARGET
$s.Arguments        = $env:MC_ARGS
$s.WorkingDirectory = $env:MC_WORKDIR
$s.IconLocation     = $env:MC_ICON
$s.Description      = 'Microclaw - AI agent for Micro-Manager'
$s.Save()
"""

def _windows(target, args, icon, workdir, dest) -> Path:
    lnk = dest / "Microclaw.lnk"
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS],
        check=True, capture_output=True,
        env={**os.environ,
             "MC_LNK": str(lnk), "MC_TARGET": str(target),
             "MC_ARGS": " ".join(args), "MC_WORKDIR": str(workdir),
             "MC_ICON": f"{icon},0"},
    )
    return lnk
```

**The console window.** The shortcut targets `microclaw.exe`, so a black console
appears and stays. That is *good*: it is the server's status window, it shows
"Connecting to Micro-Manager…", and closing it stops the server — a stop control
a novice will find. The alternative, `pythonw.exe`, hides every error including
"could not connect" and "your safety config is unreviewed". Keep the console.

But `sys.exit("…")` in a console spawned by a shortcut prints its message and
closes the window in the same frame — the user sees a flash. So the launcher marks
itself, and `main()` holds the window open on any exit:

```python
# microclaw/__main__.py
def main():
    ...
    if os.environ.get("MICROCLAW_FROM_SHORTCUT") == "1":
        atexit.register(lambda: input("\nPress Enter to close this window..."))
```

A `.lnk` cannot set environment variables, so `MICROCLAW_FROM_SHORTCUT=1` is set
by a two-line `Microclaw.cmd` in the user data directory, which the `.lnk` targets
instead of the exe. That also buys a stable target path that survives a
`uv pip install --upgrade`. Spike Q5 confirms the variable reaches Python through
the wrapper from a path containing a space; spike Q9 (`--desktop`) puts a real
shortcut on the desktop whose target prints a simulated "could not connect" error
and pauses, so the console-window behaviour can be *seen* rather than assumed.

### Not macOS, not Linux

`install-shortcut` on any other platform prints "Desktop shortcuts are Windows
only; run `microclaw serve` from a terminal" and exits 0 — not a traceback, and
not a failure, because v4's installer would otherwise abort on a developer's Mac.

Consequences for v1: the ICNS derive step is dropped. `derive_icons.py` produces
only `docs/microclaw-icon.png` for the README, and `favicon.ico` serves the
browser tab and the `.lnk` directly.

### The subcommand

```python
sc = sub.add_parser("install-shortcut", help="Put a Microclaw launcher on the desktop.")
sc.add_argument("--dest", type=Path, default=None, help="Override the desktop directory.")
sc.add_argument("--dry-run", action="store_true", help="Print what would be written.")
sc.add_argument("--remove", action="store_true", help="Delete a previously installed shortcut.")
```

`--dry-run` is what makes this testable off-platform — it prints target, args,
icon path and destination without touching PowerShell. `_windows()` is a pure
function from `(target, args, icon, workdir, dest)` to a written file, so the path
computation can be unit-tested on a maintainer's Mac with a monkeypatched `HOME`;
the COM call itself is verified by the spike and by hand.

`install-shortcut` also materializes the icon and writes the `.cmd` wrapper:

```python
icon = materialize_icon(user_data_dir() / "microclaw.ico")
```

---

## v4 — the bootstrap installer

The remaining barrier is "get a Python and a virtual environment". Three options:

| | How | Cost |
|---|---|---|
| **A. status quo** | user installs Miniconda, makes an env, `pip install -e .` | six steps, three ways to get it wrong |
| **B. `uv` bootstrap script** | one `.bat` fetches `uv`, which fetches CPython, makes a venv, installs Microclaw | one download, one double-click |
| **C. PyInstaller `.exe`** | freeze everything into one binary | no Python needed at all; but pycro-manager + scikit-image freeze badly, and every release needs a signed rebuild |

**Decided: B.** `uv` is a single static binary that provisions its own CPython, so
it does not care what Python (if any) is on the machine, needs no admin rights,
and is fast on a lab network. C is genuinely the best *end-user* experience and is
worth revisiting once the app is otherwise stable — but it is a release-engineering
project (code signing, a build matrix, freezing a Java bridge), not an afternoon.

### uv for developers too

Since `uv` is already the runtime for the end-user installer, make it the
documented developer path as well and retire conda from the README:

```bash
uv venv --python 3.12          # creates .venv, downloads CPython if needed
uv pip install -e ".[serve,test]"
uv run pytest
```

One toolchain to document, one to debug, and no more "which env am I in". This is
a README and CONTRIBUTING change, not a code change — nothing in the package
depends on how the environment was built. A conda env that already exists keeps
working; `uv pip install -e .` runs happily inside one. Spike Q7 reports whether
a conda env is active on the lab machine and whether `uv` is already on PATH.

```bat
:: install.bat — user downloads this one file and double-clicks it.
@echo off
setlocal
set "MC_HOME=%LOCALAPPDATA%\microclaw"

echo Installing Microclaw. This takes a few minutes and needs no admin rights.
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex" || goto :fail
set "UV=%USERPROFILE%\.local\bin\uv.exe"

"%UV%" venv --python 3.12 "%MC_HOME%\env"                                  || goto :fail
"%UV%" pip install --python "%MC_HOME%\env\Scripts\python.exe" "microclaw[serve] @ git+https://github.com/zacsimile/microclaw" || goto :fail

"%MC_HOME%\env\Scripts\microclaw.exe" install-shortcut                     || goto :fail
"%MC_HOME%\env\Scripts\microclaw.exe" init                                 || goto :fail

echo.
echo Done. A Microclaw icon is on your desktop.
echo Edit the safety limits that just opened BEFORE you launch it.
pause & exit /b 0

:fail
echo. & echo Install failed. Copy the messages above and send them to the maintainer.
pause & exit /b 1
```

`install-shortcut` runs *before* `init`, so the editor that `init` opens is the
last thing on screen and the final instruction is the one the user acts on.

One wrinkle to check on a fresh machine: a `.bat` downloaded from a browser
carries a Mark-of-the-Web alternate data stream, and Windows may show a SmartScreen
"protected your PC" prompt on double-click. The README should say to expect it and
which button to press ("More info" → "Run anyway"), because a novice who sees that
banner stops.

Two things this script must not do: run as administrator (nothing here needs it,
and a novice granting admin to a downloaded `.bat` is a habit worth not teaching),
and install into the system Python. Everything lives under `%LOCALAPPDATA%`,
uninstallable by deleting one folder.

---

## Rewriting the README

The current Quick Start optimizes for a developer with a terminal. Invert it:

- **New top section, "Install (Windows)"**: download `install.bat`, double-click,
  edit the safety file it opens, double-click the desktop icon. Four sentences.
  Keep the Micro-Manager ZMQ checkbox as step 0, with a screenshot — it is the
  single most likely place to get stuck, and no script can do it for the user.
- Fold the existing `pip install -e .` instructions into a **"Install from source
  (developers)"** section further down, rewritten around `uv venv` / `uv pip
  install -e ".[serve,test]"`.
- **Delete the hand-rolled `.bat` section.** It hardcodes an API key in a file
  (the browser prompt + keyring supersede it), assumes a conda layout, and is
  exactly what `install-shortcut` replaces. Leaving a worse parallel path in the
  README is how users end up on it.
- Add a **"Where things live"** table — safety config, API key, history JSON,
  shortcut — because the one question a non-technical user cannot answer is
  "where did it put my file?"

---

## Spike results

`design/17-install-spike.py`, run on the lab rig: Windows 10.0.26100, PowerShell
5.1, miniforge conda env `microclaw`, Python 3.11.15, repo at `D:\Code\microclaw`.
Q9's shortcut was confirmed by eye — the icon appeared on the desktop and `--clean`
removed it.

**Q1 — the Desktop is redirected.** `SHGetKnownFolderPath` and the registry both
say `\\isis\roamingdata\rieslab\Desktop`; `Path.home()/"Desktop"` says
`C:\Users\rieslab\Desktop`. The shell-folder lookup is **mandatory**, not
optional. The cause is a roaming profile on a network share, not OneDrive — a
`"OneDrive" in path` heuristic would have missed it. Two consequences:

- The `.lnk` lives on a **UNC path**. It wrote and deleted fine, and Explorer
  renders it. Do not, however, set the shortcut's `WorkingDirectory` to the
  desktop: `cmd.exe` refuses UNC working directories ("UNC paths are not
  supported") and would print a warning before every launch. It is already set to
  `user_data_dir()` under `%LOCALAPPDATA%`, which is local. Keep it that way.
- A network desktop can be **unavailable** at install time. `install-shortcut`
  should catch the write error and say which path it tried, rather than traceback.

**Q4 — the console script is in `Scripts\`, not beside `python.exe`.** Under
conda, `sys.executable` is at the env root. The single-location `_launcher()` in
the first draft of this document would have silently used the `-m` fallback.
Fixed above; both layouts are now probed.

**Q2, Q3, Q5 — the `.lnk` machinery works as designed.** `WScript.Shell`
instantiates; `powershell -Command` runs without an ExecutionPolicy override
(this box happens to be `Unrestricted`, but the point stands: policy gates script
*files*). The env-var form round-trips `TargetPath` and `Arguments` through a
directory named `it's a lab`, and the naive single-quote interpolation **fails
there**, as predicted — keep the env-var form. The `.cmd` wrapper propagates
`MICROCLAW_FROM_SHORTCUT` across a path containing a space.

**Q6 — `.yaml` opens in VS Code here**, so `os.startfile` will not raise on this
machine. Keep the `notepad.exe` fallback anyway: the association is an artifact of
this developer box having VS Code installed, and the target audience's machine
will not.

**Q7 — `uv` is absent and a conda env is active.** Confirms `install.bat` must
fetch `uv` itself, and that the developer-install migration is a real (if
optional) change rather than a no-op. No install path on this machine contains a
space, but `install.bat` should quote regardless.

**Q8 — not answered.** The lab clone predates the `favicon.ico` commit, so the
probe skipped. Locally the file carries 16/32/48/256px RGBA frames, which is
everything Windows and the README need; re-run Q8 on the rig after pulling.

---

## Safety review

This work lowers the activation energy for driving a microscope from a laptop.
Three things stand between the double-click and a moving stage, and all three
must fail closed:

1. **The `reviewed:` gate.** Absent, false, or unparseable ⇒ exit. The only way
   to pass it is for a human to open the file and change a line. Tested.
2. **The console window stays open** long enough for that refusal to be read. An
   invisible failure mode ("I clicked it and nothing happened") is what pushes
   people toward `pythonw` and toward guessing.
3. **The shortcut never carries `--allow-remote`, `--host`, or a bare
   `--safety-config` path.** Anything reachable by double-click is loopback-only,
   under this machine's reviewed limits.

Micro-Manager's ZMQ checkbox remains a manual step, which is a fourth, accidental
gate: a machine that has never been deliberately configured cannot be driven.
Do not automate it.

## Test plan

`design/17-install-spike.py` has been **run on the lab rig** (Windows, PowerShell
5.1, miniforge conda env `microclaw`, Python 3.11) — see "Spike results" above.
Re-run it on any new machine before trusting `install-shortcut` there.

In code:

- `paths.py`: monkeypatch `APPDATA`/`LOCALAPPDATA`/`USERPROFILE`, assert every
  path. Whether `desktop_dir` keeps its shell-folder lookup depends on spike Q1.
- `config.load_safety_config`: missing file, `reviewed: false`, missing key,
  malformed YAML, and the happy path — each asserted to `SystemExit` or succeed.
- `install-shortcut --dry-run`, asserting the launcher resolves to the console
  script inside `sys.executable`'s directory, that args are exactly `["serve"]`,
  and that neither `--allow-remote` nor `--host` can appear. Runs on any OS.
- `install-shortcut` on non-Windows exits 0 with the "Windows only" message.
- `assets.icon_bytes()` returns a valid ICO (`Image.open(BytesIO(...))`), and
  `docs/microclaw-icon.png` matches what `derive_icons.py` produces from the
  committed `.ico` — a CI check that keeps the two from drifting.
- Manual, on Windows: double-click the shortcut with Micro-Manager *not* running,
  and confirm the "Could not connect" message is readable before the window
  closes. Then again with an unreviewed safety config, for the other refusal.

## Increment summary

| | Ships | Depends on |
|---|---|---|
| v0 | `17-install-spike.py` — **done**, see Spike results | — |
| v1 | icon in package, browser tab, README header, `derive_icons.py` | — |
| v2 | `paths.py`, `microclaw init`, `reviewed:` gate, example in package | — |
| v3 | `microclaw install-shortcut` (Windows only) | v1, **v2**, v0 |
| v4 | `install.bat`, uv dev install, README rewrite | v1–v3 |

v1 and v2 are independent and can land in either order. v3 without v2 ships a
one-click hardware launcher with no reviewed-limits gate, which is the one
sequencing mistake available here.
