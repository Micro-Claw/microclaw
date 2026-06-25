"""
Integration tests against a real Micro-Manager Demo configuration.

Prerequisites:
  1. Open Micro-Manager and load MMConfig_demo.cfg.
  2. Set MM_RUNNING=1 (and optionally MM_PORT if not using the default 4827).
  3. Run: pytest -m integration

  Windows CMD:        set MM_RUNNING=1 && pytest -m integration
  Windows PowerShell: $env:MM_RUNNING=1; pytest -m integration
  macOS/Linux:        MM_RUNNING=1 pytest -m integration

The headless_mm fixture is session-scoped: MM starts once and all tests share
the same connection. Tests that move hardware restore the original position
before returning.
"""
import base64
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _first_channel(headless_mm) -> str | None:
    from microclaw.tools import _str_vector
    channels = _str_vector(headless_mm.core.get_available_configs("Channel"))
    return channels[0] if channels else None


# ---------------------------------------------------------------------------
# System state and device discovery
# ---------------------------------------------------------------------------

def test_system_state_has_all_fields(headless_mm, unconstrained_guard):
    from microclaw.tools import get_system_state
    state = get_system_state(headless_mm, unconstrained_guard)
    assert "x_um" in state
    assert "y_um" in state
    assert "z_um" in state
    assert "exposure_ms" in state


def test_list_devices_includes_demo_devices(headless_mm, unconstrained_guard):
    from microclaw.tools import list_devices
    result = list_devices(headless_mm, unconstrained_guard)
    devices = result["devices"]
    # Demo config always loads at least a camera and a focus stage
    assert len(devices) >= 2


def test_list_device_properties_camera(headless_mm, unconstrained_guard):
    from microclaw.tools import list_device_properties
    result = list_device_properties(headless_mm, unconstrained_guard, device="Camera")
    assert "properties" in result
    assert result["count"] > 0
    assert "Binning" in result["properties"]


def test_get_device_property_info_binning(headless_mm, unconstrained_guard):
    from microclaw.tools import get_device_property_info
    result = get_device_property_info(
        headless_mm, unconstrained_guard, device="Camera", property="Binning"
    )
    assert result["device"] == "Camera"
    assert result["property"] == "Binning"
    assert result["read_only"] is False
    assert result["allowed_values"] is not None  # Binning is an enum in Demo
    assert result["current_value"] is not None


def test_get_device_property_info_read_only_label(headless_mm, unconstrained_guard):
    from microclaw.tools import get_device_property_info, list_device_properties
    props = list_device_properties(headless_mm, unconstrained_guard, device="Camera")["properties"]
    # Find at least one read-only property (Demo camera exposes several)
    read_only_found = False
    for p in props:
        info = get_device_property_info(headless_mm, unconstrained_guard, device="Camera", property=p)
        if info["read_only"]:
            read_only_found = True
            break
    # Demo camera always has at least one read-only property (e.g. CCDTemperature)
    assert read_only_found


def test_get_full_device_state_camera(headless_mm, unconstrained_guard):
    from microclaw.tools import get_full_device_state
    result = get_full_device_state(headless_mm, unconstrained_guard, device="Camera")
    assert result["device"] == "Camera"
    assert "Binning" in result["state"]
    assert len(result["state"]) > 0


def test_full_device_state_values_match_individual_reads(headless_mm, unconstrained_guard):
    from microclaw.tools import get_device_property, get_full_device_state
    full = get_full_device_state(headless_mm, unconstrained_guard, device="Camera")
    binning_full = full["state"]["Binning"]
    binning_single = get_device_property(
        headless_mm, unconstrained_guard, device="Camera", property="Binning"
    )["value"]
    assert binning_full == binning_single


# ---------------------------------------------------------------------------
# Camera and exposure
# ---------------------------------------------------------------------------

def test_set_and_get_exposure(headless_mm, unconstrained_guard):
    from microclaw.tools import set_exposure, get_exposure
    original = get_exposure(headless_mm, unconstrained_guard)["exposure_ms"]
    set_exposure(headless_mm, unconstrained_guard, ms=50.0)
    assert abs(get_exposure(headless_mm, unconstrained_guard)["exposure_ms"] - 50.0) < 0.1
    set_exposure(headless_mm, unconstrained_guard, ms=original)  # restore


def test_snap_and_analyze_returns_stats(headless_mm, unconstrained_guard):
    from microclaw.tools import snap_and_analyze
    result = snap_and_analyze(headless_mm, unconstrained_guard)
    assert isinstance(result, dict)
    for field in ("focus_metric", "mean_intensity", "max_intensity", "saturated_fraction", "z_um"):
        assert field in result, f"Missing field: {field}"


