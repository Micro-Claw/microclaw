from __future__ import annotations
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from microclaw import __version__
from microclaw.image_analysis import (
    compute_stats, normalized_laplacian_variance, resolve_min_snr, snap_to_numpy,
)
from microclaw.safety import SafetyViolation


def analysis_observation_record(
    *, analyzer: str | None, analyzer_version: str | None, result,
    parameters: dict | None = None, artifact_sha256: str | None = None,
    status: str = "observed",
) -> dict:
    """Build the shared live/offline design/26 observation envelope."""
    record = {
        "schema": "microclaw.analysis-observation/v1",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "analyzer": analyzer,
        "analyzer_version": analyzer_version,
        "parameters": parameters or {},
        "result": result,
    }
    if artifact_sha256 is not None:
        record["artifact_sha256"] = artifact_sha256
    json.dumps(record, allow_nan=False)
    return record


def write_analysis_observation(
    sink: list[dict], *, where: dict | None = None,
    analyzer: str | None, analyzer_version: str | None, result,
    parameters: dict | None = None, artifact_sha256: str | None = None,
    status: str = "observed", write=None,
) -> dict:
    """Build, validate, append, and optionally persist one observation.

    This is the shared live/offline writing path.  Callers supply provenance
    around the stable observation envelope rather than inventing a second one.
    """
    if status not in {"unverified", "provisional", "observed"}:
        raise ValueError(f"Unknown analysis observation status: {status!r}")
    record = analysis_observation_record(
        analyzer=analyzer, analyzer_version=analyzer_version, result=result,
        parameters=parameters, artifact_sha256=artifact_sha256, status=status,
    )
    entry = {**(where or {}), **record}
    json.dumps(entry, allow_nan=False)
    sink.append(entry)
    if write is not None:
        write()
    return entry


def _frame_index(metadata: dict):
    """Frame/time index for a per-frame log entry.

    The live rig stamps FrameIndex/Frame (design/23 F2 metadata dump), NOT a
    "time" key; the acq-engine port carries the same index at Axes["time"]. Read
    the real keys first and fall back to the legacy "time" so the port/offline
    paths keep working. metadata.get("time") alone silently returned None on the
    rig — the wrong-key-name failure class F2 is closing.
    """
    for key in ("FrameIndex", "Frame"):
        if metadata.get(key) is not None:
            return metadata[key]
    axes = metadata.get("Axes") or {}
    if axes.get("time") is not None:
        return axes["time"]
    return metadata.get("time")


