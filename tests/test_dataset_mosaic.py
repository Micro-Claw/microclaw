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
def test_affine_families_and_frame_dimensions(transform, shape):
    image = np.arange(np.prod(shape), dtype=np.uint16).reshape(shape) + 1
    result = assemble([(image, 10, 20)], transform)
    assert result["mosaic"].shape[0] > 0 and result["mosaic"].shape[1] > 0
    assert result["overlap_statistics"]["source_sample_count"] == image.size
    assert result["coverage_mask"].dtype == np.bool_
    assert result["origin_um"][0] <= 10 <= result["origin_um"][0] + result["extent_um"][0]


def test_overlap_later_frame_wins_and_gap_is_zero():
    first = np.ones((3, 3), np.uint16)
    second = np.full((3, 3), 7, np.uint16)
    overlap = assemble([(first, 0, 0), (second, 0, 0)])
    assert np.all(overlap["mosaic"] == 7)
    assert overlap["overlap_statistics"]["overlap_pixels"] == 9
    gap = assemble([(first, 0, 0), (second, 5, 0)])
    assert np.all(gap["mosaic"][:, 3:5] == 0)
    assert gap["overlap_statistics"]["uncovered_pixels"] == 6


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
            f"{camera}-Camera": model, "ROI": roi, "Binning": "1x1"}


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
    manifest = json.loads(Path(first["manifest_path"]).read_text())
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
