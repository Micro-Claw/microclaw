"""Reviewed, reproducible analysis over an already-completed NDTiff dataset.

The runner is trusted parent code and may use a SafetyGuard for path policy.
The loaded adapter is untrusted and receives only the narrow facades below.
Source review and hash pinning are the gate until process isolation exists.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import platform
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping

import numpy as np
import tifffile
from ndstorage import Dataset

from microclaw import __version__
from microclaw.hook_decisions import write_hook_artifact
from microclaw.hook_manager import (
    FORBIDDEN_SAVED_HOOK_PARAMS, MANIFEST, lint_hook_code, select_hook_class,
    verify_saved_hook_bytes,
)
from microclaw.hooks import write_analysis_observation
from microclaw.image_analysis import (
    compute_stats, connected_components, resolve_min_snr,
)
from microclaw.ilastik_adapter import IlastikCompletedDatasetAdapter
from microclaw.safety import SafetyViolation


OFFLINE_VERBS = ("analyze_completed_dataset", "analyze_saved_frame")
_ALLOWED_CAPABILITIES = frozenset(
    {"coordinates", "read_image", "read_metadata", "as_array", "artifacts",
     "observations", "cancellation"}
)


class ConnectedComponents:
    """Component counts over original saved frames or a stage-coordinate mosaic."""

    def __init__(self, min_snr: float, min_snr_source: str,
                 min_area_um2: float = 0.0, max_area_um2: float | None = None,
                 write_annotations: bool = True):
        if not isinstance(write_annotations, bool):
            raise ValueError("write_annotations must be a boolean")
        self.write_annotations = write_annotations
        self.affine = None  # resolved and injected only by the trusted runner
        self.annotations = {"requested": 0, "written": 0, "reason": None}
        self._annotation_limit_reason = None
        self.position_labels = []  # saved identity mapping injected by the runner
        self.field_label = None
        self.count_semantics = (
            "Component count: contiguous thresholded signal, not object identification. "
            "Touching objects can merge; noise, fragmentation and threshold choice affect counts. "
            "Signal visible in overlapping acquired fields appears in both per-field counts; "
            "their sum is not a unique object total. Mosaic counts measure resampled signal "
            "with later tiles overwriting earlier tiles, not original-field counts."
        )

        self.parameters = {
            "min_area_um2": min_area_um2, "max_area_um2": max_area_um2,
            "write_annotations": write_annotations,
            "min_snr": min_snr, "min_snr_source": min_snr_source,
        }

    def analyze_saved_frame(self, image, metadata, context):
        if metadata.get("input_kind") != "stage_coordinate_mosaic":
            return self._analyze_source_frame(image, metadata, context)
        mosaic = metadata["mosaic_manifest"]
        basis = mosaic["output_basis_um"]
        if basis[0][1] != 0 or basis[1][0] != 0 or basis[0][0] != basis[1][1]:
            raise ValueError("connected_components requires a square axis-aligned mosaic basis")
        measured, labels = connected_components(
            image, pixel_size_um=float(basis[0][0]), origin_um=mosaic["origin_um"],
            min_area_um2=self.parameters["min_area_um2"],
            max_area_um2=self.parameters["max_area_um2"],
            min_snr=self.parameters["min_snr"], return_labels=True,
        )
        envelope = {"result": measured, "status": "observed", "parameters": self.parameters}
        if self.write_annotations:
            self._annotate(image, measured, labels, context, envelope, mosaic=True,
                           source_dtype=mosaic.get("source_dtype", image.dtype))
        return envelope

    def _analyze_source_frame(self, image, metadata, context):
        affine = self.affine
        if affine is None:
            raise ValueError("Source-frame calibration must be resolved by the trusted runner")
        height, width = image.shape[:2]
        absent = [key for key in ("XPosition_um_Intended", "YPosition_um_Intended")
                  if metadata.get(key) in (None, "")]
        origin = None
        geometry_refusal = None
        if absent:
            geometry_refusal = "Stage coordinates refused: missing " + ", ".join(absent)
        else:
            dx, dy = affine.px_to_um(-(width - 1) / 2, -(height - 1) / 2)
            origin = [float(metadata["XPosition_um_Intended"]) + dx,
                      float(metadata["YPosition_um_Intended"]) + dy]
        measured, labels = connected_components(
            image, basis_um=[[affine.a, affine.b], [affine.c, affine.d]],
            origin_um=origin, covered_mask=np.ones((height, width), dtype=bool),
            min_area_um2=self.parameters["min_area_um2"],
            max_area_um2=self.parameters["max_area_um2"],
            min_snr=self.parameters["min_snr"], return_labels=True,
        )
        measured.update({
            "min_area_um2": self.parameters["min_area_um2"],
            "max_area_um2": self.parameters["max_area_um2"],
            "stage_geometry_refusal": geometry_refusal,
            "count_semantics_ref": "count_semantics",
            "frame_statistics": dict(compute_stats(image, min_snr=self.parameters["min_snr"])._asdict()),
        })
        envelope = {"result": measured, "status": "observed", "parameters": self.parameters}
        if self.write_annotations:
            self._annotate(image, measured, labels, context, envelope)
        return envelope

    def _annotate(self, image, measured, labels, context, envelope, *, mosaic=False,
                  source_dtype=None):
        from scipy import ndimage
        from microclaw.dataset_mosaic import draw_text_labels

        context.raise_if_cancelled()
        self.annotations["requested"] += 1
        annotation = {
            "field_label": None if mosaic else self.field_label,
            "position_labels": self.position_labels,
            "position_labels_partial": not self.position_labels,
            "position_label_reason": (None if self.position_labels else "selection has no position axis"),
            "order": "none; T numbers saved position identity, not capture order",
            "outline": 1,
            "written": False,
        }
        measured["annotation"] = annotation
        if self._annotation_limit_reason is not None:
            annotation.update({"reason": self._annotation_limit_reason,
                               "failure_kind": "limit_exhausted_upstream"})
            return
        foreground = int(np.iinfo(image.dtype if source_dtype is None else source_dtype).max)
        height, width = image.shape[:2]
        scale = min(8, max(1, min(height, width) // 70))
        annotation.update({"foreground": foreground, "glyph_scale": scale})
        canvas = image.copy()
        text_labels = []
        for number, component in enumerate(measured["objects"], 1):
            # This is the segmentation returned by the measurement, never a
            # second threshold/label pass that could diverge from its count.
            support = labels == component["label"]
            boundary = support & ~ndimage.binary_erosion(support)
            halo = ndimage.binary_dilation(boundary) & ~boundary
            canvas[halo] = 1
            canvas[boundary] = foreground
            rows, cols = np.nonzero(support)
            text_labels.append((float(rows.mean()), float(cols.mean()), str(number)))
        if not mosaic and self.field_label is not None:
            text_labels.append((4.5 * scale, (6 * len(self.field_label) + 1) * scale / 2,
                                self.field_label))
        # No position axis and an empty field still has visible evidence: 0 is
        # a count handle, not an invented position identity.
        if not text_labels:
            text_labels.append(((height - 1) / 2, (width - 1) / 2, "0"))
        clamped = []
        for row, col, text in text_labels:
            half_h = (9 * scale - 1) / 2
            half_w = ((6 * len(text) + 1) * scale - 1) / 2
            clamped.append((max(half_h, min(row, height - 1 - half_h)),
                            max(half_w, min(col, width - 1 - half_w)), text))
        draw_text_labels(canvas, clamped, foreground=foreground, outline=1, scale=scale)
        filename = f"components-{self.annotations['requested']:04d}.tiff"
        try:
            artifact = context.artifacts.emit(filename, canvas)
        except AnalysisCancelled:
            raise
        except ValueError as error:
            # The existing bounded writer uses ValueError for these three
            # limit cases. Other ValueErrors are programming/input defects.
            reason = str(error)
            if not (reason == "artifact count limit exhausted"
                    or reason == "artifact exceeds per-run total-bytes limit"
                    or (reason.startswith("artifact size ")
                        and " exceeds per-artifact size limit " in reason)):
                raise
            self._annotation_limit_reason = reason
            self.annotations["reason"] = reason
            annotation.update({"reason": reason, "failure_kind": "limit_exhausted"})
        except OSError as error:
            reason = f"{type(error).__name__}: {error}"
            self.annotations["reason"] = reason
            annotation.update({"reason": reason, "failure_kind": "io_error"})
        else:
            envelope["artifact_sha256"] = artifact["sha256"]
            annotation["written"] = True
            self.annotations["written"] += 1


class FrameStatistics:
    """Built-in package statistics over each selected saved frame."""

    def __init__(self, min_snr: float, min_snr_source: str):
        self.min_snr = min_snr
        self.min_snr_source = min_snr_source

    def analyze_saved_frame(self, image, metadata, context):
        return {
            "result": dict(compute_stats(image, min_snr=self.min_snr)._asdict()),
            "status": "observed", "parameters": {
                "min_snr": self.min_snr, "min_snr_source": self.min_snr_source,
            },
        }


BUILTIN_ADAPTERS = {
    "connected_components": ConnectedComponents,
    "frame_statistics": FrameStatistics,
    "ilastik_pixel_classification": IlastikCompletedDatasetAdapter,
}


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json_value(value: Any) -> Any:
    """Detach adapter-owned mutable containers and validate JSON portability."""
    return json.loads(json.dumps(value, allow_nan=False))


def _immutable(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _immutable(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_immutable(item) for item in value)
    return value


def _load_saved_adapter(name: str):
    """Load one reviewed/hash-pinned adapter and resolve its offline verb."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {}
    if name not in manifest:
        raise KeyError(
            f"No adapter named {name!r}. Available built-in adapters: "
            f"{sorted(BUILTIN_ADAPTERS)}. Available saved adapters: {sorted(manifest)}."
        )
    entry = manifest[name]
    source = verify_saved_hook_bytes(name, entry)
    new = set(lint_hook_code(source.decode("utf-8"))) - set(entry.get("accepted_warnings", []))
    if new:
        raise RuntimeError(
            f"Hook {name!r} has lint warnings the user never accepted: {sorted(new)}."
        )
    requested = set(entry.get("offline_capabilities", ()))
    denied = requested - _ALLOWED_CAPABILITIES
    if denied:
        raise ValueError(f"Offline adapter requests forbidden capabilities: {sorted(denied)}")

    import importlib.util
    spec = importlib.util.spec_from_file_location(f"microclaw_offline_{name}", entry["path"])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cls = select_hook_class(module, OFFLINE_VERBS)
    if cls is not None:
        verb = next(v for v in OFFLINE_VERBS if callable(getattr(cls, v, None)))
        return cls, verb, entry, source
    if select_hook_class(module, ("analyze_frame", "image_process_fn")) is not None:
        raise ValueError(
            f"Saved hook {name!r} implements only a live acquisition callback. "
            "Use analyze_saved_frame(image, metadata, context) or "
            "analyze_completed_dataset(dataset_view, selection, context) instead."
        )
    raise AttributeError(
        f"No class with {OFFLINE_VERBS[0]} or {OFFLINE_VERBS[1]} found in hook {name!r}."
    )


