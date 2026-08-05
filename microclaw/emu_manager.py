from __future__ import annotations
from dataclasses import dataclass
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
        return sorted(Path("C:/Program Files").glob("Micro-Manager-2.0*"))
    if system == "Darwin":
        return sorted(Path("/Applications").glob("Micro-Manager-2.0*"))
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
    prefix = prefix.casefold()
    for subdir in ("mmplugins", "plugins", "EMU"):
        pattern = str(mm_app_dir / subdir / "*.jar")
        found.extend(
            Path(p).name for p in glob.glob(pattern)
            if Path(p).name.casefold().startswith(prefix)
        )
    return found


def find_plugin_jars(mm_app_dir: Path) -> dict[str, list[str]]:
    """Return EMU and htSMLM JAR filenames found in the MM plugins directory."""
    return {
        "EMU": _find_jars(mm_app_dir, "EMU"),
        "htSMLM": _find_jars(mm_app_dir, "htSMLM"),
    }


def _has_emu(mm_app_dir: Path) -> bool:
    """True if the MM app dir has the EMU plugin installed.

    This is intentionally only a locator hint.  Emu.jar ships with stock
    Micro-Manager, so this does not mean that the connected rig uses EMU.
    Authorization must use :func:`_has_emu_config` instead.
    """
    jars = find_plugin_jars(mm_app_dir)
    return (
        bool(jars["EMU"] or jars["htSMLM"])
        or _emu_config_path(mm_app_dir).exists()
    )


def _has_emu_config(mm_app_dir: Path) -> bool:
    """True only when this installation contains a configured EMU profile."""
    return _emu_config_path(mm_app_dir).is_file()


def _looks_like_mm_dir(p: Path) -> bool:
    """True if p has the shape of an MM install root (has a plugins dir).

    Deliberately weaker than _has_emu(): a valid MM without EMU is still a
    correct mm_app_dir. Used only to sanity-check the live Java answer before
    trusting it over the cache.
    """
    return (p / "mmplugins").is_dir() or (p / "plugins").is_dir()


@dataclass(frozen=True)
class MMAppDirResolution:
    """A located MM directory plus how strongly it identifies the live JVM."""

    path: Path | None
    source: str
    live_probe: str


def resolve_mm_app_dir(
    ctrl: "MicroscopeController | None" = None,
    *,
    cache_live: bool = True,
) -> MMAppDirResolution:
    """Resolve the MM directory while retaining live/fallback provenance.

    Order: (1) authoritative Java call via ctrl, (2) cache, (3) path guessing.
    A cache or guess is useful for offline tools, but is not proof that it
    belongs to a connected JVM. Startup authorization uses that distinction.
    """
    live_probe = "not_attempted"
    # 1. Ask the running MM JVM (authoritative). Validate before trusting it
    #    over the cache, then write through so offline calls stay fresh.
    if ctrl is not None:
        try:
            app_dir = ctrl.get_mm_app_dir()
        except Exception:
            app_dir = None
            live_probe = "error"
        if app_dir:
            p = Path(app_dir)
            if p.exists() and _looks_like_mm_dir(p):
                if cache_live:
                    save_mm_app_dir(str(p))
                return MMAppDirResolution(p, "live", "validated")
            # Bogus live answer (e.g. user-home ImageJ dir): fall through.
            live_probe = "invalid"
        elif live_probe != "error":
            live_probe = "unavailable"

    # 2. Cache.
    if _EMU_CACHE.exists():
        try:
            cached = json.loads(_EMU_CACHE.read_text(encoding="utf-8"))
            cached_dir = cached.get("mm_app_dir")
            if cached_dir:
                p = Path(cached_dir)
                if p.exists() and _looks_like_mm_dir(p):
                    return MMAppDirResolution(p, "cache", live_probe)
        except (json.JSONDecodeError, OSError):
            pass

    # 3. Path guessing (offline fallback).
    for candidate in _candidate_mm_dirs():
        if _has_emu(candidate):
            return MMAppDirResolution(candidate, "guess", live_probe)

    return MMAppDirResolution(None, "none", live_probe)


