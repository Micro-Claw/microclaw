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
