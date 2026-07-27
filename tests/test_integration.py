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
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

# EMU tests are rig/plugin/calibration-specific and opt in separately from the
# general MM integration suite. The name is intentionally not design-numbered:
# future EMU integration tests share this gate.
EMU_RIG = pytest.mark.skipif(
    os.environ.get("MM_EMU") != "1",
    reason="set MM_EMU=1 for EMU rig tests",
)

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
    for field in ("focus_metric", "mean_intensity", "min_intensity", "max_intensity",
                  "saturated_fraction", "z_um"):
        assert field in result, f"Missing field: {field}"


def test_snap_and_analyze_returns_valid_png_when_requested(headless_mm, unconstrained_guard):
    from microclaw.tools import snap_and_analyze
    result = snap_and_analyze(headless_mm, unconstrained_guard, return_thumbnail=True)
    assert isinstance(result, list)
    payload = json.loads(result[0]["text"])
    for field in ("focus_metric", "mean_intensity", "min_intensity", "max_intensity",
                  "saturated_fraction", "z_um"):
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
    # design/14 §4: the payload reports the passes, not a bare best_z_um.
    assert {"converged", "moved", "entry_z_um", "final_z_um", "coarse"} <= set(payload)
    assert len(payload["coarse"]["metric_curve"]) >= 2
    assert payload["fine"] is None, "the 'sweep' method runs a single pass"


def test_autofocus_coarse_then_fine_method(headless_mm, unconstrained_guard):
    from microclaw.tools import run_autofocus
    result = run_autofocus(
        headless_mm, unconstrained_guard, z_range_um=10, z_step_um=0.5,
        method="coarse_then_fine",
    )
    payload = json.loads(result[0]["text"]) if isinstance(result, list) else result
    assert "final_z_um" in payload
    if payload["converged"]:
        # Both passes are visible: the coarse one chose the plane.
        assert payload["coarse"]["metric_curve"]
        assert payload["fine"]["metric_curve"]


def test_autofocus_no_thumbnail(headless_mm, unconstrained_guard):
    from microclaw.tools import run_autofocus
    result = run_autofocus(
        headless_mm, unconstrained_guard, z_range_um=6, z_step_um=2,
        return_thumbnail=False,
    )
    assert isinstance(result, dict)
    assert "final_z_um" in result
    assert "metric_curve" in result["coarse"]


def test_autofocus_metric_curve_length(headless_mm, unconstrained_guard):
    from microclaw.tools import run_autofocus
    result = run_autofocus(
        headless_mm, unconstrained_guard, z_range_um=8, z_step_um=2,
        method="sweep", return_thumbnail=False,
    )
    assert isinstance(result, dict)
    coarse = result["coarse"]
    # sweep from current-4 to current+4 in steps of 2 → 5 positions
    assert len(coarse["metric_curve"]) == len(coarse["z_positions"])
    assert all(isinstance(v, float) for v in coarse["metric_curve"])


def test_autofocus_sweep_window_contains_entry_z(headless_mm, unconstrained_guard):
    """The amr_test bug: the reported curve must be the one that made the call."""
    from microclaw.tools import run_autofocus
    result = run_autofocus(
        headless_mm, unconstrained_guard, z_range_um=10, z_step_um=1,
        return_thumbnail=False,
    )
    zs = result["coarse"]["z_positions"]
    assert min(zs) <= result["entry_z_um"] <= max(zs)


def test_autofocus_metric_curve_not_annihilated_by_rounding(headless_mm, unconstrained_guard):
    """The normalized metric is ~1e-2..1e-4; the curve must not round to zeros.

    On the demo camera a fixed round(v, 2) reported metric_curve=[0.0, 0.0, ...]
    beside contrast=0.52 — a curve the model could learn nothing from.
    """
    from microclaw.tools import run_autofocus
    result = run_autofocus(
        headless_mm, unconstrained_guard, z_range_um=8, z_step_um=2,
        method="sweep", return_thumbnail=False,
    )
    curve = result["coarse"]["metric_curve"]
    if result["coarse"]["contrast"] > 0:
        assert any(v > 0 for v in curve), f"curve lost to rounding: {curve}"


def test_autofocus_restores_z_when_not_converged(headless_mm, unconstrained_guard):
    """converged=false must mean the stage never moved (design/14 §4)."""
    from microclaw.tools import get_z_position, run_autofocus
    entry = get_z_position(headless_mm, unconstrained_guard)["z_um"]
    result = run_autofocus(
        headless_mm, unconstrained_guard, z_range_um=6, z_step_um=2,
        return_thumbnail=False,
    )
    if not result["converged"]:
        assert result["moved"] is False
        assert result["reason"]
        assert result["final_z_um"] == pytest.approx(entry, abs=0.05)
        now = get_z_position(headless_mm, unconstrained_guard)["z_um"]
        assert now == pytest.approx(entry, abs=0.05)
    else:
        assert result["moved"] is True


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


def _clear_mm_native_list(ctrl):
    """Empty MM's own PositionList (the tool-level clear only clears the internal
    store). Mirrors the remove/set_position_list path used by the write-through."""
    pm = ctrl.studio.positions()
    plist = pm.get_position_list()
    for i in reversed(range(int(plist.get_number_of_positions()))):
        plist.remove_position(i)
    pm.set_position_list(plist)


def test_mark_position_writes_through_to_mm_native_list(headless_mm, unconstrained_guard):
    """mark_position must appear in MM's ACTUAL PositionList, read back over the
    Java bridge — exercises _write_position_to_mm (create2_d/create1_d) end-to-end
    and the numAxes read path in one round trip."""
    from microclaw.tools import clear_position_list, mark_position

    _clear_mm_native_list(headless_mm)
    clear_position_list(headless_mm, unconstrained_guard)
    orig = (headless_mm.core.get_x_position(), headless_mm.core.get_y_position())
    before = int(headless_mm.studio.positions().get_position_list().get_number_of_positions())

    mark_position(headless_mm, unconstrained_guard, name="GuiCheck")     # include_z default

    # Read MM's real PositionList back through the Java MultiStagePositions.
    native = headless_mm._read_mm_position_list()
    after = int(headless_mm.studio.positions().get_position_list().get_number_of_positions())

    assert after == before + 1, "position was not added to MM's native list"
    entry = next((p for p in native if p["name"] == "GuiCheck"), None)
    assert entry is not None, f"GuiCheck missing from MM native list: {native}"
    assert abs(entry["x_um"] - orig[0]) < 1.0
    assert abs(entry["y_um"] - orig[1]) < 1.0
    # Z was marked too (create1_d 1-axis stage position must round-trip).
    assert "z_um" in entry, f"Z not written via create1_d: {entry}"

    _clear_mm_native_list(headless_mm)
    clear_position_list(headless_mm, unconstrained_guard)