def find_mm_app_dir(ctrl: "MicroscopeController | None" = None) -> Path | None:
    """Return the best MM directory, preserving the established public API."""
    return resolve_mm_app_dir(ctrl).path


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

_FILTER_WHEEL_RE = re.compile(
    r"^Filter wheel(?: (?P<i>\d+))? position$", re.IGNORECASE
)
_FOCUS_LOCK_KEY = "Z stage focus locking"


def _slim(entry: dict) -> dict:
    """Drop the redundant mm_property_string once device/property are known."""
    if "device" in entry:
        return {k: v for k, v in entry.items() if k != "mm_property_string"}
    return dict(entry)


def _parse_parameters(raw: object) -> dict[str, dict[str, str]]:
    """Group flat ``Panel - Parameter`` keys by their EMU panel label."""
    if not isinstance(raw, dict):
        return {}
    result: dict[str, dict[str, str]] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or " - " not in key:
            continue
        panel, name = key.split(" - ", 1)
        result.setdefault(panel, {})[name] = value
    return result


def _normal_name(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if value and value.casefold() != "none" else None


def build_emu_map(
    props: dict[str, dict], params: dict[str, dict[str, str]] | None = None
) -> dict:
    """Semantic view over parsed EMU properties: slot → laser, filter wheel,
    focus lock — placeholder-free and shaped so a laser cannot be mismatched
    to another laser's trigger line.

    Pairs 'Laser i …' with 'Laser trigger i …' by SLOT INDEX. In the amr_test
    session (design/14 §1) the agent inferred "Luxx638 = index 2" from device
    naming order and read the Cobolt561's trigger line (Mode2) instead of the
    638's (Mode3), then ran a 100-frame acquisition on the unverified line.
    Nothing may infer a slot index.
    """
    params = params or {}
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

    # Rule A: a panel Name labels everything in that panel. Laser and trigger
    # panels share a slot, so either parameter names the whole paired record.
    property_names: dict[str, str] = {}
    for panel, panel_params in params.items():
        label = _normal_name(panel_params.get("Name"))
        if label is None:
            continue
        for prop_name in allocated:
            if not prop_name.casefold().startswith((panel + " ").casefold()):
                continue
            property_names[prop_name] = label
            if m := (_LASER_RE.match(prop_name) or _TRIG_RE.match(prop_name)
                     or _LASER_ALT_RE.match(prop_name)):
                slot = int(m["i"])
                record = lasers.setdefault(slot, {})
                # Two panels binding different labels to one slot: keeping
                # either makes the answer depend on JSON key order, which is
                # how the wrong laser gets picked. Refuse to name it and say
                # so, as rule C does for a slot-count mismatch. Once a slot is
                # in conflict it stays unnamed — a third agreeing panel must
                # not silently resurrect a name.
                conflict = record.get("name_conflict")
                existing = record.get("name")
                if conflict is not None:
                    if label not in conflict:
                        conflict.append(label)
                elif existing is None:
                    record["name"] = label
                elif existing.casefold() != label.casefold():
                    del record["name"]
                    record["name_conflict"] = [existing, label]

    # Rule B: "X name" labels the exact UIProperty X.
    for panel_params in params.values():
        for param_name, value in panel_params.items():
            if not param_name.casefold().endswith(" name"):
                continue
            target = param_name[:-5]
            label = _normal_name(value)
            if target in allocated and label is not None:
                property_names[target] = label

    filter_wheels: dict[int, dict] = {}
    for prop_name, entry in allocated.items():
        match = _FILTER_WHEEL_RE.match(prop_name)
        if not match:
            continue
        ordinal = int(match["i"] or 1)
        wheel = {"ui_property": prop_name, **_slim(entry)}
        filter_wheels[ordinal] = wheel
        used.add(prop_name)

    # Rule C: slot lists are the one shape join. Refuse partial pairing.
    for panel, panel_params in params.items():
        for param_name, raw_names in panel_params.items():
            match = re.fullmatch(r"Filter names(?: (?P<i>\d+))?", param_name,
                                 re.IGNORECASE)
            if not match or not isinstance(raw_names, str):
                continue
            ordinal = int(match["i"] or 1)
            target = "Filter wheel position" if ordinal == 1 else (
                f"Filter wheel {ordinal} position"
            )
            wheel = filter_wheels.get(ordinal)
            names = [part.strip() for part in raw_names.split(",")]
            states = props.get(target, {}).get("states")
            state_count = len(states) if isinstance(states, dict) else None
            contiguous = (
                isinstance(states, dict)
                and sorted(states) == list(range(len(names)))
            )
            if wheel is None or state_count != len(names) or not contiguous:
                if wheel is None:
                    wheel = {"ui_property": target}
                    filter_wheels[ordinal] = wheel
                wheel["name_mismatch"] = {
                    "names": len(names),
                    "states": state_count,
                }
                continue
            slots = {}
            for position in sorted(states):
                label = _normal_name(names[position]) if position < len(names) else None
                slot = {"name": label, "value": states[position]}
                if label is None:
                    slot["empty"] = True
                slots[position] = slot
            wheel["slots"] = slots

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

    other = {n: _slim(v) for n, v in allocated.items() if n not in used}
    for prop_name, label in property_names.items():
        target = other.get(prop_name)
        if target is not None:
            target["name"] = label
        elif prop_name == _FOCUS_LOCK_KEY and focus_lock is not None:
            focus_lock["name"] = label

    # Rule D: retain role aliases only when their exact target is allocated.
    aliases: dict[str, list[str]] = {}
    for panel, panel_params in params.items():
        for role, target in panel_params.items():
            if isinstance(target, str) and target in allocated:
                aliases.setdefault(target, []).extend((role, f"{panel} - {role}"))
    for target, role_names in aliases.items():
        if target in other:
            other[target]["aliases"] = role_names
        elif target == _FOCUS_LOCK_KEY and focus_lock is not None:
            focus_lock["aliases"] = role_names
        elif match := (_LASER_RE.match(target) or _TRIG_RE.match(target)
                       or _LASER_ALT_RE.match(target)):
            lasers[int(match["i"])].setdefault("aliases", []).extend(role_names)
        elif match := _FILTER_WHEEL_RE.match(target):
            ordinal = int(match["i"] or 1)
            filter_wheels[ordinal].setdefault("aliases", []).extend(role_names)

    return {
        "lasers": lasers,
        "filter_wheels": filter_wheels,
        "focus_lock": focus_lock,
        "other": other,
        # Names only — the placeholder noise is what buried the useful 99
        # entries in ~9 kB of context.
        "unallocated": sorted(set(props) - set(allocated)),
    }


def _resolvable(record: dict) -> bool:
    """True when a resolved record carries a usable Micro-Manager target.

    A plain record holds device/property directly; a laser slot record holds
    them on its enable/power/trigger sub-records instead.
    """
    if record.get("device"):
        return True
    return any(
        isinstance(line, dict) and line.get("device") for line in record.values()
    )


def resolve_emu_device(
    props: dict[str, dict], semantic_name: str,
    params: dict[str, dict[str, str]] | None = None,
) -> dict:
    """Resolve an exact UIProperty key or a configured human-facing name."""
    entry = props.get(semantic_name)
    if entry is not None and "device" in entry and (
        entry.get("mm_property_string") not in _PLACEHOLDER_VALUES
    ):
        return {"device": entry["device"], "property": entry["property"]}

    wanted = semantic_name.strip().casefold()
    emu_map = build_emu_map(props, params)
    candidates: list[tuple[str, dict]] = []
    for slot, laser in emu_map["lasers"].items():
        names = [laser.get("name"), *laser.get("aliases", [])]
        if any(str(name).strip().casefold() == wanted for name in names if name):
            candidates.append((f"laser slot {slot}", laser))
    for prop_name, other in emu_map["other"].items():
        names = [other.get("name"), *other.get("aliases", [])]
        if any(str(name).strip().casefold() == wanted for name in names if name):
            candidates.append((prop_name, other))
    for ordinal, wheel in emu_map["filter_wheels"].items():
        if any(
            str(name).strip().casefold() == wanted
            for name in wheel.get("aliases", [])
        ):
            candidates.append((f"filter wheel {ordinal}", wheel))
        for position, slot in wheel.get("slots", {}).items():
            if str(slot.get("name", "")).strip().casefold() == wanted:
                candidates.append((f"filter wheel {ordinal} slot {position}", {
                    "device": wheel.get("device"), "property": wheel.get("property"),
                    "value": slot["value"], "name": slot["name"],
                }))
    focus_lock = emu_map["focus_lock"]
    if focus_lock is not None:
        names = [focus_lock.get("name"), *focus_lock.get("aliases", [])]
        if any(str(name).strip().casefold() == wanted for name in names if name):
            candidates.append(("focus lock", focus_lock))
    if len(candidates) == 1:
        label, record = candidates[0]
        # The exact-key path above refuses an entry whose device/property could
        # not be split (no loaded-device list). The name path must refuse it
        # too, or a caller reading record["device"] fails downstream instead of
        # here. A laser slot carries its targets on its per-line sub-records.
        if not _resolvable(record):
            raise KeyError(
                f"'{semantic_name}' names {label}, but its Micro-Manager "
                f"device/property could not be resolved — the EMU config was "
                f"parsed without the loaded-device list, so "
                f"'DeviceLabel-PropertyLabel' could not be split."
            )
        return record
    if len(candidates) > 1:
        raise KeyError(
            f"'{semantic_name}' is ambiguous; candidates: "
            f"{[label for label, _ in candidates]}"
        )
    allocated = sorted(
        k for k, v in props.items()
        if v.get("mm_property_string") not in _PLACEHOLDER_VALUES and "device" in v
    )
    raise KeyError(
        f"'{semantic_name}' is not an allocated EMU property or configured name. "
        f"Allocated names: {allocated}"
    )


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
      parameters       — parsed panel → parameter → value map
      plugin_settings — raw plugin-level settings (tab visibility, etc.)
    """
    config_path = _emu_config_path(Path(mm_app_dir))
    raw = json.loads(config_path.read_text(encoding="utf-8"))

    if not isinstance(raw, dict):
        raise ValueError("EMU config root must be a mapping")

    current_name = raw.get("defaultConfigurationName", "")
    configs = raw.get("pluginConfigurations", [])
    if not isinstance(configs, list) or not all(isinstance(c, dict) for c in configs):
        raise ValueError("EMU pluginConfigurations must be a list of mappings")
    if not configs:
        raise ValueError("EMU config has no plugin configuration")

    # Find the active configuration (matching defaultConfigurationName).
    active = next(
        (c for c in configs if c.get("configurationName") == current_name),
        configs[0],
    )
    properties = active.get("properties")
    if not isinstance(properties, dict):
        raise ValueError("active EMU plugin configuration properties must be a mapping")
    settings = active.get("settings", {})
    if not isinstance(settings, dict):
        raise ValueError("active EMU plugin configuration settings must be a mapping")

    return {
        "config_name": active.get("configurationName", ""),
        "plugin_name": active.get("pluginName", ""),
        "properties": _parse_properties(properties, device_labels),
        "parameters": _parse_parameters(active.get("parameters", {})),
        "plugin_settings": settings,
    }
