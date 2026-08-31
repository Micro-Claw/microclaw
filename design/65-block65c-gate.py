"""Block 65c computed gate: adaptive single-field timelapse contracts.

This program uses a bridge-shaped, non-iterable Core fake and real Microclaw
hook resolution, acquisition routing, and export code.  It never connects to a
microscope.  Every limb is independent, writes its own evidence, and reports
PASS / FAIL / NOT EXERCISED.  NOT EXERCISED is never a pass.

    uv run python design/65-block65c-gate.py --out gate65c
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import queue
import sys
import tempfile
import traceback
import types
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import numpy as np

# A script under design/ otherwise resolves the editable install (the primary
# checkout), not the tree containing this gate.  Selftest deliberately replaces
# this entry when it drives the same program against the other tree.
TREE_ROOT = Path(os.environ.get(
    "MICROCLAW_GATE_TREE", Path(__file__).resolve().parent.parent
)).resolve()
sys.path.insert(0, str(TREE_ROOT))

RESULTS: list[dict] = []


def limb(name, mechanism):
    def decorate(fn):
        fn._limb = (name, mechanism)
        return fn
    return decorate


def record(name, mechanism, status, detail):
    RESULTS.append({"limb": name, "mechanism": mechanism,
                    "status": status, "detail": detail})
    print(f"[{status:<13}] {name}\n                {detail}\n", flush=True)


class BridgeVector:
    """The Java collection shape: indexed, deliberately not iterable."""

    def __init__(self, values):
        self._values = list(values)

    def size(self):
        return len(self._values)

    def get(self, index):
        return self._values[index]

    def __iter__(self):
        raise TypeError("'mmcorej_StrVector' object is not iterable")


class GateCore:
    def __init__(self):
        self.exposures = 0
        self.mutations = []
        self.properties = {("GateDevice", "GateProperty"): "1000"}

    def get_image_width(self): return 8
    def get_image_height(self): return 6
    def get_bytes_per_pixel(self): return 2
    def get_loaded_devices(self): return BridgeVector(["Camera", "GateDevice"])
    def get_available_configs(self, group): return BridgeVector(["DAPI"])
    def get_channel_group(self): return "Channel"
    def get_exposure(self): return 50.0
    def set_exposure(self, value): self.mutations.append(("exposure", value))
    def get_property(self, device, prop): return self.properties[(device, prop)]
    def get_property_type(self, device, prop): return "String"
    def get_allowed_property_values(self, device, prop):
        return BridgeVector(["1000", "1010", "1020"])
    def set_property(self, device, prop, value):
        self.mutations.append((device, prop, str(value)))
        self.properties[(device, prop)] = str(value)
    def wait_for_device(self, device): self.mutations.append(("wait", device))


class GateGuard:
    def __init__(self, root):
        self.root = Path(root)
        self.resolved = []
        stage = SimpleNamespace(
            x_min=-1000.0, x_max=1000.0, y_min=-1000.0, y_max=1000.0,
            z_min=0.0, z_max=1000.0,
        )
        camera = SimpleNamespace(max_exposure_ms=1000.0)
        self._c = SimpleNamespace(stage=stage, camera=camera, named_stages=[])
        self.constraints = self._c

    @property
    def analysis_min_snr(self): return None

    def resolve_in_workspace(self, path):
        self.resolved.append(str(path))
        candidate = Path(path)
        return str(candidate if candidate.is_absolute() else self.root / candidate)

    def check_exposure(self, value): return None
    def check_device_property(self, *args, **kwargs): return None
    def check_illumination(self, *args, **kwargs): return None
    def check_xy(self, *args): return None
    def check_z(self, *args): return None


class GateReservation:
    def __init__(self, plan):
        self.plan = plan
        self.completed_frames = 0
        self.overrun_frames = 0
        self.closed = 0

    @property
    def has_overrun(self): return bool(self.overrun_frames)

    def commit_frame(self):
        if self.completed_frames >= self.plan.frames:
            self.overrun_frames += 1
            return False
        self.completed_frames += 1
        return True

    def close(self): self.closed += 1


class GateAcquisition:
    """Synchronous engine fake, accepting only one event from each handoff."""

    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self._dataset_disk_location = str(Path(kwargs["directory"]) / kwargs["name"])
        self._event_queue = queue.Queue()
        self._acq = SimpleNamespace(is_finished=lambda: False)
        self._exception = None
        self.events = []
        type(self).instances.append(self)

    def acquire(self, events):
        for event in events:
            self.events.append(dict(event))
            pre = self.kwargs.get("pre_hardware_hook_fn")
            if pre: pre(event)
            # Distinct image-derived measurements; no chosen value is in source.
            value = 11.125 + len(self.events) * 7.25
            image = np.full((2, 2), value, dtype=np.float32)
            metadata = {"Axes": dict(event.get("axes") or {})}
            process = self.kwargs.get("image_process_fn")
            if process: process(image, metadata, None)
            saved = self.kwargs.get("image_saved_fn")
            if saved: saved(event.get("axes") or {}, None)

    def __enter__(self): return self
    def __exit__(self, *args): return None


@contextmanager
def product_sandbox(out):
    from microclaw import hook_manager, tools

    original = {
        "HOOKS_DIR": hook_manager.HOOKS_DIR,
        "MANIFEST": hook_manager.MANIFEST,
        "Acquisition": tools.Acquisition,
        "authorize": tools._authorize_acquisition,
        "confirm": tools.CONFIRM_FN,
    }
    hooks = out / "hooks"
    hook_manager.HOOKS_DIR = hooks
    hook_manager.MANIFEST = hooks / "manifest.json"
    GateAcquisition.instances.clear()
    tools.Acquisition = GateAcquisition
    reservations = []

    def authorize(ctrl, guard, plan):
        reservation = GateReservation(plan)
        reservations.append(reservation)
        return reservation

    tools._authorize_acquisition = authorize
    tools.CONFIRM_FN = lambda *args, **kwargs: True
    try:
        yield tools, hook_manager, reservations
    finally:
        hook_manager.HOOKS_DIR = original["HOOKS_DIR"]
        hook_manager.MANIFEST = original["MANIFEST"]
        tools.Acquisition = original["Acquisition"]
        tools._authorize_acquisition = original["authorize"]
        tools.CONFIRM_FN = original["confirm"]


def save_adaptive_hook(manager, name="gate_adaptive"):
    code = (
        "from microclaw.hook_decisions import ContinueAcquisition, HookResult, StopAcquisition\n"
        "class GateAdaptive:\n"
        "    def analyze_frame(self, image, metadata):\n"
        "        index = metadata['Axes']['time']\n"
        "        measured = float(image.mean())\n"
        "        route = ContinueAcquisition() if index < 2 else StopAcquisition()\n"
        "        return HookResult({'chosen_target': measured}, (route,))\n"
    )
    manager.save_hook(name, code, "block 65c gate", "user_provided")
    return code


def call_record(name, params, result=None, tool_id=None):
    tool_id = tool_id or f"gate65c-{name}"
    records = [{"role": "assistant", "content": [{
        "type": "tool_use", "id": tool_id, "name": name, "input": params,
    }]}]
    if result is not None:
        records.append({"role": "user", "content": [{
            "type": "tool_result", "tool_use_id": tool_id,
            "content": json.dumps(result, default=str),
        }]})
    return records


def adaptive_run_and_export(out):
    from microclaw import tools

    ctrl = SimpleNamespace(core=GateCore(), refresh_gui=lambda: None)
    guard = GateGuard(out)
    params = {
        "n_frames": None, "max_frames": 5, "interval_s": 0,
        "save_dir": "adaptive-data", "name": "gate-adaptive",
        "hook_strategy": "gate_adaptive", "exposure_ms": 50,
        "log_path": "gate-adaptive-hook.jsonl",
        "property_envelope": {
            "device": "GateDevice", "property": "GateProperty",
            "allowed_values": ["1000", "1010", "1020"],
            "max_writes": 2, "restore": "entry",
        },
    }
    result = tools.run_timelapse(ctrl, guard, **params)
    script = out / "adaptive_export.py"
    export_result = tools.export_session_script(
        ctrl, guard, str(script), call_record("run_timelapse", params, result),
    )
    source = script.read_text(encoding="utf-8")
    return result, export_result, source, ctrl, guard


@limb("A_adaptive_export", "a completed adaptive call emits its successor program")
def limb_adaptive_export(out):
    with product_sandbox(out) as (_, manager, _):
        save_adaptive_hook(manager)
        result, exported, source, _, _ = adaptive_run_and_export(out)
    tree = ast.parse(source)
    runnable = source.replace(
        "from pycromanager import Acquisition, Core, multi_d_acquisition_events\n", ""
    )
    before_exec = len(GateAcquisition.instances)
    real_pycromanager = sys.modules.get("pycromanager")
    fake_pycromanager = types.ModuleType("pycromanager")
    fake_pycromanager.Studio = lambda: SimpleNamespace(
        app=lambda: SimpleNamespace(refresh_gui_from_cache=lambda: None)
    )
    sys.modules["pycromanager"] = fake_pycromanager
    try:
        exec(compile(runnable, str(out / "adaptive_export.py"), "exec"), {
            "__file__": str(out / "adaptive_export.py"),
            "Core": GateCore,
            "Acquisition": GateAcquisition,
            "multi_d_acquisition_events": lambda **kwargs: [{"axes": {"time": 0}}],
        })
    finally:
        if real_pycromanager is None:
            sys.modules.pop("pycromanager", None)
        else:
            sys.modules["pycromanager"] = real_pycromanager
    executed = GateAcquisition.instances[before_exec:]
    executed_axes = ([event.get("axes") for event in executed[-1].events]
                     if executed else [])
    failures = []
    restore_tries = [node for node in tree.body if isinstance(node, ast.Try)
                     and any(isinstance(child, ast.With) for child in ast.walk(node))]
    restore_try = restore_tries[-1] if restore_tries else None
    handler_restores = bool(restore_try and any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "_restore_hardware"
        for handler in restore_try.handlers for node in ast.walk(handler)
    ))
    success_restores = False
    if restore_try is not None:
        position = tree.body.index(restore_try)
        success_restores = any(
            isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "_restore_hardware"
            for statement in tree.body[position + 1:] for node in ast.walk(statement)
        )
    checks = {
        "standalone": "import microclaw" not in source,
        "successor": "def _successor(index):" in source,
        "configured successor": "successor=_successor" in source,
        "routing required": "require_routing_decision=True" in source,
        "cap stream": "max_events=5" in source,
        "cap progress": "SurveyProgress(5)" in source,
        "envelope printed": "print(" in source and "envelope" in source.lower(),
        "no prompt": "input(" not in source and "CONFIRM" not in source,
        "with exists": any(isinstance(node, ast.With) for node in ast.walk(tree)),
        "executes dense stop trace": executed_axes == [
            {"time": 0}, {"time": 1}, {"time": 2}
        ],
        "property restorer registered": "('property', 'restore_property'" in source,
        "restore after with on success": success_restores,
        "restore after with on exception": handler_restores,
    }
    failures = [name for name, passed in checks.items() if not passed]
    evidence = {"result": result, "export": exported, "checks": checks,
                "line_count": len(source.splitlines())}
    (out / "adaptive_export.json").write_text(
        json.dumps(evidence, indent=2, default=str), encoding="utf-8")
    if failures:
        return "FAIL", f"adaptive export failed structural checks: {failures}"
    return "PASS", (f"run stopped by {result.get('stop_reason')}; emitted "
                    f"{exported['emitted_calls']} call in {len(source.splitlines())} parsed lines")


@limb("B_rule_not_trace", "the export contains the decision loop, not run-chosen values")
def limb_rule_not_trace(out):
    with product_sandbox(out) as (_, manager, _):
        save_adaptive_hook(manager)
        _, _, source, _, _ = adaptive_run_and_export(out)
        acquired = len(GateAcquisition.instances[-1].events)
    # These are the actual pixel-derived values supplied to the hook in the
    # driven run, reconstructed from the dependency-shaped fake's frame record.
    chosen = [11.125 + index * 7.25 for index in range(1, acquired + 1)]
    leaked = [value for value in chosen if repr(value) in source or str(value) in source]
    loop = ("_survey_event_stream" in source and "image_process_fn" in source
            and "candidates" in source)
    (out / "rule_not_trace.json").write_text(json.dumps(
        {"chosen_targets": chosen, "leaked": leaked, "decision_loop": loop}, indent=2
    ), encoding="utf-8")
    if not chosen:
        return "NOT EXERCISED", "the driven run produced no image-derived target values"
    if leaked or not loop:
        return "FAIL", f"leaked={leaked}; decision_loop={loop}"
    return "PASS", f"none of {len(chosen)} run-chosen targets occurs in the emitted decision loop"


@limb("C_contract_preflight", "pinned source vocabulary is checked at resolution")
def limb_contract_preflight(out):
    with product_sandbox(out) as (_, manager, _):
        manager.save_hook("legacy_shape", "class Logger:\n    def analyze_frame(self, image, metadata):\n        return None\n", "legacy", "user_provided")
        refused = None
        try:
            manager.load_hook_class("legacy_shape", require_acquisition_decision=True)
        except ValueError as error:
            refused = str(error)
        save_adaptive_hook(manager, "control_hook")
        accepted = manager.load_hook_class("control_hook", require_acquisition_decision=True)
    if refused is None:
        return "FAIL", "source with no routing vocabulary resolved"
    if accepted is None:
        return "FAIL", "control source naming the vocabulary did not resolve"
    return "PASS", f"legacy shape refused before construction; control resolved as {accepted.__name__}"


@limb("D_retired_vocabulary", "both retired source shapes refuse with replacement remedy")
def limb_retired_vocabulary(out):
    shapes = {
        "importing": "from microclaw.hook_decisions import ContinueSurvey\nclass Old:\n    def analyze_frame(self, image, metadata):\n        return ContinueSurvey()\n",
        "bare": "class Old:\n    def analyze_frame(self, image, metadata):\n        return StopSurvey()\n",
    }
    findings = {}
    with product_sandbox(out) as (_, manager, _):
        for name, code in shapes.items():
            manager.save_hook(name, code, "retired", "user_provided")
            try:
                manager.load_hook_class(name, require_acquisition_decision=True)
            except ValueError as error:
                findings[name] = str(error)
            except Exception as error:
                findings[name] = f"WRONG TYPE {type(error).__name__}: {error}"
            else:
                findings[name] = "ACCEPTED"
    (out / "retired_vocabulary.json").write_text(json.dumps(findings, indent=2), encoding="utf-8")
    bad = [name for name, text in findings.items()
           if text == "ACCEPTED" or text.startswith("WRONG TYPE")
           or ("ContinueAcquisition" not in text and "StopAcquisition" not in text)]
    if bad:
        return "FAIL", f"migration refusal/remedy failed for {bad}: {findings}"
    return "PASS", "imported ContinueSurvey and bare StopSurvey both refused at source resolution with replacements"


@limb("E_shape_refusals", "invalid acquisition shapes refuse before paths, events, or hardware")
def limb_shape_refusals(out):
    from microclaw import tools
    ctrl = SimpleNamespace(core=GateCore())
    guard = GateGuard(out)
    touched = []
    original = tools._build_acquisition_events
    tools._build_acquisition_events = lambda **kwargs: touched.append("events") or []
    cases = [
        ({"n_frames": None}, "exactly one"),
        ({"n_frames": 2, "max_frames": 3}, "exactly one"),
        ({"n_frames": None, "max_frames": 3}, "requires hook_strategy"),
        ({"n_frames": None, "max_frames": 3, "hook_strategy": "h", "hook_action_plan": []}, "incompatible"),
    ]
    findings = []
    try:
        for additions, expected in cases:
            before = (len(guard.resolved), len(ctrl.core.mutations), len(touched))
            try:
                tools.run_timelapse(ctrl, guard, interval_s=0, save_dir="forbidden", **additions)
            except ValueError as error:
                message = str(error)
            else:
                message = "NO REFUSAL"
            after = (len(guard.resolved), len(ctrl.core.mutations), len(touched))
            findings.append({"input": additions, "message": message,
                             "untouched": before == after, "expected": expected})
    finally:
        tools._build_acquisition_events = original
    (out / "shape_refusals.json").write_text(json.dumps(findings, indent=2), encoding="utf-8")
    bad = [row for row in findings if row["expected"] not in row["message"] or not row["untouched"]]
    if bad:
        return "FAIL", f"shape refusal ordering failed: {bad}"
    return "PASS", "four invalid shapes refused before path resolution, event construction, and mutation"


@limb("F_existing_routes", "hookless and fixed-hook timelapses retain distinct exports")
def limb_existing_routes(out):
    from microclaw import tools
    ctrl = SimpleNamespace(core=GateCore())
    guard = GateGuard(out)
    with product_sandbox(out) as (_, manager, _):
        manager.save_hook("logger", "class Logger:\n    def analyze_frame(self, image, metadata):\n        return None\n", "logger", "user_provided")
        plain_path = out / "plain.py"
        fixed_path = out / "fixed_hook.py"
        plain = {"n_frames": 2, "interval_s": 0, "save_dir": "plain"}
        fixed = {"n_frames": 2, "interval_s": 1, "save_dir": "fixed", "hook_strategy": "logger"}
        tools.export_session_script(ctrl, guard, str(plain_path), call_record("run_timelapse", plain))
        tools.export_session_script(ctrl, guard, str(fixed_path), call_record("run_timelapse", fixed))
        plain_source = plain_path.read_text(encoding="utf-8")
        fixed_source = fixed_path.read_text(encoding="utf-8")
    checks = {
        "plain acquisition": "acq.acquire(events)" in plain_source,
        "plain not adaptive": "_survey_event_stream" not in plain_source,
        "fixed acquisition": "acq.acquire(events)" in fixed_source,
        "fixed not successor": "def _successor(index):" not in fixed_source,
        "fixed hook retained": "class Logger" in fixed_source,
    }
    bad = [name for name, passed in checks.items() if not passed]
    if bad:
        return "FAIL", f"existing export route checks failed: {bad}"
    return "PASS", "hookless export stayed plain; logging-hook n_frames export stayed fixed-hooked"


LIMBS = [limb_adaptive_export, limb_rule_not_trace, limb_contract_preflight,
         limb_retired_vocabulary, limb_shape_refusals, limb_existing_routes]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="gate65c", help="evidence directory")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    RESULTS.clear()
    print("Block 65c computed gate — no microscope connection or exposure\n")
    for fn in LIMBS:
        name, mechanism = fn._limb
        limb_out = out / name
        limb_out.mkdir(parents=True, exist_ok=True)
        try:
            status, detail = fn(limb_out)
        except Exception as error:  # one limb must never cascade into another
            status, detail = "FAIL", f"{type(error).__name__}: {error}"
            (limb_out / "traceback.txt").write_text(traceback.format_exc(), encoding="utf-8")
        record(name, mechanism, status, detail)
    (out / "gate65c_results.json").write_text(json.dumps(RESULTS, indent=2), encoding="utf-8")
    passed = sum(row["status"] == "PASS" for row in RESULTS)
    failed = [row["limb"] for row in RESULTS if row["status"] == "FAIL"]
    unexercised = [row["limb"] for row in RESULTS if row["status"] == "NOT EXERCISED"]
    print(f"{passed}/{len(RESULTS)} PASS")
    if failed: print("FAIL: " + ", ".join(failed))
    if unexercised: print("NOT EXERCISED (never a pass): " + ", ".join(unexercised))
    print(f"evidence: {out.resolve()}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
