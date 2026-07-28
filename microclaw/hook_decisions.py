"""Phase-1 decision boundary for saved (untrusted) acquisition hooks.

Saved hook source still executes in the hardware-control process.  Source
review and hash pinning remain the containment story until Block 13 adds a
worker process; this module only removes direct hardware capabilities and
validates decisions in trusted parent code.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
from typing import Any


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


HookAction = (
    MoveStage | AcquireAt | SetExposure | ContinueSurvey | StopSurvey |
    RequestAutofocus
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
                RequestAutofocus)
}


def parse_action(value: HookAction | dict[str, Any]) -> HookAction:
    """Convert one proposal through the closed discriminated union."""
    if isinstance(value, tuple(_ACTION_TYPES.values())):
        payload = asdict(value)
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
    for name, number in numeric:
        if number is None:
            continue
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number):
            raise ValueError(f"Malformed {kind} action: {name} must be a finite number.")
    if isinstance(action, SetExposure) and action.exposure_ms <= 0:
        raise ValueError("Malformed SetExposure action: exposure_ms must be positive.")
    return action


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

    def _refuse(self, metadata: dict, action: HookAction, reason: str) -> None:
        self._record(metadata, event="hook_action", action=asdict(action),
                     decision="refused", reason=reason)

    def _accept(self, metadata: dict, action: HookAction, reason: str) -> None:
        self._record(metadata, event="hook_action", action=asdict(action),
                     decision="accepted", reason=reason)

    def _dispatch(self, action: HookAction, metadata: dict) -> None:
        ctx = self._context
        if ctx is None:
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
                actions = tuple(parse_action(a) for a in result.actions)
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
                for action in actions:
                    self._dispatch(action, metadata)
                returned = (image, metadata)
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
