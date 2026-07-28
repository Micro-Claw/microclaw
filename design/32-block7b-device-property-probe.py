"""Block 7b: enumerate every device property on the rig, READ-ONLY.

Answers "which properties could an illumination envelope name?" without asking a
human to hand-list them. Written because M5's authorization map showed only two
declared power properties (both iBeamSmartCW) and no 405 nm path at all, so the
Block 7b UV steps had nowhere to point.

>>> THIS SCRIPT WRITES NOTHING TO THE HARDWARE. <<<
It calls only get_* / is_* / has_* on the core. It never calls set_property,
set_position, set_shutter_open, or any acquisition entry point. It moves no
stage, opens no shutter, and takes no exposure. Reading a property is the only
hardware interaction, and a Micro-Manager property read is a query to a driver
that is already holding the value.

What it produces:

  1. `devices` — every loaded device, its MM type, and every property with its
     current value, type, read-only flag, allowed values, and limits.
  2. `illumination_candidates` — properties that look like continuous power or
     intensity controls, with whether the reviewed config already declares them.
  3. `enable_candidates` — the on/off property that sits on the same device as a
     power candidate. A power declaration without its enable is half a policy.
  4. `proposed_yaml` — a reviewable `illumination:` block for the undeclared
     candidates.

**The proposal is a starting point for human review, not a config to paste.**
design/33 makes declaration a deliberate human act, and the Block 3b rig gate
caught an implementation that auto-widened a laser engine's write surface
without any config edit. A heuristic that matches on property names is exactly
the kind of thing that gate existed to stop. Read every proposed row against the
device in front of you before it goes anywhere near a rig config.

Run on the rig with Micro-Manager open and the pycro-manager ZMQ server enabled:

    uv run python design/32-block7b-device-property-probe.py --port 4827 ^
        --config <reviewed M5 safety config> --out probe.json > probe.txt 2>&1

--config is optional. Without it the probe still enumerates, but cannot say
which candidates are already declared.
"""

import argparse
import json
import re
import sys

from microclaw.authorization import device_type_name
from microclaw.controller import MicroscopeController


sys.stdout.reconfigure(line_buffering=True)

# Name heuristics. Deliberately broad — a missed candidate is invisible, whereas
# a false positive is discarded by the human reading the proposal.
_POWER_NAME = re.compile(
    r"power|intens|laser\s*level|percent|\bpwr\b|\bmw\b|\bamplitude\b", re.I
)
_ENABLE_NAME = re.compile(
    r"operation|enable|shutter|on/?off|\bstate\b|emission|output", re.I
)
_ON_VALUES = {"on", "1", "true", "open", "enabled", "yes"}


def _strings(vector) -> list[str]:
    """mmcorej StrVector -> list[str]. Bridge collections are not iterable."""
    if vector is None:
        return []
    if hasattr(vector, "size"):
        return [str(vector.get(i)) for i in range(vector.size())]
    return [str(item) for item in vector]


def _property_record(core, device: str, prop: str) -> dict:
    """Read one property defensively: a raising device must not end the sweep."""
    record: dict = {"property": prop}
    for key, call in (
        ("current_value", lambda: str(core.get_property(device, prop))),
        ("read_only", lambda: bool(core.is_property_read_only(device, prop))),
        ("pre_init", lambda: bool(core.is_property_pre_init(device, prop))),
        ("allowed_values", lambda: _strings(core.get_allowed_property_values(device, prop))),
        ("has_limits", lambda: bool(core.has_property_limits(device, prop))),
    ):
        try:
            record[key] = call()
        except Exception as exc:
            record[key] = None
            record.setdefault("errors", []).append(f"{key}: {exc!r}")
    if record.get("has_limits"):
        for key, call in (
            ("lower_limit", lambda: float(core.get_property_lower_limit(device, prop))),
            ("upper_limit", lambda: float(core.get_property_upper_limit(device, prop))),
        ):
            try:
                record[key] = call()
            except Exception as exc:
                record[key] = None
                record.setdefault("errors", []).append(f"{key}: {exc!r}")
    return record


def _is_power_candidate(record: dict) -> bool:
    """Continuous, writable, and named like a level control."""
    if record.get("read_only") or record.get("pre_init"):
        return False
    if not _POWER_NAME.search(record["property"]):
        return False
    # A discrete allowed-value set is a categorical control, not a level.
    if record.get("allowed_values"):
        return False
    if record.get("has_limits"):
        return True
    # No declared limits: accept only if the current value parses as a number,
    # so a free-text property named "Power Supply Serial" is not a candidate.
    try:
        float(record.get("current_value"))
    except (TypeError, ValueError):
        return False
    return True


def _is_enable_candidate(record: dict) -> bool:
    if record.get("read_only") or record.get("pre_init"):
        return False
    if not _ENABLE_NAME.search(record["property"]):
        return False
    allowed = record.get("allowed_values") or []
    if not 0 < len(allowed) <= 4:
        return False
    return any(str(value).strip().lower() in _ON_VALUES for value in allowed)


