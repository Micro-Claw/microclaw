from __future__ import annotations
import glob
import json
import platform
from pathlib import Path

_MICROCLAW_DIR = Path.home() / ".microclaw"
_EMU_CACHE = _MICROCLAW_DIR / "emu.json"

# Suffixes appended by EMU for TwoState and Rescaled UIProperty metadata.
_META_SUFFIXES = (" on", " off", " slope", " offset")


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


def find_mm_app_dir() -> Path | None:
    """Return the µManager app directory, checking cache then common paths."""
    # Check cache first.
    if _EMU_CACHE.exists():
        try:
            cached = json.loads(_EMU_CACHE.read_text())
            cached_dir = cached.get("mm_app_dir")
            if cached_dir:
                p = Path(cached_dir)
                if p.exists():
                    return p
        except (json.JSONDecodeError, OSError):
            pass

    # Try common platform paths.
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
            existing = json.loads(_EMU_CACHE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    existing["mm_app_dir"] = mm_app_dir
    _EMU_CACHE.write_text(json.dumps(existing, indent=2))


def _parse_properties(raw: dict[str, str]) -> dict[str, dict]:
    """Convert the flat EMU properties map into a structured dict.

    EMU stores state metadata as extra entries with suffixes appended to the
    UIProperty name:
      "Laser 0 enable"        → "DeviceLabel::PropertyLabel"
      "Laser 0 enable on"     → "1"      (TwoState ON value)
      "Laser 0 enable off"    → "0"      (TwoState OFF value)
      "Laser 0 power percentage slope"  → "1.0"  (Rescaled slope)
      "Laser 0 power percentage offset" → "0.0"  (Rescaled offset)

    Returns a dict keyed by UIProperty name with nested metadata.
    """
    meta: dict[str, dict] = {}
    base: dict[str, str] = {}

    for key, value in raw.items():
        matched = False
        for suffix in _META_SUFFIXES:
            if key.endswith(suffix):
                prop_name = key[: -len(suffix)]
                meta.setdefault(prop_name, {})[suffix.strip()] = value
                matched = True
                break
        if not matched:
            base[key] = value

    result: dict[str, dict] = {}
    for prop_name, mm_str in base.items():
        entry: dict = {"mm_property_string": mm_str}
        if "::" in mm_str:
            device, prop = mm_str.split("::", 1)
            entry["device"] = device
            entry["property"] = prop
        if prop_name in meta:
            entry.update(meta[prop_name])
        result[prop_name] = entry

    return result


def read_emu_config(mm_app_dir: str | Path) -> dict:
    """Read and parse the EMU config.uicfg file from the given MM app directory.

    Returns a dict with keys:
      config_name   — name of the currently active configuration
      plugin_name   — plugin registered (should be "htSMLM")
      properties    — structured UIProperty → MM device/property mapping
      plugin_settings — raw plugin-level settings (tab visibility, etc.)
    """
    config_path = _emu_config_path(Path(mm_app_dir))
    raw = json.loads(config_path.read_text())

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
        "properties": _parse_properties(active.get("properties", {})),
        "plugin_settings": active.get("settings", {}),
    }
