from __future__ import annotations
import glob
import json
import platform
import re
from pathlib import Path
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from microclaw.controller import MicroscopeController

_MICROCLAW_DIR = Path.home() / ".microclaw"
_EMU_CACHE = _MICROCLAW_DIR / "emu.json"

# Metadata key shapes appended by EMU to a UIProperty name (design/14 §2a).
# Real configs use " - On value" / " - Off value" (TwoState) and " state N"
# (MultiState); the previously assumed " on"/" off" suffixes matched 0 of 120
# entries on a real htSMLM rig, so 68 metadata keys leaked as top-level
# pseudo-properties. " slope"/" offset" (Rescaled) are kept as-is.
_ON_OFF_RE = re.compile(r"^(?P<base>.+?) - (?P<which>On|Off) value$")
_STATE_RE = re.compile(r"^(?P<base>.+?) state (?P<idx>\d+)$")
_RESCALE_SUFFIXES = (" slope", " offset")

# EMU placeholder values for unallocated UIProperties.
_PLACEHOLDER_VALUES = {"Unallocated", "Enter value"}


def _candidate_mm_dirs() -> list[Path]:
    """Return platform-specific candidate µManager installation directories."""
    system = platform.system()
    if system == "Windows":
        return [
            Path("C:/Program Files/Micro-Manager-2.0"),
            Path("C:/Program Files/Micro-Manager-2.0.1"),
            Path("C:/Program Files/Micro-Manager-2.0.2"),
            Path("C:/Program Files/Micro-Manager-2.0.3"),
        ]
    if system == "Darwin":
        return [
            Path("/Applications/Micro-Manager-2.0"),
            Path("/Applications/Micro-Manager-2.0.1"),
            Path("/Applications/Micro-Manager-2.0.2"),
            Path("/Applications/Micro-Manager-2.0.3"),
        ]
    # Linux
    return [
        Path("/opt/micro-manager"),
        Path("/usr/local/lib/micro-manager"),
        Path("/usr/share/micro-manager"),
    ]


def _emu_config_path(mm_app_dir: Path) -> Path:
    return mm_app_dir / "EMU" / "config.uicfg"


def _find_jars(mm_app_dir: Path, prefix: str) -> list[str]:
    """Return basenames of JARs matching prefix in the MM plugins directories."""
    found = []
    for subdir in ("mmplugins", "plugins"):
        pattern = str(mm_app_dir / subdir / f"{prefix}*.jar")
        found.extend(Path(p).name for p in glob.glob(pattern))
    return found


def find_plugin_jars(mm_app_dir: Path) -> dict[str, list[str]]:
    """Return EMU and htSMLM JAR filenames found in the MM plugins directory."""
    return {
        "EMU": _find_jars(mm_app_dir, "EMU"),
        "htSMLM": _find_jars(mm_app_dir, "htSMLM"),
    }


def _has_emu(mm_app_dir: Path) -> bool:
    """True if the MM app dir contains an EMU or htSMLM JAR, or the config file."""
    jars = find_plugin_jars(mm_app_dir)
    return (
        bool(jars["EMU"] or jars["htSMLM"])
        or _emu_config_path(mm_app_dir).exists()
    )


def _looks_like_mm_dir(p: Path) -> bool:
    """True if p has the shape of an MM install root (has a plugins dir).

    Deliberately weaker than _has_emu(): a valid MM without EMU is still a
    correct mm_app_dir. Used only to sanity-check the live Java answer before
    trusting it over the cache.
    """
    return (p / "mmplugins").is_dir() or (p / "plugins").is_dir()


def find_mm_app_dir(ctrl: "MicroscopeController | None" = None) -> Path | None:
    """Return the µManager app directory.

    Order: (1) authoritative Java call via ctrl, (2) cache, (3) path guessing.
    ctrl is optional so offline callers keep working.
    """
    # 1. Ask the running MM JVM (authoritative). Validate before trusting it
    #    over the cache, then write through so offline calls stay fresh.
    if ctrl is not None:
        app_dir = ctrl.get_mm_app_dir()
        if app_dir:
            p = Path(app_dir)
            if p.exists() and _looks_like_mm_dir(p):
                save_mm_app_dir(str(p))
                return p
            # Bogus live answer (e.g. user-home ImageJ dir): fall through.

    # 2. Cache.
    if _EMU_CACHE.exists():
        try:
            cached = json.loads(_EMU_CACHE.read_text(encoding="utf-8"))
            cached_dir = cached.get("mm_app_dir")
            if cached_dir:
                p = Path(cached_dir)
                if p.exists():
                    return p
        except (json.JSONDecodeError, OSError):
            pass

    # 3. Path guessing (offline fallback).
    for candidate in _candidate_mm_dirs():
        if _has_emu(candidate):
            return candidate

    return None


