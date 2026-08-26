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


def choose_stride(shape, *, native_pixel_size_um, training_resolution_um,
                  target_size=None):
    """Pick the decimation stride, and say why.

    ilastik's trained feature scales are in PIXELS, so the only stride that
    asks the classifier the question it was trained on is the one that lands
    the effective pixel size on the size the project was drawn at. When both
    numbers are known that is simply training / native -- and it happens to
    be the same stride the old fixed 256-pixel target was reaching for on a
    fine rig, so matching the scale costs nothing in speed and is the reason
    the fixed target ever looked right.

    A caller who passes target_size explicitly overrides this. With neither a
    pixel size nor a training resolution there is nothing to match, so it
    falls back to the old behaviour and says so.
    """
    height, width = shape
    if target_size is not None:
        stride = max(1, height // target_size, width // target_size)
        return stride, "explicit_target_size"
    if native_pixel_size_um and training_resolution_um:
        # Cannot upsample: a rig coarser than the training data gets stride 1
        # and a recorded mismatch rather than a silent interpolation.
        stride = max(1, round(training_resolution_um / native_pixel_size_um))
        return stride, "scale_matched"
    # No scale to match against. Decimating anyway is a guess about a trained
    # classifier's input, and the way that guess fails is silent -- so keep the
    # pixels and pay the time. Speed is the caller's to buy with target_size.
    return 1, "unknown_scale_no_decimation"


def decimate_field(image: np.ndarray, target_size: int = 256):
    """Decimate a 2-D field by ONE stride, and report the stride used.

    The classical descriptor strides each axis independently, which is
    harmless for the photometric quantities it computes. It is not harmless
    here: a trained pixel classifier is being asked about *shape*, and an
    independent per-axis stride squashes a non-square field along one axis
    only -- a 220x512 frame becomes 220x256, and every mitochondrion in it is
    half as wide as the one the classifier was trained on. One stride, taken
    from the axis that needs the most, keeps the aspect ratio intact.
    """
    array = np.asarray(image)
    if array.ndim != 2:
        raise ValueError("ilastik field input must be a two-dimensional image")
    stride, _ = choose_stride(array.shape, native_pixel_size_um=None,
                              training_resolution_um=None, target_size=target_size)
    return array[::stride, ::stride], stride


def decimate_by_stride(image: np.ndarray, stride: int):
    array = np.asarray(image)
    if array.ndim != 2:
        raise ValueError("ilastik field input must be a two-dimensional image")
    return array[::stride, ::stride]


def _training_resolution_um(project):
    """The pixel size the project's LABEL blocks were drawn at, if recorded.

    ilastik's trained feature scales are in PIXELS. Scoring at a different
    pixel size asks those features about different physical sizes than they
    were trained on, which a probability map will not tell you about -- it
    will just be wrong. Nothing here enforces a match; recording both numbers
    is what makes a bad result diagnosable instead of mysterious.

    It lives on the label blocks, not on the input-data axistags, which the
    real project records as 0. Best effort: lanes disagree (this project has
    0.127 on one and nothing on the other), so this is what the project
    remembers, not an authority.
    """
    import json as _json
    try:
        sets = project["PixelClassification/LabelSets"]
        names = list(sets.keys())
    except Exception:
        return None
    for name in names:
        try:
            group = sets[name]
            for block in list(group.keys()):
                tags = group[block].attrs.get("axistags")
                if isinstance(tags, bytes):
                    tags = tags.decode("utf-8")
                for axis in _json.loads(tags).get("axes", []):
                    value = float(axis.get("resolution") or 0.0)
                    # ilastik writes 1 when nobody set a pixel size, so 1 is a
                    # placeholder far more often than a real micron. Reading it
                    # as a measurement decimated a 324x312 M5 field to 36x35 --
                    # below the project's own feature scales -- and ilastik
                    # produced no output at all. A rig genuinely at 1 um/px
                    # loses only the automatic stride, which is the safe way to
                    # be wrong.
                    if value > 0 and value != 1.0:
                        return value
        except Exception:
            continue
    return None


def _tail(text, lines=12):
    """The last few lines of ilastik's own output, for a failure message."""
    kept = [line for line in (text or "").strip().splitlines() if line.strip()]
    return "\n".join(kept[-lines:]) or "(ilastik printed nothing)"


def _trained_labels(project, label_names):
    """The subset of LabelNames the classifier actually learned.

    ilastik exports one channel per NAMED label, so a class nobody drew comes
    back identically zero rather than missing -- and a ratio against it is
    pinned at 1.000, which reads as a confident result and is an artifact.
    known_labels says which classes were trained; ilastik numbers them from 1.
    """
    try:
        known = [int(v) for v in project["PixelClassification/ClassifierForests/known_labels"][()]]
    except Exception:
        return None
    return {label_names[k - 1] for k in known if 1 <= k <= len(label_names)}


def _native_pixel_size_um(dataset_view, coordinates):
    """Micro-Manager's recorded pixel size, or None when it is uncalibrated."""
    try:
        metadata = dataset_view.read_metadata(**dict(coordinates))
    except Exception:
        return None
    for key in ("PixelSizeUm", "PixelSize_um"):
        try:
            value = float(metadata.get(key))
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return None


def discover_ilastik():
    """Find an installed ilastik, or return None having looked.

    Nobody should have to tell Microclaw where ilastik lives to score their
    own project with it. Returns (executable, launcher_script) because macOS
    needs both: the bundle's own `bin/ilastik` carries a shebang naming the
    build machine's interpreter, so it has to be run as an argument to the
    bundled Python. Everywhere else the launcher is None.

    Several installations is not a choice this can make for someone, so it
    reports them and lets the caller pass one explicitly.
    """
    import glob
    import shutil
    import sys

    candidates = []
    if sys.platform == "darwin":
        for bundle in sorted(glob.glob("/Applications/ilastik-*.app")):
            base = Path(bundle) / "Contents" / "ilastik-release" / "bin"
            launcher = base / "ilastik"
            for name in ("python3.11", "python3", "python"):
                interpreter = base / name
                if interpreter.is_file() and launcher.is_file():
                    candidates.append((interpreter, launcher))
                    break
    elif sys.platform.startswith("win"):
        for root in (r"C:\Program Files", r"C:\Program Files (x86)"):
            for found in sorted(glob.glob(str(Path(root) / "ilastik-*" / "ilastik.exe"))):
                candidates.append((Path(found), None))
    else:
        for pattern in ("/opt/ilastik-*/run_ilastik.sh",
                        str(Path.home() / "ilastik-*" / "run_ilastik.sh")):
            for found in sorted(glob.glob(pattern)):
                candidates.append((Path(found), None))

    if not candidates:
        found = shutil.which("ilastik") or shutil.which("run_ilastik.sh")
        if found:
            candidates.append((Path(found), None))
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        return None
    raise ValueError(
        "several ilastik installations were found and choosing between them is "
        f"not Microclaw's call: {[str(item[0]) for item in candidates]!r}. "
        "Pass executable_path to say which one."
    )


def _check_configured_labels(labels, background_label, numerator_label,
                             denominator_label) -> None:
    """Refuse a label the project does not have, naming the ones it does."""
    missing = {background_label, numerator_label, denominator_label} - set(labels)
    if missing:
        raise ValueError(
            f"configured labels are absent from project LabelNames: {sorted(missing)!r}; "
            f"available labels: {labels!r}"
        )


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
    _check_configured_labels(labels, background_label, numerator_label,
                             denominator_label)
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


def _intended_stage_xy(dataset_view, coordinates):
    """Intended stage XY for a field, or (None, None).

    Ranking a survey is only useful if the winning field can be revisited, and
    the axis coordinate alone does not locate a stage. Micro-Manager stamps
    these keys on every multi-position acquisition and omits them on
    single-position ones, so absence is ordinary and must not raise.
    """
    try:
        metadata = dataset_view.read_metadata(**dict(coordinates))
    except Exception:
        return None, None
    def _read(key):
        value = metadata.get(key)
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    return _read("XPosition_um_Intended"), _read("YPosition_um_Intended")


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

    def __init__(self, project_path,
                 background_label, numerator_label, denominator_label,
                 *, executable_path=None, project_sha256=None, timeout_s=600,
                 coverage_floor=0.01, high_percentiles=(95.0, 99.0),
                 area_threshold=0.5, target_size=None, launcher_script_path=None,
                 coverage_key="coverage", ratio_key="ratio",
                 label_semantics=None):
        # Keyword-only past the four a person actually knows. Reordering this
        # signature once already slid a label name into the hash slot.
        self.executable_path = None if executable_path is None else Path(executable_path).resolve()
        self.project_path = Path(project_path).resolve()
        self.project_sha256 = project_sha256
        self.timeout_s = float(timeout_s)
        self.coverage_floor = float(coverage_floor)
        self.high_percentiles = tuple(high_percentiles)
        self.area_threshold = float(area_threshold)
        self.target_size = None if target_size is None else int(target_size)
        self.launcher_script_path = (Path(launcher_script_path).resolve()
                                     if launcher_script_path else None)
        self.background_label = str(background_label)
        self.numerator_label = str(numerator_label)
        self.denominator_label = str(denominator_label)
        self.coverage_key = str(coverage_key)
        self.ratio_key = str(ratio_key)
        self.label_semantics = dict(label_semantics or {})

    def analyze_completed_dataset(self, dataset_view, selection, context):
        if self.executable_path is None:
            discovered = discover_ilastik()
            if discovered is None:
                raise FileNotFoundError(
                    "no ilastik installation was found. Pass executable_path with the "
                    "path to the ilastik executable (on macOS, the bundled interpreter "
                    "in ilastik-*.app/Contents/ilastik-release/bin, with "
                    "launcher_script_path alongside it)."
                )
            self.executable_path, launcher = discovered
            if launcher is not None and self.launcher_script_path is None:
                self.launcher_script_path = launcher
        if not self.executable_path.is_file():
            raise FileNotFoundError(f"ilastik executable not found: {self.executable_path}")
        if self.launcher_script_path is not None and not self.launcher_script_path.is_file():
            raise FileNotFoundError(f"ilastik launcher script not found: {self.launcher_script_path}")
        if not self.project_path.is_file():
            raise FileNotFoundError(f"ilastik project not found: {self.project_path}")
        # design/26 F4 asks for the project to be hash-PINNED IN THE MANIFEST.
        # That is provenance, and provenance is something Microclaw can take
        # for itself: on first use there is nothing for the caller to have
        # pinned against, and making them paste a digest to run their own
        # classifier is the paragraph-of-explanation this project rejects.
        # Supply one and it is verified; omit it and it is recorded.
        actual_hash = _sha256(self.project_path)
        if self.project_sha256 is None:
            sha_source = "computed"
        elif actual_hash != self.project_sha256:
            raise ValueError(
                f"ilastik project sha256 mismatch: expected {self.project_sha256}, got {actual_hash}"
            )
        else:
            sha_source = "verified"
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
            training_resolution_um = _training_resolution_um(project)
        # Check the caller's labels the moment the project's are known. This
        # lived in pool_probability_map, which runs only after the batch, so a
        # mistyped label was refused *after* ilastik had scored every field --
        # measured at 116 s on nine demo tiles, and minutes on a real survey.
            names = [item.decode("utf-8") if isinstance(item, bytes) else str(item)
                     for item in label_names]
            _check_configured_labels(names, self.background_label,
                                     self.numerator_label, self.denominator_label)
            trained = _trained_labels(project, names)
            if trained is not None:
                untrained = {self.numerator_label, self.denominator_label} - trained
                if untrained:
                    raise ValueError(
                        f"the project names {sorted(untrained)!r} but never trained on "
                        f"them: ilastik exports an all-zero channel for a class nobody "
                        f"drew, so a ratio against it would be pinned at 1.000 and read "
                        f"as a confident result. Trained labels: {sorted(trained)!r}."
                    )
        with tempfile.TemporaryDirectory(prefix="microclaw-ilastik-") as temporary:
            work = Path(temporary).resolve()
            inputs = []
            coordinates = []
            strides = []
            native_um = None
            stride_mode = None
            for index, item in enumerate(dataset_view.coordinates):
                context.raise_if_cancelled()
                coordinates.append(dict(item))
                image = dataset_view.read_image(**dict(item))
                if native_um is None:
                    native_um = _native_pixel_size_um(dataset_view, item)
                stride, stride_mode = choose_stride(
                    np.asarray(image).shape, native_pixel_size_um=native_um,
                    training_resolution_um=training_resolution_um,
                    target_size=self.target_size,
                )
                path = work / f"field_{index:06d}.tiff"
                tifffile.imwrite(path, decimate_by_stride(image, stride))
                strides.append(stride)
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
                completed = subprocess.run(
                    command, cwd=work, check=True, timeout=self.timeout_s,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                captured = completed.stdout or ""
            except subprocess.TimeoutExpired as error:
                raise TimeoutError(
                    f"ilastik batch exceeded hard timeout of {self.timeout_s:g} s and was killed"
                ) from error
            except subprocess.CalledProcessError as error:
                raise RuntimeError(
                    f"ilastik exited {error.returncode}. Its own output ends:"
                    f"\n{_tail(error.output)}"
                ) from error
            results = []
            for item, input_path, stride_used in zip(coordinates, inputs, strides):
                output = work / f"{input_path.stem}_probabilities.h5"
                if not output.is_file():
                    # We captured ilastik's own words and used to throw them
                    # away. On M5 that turned a FeatureSelectionConstraintError
                    # -- the input had been decimated below the project's
                    # feature scales -- into "did not create expected output",
                    # and the session retried three times blaming flaky I/O.
                    raise FileNotFoundError(
                        f"ilastik did not create expected output: {output.name}. "
                        f"Its own output ends:\n{_tail(captured)}"
                    )
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
                stage_x_um, stage_y_um = _intended_stage_xy(dataset_view, item)
                results.append({
                    "result": {"coordinates": item, "stage_x_um": stage_x_um,
                               "stage_y_um": stage_y_um, **pooled},
                    "status": "unverified",
                    "analyzer": "ilastik_pixel_classification",
                    "parameters": {
                        # The project records the version it was DRAWN in, which
                        # is not necessarily the binary that just ran. Name it
                        # for what it is and record the executable beside it,
                        # rather than asserting an analyzer version nothing here
                        # verified.
                        "project_ilastik_version": ilastik_version,
                        "decimation_stride": stride_used,
                        "decimation_mode": stride_mode,
                        "native_pixel_size_um": native_um,
                        "effective_pixel_size_um": (
                            None if native_um is None else native_um * stride_used),
                        "project_training_resolution_um": training_resolution_um,
                        "executable_path": str(self.executable_path),
                        "launcher_script_path": (str(self.launcher_script_path)
                                                 if self.launcher_script_path else None),
                        "project_sha256": actual_hash,
                        "project_sha256_source": sha_source,
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
