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
    manifest_document = json.loads(Path(first["manifest_path"]).read_text(encoding="utf-8"))
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


def test_roi_difference_is_recorded_not_refused(monkeypatch, tmp_path):
    """Measured on M2: a full-frame calibration is valid for a cropped dataset.

    Placement consumes only the affine's four coefficients and ROI does not
    enter that arithmetic, so a crop on the same camera at the same binning
    costs a constant translation of the whole mosaic and nothing else.
    """
    FakeDataset.images = {("p0", 0): np.ones((3, 3), np.uint16)}
    FakeDataset.metadata = {("p0", 0): metadata(0, 0, roi="36-50-453-227")}
    cropped = run_tool(monkeypatch, tmp_path, {"time": 0},
                       artifact(tmp_path / "fullframe.json", roi=(0, 0, 512, 512)))
    assert cropped["calibration_roi_difference"]["dataset"] == [36, 50, 453, 227]
    assert cropped["calibration_roi_difference"]["calibration"] == [0, 0, 512, 512]
    assert (tmp_path / "out.tif").exists()
    manifest = json.loads(Path(cropped["manifest_path"]).read_text(encoding="utf-8"))["manifest_payload"]
    assert manifest["calibration_roi_difference"]["calibration"] == [0, 0, 512, 512]

    # A matching ROI records nothing, so the field cannot be read as a warning
    # that is always present.
    FakeDataset.metadata = {("p0", 0): metadata(0, 0)}
    assert run_tool(monkeypatch, tmp_path, {"time": 0})["calibration_roi_difference"] is None


@pytest.mark.parametrize("kwargs,expected", [
    ({"camera": "Hamamatsu"}, "camera_device"),
    ({"model": "other-model"}, "camera_model"),
])
def test_a_different_instrument_is_still_refused(monkeypatch, tmp_path, kwargs, expected):
    """Relaxing ROI must not weaken design/29 §5's camera-identity guarantee."""
    FakeDataset.images = {("p0", 0): np.ones((3, 3), np.uint16)}
    FakeDataset.metadata = {("p0", 0): metadata(0, 0)}
    wrong = artifact(tmp_path / "wrong.json", **kwargs)
    with pytest.raises(ValueError, match=expected):
        run_tool(monkeypatch, tmp_path, {"time": 0}, wrong)
    assert not (tmp_path / "out.tif").exists()


@pytest.mark.parametrize("key,model", [
    ("Andor-Camera", "| iXon Ultra | DU897_BV | 8172 |"),      # M2, measured
    ("Andor-CameraName", "C15440-20UP"),                        # M5 shape, measured
    ("Andor-CameraID", "S/N: 500975"),                          # last resort
])
def test_camera_model_is_read_from_any_vendor_key(monkeypatch, tmp_path, key, model):
    """MM stamps the model under whatever the adapter calls its property.

    Measured on real data: the Andor iXon exposes '-Camera'; the Hamamatsu
    C15440-20UP has no '-Camera' key at all and carries the model under
    '-CameraName'. Assuming one vendor's shape refused every M5 dataset.
    """
    FakeDataset.images = {("p0", 0): np.ones((3, 3), np.uint16)}
    base = metadata(0, 0)
    base.pop("Andor-Camera")
    base[key] = model
    FakeDataset.metadata = {("p0", 0): base}
    result = run_tool(monkeypatch, tmp_path, {"time": 0},
                      artifact(tmp_path / "cal.json", model=model))
    assert result["dataset_identity"]["camera_model"] == model
    assert result["dataset_identity"]["camera_model_key"] == key


def test_no_camera_model_key_at_all_names_every_candidate(monkeypatch, tmp_path):
    FakeDataset.images = {("p0", 0): np.ones((3, 3), np.uint16)}
    base = metadata(0, 0)
    base.pop("Andor-Camera")
    FakeDataset.metadata = {("p0", 0): base}
    with pytest.raises(ValueError, match="Andor-Camera or Andor-CameraName or Andor-CameraID"):
        run_tool(monkeypatch, tmp_path, {"time": 0})


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


def test_singleton_non_position_axes_default_without_selection(monkeypatch, tmp_path):
    FakeDataset.axes = {"position": ["p0"], "time": [0], "z": [0]}
    FakeDataset.images = {("p0", 0): np.ones((3, 3), np.uint16)}
    FakeDataset.metadata = {("p0", 0): metadata(0, 0)}
    try:
        result = run_tool(monkeypatch, tmp_path, {})
        assert result["selection"] == {"time": 0, "z": 0}
    finally:
        FakeDataset.axes = {"position": ["p0", "p1"], "time": [0, 1]}


