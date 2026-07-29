"""Read-only Micro-Manager rig discovery.

This module deliberately accepts an MMCore-like object, not a controller.  Its
public enumeration path contains queries only; candidates are questions for a
human and are structurally separate from observed facts and human decisions.

No YAML aid is emitted.  Inventory evidence is not authorization, and making a
config-shaped derivative would invite an operator to mistake mechanically
observed properties or heuristic candidates for reviewed safety decisions.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any, Callable

from microclaw.authorization import (
    DEVICE_TYPE_NAMES,
    _auto_classified_state_pairs,
    _config_settings,
)


# Deliberately broad: a false positive is discarded in review, while a false
# negative falsely says the rig cannot do something.  `level` and bare `%`
# specifically catch iChrome's real `Laser 4: 3. Level %`; requiring laser and
# level to be adjacent missed the rig's 402 nm activation line.
_POWER_NAME = re.compile(r"power|intens|level|percent|%|\bpwr\b|\bmw\b|\bamplitude\b", re.I)
# Do not narrow this.  iChrome exposes its gate as `Laser 4: 2. Emission`.
_ENABLE_NAME = re.compile(r"operation|enable|shutter|on/?off|\bstate\b|emission|output", re.I)
_ON_VALUES = {"on", "1", "true", "open", "enabled", "yes"}
_NON_EMITTING_TYPES = {"CameraDevice", "StageDevice", "XYStageDevice"}
_CHANNEL_LABEL = re.compile(r"^(?P<chan>.+?):$")
_CHANNEL_MEMBER = re.compile(r"^(?P<chan>.+?):\s*\d+\.\s*(?P<field>.+)$")
_GATING_CONTEXT = re.compile(r"use\s*ttl|analog\s*mode|ttl\s*enable|ttl\s*high|master\s*mode", re.I)
_SECRET_NAME = re.compile(r"password|passwd|secret|token|credential|api.?key|private.?key", re.I)
_REDACTED = "<redacted>"
_PROP_TYPES = {0: "Undef", 1: "String", 2: "Float", 3: "Integer"}


class _NonPrimitiveResult(TypeError):
    pass


def _error(exc: Exception) -> str:
    text = " ".join(str(exc).split()) or type(exc).__name__
    return _SECRET_NAME.sub("credential", text)


def _fingerprint_facts(value: Any) -> Any:
    """Copy facts while removing driver-controlled exception message text."""
    if isinstance(value, dict):
        return {
            key: _fingerprint_facts(item)
            for key, item in value.items()
            if key != "error"
        }
    if isinstance(value, list):
        return [_fingerprint_facts(item) for item in value]
    return value


def _primitive(value: Any) -> str | int | float | bool | None:
    """Admit only stable JSON leaves returned by the bridge."""
    if type(value) in (str, int, float, bool) or value is None:
        return value
    raise _NonPrimitiveResult(f"non-primitive result type: {type(value).__name__}")


def _strings_result(value: Any) -> list[str]:
    """Expand a bridge vector, validating every value before conversion."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    try:
        values = list(value)
    except TypeError:
        size, get = getattr(value, "size", None), getattr(value, "get", None)
        if not callable(size) or not callable(get):
            raise _NonPrimitiveResult(f"non-primitive result type: {type(value).__name__}")
        values = [get(i) for i in range(int(_primitive(size())))]
    return [str(_primitive(item)) for item in values]


def _property_type(core: Any, device: str, prop: str) -> str:
    """Convert PropertyType through its bridge API; never stringify its proxy."""
    raw = core.get_property_type(device, prop)
    if isinstance(raw, str):
        return str(_primitive(raw))
    to_string = getattr(raw, "to_string", None)
    if callable(to_string):
        name = _primitive(to_string())
        if isinstance(name, str) and name in _PROP_TYPES.values():
            return name
    swig_value = getattr(raw, "swig_value", None)
    if callable(swig_value):
        ordinal = _primitive(swig_value())
        if type(ordinal) is int and ordinal in _PROP_TYPES:
            return _PROP_TYPES[ordinal]
    return str(_primitive(raw))


def _device_type(core: Any, label: str) -> str:
    raw = core.get_device_type(label)
    if isinstance(raw, str):
        return raw
    to_string = getattr(raw, "to_string", None)
    if callable(to_string):
        name = _primitive(to_string())
        if isinstance(name, str):
            return name
    swig_value = getattr(raw, "swig_value", None)
    if callable(swig_value):
        raw = _primitive(swig_value())
    raw = _primitive(raw)
    if type(raw) is int:
        return DEVICE_TYPE_NAMES.get(raw, str(raw))
    raise _NonPrimitiveResult(f"non-primitive result type: {type(raw).__name__}")


