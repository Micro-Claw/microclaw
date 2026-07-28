import hashlib
import json
import math
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from microclaw.calibration import StageCameraAffine, affine_payload_hash, canonical_affine_payload
from microclaw.dataset_mosaic import MosaicGeometry, assemble_stage_coordinate_mosaic


def affine(a=1, b=0, c=0, d=1):
    return StageCameraAffine(a, b, c, d, "obj", 1, math.sqrt(abs(a * d - b * c)))


def assemble(frames, transform=None, sample=1):
    return assemble_stage_coordinate_mosaic(frames, MosaicGeometry(transform or affine(), sample))


@pytest.mark.parametrize("transform", [
    affine(), affine(0, -1, 1, 0),
    affine(math.cos(.37), -math.sin(.37), math.sin(.37), math.cos(.37)),
    affine(-1, 0, 0, 1), affine(1, 0, 0, -1),
    affine(2, 0, 0, .5), affine(1, .3, .2, 1),
])
@pytest.mark.parametrize("shape", [(3, 3), (4, 4), (3, 6)])
def test_affine_families_have_exact_inverse_sampled_geometry(transform, shape):
    image = np.arange(np.prod(shape), dtype=np.uint16).reshape(shape) + 1
    result = assemble([(image, 10, 20)], transform)

    # Independent scalar reference: transform the four source-centre corners for
    # bounds, then invert the 2x2 coefficients directly for every output cell.
    height, width = shape
    corners = []
    for dy in (-(height - 1) / 2, (height - 1) / 2):
        for dx in (-(width - 1) / 2, (width - 1) / 2):
            corners.append((10 + transform.a * dx + transform.b * dy,
                            20 + transform.c * dx + transform.d * dy))
    origin_x = min(point[0] for point in corners)
    origin_y = min(point[1] for point in corners)
    max_x = max(point[0] for point in corners)
    max_y = max(point[1] for point in corners)
    expected = np.zeros((math.ceil(max_y - origin_y - 1e-12) + 1,
                         math.ceil(max_x - origin_x - 1e-12) + 1), dtype=image.dtype)
    expected_coverage = np.zeros(expected.shape, dtype=bool)
    determinant = transform.a * transform.d - transform.b * transform.c
    last_output_col = math.floor((max_x - origin_x) + .5)
    last_output_row = math.floor((max_y - origin_y) + .5)
    for out_row in range(expected.shape[0]):
        for out_col in range(expected.shape[1]):
            if out_col > last_output_col or out_row > last_output_row:
                continue
            sx = origin_x + out_col - 10
            sy = origin_y + out_row - 20
            source_col = math.floor((transform.d * sx - transform.b * sy) / determinant
                                    + (width - 1) / 2 + .5)
            source_row = math.floor((-transform.c * sx + transform.a * sy) / determinant
                                    + (height - 1) / 2 + .5)
            if 0 <= source_row < height and 0 <= source_col < width:
                expected[out_row, out_col] = image[source_row, source_col]
                expected_coverage[out_row, out_col] = True

    np.testing.assert_array_equal(result["mosaic"], expected)
    np.testing.assert_array_equal(result["coverage_mask"], expected_coverage)
    np.testing.assert_allclose(result["origin_um"], [origin_x, origin_y])
    np.testing.assert_allclose(result["extent_um"], [max_x - origin_x, max_y - origin_y])
    assert result["overlap_statistics"]["rasterized_output_sample_count"] == np.count_nonzero(expected_coverage)
    assert result["overlap_statistics"]["overlap_pixels"] == 0
    assert result["overlap_statistics"]["maximum_coverage"] == 1

    # A convex transformed tile may leave legitimate empty bounding-box corners,
    # but inverse sampling must not leave holes inside any covered row or column.
    for row in expected_coverage:
        occupied = np.flatnonzero(row)
        if occupied.size:
            assert np.all(row[occupied[0]:occupied[-1] + 1])
    for col in expected_coverage.T:
        occupied = np.flatnonzero(col)
        if occupied.size:
            assert np.all(col[occupied[0]:occupied[-1] + 1])


