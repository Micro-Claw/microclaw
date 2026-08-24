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
                         background_label, numerator_label, denominator_label,
                         coverage_floor=0.01, high_percentiles=(95.0, 99.0),
                         area_threshold=0.5, coverage_key="coverage",
                         ratio_key="ratio", label_semantics=None):
    """Pool all class channels while preserving unresolved class semantics."""
    labels = [item.decode("utf-8") if isinstance(item, bytes) else str(item)
              for item in label_names]
    axes = _axis_keys(axistags)
    if sorted(axes) != ["c", "x", "y"] or len(axes) != 3:
        raise ValueError(f"expected exactly y/x/c axes from axistags, got {axes!r}")
    array = np.moveaxis(np.asarray(probabilities), axes.index("c"), -1)
    if array.shape[-1] != len(labels):
        raise ValueError("probability channel count does not match LabelNames")
    configured = {background_label, numerator_label, denominator_label}
    missing = configured - set(labels)
    if missing:
        raise ValueError(
            f"configured labels are absent from project LabelNames: {sorted(missing)!r}; "
            f"available labels: {labels!r}"
        )
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
    coverage = 1.0 - vector[background_label]["mean"]
    result = {
        "pooled_channels": vector,
        coverage_key: coverage,
        "coverage_floor": float(coverage_floor),
        "area_threshold": float(area_threshold),
        "channel_mapping": {
            label: (label_semantics or {}).get(label, "unverified") for label in labels
        },
        "ranking_unit": "whole_field",
    }
    if coverage < coverage_floor:
        result.update(status="unresolved", **{ratio_key: None})
    else:
        numerator = float(np.sum(by_label[numerator_label]))
        denominator = numerator + float(np.sum(by_label[denominator_label]))
        result.update(status="unresolved" if denominator <= 0 else "unverified",
                      **{ratio_key: None if denominator <= 0 else numerator / denominator})
    return result


def _validate_absolute_command_paths(command: list[str], launcher_script_path,
                                     input_paths) -> None:
    paths = [command[0], *map(str, input_paths)]
    if launcher_script_path is not None:
        paths.append(command[1])
    for prefix in ("--project=", "--output_filename_format="):
        paths.extend(item.removeprefix(prefix) for item in command if item.startswith(prefix))
    relative = [item for item in paths if not Path(item).is_absolute()]
    if relative:
        raise ValueError(f"every ilastik command path must be absolute: {relative!r}")


class IlastikCompletedDatasetAdapter:
    """Run one bounded ilastik process across all selected saved fields."""

    def __init__(self, executable_path, project_path, project_sha256,
                 background_label, numerator_label, denominator_label, timeout_s=600,
                 coverage_floor=0.01, high_percentiles=(95.0, 99.0),
                 area_threshold=0.5, target_size=256, launcher_script_path=None,
                 coverage_key="coverage", ratio_key="ratio",
                 label_semantics=None):
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
        self.background_label = str(background_label)
        self.numerator_label = str(numerator_label)
        self.denominator_label = str(denominator_label)
        self.coverage_key = str(coverage_key)
        self.ratio_key = str(ratio_key)
        self.label_semantics = dict(label_semantics or {})

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
        try:
            import h5py
        except (ImportError, ValueError) as error:
            raise RuntimeError(
                "ilastik HDF5 support is unavailable; install the microclaw[ilastik] "
                "extra in Microclaw's environment"
            ) from error
        with h5py.File(self.project_path, "r") as project:
            label_names = list(project["PixelClassification/LabelNames"][()])
            version_value = project["ilastikVersion"][()]
            if isinstance(version_value, np.ndarray):
                version_value = version_value.item()
            ilastik_version = (version_value.decode("utf-8")
                               if isinstance(version_value, bytes) else str(version_value))
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
            _validate_absolute_command_paths(command, self.launcher_script_path, inputs)
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
                        label_names=label_names, background_label=self.background_label,
                        numerator_label=self.numerator_label,
                        denominator_label=self.denominator_label,
                        coverage_floor=self.coverage_floor,
                        high_percentiles=self.high_percentiles,
                        area_threshold=self.area_threshold,
                        coverage_key=self.coverage_key, ratio_key=self.ratio_key,
                        label_semantics=self.label_semantics,
                    )
                results.append({
                    "result": {"coordinates": item, **pooled},
                    "status": "unverified",
                    "analyzer": "ilastik_pixel_classification",
                    "analyzer_version": ilastik_version,
                    "parameters": {
                        "project_sha256": self.project_sha256,
                        "target_size": self.target_size,
                        "coverage_floor": self.coverage_floor,
                        "high_percentiles": list(self.high_percentiles),
                        "area_threshold": self.area_threshold,
                        "background_label": self.background_label,
                        "numerator_label": self.numerator_label,
                        "denominator_label": self.denominator_label,
                        "coverage_key": self.coverage_key,
                        "ratio_key": self.ratio_key,
                        "label_semantics": self.label_semantics,
                    },
                })
            return results