def save_mm_app_dir(mm_app_dir: str) -> None:
    """Persist the µManager app directory to the microclaw cache."""
    _MICROCLAW_DIR.mkdir(parents=True, exist_ok=True)
    existing = {}
    if _EMU_CACHE.exists():
        try:
            existing = json.loads(_EMU_CACHE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    existing["mm_app_dir"] = mm_app_dir
    _EMU_CACHE.write_text(json.dumps(existing, indent=2), encoding="utf-8")


def _split_device_property(
    mm_str: str, device_labels: Sequence[str]
) -> tuple[str, str] | None:
    """Split "Focus-lock-Enable Fine" into ("Focus-lock", "Enable Fine").

    EMU stores the target as "DeviceLabel-PropertyLabel", but device labels
    themselves contain hyphens ("Focus-lock", "MicroFPGA-Hub",
    "Thorlabs ELL9-1"), and "Thorlabs ELL9" is a prefix of "Thorlabs ELL9-1",
    so a naive split("-", 1) is wrong and even a greedy match must take the
    LONGEST matching label. There is no correct parse without the
    loaded-device list; with an empty list, no split is attempted.
    """
    matches = [d for d in device_labels if mm_str.startswith(d + "-")]
    if not matches:
        return None
    device = max(matches, key=len)
    return device, mm_str[len(device) + 1:]


def _parse_properties(
    raw: dict[str, str], device_labels: Sequence[str] = ()
) -> dict[str, dict]:
    """Convert the flat EMU properties map into a structured dict.

    EMU stores state metadata as extra entries derived from the UIProperty name:
      "Laser 3 enable"                  → "Luxx638-Laser Operation Select"
      "Laser 3 enable - On value"       → "On"     (TwoState ON value)
      "Laser 3 enable - Off value"      → "Off"    (TwoState OFF value)
      "Filter wheel position state 3"   → "32000"  (MultiState value table)
      "Laser 3 power percentage slope"  → "1.0"    (Rescaled slope)
      "Laser 3 power percentage offset" → "0.0"    (Rescaled offset)

    Metadata is folded under its parent UIProperty ("on", "off", "states",
    "slope", "offset"), and the mm_property_string is split into device /
    property by longest-prefix match against device_labels.
    """
    meta: dict[str, dict] = {}
    base: dict[str, str] = {}

    for key, value in raw.items():
        if m := _ON_OFF_RE.match(key):
            meta.setdefault(m["base"], {})[m["which"].lower()] = value
        elif m := _STATE_RE.match(key):
            meta.setdefault(m["base"], {}).setdefault("states", {})[
                int(m["idx"])
            ] = value
        elif key.endswith(_RESCALE_SUFFIXES):
            suffix = next(s for s in _RESCALE_SUFFIXES if key.endswith(s))
            meta.setdefault(key[: -len(suffix)], {})[suffix.strip()] = value
        else:
            base[key] = value

    result: dict[str, dict] = {}
    for prop_name, mm_str in base.items():
        entry: dict = {"mm_property_string": mm_str}
        if "::" in mm_str:
            # Legacy "Device::Property" shape — unambiguous, no device list needed.
            device, prop = mm_str.split("::", 1)
            entry["device"] = device
            entry["property"] = prop
        elif split := _split_device_property(mm_str, device_labels):
            entry["device"], entry["property"] = split
        if prop_name in meta:
            entry.update(meta[prop_name])
        result[prop_name] = entry

    # A metadata key whose parent UIProperty is missing from the config would
    # otherwise vanish silently; keep it visible under its own name.
    for prop_name, extra in meta.items():
        if prop_name not in result:
            result[prop_name] = {"mm_property_string": "", **extra}

    return result


# Semantic UIProperty name shapes. htSMLM: "Laser 3 enable",
# "Laser 3 power percentage", "Laser trigger 3 mode/sequence/pulse duration".
# UIProperty names are plugin-specific (design/14 §2a: the demo "Simple UI"
# plugin says "Laser0 on/off"), so an alternate shape is tolerated too.
_LASER_RE = re.compile(
    r"^Laser (?P<i>\d+) (?P<field>enable|power percentage)$", re.IGNORECASE
)
_TRIG_RE = re.compile(
    r"^Laser trigger (?P<i>\d+) (?P<field>mode|sequence|pulse duration)$",
    re.IGNORECASE,
)
_LASER_ALT_RE = re.compile(
    r"^Laser\s?(?P<i>\d+) (?P<field>on/off|power)$", re.IGNORECASE
)

_FILTER_WHEEL_KEY = "Filter wheel position"
_FOCUS_LOCK_KEY = "Z stage focus locking"


def _slim(entry: dict) -> dict:
    """Drop the redundant mm_property_string once device/property are known."""
    if "device" in entry:
        return {k: v for k, v in entry.items() if k != "mm_property_string"}
    return dict(entry)


def build_emu_map(props: dict[str, dict]) -> dict:
    """Semantic view over parsed EMU properties: slot → laser, filter wheel,
    focus lock — placeholder-free and shaped so a laser cannot be mismatched
    to another laser's trigger line.

    Pairs 'Laser i …' with 'Laser trigger i …' by SLOT INDEX. In the amr_test
    session (design/14 §1) the agent inferred "Luxx638 = index 2" from device
    naming order and read the Cobolt561's trigger line (Mode2) instead of the
    638's (Mode3), then ran a 100-frame acquisition on the unverified line.
    Nothing may infer a slot index.
    """
    allocated = {
        k: v
        for k, v in props.items()
        if v.get("mm_property_string") not in _PLACEHOLDER_VALUES
        and v.get("mm_property_string")
    }

    lasers: dict[int, dict] = {}
    used: set[str] = set()
    for name, v in allocated.items():
        if m := _LASER_RE.match(name):
            key = "enable" if m["field"].lower() == "enable" else "power_pct"
            lasers.setdefault(int(m["i"]), {})[key] = _slim(v)
        elif m := _TRIG_RE.match(name):
            key = "trigger_" + m["field"].lower().replace(" ", "_")
            lasers.setdefault(int(m["i"]), {})[key] = _slim(v)
        elif m := _LASER_ALT_RE.match(name):
            key = "enable" if m["field"].lower() == "on/off" else "power_pct"
            lasers.setdefault(int(m["i"]), {})[key] = _slim(v)
        else:
            continue
        used.add(name)

    filter_wheel = allocated.get(_FILTER_WHEEL_KEY)
    if filter_wheel is not None:
        used.add(_FILTER_WHEEL_KEY)

    focus_lock = allocated.get(_FOCUS_LOCK_KEY)
    if focus_lock is not None:
        used.add(_FOCUS_LOCK_KEY)
        focus_lock = _slim(focus_lock)
        qpd = {}
        for name, v in allocated.items():
            if name.upper().startswith("QPD"):
                qpd[name.split()[-1].lower()] = _slim(v)
                used.add(name)
        if qpd:
            focus_lock["qpd"] = qpd

    return {
        "lasers": lasers,
        "filter_wheel": _slim(filter_wheel) if filter_wheel else None,
        "focus_lock": focus_lock,
        "other": {n: _slim(v) for n, v in allocated.items() if n not in used},
        # Names only — the placeholder noise is what buried the useful 99
        # entries in ~9 kB of context.
        "unallocated": sorted(set(props) - set(allocated)),
    }


def resolve_emu_device(props: dict[str, dict], semantic_name: str) -> dict:
    """'Laser 3 enable' → {'device': 'Luxx638', 'property': 'Laser Operation Select'}."""
    entry = props.get(semantic_name)
    if entry is None or "device" not in entry:
        allocated = sorted(
            k for k, v in props.items()
            if v.get("mm_property_string") not in _PLACEHOLDER_VALUES and "device" in v
        )
        raise KeyError(
            f"'{semantic_name}' is not an allocated EMU property. "
            f"Allocated names: {allocated}"
        )
    return {"device": entry["device"], "property": entry["property"]}


def read_emu_config(
    mm_app_dir: str | Path, device_labels: Sequence[str] = ()
) -> dict:
    """Read and parse the EMU config.uicfg file from the given MM app directory.

    device_labels (core.get_loaded_devices()) is required to split the
    "DeviceLabel-PropertyLabel" strings correctly — labels contain hyphens, so
    without the list, device/property fields are left unpopulated.

    Returns a dict with keys:
      config_name   — name of the currently active configuration
      plugin_name   — plugin registered (should be "htSMLM")
      properties    — structured UIProperty → MM device/property mapping
      plugin_settings — raw plugin-level settings (tab visibility, etc.)
    """
    config_path = _emu_config_path(Path(mm_app_dir))
    raw = json.loads(config_path.read_text(encoding="utf-8"))

    current_name = raw.get("defaultConfigurationName", "")
    configs = raw.get("pluginConfigurations", [])

    # Find the active configuration (matching defaultConfigurationName).
    active = next(
        (c for c in configs if c.get("configurationName") == current_name),
        configs[0] if configs else {},
    )

    return {
        "config_name": active.get("configurationName", ""),
        "plugin_name": active.get("pluginName", ""),
        "properties": _parse_properties(active.get("properties", {}), device_labels),
        "plugin_settings": active.get("settings", {}),
    }
