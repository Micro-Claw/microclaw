"""Unit tests for the stage↔camera affine (design/14 §8)."""
import pytest

from microclaw.calibration import (
    StageCameraAffine,
    affine_key,
    load_affine,
    save_affine,
    solve_affine,
)


class TestSolveAffine:
    def test_pure_scale(self):
        # +20 µm along stage X shifts the image +40 px in columns (0.5 µm/px).
        aff = solve_affine((0.0, 40.0), (40.0, 0.0), 20.0, "obj", 1)
        assert aff.pixel_size_um == pytest.approx(0.5)
        dx, dy = aff.px_to_um(40.0, 0.0)
        assert (dx, dy) == (pytest.approx(20.0), pytest.approx(0.0))
        dx, dy = aff.px_to_um(0.0, 40.0)
        assert (dx, dy) == (pytest.approx(0.0), pytest.approx(20.0))

    def test_axis_flip_captured(self):
        # Stage +X moves the image LEFT: the affine must encode the sign, the
        # exact failure the amr_test model tried to infer from thumbnails.
        aff = solve_affine((0.0, -40.0), (40.0, 0.0), 20.0, "obj", 1)
        dx, dy = aff.px_to_um(-40.0, 0.0)
        assert (dx, dy) == (pytest.approx(20.0), pytest.approx(0.0))
        assert aff.pixel_size_um == pytest.approx(0.5)

    def test_90_degree_camera_rotation(self):
        # Stage X shows up in image rows, stage Y in image columns.
        aff = solve_affine((40.0, 0.0), (0.0, 40.0), 20.0, "obj", 1)
        dx, dy = aff.px_to_um(0.0, 40.0)   # pure row displacement
        assert (dx, dy) == (pytest.approx(20.0), pytest.approx(0.0))

    def test_degenerate_shifts_raise(self):
        with pytest.raises(ValueError, match="degenerate"):
            solve_affine((0.0, 0.0), (0.0, 0.0), 20.0, "obj", 1)


class TestPersistence:
    @pytest.fixture(autouse=True)
    def _tmp_knowledge(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "microclaw.knowledge_manager.KNOWLEDGE_PATH", tmp_path / "knowledge.yaml"
        )

    def test_save_load_roundtrip(self):
        aff = solve_affine((0.0, 40.0), (40.0, 0.0), 20.0, "20x Air", 2)
        key = save_affine(aff)
        assert key == affine_key("20x Air", 2)
        assert load_affine("20x Air", 2) == aff

    def test_keyed_by_objective_and_binning(self):
        save_affine(solve_affine((0.0, 40.0), (40.0, 0.0), 20.0, "20x", 1))
        assert load_affine("20x", 2) is None
        assert load_affine("60x", 1) is None

    def test_load_missing_returns_none(self):
        assert load_affine("nothing", 1) is None

    def test_affine_key_slugifies(self):
        assert affine_key("20x Air / NA 0.75", 1) == "stage_camera_affine_20x_Air_NA_0_75_bin1"