class HookBase:
    """All hooks write a summary log so Claude can read results afterward."""

    def __init__(self, log_path: str | None = None):
        self.log_path = log_path
        self._log: list[dict] = []
        # Set by the ADAPTIVE survey runner (design/27 Fix 4): the full built
        # tile list, seed at index 0. Only survey_events[0] is pre-dispatched;
        # the hook walks the rest — candidates.put(the next tile) to continue,
        # progress.done_early() to stop. None under every other runner.
        self.survey_events: list | None = None
        # Set by the survey-with-detector runner (adaptive or not): the queue
        # follow-up/next events are submitted on, and the SurveyProgress
        # counter. A saved hook class cannot close over these the way an
        # inline detector can, so the runner hands them over as attributes.
        # None under the batched runners — a hook that needs them must check
        # and RAISE (fail loudly), never log a quiet success (design/24).
        self.candidates = None
        self.progress = None

    def _write_log(self) -> None:
        """Rewrite the whole file from `self._log`: one hook instance per log_path.

        A caller that constructs a fresh hook per position and points them all at
        one log_path gets the last position's results only — each instance starts
        with an empty `_log` and truncates its predecessor (design/19 Fix 3).
        Hand multi-position events to a single hook instead; see
        `tools._acquire_positions_with_hook`.
        """
        if self.log_path:
            Path(self.log_path).write_text(json.dumps(self._log, indent=2), encoding="utf-8")

    @staticmethod
    def where(metadata: dict) -> dict:
        """Where this image was taken, from its own metadata. Never raises.

        A multi-position acquisition stamps PositionName / XPosition_um_Intended /
        YPosition_um_Intended; a Z-stack stamps ZPosition_um_Intended. Neither
        stamps the other's keys (the acq-engine gates each on the event carrying
        that coordinate), so every read is a .get() and a missing key means "this
        acquisition has no such axis", not "wrong name". This is the fix for the
        design/23 F2 bug where hook_docs claimed XPosition_um_Intended did not
        exist; it does, for every multi-position grid. Verified without hardware in
        design/23-hook-metadata-coords-spike.py and on the live bridge.
        """
        axes = metadata.get("Axes") or {}
        out: dict = {"position": metadata.get("PositionName", axes.get("position"))}
        for key, field in (("XPosition_um_Intended", "x_um"),
                           ("YPosition_um_Intended", "y_um"),
                           ("ZPosition_um_Intended", "z_um")):
            value = metadata.get(key)
            if value is not None:
                out[field] = round(float(value), 3)
        return out

    @staticmethod
    def where_event(event: dict) -> dict:
        """Same shape as where(), for pre/post-hardware hooks that get an event
        (which carries absolute x/y/z and an axes dict) rather than image metadata.

        Keeps "where was this" to a single spelling across the two hook shapes —
        the thing design/23 F2 was written to prevent a third of.
        """
        axes = event.get("axes") or {}
        out: dict = {"position": axes.get("position")}
        for key, field in (("x", "x_um"), ("y", "y_um"), ("z", "z_um")):
            value = event.get(key)
            if value is not None:
                out[field] = round(float(value), 3)
        return out

    def log(self, metadata: dict, **fields) -> None:
        """Append ONE self-describing entry: where the image was + what the hook
        measured, then persist.

        Every hook routes its per-image record through here instead of appending
        to self._log directly, so no hook log can ever again say "filament_r2_c2,
        SNR 73" without saying where r2_c2 was (design/23 Episode A).
        """
        self._log.append({**self.where(metadata), **fields})
        self._write_log()

    def log_event(self, event: dict, **fields) -> None:
        """log() for the pre/post-hardware hook shape (see where_event)."""
        self._log.append({**self.where_event(event), **fields})
        self._write_log()

    def log_analysis(
        self,
        metadata: dict,
        *,
        analyzer: str,
        analyzer_version: str,
        result,
        parameters: dict | None = None,
        artifact_sha256: str | None = None,
        status: str = "observed",
    ) -> None:
        """Persist one provenance-bearing, observation-only analysis result.

        This is the stable design/26 boundary for a newly generated analysis
        adapter. ``result`` and ``parameters`` must already be JSON values: the
        adapter owns conversion from package-specific arrays, masks, boxes, or
        scalar types. Keeping that conversion explicit prevents a log from
        silently stringifying an output whose axes or units were never checked.

        Logging has no acquisition side effect. A later, separately reviewed
        hook may use a verified result to make a guarded decision.
        """
        write_analysis_observation(
            self._log, where=self.where(metadata), analyzer=analyzer,
            analyzer_version=analyzer_version, result=result,
            parameters=parameters, artifact_sha256=artifact_sha256,
            status=status, write=self._write_log,
        )

    def note_stalled(self, max_idle_s: float) -> None:
        """The survey runner's idle watchdog fired: no image came back and no
        candidate arrived for max_idle_s. Called by the event-stream generator
        (tools._acquire_survey_with_detector), not by hook code — loud in the
        log, never silent (design/24 Fix 2a).
        """
        self._log.append({"event": "stalled", "max_idle_s": max_idle_s})
        self._write_log()

    def note_aborted(self) -> None:
        """The acquisition was aborted from outside mid-survey. Same caller as
        note_stalled; keeps the log word "aborted" distinct from "stalled"
        (design/24 Fix 2a).
        """
        self._log.append({"event": "aborted"})
        self._write_log()

    def note_budget_exhausted(self, max_events: int, *, overrun_frames: int = 0) -> None:
        """Record a clean feeder stop at its authorized event budget."""
        self._log.append({
            "event": "budget_exhausted",
            "max_events": max_events,
            "overrun_frames": overrun_frames,
        })
        self._write_log()

    def get_summary(self) -> list[dict]:
        return self._log


