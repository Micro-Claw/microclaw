"""Phase-1 decision boundary for saved (untrusted) acquisition hooks.

Saved hook source still executes in the hardware-control process.  Source
review and hash pinning remain the containment story until Block 13 adds a
worker process; this module only removes direct hardware capabilities and
validates decisions in trusted parent code.
"""

from __future__ import annotations

import copy
from dataclasses import asdict, dataclass
import hashlib
import io
import json
import math
from pathlib import Path
from typing import Any

import numpy as np


@dataclass(frozen=True)
class MoveStage:
    kind: str = "MoveStage"
    x_um: float | None = None
    y_um: float | None = None
    z_um: float | None = None


@dataclass(frozen=True)
class AcquireAt:
    position: int | str
    kind: str = "AcquireAt"


@dataclass(frozen=True)
class SetExposure:
    exposure_ms: float
    kind: str = "SetExposure"


@dataclass(frozen=True)
class ContinueSurvey:
    kind: str = "ContinueSurvey"


@dataclass(frozen=True)
class StopSurvey:
    kind: str = "StopSurvey"


@dataclass(frozen=True)
class RequestAutofocus:
    kind: str = "RequestAutofocus"


@dataclass(frozen=True)
class SetIlluminationPower:
    """Propose power modulation; shutters/on-values are not expressible."""
    value_percent: float
    kind: str = "SetIlluminationPower"


@dataclass(frozen=True)
class EmitArtifact:
    """Propose an in-memory artifact with a parent-confined bare filename."""
    filename: str
    payload: bytes | np.ndarray
    kind: str = "EmitArtifact"


@dataclass(frozen=True)
class DiscardFrame:
    """Discard saved pixels only; the position was moved to and exposed."""
    kind: str = "DiscardFrame"


HookAction = (
    MoveStage | AcquireAt | SetExposure | ContinueSurvey | StopSurvey |
    RequestAutofocus | SetIlluminationPower | EmitArtifact | DiscardFrame
)


@dataclass(frozen=True)
class HookResult:
    measurements: dict[str, Any]
    actions: (
        tuple[HookAction | dict[str, Any], ...] |
        list[HookAction | dict[str, Any]]
    ) = ()
    analyzer: str | None = None
    analyzer_version: str | None = None
    parameters: dict[str, Any] | None = None
    artifact_sha256: str | None = None
    status: str = "unverified"

    def __post_init__(self) -> None:
        if not isinstance(self.measurements, dict):
            raise TypeError("HookResult.measurements must be a dict.")
        json.dumps(self.measurements, allow_nan=False)
        if not isinstance(self.actions, (list, tuple)):
            raise TypeError("HookResult.actions must be a list or tuple.")
        object.__setattr__(self, "actions", tuple(self.actions))
        if self.status not in {"unverified", "provisional"}:
            raise ValueError(
                "Untrusted HookResult.status must be 'unverified' or 'provisional'; "
                f"{self.status!r} is not self-assertable."
            )
        if self.parameters is not None and not isinstance(self.parameters, dict):
            raise TypeError("HookResult.parameters must be a dict or None.")
        for name, value in (("analyzer", self.analyzer),
                            ("analyzer_version", self.analyzer_version),
                            ("artifact_sha256", self.artifact_sha256)):
            if value is not None and not isinstance(value, str):
                raise TypeError(f"HookResult.{name} must be a string or None.")
        json.dumps(self.parameters or {}, allow_nan=False)


_ACTION_TYPES = {
    cls.__dataclass_fields__["kind"].default: cls
    for cls in (MoveStage, AcquireAt, SetExposure, ContinueSurvey, StopSurvey,
                RequestAutofocus, SetIlluminationPower, EmitArtifact, DiscardFrame)
}


