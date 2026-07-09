"""design/17 spike — resolve the Windows unknowns behind `install-shortcut`.

Run this ON THE WINDOWS LAB MACHINE, inside the environment microclaw is
installed into:

    python design/17-install-spike.py            # read-only probes
    python design/17-install-spike.py --desktop  # + drop a real test shortcut
    python design/17-install-spike.py --clean    # remove the test shortcut

Stdlib only (plus an optional Pillow probe), so it runs even in a bare
interpreter. It writes nothing outside %TEMP% unless --desktop is passed.

Each section answers a question design/17 currently guesses at. The output is
meant to be pasted back verbatim.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

if os.name != "nt":
    sys.exit("This spike answers Windows-only questions. Run it on the lab machine.")

# Below the guard: these do not exist (winreg) or are useless (ctypes.wintypes,
# ctypes.windll) off Windows, and importing them first turns a clear message into
# an ImportError traceback.
import ctypes  # noqa: E402
import winreg  # noqa: E402
from ctypes import wintypes  # noqa: E402

FAIL = []


def head(n, title):
    print(f"\n{'=' * 72}\nQ{n}. {title}\n{'=' * 72}")


def ok(msg):
    print(f"  [ok]    {msg}")


def info(msg):
    print(f"  [info]  {msg}")


def bad(msg):
    print(f"  [FAIL]  {msg}")
    FAIL.append(msg)


# ---------------------------------------------------------------------------
# Q1. Where is the Desktop, really?
#
# design/17 worries the Desktop is OneDrive-redirected and that a .lnk written
# to Path.home()/"Desktop" would land somewhere Explorer never renders. Three
# sources of truth; if they agree, the doc's registry branch is dead code.
# SHGetKnownFolderPath is the authoritative one — it is what Explorer itself
# calls. The registry read is the fallback design/17 proposes.
# ---------------------------------------------------------------------------
FOLDERID_Desktop = "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}"


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_byte * 8),
    ]


def known_folder(guid_str: str) -> str:
    guid = GUID()
    hr = ctypes.windll.ole32.CLSIDFromString(ctypes.c_wchar_p(guid_str), ctypes.byref(guid))
    if hr != 0:
        raise OSError(f"CLSIDFromString failed: 0x{hr & 0xFFFFFFFF:08x}")
    out = ctypes.c_wchar_p()
    hr = ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out))
    if hr != 0:
        raise OSError(f"SHGetKnownFolderPath failed: 0x{hr & 0xFFFFFFFF:08x}")
    try:
        return out.value
    finally:
        ctypes.windll.ole32.CoTaskMemFree(out)


def registry_desktop() -> str | None:
    key = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            raw, _ = winreg.QueryValueEx(k, "Desktop")
        return os.path.expandvars(raw)
    except OSError:
        return None


def q1_desktop():
    head(1, "Desktop location: is it redirected (OneDrive) or plain?")
    api = reg = home = None
    try:
        api = known_folder(FOLDERID_Desktop)
        ok(f"SHGetKnownFolderPath : {api}")
    except OSError as e:
        bad(f"SHGetKnownFolderPath : {e}")
    reg = registry_desktop()
    info(f"registry User Shell Folders: {reg}")
    home = str(Path.home() / "Desktop")
    info(f"Path.home()/'Desktop'      : {home}")

    if api:
        same = os.path.normcase(os.path.normpath(api)) == os.path.normcase(os.path.normpath(home))
        if same:
            ok("Desktop is NOT redirected — `Path.home()/'Desktop'` is correct. "
               "design/17 can drop the registry branch entirely.")
        else:
            bad("Desktop IS redirected. The shell-folder lookup is MANDATORY: "
                "`Path.home()/'Desktop'` would write to a folder Explorer never "
                "shows. (This does not mean Q9's shortcut failed — Q9 uses the "
                "shell API and works. It means the naive fallback would not.)")
        info(f"'OneDrive' in path: {'OneDrive' in (api or '')}")
        info(f"exists: {Path(api).is_dir()}")
    info(f"%OneDrive% env: {os.environ.get('OneDrive', '(unset)')}")


# ---------------------------------------------------------------------------
# Q2. Is PowerShell + WScript.Shell usable for .lnk creation, non-interactively?
#
# Probes: powershell.exe present? Does -Command run under the machine's
# ExecutionPolicy without -ExecutionPolicy Bypass? Does the COM object exist?
# ---------------------------------------------------------------------------
def powershell(script: str, env: dict | None = None) -> subprocess.CompletedProcess:
    e = dict(os.environ)
    e.update(env or {})
    return subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, env=e,
    )


def q2_powershell():
    head(2, "PowerShell + WScript.Shell availability")
    exe = shutil.which("powershell")
    if not exe:
        bad("powershell.exe not on PATH — the .lnk strategy is dead, use pywin32.")
        return
    ok(f"powershell.exe: {exe}")
    info(f"pwsh (7.x) also present: {shutil.which('pwsh')}")

    r = powershell("$PSVersionTable.PSVersion.ToString(); Get-ExecutionPolicy")
    info(f"version / execution policy: {r.stdout.strip().splitlines()}")

    r = powershell("(New-Object -ComObject WScript.Shell).GetType().Name")
    if r.returncode == 0:
        ok(f"WScript.Shell COM instantiates: {r.stdout.strip()}")
        ok("-Command was not blocked by ExecutionPolicy (expected: policy gates "
           "script FILES, not -Command). No -ExecutionPolicy Bypass needed.")
    else:
        bad(f"WScript.Shell failed: {r.stderr.strip()[:300]}")


# ---------------------------------------------------------------------------
# Q3. Does the .lnk round-trip, and does the env-var form survive nasty paths?
#
# design/17 flags the naive f-string interpolation of paths into a PowerShell
# single-quoted string as a command-injection sink. This builds a shortcut in a
# directory whose name contains an apostrophe, both ways, and compares.
# ---------------------------------------------------------------------------
LNK_VIA_ENV = """
$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:MC_LNK)
$s.TargetPath       = $env:MC_TARGET
$s.Arguments        = $env:MC_ARGS
$s.WorkingDirectory = $env:MC_WORKDIR
$s.IconLocation     = $env:MC_ICON
$s.Description      = 'Microclaw - AI agent for Micro-Manager'
$s.Save()
"""

LNK_READBACK = """
$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:MC_LNK)
Write-Output $s.TargetPath
Write-Output $s.Arguments
Write-Output $s.IconLocation
"""


def q3_lnk_roundtrip():
    head(3, ".lnk creation: round-trip + apostrophe/space-hostile paths")
    tmp = Path(tempfile.mkdtemp(prefix="mc_spike_"))
    # A directory name that breaks naive single-quote interpolation AND has a space.
    hostile = tmp / "it's a lab"
    hostile.mkdir()
    lnk = hostile / "Spike.lnk"
    target = sys.executable
    icon = os.path.expandvars(r"%SystemRoot%\System32\shell32.dll,13")

    env = {
        "MC_LNK": str(lnk),
        "MC_TARGET": target,
        "MC_ARGS": "-m microclaw serve",
        "MC_WORKDIR": str(hostile),
        "MC_ICON": icon,
    }
    r = powershell(LNK_VIA_ENV, env)
    if r.returncode != 0 or not lnk.exists():
        bad(f"env-var .lnk creation failed: {r.stderr.strip()[:300]}")
    else:
        ok(f"created {lnk}")
        rb = powershell(LNK_READBACK, {"MC_LNK": str(lnk)})
        lines = [l.strip() for l in rb.stdout.strip().splitlines()]
        info(f"read back: {lines}")
        if lines and os.path.normcase(lines[0]) == os.path.normcase(target):
            ok("TargetPath round-trips through an apostrophe'd directory.")
        else:
            bad(f"TargetPath mismatch: {lines[:1]} != {target}")
        if len(lines) > 1 and lines[1] == "-m microclaw serve":
            ok("Arguments round-trip.")
        else:
            bad(f"Arguments mismatch: {lines[1:2]}")

    # Now the form design/17's stub literally shows, to confirm it breaks.
    naive = f"""
    $s = (New-Object -ComObject WScript.Shell).CreateShortcut('{hostile / "Naive.lnk"}')
    $s.TargetPath = '{target}'
    $s.Save()
    """
    r = powershell(naive)
    if r.returncode != 0:
        first = (r.stderr.strip().splitlines() or [""])[0][:120]
        ok("naive single-quote interpolation FAILS on this path, as predicted. "
           f"Keep the env-var form. stderr: {first}")
    else:
        info("naive interpolation happened to survive; still use env vars "
             "(this is an injection sink, not just a quoting bug).")

    shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Q4. What exactly should the shortcut target?
#
# design/17 assumes `<sys.executable parent>/Scripts/microclaw.exe` exists after
# a pip/uv install, and falls back to `python -m microclaw`. Verify both, and
# check that `python -m microclaw --help` actually works (it needs no hardware).
# ---------------------------------------------------------------------------
def q4_launcher():
    head(4, "Launcher target: console script vs `-m microclaw`")
    info(f"sys.executable: {sys.executable}")
    info(f"sys.prefix    : {sys.prefix}")
    info(f"in venv       : {sys.prefix != sys.base_prefix}")

    d = Path(sys.executable).parent
    for cand in (d / "microclaw.exe", d / "Scripts" / "microclaw.exe"):
        (ok if cand.exists() else info)(f"{'found' if cand.exists() else 'absent'}: {cand}")

    info(f"`microclaw` on PATH: {shutil.which('microclaw')}")

    r = subprocess.run([sys.executable, "-m", "microclaw", "--help"],
                       capture_output=True, text=True)
    if r.returncode == 0:
        ok("`python -m microclaw --help` works (fallback launcher is viable).")
    else:
        bad(f"`python -m microclaw --help` rc={r.returncode}: {r.stderr.strip()[:300]}")

    info(f"pythonw.exe present: {(d / 'pythonw.exe').exists()} "
         "(design/17 rejects it — console must stay visible)")


# ---------------------------------------------------------------------------
# Q5. Does a .cmd wrapper propagate MICROCLAW_FROM_SHORTCUT?
#
# A .lnk cannot set environment variables, which is why design/17 routes the
# shortcut through a one-line .cmd. Confirm the variable reaches Python, and
# that a path with spaces survives the batch quoting.
# ---------------------------------------------------------------------------
def q5_cmd_wrapper():
    head(5, ".cmd wrapper: does it set the env var and survive spaces?")
    tmp = Path(tempfile.mkdtemp(prefix="mc_spike_"))
    spaced = tmp / "Program Folder"
    spaced.mkdir()
    cmd = spaced / "Microclaw.cmd"
    cmd.write_text(
        "@echo off\r\n"
        "set MICROCLAW_FROM_SHORTCUT=1\r\n"
        f'"{sys.executable}" -c "import os;print(\'VAR=\'+os.environ.get(\'MICROCLAW_FROM_SHORTCUT\',\'unset\'))"\r\n',
        encoding="utf-8",
    )
    r = subprocess.run(["cmd", "/c", str(cmd)], capture_output=True, text=True)
    if "VAR=1" in r.stdout:
        ok("MICROCLAW_FROM_SHORTCUT reaches Python through the .cmd wrapper, "
           "from a path containing a space.")
    else:
        bad(f"wrapper did not propagate the var: {r.stdout!r} {r.stderr[:200]!r}")
    shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Q6. Can `microclaw init` open the safety YAML in an editor?
#
# os.startfile() raises if .yaml has no registered handler — a real risk on a
# clean lab box, and it would abort `init` right when the user most needs the
# file open. Query the association without launching anything.
# ---------------------------------------------------------------------------
def assoc_exe(ext: str) -> str | None:
    ASSOCF_NONE, ASSOCSTR_EXECUTABLE = 0, 2
    buf = ctypes.create_unicode_buffer(1024)
    size = wintypes.DWORD(len(buf))
    hr = ctypes.windll.shlwapi.AssocQueryStringW(
        ASSOCF_NONE, ASSOCSTR_EXECUTABLE, ext, None, buf, ctypes.byref(size)
    )
    return buf.value if hr == 0 else None


def q6_editor():
    head(6, "Editor association for the safety config")
    for ext in (".yaml", ".yml", ".txt"):
        exe = assoc_exe(ext)
        (ok if exe else info)(f"{ext:6} -> {exe or '(no handler registered)'}")
    if not assoc_exe(".yaml"):
        info("=> os.startfile(safety_config.yaml) would RAISE here. `init` should "
             "fall back to an explicit notepad.exe, or name the file .txt. "
             "Recommend: try os.startfile, except OSError -> notepad.exe.")
    info(f"notepad.exe on PATH: {shutil.which('notepad')}")


# ---------------------------------------------------------------------------
# Q7. uv, and the paths install.bat will write into.
# ---------------------------------------------------------------------------
def q7_uv_and_paths():
    head(7, "uv availability and installer paths")
    uv = shutil.which("uv")
    (ok if uv else info)(f"uv on PATH: {uv or '(absent — install.bat will fetch it)'}")
    if uv:
        r = subprocess.run([uv, "--version"], capture_output=True, text=True)
        info(f"uv version: {r.stdout.strip()}")
    default_uv = Path.home() / ".local" / "bin" / "uv.exe"
    info(f"install.ps1 default location exists: {default_uv.exists()} ({default_uv})")

    for var in ("LOCALAPPDATA", "APPDATA", "USERPROFILE", "TEMP"):
        v = os.environ.get(var)
        info(f"%{var}% = {v}")
        if v and " " in v:
            info(f"  ^ contains a space — install.bat must quote every use of %{var}%")
    info(f"conda env active: {os.environ.get('CONDA_DEFAULT_ENV', '(none)')}")


# ---------------------------------------------------------------------------
# Q8. Does the icon have the frames Windows and the README want?
# ---------------------------------------------------------------------------
def q8_icon():
    head(8, "favicon.ico frames")
    ico = Path(__file__).resolve().parents[1] / "microclaw" / "favicon.ico"
    if not ico.exists():
        info(f"not committed yet: {ico} — skip (re-run after adding it)")
        return
    try:
        from PIL import Image
    except ImportError:
        info("Pillow not importable; skipping frame probe")
        return
    with Image.open(ico) as im:
        sizes = sorted(getattr(im, "ico", None).sizes()) if hasattr(im, "ico") else [im.size]
    info(f"frames: {sizes}")
    if (256, 256) in sizes:
        ok("256x256 present — ICNS/README PNG derive cleanly, no upscaling.")
    else:
        bad("no 256x256 frame; docs/microclaw-icon.png would be upscaled and soft.")
    for want in ((16, 16), (32, 32), (48, 48)):
        (ok if want in sizes else info)(
            f"{want[0]}px frame {'present' if want in sizes else 'missing'} "
            "(Windows uses 16/32/48 for taskbar, desktop, alt-tab)"
        )


# ---------------------------------------------------------------------------
# Q9 (opt-in). A real desktop shortcut, so you can look at it and click it.
#
# Targets a .cmd that prints the env var and pauses — i.e. exactly the console
# behaviour design/17 wants from a failed `serve`. Double-click it: the icon
# should be the shell32 gear, the window should stay open until you press Enter.
# ---------------------------------------------------------------------------
def q9_desktop(clean=False):
    head(9, "Real desktop shortcut (opt-in)")
    desktop = Path(known_folder(FOLDERID_Desktop))
    lnk = desktop / "Microclaw Spike.lnk"
    home = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "microclaw-spike"
    cmd = home / "spike.cmd"

    if clean:
        for p in (lnk, cmd):
            if p.exists():
                p.unlink()
                ok(f"removed {p}")
        if home.exists():
            shutil.rmtree(home, ignore_errors=True)
        return

    home.mkdir(parents=True, exist_ok=True)
    cmd.write_text(
        "@echo off\r\n"
        "set MICROCLAW_FROM_SHORTCUT=1\r\n"
        "echo Microclaw spike: this window is the server status window.\r\n"
        "echo MICROCLAW_FROM_SHORTCUT=%MICROCLAW_FROM_SHORTCUT%\r\n"
        "echo(\r\n"
        "echo Simulating a fatal startup error, as `serve` would print:\r\n"
        "echo   Could not connect to Micro-Manager. Is the ZMQ server enabled?\r\n"
        "echo(\r\n"
        "pause\r\n",
        encoding="utf-8",
    )
    env = {
        "MC_LNK": str(lnk),
        "MC_TARGET": str(cmd),
        "MC_ARGS": "",
        "MC_WORKDIR": str(home),
        "MC_ICON": os.path.expandvars(r"%SystemRoot%\System32\shell32.dll,13"),
    }
    r = powershell(LNK_VIA_ENV, env)
    if r.returncode == 0 and lnk.exists():
        ok(f"wrote {lnk}")
        print("\n  --> Look at your desktop. Do you SEE 'Microclaw Spike' with a")
        print("      gear icon? Double-click it. Does a console open, show the")
        print("      error text, and STAY OPEN until you press Enter?")
        print("      Then: python design/17-install-spike.py --clean")
    else:
        bad(f"could not write desktop shortcut: {r.stderr.strip()[:300]}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--desktop", action="store_true", help="also drop a real test shortcut")
    ap.add_argument("--clean", action="store_true", help="remove the test shortcut")
    args = ap.parse_args()

    print(f"python {sys.version}\nprefix {sys.prefix}")
    q1_desktop()
    q2_powershell()
    q3_lnk_roundtrip()
    q4_launcher()
    q5_cmd_wrapper()
    q6_editor()
    q7_uv_and_paths()
    q8_icon()
    if args.desktop or args.clean:
        q9_desktop(clean=args.clean)

    print(f"\n{'=' * 72}")
    if FAIL:
        print(f"{len(FAIL)} check(s) FAILED — design/17 needs to account for:")
        for f in FAIL:
            print(f"  - {f}")
    else:
        print("All checks passed. Paste this output into design/17.")


if __name__ == "__main__":
    main()