def test_snap_and_analyze_returns_valid_png_when_requested(headless_mm, unconstrained_guard):
    from microclaw.tools import snap_and_analyze
    result = snap_and_analyze(headless_mm, unconstrained_guard, return_thumbnail=True)
    assert isinstance(result, list)
    payload = json.loads(result[0]["text"])
    for field in ("focus_metric", "mean_intensity", "max_intensity", "saturated_fraction", "z_um"):
        assert field in payload, f"Missing field: {field}"
    png_bytes = base64.standard_b64decode(result[1]["source"]["data"])
    assert png_bytes[:4] == b"\x89PNG", "Thumbnail is not a valid PNG"


def test_snap_and_analyze_focus_metric_positive(headless_mm, unconstrained_guard):
    from microclaw.tools import snap_and_analyze
    result = snap_and_analyze(headless_mm, unconstrained_guard)
    assert result["focus_metric"] >= 0.0


def test_snap_and_analyze_intensity_in_range(headless_mm, unconstrained_guard):
    from microclaw.tools import snap_and_analyze
    result = snap_and_analyze(headless_mm, unconstrained_guard)
    assert 0.0 <= result["mean_intensity"] <= 65535.0
    assert 0.0 <= result["saturated_fraction"] <= 1.0


def test_snap_and_analyze_thumbnail_size(headless_mm, unconstrained_guard):
    from PIL import Image
    from microclaw.tools import snap_and_analyze
    result = snap_and_analyze(headless_mm, unconstrained_guard, return_thumbnail=True, thumbnail_size=128)
    png_bytes = base64.standard_b64decode(result[1]["source"]["data"])
    import io
    pil = Image.open(io.BytesIO(png_bytes))
    assert max(pil.size) <= 128


def test_get_pixel_size_returns_numeric(headless_mm, unconstrained_guard):
    from microclaw.tools import get_pixel_size
    result = get_pixel_size(headless_mm, unconstrained_guard)
    assert isinstance(result["pixel_size_um"], float)
    assert result["pixel_size_um"] >= 0.0


def test_get_pixel_size_zero_includes_warning(headless_mm, unconstrained_guard):
    # The Demo config typically has no pixel size calibration, so this verifies
    # that a 0.0 result is accompanied by an actionable warning. If the demo
    # does have calibration the test is skipped rather than failing.
    from microclaw.tools import get_pixel_size
    result = get_pixel_size(headless_mm, unconstrained_guard)
    if result["pixel_size_um"] == 0.0:
        assert "warning" in result
        assert "calibration" in result["warning"].lower()
    else:
        pytest.skip("Demo config has pixel size calibration set; zero-warning path not exercised")


# ---------------------------------------------------------------------------
# Z stage
# ---------------------------------------------------------------------------

def test_move_and_return_z(headless_mm, unconstrained_guard):
    from microclaw.tools import get_z_position, move_stage_z
    original = get_z_position(headless_mm, unconstrained_guard)["z_um"]
    move_stage_z(headless_mm, unconstrained_guard, z_um=original + 5.0)
    assert abs(get_z_position(headless_mm, unconstrained_guard)["z_um"] - (original + 5.0)) < 0.1
    move_stage_z(headless_mm, unconstrained_guard, z_um=original)


def test_move_stage_z_relative(headless_mm, unconstrained_guard):
    from microclaw.tools import get_z_position, move_stage_z
    original = get_z_position(headless_mm, unconstrained_guard)["z_um"]
    move_stage_z(headless_mm, unconstrained_guard, z_um=3.0, absolute=False)
    assert abs(get_z_position(headless_mm, unconstrained_guard)["z_um"] - (original + 3.0)) < 0.1
    move_stage_z(headless_mm, unconstrained_guard, z_um=original)


def test_move_stage_z_blocked_by_safety(headless_mm):
    from microclaw.safety import SafetyConstraints, SafetyGuard, StageConstraints
    from microclaw.tools import get_z_position, move_stage_z
    current_z = get_z_position(headless_mm, SafetyGuard(SafetyConstraints()))["z_um"]
    tight_guard = SafetyGuard(
        SafetyConstraints(stage=StageConstraints(z_min=current_z + 1.0, z_max=current_z + 100.0))
    )
    from microclaw.safety import SafetyViolation
    with pytest.raises(SafetyViolation):
        move_stage_z(headless_mm, tight_guard, z_um=current_z)  # below z_min


# ---------------------------------------------------------------------------
# XY stage
# ---------------------------------------------------------------------------