def test_mark_position_with_supplied_coords_writes_through_without_moving(
    headless_mm, unconstrained_guard
):
    """design/23 F3: mark_position(x_um, y_um) records a KNOWN coordinate into MM's
    native list without moving the stage and without imaging — the whole point of
    the change (Episode B re-imaged keepers just to get them into the list). This
    asserts the write-through path against the real PositionList AND that the stage
    stayed put."""
    from microclaw.tools import clear_position_list, mark_position

    _clear_mm_native_list(headless_mm)
    clear_position_list(headless_mm, unconstrained_guard)
    orig = (headless_mm.core.get_x_position(), headless_mm.core.get_y_position())
    # A coordinate deliberately offset from where the stage sits.
    target_x, target_y = orig[0] + 37.0, orig[1] - 24.0

    result = mark_position(
        headless_mm, unconstrained_guard, name="Known",
        x_um=target_x, y_um=target_y, include_z=False,
    )
    assert result["imaged"] is False
    assert result["stage_moved"] is False

    # The stage must NOT have moved to the supplied coordinate.
    now = (headless_mm.core.get_x_position(), headless_mm.core.get_y_position())
    assert abs(now[0] - orig[0]) < 1.0 and abs(now[1] - orig[1]) < 1.0

    # ...but the supplied coordinate is in MM's real PositionList.
    native = headless_mm._read_mm_position_list()
    entry = next((p for p in native if p["name"] == "Known"), None)
    assert entry is not None, f"Known missing from MM native list: {native}"
    assert abs(entry["x_um"] - target_x) < 1.0
    assert abs(entry["y_um"] - target_y) < 1.0

    _clear_mm_native_list(headless_mm)
    clear_position_list(headless_mm, unconstrained_guard)


def test_remark_label_does_not_duplicate_in_mm_native_list(headless_mm, unconstrained_guard):
    """Re-marking a label replaces the MSP in MM's list rather than duplicating it."""
    from microclaw.tools import clear_position_list, mark_position

    _clear_mm_native_list(headless_mm)
    clear_position_list(headless_mm, unconstrained_guard)
    orig = (headless_mm.core.get_x_position(), headless_mm.core.get_y_position())
    xy_stage = headless_mm.core.get_xy_stage_device()

    mark_position(headless_mm, unconstrained_guard, name="Dup")
    # Move a little, then re-mark the SAME label at the new spot.
    headless_mm.core.set_xy_position(orig[0] + 15.0, orig[1] + 15.0)
    headless_mm.core.wait_for_device(xy_stage)
    mark_position(headless_mm, unconstrained_guard, name="Dup")

    native = headless_mm._read_mm_position_list()
    dup_entries = [p for p in native if p["name"] == "Dup"]
    assert len(dup_entries) == 1, f"label duplicated in MM native list: {native}"
    # The surviving entry reflects the second (moved) mark.
    assert abs(dup_entries[0]["x_um"] - (orig[0] + 15.0)) < 1.0

    # restore stage + lists
    headless_mm.core.set_xy_position(*orig)
    headless_mm.core.wait_for_device(xy_stage)
    _clear_mm_native_list(headless_mm)
    clear_position_list(headless_mm, unconstrained_guard)


def test_delete_position_mirrors_to_mm_native_list(headless_mm, unconstrained_guard):
    """delete_position removes the entry from MM's actual PositionList too."""
    from microclaw.tools import clear_position_list, delete_position, mark_position

    _clear_mm_native_list(headless_mm)
    clear_position_list(headless_mm, unconstrained_guard)
    orig = (headless_mm.core.get_x_position(), headless_mm.core.get_y_position())
    xy_stage = headless_mm.core.get_xy_stage_device()

    mark_position(headless_mm, unconstrained_guard, name="Keep")
    headless_mm.core.set_xy_position(orig[0] + 12.0, orig[1])
    headless_mm.core.wait_for_device(xy_stage)
    mark_position(headless_mm, unconstrained_guard, name="Drop")

    delete_position(headless_mm, unconstrained_guard, name="Drop")

    native_names = [p["name"] for p in headless_mm._read_mm_position_list()]
    assert "Drop" not in native_names, f"Drop still in MM native list: {native_names}"
    assert "Keep" in native_names

    headless_mm.core.set_xy_position(*orig)
    headless_mm.core.wait_for_device(xy_stage)
    _clear_mm_native_list(headless_mm)
    clear_position_list(headless_mm, unconstrained_guard)


def test_clear_position_list_mirrors_to_mm_native_list(headless_mm, unconstrained_guard):
    """clear_position_list empties MM's actual PositionList, not just the store."""
    from microclaw.tools import clear_position_list, mark_position

    _clear_mm_native_list(headless_mm)
    mark_position(headless_mm, unconstrained_guard, name="Tmp1")
    mark_position(headless_mm, unconstrained_guard, name="Tmp2")
    assert int(headless_mm.studio.positions().get_position_list().get_number_of_positions()) >= 2

    clear_position_list(headless_mm, unconstrained_guard)

    after = int(headless_mm.studio.positions().get_position_list().get_number_of_positions())
    assert after == 0, "MM native list not cleared"
    assert headless_mm._read_mm_position_list() == []


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


