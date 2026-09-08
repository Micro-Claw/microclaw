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

from microclaw.controller import (
    StageMoveError, read_stage_start_position, settle_stage_move,
    stage_move_dispatch_failure,
)

#: Payload for a run whose hardware program is fully specified by
#: hook_action_plan. It analyses nothing; the adapter's image_process_fn
#: already no-ops on a payload with no analyze_frame.
PLAN_ONLY = object()


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
class ContinueAcquisition:
    kind: str = "ContinueAcquisition"


@dataclass(frozen=True)
class StopAcquisition:
    kind: str = "StopAcquisition"


_RETIRED_ACTION_NAMES = {
    "ContinueSurvey": "ContinueAcquisition",
    "StopSurvey": "StopAcquisition",
}


@dataclass(frozen=True)
class RequestAutofocus:
    kind: str = "RequestAutofocus"


@dataclass(frozen=True)
class SetIlluminationPower:
    """Propose power modulation; shutters/on-values are not expressible."""
    value_percent: float
    kind: str = "SetIlluminationPower"


@dataclass(frozen=True)
class MoveNamedStage:
    """Propose a position for the single named-stage envelope on this run."""
    position_um: float
    kind: str = "MoveNamedStage"


@dataclass(frozen=True)
class SetDeviceProperty:
    """Propose a value for the single property envelope on this run."""
    value: str
    kind: str = "SetDeviceProperty"


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
    MoveStage | AcquireAt | SetExposure | ContinueAcquisition | StopAcquisition |
    RequestAutofocus | SetIlluminationPower | MoveNamedStage | SetDeviceProperty | EmitArtifact |
    DiscardFrame
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
    for cls in (MoveStage, AcquireAt, SetExposure, ContinueAcquisition, StopAcquisition,
                RequestAutofocus, SetIlluminationPower, MoveNamedStage, SetDeviceProperty,
                EmitArtifact, DiscardFrame)
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
        accepted = ", ".join(sorted(_ACTION_TYPES))
        raise ValueError(
            "Unknown hook action. Actions require a 'kind' discriminator with one of: "
            f"{accepted}. Hardware actions have exactly "
            "{'kind': 'MoveNamedStage', 'position_um': <finite number>} or "
            "{'kind': 'SetDeviceProperty', 'value': <string>}."
        )
    allowed = set(cls.__dataclass_fields__)
    extra = set(payload) - allowed
    if extra:
        raise ValueError(
            f"Malformed {kind} action: unexpected fields {sorted(extra)}; "
            f"expected exactly {sorted(allowed)}."
        )
    try:
        action = cls(**payload)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Malformed {kind} action: {exc}") from exc
    if isinstance(action, MoveNamedStage) and (
        isinstance(action.position_um, bool)
        or not isinstance(action.position_um, (int, float))
        or not math.isfinite(action.position_um)
    ):
        raise ValueError(
            "Malformed MoveNamedStage action: position_um must be a finite number."
        )
    if isinstance(action, SetDeviceProperty) and not isinstance(action.value, str):
        raise ValueError("Malformed SetDeviceProperty action: value must be a string.")
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
    elif isinstance(action, MoveNamedStage):
        numeric = (("position_um", action.position_um),)
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
        self.can_emit_artifacts = bool(getattr(hook, "can_emit_artifacts", False))
        self.log_path = log_path
        self._log: list[dict[str, Any]] = []
        self._context: dict[str, Any] | None = None
        self._illumination_context: dict[str, Any] | None = None
        self._named_stage_context: dict[str, Any] | None = None
        self._property_context: dict[str, Any] | None = None
        self._fixed_plan_context: dict[str, Any] | None = None
        self._artifact_context: dict[str, Any] | None = None
        self._autofocus_context: dict[str, Any] | None = None
        self._action_counts: dict[str, int] = {}

    @property
    def action_counts(self) -> dict[str, int]:
        """Typed actions observed at the trusted parent dispatch boundary."""
        return dict(self._action_counts)

    @property
    def proposes_actions(self) -> bool:
        """Whether the wrapped hook can return typed actions at all.

        A legacy ``image_process_fn``-only hook cannot: it has no channel for a
        proposal now that the runner state is parent-side. Callers that need a
        hook to steer an acquisition check this before starting one.
        """
        return hasattr(self.hook, "analyze_frame")

    def configure_adaptive(self, *, events, candidates, progress, guard,
                           max_events: int, acquire_hits=None, max_hits=None,
                           read_z=None, successor=None,
                           require_routing_decision: bool = False) -> None:
        if self._autofocus_context is not None:
            # Make the refocus axis DENSE before the plan is dispatched. NDTiff
            # keys each frame by its exact axis set, so a second look carrying
            # refocus=1 against first looks carrying no such key leaves
            # dataset.axes["refocus"] == [1]: every reader that enumerates the
            # Cartesian product of the axes -- export_dataset_as_tiff does
            # exactly this -- then generates only refocus=1 combinations and
            # silently drops every first look. Measured on M5 2026-08-11, where
            # a 4-frame dataset exported as 1 real frame and 2 zeros.
            #
            # Stamped here, on the shared event dicts, because this is the one
            # call both the live runner and the emitted script make with the
            # same list the event stream will yield. Requires configure_autofocus
            # first; both callers do that, and an unauthorized run must keep its
            # current axes exactly, so there is nothing to stamp without it.
            for event in events:
                event.setdefault("axes", {}).setdefault("refocus", 0)
        self._context = {
            "events": list(events), "candidates": candidates, "progress": progress,
            "guard": guard, "max_events": max_events, "emitted": 1, "cursor": 1,
            "successor": successor,
            "require_routing_decision": require_routing_decision,
            "closed": False,
        }
        if not events:
            raise ValueError("an adaptive survey requires a seed event")
        # Only hardware-envelope runs need the axes-keyed coordinator. Without
        # one, revisits remain ordinary adaptive candidates as they were before
        # hardware actions existed.
        if self._named_stage_context is not None or self._property_context is not None:
            if self._fixed_plan_context is None:
                self._fixed_plan_context = {
                    "plan": {}, "consumed": set(), "adaptive": True, "closed": False,
                }
            coordinator = self._fixed_plan_context
            if not coordinator.get("adaptive") and not coordinator["plan"]:
                coordinator.update(adaptive=True, closed=False)
            if not coordinator.get("adaptive"):
                raise ValueError("adaptive and fixed hardware coordinators cannot be combined")
            # The seed has no preceding analysis. Its empty set is explicit so
            # a missing registration can never pass through before exposure.
            signature = self.axes_signature(events[0])
            coordinator["plan"][signature] = (0, ())
        if acquire_hits is not None:
            if successor is not None:
                raise ValueError("acquire_hits cannot be combined with a successor route")
            self._context.update(
                acquire_hits=acquire_hits, max_hits=max_hits, read_z=read_z,
            )

    def configure_autofocus(self, *, ctrl, guard, max_exposures: int,
                            z_range_um: float, z_step_um: float, method: str,
                            settle_ms: int, sweep_exposures: int,
                            focus_lock_check=None) -> None:
        """Authorize bounded autofocus proposals for an adaptive survey."""
        self._autofocus_context = {
            "ctrl": ctrl, "guard": guard, "remaining": max_exposures,
            "z_range_um": z_range_um, "z_step_um": z_step_um,
            "method": method, "settle_ms": settle_ms,
            "sweep_exposures": sweep_exposures,
            "focus_lock_check": focus_lock_check, "refocused_tiles": set(),
            "second_look_tiles": set(),
        }

    def planned_extra_exposures(self) -> int:
        """Worst-case sweep and re-exposure dose authorized for this run."""
        if self._autofocus_context is None:
            return 0
        return self._autofocus_context["remaining"]

    def planned_refocus_reexposures(self) -> int:
        if self._autofocus_context is None:
            return 0
        cost = self._autofocus_context["sweep_exposures"] + 1
        return self._autofocus_context["remaining"] // cost

    def configure_illumination(self, *, core, guard, device: str, property: str,
                               max_power_percent: float, max_writes: int,
                               initial_value: float) -> None:
        self._illumination_context = {
            "core": core, "guard": guard, "device": device, "property": property,
            "ceiling": max_power_percent, "remaining": max_writes,
            "last_written": initial_value, "baseline_stale": False,
        }

    def configure_named_stage(self, *, core, guard, device: str, min_um: float,
                              max_um: float, max_writes: int, initial_value: float,
                              restore: str | dict[str, float],
                              action_plan: dict[tuple, tuple[int, tuple[HookAction, ...]]] | None) -> None:
        plan = dict(action_plan or {})
        if self._fixed_plan_context is None:
            self._fixed_plan_context = {"plan": plan, "consumed": set(),
                                        "adaptive": action_plan is None, "closed": False}
        elif self._fixed_plan_context["plan"] != plan:
            raise ValueError("fixed hardware capabilities received different action plans")
        self._named_stage_context = {
            "core": core, "guard": guard, "device": device,
            "min_um": min_um, "max_um": max_um, "remaining": max_writes,
            "initial_value": initial_value, "last_known": initial_value,
            "restore": restore,
        }

    def configure_property(self, *, ctrl, guard, device: str, property: str,
                           allowed_values: tuple[str, ...] | None,
                           min_value: float | None, max_value: float | None,
                           max_writes: int, initial_value: str,
                           restore: str | dict[str, str],
                           action_plan: dict[tuple, tuple[int, tuple[HookAction, ...]]] | None) -> None:
        plan = dict(action_plan or {})
        if self._fixed_plan_context is None:
            self._fixed_plan_context = {"plan": plan, "consumed": set(),
                                        "adaptive": action_plan is None, "closed": False}
        elif self._fixed_plan_context["plan"] != plan:
            raise ValueError("fixed hardware capabilities received different action plans")
        self._property_context = {
            "ctrl": ctrl, "core": ctrl.core, "guard": guard, "device": device,
            "property": property, "allowed_values": allowed_values,
            "min": min_value, "max": max_value, "remaining": max_writes,
            "initial_value": initial_value, "last_known": initial_value,
            "restore": restore,
        }

    def _apply_property(self, action: SetDeviceProperty, event: dict,
                        *, restoration: bool = False,
                        hook_event_index: int | None = None) -> tuple[SetDeviceProperty, dict, int | None, dict]:
        """Validate, write and settle before exposure; verification follows.

        GUI controls may lag until the single teardown refresh. Timing follows
        this write through the separate verification pass in the same clock.
        """
        from microclaw.authorization import authorize_property_write
        from microclaw.safety import _finite_number_text
        import time
        ctx = self._property_context
        # Absolute seconds from one monotonic clock; only attempted phases are
        # present. This travels with the budget-bounded write, never per frame.
        timing = {"clock": "time.monotonic", "validation": {"start_s": time.monotonic()}}
        def refuse(reason):
            timing["validation"]["end_s"] = time.monotonic()
            self._record_event(
                event, event="hook_action", action=self._action_record(action),
                decision="refused", reason=reason, timing=timing,
                hook_event_index=hook_event_index, restoration=restoration,
            )
        if ctx is None:
            refuse("no property envelope was authorized for this run")
            raise RuntimeError("property action refused: no authorized envelope")
        value = action.value
        restoring_entry = restoration and value == ctx["initial_value"]
        if not restoring_entry and ctx["allowed_values"] is not None:
            if value not in ctx["allowed_values"]:
                refuse("proposal is outside the authorized property values")
                raise RuntimeError("property action refused: outside authorized values")
        elif not restoring_entry:
            try:
                number = _finite_number_text(value, "SetDeviceProperty.value")
            except Exception as exc:
                refuse(f"malformed numeric property value: {exc}")
                raise RuntimeError(f"property action refused: {exc}") from exc
            if number < ctx["min"] or number > ctx["max"]:
                refuse("proposal is outside the authorized property interval")
                raise RuntimeError("property action refused: outside authorized interval")
        if not restoration and ctx["remaining"] <= 0:
            refuse("authorized property write budget exhausted")
            raise RuntimeError("property action refused: write budget exhausted")
        try:
            authorize_property_write(
                ctx["ctrl"], ctx["device"], ctx["property"],
                approved_envelope=True,
            )
            guard_kwargs = {"approved_envelope": True}
            if restoring_entry:
                guard_kwargs["restoration_entry"] = True
            ctx["guard"].check_device_property(
                ctx["core"], ctx["device"], ctx["property"], value,
                **guard_kwargs,
            )
            ctx["guard"].check_illumination(
                ctx["core"], ctx["device"], ctx["property"], value,
                confirm_fn=None,
            )
        except Exception as exc:
            refuse(f"property write refused: {exc}")
            raise RuntimeError(f"property action refused: {exc}") from exc
        timing["validation"]["end_s"] = time.monotonic()
        # max_writes caps hook proposals. Restoration is the envelope's own
        # teardown promise, not another proposal and never consumes that cap.
        if not restoration:
            ctx["remaining"] -= 1
        try:
            timing["write"] = {"start_s": time.monotonic()}
            try:
                ctx["core"].set_property(ctx["device"], ctx["property"], value)
            finally:
                timing["write"]["end_s"] = time.monotonic()
            timing["wait"] = {"start_s": time.monotonic()}
            try:
                ctx["core"].wait_for_device(ctx["device"])
            finally:
                timing["wait"]["end_s"] = time.monotonic()
        except Exception as exc:
            self._record_event(event, event="property_write_failure",
                               hook_event_index=hook_event_index,
                               action=self._action_record(action), decision="failed",
                               reason=f"parent device write failed: {exc}",
                               last_known=ctx["last_known"], restoration=restoration, timing=timing)
            raise RuntimeError(f"property write failed: {exc}") from exc
        return action, event, hook_event_index, timing

    def _verify_property_actions(
        self, applied: list[tuple[SetDeviceProperty, dict, int | None, dict]],
        *, restoration: bool = False,
    ) -> None:
        # The index travels with each applied action because the accept record is
        # written HERE, after the whole set verifies -- not in _apply_property.
        # Without it every accepted property write logged hook_event_index null
        # while its named-stage twin logged 0..N, so the property audit carried
        # no frame identity at all. Measured on M5, 2026-08-17.
        import time
        from microclaw.authorization import _verify_property
        ctx = self._property_context
        assert ctx is not None
        for action, event, hook_event_index, timing in applied:
            timing["read_back"] = {"start_s": time.monotonic()}
            try:
                try:
                    observed = _verify_property(
                        ctx["core"], ctx["device"], ctx["property"], action.value
                    )
                finally:
                    timing["read_back"]["end_s"] = time.monotonic()
            except Exception as exc:
                self._record_event(event, event="property_verification_failure",
                                   hook_event_index=hook_event_index,
                                   action=self._action_record(action), decision="failed",
                                   reason=str(exc), restoration=restoration, timing=timing)
                raise RuntimeError(f"property verification failed: {exc}") from exc
            ctx["last_known"] = observed
            self._accept_event(event, action, "property write passed envelope, authorization, SafetyGuard, and read-back",
                               hook_event_index=hook_event_index,
                               device=ctx["device"], property=ctx["property"],
                               requested=action.value, achieved=ctx["last_known"],
                               restoration=restoration, timing=timing)

    @staticmethod
    def axes_signature(event: dict) -> tuple:
        """Return the engine-preserved identity of an acquisition event."""
        axes = event.get("axes", {})
        if not isinstance(axes, dict):
            raise RuntimeError(f"planned hook event has invalid axes {axes!r}")
        return tuple(sorted(axes.items()))

    def _apply_named_stage(self, action: MoveNamedStage, event: dict,
                           *, restoration: bool = False,
                           hook_event_index: int | None = None) -> None:
        ctx = self._named_stage_context
        index = hook_event_index
        if ctx is None:
            self._refuse_event(event, action, "no named-stage envelope was authorized for this run")
            raise RuntimeError("named-stage action refused: no authorized envelope")
        target = float(action.position_um)
        restoring_entry = restoration and target == ctx["initial_value"]
        if not restoring_entry and (target < ctx["min_um"] or target > ctx["max_um"]):
            self._refuse_event(event, action, "proposal is outside the authorized named-stage interval")
            raise RuntimeError("named-stage action refused: outside authorized interval")
        if not restoration and ctx["remaining"] <= 0:
            self._refuse_event(event, action, "authorized named-stage write budget exhausted")
            raise RuntimeError("named-stage action refused: write budget exhausted")
        try:
            if restoring_entry:
                ctx["guard"].check_named_stage(
                    ctx["device"], target, restoration_entry=True,
                )
            else:
                ctx["guard"].check_named_stage(ctx["device"], target)
        except Exception as exc:
            self._refuse_event(event, action, f"SafetyGuard refused named-stage motion: {exc}")
            raise RuntimeError(f"named-stage action refused: {exc}") from exc
        # The budget counts attempted dispatches, including writes that raise.
        if not restoration:
            ctx["remaining"] -= 1
        band_policy = "floor" if restoration else "relative"
        tolerance_lookup = getattr(ctx["guard"], "stage_move_tolerance", None)
        configured = tolerance_lookup(ctx["device"]) if tolerance_lookup else None
        start_um = read_stage_start_position(
            ctx["core"], ctx["device"], target, band_policy, configured
        )
        try:
            try:
                ctx["core"].set_position(ctx["device"], target)
            except Exception as exc:
                raise stage_move_dispatch_failure(
                    ctx["core"], ctx["device"], target, start_um,
                    band_policy, configured, exc,
                ) from exc
            result = settle_stage_move(
                ctx["core"], ctx["device"], target, start_um,
                band_policy, configured,
            )
            achieved = result["measured_um"]
        except Exception as exc:
            failure_result = exc.result if isinstance(exc, StageMoveError) else {}
            if isinstance(exc, StageMoveError) and exc.result["measured_um"] is not None:
                ctx["last_known"] = exc.result["measured_um"]
            self._record_event(
                event, event="named_stage_write_failure",
                hook_event_index=index, action=self._action_record(action),
                decision="failed", reason=f"parent stage move failed: {exc}",
                last_known_um=ctx["last_known"], restoration=restoration,
                **failure_result,
            )
            raise RuntimeError(f"named-stage move failed: {exc}") from exc
        ctx["last_known"] = achieved
        event["named_stage_device"] = ctx["device"]
        event["named_stage_requested_um"] = target
        event["named_stage_achieved_um"] = achieved
        event["named_stage_measured_um"] = achieved
        event["named_stage_tolerance_um"] = result["tolerance_um"]
        event["named_stage_within_tolerance"] = result["within_tolerance"]
        event["named_stage_start_um"] = result["start_um"]
        event["named_stage_arrival_residual_um"] = result["arrival_residual_um"]
        event["named_stage_band_policy"] = result["band_policy"]
        event["named_stage_band_source"] = result["band_source"]
        event["named_stage_arrival_unverifiable"] = result["arrival_unverifiable"]
        event["named_stage_verification_kind"] = result["verification_kind"]
        event["named_stage_error_um"] = achieved - target
        self._accept_event(
            event, action, "named-stage move passed envelope and SafetyGuard",
            hook_event_index=index, device=ctx["device"], requested_um=target,
            achieved_um=achieved, error_um=achieved - target,
            measured_um=achieved, tolerance_um=result["tolerance_um"],
            within_tolerance=result["within_tolerance"],
            start_um=result["start_um"],
            arrival_residual_um=result["arrival_residual_um"],
            band_policy=result["band_policy"], band_source=result["band_source"],
            arrival_unverifiable=result["arrival_unverifiable"],
            verification_kind=result["verification_kind"],
            restoration=restoration,
        )

    def pre_hardware_hook_fn(self, event: dict | list[dict]) -> dict | list[dict]:
        """Consume the immutable planned action set for this event's axes."""
        ctx = self._fixed_plan_context
        if ctx is None:
            return event
        if isinstance(event, list):
            if len(event) == 1:
                self.pre_hardware_hook_fn(event[0])
                return event
            reason = (
                "a planned per-frame hardware action cannot be honoured inside a "
                "hardware-sequenced burst because the burst runs with no software "
                "callback between exposures; use a nonzero interval_s to disable "
                "time-axis sequencing"
            )
            record_event = event[0] if event and isinstance(event[0], dict) else {}
            self._record_event(
                record_event, event="hook_action", decision="refused", reason=reason,
                hook_event_axes=[item.get("axes") for item in event
                                 if isinstance(item, dict)],
            )
            raise RuntimeError(reason)
        signature = self.axes_signature(event)
        if signature not in ctx["plan"] or signature in ctx["consumed"]:
            raise RuntimeError(
                f"planned hook actions could not resolve unconsumed axes {dict(signature)!r}"
            )
        ctx["consumed"].add(signature)
        index, actions = ctx["plan"][signature]
        property_actions = []
        for action in actions:
            if isinstance(action, MoveNamedStage):
                self._apply_named_stage(action, event, hook_event_index=index)
            elif isinstance(action, SetDeviceProperty):
                property_actions.append(self._apply_property(action, event, hook_event_index=index))
            else:
                # Empty lists are explicit; every nonempty fixed-plan entry must
                # contain an action this coordinator owns.
                self._refuse_event(event, action, "unsupported hardware action in fixed hook_action_plan")
                raise RuntimeError(f"unsupported planned hook action {action.kind}")
        if property_actions:
            self._verify_property_actions(property_actions)
        return event

    def close_adaptive_handoff(self) -> None:
        """Prevent decisions made after the final authorized yield becoming writes."""
        if self._context is not None:
            self._context["closed"] = True
        if self._fixed_plan_context is not None and self._fixed_plan_context.get("adaptive"):
            self._fixed_plan_context["closed"] = True

    def _queue_adaptive_candidate(self, event: dict, metadata: dict,
                                  actions: tuple[HookAction, ...]) -> bool:
        """Register the engine-preserved identity before publishing the event."""
        ctx = self._context
        coordinator = self._fixed_plan_context
        assert ctx is not None
        if ctx.get("closed"):
            reason = "adaptive handoff is closed after the final authorized event"
            for action in actions:
                self._refuse(metadata, action, reason)
            if not actions:
                self._record(metadata, event="hook_action", decision="refused", reason=reason)
            return False
        if coordinator is None:
            ctx["candidates"].put(event)
            ctx["emitted"] += 1
            return True
        signature = self.axes_signature(event)
        if coordinator.get("closed"):
            reason = "adaptive handoff is closed after the final authorized event"
            for action in actions:
                self._refuse(metadata, action, reason)
            if not actions:
                self._record(metadata, event="hook_action", decision="refused", reason=reason)
            self.note_aborted()
            return False
        if signature in coordinator["plan"]:
            reason = f"adaptive event repeats axes signature {dict(signature)!r}"
            for action in actions:
                self._refuse(metadata, action, reason)
            self._record(metadata, event="hook_action", decision="refused", reason=reason)
            return False
        index = ctx["emitted"]
        coordinator["plan"][signature] = (index, actions)
        ctx["candidates"].put(event)
        ctx["emitted"] += 1
        return True

    def restore_named_stage(self) -> dict[str, Any] | None:
        ctx = self._named_stage_context
        if ctx is None:
            return None
        restore = ctx["restore"]
        if restore == "leave":
            return {"policy": "leave", "entry_um": ctx["initial_value"],
                    "last_known_um": ctx["last_known"], "restored": False}
        target = ctx["initial_value"] if restore == "entry" else float(restore["value"])
        event = {"axes": {}}
        self._apply_named_stage(MoveNamedStage(target), event, restoration=True)
        return {"policy": restore, "entry_um": ctx["initial_value"],
                "last_known_um": ctx["last_known"], "restored": True}

    def restore_property(self) -> dict[str, Any] | None:
        ctx = self._property_context
        if ctx is None:
            return None
        restore = ctx["restore"]
        if restore == "leave":
            return {"policy": "leave", "entry_value": ctx["initial_value"],
                    "last_known_value": ctx["last_known"], "restored": False}
        target = ctx["initial_value"] if restore == "entry" else restore["value"]
        event = {"axes": {}}
        applied = [self._apply_property(SetDeviceProperty(target), event, restoration=True)]
        self._verify_property_actions(applied, restoration=True)
        return {"policy": restore, "entry_value": ctx["initial_value"],
                "last_known_value": ctx["last_known"], "restored": True}

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

    def _record_event(self, hardware_event: dict, **fields: Any) -> None:
        """Record a pre-hardware decision using event-shaped frame identity."""
        from microclaw.hooks import HookBase
        self._log.append({**HookBase.where_event(hardware_event), **fields})
        self._write_log()

    def _refuse_event(self, event: dict, action: HookAction, reason: str) -> None:
        self._record_event(
            event, event="hook_action", action=self._action_record(action),
            decision="refused", reason=reason,
        )

    def _accept_event(self, event: dict, action: HookAction, reason: str,
                      **fields: Any) -> None:
        self._record_event(
            event, event="hook_action", action=self._action_record(action),
            decision="accepted", reason=reason, **fields,
        )

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

    def _refuse_selector(self, metadata: dict, action: HookAction, reason: str) -> None:
        if self._context is not None:
            self._context["selector_refusal_reason"] = reason
        self._refuse(metadata, action, reason)

    def _done_early(self, reason: str) -> None:
        assert self._context is not None
        progress = self._context["progress"]
        if self._context.get("require_routing_decision"):
            progress.done_early(reason)
        else:
            progress.done_early()

    def _accept(self, metadata: dict, action: HookAction, reason: str,
                **fields: Any) -> None:
        self._record(metadata, event="hook_action", action=self._action_record(action),
                     decision="accepted", reason=reason, **fields)

    @staticmethod
    def _current_event(events: list[dict], metadata: dict) -> dict | None:
        """Resolve the exposed event from MM-stamped position/axis metadata."""
        axes = metadata.get("Axes") or {}
        position = metadata.get("PositionName")
        matches = []
        for event in events:
            event_axes = event.get("axes") or {}
            if position is not None and event_axes.get("position") != position:
                continue
            if any(key != "position" and key in event_axes and event_axes[key] != value
                   for key, value in axes.items()):
                continue
            matches.append(event)
        if len(matches) == 1:
            return matches[0]
        x = metadata.get("XPosition_um_Intended")
        y = metadata.get("YPosition_um_Intended")
        xy_matches = [event for event in matches
                      if (x is None or event.get("x") == x)
                      and (y is None or event.get("y") == y)]
        return xy_matches[0] if len(xy_matches) == 1 else None

    def _dispatch(self, action: HookAction, metadata: dict) -> str | None:
        self._action_counts[action.kind] = self._action_counts.get(action.kind, 0) + 1
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
        if isinstance(action, (MoveNamedStage, SetDeviceProperty)):
            self._refuse(
                metadata, action,
                "fixed-plan hardware actions must come from hook_action_plan, not analyze_frame",
            )
            return None
        ctx = self._context
        if ctx is None:
            if isinstance(action, ContinueAcquisition):
                self._accept(
                    metadata, action,
                    "noop: this runner already continues through its fixed event plan",
                )
            else:
                self._refuse(metadata, action, "unsupported-by-this-runner")
            return
        if isinstance(action, RequestAutofocus):
            af = self._autofocus_context
            if af is None:
                self._refuse_selector(metadata, action, "unsupported-by-run_adaptive_survey")
                return
            from microclaw.hooks import HookBase
            where = HookBase.where(metadata)
            tile = (("position", where["position"])
                    if where.get("position") is not None else
                    ("xy", where.get("x_um"), where.get("y_um")))
            if tile in af["refocused_tiles"]:
                self._refuse_selector(metadata, action, "this tile has already been refocused")
                return
            required = af["sweep_exposures"] + 1
            if af["remaining"] < required:
                self._refuse_selector(metadata, action, "authorized autofocus exposure budget exhausted")
                return
            event = self._current_event(ctx["events"], metadata)
            if event is None:
                self._refuse_selector(metadata, action, "current tile could not be resolved from image metadata")
                return
            lock_check = af["focus_lock_check"]
            if lock_check is not None:
                try:
                    lock = lock_check()
                except Exception as exc:
                    self._refuse_selector(metadata, action,
                                 f"focus lock state could not be read: {exc}")
                    return
                if lock.get("engaged"):
                    self._refuse_selector(metadata, action,
                                 "focus lock is engaged; autofocus sweep refused")
                    return
            try:
                entry_z = af["ctrl"].core.get_position()
                af["guard"].check_z(entry_z - af["z_range_um"] / 2)
                af["guard"].check_z(entry_z + af["z_range_um"] / 2)
            except Exception as exc:
                self._refuse_selector(metadata, action,
                             f"SafetyGuard refused autofocus sweep: {exc}")
                return
            from microclaw.tools import _run_autofocus_passes
            result = _run_autofocus_passes(
                af["ctrl"], af["z_range_um"], af["z_step_um"],
                af["method"], af["settle_ms"],
            )
            af["remaining"] -= af["sweep_exposures"]
            af["refocused_tiles"].add(tile)
            outcome = {
                "converged": result.converged, "moved": result.moved,
                "reason": result.reason, "entry_z_um": result.entry_z_um,
                "final_z_um": result.final_z_um,
            }
            if not result.converged:
                self._accept(metadata, action,
                             "autofocus ran and did not converge; Z restored",
                             autofocus=outcome)
                return
            if ctx["emitted"] >= ctx["max_events"]:
                self._refuse_selector(metadata, action,
                             "outside committed reservation: refocused tile cannot be re-exposed")
                return
            refocused_event = dict(event)
            refocused_event["axes"] = dict(event.get("axes") or {})
            # NDTiff indexes frames by their complete axes key. Reusing the
            # first look's axes makes the second replace it in the readable
            # index, so distinguish the focused look explicitly.
            refocused_event["axes"]["refocus"] = 1
            if refocused_event.get("z") is not None:
                refocused_event["z"] = result.final_z_um
            if not self._queue_adaptive_candidate(refocused_event, metadata, ()):
                return
            # The survey will now receive one frame more than its plan. Raised
            # here rather than sized from the budget up front, so a survey that
            # never spends its refocuses still completes instead of idling out.
            ctx["progress"].expect_one_more()
            af["remaining"] -= 1
            af["second_look_tiles"].add(tile)
            ctx["refocus_requeued"] = True
            self._accept(metadata, action, "refocused and re-queued this tile",
                         autofocus=outcome)
            return
        if isinstance(action, (MoveStage, SetExposure)):
            self._refuse(metadata, action, "unsupported-by-run_adaptive_survey")
            return
        if isinstance(action, StopAcquisition):
            self._done_early("hook_stop")
            self._accept(metadata, action, "survey stopped before another tile was dispatched")
            return
        events = ctx["events"]
        # A finished plan is reported as a finished plan. Checked before the
        # reservation because an authorized refocus widens max_events by exactly
        # the re-exposures it may take, so on M5 2026-08-11 a three-tile survey
        # that spent its one re-exposure satisfied both conditions at the last
        # tile and reported "outside committed reservation" -- which reads as a
        # dose cap when the survey had simply run out of tiles. Refusing here
        # dispatches nothing either way, so the order cannot admit an exposure.
        if (isinstance(action, ContinueAcquisition)
                and ctx["successor"] is None
                and ctx["cursor"] >= len(events)):
            self._refuse_selector(metadata, action, "planned survey cursor is already at the end")
            return
        deferred_acquire = (
            isinstance(action, AcquireAt) and "acquire_hits" in ctx
        )
        if not deferred_acquire and ctx["emitted"] >= ctx["max_events"]:
            self._refuse_selector(
                metadata, action,
                "outside committed reservation: all planned frame slots are already dispatched",
            )
            return
        if isinstance(action, ContinueAcquisition):
            # A survey indexes its trusted finite plan. A streaming route asks
            # trusted parent code for exactly one successor instead.
            if ctx["successor"] is not None:
                event = ctx["successor"](ctx["cursor"])
            else:
                # The cursor-at-end refusal is made above, before reservation.
                event = events[ctx["cursor"]]
            ctx["cursor"] += 1
        else:
            assert isinstance(action, AcquireAt)
            if isinstance(action.position, int):
                if action.position < 0 or action.position >= len(events):
                    self._refuse_selector(metadata, action, "position is not in the planned survey")
                    return
                event = events[action.position]
            else:
                matches = [e for e in events if
                           (e.get("axes") or {}).get("position") == action.position]
                if not matches:
                    # Absent and ambiguous are different operator mistakes and
                    # must not share a reason string: the refusal record is the
                    # only account of why a tile was not acquired.
                    self._refuse_selector(metadata, action,
                                 "no planned survey position is labelled "
                                 f"{action.position!r}")
                    return
                if len(matches) > 1:
                    self._refuse_selector(metadata, action,
                                 f"label {action.position!r} matches {len(matches)} "
                                 "planned positions and is not a unique target")
                    return
                event = matches[0]
        try:
            has_x, has_y = event.get("x") is not None, event.get("y") is not None
            if has_x != has_y:
                raise ValueError("event carries only one XY coordinate")
            x = y = None
            if has_x:
                x, y = self._event_xy(event)
                ctx["guard"].check_xy(x, y)
            if event.get("z") is not None:
                ctx["guard"].check_z(event["z"])
        except Exception as exc:
            self._refuse_selector(metadata, action, f"SafetyGuard refused planned event: {exc}")
            return
        if deferred_acquire:
            assert ctx["successor"] is None and x is not None and y is not None
            label = (event.get("axes") or {}).get("position")
            key = (label, x, y)
            if any(hit["_key"] == key for hit in ctx["acquire_hits"]):
                self._refuse_selector(
                    metadata, action,
                    "planned tile is already recorded for acquire phase",
                )
                return
            if len(ctx["acquire_hits"]) >= ctx["max_hits"]:
                self._refuse_selector(metadata, action, "acquire phase max_hits exhausted")
                return
            try:
                z = float(ctx["read_z"]())
                ctx["guard"].check_z(z)
            except Exception as exc:
                self._refuse_selector(metadata, action,
                             f"SafetyGuard refused current focus position: {exc}")
                return
            ctx["acquire_hits"].append({
                "name": str(label), "x_um": x, "y_um": y, "z_um": z,
                "_key": key,
            })
            self._accept(metadata, action, "planned tile recorded for acquire phase")
            return
        pending = tuple(ctx.pop("pending_hardware_actions", ()))
        if not self._queue_adaptive_candidate(event, metadata, pending):
            return
        self._accept(metadata, action, "planned event passed guard and committed reservation")

    def _dispatch_adaptive_partition(self, actions: tuple[HookAction, ...],
                                     metadata: dict,
                                     observation_index: int) -> tuple[bool, bool]:
        current = tuple(a for a in actions if isinstance(a, (EmitArtifact, DiscardFrame)))
        hardware = tuple(a for a in actions if isinstance(a, (MoveNamedStage, SetDeviceProperty)))
        selectors = tuple(a for a in actions if isinstance(
            a, (ContinueAcquisition, AcquireAt, RequestAutofocus, StopAcquisition)
        ))
        known = current + hardware + selectors
        other = tuple(a for a in actions if a not in known)
        if self._context is not None and self._context.get("require_routing_decision"):
            malformed_route = len(selectors) != 1 or not isinstance(
                selectors[0], (ContinueAcquisition, StopAcquisition)
            )
            if malformed_route:
                reason = (
                    "malformed adaptive action partition: each image requires "
                    "exactly one ContinueAcquisition or StopAcquisition decision"
                )
                for action in actions:
                    self._action_counts[action.kind] = self._action_counts.get(action.kind, 0) + 1
                    self._refuse(metadata, action, reason)
                if not actions:
                    self._record(metadata, event="hook_action", decision="refused",
                                 reason=reason)
                self._done_early("routing_refusal")
                return False, True
        discard = False
        for action in current:
            artifact_hash = self._dispatch(action, metadata)
            if artifact_hash:
                self._log[observation_index]["artifact_sha256"] = artifact_hash
                self._write_log()
            discard = discard or isinstance(action, DiscardFrame)
        autofocus = tuple(a for a in selectors if isinstance(a, RequestAutofocus))
        # Without hardware, preserve the established sequential adaptive
        # contract (notably RequestAutofocus + ContinueAcquisition). Cardinality is
        # a hardware-to-one-event association rule.
        if not hardware:
            assert self._context is not None
            self._context["refocus_requeued"] = False
            remaining = tuple(a for a in actions if a not in current)
            for index, action in enumerate(remaining):
                self._dispatch(action, metadata)
                if self._context.get("refocus_requeued"):
                    for deferred in remaining[index + 1:]:
                        self._action_counts[deferred.kind] = self._action_counts.get(deferred.kind, 0) + 1
                        self._refuse(metadata, deferred,
                                     "not dispatched until the refocused tile is judged")
                    break
            return discard, False

        malformed = len(selectors) != 1 or isinstance(selectors[0], StopAcquisition)
        if malformed:
            reason = ("malformed adaptive action partition: next-frame hardware "
                      "actions require exactly one compatible next-event selector")
            for action in selectors + hardware + other:
                self._action_counts[action.kind] = self._action_counts.get(action.kind, 0) + 1
                self._refuse(metadata, action, reason)
            assert self._context is not None
            self._done_early("routing_refusal")
            return discard, True
        if autofocus:
            self._dispatch(autofocus[0], metadata)
            for action in hardware:
                self._action_counts[action.kind] = self._action_counts.get(action.kind, 0) + 1
                self._refuse(metadata, action,
                             "not dispatched until the refocused tile is judged")
            for action in other:
                self._dispatch(action, metadata)
            return discard, False
        assert self._context is not None
        coordinator = self._fixed_plan_context
        if coordinator is not None and coordinator.get("closed"):
            reason = "adaptive handoff is closed after the final authorized event"
            for action in selectors + hardware:
                self._action_counts[action.kind] = self._action_counts.get(action.kind, 0) + 1
                self._refuse(metadata, action, reason)
            self.note_aborted()
            return discard, False
        self._context["pending_hardware_actions"] = hardware
        self._context.pop("selector_refusal_reason", None)
        for action in selectors + other:
            self._dispatch(action, metadata)
        pending = tuple(self._context.pop("pending_hardware_actions", ()))
        if pending:
            selector_reason = self._context.pop(
                "selector_refusal_reason", "next-event selector did not commit a candidate"
            )
            reason = f"next-event selector was refused: {selector_reason}"
            for action in pending:
                self._action_counts[action.kind] = self._action_counts.get(action.kind, 0) + 1
                self._refuse(metadata, action, reason)
        return discard, False

    def image_process_fn(self, image, metadata, _hardware_event_queue):
        try:
            if hasattr(self.hook, "analyze_frame"):
                hook_metadata = dict(metadata)
                af = self._autofocus_context
                if af is not None:
                    from microclaw.hooks import HookBase
                    where = HookBase.where(metadata)
                    tile = (("position", where["position"])
                            if where.get("position") is not None else
                            ("xy", where.get("x_um"), where.get("y_um")))
                    second_look = tile in af["second_look_tiles"]
                    hook_metadata["microclaw_refocused"] = second_look
                    if second_look:
                        af["second_look_tiles"].remove(tile)
                raw = self.hook.analyze_frame(image, hook_metadata)
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
                        if self._context.get("require_routing_decision"):
                            self._done_early("routing_refusal")
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
                if self._context is not None:
                    discard, _malformed = self._dispatch_adaptive_partition(
                        actions, metadata, observation_index
                    )
                else:
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
            elif hasattr(self.hook, "image_process_fn"):
                returned = self.hook.image_process_fn(
                    image, metadata, DeniedEventQueue()
                )
                self._record(
                    metadata, event="legacy_hook_frame",
                    outcome="discarded" if returned is None else "retained",
                )
            else:
                # A fixed hook_action_plan is dispatched before exposure and
                # needs no image analysis. Keep the frame and add no misleading
                # legacy-hook record; the hook_action entries are its audit trail.
                returned = image, metadata
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

    def post_hardware_hook_fn(self, event: dict | list[dict]) -> dict | list[dict]:
        if isinstance(event, list):
            # Thread each item's RESULT back, exactly as the dict path threads
            # `current`, but in place so the very list we were handed is the one
            # returned. Discarding the result would drop a child's replacement
            # event in sequenced batches only, and would also skip the None check
            # below -- so a hook bug that raises loudly on one event would pass
            # silently on a burst of them.
            event[:] = [self.post_hardware_hook_fn(item) for item in event]
            return event
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

    @property
    def artifact_emitting_hook_names(self) -> list[str]:
        return [
            name for name, hook in self.named_hooks
            if bool(getattr(hook, "can_emit_artifacts", False))
        ]