def test_move_and_return_xy(headless_mm, unconstrained_guard):
    from microclaw.tools import get_xy_position, move_stage_xy
    orig = get_xy_position(headless_mm, unconstrained_guard)
    move_stage_xy(headless_mm, unconstrained_guard, x_um=orig["x_um"] + 10.0, y_um=orig["y_um"] + 10.0)
    new = get_xy_position(headless_mm, unconstrained_guard)
    assert abs(new["x_um"] - (orig["x_um"] + 10.0)) < 0.5
    assert abs(new["y_um"] - (orig["y_um"] + 10.0)) < 0.5
    move_stage_xy(headless_mm, unconstrained_guard, x_um=orig["x_um"], y_um=orig["y_um"])


def test_move_stage_xy_relative(headless_mm, unconstrained_guard):
    from microclaw.tools import get_xy_position, move_stage_xy
    orig = get_xy_position(headless_mm, unconstrained_guard)
    move_stage_xy(headless_mm, unconstrained_guard, x_um=5.0, y_um=-5.0, absolute=False)
    new = get_xy_position(headless_mm, unconstrained_guard)
    assert abs(new["x_um"] - (orig["x_um"] + 5.0)) < 0.5
    assert abs(new["y_um"] - (orig["y_um"] - 5.0)) < 0.5
    move_stage_xy(headless_mm, unconstrained_guard, x_um=orig["x_um"], y_um=orig["y_um"])


# ---------------------------------------------------------------------------
# Channels
# ---------------------------------------------------------------------------

def test_get_available_channels_nonempty(headless_mm, unconstrained_guard):
    from microclaw.tools import get_available_channels
    result = get_available_channels(headless_mm, unconstrained_guard)
    assert len(result["channels"]) >= 1


def test_set_channel_roundtrip(headless_mm, unconstrained_guard):
    from microclaw.tools import get_available_channels, set_channel
    channels = get_available_channels(headless_mm, unconstrained_guard)["channels"]
    if not channels:
        pytest.skip("No channels in Demo config")
    original = channels[0]
    target = channels[-1]  # may be same as original if only one channel
    set_channel(headless_mm, unconstrained_guard, preset=target)
    set_channel(headless_mm, unconstrained_guard, preset=original)  # restore


# ---------------------------------------------------------------------------
# Acquisitions (Z-stack and timelapse)
# ---------------------------------------------------------------------------

def test_zstack_creates_dataset(headless_mm, unconstrained_guard, tmp_path):
    from microclaw.tools import run_zstack
    result = run_zstack(
        headless_mm, unconstrained_guard,
        z_start_um=45.0, z_end_um=55.0, z_step_um=2.0,
        save_dir=str(tmp_path), name="test_stack",
    )
    assert result["status"] == "Z-stack complete."
    assert Path(result["dataset_path"]).exists()


def test_zstack_with_channel(headless_mm, unconstrained_guard, tmp_path):
    from microclaw.tools import run_zstack
    channel = _first_channel(headless_mm)
    if channel is None:
        pytest.skip("No channels in Demo config")
    result = run_zstack(
        headless_mm, unconstrained_guard,
        z_start_um=48.0, z_end_um=52.0, z_step_um=2.0,
        channel=channel,
        save_dir=str(tmp_path), name="channel_stack",
    )
    assert result["status"] == "Z-stack complete."


def test_timelapse_creates_dataset(headless_mm, unconstrained_guard, tmp_path):
    from microclaw.tools import run_timelapse
    result = run_timelapse(
        headless_mm, unconstrained_guard,
        n_frames=3, interval_s=0,
        save_dir=str(tmp_path), name="test_timelapse",
    )
    assert result["status"] == "Timelapse complete."
    assert Path(result["dataset_path"]).exists()


def test_zstack_export_tiff(headless_mm, unconstrained_guard, tmp_path):
    import tifffile
    from microclaw.tools import export_dataset_as_tiff, run_zstack
    zstack_result = run_zstack(headless_mm, unconstrained_guard,
                               z_start_um=45.0, z_end_um=50.0, z_step_um=1.0,
                               save_dir=str(tmp_path), name="stack")
    out = str(tmp_path / "out.tiff")
    result = export_dataset_as_tiff(
        headless_mm, unconstrained_guard,
        dataset_path=zstack_result["dataset_path"], output_path=out,
    )
    assert result["status"] == "Export complete."
    img = tifffile.imread(out)
    assert img.ndim >= 2


def test_timelapse_export_tiff(headless_mm, unconstrained_guard, tmp_path):
    import tifffile
    from microclaw.tools import export_dataset_as_tiff, run_timelapse
    tl_result = run_timelapse(headless_mm, unconstrained_guard,
                              n_frames=3, interval_s=0,
                              save_dir=str(tmp_path), name="tl")
    out = str(tmp_path / "timelapse.tiff")
    result = export_dataset_as_tiff(
        headless_mm, unconstrained_guard,
        dataset_path=tl_result["dataset_path"], output_path=out,
    )
    assert result["status"] == "Export complete."
    img = tifffile.imread(out)
    assert img.ndim >= 2