def test_hooked_grid_log_carries_real_stamped_xy(headless_mm, unconstrained_guard, tmp_path):
    """design/23 F1+F2, the empirical claim the whole fix rests on: a REAL
    multi-position acquisition's metadata carries XPosition_um_Intended /
    YPosition_um_Intended, so HookBase.where() can stamp per-image XY without a
    position-list join. design/19 wrongly concluded these keys never exist (it
    examined a single-position z-stack). This drives a hooked 1x2 grid on the demo
    config and asserts the hook log carries non-null x_um/y_um — if this fails on
    the rig, F1/F2's premise is wrong and Episode A cannot be closed by stamping.

    position_filter with an unreachable threshold forces a log entry at every tile
    (it logs on rejection), and each entry routes through self.log(metadata, ...).
    """
    from microclaw.tools import run_tile_acquisition

    log_path = str(tmp_path / "grid_xy_log.json")
    orig = (headless_mm.core.get_x_position(), headless_mm.core.get_y_position())
    result = run_tile_acquisition(
        headless_mm, unconstrained_guard, rows=1, cols=2, step_um=25.0,
        protocol="timelapse", save_dir=str(tmp_path), name="xygrid",
        protocol_params={"n_frames": 1, "interval_s": 0},
        hook_strategy="position_filter", hook_params={"min_mean_intensity": 1e12},
        log_path=log_path,
    )

    # F1: the result echoes exact per-tile coordinates without a re-scan.
    assert len(result["tiles"]) == 2
    assert all(t["x_um"] is not None for t in result["tiles"])

    # F2: the hook log, stamped from REAL metadata, carries position + stage XY.
    log = json.loads(Path(log_path).read_text())
    assert len(log) == 2, f"expected one entry per tile, got: {log}"
    labels = {e["position"] for e in log}
    assert labels == {"xygrid_r0_c0", "xygrid_r0_c1"}, f"positions not stamped: {log}"
    for entry in log:
        # THE assertion: XPosition_um_Intended was present on real metadata.
        assert entry.get("x_um") is not None, f"no stamped x_um (F2 premise fails): {entry}"
        assert entry.get("y_um") is not None, f"no stamped y_um (F2 premise fails): {entry}"
    # The two tiles are one step_um apart in X, from the metadata, not our arithmetic.
    xs = sorted(e["x_um"] for e in log)
    assert abs((xs[1] - xs[0]) - 25.0) < 2.0

    headless_mm.core.set_xy_position(*orig)
    headless_mm.core.wait_for_device(headless_mm.core.get_xy_stage_device())


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

    # Block 7: this hook keeps its own log, which the saved-hook boundary no
    # longer permits — the trusted parent owns the audit record. Left to run it
    # would take every exposure and record none of its means, so the refusal must
    # arrive before the acquisition, not after. The 2026-07-27 demo gate caught
    # exactly this case; before that it was invisible off-rig, because this test
    # needs the headless_mm fixture.
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
    assert "error" in result and "writes its own log" in result["error"]
    assert "analyze_frame" in result["error"], "the refusal must name the migration"
    assert not Path(log_path).exists(), "nothing may be acquired or logged"


def test_a_saved_hook_that_keeps_no_log_of_its_own_still_runs(
    headless_mm, unconstrained_guard, tmp_path, monkeypatch
):
    """The refusal above is aimed at silent measurement loss, not at legacy
    callbacks as such: a saved hook that makes no logging claim keeps working,
    and the parent records each frame's retained/discarded outcome for it."""
    import microclaw.hook_manager as hm
    monkeypatch.setattr(hm, "HOOKS_DIR", tmp_path)
    monkeypatch.setattr(hm, "MANIFEST", tmp_path / "manifest.json")

    from microclaw.tools import generate_and_save_hook, run_adaptive_zstack

    generate_and_save_hook(
        headless_mm, unconstrained_guard,
        name="quiet_legacy",
        code=(
            "class QuietLegacy:\n"
            "    def image_process_fn(self, image, metadata, event_queue):\n"
            "        return image, metadata\n"
        ),
        description="Legacy callback that keeps no log of its own",
        source="claude_generated",
    )

    log_path = str(tmp_path / "quiet_log.json")
    current_z = headless_mm.core.get_position()
    result = run_adaptive_zstack(
        headless_mm, unconstrained_guard,
        z_start_um=current_z, z_end_um=current_z + 2.0, z_step_um=1.0,
        save_dir=str(tmp_path), name="quiet_hook_run",
        hook_strategy="quiet_legacy", hook_params={}, log_path=log_path,
    )
    assert "complete" in result["status"]
    log = json.loads(Path(log_path).read_text())
    assert log and all(e["event"] == "legacy_hook_frame" for e in log)
    assert all(e["outcome"] == "retained" for e in log)


# ---------------------------------------------------------------------------
# Micro-Manager plugin hooks (design/09)
# ---------------------------------------------------------------------------

def _require_plugin_loader(headless_mm):
    """Skip unless the MM build has the #2401 unified plugin classloader.

    The autofocus-manager path technically predates #2401, but plugin hooks are
    documented as requiring that build, so we gate the live plugin tests on it
    for consistency (and so they self-skip cleanly on older Micro-Manager).
    """
    try:
        headless_mm.plugins._assert_plugin_loader()
    except Exception as e:
        pytest.skip(f"MM build predates PR #2401 (no unified plugin classloader): {e}")


def test_list_mm_plugins_returns_roles(headless_mm, unconstrained_guard):
    # Safe on any MM build: reads studio.plugins() (PluginManager), which does
    # not depend on the #2401 classloader probe.
    from microclaw.tools import list_mm_plugins
    result = list_mm_plugins(headless_mm, unconstrained_guard)
    assert "error" not in result, result
    plugins = result["plugins"]
    for role in ("autofocus", "processor", "menu"):
        assert role in plugins
        assert isinstance(plugins[role], list)
        assert all(isinstance(name, str) for name in plugins[role])  # classpath/name strings
    assert "hint" in result
    # Whether any plugins are actually discoverable is environment-dependent
    # (headless connections and minimal builds legitimately report none), so a
    # genuinely empty result is a skip, not a failure. The structure/marshalling
    # is already verified above.
    all_names = [name for names in plugins.values() for name in names]
    if not all_names:
        pytest.skip(
            "No MM plugins discoverable via studio.plugins() in this instance "
            "(e.g. headless or a minimal build)."
        )


def test_autofocus_mm_plugin_hook_blocked_without_motion_flag(headless_mm, tmp_path):
    # The hardware-motion gate is enforced at hook construction, before any
    # hardware is touched, so this needs neither #2401 nor a working autofocus.
    from microclaw.safety import SafetyConstraints, SafetyGuard, SafetyViolation
    from microclaw.tools import run_adaptive_zstack

    default_motion_off = SafetyGuard(SafetyConstraints())  # allow_hardware_motion=False
    current_z = headless_mm.core.get_position()
    with pytest.raises(SafetyViolation, match="allow_hardware_motion"):
        run_adaptive_zstack(
            headless_mm, default_motion_off,
            z_start_um=current_z, z_end_um=current_z + 2.0, z_step_um=1.0,
            save_dir=str(tmp_path), name="af_plugin_blocked",
            hook_strategy="autofocus_mm_plugin",
            hook_params={},
        )


