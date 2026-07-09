"""Where Microclaw's per-user files live (design/17 v2).

One module owns the platform conventions, so `credentials.py` and the safety
config cannot disagree about what "the config directory" means.

The desktop lookup is the interesting one, and it is measured, not guessed —
see `desktop_dir`.
"""
from __future__ import annotations

import os
from pathlib import Path

APP = "microclaw"


def user_config_dir() -> Path:
    """Roaming config: the safety limits and the API-key fallback file."""
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / APP


def user_data_dir() -> Path:
    """Machine-local data: the icon and .cmd wrapper the shortcut points at.

    Deliberately *not* the roaming config dir. On the lab rig the roaming profile
    is a network share (see `desktop_dir`), and a shortcut whose target sits on a
    share breaks whenever the network does.
    """
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / APP


def default_safety_config() -> Path:
    """The safety config a zero-argument launch loads. See `microclaw init`."""
    return user_config_dir() / "safety_config.yaml"


def desktop_dir() -> Path:
    """The desktop Explorer actually renders. NOT `Path.home() / "Desktop"`.

    Measured on the lab rig (design/17 spike Q1): the Desktop is redirected to a
    roaming profile on a network share, `\\\\isis\\roamingdata\\rieslab\\Desktop`,
    while `Path.home()/"Desktop"` is a local `C:\\Users\\rieslab\\Desktop` that
    Explorer never shows. A shortcut written there reports success and appears
    nowhere.

    The redirect there is a roaming profile, not OneDrive — checking for
    "OneDrive" in the path, or reading %OneDrive%, would have missed it. Ask the
    shell. SHGetKnownFolderPath is what Explorer itself calls.
    """
    if os.name != "nt":
        return Path.home() / "Desktop"
    try:
        return Path(_known_folder(_FOLDERID_DESKTOP))
    except OSError:
        # A broken shell API is not a reason to crash; ~/Desktop is right on the
        # majority of machines and wrong loudly (no icon) rather than silently.
        return Path.home() / "Desktop"


# {B4BFCC3A-DB2C-424C-B029-7FE99A87C641}
_FOLDERID_DESKTOP = "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}"


def _known_folder(guid_str: str) -> str:
    """SHGetKnownFolderPath via ctypes. Windows only; raises OSError on failure."""
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_byte * 8),
        ]

    guid = GUID()
    hr = ctypes.windll.ole32.CLSIDFromString(ctypes.c_wchar_p(guid_str), ctypes.byref(guid))
    if hr != 0:
        raise OSError(f"CLSIDFromString: 0x{hr & 0xFFFFFFFF:08x}")

    out = ctypes.c_wchar_p()
    hr = ctypes.windll.shell32.SHGetKnownFolderPath(
        ctypes.byref(guid), 0, None, ctypes.byref(out)
    )
    if hr != 0:
        raise OSError(f"SHGetKnownFolderPath: 0x{hr & 0xFFFFFFFF:08x}")
    try:
        return out.value
    finally:
        ctypes.windll.ole32.CoTaskMemFree(out)