# ---------------------------------------------------------------------------
# Autofocus
# ---------------------------------------------------------------------------

def test_autofocus_sweep_method(headless_mm, unconstrained_guard):
    from microclaw.tools import run_autofocus
    result = run_autofocus(
        headless_mm, unconstrained_guard, z_range_um=10, z_step_um=2, method="sweep"
    )
    payload = json.loads(result[0]["text"]) if isinstance(result, list) else result
    assert "best_z_um" in payload
    assert len(payload["metric_curve"]) >= 2


def test_autofocus_coarse_then_fine_method(headless_mm, unconstrained_guard):
    from microclaw.tools import run_autofocus
    result = run_autofocus(
        headless_mm, unconstrained_guard, z_range_um=10, z_step_um=0.5,
        method="coarse_then_fine",
    )
    payload = json.loads(result[0]["text"]) if isinstance(result, list) else result
    assert "best_z_um" in payload


def test_autofocus_no_thumbnail(headless_mm, unconstrained_guard):
    from microclaw.tools import run_autofocus
    result = run_autofocus(
        headless_mm, unconstrained_guard, z_range_um=6, z_step_um=2,
        return_thumbnail=False,
    )
    assert isinstance(result, dict)
    assert "best_z_um" in result
    assert "metric_curve" in result


def test_autofocus_metric_curve_length(headless_mm, unconstrained_guard):
    from microclaw.tools import run_autofocus
    result = run_autofocus(
        headless_mm, unconstrained_guard, z_range_um=8, z_step_um=2,
        method="sweep", return_thumbnail=False,
    )
    assert isinstance(result, dict)
    # sweep from current-4 to current+4 in steps of 2 → 5 positions
    assert len(result["metric_curve"]) == len(result["z_positions"])
    assert all(isinstance(v, float) for v in result["metric_curve"])


# ---------------------------------------------------------------------------
# Position list workflow
# ---------------------------------------------------------------------------

def test_mark_multiple_positions_and_list(headless_mm, unconstrained_guard):
    from microclaw.tools import clear_position_list, get_position_list, mark_position
    clear_position_list(headless_mm, unconstrained_guard)
    orig_xy = (headless_mm.core.get_x_position(), headless_mm.core.get_y_position())

    mark_position(headless_mm, unconstrained_guard, name="Alpha")
    headless_mm.core.set_xy_position(orig_xy[0] + 20.0, orig_xy[1] + 20.0)
    headless_mm.core.wait_for_device(headless_mm.core.get_xy_stage_device())
    mark_position(headless_mm, unconstrained_guard, name="Beta")

    result = get_position_list(headless_mm, unconstrained_guard)
    names = [p["name"] for p in result["positions"]]
    assert "Alpha" in names
    assert "Beta" in names
    assert result["count"] == 2

    # restore XY
    headless_mm.core.set_xy_position(*orig_xy)
    headless_mm.core.wait_for_device(headless_mm.core.get_xy_stage_device())
    clear_position_list(headless_mm, unconstrained_guard)


def test_go_to_position(headless_mm, unconstrained_guard):
    from microclaw.tools import clear_position_list, go_to_position, mark_position
    clear_position_list(headless_mm, unconstrained_guard)
    orig = (headless_mm.core.get_x_position(), headless_mm.core.get_y_position())

    mark_position(headless_mm, unconstrained_guard, name="Home")

    # Move away, then return via go_to_position
    headless_mm.core.set_xy_position(orig[0] + 30.0, orig[1] + 30.0)
    headless_mm.core.wait_for_device(headless_mm.core.get_xy_stage_device())

    result = go_to_position(headless_mm, unconstrained_guard, name="Home")
    assert "Moved" in result["status"]

    final_x = headless_mm.core.get_x_position()
    final_y = headless_mm.core.get_y_position()
    assert abs(final_x - orig[0]) < 1.0
    assert abs(final_y - orig[1]) < 1.0

    clear_position_list(headless_mm, unconstrained_guard)


def test_delete_position(headless_mm, unconstrained_guard):
    from microclaw.tools import clear_position_list, delete_position, get_position_list, mark_position
    clear_position_list(headless_mm, unconstrained_guard)
    mark_position(headless_mm, unconstrained_guard, name="ToDelete")
    mark_position(headless_mm, unconstrained_guard, name="ToKeep")
    delete_position(headless_mm, unconstrained_guard, name="ToDelete")
    names = [p["name"] for p in get_position_list(headless_mm, unconstrained_guard)["positions"]]
    assert "ToDelete" not in names
    assert "ToKeep" in names
    clear_position_list(headless_mm, unconstrained_guard)