@pytest.mark.parametrize('transform,expected', [
    (affine(0, .127, -.127, 0), {
        'origin_um': [9.873, 19.8095], 'extent_um': [0.554000000000002, 0.48100000000000165],
        'output_basis_um': [[.127, 0.], [0., .127]],
        'overlap_statistics': {'covered_pixels': 21, 'uncovered_pixels': 9, 'overlap_pixels': 3, 'maximum_coverage': 2, 'rasterized_output_sample_count': 24},
        'mosaic_sha256': 'd3f2f91b2ad4a24cd9cc9b15a632ffbeb5f616a6a16fc821e687ce20d5bc29f2',
        'coverage_mask_sha256': 'df9fddce92d8dfe9d70624d59c9f6090631a89d581893a93a269cfb8793e3f1f',
    }),
    (affine(1, .3, .2, 1), {
        'origin_um': [8.2, 18.7], 'extent_um': [3.900000000000002, 2.700000000000003],
        'output_basis_um': [[.127, 0.], [0., .127]],
        'overlap_statistics': {'covered_pixels': 637, 'uncovered_pixels': 99, 'overlap_pixels': 507, 'maximum_coverage': 2, 'rasterized_output_sample_count': 1144},
        'mosaic_sha256': '28c3e19a721d855bbcffec59d0be50552f657691d876f4c8a88a29ff5b4fdf0d',
        'coverage_mask_sha256': 'aead848e8438df808d8796d56645888bfc1abad6bd0d5e7f7cb574c4410e89ed',
    }),
])
def test_placements_preserve_pre81b_mosaic_golden(transform, expected, monkeypatch, tmp_path):
    frames = [(np.arange(12, dtype=np.uint16).reshape(3, 4) + 1, 10, 20),
              (np.full((3, 4), 99, np.uint16), 10.3, 20.1)]
    result = assemble(frames, transform, sample=.127)
    placements = result.pop('tile_placements')
    source_basis = result.pop('source_basis_um')
    convention = result.pop('tile_bounds_convention')
    for key in ('mosaic', 'coverage_mask'):
        result[key + '_sha256'] = hashlib.sha256(result.pop(key).tobytes()).hexdigest()
    # Captured from 57652fd before adding placements: byte hashes + exact values.
    assert result == expected
    for index, placement in enumerate(placements):
        _, x, y = frames[index]
        assert placement['index'] == index
        assert placement['centre_stage_um'] == [x, y]
        assert placement['source_shape'] == [3, 4]
        assert placement['source_basis_ref'] == 'source_basis_um'
        assert source_basis == [[transform.a, transform.b], [transform.c, transform.d]]
        corners = [(x + transform.a*dx + transform.b*dy, y + transform.c*dx + transform.d*dy)
                   for dx in (-1.5, 1.5) for dy in (-1, 1)]
        bounds = placement['bounds_stage_um']
        assert bounds == {'x_min': min(v[0] for v in corners), 'x_max': max(v[0] for v in corners),
                          'y_min': min(v[1] for v in corners), 'y_max': max(v[1] for v in corners)}
        assert placement['bounds_convention_ref'] == 'tile_bounds_convention'
        assert 'not exact tile footprints' in convention
        ox, oy = result['origin_um']
        assert placement['output_window_px'] == [
            math.floor((bounds['y_min']-oy)/.127+.5), math.floor((bounds['y_max']-oy)/.127+.5)+1,
            math.floor((bounds['x_min']-ox)/.127+.5), math.floor((bounds['x_max']-ox)/.127+.5)+1,
        ]


    # Exercise the manifest boundary too: its TIFF hash and coverage fraction
    # must retain the pre-change raster, not merely agree with this build.
    from microclaw import tools
    FakeDataset.images = {(f"p{i}", 0): frame[0] for i, frame in enumerate(frames)}
    FakeDataset.metadata = {
        (f"p{i}", 0): {**metadata(x, y, roi="0-0-4-3"), "Width": 4}
        for i, (_, x, y) in enumerate(frames)
    }
    calibration = tmp_path / 'golden-calibration.json'
    calibration.write_text(json.dumps({
        'payload': canonical_affine_payload(transform), 'payload_sha256': affine_payload_hash(transform),
        'camera_device': 'Andor', 'camera_model': 'model', 'roi': [0, 0, 4, 3],
    }), encoding='utf-8')
    monkeypatch.setattr(tools, 'Dataset', FakeDataset)
    guard = MagicMock()
    guard.resolve_readable_path.side_effect = lambda path: path
    guard.resolve_in_workspace.side_effect = lambda path: path
    manifest = tools.build_stage_coordinate_mosaic(
        None, guard, 'dataset', str(tmp_path / 'golden.tiff'), {'time': 0},
        {'kind': 'artifact', 'path': str(calibration)}, .127,
    )
    assert manifest['pixel_sha256'] == expected['mosaic_sha256']
    for key in ('origin_um', 'extent_um', 'output_basis_um', 'overlap_statistics'):
        assert manifest[key] == expected[key]
    stats = expected['overlap_statistics']
    assert manifest['coverage_fraction'] == stats['covered_pixels'] / (stats['covered_pixels'] + stats['uncovered_pixels'])
    assert manifest['overwrite_convention'] == 'later source tiles overwrite earlier source tiles for display'