class DatasetView:
    """Read-only, selection-limited facade over an ndstorage Dataset."""

    __slots__ = ("__dataset", "__coordinates", "__keys", "__max_array_bytes")

    def __init__(self, dataset, coordinates: Iterable[dict], *, max_array_bytes: int):
        coords = tuple(MappingProxyType(dict(item)) for item in coordinates)
        self.__dataset = dataset
        self.__coordinates = coords
        self.__keys = frozenset(tuple(sorted(item.items())) for item in coords)
        self.__max_array_bytes = int(max_array_bytes)

    @property
    def coordinates(self) -> tuple[Mapping[str, Any], ...]:
        return self.__coordinates

    def _checked(self, coordinates: Mapping[str, Any]) -> dict:
        item = dict(coordinates)
        if tuple(sorted(item.items())) not in self.__keys:
            raise PermissionError("Coordinates are outside this DatasetView selection")
        return item

    def read_image(self, **coordinates):
        image = np.array(self.__dataset.read_image(**self._checked(coordinates)), copy=True)
        image.setflags(write=False)
        return image

    def read_metadata(self, **coordinates):
        return _immutable(_json_value(
            self.__dataset.read_metadata(**self._checked(coordinates))
        ))

    def as_array(self):
        images = [self.read_image(**dict(item)) for item in self.__coordinates]
        total = sum(image.nbytes for image in images)
        if total > self.__max_array_bytes:
            raise ValueError(
                f"Selected array is {total} bytes; limit is {self.__max_array_bytes} bytes"
            )
        array = np.stack(images)
        array.setflags(write=False)
        return array


