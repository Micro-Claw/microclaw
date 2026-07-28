from __future__ import annotations

import importlib.util
import math
from pathlib import Path


def _probe():
    path = Path(__file__).parents[1] / "design" / "29-mm-pixel-affine-probe.py"
    spec = importlib.util.spec_from_file_location("design29_affine_probe", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_mmcore_row_major_ordering_and_verdicts():
    probe = _probe()
    cases = {
        "identity": ([1, 0, 0, 0, 1, 0], "IDENTITY"),
        "isotropic": ([0.16, 0, 0, 0, 0.16, 0], "USABLE"),
        "rotated": ([0, -0.16, 0, 0.16, 0, 0], "FULL GEOMETRY"),
        "reflected": ([-0.16, 0, 0, 0, 0.16, 0], "FULL GEOMETRY"),
        "singular": ([1, 2, 0, 2, 4, 0], "INVALID — singular"),
        "all_zeros": ([0, 0, 0, 0, 0, 0], "INVALID — all-zeros"),
        "non_finite": ([math.nan, 0, 0, 0, 1, 0], "INVALID — non-finite"),
    }
    for raw, prefix in cases.values():
        decoded = probe._decode_mmcore_affine(raw)
        assert probe._classify(decoded).startswith(prefix)

    decoded = probe._decode_mmcore_affine([1, 2, 3, 4, 5, 6])
    assert decoded["linear_pixel_to_stage"] == [[1, 2], [4, 5]]
    assert decoded["translation_um"] == [3, 6]


def test_zero_scale_does_not_manufacture_nan():
    probe = _probe()
    decoded = probe._decode_mmcore_affine([1, 0, 0, 0, 0, 0])
    assert decoded["normalized_axis_dot"] is None
