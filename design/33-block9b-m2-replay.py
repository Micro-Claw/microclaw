"""Replay a real probe JSON and loaded Micro-Manager config through inspect-rig.

This is an offline scale/shape spike, not a live rig test.  The Block 7b probe
never called ``get_property_type``; JSON contains no Java proxy, so the bridge
conversion checked by demo-gate Step 3 cannot be exercised.  The recorded M2
probe also has zero real enumeration failures and no credential-like property,
so neither the failure path nor redaction is exercised.  Config groups, adapter
identity, and Label lines are reconstructed from the supplied .cfg; the replay
cannot establish live config-group behavior or state labels absent from it.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


class _Type:
    """Stand in for the device-type enum; this is not a property-type proxy."""

    def __init__(self, name: str):
        self._name = name

    def to_string(self) -> str:
        return self._name


class _Unavailable(Exception):
    """The source artifacts genuinely do not contain this observation."""


def _cfg(path: Path) -> tuple[dict, dict, dict, dict]:
    libraries, names, labels, groups = {}, {}, {}, {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        fields = [field.strip() for field in line.split(",")]
        if fields[0] == "Device" and len(fields) >= 4:
            libraries[fields[1]], names[fields[1]] = fields[2], fields[3]
        elif fields[0] == "Label" and len(fields) >= 4:
            labels.setdefault(fields[1], {})[int(fields[2])] = fields[3]
        elif fields[0] == "ConfigGroup" and len(fields) >= 6:
            groups.setdefault(fields[1], {}).setdefault(fields[2], []).append(
                (fields[3], fields[4], fields[5])
            )
    return libraries, names, labels, groups


class M2ReplayCore:
    def __init__(self, probe_path: Path, cfg_path: Path):
        probe = json.loads(probe_path.read_text(encoding="utf-8"))
        self.devices = {device["device"]: device for device in probe["devices"]}
        self.properties = {
            (device["device"], prop["property"]): prop
            for device in probe["devices"]
            for prop in device.get("properties", [])
        }
        self.libraries, self.names, self.labels, self.groups = _cfg(cfg_path)

    def get_version_info(self): raise _Unavailable("not probed")
    def get_api_version_info(self): raise _Unavailable("not probed")
    def get_camera_device(self): return "Andor"
    def get_focus_device(self): return "PIZStage"
    def get_xy_stage_device(self): return "SmarActXY"
    def get_shutter_device(self): return ""
    def get_auto_focus_device(self): return ""
    def get_galvo_device(self): return ""
    def get_image_processor_device(self): return ""
    def get_slm_device(self): return ""
    def get_loaded_devices(self): return list(self.devices)
    def get_device_type(self, label): return _Type(self.devices[label]["type"])

    def get_device_property_names(self, label):
        device = self.devices[label]
        if "error" in device:
            raise RuntimeError(device["error"])
        return [prop["property"] for prop in device.get("properties", [])]

    def get_device_library(self, label): return self.libraries.get(label, "")
    def get_device_name(self, label): return self.names.get(label, "")
    def get_device_description(self, label): raise _Unavailable("not probed")

    def get_state_labels(self, label):
        values = self.labels.get(label)
        if values is None:
            raise _Unavailable("no Label lines in cfg")
        return [values[index] for index in sorted(values)]

    def _property(self, device, prop): return self.properties[(device, prop)]
    def get_property(self, device, prop): return self._property(device, prop)["current_value"]
    def is_property_read_only(self, device, prop): return self._property(device, prop)["read_only"]
    def is_property_pre_init(self, device, prop): return self._property(device, prop)["pre_init"]
    def get_allowed_property_values(self, device, prop): return list(self._property(device, prop)["allowed_values"])
    def has_property_limits(self, device, prop): return self._property(device, prop)["has_limits"]
    def get_property_lower_limit(self, device, prop): return self._property(device, prop)["lower_limit"]
    def get_property_upper_limit(self, device, prop): return self._property(device, prop)["upper_limit"]
    def get_property_type(self, device, prop): raise _Unavailable("probe never called get_property_type")
    def get_available_config_groups(self): return list(self.groups)
    def get_available_configs(self, group): return list(self.groups.get(group, {}))

    def get_config_data(self, group, preset):
        return [
            {"device": device, "property": prop, "value": value}
            for device, prop, value in self.groups[group][preset]
        ]


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("probe_json", type=Path, help="Block 7b device-property probe JSON")
    parser.add_argument("mm_config", type=Path, help="Micro-Manager .cfg loaded during the probe")
    parser.add_argument("--out", type=Path, help="optional output directory (do not commit its contents)")
    return parser.parse_args()


def main() -> None:
    from microclaw.rig_inventory import enumerate_rig, write_inventory_outputs

    args = _arguments()
    inventory = enumerate_rig(M2ReplayCore(args.probe_json, args.mm_config), mm_config=args.mm_config)
    facts = inventory["facts"]
    candidates = inventory["heuristic_candidates"]
    replay_failures = [
        failure for failure in facts["enumeration_failures"]
        if any(text in failure["error"] for text in ("not probed", "never called", "no Label lines"))
    ]
    real_failures = [failure for failure in facts["enumeration_failures"] if failure not in replay_failures]

    print(
        f"devices {len(facts['devices'])} | properties "
        f"{sum(len(device['properties']) for device in facts['devices'])} | "
        f"groups {len(facts['configuration_groups'])}"
    )
    print(
        f"failures {len(facts['enumeration_failures'])} "
        f"({len(replay_failures)} replay-artifact, {len(real_failures)} real)"
    )
    print(f"unclassified writable {len(candidates['unclassified_writable_properties'])}")
    print(
        f"power candidates {len(candidates['suspected_continuous_actuators'])} | "
        f"enable candidates {len(candidates['illumination_enable_properties'])} | "
        f"candidate groups {len(candidates['illumination_power_enable_groups'])}"
    )
    represented_edges = sum(
        len(group["power_paths"]) * len(group["enable_paths"])
        for group in candidates["illumination_power_enable_groups"]
    )
    print(f"Cartesian relationships asserted 0 (old schema would render {represented_edges})")
    print(
        "possible duplicate power representations "
        f"{len(candidates['possible_duplicate_power_representations'])}"
    )
    for observation in candidates["possible_duplicate_power_representations"]:
        print("  ", observation["device"], ", ".join(
            row["path"] for row in observation["representations"]
        ))
    stable = enumerate_rig(
        M2ReplayCore(args.probe_json, args.mm_config), mm_config=args.mm_config
    )["live_inventory_fingerprint"] == inventory["live_inventory_fingerprint"]
    print("fingerprint stable across two replays", stable)
    if args.out:
        write_inventory_outputs(inventory, args.out)
        print("wrote", args.out)


if __name__ == "__main__":
    main()