def _setting_primitive(setting: Any, names: tuple[str, ...]) -> str | None:
    if isinstance(setting, dict):
        for name in names:
            if name in setting:
                return str(_primitive(setting[name]))
        return None
    for name in names:
        value = getattr(setting, name, None)
        if callable(value):
            value = value()
        if value is not None:
            return str(_primitive(value))
    return None


def _query(errors: list[dict], scope: str, field: str, call: Callable[[], Any], default=None):
    try:
        return call()
    except Exception as exc:
        errors.append({"scope": scope, "field": field, "error": _error(exc)})
        return default


def _property_record(core: Any, device: str, prop: str, failures: list[dict]) -> dict:
    """Read one property defensively; one bad attribute cannot end the sweep."""
    scope = f"property:{device}.{prop}"
    local: list[dict] = []
    value = _query(local, scope, "current_value", lambda: str(_primitive(core.get_property(device, prop))))
    if _SECRET_NAME.search(prop):
        value = _REDACTED if value is not None else None
    allowed = _query(local, scope, "allowed_values", lambda: sorted(_strings_result(core.get_allowed_property_values(device, prop))))
    if _SECRET_NAME.search(prop) and allowed is not None:
        allowed = [_REDACTED] if allowed else []
    record = {
        "name": prop,
        "current_value": value,
        "read_only": _query(local, scope, "read_only", lambda: bool(_primitive(core.is_property_read_only(device, prop)))),
        "pre_init": _query(local, scope, "pre_init", lambda: bool(_primitive(core.is_property_pre_init(device, prop)))),
        "allowed_values": allowed,
        "has_limits": _query(local, scope, "has_limits", lambda: bool(_primitive(core.has_property_limits(device, prop)))),
        "reported_type": _query(local, scope, "reported_type", lambda: _property_type(core, device, prop)),
    }
    if record["has_limits"]:
        record["technical_range"] = {
            "lower": _query(local, scope, "lower_limit", lambda: float(_primitive(core.get_property_lower_limit(device, prop)))),
            "upper": _query(local, scope, "upper_limit", lambda: float(_primitive(core.get_property_upper_limit(device, prop)))),
            "source": "driver_reported",
        }
    if local:
        record["query_errors"] = local
        failures.extend(local)
    return record


def _is_power(record: dict) -> bool:
    if record.get("read_only") is not False or record.get("pre_init") is not False:
        return False
    if not _POWER_NAME.search(record["name"]) or record.get("allowed_values"):
        return False
    if record.get("has_limits"):
        return True
    try:
        float(record.get("current_value"))
        return True
    except (TypeError, ValueError):
        return False


def _is_enable(record: dict) -> bool:
    if record.get("read_only") is not False or record.get("pre_init") is not False:
        return False
    if not _ENABLE_NAME.search(record["name"]):
        return False
    allowed = record.get("allowed_values") or []
    if allowed:
        return len(allowed) <= 4 and any(str(v).strip().lower() in _ON_VALUES for v in allowed)
    span = record.get("technical_range") or {}
    return span.get("lower") == 0.0 and span.get("upper") == 1.0


def _channel_labels(properties: list[dict]) -> dict[str, str]:
    out = {}
    for record in properties:
        match = _CHANNEL_LABEL.match(record["name"])
        if match and record.get("read_only") and record.get("current_value"):
            out[match.group("chan").strip()] = record["current_value"]
    return out


def _channel(prop: str, labels: dict[str, str]) -> str | None:
    match = _CHANNEL_MEMBER.match(prop)
    return labels.get(match.group("chan").strip()) if match else None


def _preset(core: Any, group: str, name: str, failures: list[dict]) -> dict:
    scope = f"preset:{group}.{name}"
    config = _query(failures, scope, "settings", lambda: core.get_config_data(group, name))
    effects = []
    if config is not None:
        try:
            settings = _config_settings(config)
        except Exception as exc:
            failures.append({"scope": scope, "field": "settings", "error": _error(exc)})
            settings = []
        for index, setting in enumerate(settings):
            prop = _query(
                failures, scope, f"setting_{index}_property",
                lambda s=setting: _setting_primitive(
                    s, ("property", "property_name", "get_property_name", "getPropertyName")
                ),
            )
            effects.append({
                "device": _query(
                    failures, scope, f"setting_{index}_device",
                    lambda s=setting: _setting_primitive(
                        s, ("device", "device_label", "get_device_label", "getDeviceLabel")
                    ),
                ),
                "property": prop,
                "value": _REDACTED if _SECRET_NAME.search(prop or "") else _query(
                    failures, scope, f"setting_{index}_value",
                    lambda s=setting: _setting_primitive(
                        s, ("value", "property_value", "get_property_value", "getPropertyValue")
                    ),
                ),
                "setting_index": index,
            })
    return {"name": name, "effects": effects}