def test_autofocus_mm_plugin_hook(headless_mm, tmp_path):
    _require_plugin_loader(headless_mm)
    from microclaw.safety import (
        PluginConstraints,
        SafetyConstraints,
        SafetyGuard,
        StageConstraints,
    )
    from microclaw.tools import list_mm_plugins, run_adaptive_zstack

    autofocus_plugins = list_mm_plugins(headless_mm, SafetyGuard(SafetyConstraints()))[
        "plugins"
    ]["autofocus"]
    if not autofocus_plugins:
        pytest.skip("No MM autofocus plugin installed to delegate to.")
    plugin_name = autofocus_plugins[0]

    # Hardware-motion plugin: opt in via the flag, and keep Z limits wide enough
    # that the plugin's chosen focus is in-bounds (the hook guards the result).
    motion_guard = SafetyGuard(
        SafetyConstraints(
            stage=StageConstraints(z_min=-10000, z_max=10000),
            plugins=PluginConstraints(allow_hardware_motion=True),
        )
    )

    log_path = str(tmp_path / "af_plugin_log.json")
    current_z = headless_mm.core.get_position()
    try:
        result = run_adaptive_zstack(
            headless_mm, motion_guard,
            z_start_um=current_z, z_end_um=current_z + 2.0, z_step_um=1.0,
            save_dir=str(tmp_path), name="af_plugin_test",
            hook_strategy="autofocus_mm_plugin",
            hook_params={"plugin_name": plugin_name},
            log_path=log_path,
        )
        assert "complete" in result["status"]
        log = json.loads(Path(log_path).read_text())
        assert len(log) > 0
        # Each entry is either a successful focus (best_z_um) or a logged
        # skip/abort (autofocus key) — the plugin may decline to focus on demo.
        for entry in log:
            assert "best_z_um" in entry or "autofocus" in entry
    finally:
        # The plugin owns the motion; restore Z so later tests start clean.
        headless_mm.core.set_position(current_z)
        headless_mm.core.wait_for_device(headless_mm.core.get_focus_device())


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


# ---------------------------------------------------------------------------
# MM app-dir resolution via the live ImageJ JVM (design/12 open questions)
# ---------------------------------------------------------------------------

def _app_dir_probe_report(ctrl) -> str:
    """Report what each Java probe returns, for when get_mm_app_dir() fails.

    The design/12 lab run traced the failure to a pyjavaz cache collision: all
    static JavaClass shadows share the 'java.lang.Class' cache key, so the
    first-wrapped class wins and later ones (ij.IJ, System) expose its methods
    instead. get_mm_app_dir() now evicts that key via _new_static_java_class and
    falls back to user.dir. This probes through the same eviction helper (the
    real code path) so a lingering failure pinpoints which probe broke — plus a
    raw JavaClass('ij.IJ') to show the un-evicted (collided) proxy for contrast.
    """
    from pycromanager import JavaClass
    from microclaw.controller import _new_static_java_class

    lines = ["get_mm_app_dir() returned no path; probe diagnostics:"]
    raw_type = None
    try:
        raw_type = type(JavaClass("ij.IJ", port=ctrl._port)).__name__
    except Exception as exc:  # noqa: BLE001
        raw_type = f"<raise {exc!r}>"
    lines.append(f"  raw JavaClass('ij.IJ') proxy type (un-evicted): {raw_type}")
    try:
        ij = _new_static_java_class(ctrl._port, "ij.IJ")
        for name in ("get_directory", "getDirectory"):
            fn = getattr(ij, name, None)
            if fn is None:
                lines.append(f"  ij.IJ.{name}: NOT exposed on proxy")
                continue
            try:
                lines.append(f"  ij.IJ.{name}('imagej') -> {fn('imagej')!r}")
            except Exception as exc:  # noqa: BLE001
                lines.append(f"  ij.IJ.{name}('imagej') raised {exc!r}")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"  _new_static_java_class('ij.IJ') raised {exc!r}")
    try:
        system = _new_static_java_class(ctrl._port, "java.lang.System")
        for name in ("get_property", "getProperty"):
            fn = getattr(system, name, None)
            if fn is None:
                lines.append(f"  System.{name}: NOT exposed on proxy")
                continue
            try:
                lines.append(f"  System.{name}('user.dir') -> {fn('user.dir')!r}")
            except Exception as exc:  # noqa: BLE001
                lines.append(f"  System.{name}('user.dir') raised {exc!r}")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"  JavaClass('java.lang.System') construction raised {exc!r}")
    return "\n".join(lines)


def test_get_mm_app_dir_returns_mm_root(headless_mm):
    """Open question 1+2: the JVM reports its install root over the bridge and it
    is the MM root (has plugins/ or mmplugins/), not a user-home ImageJ dir.

    get_mm_app_dir() works around the pyjavaz static-class cache collision found
    in the design/12 lab run (all static JavaClass shadows share the
    'java.lang.Class' key, so the first-wrapped class wins and ij.IJ/System came
    back with the wrong methods): it evicts that key per call and falls back to
    System user.dir. On failure this test dumps every probe so one run says which
    broke.
    """
    from microclaw.emu_manager import _looks_like_mm_dir

    assert headless_mm.is_connected(), (
        "bridge is not connected at the app-dir probe — the shared session "
        "connection dropped during the suite (not an ImageJ problem)"
    )

    app_dir = headless_mm.get_mm_app_dir()
    if not app_dir:
        pytest.fail(_app_dir_probe_report(headless_mm))

    p = Path(app_dir)
    assert p.exists(), f"reported MM app dir does not exist: {p}"
    assert _looks_like_mm_dir(p), (
        f"reported dir {p} lacks plugins/ and mmplugins/ — likely a user-home "
        "ImageJ dir rather than the MM root (open question 1)"
    )


def test_find_mm_app_dir_prefers_live_answer_over_cache(headless_mm, tmp_path, monkeypatch):
    """End-to-end: with a connected scope, find_mm_app_dir resolves the real
    install root and writes it through to the cache, even when the cache and
    path-guessing point elsewhere."""
    from microclaw import emu_manager

    assert headless_mm.is_connected(), "bridge dropped before the app-dir probe"

    # Redirect the cache to a tmp file and neutralise path-guessing so only the
    # live answer can succeed.
    cache_dir = tmp_path / ".microclaw"
    monkeypatch.setattr(emu_manager, "_MICROCLAW_DIR", cache_dir)
    monkeypatch.setattr(emu_manager, "_EMU_CACHE", cache_dir / "emu.json")
    monkeypatch.setattr(emu_manager, "_candidate_mm_dirs", lambda: [])
    # Seed a stale cache pointing at a bogus (nonexistent) dir.
    emu_manager.save_mm_app_dir(str(tmp_path / "stale-nonexistent"))

    result = emu_manager.find_mm_app_dir(headless_mm)

    assert result is not None
    assert emu_manager._looks_like_mm_dir(result)
    # Live answer written through to the cache.
    cached = json.loads(emu_manager._EMU_CACHE.read_text())
    assert cached["mm_app_dir"] == str(result)


# ---------------------------------------------------------------------------
# design/14 — new tool surfaces against a real MM bridge
#
# V1/V2/V3 in design/14 were verified once by a manual spike script. These lock
# those findings in: they exercise the real pyjavaz proxies, not mocks.
# ---------------------------------------------------------------------------

