"""design/29 probe — can MM be the source of pixel→stage affine calibration?

Read-only: takes no exposure, moves no stage, and changes no configuration. Run
on the rig with Micro-Manager and microclaw's ZMQ server running:

    python design/29-mm-pixel-affine-probe.py
    python design/29-mm-pixel-affine-probe.py --dataset /path/to/saved.ndtiff

This answers three separate questions:

1. Does MM expose a finite, nonsingular full pixel→stage affine?
2. Does its pixel-size configuration select that affine using the relevant
   camera/objective/binning state?
3. Does a saved dataset identify or embed the exact affine used to acquire it?

A positive result does not make an MM pixel-size config name an immutable
calibration identity. Configs are mutable. design/29 must still canonicalize,
hash, and embed the exact resolved affine payload in each mosaic result manifest.

The remaining empirical convention check is intentionally not performed here:
verify the decoded matrix once against a known saved tile pair, or in a separate
explicitly authorized move/snap probe, before replacing microclaw's solver.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from typing import Any


def _as_floats(value: Any) -> list[float]:
    """Normalize an MMCore DoubleVector-ish bridge value."""
    try:
        return [float(x) for x in value]
    except TypeError:
        out, i = [], 0
        while True:
            try:
                out.append(float(value.get(i)))
            except Exception:
                break
            i += 1
        return out


def _as_strings(value: Any) -> list[str]:
    try:
        return [str(x) for x in value]
    except TypeError:
        out, i = [], 0
        while True:
            try:
                out.append(str(value.get(i)))
            except Exception:
                break
            i += 1
        return out


def _decode_java_affine(raw: list[float]) -> dict[str, Any]:
    """Decode java.awt.geom.AffineTransform.getMatrix ordering.

    Java returns [m00, m10, m01, m11, m02, m12], representing:

        stage_x = m00 * pixel_x + m01 * pixel_y + m02
        stage_y = m10 * pixel_x + m11 * pixel_y + m12
    """
    if len(raw) != 6:
        raise ValueError(f"expected six Java affine values, got {len(raw)}: {raw}")
    m00, m10, m01, m11, m02, m12 = raw
    det = m00 * m11 - m01 * m10
    x_scale = math.hypot(m00, m10)
    y_scale = math.hypot(m01, m11)
    dot = m00 * m01 + m10 * m11
    normalized_dot = dot / (x_scale * y_scale) if x_scale and y_scale else math.nan
    return {
        "raw_java_order": raw,
        "linear_pixel_to_stage": [[m00, m01], [m10, m11]],
        "translation_um": [m02, m12],
        "determinant": det,
        "reflection": det < 0,
        "x_scale_um_per_px": x_scale,
        "y_scale_um_per_px": y_scale,
        "x_axis_angle_deg": math.degrees(math.atan2(m10, m00)),
        "normalized_axis_dot": normalized_dot,
    }


def _canonical_payload(decoded: dict[str, Any]) -> tuple[str, str]:
    payload = json.dumps(decoded, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return payload, hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _classify(decoded: dict[str, Any]) -> str:
    matrix = decoded["linear_pixel_to_stage"]
    a, b = matrix[0]
    c, d = matrix[1]
    det = decoded["determinant"]
    if not all(math.isfinite(x) for x in (a, b, c, d, det)):
        return "INVALID — non-finite coefficient"
    if math.isclose(det, 0.0, abs_tol=1e-12):
        return "INVALID — singular/empty affine"
    off_axis = not (math.isclose(b, 0.0, abs_tol=1e-9)
                    and math.isclose(c, 0.0, abs_tol=1e-9))
    anisotropic = not math.isclose(
        decoded["x_scale_um_per_px"], decoded["y_scale_um_per_px"], rel_tol=1e-3)
    if off_axis or anisotropic or decoded["reflection"]:
        return "FULL GEOMETRY PRESENT — rotation/anisotropy/reflection is encoded"
    return ("USABLE BUT INCONCLUSIVE — axis-aligned isotropic geometry may be a real "
            "calibration or a scalar-derived affine; shape alone cannot distinguish them")


def _report_affine(label: str, raw: list[float]) -> dict[str, Any] | None:
    print(label)
    try:
        decoded = _decode_java_affine(raw)
        payload, digest = _canonical_payload(decoded)
    except (ValueError, TypeError) as error:
        print("  INVALID:", error)
        return None
    print("  raw Java order          :", decoded["raw_java_order"])
    print("  pixel->stage 2x2        :", decoded["linear_pixel_to_stage"])
    print("  translation (ignored)   :", decoded["translation_um"])
    print("  determinant/reflection  :", decoded["determinant"], decoded["reflection"])
    print("  x/y scale um/px         :", decoded["x_scale_um_per_px"],
          decoded["y_scale_um_per_px"])
    print("  x-axis angle deg        :", decoded["x_axis_angle_deg"])
    print("  normalized axis dot     :", decoded["normalized_axis_dot"])
    print("  canonical SHA-256       :", digest)
    print("  canonical payload       :", payload)
    print("  verdict                 :", _classify(decoded))
    return decoded


def _call_string(core: Any, method: str) -> str:
    try:
        return str(getattr(core, method)())
    except Exception as error:
        return f"ERR {error}"


def _report_build(core: Any) -> None:
    print("== Micro-Manager/MMCore build identity ==")
    for method in ("get_version_info", "get_api_version_info"):
        print(f"{method:<28}:", _call_string(core, method))


def _report_camera_state(core: Any) -> None:
    print("\n== camera, ROI, and image-pipeline state ==")
    try:
        cam = str(core.get_camera_device())
        print("camera device            :", repr(cam))
    except Exception as error:
        print("camera device            : ERR", error)
        return
    for method in ("get_image_width", "get_image_height", "get_bytes_per_pixel",
                   "get_number_of_camera_channels"):
        try:
            print(f"{method:<25}:", getattr(core, method)())
        except Exception as error:
            print(f"{method:<25}: ERR", error)
    try:
        print("ROI                      :", list(core.get_roi()))
    except Exception as error:
        print("ROI                      : ERR", error)
    interesting = re.compile(r"bin|transpose|mirror|flip|rotat|orientation", re.I)
    try:
        for prop in _as_strings(core.get_device_property_names(cam)):
            if interesting.search(prop):
                try:
                    value = str(core.get_property(cam, prop))
                    print(f"  property {prop!r:<28}:", repr(value))
                except Exception as error:
                    print(f"  property {prop!r:<28}: ERR", error)
    except Exception as error:
        print("camera properties        : ERR", error)


def _report_objective(core: Any) -> None:
    print("\n== objective-like state (best effort) ==")
    pattern = re.compile(r"obj|nosepiece|turret", re.I)
    found = False
    try:
        for group in _as_strings(core.get_available_config_groups()):
            if pattern.search(group):
                try:
                    print(f"config group {group!r}:", repr(str(core.get_current_config(group))))
                    found = True
                except Exception as error:
                    print(f"config group {group!r}: ERR", error)
    except Exception as error:
        print("config groups            : ERR", error)
    try:
        for device in _as_strings(core.get_loaded_devices()):
            if pattern.search(device):
                try:
                    label = str(core.get_state_label(device))
                except Exception:
                    try:
                        label = str(core.get_property(device, "Label"))
                    except Exception as error:
                        label = f"ERR {error}"
                print(f"device {device!r} label:", repr(label))
                found = True
    except Exception as error:
        print("loaded devices           : ERR", error)
    if not found:
        print("objective                : not found")


def _setting_value(setting: Any, *names: str) -> str:
    for name in names:
        try:
            value = getattr(setting, name)
            return str(value() if callable(value) else value)
        except Exception:
            pass
    return "<?>"


def _report_config_rules(core: Any, config: str) -> None:
    """Print the device/property predicates selecting a pixel-size config."""
    try:
        data = core.get_pixel_size_config_data(config)
    except Exception as error:
        print("    selection rules       : ERR", error)
        return
    try:
        size = int(data.size())
        if size == 0:
            print("    selection rules       : []")
        for index in range(size):
            setting = data.get_setting(index)
            device = _setting_value(setting, "get_device_label", "getDeviceLabel")
            prop = _setting_value(setting, "get_property_name", "getPropertyName")
            value = _setting_value(setting, "get_property_value", "getPropertyValue")
            print(f"    rule[{index}]              : {device!r}.{prop!r} == {value!r}")
    except Exception as error:
        print("    selection rules       : ERR bridge shape:", error, repr(data))


def _report_dataset_metadata(path: str) -> None:
    print("\n== saved-dataset affine/config provenance ==")
    print("dataset                  :", path)
    try:
        from ndstorage import Dataset

        dataset = Dataset(path)
    except Exception as error:
        print("open dataset             : ERR", error)
        return
    candidates: list[tuple[str, Any]] = []
    for name in ("summary_metadata", "summary", "metadata"):
        try:
            value = getattr(dataset, name)
            candidates.append((name, value() if callable(value) else value))
        except Exception:
            pass
    pattern = re.compile(r"affine|pixel.?size|calibr|objective|binning|camera|roi", re.I)

    def walk(value: Any, prefix: str = "") -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                child_prefix = f"{prefix}.{key}" if prefix else str(key)
                if pattern.search(child_prefix):
                    print(f"  {child_prefix:<40}:", repr(child))
                if isinstance(child, (dict, list, tuple)):
                    walk(child, child_prefix)
        elif isinstance(value, (list, tuple)):
            for index, child in enumerate(value):
                walk(child, f"{prefix}[{index}]")

    if not candidates:
        print("summary metadata         : unavailable through this ndstorage API")
        return
    for name, metadata in candidates:
        print(f"metadata source {name!r}:")
        walk(metadata)
    print("NOTE: absence here means historical calibration identity is not proven; inspect")
    print("      raw NDTiff metadata if the installed ndstorage API omits summary fields.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", help="optional saved NDTiff to inspect; never modifies it")
    args = parser.parse_args()

    from microclaw.controller import MicroscopeController

    ctrl = MicroscopeController()
    core = ctrl.core
    _report_build(core)
    _report_camera_state(core)
    _report_objective(core)

    print("\n== scalar and pixel-size config identity ==")
    try:
        print("pixel_size_um            :", float(core.get_pixel_size_um()))
    except Exception as error:
        print("pixel_size_um            : ERR", error)
    try:
        current = str(core.get_current_pixel_size_config())
        print("current config           :", repr(current))
    except Exception as error:
        current = None
        print("current config           : ERR", error)
    try:
        configs = _as_strings(core.get_available_pixel_size_configs())
        print("available configs        :", configs)
    except Exception as error:
        configs = []
        print("available configs        : ERR", error)

    print("\n== current affine ==")
    current_raw = None
    try:
        current_raw = _as_floats(core.get_pixel_size_affine())
        _report_affine("get_pixel_size_affine():", current_raw)
    except Exception as error:
        print("get_pixel_size_affine    : ERR", error)

    print("\n== per-config affine and matching rules ==")
    current_by_id = None
    for config in configs:
        marker = "  <-- current" if config == current else ""
        print(f"config {config!r}{marker}")
        _report_config_rules(core, config)
        try:
            raw = _as_floats(core.get_pixel_size_affine_by_id(config))
            _report_affine("    affine:", raw)
            if config == current:
                current_by_id = raw
        except Exception as error:
            print("    affine               : ERR", error)

    print("\n== current-vs-ID consistency ==")
    if current_raw is None or current_by_id is None:
        print("comparison               : unavailable")
    else:
        print("exact raw equality       :", current_raw == current_by_id)
        print("current raw              :", current_raw)
        print("by-ID raw                :", current_by_id)

    if args.dataset:
        _report_dataset_metadata(args.dataset)

    print("\n== interpretation boundary ==")
    print("This probe does not prove Java/Python row-column signs against real imagery,")
    print("config immutability, or persistence across an MM restart. Capture this output,")
    print("restart/reload MM, rerun it, and compare config rules plus affine SHA-256.")
    print("Before redesigning design/29, also validate one known saved tile displacement")
    print("against the decoded 2x2 matrix. Keep the per-mosaic resolved payload manifest.")


if __name__ == "__main__":
    main()