@dataclass(frozen=True)
class ArtifactDirectory:
    """No path is exposed: adapters propose bounded parent-written artifacts."""

    _writer: Any

    def emit(self, filename: str, payload: bytes | np.ndarray) -> Mapping[str, Any]:
        return MappingProxyType(self._writer(filename, payload))


class AnalysisContext:
    """Bounded adapter context; deliberately contains no live capabilities."""

    __slots__ = ("input_identity", "artifacts", "__emit", "__cancelled")

    def __init__(self, identity: dict, artifacts: ArtifactDirectory, emit, cancelled):
        self.input_identity = _immutable(_json_value(identity))
        self.artifacts = artifacts
        self.__emit = emit
        self.__cancelled = cancelled

    @property
    def cancelled(self) -> bool:
        return bool(self.__cancelled())

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise AnalysisCancelled("Analysis was cancelled")

    def emit_observation(self, result, *, status="unverified", analyzer=None,
                         analyzer_version=None, parameters=None,
                         artifact_sha256=None):
        return MappingProxyType(self.__emit(
            result, status=status, analyzer=analyzer,
            analyzer_version=analyzer_version, parameters=parameters,
            artifact_sha256=artifact_sha256,
        ))


class AnalysisCancelled(RuntimeError):
    pass


def _dataset_content(dataset_path: str) -> tuple[str, str, list[dict]]:
    root = Path(dataset_path)
    files = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())
    content_digest = hashlib.sha256()
    manifest = []
    for path in files:
        relative = path.name if root.is_file() else path.relative_to(root).as_posix()
        data = path.read_bytes()
        item = {"relative_path": relative, "size_bytes": len(data), "sha256": _sha(data)}
        manifest.append(item)
        content_digest.update(len(data).to_bytes(8, "big"))
        content_digest.update(data)
    return (_sha(_canonical_bytes(manifest)), content_digest.hexdigest(), manifest)