class AutofocusHook(HookBase):
    """Form B autofocus: runs a Z sweep before each image in the acquisition.

    Used as a post_hardware_hook_fn inside an Acquisition context. After hardware
    moves to the event's XY (and nominal Z), this hook sweeps Z and updates the
    focus device to the sharpest plane before the camera fires.
    """

    def __init__(
        self,
        ctrl,
        guard,
        z_range_um: float,
        z_step_um: float,
        settle_ms: int = 50,
        log_path: str | None = None,
    ):
        super().__init__(log_path)
        from microclaw.autofocus import coarse_then_fine_autofocus
        self.ctrl = ctrl
        self.guard = guard
        self.z_range_um = z_range_um
        self.z_step_um = z_step_um
        self.settle_ms = settle_ms
        self._autofocus_fn = coarse_then_fine_autofocus

    def post_hardware_hook_fn(self, event: dict) -> dict:
        """Called after hardware moves to event position, before image capture."""
        current_z = self.ctrl.core.get_position()
        z_start = current_z - self.z_range_um / 2
        z_end = current_z + self.z_range_um / 2
        try:
            self.guard.check_z(z_start)
            self.guard.check_z(z_end)
        except Exception as e:
            self.log_event(event, autofocus="skipped", reason=str(e))
            return event

        coarse_step = max(self.z_step_um * 5, 1.0)
        result = self._autofocus_fn(
            self.ctrl, self.z_range_um, coarse_step, self.z_step_um, self.settle_ms
        )
        self.log_event(
            event,
            best_z_um=round(result.final_z_um, 3),
            converged=result.converged,
            **({"warning": result.reason} if not result.converged else {}),
        )
        return event


class FocusFeedbackHook(HookBase):
    """Corrects Z drift per frame during timelapse using Laplacian variance."""

    def __init__(
        self,
        ctrl,
        guard,
        threshold_fraction: float = 0.85,
        z_step_um: float = 0.5,
        max_jogs: int = 5,
        log_path: str | None = None,
    ):
        super().__init__(log_path)
        self.ctrl = ctrl
        self.guard = guard
        self.threshold = threshold_fraction
        self.z_step = z_step_um
        self.max_jogs = max_jogs
        self.reference_metric: float | None = None

    def image_process_fn(self, image: np.ndarray, metadata: dict, event_queue):
        # Normalized metric (design/14 §10): photobleaching dims the frames
        # over a timelapse, and a raw Laplacian variance would read that
        # intensity loss as focus loss and jog Z for no reason.
        metric = normalized_laplacian_variance(image)
        if self.reference_metric is None:
            self.reference_metric = metric
            return image, metadata

        if metric < self.reference_metric * self.threshold:
            focus_device = self.ctrl.core.get_focus_device()
            jogs, corrected, outcome, reason = 0, False, "no_improvement", None
            for _ in range(self.max_jogs):
                current_z = self.ctrl.core.get_position()
                try:
                    self.guard.check_z(current_z + self.z_step)
                    self.ctrl.core.set_position(current_z + self.z_step)
                    self.ctrl.core.wait_for_device(focus_device)
                    jogs += 1
                    new_metric = normalized_laplacian_variance(snap_to_numpy(self.ctrl))
                    if new_metric >= self.reference_metric * self.threshold:
                        self.reference_metric = new_metric
                        corrected, outcome = True, "recovered"
                        break
                except SafetyViolation as e:
                    # A guard rejection means the correction hit a limit — it is
                    # NOT "corrected". Record it; the operator's only signal is
                    # this log line (a swallowed violation never reaches
                    # execute_tool's handler from an acquisition thread).
                    outcome, reason = "blocked_by_guard", str(e)
                    break
                except Exception as e:
                    outcome, reason = "hardware_error", str(e)
                    break
            fields = {"frame": _frame_index(metadata), "focus_correction": corrected,
                      "jogs": jogs, "outcome": outcome}
            if reason:
                fields["reason"] = reason
            self.log(metadata, **fields)   # exactly one entry per triggering frame
        return image, metadata