def test_property_type_is_an_enum_name_not_a_heap_address(headless_mm, unconstrained_guard):
    """§11 (V2): over ZMQ, get_property_type returns a pyjavaz proxy whose repr
    embeds a memory address. The old str(...).split('.')[-1] leaked it."""
    from microclaw.tools import get_device_property_info
    info = get_device_property_info(
        headless_mm, unconstrained_guard, device="Camera", property="Exposure"
    )
    assert info["type"] in {"Undef", "String", "Float", "Integer"}
    assert "0x" not in info["type"]
    assert "object at" not in info["type"]


def test_every_camera_property_type_resolves(headless_mm, unconstrained_guard):
    from microclaw.tools import get_device_property_info, list_device_properties
    props = list_device_properties(headless_mm, unconstrained_guard, device="Camera")["properties"]
    for p in props:
        info = get_device_property_info(headless_mm, unconstrained_guard, device="Camera", property=p)
        assert info["type"] in {"Undef", "String", "Float", "Integer"}, (p, info["type"])


def test_snap_and_analyze_displays_and_stamps_metric(headless_mm, unconstrained_guard):
    """§7 (V1) + §10: one exposure reaches the viewer; the metric is stamped."""
    from microclaw.tools import snap_and_analyze
    result = snap_and_analyze(headless_mm, unconstrained_guard)
    assert result["displayed_in_mm_viewer"] is True
    # "_gated": focus_metric now travels with focus_metric_valid + snr (design/25).
    assert result["focus_metric_kind"] == "normalized_laplacian_variance_gated"
    assert set(result["metric_valid_for"]) == {"roi", "exposure_ms", "binning"}
    assert result["focus_metric"] >= 0.0
    assert "focus_metric_valid" in result
    assert "snr" in result


def test_snap_and_analyze_headless_does_not_claim_display(headless_mm, unconstrained_guard):
    from microclaw.tools import snap_and_analyze
    result = snap_and_analyze(headless_mm, unconstrained_guard, display=False)
    assert result["displayed_in_mm_viewer"] is False


def test_snap_and_analyze_survives_live_view(headless_mm, unconstrained_guard):
    """§7 defect (a): the amr_test session crashed here with a Java stack trace.

    V1 also found live().snap(True) under live mode never returns and wedges the
    bridge, so _pause_live must stop live BEFORE snapping, never probe by calling.
    """
    from microclaw.tools import snap_and_analyze, start_live_view, stop_live_view
    start_live_view(headless_mm, unconstrained_guard)
    try:
        result = snap_and_analyze(headless_mm, unconstrained_guard)
        assert "focus_metric" in result
        assert result["live_view"].startswith("paused")
        # Live mode must be running again afterwards.
        assert headless_mm.studio.live().is_live_mode_on()
    finally:
        stop_live_view(headless_mm, unconstrained_guard)


def test_snap_displayed_and_headless_agree_on_geometry(headless_mm, unconstrained_guard):
    """§7 (V1): studio.live().snap(True) and core.snap_image() see one camera."""
    from microclaw.image_analysis import snap_to_numpy, snap_to_numpy_displayed
    from microclaw.tools import _pause_live
    with _pause_live(headless_mm):
        headless = snap_to_numpy(headless_mm)
        displayed = snap_to_numpy_displayed(headless_mm)
    assert headless.shape == displayed.shape
    assert headless.dtype == displayed.dtype


def test_list_stages_classifies_stages_by_device_type(headless_mm, unconstrained_guard):
    """§6 (V3): classification via core.get_device_type, no DeviceType JavaClass.

    Labels are read from the core rather than hardcoded, so this holds on the
    demo config and on a real rig alike.
    """
    from microclaw.tools import list_stages
    focus = str(headless_mm.core.get_focus_device())
    xy = str(headless_mm.core.get_xy_stage_device())
    result = list_stages(headless_mm, unconstrained_guard)
    assert result["focus_device"] == focus
    assert focus in result["single_axis_stages"]
    assert xy in result["xy_stages"]
    # The focus device is driven by move_stage_z, so it is not "other".
    assert focus not in result["other_single_axis"]


def test_get_stage_position_by_label(headless_mm, unconstrained_guard):
    """§6 (V3): core.get_position(label) dispatches over the bridge."""
    from microclaw.tools import get_stage_position, get_z_position
    focus = str(headless_mm.core.get_focus_device())
    by_label = get_stage_position(headless_mm, unconstrained_guard, device=focus)["position_um"]
    by_core = get_z_position(headless_mm, unconstrained_guard)["z_um"]
    assert by_label == pytest.approx(by_core, abs=0.01)


def test_move_named_stage_fails_closed_without_limits(headless_mm, unconstrained_guard):
    """§6: no named_stages entry, no motion — even with an unconstrained guard."""
    from microclaw.safety import SafetyViolation
    from microclaw.tools import get_stage_position, move_named_stage
    focus = str(headless_mm.core.get_focus_device())
    before = get_stage_position(headless_mm, unconstrained_guard, device=focus)["position_um"]
    with pytest.raises(SafetyViolation, match="No limits configured"):
        move_named_stage(headless_mm, unconstrained_guard, device=focus, um=before + 1.0)
    after = get_stage_position(headless_mm, unconstrained_guard, device=focus)["position_um"]
    assert after == pytest.approx(before, abs=0.01)


def test_move_named_stage_with_limits_reports_achieved(headless_mm):
    """§6 (V3): core.set_position(label, pos) moves and reports settling error."""
    from microclaw.safety import NamedStageLimits, SafetyConstraints, SafetyGuard
    from microclaw.tools import get_stage_position, move_named_stage
    focus = str(headless_mm.core.get_focus_device())
    guard = SafetyGuard(SafetyConstraints(
        named_stages=[NamedStageLimits(focus, -1000.0, 1000.0)]
    ))
    before = get_stage_position(headless_mm, guard, device=focus)["position_um"]
    try:
        r = move_named_stage(headless_mm, guard, device=focus, um=1.0, absolute=False)
        assert r["requested_um"] == pytest.approx(before + 1.0, abs=0.01)
        assert r["achieved_um"] == pytest.approx(before + 1.0, abs=0.5)
        assert "error_um" in r
    finally:
        move_named_stage(headless_mm, guard, device=focus, um=before, absolute=True)


def test_move_stage_xy_reports_requested_vs_achieved(headless_mm, unconstrained_guard):
    """§8: the settling error must be visible, not silently swallowed."""
    from microclaw.tools import get_xy_position, move_stage_xy
    orig = get_xy_position(headless_mm, unconstrained_guard)
    try:
        r = move_stage_xy(headless_mm, unconstrained_guard,
                          x_um=orig["x_um"] + 10.0, y_um=orig["y_um"] + 10.0)
        assert r["achieved_um"] == pytest.approx(
            [orig["x_um"] + 10.0, orig["y_um"] + 10.0], abs=1.0
        )
        assert len(r["error_um"]) == 2
    finally:
        move_stage_xy(headless_mm, unconstrained_guard, x_um=orig["x_um"], y_um=orig["y_um"])