def test_overlap_later_frame_wins_and_gap_is_zero():
    first = np.ones((3, 3), np.uint16)
    second = np.full((3, 3), 7, np.uint16)
    overlap = assemble([(first, 0, 0), (second, 0, 0)])
    assert np.all(overlap["mosaic"] == 7)
    assert overlap["overlap_statistics"]["overlap_pixels"] == 9
    gap = assemble([(first, 0, 0), (second, 5, 0)])
    assert np.all(gap["mosaic"][:, 3:5] == 0)
    assert gap["overlap_statistics"]["uncovered_pixels"] == 6


def test_single_tile_coarse_sampling_is_not_overlap():
    result = assemble([(np.ones((64, 64), np.uint16), 0, 0)], sample=4)
    statistics = result["overlap_statistics"]
    assert statistics["overlap_pixels"] == 0
    assert statistics["maximum_coverage"] == 1
    assert statistics["rasterized_output_sample_count"] == statistics["covered_pixels"]


def test_non_grid_coordinates_control_placement_not_iteration_index():
    tiles = [(np.full((1, 1), value, np.uint16), x, y) for value, x, y in (
        (1, 0, 0), (2, 2, 0), (3, 2, 2), (4, -1, 2), (5, -1, -1)
    )]
    result = assemble(tiles)
    origin_x, origin_y = result["origin_um"]
    for image, x, y in tiles:
        assert result["mosaic"][round(y - origin_y), round(x - origin_x)] == image[0, 0]


@pytest.mark.parametrize("transform,sample", [
    (affine(float("nan"), 0, 0, 1), 1),
    (affine(1, 2, 2, 4), 1),
    (affine(), 0), (affine(), float("inf")),
])
def test_invalid_geometry_is_rejected_on_direct_construction(transform, sample):
    with pytest.raises(ValueError):
        MosaicGeometry(transform, sample)


class FakeDataset:
    images = {}
    metadata = {}
    axes = {"position": ["p0", "p1"], "time": [0, 1]}

    def __init__(self, path):
        self.path = path

    def has_image(self, **coords):
        return (coords["position"], coords["time"]) in self.images

    def read_image(self, **coords):
        return self.images[(coords["position"], coords["time"])]

    def read_metadata(self, **coords):
        return self.metadata[(coords["position"], coords["time"])]


def metadata(x, y, *, camera="Andor", model="model", roi="0-0-3-3"):
    return {"XPosition_um_Intended": x, "YPosition_um_Intended": y,
            "PixelSizeAffine": "0;0;0;0;0;0", "Core-Camera": camera,
            f"{camera}-Camera": model, "ROI": roi, "Binning": "1x1",
            "Height": 3, "Width": 3, "PixelType": "GRAY16"}


def artifact(path, *, camera="Andor", model="model", roi=(0, 0, 3, 3)):
    transform = affine()
    identity = {"payload": canonical_affine_payload(transform),
                "payload_sha256": affine_payload_hash(transform),
                "camera_device": camera, "camera_model": model, "roi": list(roi)}
    path.write_text(json.dumps(identity), encoding="utf-8")
    return {"kind": "artifact", "path": str(path)}


def run_tool(monkeypatch, tmp_path, selection, ref=None):
    from microclaw import tools
    monkeypatch.setattr(tools, "Dataset", FakeDataset)
    guard = MagicMock()
    guard.resolve_readable_path.side_effect = lambda p: p
    guard.resolve_in_workspace.side_effect = lambda p: p
    return tools.build_stage_coordinate_mosaic(
        None, guard, "dataset", str(tmp_path / "out.tif"), selection,
        ref or artifact(tmp_path / "cal.json"),
    )