def parse_action(value: HookAction | dict[str, Any]) -> HookAction:
    """Convert one proposal through the closed discriminated union."""
    if isinstance(value, tuple(_ACTION_TYPES.values())):
        payload = {f: getattr(value, f) for f in value.__dataclass_fields__}
    elif isinstance(value, dict):
        payload = dict(value)
    else:
        raise TypeError(f"Hook action must be a typed action or dict, got {type(value).__name__}.")
    kind = payload.get("kind")
    cls = _ACTION_TYPES.get(kind)
    if cls is None:
        raise ValueError(f"Unknown hook action kind {kind!r}.")
    allowed = set(cls.__dataclass_fields__)
    extra = set(payload) - allowed
    if extra:
        raise ValueError(f"Malformed {kind} action: unexpected fields {sorted(extra)}.")
    try:
        action = cls(**payload)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Malformed {kind} action: {exc}") from exc
    # JSON validation rejects NaN/infinity and non-portable scalar objects.
    try:
        if not isinstance(action, EmitArtifact):
            json.dumps(asdict(action), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Malformed {kind} action: {exc}") from exc
    if isinstance(action, AcquireAt) and (
        isinstance(action.position, bool) or not isinstance(action.position, (int, str))
    ):
        raise ValueError("Malformed AcquireAt action: position must be an index or label.")
    if isinstance(action, AcquireAt) and isinstance(action.position, str) and not action.position:
        raise ValueError("Malformed AcquireAt action: position label must not be empty.")
    numeric: tuple[tuple[str, Any], ...] = ()
    if isinstance(action, MoveStage):
        if action.x_um is action.y_um is action.z_um is None:
            raise ValueError("Malformed MoveStage action: at least one axis is required.")
        numeric = (("x_um", action.x_um), ("y_um", action.y_um), ("z_um", action.z_um))
    elif isinstance(action, SetExposure):
        numeric = (("exposure_ms", action.exposure_ms),)
    elif isinstance(action, SetIlluminationPower):
        numeric = (("value_percent", action.value_percent),)
    for name, number in numeric:
        if number is None:
            continue
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number):
            raise ValueError(f"Malformed {kind} action: {name} must be a finite number.")
    if isinstance(action, SetExposure) and action.exposure_ms <= 0:
        raise ValueError("Malformed SetExposure action: exposure_ms must be positive.")
    if isinstance(action, SetIlluminationPower) and action.value_percent < 0:
        raise ValueError("Malformed SetIlluminationPower action: value_percent must be non-negative.")
    if isinstance(action, EmitArtifact):
        if not isinstance(action.filename, str):
            raise ValueError("Malformed EmitArtifact action: filename must be a string.")
        if not isinstance(action.payload, (bytes, np.ndarray)):
            raise ValueError("Malformed EmitArtifact action: payload must be bytes or ndarray.")
    return action


