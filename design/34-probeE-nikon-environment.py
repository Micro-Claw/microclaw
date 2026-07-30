"""Read-only: reads Nikon/MM identity, assignments, limits, objective, and properties.

This probe changes no device or configuration. Its cleanup contract is read-only.
Discoverable APIs/properties do not establish what Studio's Stage Control invokes.
"""
from __future__ import annotations
import argparse, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent; sys.path.insert(0, str(HERE))
import nikon_kit_common as kit
PROBE = "probeE-nikon-environment"

def dump(core, device):
    result = {}
    for prop in kit.strings(core.get_device_property_names(device)):
        item = {}
        try: item["value"] = str(core.get_property(device, prop))
        except Exception as e: item["read_error"] = str(e)
        for name, method in (("lower_limit", "get_property_lower_limit"), ("upper_limit", "get_property_upper_limit")):
            try: item[name] = float(getattr(core, method)(device, prop))
            except Exception: pass
        try: item["read_only"] = bool(core.is_property_read_only(device, prop))
        except Exception: pass
        try: item["allowed_values"] = kit.strings(core.get_allowed_property_values(device, prop))
        except Exception: pass
        result[prop] = item
    return result

def objective(core):
    found = []
    for device in kit.strings(core.get_loaded_devices()):
        if any(word in device.lower() for word in ("obj", "nose", "turret")):
            try: value = str(core.get_state_label(device))
            except Exception:
                try: value = str(core.get_property(device, "Label"))
                except Exception as e: value = {"unavailable": str(e)}
            found.append({"device": device, "value": value})
    return found

def body(core, payload, log):
    payload["property_dumps"] = {d: dump(core, d) for d in kit.DEVICES}
    payload["current_objective_candidates"] = objective(core)
    limits = {}
    for key, method in (("lower", "get_stage_lower_limit"), ("upper", "get_stage_upper_limit")):
        try: limits[key] = float(getattr(core, method)("TIPFSOffset"))
        except Exception as error: limits[key] = {"unavailable": str(error)}
    payload["tipfs_offset_stage_limits_um"] = limits
    payload["claim_boundary"] = "This inventory does not establish the Studio Stage Control call path."
    log.append("Environment capture completed; no state was changed.")
    print("Environment capture completed.")

if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__); kit.add_common_arguments(p)
    args = p.parse_args(); raise SystemExit(kit.run_probe(PROBE, Path(__file__), args, body, None))