def test_clear_position_list(headless_mm, unconstrained_guard):
    from microclaw.tools import clear_position_list, get_position_list, mark_position
    mark_position(headless_mm, unconstrained_guard, name="TempPos")
    clear_position_list(headless_mm, unconstrained_guard)
    result = get_position_list(headless_mm, unconstrained_guard)
    assert result["count"] == 0


def test_position_list_save_and_load(headless_mm, unconstrained_guard, tmp_path):
    from microclaw.tools import (
        clear_position_list,
        get_position_list,
        load_position_list,
        mark_position,
        save_position_list,
    )
    clear_position_list(headless_mm, unconstrained_guard)
    mark_position(headless_mm, unconstrained_guard, name="SavedPos")
    pos_file = str(tmp_path / "positions.pos")
    save_position_list(headless_mm, unconstrained_guard, path=pos_file)

    clear_position_list(headless_mm, unconstrained_guard)
    assert get_position_list(headless_mm, unconstrained_guard)["count"] == 0

    load_position_list(headless_mm, unconstrained_guard, path=pos_file)
    names = [p["name"] for p in get_position_list(headless_mm, unconstrained_guard)["positions"]]
    assert "SavedPos" in names
    clear_position_list(headless_mm, unconstrained_guard)


# ---------------------------------------------------------------------------
# Multiposition acquisition
# ---------------------------------------------------------------------------

def test_multiposition_snap(headless_mm, unconstrained_guard, tmp_path):
    from microclaw.tools import clear_position_list, mark_position, run_multiposition_acquisition
    clear_position_list(headless_mm, unconstrained_guard)
    orig = (headless_mm.core.get_x_position(), headless_mm.core.get_y_position())

    mark_position(headless_mm, unconstrained_guard, name="MP1")
    headless_mm.core.set_xy_position(orig[0] + 20.0, orig[1])
    headless_mm.core.wait_for_device(headless_mm.core.get_xy_stage_device())
    mark_position(headless_mm, unconstrained_guard, name="MP2")

    result = run_multiposition_acquisition(
        headless_mm, unconstrained_guard,
        position_names=["MP1", "MP2"],
        protocol="snap",
        save_dir=str(tmp_path),
    )
    assert "2/2" in result["status"]
    assert all("error" not in r for r in result["results"])

    headless_mm.core.set_xy_position(*orig)
    headless_mm.core.wait_for_device(headless_mm.core.get_xy_stage_device())
    clear_position_list(headless_mm, unconstrained_guard)


def test_multiposition_zstack(headless_mm, unconstrained_guard, tmp_path):
    from microclaw.tools import clear_position_list, mark_position, run_multiposition_acquisition
    clear_position_list(headless_mm, unconstrained_guard)
    orig_xy = (headless_mm.core.get_x_position(), headless_mm.core.get_y_position())
    orig_z = headless_mm.core.get_position()

    mark_position(headless_mm, unconstrained_guard, name="ZPos1")
    headless_mm.core.set_xy_position(orig_xy[0] + 15.0, orig_xy[1])
    headless_mm.core.wait_for_device(headless_mm.core.get_xy_stage_device())
    mark_position(headless_mm, unconstrained_guard, name="ZPos2")

    result = run_multiposition_acquisition(
        headless_mm, unconstrained_guard,
        position_names=["ZPos1", "ZPos2"],
        protocol="zstack",
        save_dir=str(tmp_path),
        protocol_params={"z_start_um": orig_z - 2.0, "z_end_um": orig_z + 2.0, "z_step_um": 2.0},
    )
    assert "2/2" in result["status"]

    headless_mm.core.set_xy_position(*orig_xy)
    headless_mm.core.wait_for_device(headless_mm.core.get_xy_stage_device())
    headless_mm.core.set_position(orig_z)
    headless_mm.core.wait_for_device(headless_mm.core.get_focus_device())
    clear_position_list(headless_mm, unconstrained_guard)


def test_multiposition_missing_position_returns_error(headless_mm, unconstrained_guard, tmp_path):
    from microclaw.tools import clear_position_list, run_multiposition_acquisition
    clear_position_list(headless_mm, unconstrained_guard)
    result = run_multiposition_acquisition(
        headless_mm, unconstrained_guard,
        position_names=["DoesNotExist"],
        protocol="snap",
        save_dir=str(tmp_path),
    )
    assert result["results"][0]["error"] == "Not found in position list."