class IntensityAdaptiveHook(HookBase):
    """Adjusts exposure per frame to keep mean intensity near a target."""

    def __init__(
        self,
        ctrl,
        guard,
        target_mean: float,
        tolerance: float = 0.1,
        min_exposure_ms: float = 1.0,
        max_exposure_ms: float = 1000.0,
        log_path: str | None = None,
    ):
        super().__init__(log_path)
        self.ctrl = ctrl
        self.guard = guard
        self.target_mean = target_mean
        self.tolerance = tolerance
        self.min_exp = min_exposure_ms
        self.max_exp = max_exposure_ms

    def image_process_fn(self, image: np.ndarray, metadata: dict, event_queue):
        mean = float(np.mean(image))
        if abs(mean - self.target_mean) / self.target_mean > self.tolerance:
            ratio = self.target_mean / max(mean, 1.0)
            new_exp = float(
                np.clip(self.ctrl.core.get_exposure() * ratio, self.min_exp, self.max_exp)
            )
            frame = _frame_index(metadata)
            try:
                self.guard.check_exposure(new_exp)
                self.ctrl.core.set_exposure(new_exp)
                self.log(metadata, frame=frame, new_exposure_ms=round(new_exp, 1))
            except SafetyViolation as e:
                # Guard rejection: expected, but never silent — this log line is
                # the operator's only signal from an acquisition thread.
                self.log(metadata, frame=frame, exposure_change="blocked", reason=str(e))
            except Exception as e:
                self.log(metadata, frame=frame, exposure_change="error", reason=str(e))
        return image, metadata


class PositionFilterHook(HookBase):
    """Keeps images from low-intensity positions out of the dataset.

    Returns None to discard the image — and NOTHING else happens: no event is
    dropped, and a rejected position keeps being moved to and exposed for the
    rest of the acquisition; each of its frames is discarded one by one as it
    arrives (design/27). To stop exposures from happening at all, use the
    adaptive survey runner (stop = don't submit), not this hook.
    """

    def __init__(self, min_mean_intensity: float = 100.0, log_path: str | None = None):
        super().__init__(log_path)
        self.min_mean = min_mean_intensity
        self.rejected: list = []

    def image_process_fn(self, image: np.ndarray, metadata: dict, event_queue):
        mean = float(np.mean(image))
        if mean < self.min_mean:
            # Dedup on the stamped position identity, not metadata["position_index"]:
            # the live rig has no such key, so the old read collapsed every
            # rejection under -1 and stopped logging which positions it dropped
            # after the first (design/23 F2). self.where() reads PositionName.
            pos = self.where(metadata).get("position")
            if pos not in self.rejected:
                self.rejected.append(pos)
                self.log(metadata, mean_intensity=round(mean, 1), action="rejected")
            return None
        return image, metadata


class SNRObservationHook(HookBase):
    """Record deterministic per-tile image statistics without changing the run.

    This is design/26 Run A's positive-control analyzer. It deliberately has no
    threshold action: every image is returned unchanged, no events are submitted,
    and no hardware is touched. Offline code may rank its records by SNR after the
    fixed survey has completed.
    """

    def __init__(self, min_snr: float | None = None, log_path: str | None = None,
                 guard=None, calibration_path: str | None = None):
        super().__init__(log_path)
        source = "explicit"
        if calibration_path:
            if guard is None:
                raise ValueError("calibration_path requires an injected safety guard")
            calibration_path = guard.resolve_readable_path(calibration_path)
            calibration = json.loads(Path(calibration_path).read_text(encoding="utf-8"))
            min_snr = float(calibration["recommended_min_snr"])
            source = "calibration_artifact"
        else:
            min_snr, source = resolve_min_snr(
                explicit=min_snr,
                configured=guard.analysis_min_snr if guard is not None else None,
            )
        self.min_snr = float(min_snr)
        self.threshold_source = source

    def image_process_fn(self, image: np.ndarray, metadata: dict, event_queue):
        started = time.perf_counter()
        stats = compute_stats(image, min_snr=self.min_snr)._asdict()
        stats["analysis_ms"] = round((time.perf_counter() - started) * 1000, 3)
        self.log_analysis(
            metadata,
            analyzer="microclaw.image_analysis.compute_stats",
            analyzer_version=__version__,
            parameters={"min_snr": self.min_snr,
                        "min_snr_source": self.threshold_source},
            result=stats,
        )
        return image, metadata