def _declared_pairs(config_path: str) -> tuple[set, set, dict]:
    from microclaw.config import load_safety_config_or_exit

    parsed = load_safety_config_or_exit(config_path)
    illum = parsed.constraints.illumination
    powers = {(item.device, item.property) for item in illum.power_properties}
    shutters = {(item.device, item.property) for item in illum.shutters}
    limits = {
        "max_power_percent": illum.max_power_percent,
        "max_power_step_factor": illum.max_power_step_factor,
        "require_confirm_on_enable": illum.require_confirm_on_enable,
    }
    return powers, shutters, limits


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--config", help="reviewed safety config, to mark declared pairs")
    parser.add_argument("--out", required=True, help="JSON output path")
    args = parser.parse_args()

    declared_power: set = set()
    declared_shutters: set = set()
    limits: dict = {}
    if args.config:
        declared_power, declared_shutters, limits = _declared_pairs(args.config)
        print(f"Config declares {len(declared_power)} power and "
              f"{len(declared_shutters)} shutter properties.")
        print(f"  limits: {limits}")

    print(f"Connecting to Micro-Manager on port {args.port} (read-only)...")
    ctrl = MicroscopeController(port=args.port)
    core = ctrl.core

    devices: list[dict] = []
    power_candidates: list[dict] = []
    enable_candidates: list[dict] = []

    for label in _strings(core.get_loaded_devices()):
        try:
            kind = device_type_name(core, label)
        except Exception as exc:
            kind = f"<unresolved: {exc!r}>"
        entry = {"device": label, "type": kind, "properties": []}
        try:
            names = _strings(core.get_device_property_names(label))
        except Exception as exc:
            entry["error"] = repr(exc)
            devices.append(entry)
            print(f"  {label} ({kind}): property enumeration failed: {exc!r}")
            continue
        for prop in names:
            record = _property_record(core, label, prop)
            entry["properties"].append(record)
            if _is_power_candidate(record):
                power_candidates.append({
                    "device": label, "device_type": kind, "property": prop,
                    "current_value": record.get("current_value"),
                    "lower_limit": record.get("lower_limit"),
                    "upper_limit": record.get("upper_limit"),
                    "declared": (label, prop) in declared_power,
                })
            if _is_enable_candidate(record):
                enable_candidates.append({
                    "device": label, "device_type": kind, "property": prop,
                    "current_value": record.get("current_value"),
                    "allowed_values": record.get("allowed_values"),
                    "declared": (label, prop) in declared_shutters,
                })
        devices.append(entry)
        print(f"  {label} ({kind}): {len(names)} properties")

    # Only propose an enable that shares a device with a power candidate.
    power_devices = {c["device"] for c in power_candidates}
    proposal_powers = [c for c in power_candidates if not c["declared"]]
    proposal_enables = [
        c for c in enable_candidates
        if not c["declared"] and c["device"] in power_devices
    ]

    lines = ["illumination:"]
    if proposal_enables:
        lines.append("  shutters:")
        for c in proposal_enables:
            on = next(
                (v for v in (c["allowed_values"] or [])
                 if str(v).strip().lower() in _ON_VALUES),
                "<on value>",
            )
            lines.append(f"    - device: {c['device']}")
            lines.append(f"      property: {c['property']}")
            lines.append(f"      on_value: {on!r}    # allowed: {c['allowed_values']}")
    if proposal_powers:
        lines.append("  power_properties:")
        for c in proposal_powers:
            span = ""
            if c["upper_limit"] is not None:
                span = f"    # driver range {c['lower_limit']}..{c['upper_limit']}"
            lines.append(f"    - device: {c['device']}{span}")
            lines.append(f"      property: {c['property']}")
    proposed_yaml = "\n".join(lines) if len(lines) > 1 else ""

    payload = {
        "port": args.port,
        "config": args.config,
        "configured_limits": limits,
        "devices": devices,
        "illumination_candidates": power_candidates,
        "enable_candidates": enable_candidates,
        "proposed_yaml": proposed_yaml,
    }
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)

    print("\n" + "=" * 70)
    print("CANDIDATE ILLUMINATION POWER PROPERTIES")
    print("=" * 70)
    for c in power_candidates:
        mark = "declared" if c["declared"] else "UNDECLARED"
        span = ""
        if c["upper_limit"] is not None:
            span = f"  range {c['lower_limit']}..{c['upper_limit']}"
        print(f"  [{mark:10}] {c['device']}.{c['property']} "
              f"= {c['current_value']}{span}   ({c['device_type']})")
    if not power_candidates:
        print("  none matched. Widen _POWER_NAME and re-run before concluding "
              "the rig has no power control.")

    print("\n" + "=" * 70)
    print("CANDIDATE ENABLE/SHUTTER PROPERTIES ON THOSE DEVICES")
    print("=" * 70)
    for c in enable_candidates:
        if c["device"] not in power_devices:
            continue
        mark = "declared" if c["declared"] else "UNDECLARED"
        print(f"  [{mark:10}] {c['device']}.{c['property']} "
              f"= {c['current_value']}  allowed={c['allowed_values']}")

    if proposed_yaml:
        print("\n" + "=" * 70)
        print("PROPOSED illumination: BLOCK  --  REVIEW EVERY ROW, DO NOT PASTE BLIND")
        print("=" * 70)
        print(proposed_yaml)
        print("""
Before any of this reaches a rig config:

  * Confirm each device actually emits light. A name match is not evidence.
  * Confirm the UNITS of each power property. If it reads in mW, note that
    illumination.max_power_percent is compared against the raw value and is
    NOT a percentage there (design/33 line 562, and design/32 Block 7b).
  * Declaring a power property makes it writable by a generated hook inside a
    pre-authorized envelope. Declaring its enable makes it confirm-gated for a
    human. Those are different decisions; make each one deliberately.
  * The Block 3b rig gate refused a laser engine's State write that had been
    auto-admitted without a config edit. This proposal is a heuristic; that
    refusal is the standard it has to meet.
""")

    print(f"\nFull enumeration written to {args.out}")
    print("No hardware was written to by this script.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