# ---------------------------------------------------------------------------
# Multiposition acquisition with autofocus
# ---------------------------------------------------------------------------

def test_multiposition_with_autofocus_snap(headless_mm, unconstrained_guard, tmp_path):
    from microclaw.tools import (
        clear_position_list,
        mark_position,
        run_multiposition_with_autofocus,
    )
    clear_position_list(headless_mm, unconstrained_guard)
    orig_xy = (headless_mm.core.get_x_position(), headless_mm.core.get_y_position())

    mark_position(headless_mm, unconstrained_guard, name="AF1")
    headless_mm.core.set_xy_position(orig_xy[0] + 20.0, orig_xy[1])
    headless_mm.core.wait_for_device(headless_mm.core.get_xy_stage_device())
    mark_position(headless_mm, unconstrained_guard, name="AF2")

    result = run_multiposition_with_autofocus(
        headless_mm, unconstrained_guard,
        position_names=["AF1", "AF2"],
        z_range_um=6.0, z_step_um=2.0,
        protocol="snap",
        save_dir=str(tmp_path),
    )
    assert "2/2" in result["status"]
    for r in result["results"]:
        assert "error" not in r
        assert "best_z_um" in r

    headless_mm.core.set_xy_position(*orig_xy)
    headless_mm.core.wait_for_device(headless_mm.core.get_xy_stage_device())
    clear_position_list(headless_mm, unconstrained_guard)


def test_multiposition_with_autofocus_autofocus_range_blocked(headless_mm):
    from microclaw.safety import SafetyConstraints, SafetyGuard, StageConstraints
    from microclaw.tools import clear_position_list, mark_position, run_multiposition_with_autofocus
    current_z = headless_mm.core.get_position()
    tight = SafetyGuard(
        SafetyConstraints(
            stage=StageConstraints(z_min=current_z - 0.5, z_max=current_z + 0.5)
        )
    )
    clear_position_list(headless_mm, SafetyGuard(SafetyConstraints()))
    mark_position(headless_mm, SafetyGuard(SafetyConstraints()), name="TightPos")
    result = run_multiposition_with_autofocus(
        headless_mm, tight,
        position_names=["TightPos"],
        z_range_um=10.0,  # extends beyond ±0.5 limit
        z_step_um=1.0,
        protocol="snap",
        save_dir="/tmp",
    )
    assert "error" in result["results"][0]
    assert "Autofocus range out of bounds" in result["results"][0]["error"]
    clear_position_list(headless_mm, SafetyGuard(SafetyConstraints()))


# ---------------------------------------------------------------------------
# Hook-based adaptive acquisition
# ---------------------------------------------------------------------------

def test_autofocus_hook(headless_mm, unconstrained_guard, tmp_path):
    from microclaw.tools import run_adaptive_zstack
    log_path = str(tmp_path / "af_log.json")
    result = run_adaptive_zstack(
        headless_mm, unconstrained_guard,
        z_start_um=45, z_end_um=55, z_step_um=2,
        save_dir=str(tmp_path), name="af_hook_test",
        hook_strategy="autofocus_per_position",
        hook_params={"z_range_um": 5, "z_step_um": 1.0},
        log_path=log_path,
    )
    assert "complete" in result["status"]
    log = json.loads(Path(log_path).read_text())
    assert len(log) > 0
    # Each log entry should have best_z_um
    for entry in log:
        assert "best_z_um" in entry or "autofocus" in entry  # skipped entries have "autofocus" key


def test_focus_feedback_hook(headless_mm, unconstrained_guard, tmp_path):
    from microclaw.tools import run_adaptive_zstack
    log_path = str(tmp_path / "focus_log.json")
    # Single-slice "stack" so image_process_fn is called at least once
    current_z = headless_mm.core.get_position()
    result = run_adaptive_zstack(
        headless_mm, unconstrained_guard,
        z_start_um=current_z, z_end_um=current_z + 2.0, z_step_um=1.0,
        save_dir=str(tmp_path), name="focus_feedback_test",
        hook_strategy="focus_feedback",
        hook_params={"threshold_fraction": 0.5, "z_step_um": 0.5, "max_jogs": 2},
        log_path=log_path,
    )
    assert "complete" in result["status"]
    # Log file is written only if focus correction triggered; absence is also valid
    if Path(log_path).exists():
        log = json.loads(Path(log_path).read_text())
        for entry in log:
            assert "focus_correction" in entry