class MMPluginHook(HookBase):
    """Delegate per-image analysis to an installed Micro-Manager plugin.

    The plugin is treated as an ANALYZER: it receives a scalar/feature derived
    from the image and returns a value microclaw uses for a guarded, Python-side
    decision (e.g. keep/skip). The plugin must NOT be relied on to move hardware
    here — use MMAutofocusPluginHook for that.

    Only a scalar crosses the bridge (np.mean of the image); the full image never
    leaves Python. See design/09 "Composing plugins in a reusable hook".
    """

    def __init__(self, ctrl, guard, classpath: str, method: str = "analyze",
                 reject_below: float | None = None, log_path: str | None = None):
        super().__init__(log_path)
        self.ctrl = ctrl
        self.guard = guard
        guard.check_plugin(classpath)          # runtime blocklist check
        self.classpath = classpath
        self.method = method
        self.reject_below = reject_below
        self._plugin = ctrl.plugins.get_object(classpath)

    def image_process_fn(self, image: np.ndarray, metadata: dict, event_queue):
        feature = float(np.mean(image))        # keep marshalling trivial/scalar
        try:
            score = float(getattr(self._plugin, self.method)(feature))
        except Exception as e:
            self.log(metadata, frame=_frame_index(metadata), plugin_error=str(e))
            return image, metadata             # fail open: never lose data on bug
        keep = self.reject_below is None or score >= self.reject_below
        self.log(metadata, frame=_frame_index(metadata),
                 plugin=self.classpath, score=score, kept=keep)
        return (image, metadata) if keep else None


class MMAutofocusPluginHook(HookBase):
    """Run an installed MM autofocus plugin before each capture, guarded.

    Drop-in alternative to the pure-Python AutofocusHook: same post_hardware slot,
    but focusing is delegated to the lab's validated MM autofocus plugin. The
    plugin owns the motion; microclaw only guards the *result* passively (never
    re-drives Z, which would fight the plugin's own safety controller).
    """

    def __init__(self, ctrl, guard, plugin_name: str | None = None,
                 log_path: str | None = None):
        super().__init__(log_path)
        self.ctrl = ctrl
        self.guard = guard
        self.plugin_name = plugin_name
        # Hardware-motion plugin: gate on the global motion flag, not a blocklist.
        guard.check_plugin_motion(f"autofocus:{plugin_name or '<active>'}")
        self._af = ctrl.plugins.get_autofocus_method(plugin_name)

    def post_hardware_hook_fn(self, event: dict):
        try:
            new_z = float(self._af.full_focus())     # plugin owns the motion
        except Exception as e:
            self.log_event(event, autofocus="skipped", reason=str(e))
            return event
        # PASSIVE guard: assert on the result; if unsafe, abort the whole
        # acquisition by raising — do NOT re-drive Z (that would fight the
        # plugin's own safety controller), and do NOT return None ("skip this
        # capture" does not exist over the bridge: None becomes an empty event
        # that fires the camera at the unsafe Z, and the acquisition keeps
        # going — design/27). Raising is the one lever that stops anything:
        # pycro-manager's hook thread calls acquisition.abort(e) and the error
        # surfaces to the caller. Log first — the raise ends the run.
        try:
            self.guard.check_z(new_z)
        except Exception as e:
            self.log_event(event, autofocus="unsafe_abort", unsafe_z=new_z,
                           reason=str(e))
            raise SafetyViolation(
                f"Autofocus plugin left Z at {new_z:.3f} um, outside the "
                f"guard limit ({e}); aborting the acquisition."
            ) from e
        self.log_event(event, best_z_um=round(new_z, 3), plugin=self.plugin_name)
        return event


PRECODED_HOOK_REGISTRY: dict[str, type] = {
    "autofocus_per_position": AutofocusHook,
    "focus_feedback": FocusFeedbackHook,
    "intensity_adaptive": IntensityAdaptiveHook,
    "position_filter": PositionFilterHook,
    "snr_observer": SNRObservationHook,
    "mm_plugin_analyzer": MMPluginHook,
    "autofocus_mm_plugin": MMAutofocusPluginHook,
}

# Stable analysis integrations belong here as HookBase adapters, including
# ilastik, Cellpose, learned scorers, and lab software. Register the adapter
# here; do not add a package-specific agent tool. User-specific invocations are
# generated and saved through hook_manager instead. Acquisition-time adapters
# must be offline and log analyzer/model versions, parameters, and provenance.