def test_find_features_returns_numbers(headless_mm, unconstrained_guard):
    """§9: the field is described by numbers, not by a thumbnail."""
    from microclaw.tools import find_features
    result = find_features(headless_mm, unconstrained_guard)
    assert isinstance(result["n_spots"], int)
    assert "background_level" in result
    if result["offset_from_center_px"] is not None:
        assert len(result["offset_from_center_px"]) == 2


def test_find_features_is_deterministic(headless_mm, unconstrained_guard):
    """The amr_test failure: one field read three different ways."""
    from microclaw.tools import find_features
    a = find_features(headless_mm, unconstrained_guard)
    b = find_features(headless_mm, unconstrained_guard)
    assert a["n_spots"] == b["n_spots"]


def test_center_feature_refuses_without_calibration(headless_mm, unconstrained_guard, monkeypatch):
    from microclaw.tools import center_feature
    monkeypatch.setattr("microclaw.tools._load_current_affine", lambda ctrl: None)
    result = center_feature(headless_mm, unconstrained_guard)
    assert "calibrate_stage_to_camera" in result["error"]


def test_focus_lock_state_is_unknown_on_a_non_emu_rig(headless_mm, unconstrained_guard, monkeypatch):
    """§5: engaged=None means 'unknown' — never a false reassurance of False."""
    from microclaw import tools
    monkeypatch.setattr(tools, "_cached_emu_properties", lambda ctrl: None)
    result = tools.get_focus_lock_state(headless_mm, unconstrained_guard)
    assert result["engaged"] is None
    assert "reason" in result


def test_run_timelapse_without_laser_slot_skips_preflight(headless_mm, unconstrained_guard, tmp_path):
    """§1: the pre-flight is opt-in; a non-EMU timelapse still runs."""
    from microclaw.tools import run_timelapse
    result = run_timelapse(
        headless_mm, unconstrained_guard, n_frames=1, interval_s=0,
        save_dir=str(tmp_path), name="preflight_off",
    )
    assert result["status"] == "Timelapse complete."


def test_java_error_is_translated_not_forwarded(headless_mm, unconstrained_guard):
    """§7: a raw 16-line JVM stack trace must never reach the model."""
    from microclaw.tools import execute_tool
    result = json.loads(
        execute_tool("get_device_property", {"device": "NoSuchDevice", "property": "X"},
                     headless_mm, unconstrained_guard)
    )
    assert "error" in result
    assert result["error"].count("\n") == 0

# ---------------------------------------------------------------------------
# design/24: the survey runner against the real engine
# ---------------------------------------------------------------------------

def test_survey_runner_images_a_mid_scan_detection(headless_mm, unconstrained_guard, tmp_path):
    """design/24 end-to-end through the SHIPPED runner (the spike's A_9 drives
    its own replica generator; this drives tools._acquire_survey_with_detector):
    the generator holds the real event source open, a follow-up with a label
    AcqEngJ has never seen is accepted mid-acquisition and lands in the same
    NDTiff dataset, event_queue.put() stays a no-op on the real queue, the
    watchdog stays quiet, and __exit__ returns.

    The inline hook is a minimal design/24 detector wired the way hook_docs
    demands: on tile_1 it enqueues a follow-up at a NEW position label
    ("roi_0") on the injected candidates queue — guarded first, and put()
    BEFORE image_done() (spike A_8) — and also drops an event on
    pycro-manager's own event_queue under a "poison" label, which must NOT
    reach the dataset (spike A_5, confirmed on the real queue by A_9).
    Non-survey labels are returned unanalyzed (spike A_7).
    """
    import queue as _queue

    from ndstorage import Dataset

    from microclaw.hooks import HookBase
    from microclaw.tools import SurveyProgress, _acquire_survey_with_detector

    positions = [{"name": f"tile_{i}", "x_um": 50.0 * i, "y_um": 0.0} for i in range(4)]
    survey_labels = {p["name"] for p in positions}
    candidates: _queue.Queue = _queue.Queue()
    progress = SurveyProgress(len(positions))

    class OneHitDetector(HookBase):
        fired = False

        def image_process_fn(self, image, metadata, event_queue):
            label = (metadata.get("Axes") or {}).get("position")
            if label not in survey_labels:
                return image, metadata          # never re-analyze a follow-up (A_7)
            if label == "tile_1" and not self.fired:
                self.fired = True
                followup = {"axes": {"position": "roi_0"}, "x": 75.0, "y": 25.0}
                unconstrained_guard.check_xy(followup["x"], followup["y"])
                candidates.put(followup)        # BEFORE image_done() (A_8)
                event_queue.put({"axes": {"position": "poison"}, "x": 0.0, "y": 0.0})
            progress.image_done()
            return image, metadata

    hook = OneHitDetector(log_path=str(tmp_path / "survey24_log.json"))

    orig = (headless_mm.core.get_x_position(), headless_mm.core.get_y_position())
    try:
        result = _acquire_survey_with_detector(
            headless_mm, unconstrained_guard, positions, str(tmp_path), "survey24",
            hook=hook, progress=progress, candidates=candidates, max_idle_s=15.0,
            num_time_points=1, time_interval_s=0,
        )
    finally:
        headless_mm.core.set_xy_position(*orig)
        headless_mm.core.wait_for_device(headless_mm.core.get_xy_stage_device())

    assert result["positions"] == 4
    dataset = Dataset(result["dataset_path"])
    try:
        seen = {c.get("position") for c in dataset.get_image_coordinates_list()}
    finally:
        dataset.close()

    assert {f"tile_{i}" for i in range(4)} <= seen, f"survey tiles missing: {seen}"
    assert "roi_0" in seen, \
        "the mid-scan detection was never imaged — the design/24 defect, live"
    assert "poison" not in seen, \
        "event_queue.put() must remain a silent no-op on the real queue (Fix 1)"
    assert hook.fired
    runner_events = {e.get("event") for e in hook._log}
    assert not ({"stalled", "aborted"} & runner_events), f"runner logged: {hook._log}"


# ---------------------------------------------------------------------------
# design/27: the adaptive runner against the real engine
# ---------------------------------------------------------------------------