def test_focus_feedback_hook_on_timelapse(headless_mm, unconstrained_guard, tmp_path):
    # focus_feedback is a per-frame timelapse hook; this exercises the path that
    # was previously impossible when adaptive acquisition was Z-stack-only.
    from microclaw.tools import run_adaptive_timelapse
    log_path = str(tmp_path / "focus_tl_log.json")
    result = run_adaptive_timelapse(
        headless_mm, unconstrained_guard,
        n_frames=3, interval_s=0.0,
        save_dir=str(tmp_path), name="focus_feedback_tl_test",
        hook_strategy="focus_feedback",
        hook_params={"threshold_fraction": 0.5, "z_step_um": 0.5, "max_jogs": 2},
        log_path=log_path,
    )
    assert "complete" in result["status"]
    # Log file is written only if focus correction triggered; absence is also valid
    if Path(log_path).exists():
        log = json.loads(Path(log_path).read_text())
        for entry in log:
            assert "focus_correction" in entry


def test_unknown_hook_strategy_timelapse_returns_error(headless_mm, unconstrained_guard, tmp_path):
    from microclaw.tools import run_adaptive_timelapse
    result = run_adaptive_timelapse(
        headless_mm, unconstrained_guard,
        n_frames=2, interval_s=0.0,
        save_dir=str(tmp_path), name="bad_strategy_tl",
        hook_strategy="nonexistent_hook",
    )
    assert "error" in result


def test_intensity_adaptive_hook(headless_mm, unconstrained_guard, tmp_path):
    from microclaw.tools import get_system_state, run_adaptive_zstack
    log_path = str(tmp_path / "intensity_log.json")
    state = get_system_state(headless_mm, unconstrained_guard)
    current_z = state["z_um"]
    result = run_adaptive_zstack(
        headless_mm, unconstrained_guard,
        z_start_um=current_z, z_end_um=current_z + 2.0, z_step_um=1.0,
        save_dir=str(tmp_path), name="intensity_test",
        hook_strategy="intensity_adaptive",
        hook_params={"target_mean": 1000.0, "tolerance": 0.5, "max_exposure_ms": 2000.0},
        log_path=log_path,
    )
    assert "complete" in result["status"]


def test_position_filter_hook_passes_nonzero_images(headless_mm, unconstrained_guard, tmp_path):
    from microclaw.tools import run_adaptive_zstack
    log_path = str(tmp_path / "filter_log.json")
    current_z = headless_mm.core.get_position()
    # Set threshold to 0 — Demo camera images should always pass
    result = run_adaptive_zstack(
        headless_mm, unconstrained_guard,
        z_start_um=current_z, z_end_um=current_z + 2.0, z_step_um=1.0,
        save_dir=str(tmp_path), name="filter_pass_test",
        hook_strategy="position_filter",
        hook_params={"min_mean_intensity": 0.0},
        log_path=log_path,
    )
    assert "complete" in result["status"]
    # With threshold=0, no positions should be rejected; log should be empty or absent
    if Path(log_path).exists():
        log = json.loads(Path(log_path).read_text())
        assert len(log) == 0  # no rejections


def test_position_filter_hook_rejects_bright_threshold(headless_mm, unconstrained_guard, tmp_path):
    from microclaw.tools import run_adaptive_zstack
    log_path = str(tmp_path / "filter_reject_log.json")
    current_z = headless_mm.core.get_position()
    # Set threshold above any real intensity — all positions should be rejected
    result = run_adaptive_zstack(
        headless_mm, unconstrained_guard,
        z_start_um=current_z, z_end_um=current_z + 2.0, z_step_um=1.0,
        save_dir=str(tmp_path), name="filter_reject_test",
        hook_strategy="position_filter",
        hook_params={"min_mean_intensity": 1e9},
        log_path=log_path,
    )
    assert "complete" in result["status"]
    log = json.loads(Path(log_path).read_text())
    assert len(log) > 0
    assert all(entry["action"] == "rejected" for entry in log)


def test_unknown_hook_strategy_returns_error(headless_mm, unconstrained_guard, tmp_path):
    from microclaw.tools import run_adaptive_zstack
    current_z = headless_mm.core.get_position()
    result = run_adaptive_zstack(
        headless_mm, unconstrained_guard,
        z_start_um=current_z, z_end_um=current_z + 2.0, z_step_um=1.0,
        save_dir=str(tmp_path), name="bad_strategy",
        hook_strategy="nonexistent_hook",
    )
    assert "error" in result


# ---------------------------------------------------------------------------
# Hook log reading
# ---------------------------------------------------------------------------

