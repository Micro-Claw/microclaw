"""Unit tests for the stage↔camera affine (design/14 §8)."""
import json

import pytest

from microclaw.calibration import (
    CalibrationResolutionError,
    StageCameraAffine,
    affine_payload_hash,
    affine_key,
    affine_version_key,
    canonical_affine_payload,
    load_affine,
    load_affine_version,
    parse_mm_pixel_size_affine,
    resolve_calibration,
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

    def test_recalibration_does_not_change_old_version(self):
        first = StageCameraAffine(0.2, 0, 0, 0.2, "20x", 1, 0.2)
        second = StageCameraAffine(0, -0.3, 0.3, 0, "20x", 1, 0.3)
        save_affine(first, camera_device="Cam", camera_model="Model", roi=[0, 0, 8, 8])
        first_key = affine_version_key(first)
        save_affine(second, camera_device="Cam", camera_model="Model", roi=[0, 0, 8, 8])
        assert load_affine_version(first_key)[0] == first
        assert load_affine("20x", 1) == second

    def test_tampered_immutable_payload_is_refused(self):
        from microclaw import knowledge_manager

        affine = StageCameraAffine(0.2, 0, 0, 0.2, "20x", 1, 0.2)
        save_affine(affine, camera_device="Cam", camera_model="Model", roi=[0, 0, 8, 8])
        key = affine_version_key(affine)
        data = knowledge_manager.load_knowledge()
        data["devices"][key]["payload"]["a"] = 9
        knowledge_manager.KNOWLEDGE_PATH.write_text(
            __import__("yaml").dump(data), encoding="utf-8"
        )
        with pytest.raises(CalibrationResolutionError, match="hash"):
            load_affine_version(key)

    def test_legacy_alias_is_versioned_before_use(self):
        from microclaw.knowledge_manager import load_knowledge, save_entry

        affine = StageCameraAffine(0.2, 0, 0, 0.2, "20x", 1, 0.2)
        save_entry("devices", affine_key("20x", 1), canonical_affine_payload(affine))
        assert load_affine("20x", 1) == affine
        alias = load_knowledge()["devices"][affine_key("20x", 1)]
        assert alias["current_version"] == affine_version_key(affine)


class FakeDataset:
    def __init__(self, records, summary_metadata=None):
        self.records = records
        self.axes = {"position": [record[0] for record in records]}
        self.summary_metadata = summary_metadata or {}

    def has_image(self, **coords):
        return coords["position"] in dict(self.records)

    def read_image(self, **coords):
        return None

    def read_metadata(self, **coords):
        return dict(self.records)[coords["position"]]


def _real_metadata(
    raw="0.0;0.0;0.0;0.0;0.0;0.0", roi="36-50-453-227", *,
    objective=None,
):
    """Calibration-relevant keys copied verbatim from Run A per-image metadata."""
    metadata = {
        "PixelSizeAffine": raw,
        "PixelSizeUm": 0,
        "Binning": "1",
        "Core-Camera": "Andor",
        "Andor-Camera": "| iXon Ultra | DU897_BV | 8172 |",
        "ROI": roi,
        "Height": 227,
        "Width": 453,
    }
    if objective is not None:
        metadata["PixelSizeConfig"] = objective
    return metadata


class TestCalibrationResolver:
    def test_mm_summary_affine_transform_is_accepted_without_objective(self):
        dataset = FakeDataset(
            [("p", _real_metadata(None))],
            summary_metadata={"AffineTransform": "0.01_-0.1_-0.1_-0.01"},
        )
        affine, identity = resolve_calibration(dataset, None)
        assert (affine.a, affine.b, affine.c, affine.d) == (0.01, -0.1, -0.1, -0.01)
        assert affine.objective == ""
        assert identity["source_reference"]["metadata_key"] == "AffineTransform"
        assert identity["objective_unrecorded"] is True
        assert "verify the optical path" in identity["objective_unrecorded_reason"]

    def test_mm_row_major_and_sentinels(self):
        affine = parse_mm_pixel_size_affine(
            "1;2;3;4;5;6", objective="obj", binning=1
        )
        assert (affine.a, affine.b, affine.c, affine.d) == (1, 2, 4, 5)
        sentinel_pairs = (
            ("0;0;0;0;0;0", "0_0_0_0"),
            ("1;0;0;0;1;0", "1_0_0_1"),
            ("nan;0;0;0;1;0", "nan_0_0_1"),
            ("1;2;0;2;4;0", "1_2_2_4"),
        )
        for six_value, four_value in sentinel_pairs:
            assert parse_mm_pixel_size_affine(
                six_value, objective="obj", binning=1
            ) is None
            assert parse_mm_pixel_size_affine(
                four_value, objective="obj", binning=1
            ) is None
        assert parse_mm_pixel_size_affine(
            "Undefined", objective="obj", binning=1
        ) is None

    def test_captured_mm_affine_forms_decode_to_identical_asymmetric_matrix(self):
        # Copied verbatim from plus_mosaic_2. MMCore's six-value form is
        # row-major; java.awt.geom.AffineTransform.getMatrix's four-value form
        # is column-major. b != c makes a mistaken transpose observable.
        per_frame = (
            "0.004927971153294251;-0.10452770834076626;0.0;"
            "-0.10638852331845677;-0.00512650316309355;0.0"
        )
        summary = (
            "0.004927971153294251_-0.10638852331845677_"
            "-0.10452770834076626_-0.00512650316309355"
        )
        parsed_per_frame = parse_mm_pixel_size_affine(
            per_frame, objective="", binning=1
        )
        parsed_summary = parse_mm_pixel_size_affine(summary, objective="", binning=1)
        expected = (
            0.004927971153294251, -0.10452770834076626,
            -0.10638852331845677, -0.00512650316309355,
        )
        assert (parsed_per_frame.a, parsed_per_frame.b,
                parsed_per_frame.c, parsed_per_frame.d) == expected
        assert (parsed_summary.a, parsed_summary.b,
                parsed_summary.c, parsed_summary.d) == expected

    def test_semicolon_delimiter_always_selects_six_value_mmcore_order(self):
        affine = parse_mm_pixel_size_affine(
            "2;3;99;5;7;101", objective="obj", binning=1
        )
        assert (affine.a, affine.b, affine.c, affine.d) == (2, 3, 5, 7)

    def test_per_frame_affine_wins_over_stale_summary(self):
        dataset = FakeDataset(
            [("p", _real_metadata("0;-0.1056;0;-0.1056;0;0", objective="20x"))],
            summary_metadata={"AffineTransform": "1_0_0_1"},
        )
        affine, identity = resolve_calibration(dataset, None)
        assert affine.pixel_size_um == pytest.approx(0.1056)
        assert identity["source_reference"]["metadata_key"] == "PixelSizeAffine"

    def test_summary_cannot_mask_inconsistent_per_frame_affines(self):
        dataset = FakeDataset([
            ("p0", _real_metadata("0;-0.2;0;0.2;0;0", objective="20x")),
            ("p1", _real_metadata("0;-0.3;0;0.3;0;0", objective="20x")),
        ], summary_metadata={"AffineTransform": "0_-0.1_0.1_0"})
        with pytest.raises(CalibrationResolutionError, match="changes between frames"):
            resolve_calibration(dataset, None)

    def test_literal_run_a_metadata_reports_recorded_sentinel(self):
        dataset = FakeDataset([("run_a_r0_c0", _real_metadata())])
        _, identity = resolve_calibration(dataset, {
            "kind": "legacy_derived", "pixel_size_um": 0.5,
            "objective": "20x", "binning": 1, "camera_device": "Cam",
            "camera_model": "Model", "roi": [0, 0, 64, 64],
        })
        assert identity["source_kind"] == "legacy_derived"
        assert identity["acquisition_fallthrough_reason"] == (
            "PixelSizeAffine is absent, a sentinel, or invalid"
        )

    def test_dash_roi_and_real_camera_model_produce_acquisition_identity(self):
        dataset = FakeDataset([(
            "p7", _real_metadata("0;-0.2;0;0.2;0;0", objective="20x")
        )])
        affine, identity = resolve_calibration(dataset, None)
        assert (affine.a, affine.b, affine.c, affine.d) == (0, -0.2, 0.2, 0)
        assert identity["source_kind"] == "acquisition_recorded"
        assert identity["roi"] == [36, 50, 453, 227]
        assert identity["camera_model"] == "| iXon Ultra | DU897_BV | 8172 |"

    def test_explicit_reference_takes_precedence_over_complete_acquisition(self):
        dataset = FakeDataset([(
            "p7", _real_metadata("0;-0.2;0;0.2;0;0", objective="20x")
        )])
        affine, identity = resolve_calibration(dataset, {
            "kind": "legacy_derived", "pixel_size_um": 0.5, "rot90_k": 0,
            "objective": "known-override", "binning": 1,
            "camera_device": "OtherCam", "camera_model": "OtherModel",
            "roi": [0, 0, 64, 64],
        })
        assert affine.objective == "known-override"
        assert identity["source_kind"] == "legacy_derived"
        assert identity["acquisition_recorded_not_used_reason"] == (
            "explicit calibration_ref supplied"
        )

    def test_mid_dataset_roi_change_is_refused(self):
        dataset = FakeDataset([
            ("p0", _real_metadata(roi="36-50-453-227", objective="20x")),
            ("p9", _real_metadata(roi="37-50-453-227", objective="20x")),
        ])
        with pytest.raises(CalibrationResolutionError, match="ROI changes"):
            resolve_calibration(dataset, None)

    def test_unknown_roi_is_incomplete_and_mixed_unknown_roi_is_inconsistent(self):
        missing = _real_metadata("0;-0.2;0;0.2;0;0", objective="20x")
        del missing["ROI"]
        dataset = FakeDataset([("p0", missing)])
        _, identity = resolve_calibration(dataset, {
            "kind": "legacy_derived", "pixel_size_um": 0.5,
            "objective": "20x", "binning": 1, "camera_device": "Cam",
            "camera_model": "Model", "roi": [0, 0, 64, 64],
        })
        assert "missing roi" in identity["acquisition_fallthrough_reason"]
        with pytest.raises(CalibrationResolutionError, match="does not record"):
            resolve_calibration(dataset, None)

        mixed = FakeDataset([
            ("p0", _real_metadata("0;-0.2;0;0.2;0;0", objective="20x")),
            ("p1", missing),
        ])
        with pytest.raises(CalibrationResolutionError, match="becomes unknown"):
            resolve_calibration(mixed, {
                "kind": "legacy_derived", "pixel_size_um": 0.5,
                "objective": "20x", "binning": 1, "camera_device": "Cam",
                "camera_model": "Model", "roi": [0, 0, 64, 64],
            })

    def test_malformed_roi_is_refused_loudly(self):
        dataset = FakeDataset([("p0", _real_metadata(roi="36-50-453"))])
        with pytest.raises(CalibrationResolutionError, match="Invalid acquisition ROI"):
            resolve_calibration(dataset, None)

    def test_all_four_explicit_source_kinds_and_artifact_replay_without_kb(
        self, tmp_path
    ):
        empty = FakeDataset([("p", _real_metadata("Undefined"))])
        affine = StageCameraAffine(0.2, 0, 0, 0.2, "20x", 1, 0.2)
        save_affine(affine, camera_device="Cam", camera_model="Model", roi=[0, 0, 8, 8])
        version = affine_version_key(affine)
        _, version_identity = resolve_calibration(
            empty, {"kind": "knowledge_version", "key": version}
        )
        _, current_identity = resolve_calibration(
            empty, {"kind": "confirmed_current", "objective": "20x", "binning": 1}
        )
        assert version_identity["source_kind"] == "knowledge_version"
        assert current_identity["source_kind"] == "confirmed_current"
        _, legacy_identity = resolve_calibration(empty, {
            "kind": "legacy_derived", "pixel_size_um": 0.2, "rot90_k": 1,
            "objective": "20x", "binning": 1, "camera_device": "Cam",
            "camera_model": "Model", "roi": [0, 0, 8, 8],
        })
        assert legacy_identity["source_kind"] == "legacy_derived"

        artifact = tmp_path / "manifest.json"
        artifact.write_text(json.dumps({"calibration_identity": version_identity}), encoding="utf-8")
        from microclaw import knowledge_manager
        knowledge_manager.KNOWLEDGE_PATH.unlink()
        replayed, replay_identity = resolve_calibration(
            empty, {"kind": "artifact", "path": str(artifact)}
        )
        assert replayed == affine
        assert replay_identity["source_kind"] == "artifact"
        assert replay_identity["payload_sha256"] == affine_payload_hash(affine)

    def test_missing_identity_refuses_instead_of_guessing(self):
        empty = FakeDataset([("p", _real_metadata("1;0;0;0;1;0"))])
        with pytest.raises(CalibrationResolutionError, match="does not record"):
            resolve_calibration(empty, None)

    def test_explicit_ref_overrides_recorded_affine_with_unrecorded_objective(self, tmp_path):
        dataset = FakeDataset([("p", _real_metadata("0;-0.2;0;0.2;0;0"))])
        affine = StageCameraAffine(0.2, 0, 0, 0.2, "known", 1, 0.2)
        identity = {
            "payload": canonical_affine_payload(affine),
            "payload_sha256": affine_payload_hash(affine),
            "camera_device": "Cam", "camera_model": "Model", "roi": [0, 0, 8, 8],
        }
        artifact = tmp_path / "calibration.json"
        artifact.write_text(json.dumps(identity), encoding="utf-8")
        _, resolved = resolve_calibration(
            dataset, {"kind": "artifact", "path": str(artifact)}
        )
        assert resolved["source_kind"] == "artifact"
        assert resolved["acquisition_recorded_not_used_reason"] == (
            "explicit calibration_ref supplied"
        )
        assert resolved["payload"]["objective"] == "known"
        assert resolved["payload"]["objective"] != "None"

    def test_inconsistent_acquisition_affine_refuses_even_with_explicit_ref(self):
        dataset = FakeDataset([
            ("p0", _real_metadata("0;-0.2;0;0.2;0;0", objective="20x")),
            ("p1", _real_metadata("0;-0.3;0;0.3;0;0", objective="20x")),
        ])
        with pytest.raises(CalibrationResolutionError, match="changes between frames"):
            resolve_calibration(dataset, {
                "kind": "legacy_derived", "pixel_size_um": 0.5,
                "objective": "20x", "binning": 1, "camera_device": "Cam",
                "camera_model": "Model", "roi": [0, 0, 64, 64],
            })