def _optional_input_hashes(values: dict | None, guard) -> dict | None:
    if values is None:
        return None
    if not isinstance(values, dict):
        raise ValueError("model_project_config must be an object or None")
    records = {}
    for name, value in sorted(values.items()):
        # A *type probe*, not path handling: these values are arbitrary operator
        # data and the only question is "is this string a file?". A string the
        # resolver refuses is not a path, so the answer is no. `~500 cells` is
        # free text — expanduser leaves anything but a real `~user` alone, so
        # the resolver rightly refuses it, and refusing the whole record over a
        # tilde in a free-text value would be absurd (block 41d).
        source = None
        if isinstance(value, str):
            try:
                candidate = Path(guard.resolve_readable_path(value))
            except SafetyViolation:
                candidate = None
            if candidate is not None and candidate.is_file():
                source = candidate  # resolved once, then reused
        if source is not None:
            data = source.read_bytes()
            records[name] = {"reference": value, "sha256": _sha(data),
                             "size_bytes": len(data)}
        else:
            detached = _json_value(value)
            records[name] = {"value": detached, "sha256": _sha(_canonical_bytes(detached))}
    return records


def _normalized_result(raw: Any) -> dict:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise TypeError("Offline analysis results must be dictionaries or None")
    known = {"result", "status", "analyzer", "analyzer_version", "parameters",
             "artifact_sha256"}
    # A normalized envelope is explicit: it always has a ``result`` field.
    # Otherwise every key, including names such as ``status``, is a measurement.
    if "result" not in raw:
        return {"result": raw, "status": "unverified"}
    unknown = set(raw) - known
    if unknown:
        raise ValueError(f"Unknown normalized result fields: {sorted(unknown)}")
    normalized = dict(raw)
    normalized.setdefault("status", "unverified")
    return normalized


