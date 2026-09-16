"""A desktop shortcut that launches `microclaw serve` (design/17 v3).

Windows only. Micro-Manager is used on Windows essentially exclusively, and a
macOS .app bundle or a Linux .desktop entry would be a second and third code path
to maintain for no real users; elsewhere this exits cleanly and says to run
`microclaw serve` from a terminal.

Three things here are load-bearing, and each was measured on the lab rig rather
than assumed (design/17 spike):

* **The desktop comes from the shell, not from `~/Desktop`.** See `paths.desktop_dir`.
* **Paths reach PowerShell as environment variables, never interpolated.** A path
  containing an apostrophe (`C:\\Users\\O'Brien`) breaks a single-quoted PowerShell
  string, and interpolation is a command-injection sink besides.
* **The shortcut targets a .cmd wrapper, not the .exe.** A .lnk cannot set an
  environment variable, and `MICROCLAW_FROM_SHORTCUT` is what tells `main()` to
  hold the console open so a novice can read why startup failed. The wrapper also
  gives a stable target path that survives `pip install --upgrade`.

The shortcut launches `serve` and nothing else: never `--allow-remote`, never a
`--host`. Anything reachable by double-click is the loopback GUI, under this
machine's reviewed safety limits.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from microclaw.assets import materialize_icon
from microclaw.paths import desktop_dir, user_data_dir

LNK_NAME = "Microclaw.lnk"
CMD_NAME = "Microclaw.cmd"
ICO_NAME = "microclaw.ico"
MANAGED_STATE_NAME = "update-state.json"

#: Set by the .cmd wrapper. `main()` reads it to keep the console open on exit.
FROM_SHORTCUT_ENV = "MICROCLAW_FROM_SHORTCUT"
UPDATE_RESTART_ENV = "MICROCLAW_UPDATE_RESTART"

# Paths arrive as $env: lookups, which PowerShell treats as data. Interpolating
# them into the script text would break on an apostrophe and invite injection.
_PS_CREATE = """
$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:MC_LNK)
$s.TargetPath       = $env:MC_TARGET
$s.Arguments        = $env:MC_ARGS
$s.WorkingDirectory = $env:MC_WORKDIR
$s.IconLocation     = $env:MC_ICON
$s.Description      = 'Microclaw - AI agent for Micro-Manager'
$s.Save()
"""


class ShortcutError(Exception):
    """Creating or removing the shortcut failed, with a reason worth printing."""


def supported() -> bool:
    return os.name == "nt"


def launcher() -> tuple[str, list[str]]:
    """(target, args) for *this* environment's `microclaw serve`.

    A shortcut has no shell and no activated environment, so `microclaw` on PATH
    is not good enough — resolve the console script inside the running
    interpreter's environment.

    Two layouts, and they differ (spike Q4). In a Windows venv python.exe lives
    *in* Scripts\\, so the console script is its sibling. Under conda — what the
    lab machine runs — python.exe sits at the env root and the script is one level
    down. Checking only the sibling silently falls through to the `-m` form.
    """
    d = Path(sys.executable).parent
    for exe in (d / "microclaw.exe", d / "Scripts" / "microclaw.exe"):
        if exe.exists():
            return str(exe), ["serve"]
    return sys.executable, ["-m", "microclaw", "serve"]


def _wrapper_text(target: str, args: list[str]) -> str:
    quoted = " ".join(f'"{a}"' if " " in a else a for a in args)
    # CRLF: this is a batch file, and `set X=1\n` with a bare LF can carry the
    # newline into the value on older cmd.exe.
    return (
        "@echo off\r\n"
        "rem Written by `microclaw install-shortcut`. Safe to delete.\r\n"
        f"set {FROM_SHORTCUT_ENV}=1\r\n"
        f'"{target}" {quoted}\r\n'
    )


def managed_layout_present(data: Path | None = None) -> bool:
    """Whether launcher ownership belongs to install.bat.

    ``update-state.json`` is the single opt-in marker for managed updates.  Its
    absence deliberately keeps conda, embedded-Python, and developer installs
    on the legacy wrapper path.
    """
    return ((Path(data) if data is not None else user_data_dir()) / MANAGED_STATE_NAME).is_file()


def plan(dest: Path | None = None) -> dict:
    """Everything `install` would write, without writing it. Pure; testable anywhere."""
    target, args = launcher()
    data = user_data_dir()
    desktop = Path(dest) if dest else desktop_dir()
    return {
        "target": target,
        "args": args,
        "lnk": desktop / LNK_NAME,
        "cmd": data / CMD_NAME,
        "icon": data / ICO_NAME,
        "workdir": data,
    }


def _powershell(script: str, env: dict) -> None:
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            env={**os.environ, **env},
        )
    except FileNotFoundError as e:  # no powershell.exe at all
        raise ShortcutError(f"PowerShell is not available: {e}") from e
    if r.returncode != 0:
        raise ShortcutError((r.stderr or r.stdout).strip() or "PowerShell failed")


def install(dest: Path | None = None, dry_run: bool = False) -> dict:
    """Write the icon and .lnk, plus the legacy wrapper for unmanaged installs."""
    p = plan(dest)
    if dry_run:
        return p

    # Checked first, so a failure here leaves nothing behind: a redirected desktop
    # can be an offline network share, which is exactly when this trips.
    if not p["lnk"].parent.is_dir():
        raise ShortcutError(f"Desktop directory is not reachable: {p['lnk'].parent}")

    # Machine-local, both of them: the lab rig's desktop is a network share, and a
    # shortcut whose target lives on a share breaks whenever the network does.
    materialize_icon(p["icon"])
    p["cmd"].parent.mkdir(parents=True, exist_ok=True)
    if not managed_layout_present(p["workdir"]):
        # newline="" or Windows text mode expands the \n of this already-CRLF
        # text and the file lands with \r\r\n -- which is precisely the
        # hazard _wrapper_text's CRLF comment exists to avoid, reintroduced by
        # the write.  Invisible on macOS and Linux, where "\n" needs no
        # translation; found by the first Windows CI run, 2026-09-16.
        p["cmd"].write_text(
            _wrapper_text(p["target"], p["args"]), encoding="utf-8", newline="",
        )

    _powershell(
        _PS_CREATE,
        {
            "MC_LNK": str(p["lnk"]),
            "MC_TARGET": str(p["cmd"]),
            "MC_ARGS": "",  # the wrapper carries them
            "MC_WORKDIR": str(p["workdir"]),
            # ",0" selects the first icon group in the file.
            "MC_ICON": f"{p['icon']},0",
        },
    )
    return p


def remove(dest: Path | None = None) -> list[Path]:
    """Delete what `install` wrote. Missing files are not an error."""
    p = plan(dest)
    gone = []
    keys = ("lnk", "icon") if managed_layout_present(p["workdir"]) else ("lnk", "cmd", "icon")
    for key in keys:
        try:
            p[key].unlink()
            gone.append(p[key])
        except FileNotFoundError:
            pass
        except OSError as e:
            raise ShortcutError(f"Could not remove {p[key]}: {e}") from e
    return gone


def pause_on_exit() -> None:
    """Hold a shortcut-spawned console open so its last message can be read.

    `sys.exit("...")` in a console the shortcut created prints and closes in the
    same frame — the user sees a flash. The console is deliberately visible (not
    pythonw): it is the server's status window, closing it stops the server, and
    it is where "Could not connect to Micro-Manager" and "your safety config is
    unreviewed" appear.
    """
    if os.environ.get(FROM_SHORTCUT_ENV) != "1":
        return
    import atexit

    def _wait():
        if os.environ.get(UPDATE_RESTART_ENV) == "1":
            return
        try:
            input("\nPress Enter to close this window...")
        except (EOFError, KeyboardInterrupt):
            pass

    atexit.register(_wait)
