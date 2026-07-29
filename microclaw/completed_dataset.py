"""Reviewed, reproducible analysis over an already-completed NDTiff dataset.

The runner is trusted parent code and may use a SafetyGuard for path policy.
The loaded adapter is untrusted and receives only the narrow facades below.
Source review and hash pinning are the gate until process isolation exists.
"""

from __future__ import annotations

import hashlib
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
)
from microclaw.hooks import write_analysis_observation


OFFLINE_VERBS = ("analyze_completed_dataset", "analyze_saved_frame")
_ALLOWED_CAPABILITIES = frozenset(
    {"coordinates", "read_image", "read_metadata", "as_array", "artifacts",
     "observations", "cancellation"}
)


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
        raise KeyError(f"No saved hook named {name!r}.")
    entry = manifest[name]
    source = Path(entry["path"]).read_bytes()
    if "sha256" not in entry:
        raise RuntimeError(
            f"Hook {name!r} predates hash-pinning; re-save it before running."
        )
    if _sha(source) != entry["sha256"]:
        raise RuntimeError(
            f"Hook {name!r} changed on disk since it was saved; refusing to load."
        )
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
        if isinstance(value, str) and Path(guard.resolve_readable_path(value)).is_file():
            resolved = guard.resolve_readable_path(value)
            data = Path(resolved).read_bytes()
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
    Path(output_dir).mkdir(parents=True, exist_ok=False)
    if input_kind == "stage_coordinate_mosaic":
        if calibration_ref is None:
            raise ValueError("stage_coordinate_mosaic requires an explicit calibration_ref")
        if calibration_ref.get("kind") == "confirmed_current":
            raise ValueError(
                "confirmed_current calibration requires a live microscope core and is "
                "not available to completed-dataset replay; use artifact or knowledge_version"
            )

    cls, verb, entry, source = _load_saved_adapter(adapter)
    forbidden = set(parameters) & set(FORBIDDEN_SAVED_HOOK_PARAMS)
    if forbidden:
        raise ValueError(f"Offline adapter parameters request forbidden capabilities: {sorted(forbidden)}")
    instance = cls(**parameters)
    dataset = Dataset(dataset_path)
    from microclaw.tools import _iter_present_coords
    coordinates = list(_iter_present_coords(dataset, axis_selection))
    if not coordinates:
        raise ValueError("No images exist in the selected dataset")
    selection = {"axis_selection": {k: axis_selection[k] for k in sorted(axis_selection)},
                 "coordinates": [{k: item[k] for k in sorted(item)} for item in coordinates]}
    dataset_hash, content_hash, content_manifest = _dataset_content(dataset_path)
    dataset_identity = {"dataset_sha256": dataset_hash,
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
             artifact_sha256=None):
        # Saved adapters are untrusted, exactly like saved live hooks.
        if status not in {"unverified", "provisional"}:
            raise ValueError(
                "Untrusted offline analysis status must be 'unverified' or 'provisional'; "
                f"{status!r} is not self-assertable."
            )
        return write_analysis_observation(
            observations, analyzer=analyzer or adapter,
            analyzer_version=analyzer_version or entry.get("version"), result=result,
            parameters=parameters, artifact_sha256=artifact_sha256, status=status,
        )

    context = AnalysisContext(dataset_identity, ArtifactDirectory(write_artifact),
                              emit, cancelled.is_set)
    view = DatasetView(dataset, coordinates, max_array_bytes=max_array_bytes)
    calibration_identity = None
    failure = None
    status = "completed"
    mosaic_result = None
    try:
        context.raise_if_cancelled()
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
            raw_results = []
            for item in coordinates:
                context.raise_if_cancelled()
                raw_results.append(instance.analyze_saved_frame(
                    view.read_image(**item), view.read_metadata(**item), context
                ))
        else:
            raw_results = instance.analyze_completed_dataset(
                view, _immutable(selection), context
            )
        if raw_results is not None:
            for raw in raw_results:
                context.raise_if_cancelled()
                normalized = _normalized_result(raw)
                if normalized:
                    emit(**normalized)
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
        **dataset_identity, "dataset_content_manifest": content_manifest,
        "selection": selection, "input_kind": input_kind,
        "analyzer": {"name": adapter, "source": entry.get("source"),
                     "source_sha256": _sha(source), "version": entry.get("version"),
                     "environment": {"microclaw": __version__, "python": sys.version.split()[0],
                                     "platform": platform.platform()}},
        "parameters": _json_value(parameters),
        "calibration_used": calibration_identity is not None,
        "cancelled": status == "cancelled", "failure": failure,
    }
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