def run_analysis_on_saved_dataset(
    guard, dataset_path: str, adapter: str, axis_selection: dict,
    input_kind: str, parameters: dict, output_dir: str, *,
    calibration_ref: dict | None = None, output_pixel_size_um: float | None = None,
    model_project_config: dict | None = None,
    artifact_limits: dict | None = None, max_array_bytes: int = 512 * 1024 * 1024,
    cancellation_event: threading.Event | None = None,
) -> dict:
    """Run a reviewed offline adapter; this function performs no hardware action."""
    started = datetime.now(timezone.utc)
    clock = time.monotonic()
    if input_kind not in {"frames", "stage_coordinate_mosaic"}:
        raise ValueError("input_kind must be 'frames' or 'stage_coordinate_mosaic'")
    if not isinstance(axis_selection, dict) or not isinstance(parameters, dict):
        raise ValueError("axis_selection and parameters must be objects")
    dataset_path = guard.resolve_readable_path(dataset_path)
    output_dir = guard.resolve_in_workspace(output_dir)
    if Path(output_dir).exists():
        raise FileExistsError(
            f"Cannot create a file when that file already exists: {output_dir!r}"
        )
    if calibration_ref is not None and calibration_ref.get("kind") == "confirmed_current":
        raise ValueError(
            "confirmed_current calibration requires a live microscope core and is "
            "not available to completed-dataset replay; use artifact or knowledge_version"
        )

    builtin = BUILTIN_ADAPTERS.get(adapter)
    if builtin is not None:
        # Package code has the same trusted standing as live image_analysis.
        # Its exact class source is pinned in the reproducibility manifest.
        cls = builtin
        verb = ("analyze_completed_dataset"
                if callable(getattr(builtin, "analyze_completed_dataset", None))
                else "analyze_saved_frame")
        source = inspect.getsource(builtin).encode("utf-8")
        entry = {"source": "builtin", "version": __version__}
    else:
        cls, verb, entry, source = _load_saved_adapter(adapter)
    if builtin is not None and verb == "analyze_saved_frame":
        # Resolve optional rig state at the trusted runner boundary; adapters
        # remain plain measurement classes with no guard or configuration access.
        min_snr, min_snr_source = resolve_min_snr(
            explicit=parameters.get("min_snr"), configured=guard.analysis_min_snr,
        )
        parameters = {
            **parameters, "min_snr": min_snr, "min_snr_source": min_snr_source,
        }
    forbidden = set(parameters) & set(FORBIDDEN_SAVED_HOOK_PARAMS)
    if forbidden:
        raise ValueError(f"Offline adapter parameters request forbidden capabilities: {sorted(forbidden)}")
    # Built last, and only now is the output directory created. Constructing the
    # adapter is where a wrong or missing parameter surfaces, and a directory
    # made before that point survives the failure and then blocks the very name
    # the caller retries with -- turning one argument error into two unrelated
    # ones. Measured on the demo machine: three runs left five directories.
    instance = cls(**parameters)
    Path(output_dir).mkdir(parents=True, exist_ok=False)
    dataset = Dataset(dataset_path)
    from microclaw.tools import _iter_present_coords
    coordinates = list(_iter_present_coords(dataset, axis_selection))
    if not coordinates:
        raise ValueError("No images exist in the selected dataset")
    selection = {"axis_selection": {k: axis_selection[k] for k in sorted(axis_selection)},
                 "coordinates": [{k: item[k] for k in sorted(item)} for item in coordinates]}
    dataset_hash, content_hash, content_manifest = _dataset_content(dataset_path)
    content_identity = {"dataset_sha256": dataset_hash,
                        "selection_sha256": _sha(_canonical_bytes(selection)),
                        "content_sha256": content_hash,
                        "selected_coordinates": selection["coordinates"]}

    limits = artifact_limits or {"max_artifact_bytes": 64 * 1024 * 1024,
                                 "max_count": 64, "max_total_bytes": 256 * 1024 * 1024}
    if set(limits) != {"max_artifact_bytes", "max_count", "max_total_bytes"} or any(
        not isinstance(v, int) or isinstance(v, bool) or v <= 0 for v in limits.values()
    ):
        raise ValueError("artifact_limits must contain three positive integer limits")
    artifact_state: dict[str, int] = {}
    artifacts: list[dict] = []
    observations: list[dict] = []
    cancelled = cancellation_event or threading.Event()

    def write_artifact(filename, payload):
        info = write_hook_artifact(Path(output_dir) / "artifacts", filename, payload,
                                   state=artifact_state, **limits)
        artifacts.append(info)
        return info

    def emit(result, *, status, analyzer=None, analyzer_version=None, parameters=None,
             artifact_sha256=None, where=None):
        # Built-ins are reviewed package measurements and may assert `observed`;
        # saved adapters remain untrusted, exactly like saved live hooks.
        allowed_statuses = ({"unverified", "provisional", "observed"}
                            if builtin is not None else {"unverified", "provisional"})
        if status not in allowed_statuses:
            reason = (" and is not self-assertable"
                      if builtin is None else "")
            raise ValueError(
                f"Offline analysis status must be one of {sorted(allowed_statuses)}; "
                f"{status!r} is not allowed for this adapter{reason}."
            )
        return write_analysis_observation(
            observations, where=where, analyzer=analyzer or adapter,
            analyzer_version=analyzer_version or entry.get("version"), result=result,
            parameters=parameters, artifact_sha256=artifact_sha256, status=status,
        )

    context = AnalysisContext(content_identity, ArtifactDirectory(write_artifact),
                              emit, cancelled.is_set)
    view = DatasetView(dataset, coordinates, max_array_bytes=max_array_bytes)
    calibration_identity = None
    saved_calibration_details = {}
    failure = None
    status = "completed"
    mosaic_result = None
    try:
        context.raise_if_cancelled()
        if builtin is ConnectedComponents:
            metadata_items = [(item, dataset.read_metadata(**item)) for item in coordinates]
            if "position" in dataset.axes:
                # Lexical order numbers saved identity, never capture order.
                # design/73's warning about lexical P10/P2 applies to P, not T.
                for number, position in enumerate(sorted({item["position"] for item in coordinates}), 1):
                    label = {"label": f"T{number}", "position": position}
                    names = []
                    for item, metadata in metadata_items:
                        if item["position"] == position and "PositionName" in metadata:
                            if metadata["PositionName"] not in names:
                                names.append(metadata["PositionName"])
                    if len(names) == 1:
                        label["PositionName"] = names[0]
                    elif names:
                        label["PositionNames"] = names
                    instance.position_labels.append(label)
        if input_kind == "stage_coordinate_mosaic":
            from microclaw.tools import build_stage_coordinate_mosaic
            mosaic_path = str(Path(output_dir) / "stage_coordinate_mosaic.tiff")
            mosaic_result = build_stage_coordinate_mosaic(
                None, guard, dataset_path, mosaic_path, axis_selection,
                calibration_ref, output_pixel_size_um,
            )
            calibration_identity = mosaic_result["calibration_identity"]
            for path in (mosaic_path, mosaic_result["manifest_path"]):
                data = Path(path).read_bytes()
                artifacts.append({"path": path, "sha256": _sha(data),
                                  "size_bytes": len(data)})
            image = np.asarray(tifffile.imread(mosaic_path))
            metadata = {"input_kind": input_kind, "mosaic_manifest": mosaic_result}
            if verb == "analyze_saved_frame":
                raw_results = [instance.analyze_saved_frame(image, metadata, context)]
            else:
                raw_results = instance.analyze_completed_dataset(view, _immutable(selection), context)
        elif verb == "analyze_saved_frame":
            if builtin is ConnectedComponents:
                from microclaw.tools import _resolve_saved_dataset_calibration
                affine, calibration_identity, camera_identity, roi_difference = (
                    _resolve_saved_dataset_calibration(
                        dataset, metadata_items,
                        axis_selection, calibration_ref, guard=guard,
                    )
                )
                instance.affine = affine
                saved_calibration_details = {"dataset_identity": camera_identity,
                                             "calibration_roi_difference": roi_difference}
            raw_results = []
            for item in coordinates:
                context.raise_if_cancelled()
                metadata = view.read_metadata(**item)
                where = dict(item)
                if "PositionName" in metadata:
                    where["PositionName"] = metadata["PositionName"]
                if builtin is ConnectedComponents:
                    instance.field_label = next((entry["label"] for entry in instance.position_labels
                                                 if entry["position"] == item.get("position")), None)
                raw_results.append((where, instance.analyze_saved_frame(
                    view.read_image(**item), metadata, context
                )))
        else:
            raw_results = instance.analyze_completed_dataset(
                view, _immutable(selection), context
            )
        if raw_results is not None:
            for raw in raw_results:
                context.raise_if_cancelled()
                where = None
                if input_kind == "frames" and verb == "analyze_saved_frame":
                    where, raw = raw
                normalized = _normalized_result(raw)
                if normalized:
                    emit(**normalized, where=where)
    except AnalysisCancelled as error:
        status = "cancelled"
        failure = {"type": type(error).__name__, "message": str(error)}
    except Exception as error:
        status = "failed"
        failure = {"type": type(error).__name__, "message": str(error)}

    finished = datetime.now(timezone.utc)
    manifest_base = {
        "schema": "microclaw.completed-dataset-analysis/v1",
        "run_id": str(uuid.uuid4()), "status": status,
        "started_at": started.isoformat(), "finished_at": finished.isoformat(),
        "latency_s": time.monotonic() - clock, "dataset_path": dataset_path,
        **content_identity, "dataset_content_manifest": content_manifest,
        "selection": selection, "input_kind": input_kind,
        "analyzer": {"name": adapter, "source": entry.get("source"),
                     "source_sha256": _sha(source), "version": entry.get("version"),
                     "environment": {"microclaw": __version__, "python": sys.version.split()[0],
                                     "platform": platform.platform()}},
        "parameters": _json_value(parameters),
        "calibration_used": calibration_identity is not None,
        **saved_calibration_details,
        "cancelled": status == "cancelled", "failure": failure,
    }
    if builtin is ConnectedComponents:
        manifest_base["annotations"] = instance.annotations
        manifest_base["position_labels"] = instance.position_labels
        manifest_base["count_semantics"] = instance.count_semantics
    if calibration_identity is not None:
        manifest_base["calibration_identity"] = calibration_identity
    try:
        artifact_records = [
            {**item, "relative_path": Path(item["path"]).relative_to(output_dir).as_posix()}
            for item in artifacts
        ]
        scientific_payload = {
            "selection": selection, "observations": [
                {k: v for k, v in item.items() if k != "observed_at"}
                for item in observations
            ],
            "artifacts": [
                {k: item[k] for k in ("relative_path", "sha256", "size_bytes")}
                for item in artifact_records
            ],
        }
        manifest = {
            **manifest_base,
            "model_project_config": _optional_input_hashes(model_project_config, guard),
            "artifacts": artifact_records, "observations": observations,
            "scientific_payload": scientific_payload,
            "scientific_payload_sha256": _sha(_canonical_bytes(scientific_payload)),
        }
    except Exception as error:
        assembly_failure = {"type": type(error).__name__, "message": str(error)}
        manifest = {
            **manifest_base, "status": "failed", "cancelled": False,
            "failure": failure or assembly_failure,
            "manifest_assembly_failure": assembly_failure,
            "artifacts": [], "observations": observations,
        }
    manifest_path = Path(output_dir) / "analysis-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, allow_nan=False), encoding="utf-8")
    return {**manifest, "manifest_path": str(manifest_path),
            "mosaic": mosaic_result}