def write_hook_artifact(target_dir: str | Path, filename: str, payload: bytes | np.ndarray,
                        *, max_artifact_bytes: int, max_count: int,
                        max_total_bytes: int, state: dict[str, int]) -> dict[str, Any]:
    """Write one bounded payload below *target_dir*, without an acquisition handle."""
    if (not filename or Path(filename).name != filename or "/" in filename or
            "\\" in filename or filename in {".", ".."}):
        raise ValueError("artifact filename must be a non-empty bare filename")
    target = Path(target_dir)
    path = target / filename
    if path.exists():
        raise ValueError("artifact filename collides with an existing artifact")
    if state.get("count", 0) >= max_count:
        raise ValueError("artifact count limit exhausted")
    if isinstance(payload, bytes):
        data = payload
    else:
        stream = io.BytesIO()
        if filename.lower().endswith((".tif", ".tiff")):
            import tifffile
            tifffile.imwrite(stream, payload)
        else:
            np.save(stream, payload)
        data = stream.getvalue()
    if len(data) > max_artifact_bytes:
        raise ValueError(
            f"artifact size {len(data)} bytes exceeds per-artifact size limit "
            f"{max_artifact_bytes} bytes"
        )
    if state.get("total_bytes", 0) + len(data) > max_total_bytes:
        raise ValueError("artifact exceeds per-run total-bytes limit")
    target.mkdir(parents=True, exist_ok=True)
    # Exclusive creation also closes the collision race.
    with path.open("xb") as handle:
        handle.write(data)
    state["count"] = state.get("count", 0) + 1
    state["total_bytes"] = state.get("total_bytes", 0) + len(data)
    return {"path": str(path), "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data)}


class DeniedEventQueue:
    """Loud replacement for pycro-manager's hardware event queue."""

    _MESSAGE = (
        "Saved hooks cannot access pycro-manager's event queue; return typed "
        "actions from analyze_frame instead."
    )

    def __getattribute__(self, _name):
        if _name in {"_MESSAGE", "__class__"}:
            return object.__getattribute__(self, _name)
        raise RuntimeError(object.__getattribute__(self, "_MESSAGE"))

    def __call__(self, *_args, **_kwargs):
        raise RuntimeError(self._MESSAGE)


class UntrustedHookAdapter:
    """Trusted parent for a hash-pinned saved hook.

    The wrapped class is untrusted by provenance regardless of its manifest
    ``source`` label.  It receives pixels and metadata only.  This is not an
    execution sandbox: source review and hash pinning remain the containment
    boundary until Block 13.
    """

    provenance = "saved_untrusted"

    def __init__(self, hook: Any, log_path: str | None = None) -> None:
        self.hook = hook
        self.log_path = log_path
        self._log: list[dict[str, Any]] = []
        self._context: dict[str, Any] | None = None
        self._illumination_context: dict[str, Any] | None = None
        self._artifact_context: dict[str, Any] | None = None

    @property
    def proposes_actions(self) -> bool:
        """Whether the wrapped hook can return typed actions at all.

        A legacy ``image_process_fn``-only hook cannot: it has no channel for a
        proposal now that the runner state is parent-side. Callers that need a
        hook to steer an acquisition check this before starting one.
        """
        return hasattr(self.hook, "analyze_frame")

    def configure_adaptive(self, *, events, candidates, progress, guard,
                           max_events: int) -> None:
        self._context = {
            "events": list(events), "candidates": candidates, "progress": progress,
            "guard": guard, "max_events": max_events, "emitted": 1, "cursor": 1,
        }

    def configure_illumination(self, *, core, guard, device: str, property: str,
                               max_power_percent: float, max_writes: int,
                               initial_value: float) -> None:
        self._illumination_context = {
            "core": core, "guard": guard, "device": device, "property": property,
            "ceiling": max_power_percent, "remaining": max_writes,
            "last_written": initial_value, "baseline_stale": False,
        }

    def configure_artifacts(self, *, target_dir: str | Path,
                            max_artifact_bytes: int, max_count: int,
                            max_total_bytes: int) -> None:
        self._artifact_context = {
            "target_dir": target_dir, "max_artifact_bytes": max_artifact_bytes,
            "max_count": max_count, "max_total_bytes": max_total_bytes,
            "state": {"count": 0, "total_bytes": 0},
        }

    def bind_artifact_directory(self, target_dir: str | Path) -> None:
        """Bind an authorized artifact budget to its acquisition's real path.

        Acquisition naming collisions are resolved only when pycro-manager
        constructs the acquisition.  Change only the destination here: limits
        and already-consumed per-run state remain owned by the same context.
        """
        if self._artifact_context is not None:
            self._artifact_context["target_dir"] = target_dir

    def _write_log(self) -> None:
        if self.log_path:
            # Only this trusted adapter receives the acquisition's audit path.
            from pathlib import Path
            Path(self.log_path).write_text(
                json.dumps(self._log, indent=2, allow_nan=False), encoding="utf-8"
            )

    def _record(self, metadata: dict, **fields: Any) -> None:
        from microclaw.hooks import HookBase
        self._log.append({**HookBase.where(metadata), **fields})
        self._write_log()

    def note_stalled(self, max_idle_s: float) -> None:
        self._record({}, event="stalled", max_idle_s=max_idle_s)

    def note_aborted(self) -> None:
        self._record({}, event="aborted")

    def note_budget_exhausted(self, max_events: int, *, overrun_frames: int = 0) -> None:
        self._record({}, event="budget_exhausted", max_events=max_events,
                     overrun_frames=overrun_frames)

    @staticmethod
    def _event_xy(event: dict) -> tuple[float, float]:
        return float(event["x"]), float(event["y"])

    @staticmethod
    def _action_record(action: HookAction) -> dict[str, Any]:
        if isinstance(action, EmitArtifact):
            return {"kind": action.kind, "filename": action.filename,
                    "payload_type": type(action.payload).__name__}
        return asdict(action)

    def _refuse(self, metadata: dict, action: HookAction, reason: str) -> None:
        self._record(metadata, event="hook_action", action=self._action_record(action),
                     decision="refused", reason=reason)

    def _accept(self, metadata: dict, action: HookAction, reason: str,
                **fields: Any) -> None:
        self._record(metadata, event="hook_action", action=self._action_record(action),
                     decision="accepted", reason=reason, **fields)

    def _dispatch(self, action: HookAction, metadata: dict) -> str | None:
        if isinstance(action, DiscardFrame):
            self._accept(metadata, action, "frame pixels discarded after exposure")
            return None
        if isinstance(action, EmitArtifact):
            ctx = self._artifact_context
            if ctx is None:
                self._refuse(metadata, action, "no artifact budget was authorized for this run")
                return None
            # Only a CompositeHook installs this per-frame counter. A standalone
            # adapter already enforces HookResult's one-artifact rule per call.
            if ("frame_artifacts" in ctx["state"] and
                    ctx["state"]["frame_artifacts"] >= 1):
                self._refuse(
                    metadata, action,
                    "another hook already emitted the run's one artifact for this frame",
                )
                return None
            try:
                info = write_hook_artifact(**ctx, filename=action.filename,
                                           payload=action.payload)
            except (OSError, ValueError) as exc:
                self._refuse(metadata, action, str(exc))
                return None
            if "frame_artifacts" in ctx["state"]:
                ctx["state"]["frame_artifacts"] += 1
            self._accept(metadata, action, "parent wrote bounded artifact", **info)
            return info["sha256"]
        if isinstance(action, SetIlluminationPower):
            ctx = self._illumination_context
            if ctx is None:
                self._refuse(metadata, action, "no illumination envelope was authorized for this run")
                return None
            new = float(action.value_percent)
            if ctx["baseline_stale"]:
                try:
                    raw = ctx["core"].get_property(ctx["device"], ctx["property"])
                    current = ctx["guard"].illumination_to_percent(
                        ctx["device"], ctx["property"], raw
                    )
                    if not math.isfinite(current):
                        raise ValueError(f"non-finite value {raw!r}")
                except Exception as exc:
                    self._refuse(
                        metadata, action,
                        f"illumination baseline could not be re-established: {exc}",
                    )
                    return None
                ctx["last_written"] = current
                ctx["baseline_stale"] = False
                self._record(
                    metadata, event="illumination_baseline_reread",
                    decision="succeeded", value_percent=current,
                    baseline_stale=False,
                )
            if new > ctx["ceiling"]:
                self._refuse(metadata, action, "proposal exceeds authorized envelope ceiling")
                return None
            old = ctx["last_written"]
            increasing = new > old
            if increasing and ctx["remaining"] <= 0:
                self._refuse(metadata, action, "authorized illumination write budget exhausted")
                return None
            try:
                raw_new = ctx["guard"].illumination_from_percent(
                    ctx["device"], ctx["property"], new
                )
                ctx["guard"].check_illumination(
                    ctx["core"], ctx["device"], ctx["property"], str(raw_new),
                    confirm_fn=None, previous_percent=old,
                )
            except Exception as exc:
                self._refuse(metadata, action, f"SafetyGuard refused illumination: {exc}")
                return None
            try:
                ctx["core"].set_property(ctx["device"], ctx["property"], str(raw_new))
            except Exception as exc:
                ctx["baseline_stale"] = True
                self._record(
                    metadata, event="illumination_write_failure",
                    action=self._action_record(action), decision="failed",
                    reason=f"parent device write failed: {exc}",
                    baseline_stale=True,
                )
                return None
            ctx["last_written"] = new
            if increasing:
                ctx["remaining"] -= 1
            self._accept(metadata, action, "power write passed envelope and SafetyGuard")
            return None
        ctx = self._context
        if ctx is None:
            if isinstance(action, ContinueSurvey):
                self._accept(
                    metadata, action,
                    "noop: this runner already continues through its fixed event plan",
                )
            else:
                self._refuse(metadata, action, "unsupported-by-this-runner")
            return
        if isinstance(action, (MoveStage, SetExposure, RequestAutofocus)):
            self._refuse(metadata, action, "unsupported-by-run_adaptive_survey")
            return
        if isinstance(action, StopSurvey):
            ctx["progress"].done_early()
            self._accept(metadata, action, "survey stopped before another tile was dispatched")
            return
        if ctx["emitted"] >= ctx["max_events"]:
            self._refuse(
                metadata, action,
                "outside committed reservation: all planned frame slots are already dispatched",
            )
            return
        events = ctx["events"]
        if isinstance(action, ContinueSurvey):
            if ctx["cursor"] >= len(events):
                self._refuse(metadata, action, "planned survey cursor is already at the end")
                return
            event = events[ctx["cursor"]]
            ctx["cursor"] += 1
        else:
            assert isinstance(action, AcquireAt)
            if isinstance(action.position, int):
                if action.position < 0 or action.position >= len(events):
                    self._refuse(metadata, action, "position is not in the planned survey")
                    return
                event = events[action.position]
            else:
                matches = [e for e in events if
                           (e.get("axes") or {}).get("position") == action.position]
                if not matches:
                    # Absent and ambiguous are different operator mistakes and
                    # must not share a reason string: the refusal record is the
                    # only account of why a tile was not acquired.
                    self._refuse(metadata, action,
                                 "no planned survey position is labelled "
                                 f"{action.position!r}")
                    return
                if len(matches) > 1:
                    self._refuse(metadata, action,
                                 f"label {action.position!r} matches {len(matches)} "
                                 "planned positions and is not a unique target")
                    return
                event = matches[0]
        try:
            x, y = self._event_xy(event)
            ctx["guard"].check_xy(x, y)
            if event.get("z") is not None:
                ctx["guard"].check_z(event["z"])
        except Exception as exc:
            self._refuse(metadata, action, f"SafetyGuard refused planned event: {exc}")
            return
        ctx["candidates"].put(event)
        ctx["emitted"] += 1
        self._accept(metadata, action, "planned event passed guard and committed reservation")

    def image_process_fn(self, image, metadata, _hardware_event_queue):
        try:
            if hasattr(self.hook, "analyze_frame"):
                raw = self.hook.analyze_frame(image, metadata)
                if raw is None:
                    result = HookResult({})
                elif isinstance(raw, HookResult):
                    result = raw
                else:
                    raise TypeError("analyze_frame must return HookResult or None.")
                # Parse the complete proposal before dispatching any part of it.
                try:
                    actions = tuple(parse_action(a) for a in result.actions)
                except (TypeError, ValueError) as exc:
                    # The frame is already exposed and may already be on disk. A
                    # malformed proposal must not turn an analysis defect into an
                    # acquisition abort that hides the dataset from the caller.
                    self._record(metadata, event="hook_action", decision="refused",
                                 reason=str(exc))
                    if self._context is not None:
                        self._context["progress"].image_done()
                    return image, metadata
                if sum(isinstance(a, EmitArtifact) for a in actions) > 1:
                    raise ValueError(
                        "HookResult may propose at most one EmitArtifact per frame."
                    )
                from microclaw.hooks import analysis_observation_record
                observation = analysis_observation_record(
                    analyzer=result.analyzer,
                    analyzer_version=result.analyzer_version,
                    result=result.measurements,
                    parameters=result.parameters,
                    artifact_sha256=result.artifact_sha256,
                    status=result.status,
                )
                # Same where + envelope shape as HookBase.log_analysis. Action
                # decisions remain separate parent-owned records below.
                self._record(metadata, **observation)
                observation_index = len(self._log) - 1
                discard = False
                for action in actions:
                    artifact_hash = self._dispatch(action, metadata)
                    if artifact_hash:
                        self._log[observation_index]["artifact_sha256"] = artifact_hash
                        self._write_log()
                    discard = discard or isinstance(action, DiscardFrame)
                returned = None if discard else (image, metadata)
                if discard:
                    self._record(metadata, event="legacy_hook_frame", outcome="discarded")
            else:
                returned = self.hook.image_process_fn(
                    image, metadata, DeniedEventQueue()
                )
                self._record(
                    metadata, event="legacy_hook_frame",
                    outcome="discarded" if returned is None else "retained",
                )
            if self._context is not None:
                self._context["progress"].image_done()
            return returned
        except Exception as exc:
            self._record(metadata, event="hook_failure", reason=str(exc))
            raise


class CompositeHook:
    """Compose independently-resolved hooks without combining their authority.

    Each image observer receives its own pixel copy of the original frame.  A
    discard by any observer wins.  Child audit records are merged into one
    run log and attributed by strategy name.
    """

    def __init__(self, named_hooks: list[tuple[str, Any]], log_path: str | None) -> None:
        self.named_hooks = named_hooks
        self.log_path = log_path
        self._log: list[dict[str, Any]] = []
        self._seen = [0] * len(named_hooks)
        self._artifact_state: dict[str, int] | None = None

    def _child_log(self, hook: Any) -> list[dict[str, Any]]:
        if isinstance(hook, UntrustedHookAdapter):
            return hook._log
        if hasattr(hook, "get_summary"):
            return hook.get_summary()
        return []

    def _sync(self, index: int) -> None:
        name, hook = self.named_hooks[index]
        records = self._child_log(hook)
        for record in records[self._seen[index]:]:
            self._log.append({"hook_strategy": name, **record})
        self._seen[index] = len(records)
        if self.log_path:
            Path(self.log_path).write_text(
                json.dumps(self._log, indent=2, allow_nan=False), encoding="utf-8"
            )

    def post_hardware_hook_fn(self, event: dict) -> dict:
        current = event
        for index, (_name, hook) in enumerate(self.named_hooks):
            callback = getattr(hook, "post_hardware_hook_fn", None)
            if callback is None:
                continue
            try:
                current = callback(current)
            finally:
                self._sync(index)
            if current is None:
                raise RuntimeError(
                    f"post_hardware_hook_fn for hook {_name!r} returned None; "
                    "every post-hardware hook must return the event"
                )
        return current

    def image_process_fn(self, image, metadata, event_queue):
        if self._artifact_state is not None:
            self._artifact_state["frame_artifacts"] = 0
        discard = False
        for index, (_name, hook) in enumerate(self.named_hooks):
            callback = getattr(hook, "image_process_fn", None)
            if callback is None:
                continue
            observer_image = np.array(image, copy=True)
            # deepcopy, not dict(): metadata is nested (metadata["Axes"] is a
            # dict), so a shallow copy leaves the nesting shared. A hook writing
            # metadata["Axes"]["position"] would then rewrite the next hook's
            # view *and* the parent's, whose log attribution reads the same key
            # through HookBase.where. Pixels were already isolated; this closes
            # the other half.
            try:
                returned = callback(
                    observer_image, copy.deepcopy(metadata), event_queue
                )
            finally:
                self._sync(index)
            discard = discard or returned is None
        return None if discard else (image, metadata)

    def configure_artifacts(self, *, target_dir: str | Path, **limits: int) -> None:
        adapters = [h for _n, h in self.named_hooks
                    if isinstance(h, UntrustedHookAdapter)]
        state = {"count": 0, "total_bytes": 0, "frame_artifacts": 0}
        for hook in adapters:
            hook.configure_artifacts(target_dir=target_dir, **limits)
            hook._artifact_context["state"] = state
        self._artifact_state = state

    def bind_artifact_directory(self, target_dir: str | Path) -> None:
        for _name, hook in self.named_hooks:
            if hasattr(hook, "bind_artifact_directory"):
                hook.bind_artifact_directory(target_dir)

    def bind_reservation(self, reservation) -> None:
        for _name, hook in self.named_hooks:
            if hasattr(hook, "bind_reservation"):
                hook.bind_reservation(reservation)

    def planned_extra_exposures_per_event(self) -> int:
        return sum(
            getattr(hook, "planned_extra_exposures_per_event", lambda: 0)()
            for _name, hook in self.named_hooks
        )