def enumerate_rig(core: Any, *, mm_config: str | Path | None = None) -> dict:
    """Enumerate *core* using only read-only bridge queries."""
    failures: list[dict] = []
    assignments = {}
    for field, method in (
        ("camera", "get_camera_device"), ("focus", "get_focus_device"),
        ("xy_stage", "get_xy_stage_device"), ("shutter", "get_shutter_device"),
        ("autofocus", "get_auto_focus_device"), ("galvo", "get_galvo_device"),
        ("image_processor", "get_image_processor_device"), ("slm", "get_slm_device"),
    ):
        assignments[field] = _query(failures, "core_assignments", field, lambda m=method: str(_primitive(getattr(core, m)()) or ""))

    devices = []
    labels = _query(failures, "core", "loaded_devices", lambda: sorted(_strings_result(core.get_loaded_devices())), [])
    for label in labels:
        scope = f"device:{label}"
        kind = _query(failures, scope, "device_type", lambda: _device_type(core, label))
        names = _query(failures, scope, "property_names", lambda: sorted(_strings_result(core.get_device_property_names(label))))
        properties = [] if names is None else [_property_record(core, label, p, failures) for p in names]
        state_labels = []
        if kind == "StateDevice":
            state_labels = _query(failures, scope, "state_labels", lambda: _strings_result(core.get_state_labels(label)), [])
        devices.append({
            "label": label, "device_type": kind,
            "adapter": {
                "library": _query(failures, scope, "adapter_library", lambda: str(_primitive(core.get_device_library(label)))),
                "name": _query(failures, scope, "adapter_name", lambda: str(_primitive(core.get_device_name(label)))),
                "description": _query(failures, scope, "description", lambda: str(_primitive(core.get_device_description(label)))),
            },
            "properties": properties, "state_labels": sorted(state_labels),
        })

    groups = []
    for group in _query(failures, "core", "configuration_groups", lambda: sorted(_strings_result(core.get_available_config_groups())), []):
        presets = _query(failures, f"config_group:{group}", "presets", lambda g=group: sorted(_strings_result(core.get_available_configs(g))), [])
        groups.append({"name": group, "presets": [_preset(core, group, p, failures) for p in presets]})

    powers, enables, unclassified, unknown_writability = [], [], [], []
    for device in devices:
        channels = _channel_labels(device["properties"])
        gating = {p["name"]: p.get("current_value") for p in device["properties"] if _GATING_CONTEXT.search(p["name"])}
        for prop in device["properties"]:
            path = f'{device["label"]}.{prop["name"]}'
            if prop.get("read_only") is None or prop.get("pre_init") is None:
                unknown_writability.append(path)
            if prop.get("read_only") is False and prop.get("pre_init") is False:
                unclassified.append(path)
            base = {"path": path, "device_type": device["device_type"], "channel_description": _channel(prop["name"], channels)}
            if _is_power(prop):
                base["gating_context"] = gating
                base["rejected_non_emitting"] = device["device_type"] in _NON_EMITTING_TYPES
                powers.append(base)
            if _is_enable(prop):
                enables.append(base)

    emitting_powers = [p for p in powers if not p["rejected_non_emitting"]]
    pairs = [
        {"power_path": power["path"], "enable_path": enable["path"]}
        for power in emitting_powers for enable in enables
        if power["path"].split(".", 1)[0] == enable["path"].split(".", 1)[0]
    ]
    facts = {
        "core_identity": {
            "version": _query(failures, "core", "version", lambda: str(_primitive(core.get_version_info()))),
            "api_version": _query(failures, "core", "api_version", lambda: str(_primitive(core.get_api_version_info()))),
        },
        "core_device_assignments": assignments,
        "devices": devices,
        "configuration_groups": groups,
        "enumeration_failures": failures,
    }
    config_record = None
    if mm_config is not None:
        p = Path(mm_config)
        config_record = {"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
    # Driver exception text is evidence, but not identity: adapters may embed a
    # handle, address, or timestamp.  Retain our stable failure coordinates in
    # the fingerprint while keeping the full text in facts/review.md.
    fingerprint_payload = {
        "schema": "microclaw.rig-inventory-fingerprint/v1",
        "facts": _fingerprint_facts(facts),
    }
    fingerprint = hashlib.sha256(json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {
        "schema": "microclaw.rig-inventory/v1",
        "mm_config": config_record,
        "live_inventory_fingerprint": {"algorithm": "sha256", "value": fingerprint},
        "facts": facts,
        "heuristic_candidates": {
            "suspected_continuous_actuators": sorted(powers, key=lambda x: x["path"]),
            "illumination_enable_properties": sorted(enables, key=lambda x: x["path"]),
            "illumination_power_enable_pairs": sorted(pairs, key=lambda x: (x["power_path"], x["enable_path"])),
            "unclassified_writable_properties": sorted(unclassified),
            "properties_with_unknown_writability": sorted(unknown_writability),
        },
        "human_decisions": {"source": None, "comparison": None},
    }


def _declared_paths(parsed: Any, core: Any, loaded_devices: list[str]) -> set[str]:
    paths = {f"{d}.{p}" for d, p in parsed.rig_profile.categorical_properties | parsed.rig_profile.excluded_properties}
    paths |= {f"{x.device}.{x.property}" for x in parsed.constraints.forbidden_properties}
    illum = parsed.constraints.illumination
    illumination_pairs = {
        (x.device, x.property) for x in (*illum.shutters, *illum.power_properties)
    }
    paths |= {f"{device}.{prop}" for device, prop in illumination_pairs}
    paths |= {
        f"{device}.{prop}"
        for device, prop in _auto_classified_state_pairs(
            core, parsed, loaded_devices, illumination_pairs
        )
    }
    return paths


def compare_reviewed_config(inventory: dict, parsed: Any, source: str, core: Any) -> None:
    devices = inventory["facts"]["devices"]
    live = {
        f'{device["label"]}.{prop["name"]}'
        for device in devices
        for prop in device["properties"]
        if prop.get("read_only") is False and prop.get("pre_init") is False
    }
    declared = _declared_paths(parsed, core, [d["label"] for d in devices])
    inventory["human_decisions"] = {
        "source": source,
        "comparison": {
            "declared_paths_missing_from_live_rig": sorted(declared - live),
            "live_paths_missing_from_reviewed_config": sorted(live - declared),
        },
    }


def render_review(inventory: dict) -> str:
    candidates = inventory["heuristic_candidates"]
    facts = inventory["facts"]
    comparison = inventory["human_decisions"].get("comparison") or {}
    device_count = len(facts["devices"])
    repeated = {}
    for failure in facts["enumeration_failures"]:
        if failure["scope"].startswith("device:"):
            key = (failure["field"], failure["error"])
            repeated.setdefault(key, set()).add(failure["scope"])
    systemic = [
        f"{field}: identical failure on every device ({device_count}/{device_count}): {error}"
        for (field, error), scopes in sorted(repeated.items())
        if device_count and len(scopes) == device_count
    ]
    sections = [
        ("Unclassified writable properties", candidates["unclassified_writable_properties"]),
        ("Properties with unknown writability", candidates["properties_with_unknown_writability"]),
        ("Suspected continuous actuators", [x["path"] for x in candidates["suspected_continuous_actuators"]]),
        ("Illumination power/enable pairs", [f'{x["power_path"]} ↔ {x["enable_path"]}' for x in candidates["illumination_power_enable_pairs"]]),
        ("Preset effects", [f'{g["name"]}.{p["name"]}: ' + ", ".join(f'{e["device"]}.{e["property"]}={e["value"]}' for e in p["effects"]) for g in facts["configuration_groups"] for p in g["presets"]]),
        ("Systemic enumeration failures", systemic),
        ("Enumeration failures", [f'{x["scope"]} / {x["field"]}: {x["error"]}' for x in facts["enumeration_failures"]]),
        ("Reviewed declarations missing from live rig", comparison.get("declared_paths_missing_from_live_rig", [])),
        ("Live paths missing from reviewed config", comparison.get("live_paths_missing_from_reviewed_config", [])),
    ]
    lines = ["# Rig inventory review", "", "Candidates below are questions for a human, not safety decisions or approvals.", ""]
    for title, rows in sections:
        lines.extend([f"## {title}", ""])
        lines.extend([f"- {row}" for row in rows] or ["- None observed."])
        lines.append("")
    return "\n".join(lines)


def write_inventory_outputs(inventory: dict, out: str | Path) -> tuple[Path, Path]:
    directory = Path(out)
    directory.mkdir(parents=True, exist_ok=True)
    inventory_path = directory / "inventory.json"
    review_path = directory / "review.md"
    inventory_path.write_text(json.dumps(inventory, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    review_path.write_text(render_review(inventory), encoding="utf-8")
    return inventory_path, review_path
