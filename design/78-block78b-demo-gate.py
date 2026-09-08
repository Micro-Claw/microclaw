r"""Block 78b's demo-machine gate: does a real engine batch where we predict?

design/78 settled the *arithmetic* off-rig, from AcqEngJ-0.39.4's bytecode:
`AcquisitionEvent.fromJSON` truncates `min_start_time` to Long milliseconds with
`d2l`, and `Engine.isSequencable` refuses a differing-t-index pair only when
those deadlines differ. This gate is the half that bytecode cannot answer --
whether the running engine actually batches where that predicts.

The observation is direct and needs no hardware: an **observation-only** hook
records whether each callback received a dict or a list. A list is the engine
handing over a hardware-sequenced burst. Observation-only matters twice over --
it is never refused by the plan-time check this block adds, so it can watch the
engine at exactly the intervals the product refuses for hardware control, and it
writes nothing to the rig.

The crown limb is `0.001 s x 4008 frames`. Every short run at 0.001 s is clean,
which is what makes a "use at least 1 ms" rule look right; the prediction is
that frames 4006/4007 collide anyway through double rounding, because
`4007 * 0.001 * 1000` is `4006.9999999999995`. If a real engine batches exactly
there, the reason this block refuses to state a threshold is observed rather
than argued.

Every limb reports independently, NOT EXERCISED is never a pass, and the exit
status is nonzero unless all limbs pass.

    uv run python design\78-block78b-demo-gate.py --out block78b-evidence
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path


class Report:
    def __init__(self):
        self.limbs = []

    def add(self, name, status, detail, would_fail=None, data=None):
        assert status in ("PASS", "FAIL", "NOT EXERCISED")
        self.limbs.append({"limb": name, "status": status, "detail": detail,
                           "would_have_failed_if": would_fail, "data": data})
        print(f"{status:<14} {name}\n               {detail}", flush=True)

    def finish(self, out: Path):
        ok = sum(1 for l in self.limbs if l["status"] == "PASS")
        print(f"\n{ok}/{len(self.limbs)} PASS")
        for l in self.limbs:
            if l["status"] != "PASS":
                print(f"  {l['status']}: {l['limb']}")
        (out / "score.json").write_text(
            json.dumps({"limbs": self.limbs, "passed": ok,
                        "total": len(self.limbs)}, indent=2), encoding="utf-8")
        return 0 if ok == len(self.limbs) else 1


def observe(n_frames: int, interval_s: float, exposure_ms: float, save_dir: Path,
            name: str):
    """Run an observation-only acquisition; return the callback shapes.

    Returns (shapes, first_batched_axes). `shapes[i]` is how many events the
    i-th callback received. Any value above 1 is the engine sequencing.
    """
    from pycromanager import Acquisition, multi_d_acquisition_events

    shapes: list[int] = []
    first_batch_axes: list | None = None

    def pre_hardware_hook_fn(event):
        nonlocal first_batch_axes
        if isinstance(event, list):
            shapes.append(len(event))
            if first_batch_axes is None:
                first_batch_axes = [e.get("axes") for e in event]
        else:
            shapes.append(1)
        return event

    events = multi_d_acquisition_events(
        num_time_points=n_frames, time_interval_s=interval_s)
    with Acquisition(directory=str(save_dir), name=name, show_display=False,
                     pre_hardware_hook_fn=pre_hardware_hook_fn) as acq:
        acq.acquire(events)
    return shapes, first_batch_axes


def frames_before_first_batch(shapes) -> int | None:
    """How many frames were dispatched singly before the first burst."""
    total = 0
    for size in shapes:
        if size > 1:
            return total
        total += size
    return None


def run_engine_limbs(report: Report, out: Path, exposure_ms: float, long_run: bool):
    from microclaw.tools import _sequenced_ms

    save = out / "datasets"
    save.mkdir(parents=True, exist_ok=True)
    observations = {}

    # CONTROL FIRST. If a well-spaced run batches, this engine does not behave
    # the way the whole gate assumes and nothing below means anything.
    try:
        shapes, _ = observe(8, 0.05, exposure_ms, save, "control-50ms")
        observations["50ms"] = shapes
        batched = [s for s in shapes if s > 1]
        report.add("control: 50 ms spacing is NOT batched",
                   "PASS" if not batched else "FAIL",
                   f"callback sizes {shapes}; distinct deadlines "
                   f"{[_sequenced_ms(k, 0.05) for k in range(4)]}...",
                   "any callback receiving a list at 50 ms")
    except Exception:
        report.add("control: 50 ms spacing is NOT batched", "NOT EXERCISED",
                   "the control run raised:\n" + traceback.format_exc(),
                   "an engine that cannot run a plain timelapse")
        return observations

    # The mechanism: every sub-millisecond pair shares a deadline.
    try:
        shapes, axes = observe(8, 0.0001, exposure_ms, save, "collide-100us")
        observations["100us"] = shapes
        deadlines = [_sequenced_ms(k, 0.0001) for k in range(8)]
        batched = max(shapes) if shapes else 0
        report.add("0.0001 s: the engine batches, as the predicate says",
                   "PASS" if batched > 1 else "FAIL",
                   f"callback sizes {shapes}, largest burst {batched}; all eight "
                   f"deadlines are {deadlines}. First burst axes: {axes}",
                   "no callback receiving a list, which would refute the predicate")
    except Exception:
        report.add("0.0001 s: the engine batches, as the predicate says",
                   "NOT EXERCISED", traceback.format_exc(),
                   "the run raising")

    # Short runs at 1 ms are clean -- this is what makes a threshold look right.
    try:
        shapes, _ = observe(8, 0.001, exposure_ms, save, "short-1ms")
        observations["1ms-short"] = shapes
        batched = [s for s in shapes if s > 1]
        report.add("0.001 s over 8 frames is NOT batched",
                   "PASS" if not batched else "FAIL",
                   f"callback sizes {shapes}. This is the observation a '1 ms is "
                   "safe' rule would be built on, and the next limb is why it "
                   "would be wrong.",
                   "a burst in a short 1 ms run")
    except Exception:
        report.add("0.001 s over 8 frames is NOT batched", "NOT EXERCISED",
                   traceback.format_exc(), "the run raising")

    # design/77b, from the Java side: at exactly zero there is no deadline at all.
    try:
        shapes, _ = observe(8, 0.0, exposure_ms, save, "zero-interval")
        observations["zero"] = shapes
        report.add("interval_s = 0 is batched (no min_start_time is emitted)",
                   "PASS" if max(shapes) > 1 else "FAIL",
                   f"callback sizes {shapes}; multi_d_acquisition_events emits no "
                   "min_start_time at zero, so every pair is sequencable "
                   "regardless of t-index",
                   "a zero-interval burst arriving one event at a time")
    except Exception:
        report.add("interval_s = 0 is batched (no min_start_time is emitted)",
                   "NOT EXERCISED", traceback.format_exc(), "the run raising")

    # THE CROWN LIMB. Predicted collision at frames 4006/4007 and nowhere earlier.
    if not long_run:
        report.add("0.001 s collides at frames 4006/4007", "NOT EXERCISED",
                   "skipped: pass --long-run to spend the ~4008-frame acquisition "
                   "this limb needs. Without it the gate does NOT confirm the "
                   "block's central claim that no threshold is safe.",
                   "skipping the long run")
        return observations

    predicted = next(k for k in range(200000)
                     if _sequenced_ms(k, 0.001) == _sequenced_ms(k + 1, 0.001))
    try:
        shapes, axes = observe(predicted + 2, 0.001, exposure_ms, save, "long-1ms")
        observations["1ms-long"] = shapes
        before = frames_before_first_batch(shapes)
        if max(shapes) <= 1:
            report.add(f"0.001 s collides at frames {predicted}/{predicted + 1}",
                       "FAIL",
                       f"{predicted + 2} frames dispatched with no burst at all. "
                       "The arithmetic predicts a collision; this engine did not "
                       "produce one, so the prediction does not transfer to it.",
                       "no burst anywhere in the long run")
        else:
            hit = before == predicted
            report.add(f"0.001 s collides at frames {predicted}/{predicted + 1}",
                       "PASS" if hit else "FAIL",
                       f"first burst after {before} single frames; predicted "
                       f"{predicted}. Largest burst {max(shapes)}. First burst "
                       f"axes: {axes}. "
                       + ("Observed exactly where double rounding puts it, which "
                          "is why this block states a predicate and refuses to "
                          "state a threshold."
                          if hit else
                          "The engine batched somewhere else, so the model is "
                          "incomplete -- report this rather than adjusting it."),
                       "a first burst anywhere other than the predicted frame")
    except Exception:
        report.add(f"0.001 s collides at frames {predicted}/{predicted + 1}",
                   "NOT EXERCISED", traceback.format_exc(), "the long run raising")
    return observations


def run_refusal_limbs(report: Report, out: Path, tmp: Path):
    """The product half: the refusal fires at plan time, before any exposure."""
    from unittest.mock import MagicMock
    from microclaw import tools
    from microclaw.safety import SafetyConstraints, SafetyGuard

    class StrVector:
        """A Micro-Manager StrVector, which is NOT Python-iterable.

        CLAUDE.md records that a bridge collection answers size()/get(i) and
        that iterating one raises -- and that a MagicMock hides this by handing
        back Python-friendly objects. The envelope validation below reads the
        driver's allowed values through exactly such a collection, so the fake
        has to be this shape or the limb is testing the mock.
        """

        def __init__(self, values):
            self._values = list(values)

        def size(self):
            return len(self._values)

        def get(self, index):
            return self._values[index]

        def __iter__(self):
            raise TypeError("'mmcorej_StrVector' object is not iterable")

    ctrl = MagicMock()
    ctrl.core.get_image_width.return_value = 2
    ctrl.core.get_image_height.return_value = 2
    ctrl.core.get_bytes_per_pixel.return_value = 2
    ctrl.core.get_exposure.return_value = 1
    ctrl.core.get_allowed_property_values.return_value = StrVector(["0", "1"])
    ctrl.core.get_property.return_value = "0"
    guard = SafetyGuard(SafetyConstraints())
    guard.resolve_in_workspace = lambda path: path

    constructed = {"count": 0}
    real_acquisition = tools.Acquisition
    real_confirm = tools.CONFIRM_FN
    confirmations = []

    def auto_confirm(message, **kwargs):
        """Answer the product's hook-hardware-control confirmation.

        This is a real confirmation and it is right that the product asks: the
        operator is authorizing a hook to write hardware. Answering it here
        moves no hardware -- `ctrl` is a mock and `Acquisition` is counted and
        raises -- and the alternative is a gate that stops for a human in the
        middle of a scripted run. The prompts are recorded and reported.
        """
        confirmations.append(message.splitlines()[0] if message else "")
        return True

    def counting(*a, **k):
        constructed["count"] += 1
        raise RuntimeError("Acquisition constructed")

    # One entry per frame: the plan's indices must be exactly 0..n-1.
    plan = [{"hook_event_index": index,
             "actions": [{"kind": "SetDeviceProperty", "value": "1"}]}
            for index in range(5)]
    # A hook_action_plan REQUIRES its envelope. Round 1 of this gate omitted it,
    # so the well-spaced control died on
    # "hook_action_plan requires named_stage_envelope or property_envelope"
    # one step BEFORE the acquisition -- and still passed, because the limb only
    # asked whether the sequencing refusal fired. It had not, for the wrong
    # reason. Supply the envelope so the control genuinely reaches the engine.
    envelope = {"device": "D", "property": "P", "allowed_values": ["1"],
                "max_writes": 8, "restore": "entry"}
    try:
        tools.Acquisition = counting
        tools.CONFIRM_FN = auto_confirm
        # Colliding: must refuse, and must not construct an Acquisition.
        error = None
        try:
            tools.run_timelapse(ctrl, guard, 5, 0.0001, str(tmp),
                                hook_action_plan=plan, property_envelope=envelope)
        except Exception as exc:
            error = exc
        refused = isinstance(error, ValueError) and "truncated millisecond" in str(error)
        report.add("a colliding hardware-control run is refused at plan time",
                   "PASS" if refused and constructed["count"] == 0 else "FAIL",
                   f"error={type(error).__name__ if error else None}: "
                   f"{str(error)[:200]!r}; acquisitions constructed "
                   f"{constructed['count']}",
                   "an Acquisition being constructed, or a different error")

        # Well-spaced: must NOT refuse. A refusal that always fires is not a
        # criterion, and a plain run must keep working.
        constructed["count"] = 0
        error = None
        try:
            tools.run_timelapse(ctrl, guard, 5, 0.05, str(tmp),
                                hook_action_plan=plan, property_envelope=envelope)
        except Exception as exc:
            error = exc
        sequencing_refusal = (isinstance(error, ValueError)
                              and "truncated millisecond" in str(error))
        # PASS requires the run to have REACHED the engine, not merely to have
        # avoided this one refusal. "The sequencing refusal did not fire" is
        # also true of a run that died earlier for an unrelated reason, and a
        # control that cannot distinguish those is not a control.
        reached = constructed["count"] > 0
        report.add("a well-spaced hardware-control run is NOT refused",
                   "PASS" if reached and not sequencing_refusal else "FAIL",
                   f"reached the acquisition ({constructed['count']} constructed)"
                   if reached and not sequencing_refusal else
                   f"did NOT reach the acquisition ({constructed['count']} "
                   f"constructed); stopped by "
                   f"{type(error).__name__ if error else 'nothing'}: "
                   f"{str(error)[:200]!r}",
                   "the sequencing refusal firing on distinct deadlines, OR the "
                   "run failing to reach the acquisition at all")
        report.add("the run asked to authorize hook hardware control",
                   "PASS" if confirmations else "FAIL",
                   f"confirmations raised: {confirmations}" if confirmations else
                   "no confirmation was requested for a run that authorizes a "
                   "hook to write a device property; this block must not have "
                   "removed one",
                   "a hardware-control run authorizing itself silently")
    finally:
        tools.Acquisition = real_acquisition
        tools.CONFIRM_FN = real_confirm


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--exposure-ms", type=float, default=1.0)
    ap.add_argument("--long-run", action="store_true",
                    help="spend the ~4008-frame acquisition for the 4006/4007 limb")
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)

    report = Report()
    observations = run_engine_limbs(report, args.out, args.exposure_ms,
                                    args.long_run)
    (args.out / "callback-shapes.json").write_text(
        json.dumps({k: v for k, v in observations.items()}, indent=2),
        encoding="utf-8")
    run_refusal_limbs(report, args.out, args.out / "refusal-tmp")
    return report.finish(args.out)


if __name__ == "__main__":
    sys.exit(main())
