"""Completed-survey ilastik scoring and probability-map pooling.

The executable boundary deliberately lives here, outside saved hook source.  A
saved hook containing this subprocess would be rejected by the hook linter and
could not be emitted as a standalone acquisition program.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import tifffile

from microclaw.hook_decisions import HookResult


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def decimate_field(image: np.ndarray, target_size: int = 256) -> np.ndarray:
    """Use ClassicalDescriptor's exact stride decimation on a 2-D field."""
    array = np.asarray(image)
    if array.ndim != 2:
        raise ValueError("ilastik field input must be a two-dimensional image")
    height, width = array.shape
    return array[::max(1, height // target_size), ::max(1, width // target_size)]


def _axis_keys(axistags) -> list[str]:
    if isinstance(axistags, bytes):
        axistags = axistags.decode("utf-8")
    if isinstance(axistags, str):
        axistags = json.loads(axistags)
    axes = axistags.get("axes", axistags) if isinstance(axistags, dict) else axistags
    return [axis["key"] if isinstance(axis, dict) else str(axis) for axis in axes]


def pool_probability_map(probabilities, *, axistags, label_names,
                         coverage_floor=0.01, high_percentiles=(95.0, 99.0),
                         area_threshold=0.5):
    """Pool all class channels while preserving unresolved class semantics."""
    labels = [item.decode("utf-8") if isinstance(item, bytes) else str(item)
              for item in label_names]
    axes = _axis_keys(axistags)
    if sorted(axes) != ["c", "x", "y"] or len(axes) != 3:
        raise ValueError(f"expected exactly y/x/c axes from axistags, got {axes!r}")
    array = np.moveaxis(np.asarray(probabilities), axes.index("c"), -1)
    if array.shape[-1] != len(labels):
        raise ValueError("probability channel count does not match LabelNames")
    required = {"BG", "apo_mito", "healthy_mito"}
    if set(labels) != required:
        raise ValueError(f"project labels must be exactly {sorted(required)!r}")
    by_label = {label: array[..., index].astype(np.float64, copy=False)
                for index, label in enumerate(labels)}
    vector = {}
    for label in labels:
        channel = by_label[label]
        vector[label] = {
            "mean": float(np.mean(channel)),
            "high_percentiles": {
                str(float(q)): float(np.percentile(channel, q)) for q in high_percentiles
            },
            "thresholded_area": float(np.mean(channel >= area_threshold)),
        }
    coverage = 1.0 - vector["BG"]["mean"]
    result = {
        "pooled_channels": vector,
        "mito_coverage": coverage,
        "coverage_floor": float(coverage_floor),
        "area_threshold": float(area_threshold),
        "channel_mapping": {
            "BG": "confirmed", "apo_mito": "unverified", "healthy_mito": "unverified"
        },
        "ranking_unit": "whole_field",
    }
    if coverage < coverage_floor:
        result.update(status="unresolved", apo_fraction=None)
    else:
        apo = float(np.sum(by_label["apo_mito"]))
        healthy = float(np.sum(by_label["healthy_mito"]))
        denominator = apo + healthy
        result.update(status="unresolved" if denominator <= 0 else "unverified",
                      apo_fraction=None if denominator <= 0 else apo / denominator)
    return result


class IlastikPooledObservationAdapter:
    """Plain saved-hook shape for an already-produced probability map.

    It intentionally does not launch ilastik.  The completed-survey adapter
    below owns that boundary between passes.
    """

    def __init__(self, coverage_floor=0.01, high_percentiles=(95.0, 99.0),
                 area_threshold=0.5):
        self.parameters = (coverage_floor, high_percentiles, area_threshold)

    def analyze_frame(self, image, metadata):
        coverage_floor, high_percentiles, area_threshold = self.parameters
        pooled = pool_probability_map(
            image, axistags=metadata["axistags"], label_names=metadata["LabelNames"],
            coverage_floor=coverage_floor, high_percentiles=high_percentiles,
            area_threshold=area_threshold,
        )
        return HookResult(pooled)


class IlastikCompletedDatasetAdapter:
    """Run one bounded ilastik process across all selected saved fields."""

    def __init__(self, executable_path, project_path, project_sha256, timeout_s=600,
                 coverage_floor=0.01, high_percentiles=(95.0, 99.0),
                 area_threshold=0.5, target_size=256, launcher_script_path=None):
        self.executable_path = Path(executable_path).resolve()
        self.project_path = Path(project_path).resolve()
        self.project_sha256 = project_sha256
        self.timeout_s = float(timeout_s)
        self.coverage_floor = float(coverage_floor)
        self.high_percentiles = tuple(high_percentiles)
        self.area_threshold = float(area_threshold)
        self.target_size = int(target_size)
        self.launcher_script_path = (Path(launcher_script_path).resolve()
                                     if launcher_script_path else None)

    def analyze_completed_dataset(self, dataset_view, selection, context):
        if not self.executable_path.is_file():
            raise FileNotFoundError(f"ilastik executable not found: {self.executable_path}")
        if self.launcher_script_path is not None and not self.launcher_script_path.is_file():
            raise FileNotFoundError(f"ilastik launcher script not found: {self.launcher_script_path}")
        if not self.project_path.is_file():
            raise FileNotFoundError(f"ilastik project not found: {self.project_path}")
        actual_hash = _sha256(self.project_path)
        if actual_hash != self.project_sha256:
            raise ValueError(
                f"ilastik project sha256 mismatch: expected {self.project_sha256}, got {actual_hash}"
            )
        import h5py
        with h5py.File(self.project_path, "r") as project:
            label_names = list(project["PixelClassification/LabelNames"][()])
        with tempfile.TemporaryDirectory(prefix="microclaw-ilastik-") as temporary:
            work = Path(temporary).resolve()
            inputs = []
            coordinates = []
            for index, item in enumerate(dataset_view.coordinates):
                context.raise_if_cancelled()
                coordinates.append(dict(item))
                path = work / f"field_{index:06d}.tiff"
                tifffile.imwrite(path, decimate_field(
                    dataset_view.read_image(**dict(item)), self.target_size
                ))
                inputs.append(path)
            output_template = str(work / "{nickname}_probabilities.h5")
            command = [str(self.executable_path)]
            if self.launcher_script_path is not None:
                command.append(str(self.launcher_script_path))
            command += [
                "--headless", "--readonly=true",
                f"--project={self.project_path}", "--output_format=hdf5",
                f"--output_filename_format={output_template}",
                *map(str, inputs),
            ]
            try:
                subprocess.run(command, cwd=work, check=True, timeout=self.timeout_s,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            except subprocess.TimeoutExpired as error:
                raise TimeoutError(
                    f"ilastik batch exceeded hard timeout of {self.timeout_s:g} s and was killed"
                ) from error
            results = []
            for item, input_path in zip(coordinates, inputs):
                output = work / f"{input_path.stem}_probabilities.h5"
                if not output.is_file():
                    raise FileNotFoundError(f"ilastik did not create expected output: {output.name}")
                with h5py.File(output, "r") as handle:
                    dataset = handle["exported_data"]
                    pooled = pool_probability_map(
                        dataset[()], axistags=dataset.attrs["axistags"],
                        label_names=label_names, coverage_floor=self.coverage_floor,
                        high_percentiles=self.high_percentiles,
                        area_threshold=self.area_threshold,
                    )
                results.append({
                    "result": {"coordinates": item, **pooled},
                    "status": "unverified",
                    "analyzer": "ilastik_pixel_classification",
                    "analyzer_version": "1.4.2",
                    "parameters": {
                        "project_sha256": self.project_sha256,
                        "target_size": self.target_size,
                        "coverage_floor": self.coverage_floor,
                        "high_percentiles": list(self.high_percentiles),
                        "area_threshold": self.area_threshold,
                    },
                })
            return results