def test_adaptive_survey_stops_early_with_zero_ghost_exposures(
    headless_mm, unconstrained_guard, tmp_path
):
    """design/27 Fix 4 end-to-end through the SHIPPED runner (spike B_8 drove
    its own miniature generator; this drives _acquire_survey_with_detector
    with adaptive=True): the founding scenario — stop at tile 4 of a 9-tile
    grid — resolved by NOT SUBMITTING instead of returning None. A returned
    None becomes an unlabeled ghost exposure on the very tile it meant to
    spare (the rig trace, 2026-07-15); an event that was never submitted fires
    nothing. Asserts zero ghost frames, the dataset holding exactly the
    submitted tiles, the stage never past the stop, the watchdog quiet, and
    __exit__ returning without error.
    """
    import queue as _queue

    from ndstorage import Dataset

    from microclaw.hooks import HookBase
    from microclaw.tools import SurveyProgress, _acquire_survey_with_detector

    positions = [{"name": f"tile_{i}", "x_um": 50.0 * i, "y_um": 0.0} for i in range(9)]
    stop_after = 4
    candidates: _queue.Queue = _queue.Queue()
    progress = SurveyProgress(len(positions))

    class StopAtFour(HookBase):
        frames = 0
        ghosts = 0

        def image_process_fn(self, image, metadata, event_queue):
            label = (metadata.get("Axes") or {}).get("position")
            if label is None:
                # An exposure no submitted event asked for — the design/27
                # defect. Recorded, never analyzed; the assert below fails.
                self.ghosts += 1
                return image, metadata
            self.frames += 1                       # single processor thread
            self.log(metadata, frame=self.frames)
            if self.frames >= stop_after:
                progress.done_early()              # the next tile never exists
            else:
                candidates.put(self.survey_events[self.frames])
            progress.image_done()                  # decide/submit, THEN mark
            return image, metadata

    hook = StopAtFour(log_path=str(tmp_path / "survey27_log.json"))

    orig = (headless_mm.core.get_x_position(), headless_mm.core.get_y_position())
    try:
        result = _acquire_survey_with_detector(
            headless_mm, unconstrained_guard, positions, str(tmp_path), "survey27",
            hook=hook, progress=progress, candidates=candidates, max_idle_s=15.0,
            adaptive=True, num_time_points=1, time_interval_s=0,
        )
        x_after = headless_mm.core.get_x_position()
    finally:
        headless_mm.core.set_xy_position(*orig)
        headless_mm.core.wait_for_device(headless_mm.core.get_xy_stage_device())

    assert hook.ghosts == 0, \
        f"{hook.ghosts} ghost exposure(s) fired — the design/27 defect, through Fix 4"
    assert hook.frames == stop_after

    submitted = {f"tile_{i}" for i in range(stop_after)}
    dataset = Dataset(result["dataset_path"])
    try:
        coords = dataset.get_image_coordinates_list()
    finally:
        dataset.close()
    assert {c.get("position") for c in coords} == submitted, \
        "the dataset must hold exactly the submitted tiles, nothing else"
    assert len(coords) == stop_after

    # tile_3 is the last submitted tile; the stage must never move past it.
    assert abs(x_after - 150.0) < 1.0, \
        f"stage at x={x_after} — moved past the stop (tile_3 is at 150.0)"

    runner_events = {e.get("event") for e in hook._log}
    assert not ({"stalled", "aborted"} & runner_events), f"runner logged: {hook._log}"


def test_run_adaptive_survey_tool_reaches_the_adaptive_runner(
    headless_mm, unconstrained_guard, tmp_path, monkeypatch
):
    """The tool-surface sibling of the test above. Rig run 20260716_140329
    proved the adaptive runner unreachable from TOOL_REGISTRY: a correct
    adaptive hook raised at frame 1 under run_multiposition_acquisition (the
    batched runner), one wasted exposure and a stranded stage later. This
    drives the same stop-early scenario through execute_tool("run_adaptive_
    survey") with a hook_strategy-loaded hook that reads the injected
    attributes (self.survey_events / self.candidates / self.progress) — a
    loaded hook class has no test-local objects to close over, so the
    attribute injection is itself under test. Positions are given in
    DECREASING x (the rig run's reverse scan): order must be the caller's.
    """
    from ndstorage import Dataset

    from microclaw.hooks import HookBase, PRECODED_HOOK_REGISTRY
    from microclaw.tools import execute_tool

    stop_after = 2

    class StopAtTwo(HookBase):
        instances: list = []

        def __init__(self, log_path=None):
            super().__init__(log_path)
            self.frames = 0
            self.ghosts = 0
            StopAtTwo.instances.append(self)

        def image_process_fn(self, image, metadata, event_queue):
            if self.candidates is None or self.progress is None:
                raise RuntimeError("adaptive attributes were not injected")
            label = (metadata.get("Axes") or {}).get("position")
            if label is None:
                self.ghosts += 1          # the design/27 defect, if it fires
                return image, metadata
            self.frames += 1
            self.log(metadata, frame=self.frames)
            if self.frames >= stop_after:
                self.progress.done_early()
            else:
                self.candidates.put(self.survey_events[self.frames])
            self.progress.image_done()
            return image, metadata

    StopAtTwo.instances = []
    monkeypatch.setitem(PRECODED_HOOK_REGISTRY, "stop_at_two", StopAtTwo)

    n_tiles = 5
    positions = [{"name": f"tile_{i}", "x_um": 50.0 * (n_tiles - i), "y_um": 0.0}
                 for i in range(n_tiles)]

    orig = (headless_mm.core.get_x_position(), headless_mm.core.get_y_position())
    try:
        result = json.loads(execute_tool(
            "run_adaptive_survey",
            {"protocol": "timelapse", "save_dir": str(tmp_path),
             "hook_strategy": "stop_at_two", "positions": positions,
             "name": "survey27tool",
             "protocol_params": {"n_frames": 1, "interval_s": 0},
             "log_path": str(tmp_path / "survey27tool_log.json"),
             "max_idle_s": 15.0},
            headless_mm, unconstrained_guard,
        ))
    finally:
        headless_mm.core.set_xy_position(*orig)
        headless_mm.core.wait_for_device(headless_mm.core.get_xy_stage_device())

    assert "error" not in result, result
    hook = StopAtTwo.instances[0]
    assert hook.ghosts == 0, f"{hook.ghosts} ghost exposure(s) through the tool"
    assert hook.frames == stop_after

    # The result says what ran, not what was planned — the 20260716_140329
    # transcript reported "complete across 9 position(s)" over a 4-tile stop.
    assert result["frames_acquired"] == stop_after
    assert result["stopped_early"] is True
    assert f"{stop_after} frame(s) acquired from a {n_tiles}-tile plan" in result["status"]

    dataset = Dataset(result["dataset_path"])
    try:
        coords = dataset.get_image_coordinates_list()
    finally:
        dataset.close()
    assert {c.get("position") for c in coords} == {"tile_0", "tile_1"}, \
        "the dataset must hold exactly the first two tiles IN THE GIVEN ORDER"