def test_plane_isolation_manifest_and_deterministic_replay(monkeypatch, tmp_path):
    FakeDataset.images = {
        ("p0", 0): np.full((3, 3), 10, np.uint16),
        ("p1", 0): np.full((3, 3), 20, np.uint16),
        ("p0", 1): np.full((3, 3), 999, np.uint16),
    }
    FakeDataset.metadata = {key: metadata(index * 3, 0)
                            for index, key in enumerate(FakeDataset.images)}
    ref = artifact(tmp_path / "cal.json")
    first = run_tool(monkeypatch, tmp_path, {"time": 0}, ref)
    assert 999 not in __import__("tifffile").imread(tmp_path / "out.tif")
    manifest_document = json.loads(Path(first["manifest_path"]).read_text())
    manifest = manifest_document["manifest_payload"]
    canonical_payload = json.dumps(
        manifest, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    assert manifest_document["manifest_payload_sha256"] == hashlib.sha256(canonical_payload).hexdigest()
    assert first["manifest_payload_sha256"] == manifest_document["manifest_payload_sha256"]
    assert manifest["calibration_identity"]["payload"] == canonical_affine_payload(affine())
    assert manifest["calibration_identity"]["source_kind"] == "artifact"
    second = run_tool(monkeypatch, tmp_path, {"time": 0}, ref)
    assert first["pixel_sha256"] == second["pixel_sha256"]
    assert first["manifest_payload_sha256"] == second["manifest_payload_sha256"]
    # The result manifest itself is a complete calibration artifact; replay does
    # not consult a mutable current alias or require the original artifact.
    Path(ref["path"]).unlink()
    replay = run_tool(
        monkeypatch, tmp_path, {"time": 0},
        {"kind": "artifact", "path": first["manifest_path"]},
    )
    assert replay["pixel_sha256"] == first["pixel_sha256"]


def test_axis_selection_xy_and_identity_fail_loud_without_output(monkeypatch, tmp_path):
    FakeDataset.images = {("p0", 0): np.ones((3, 3), np.uint16)}
    FakeDataset.metadata = {("p0", 0): metadata(0, 0)}
    with pytest.raises(ValueError, match="fix every"):
        run_tool(monkeypatch, tmp_path, {})
    with pytest.raises(ValueError, match="not present"):
        run_tool(monkeypatch, tmp_path, {"time": 9})
    FakeDataset.metadata[("p0", 0)].pop("XPosition_um_Intended")
    with pytest.raises(ValueError, match="XPosition_um_Intended"):
        run_tool(monkeypatch, tmp_path, {"time": 0})
    assert not (tmp_path / "out.tif").exists()
    FakeDataset.metadata[("p0", 0)] = metadata(0, 0)
    wrong = artifact(tmp_path / "wrong.json", camera="Hamamatsu")
    with pytest.raises(ValueError, match="contradicts"):
        run_tool(monkeypatch, tmp_path, {"time": 0}, wrong)
    assert not (tmp_path / "out.tif").exists()


def test_tool_is_off_acquisition_ledger():
    from microclaw.tools import build_stage_coordinate_mosaic
    assert not getattr(build_stage_coordinate_mosaic, "_microclaw_acquisition_entry_point", False)


def test_tool_uses_shared_present_coordinate_traversal(monkeypatch, tmp_path):
    from microclaw import tools
    FakeDataset.images = {("p0", 0): np.ones((3, 3), np.uint16)}
    FakeDataset.metadata = {("p0", 0): metadata(0, 0)}
    original = tools._iter_present_coords
    calls = []

    def observed(dataset, fixed_axes):
        calls.append(dict(fixed_axes))
        yield from original(dataset, fixed_axes)

    monkeypatch.setattr(tools, "_iter_present_coords", observed)
    run_tool(monkeypatch, tmp_path, {"time": 0})
    # Once in the tool and once in resolve_calibration's acquisition audit.
    assert calls == [{"time": 0}, {"time": 0}]