def test_read_hook_log_after_acquisition(headless_mm, unconstrained_guard, tmp_path):
    from microclaw.tools import read_hook_log, run_adaptive_zstack
    log_path = str(tmp_path / "log.json")
    run_adaptive_zstack(
        headless_mm, unconstrained_guard,
        z_start_um=45, z_end_um=51, z_step_um=2,
        save_dir=str(tmp_path), name="log_test",
        hook_strategy="autofocus_per_position",
        hook_params={"z_range_um": 4, "z_step_um": 1.0},
        log_path=log_path,
    )
    result = read_hook_log(headless_mm, unconstrained_guard, log_path=log_path)
    assert "entries" in result
    assert result["entry_count"] == len(result["entries"])


def test_read_hook_log_missing_file(headless_mm, unconstrained_guard):
    from microclaw.tools import read_hook_log
    result = read_hook_log(headless_mm, unconstrained_guard, log_path="/no/such/file.json")
    assert "error" in result


# ---------------------------------------------------------------------------
# Hook management
# ---------------------------------------------------------------------------

def test_list_hooks_shows_precoded(headless_mm, unconstrained_guard):
    from microclaw.tools import list_hooks
    result = list_hooks(headless_mm, unconstrained_guard)
    for name in ("autofocus_per_position", "focus_feedback", "intensity_adaptive", "position_filter"):
        assert name in result["precoded"]
    assert "saved" in result


def test_generate_save_and_use_custom_hook(headless_mm, unconstrained_guard, tmp_path, monkeypatch):
    import microclaw.hook_manager as hm
    monkeypatch.setattr(hm, "HOOKS_DIR", tmp_path)
    monkeypatch.setattr(hm, "MANIFEST", tmp_path / "manifest.json")

    from microclaw.tools import generate_and_save_hook, list_hooks, run_adaptive_zstack

    code = (
        "from __future__ import annotations\n"
        "import json\n"
        "from pathlib import Path\n"
        "import numpy as np\n"
        "\n"
        "class MeanLogger:\n"
        "    def __init__(self, log_path=None):\n"
        "        self.log_path = log_path\n"
        "        self._log = []\n"
        "\n"
        "    def image_process_fn(self, image: np.ndarray, metadata: dict, event_queue):\n"
        "        self._log.append({'mean': float(np.mean(image))})\n"
        "        if self.log_path:\n"
        "            Path(self.log_path).write_text(json.dumps(self._log, indent=2))\n"
        "        return image, metadata\n"
    )

    save_result = generate_and_save_hook(
        headless_mm, unconstrained_guard,
        name="mean_logger", code=code,
        description="Logs mean intensity per image",
        source="claude_generated",
    )
    assert "saved" in save_result["status"]

    hooks = list_hooks(headless_mm, unconstrained_guard)
    assert "mean_logger" in hooks["saved"]
    assert hooks["saved"]["mean_logger"]["source"] == "claude_generated"

    log_path = str(tmp_path / "mean_log.json")
    current_z = headless_mm.core.get_position()
    result = run_adaptive_zstack(
        headless_mm, unconstrained_guard,
        z_start_um=current_z, z_end_um=current_z + 2.0, z_step_um=1.0,
        save_dir=str(tmp_path), name="custom_hook_run",
        hook_strategy="mean_logger",
        hook_params={},
        log_path=log_path,
    )
    assert "complete" in result["status"]
    log = json.loads(Path(log_path).read_text())
    assert len(log) > 0
    assert all("mean" in entry for entry in log)


# ---------------------------------------------------------------------------
# execute_tool return type integration
# ---------------------------------------------------------------------------

def test_execute_tool_snap_and_analyze_default_returns_json_string(headless_mm, unconstrained_guard):
    from microclaw.tools import execute_tool
    result = execute_tool("snap_and_analyze", {}, headless_mm, unconstrained_guard)
    assert isinstance(result, str)
    payload = json.loads(result)
    assert "focus_metric" in payload


def test_execute_tool_snap_and_analyze_with_thumbnail_returns_list(headless_mm, unconstrained_guard):
    from microclaw.tools import execute_tool
    result = execute_tool("snap_and_analyze", {"return_thumbnail": True}, headless_mm, unconstrained_guard)
    assert isinstance(result, list)
    assert result[0]["type"] == "text"
    assert result[1]["type"] == "image"


def test_execute_tool_returns_json_string_for_state_tool(headless_mm, unconstrained_guard):
    from microclaw.tools import execute_tool
    result = execute_tool("get_system_state", {}, headless_mm, unconstrained_guard)
    assert isinstance(result, str)
    parsed = json.loads(result)
    assert "z_um" in parsed


def test_execute_tool_unknown_returns_error_json(headless_mm, unconstrained_guard):
    from microclaw.tools import execute_tool
    result = execute_tool("does_not_exist", {}, headless_mm, unconstrained_guard)
    assert isinstance(result, str)
    assert "error" in json.loads(result)