# ---------------------------------------------------------------------------
# EMU semantic power, MMStudio Album, and MDA (design/30)
#
# PowerShell setup, in addition to MM_RUNNING=1:
#   $env:MM_EMU="1"
#   $env:MM_APP_DIR="C:\Program Files\Micro-Manager-2.0"
#   $env:MM_EMU_LASER_SLOT="2"
#   $env:MM_EMU_CALIBRATION_POINTS='[{"percent":1,"raw_value":0},{"percent":10,"raw_value":3}]'
# Album reads and one Album snap use the normal MM_RUNNING integration opt-in.
# Running the GUI's current MDA additionally requires its exact phrase below.
# ---------------------------------------------------------------------------

def _load_design30_real_emu(headless_mm, unconstrained_guard):
    from microclaw import tools
    app_dir = os.environ.get("MM_APP_DIR")
    if not app_dir:
        pytest.skip("MM_APP_DIR must name the rig's Micro-Manager installation")
    tools._EMU_SESSION_CACHE.clear()
    result = tools.get_emu_configuration(
        headless_mm, unconstrained_guard, mm_app_dir=app_dir
    )
    assert "error" not in result, result
    return result


def _design30_calibration_points():
    raw = os.environ.get("MM_EMU_CALIBRATION_POINTS")
    if not raw:
        pytest.skip("MM_EMU_CALIBRATION_POINTS must contain two known GUI/raw observations")
    points = json.loads(raw)
    assert isinstance(points, list) and len(points) >= 2
    return points


@EMU_RIG
def test_real_emu_percentage_readback_matches_verified_affine(
    headless_mm, unconstrained_guard
):
    from microclaw import tools
    _load_design30_real_emu(headless_mm, unconstrained_guard)
    slot = int(os.environ.get("MM_EMU_LASER_SLOT", "2"))
    verified = tools.verify_emu_laser_power_calibration(
        headless_mm, unconstrained_guard, slot, _design30_calibration_points()
    )
    assert verified["verified"] is True, verified
    state = tools.get_emu_laser_power_percentage(headless_mm, unconstrained_guard, slot)
    assert state["interpreted_state"]["calibration_verified"] is True
    assert state["commanded_state"]["raw_value"] is not None
    assert isinstance(state["interpreted_state"]["effective_percent"], float)
    assert state["measured_state"] is None
    assert state["gui_state"] is None


@EMU_RIG
@pytest.mark.skipif(
    os.environ.get("MM_ALLOW_EMU_POWER_WRITE") != "WRITE DISABLED LASER SETPOINT",
    reason="set MM_ALLOW_EMU_POWER_WRITE='WRITE DISABLED LASER SETPOINT' to opt in",
)
def test_real_emu_percentage_write_roundtrips_while_laser_is_disabled(
    headless_mm, unconstrained_guard
):
    from microclaw import tools
    emu = _load_design30_real_emu(headless_mm, unconstrained_guard)
    slot = int(os.environ.get("MM_EMU_LASER_SLOT", "2"))
    percent = float(os.environ.get("MM_EMU_WRITE_PERCENT", "1"))
    laser = emu["lasers"][slot]
    enable = laser.get("enable")
    if not enable or "device" not in enable:
        pytest.skip(f"slot {slot} has no readable enable property")
    enable_raw = str(headless_mm.core.get_property(enable["device"], enable["property"]))
    assert enable_raw == str(enable.get("off", "0")), (
        "Refusing setpoint integration test while illumination is enabled"
    )
    verified = tools.verify_emu_laser_power_calibration(
        headless_mm, unconstrained_guard, slot, _design30_calibration_points()
    )
    assert verified["verified"] is True, verified
    power = laser["power_pct"]
    original = str(headless_mm.core.get_property(power["device"], power["property"]))
    try:
        result = tools.set_emu_laser_power_percentage(
            headless_mm, unconstrained_guard, slot, percent
        )
        assert "error" not in result, result
        assert result["raw_value_written"] == str(
            headless_mm.core.get_property(power["device"], power["property"])
        )
    finally:
        headless_mm.core.set_property(power["device"], power["property"], original)
        headless_mm.core.wait_for_device(power["device"])


def test_real_album_state_is_reachable(headless_mm, unconstrained_guard):
    from microclaw import tools
    result = tools.get_album_state(headless_mm, unconstrained_guard)
    assert isinstance(result["album_exists"], bool)
    if result["album_exists"]:
        assert result["datastore"]["image_count"] >= 0


def test_real_snap_appears_in_mmstudio_album(headless_mm, unconstrained_guard):
    from microclaw import tools
    before = tools.get_album_state(headless_mm, unconstrained_guard)
    before_count = before["datastore"]["image_count"] if before["album_exists"] else 0
    result = tools.snap_to_album(headless_mm, unconstrained_guard)
    assert result["album_exists"] is True
    assert result["datastore"]["image_count"] > before_count


def test_real_mda_preview_reads_gui_state(headless_mm, unconstrained_guard):
    from microclaw import tools
    result = tools.get_mda_settings(headless_mm, unconstrained_guard)
    assert result["source"] == "MMStudio GUI current MDA"
    assert len(result["preview_token"]) == 64
    for key in ("save", "use_frames", "use_position_list", "use_slices",
                "use_channels", "use_autofocus"):
        assert isinstance(result["settings"][key], bool), result


@pytest.mark.skipif(
    os.environ.get("MM_ALLOW_SAFE_MDA") != "RUN ONE SAFE IMAGE",
    reason="set MM_ALLOW_SAFE_MDA='RUN ONE SAFE IMAGE' after configuring safe GUI MDA",
)
def test_real_mda_runs_exactly_one_deliberately_safe_image(
    headless_mm, unconstrained_guard, monkeypatch
):
    from microclaw import tools
    preview = tools.get_mda_settings(headless_mm, unconstrained_guard)
    settings = preview["settings"]
    unsafe = {
        key: settings[key]
        for key in ("save", "use_frames", "use_position_list", "use_slices",
                    "use_channels", "use_autofocus")
        if settings[key] is not False
    }
    assert not unsafe, f"Refusing unsafe current GUI MDA settings: {unsafe}"
    monkeypatch.setattr(tools, "CONFIRM_FN", lambda summary, kind="action": True)
    result = tools.run_mda(headless_mm, unconstrained_guard, preview["preview_token"])
    assert "error" not in result, result
    assert result["datastore"]["image_count"] == 1
    assert result["datastore"]["frozen"] is True
