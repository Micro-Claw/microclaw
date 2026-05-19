"""
Integration tests against a real Micro-Manager Demo configuration.
Requires --mm-path and --demo-config pytest options.
Skip by default; run with: pytest -m integration --mm-path /path/to/MM --demo-config /path/to/MMConfig_demo.cfg
"""
import pytest

pytestmark = pytest.mark.integration


def test_snap_image_demo(headless_mm, unconstrained_guard):
    from microclaw.tools import snap_image
    result = snap_image(headless_mm, unconstrained_guard)
    assert "status" in result


def test_move_and_return_z(headless_mm, unconstrained_guard):
    from microclaw.tools import get_z_position, move_stage_z
    original = get_z_position(headless_mm, unconstrained_guard)["z_um"]
    move_stage_z(headless_mm, unconstrained_guard, z_um=original + 5.0)
    new_z = get_z_position(headless_mm, unconstrained_guard)["z_um"]
    assert abs(new_z - (original + 5.0)) < 0.1
    move_stage_z(headless_mm, unconstrained_guard, z_um=original)  # restore


def test_zstack_creates_dataset(headless_mm, unconstrained_guard, tmp_path):
    from microclaw.tools import run_zstack
    result = run_zstack(
        headless_mm, unconstrained_guard,
        z_start_um=45.0, z_end_um=55.0, z_step_um=2.0,
        save_dir=str(tmp_path), name="test_stack",
    )
    assert result["status"] == "Z-stack complete."
    assert (tmp_path / "test_stack").exists()


def test_zstack_export_tiff(headless_mm, unconstrained_guard, tmp_path):
    from microclaw.tools import run_zstack, export_dataset_as_tiff
    run_zstack(headless_mm, unconstrained_guard,
               z_start_um=45.0, z_end_um=50.0, z_step_um=1.0,
               save_dir=str(tmp_path), name="stack")
    out = str(tmp_path / "out.tiff")
    result = export_dataset_as_tiff(
        headless_mm, unconstrained_guard,
        dataset_path=str(tmp_path / "stack"),
        output_path=out,
    )
    assert result["status"] == "Export complete."
    import tifffile
    img = tifffile.imread(out)
    assert img.ndim >= 2


def test_snap_and_analyze_demo(headless_mm, unconstrained_guard):
    import base64
    import json
    from microclaw.tools import snap_and_analyze
    result = snap_and_analyze(headless_mm, unconstrained_guard)
    assert isinstance(result, list)
    payload = json.loads(result[0]["text"])
    assert "focus_metric" in payload
    assert base64.standard_b64decode(result[1]["source"]["data"])[:4] == b"\x89PNG"


def test_autofocus_demo(headless_mm, unconstrained_guard):
    import json
    from microclaw.tools import run_autofocus
    result = run_autofocus(headless_mm, unconstrained_guard, z_range_um=10, z_step_um=1)
    payload = json.loads(result[0]["text"]) if isinstance(result, list) else result
    assert "best_z_um" in payload
    assert isinstance(payload["metric_curve"], list)


def test_position_list_roundtrip_demo(headless_mm, unconstrained_guard):
    from microclaw.tools import mark_position, get_position_list, delete_position
    headless_mm.clear_positions()
    mark_position(headless_mm, unconstrained_guard, name="test_pos")
    positions = headless_mm.get_positions()
    assert any(p["name"] == "test_pos" for p in positions)
    delete_position(headless_mm, unconstrained_guard, name="test_pos")


def test_autofocus_hook_demo(headless_mm, unconstrained_guard, tmp_path):
    import json
    from pathlib import Path
    from microclaw.tools import run_adaptive_acquisition
    log_path = str(tmp_path / "af_log.json")
    result = run_adaptive_acquisition(
        headless_mm, unconstrained_guard,
        z_start_um=45, z_end_um=55, z_step_um=2,
        save_dir=str(tmp_path), name="af_hook_test",
        hook_strategy="autofocus_per_position",
        hook_params={"z_range_um": 5, "z_step_um": 0.5},
        log_path=log_path,
    )
    assert "complete" in result["status"]
    log = json.loads(Path(log_path).read_text())
    assert len(log) > 0
