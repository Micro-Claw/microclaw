"""Bridge-shaped end-to-end self-test for block 66's scorer and command sheet.

Two rules this file exists to obey. The history fixtures are built by driving
``execute_tool`` -- the boundary that actually writes a ``tool_result`` -- and
wrapping its output in the message shape ``AuditLog.append`` records, so the
scorer is tested against the product's transcript rather than against the
scorer author's idea of one. And the emitted script is produced by
``export_session_script``, not stubbed, so the emitted-move limb is scored on
real exporter output. An earlier version wrote both fixtures itself and passed
while the scorer could not read a single real rig history.
"""
from __future__ import annotations
import contextlib, importlib.util, io, json, sys, tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FAILURES: list[str] = []


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "design" / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


scorer = load("block66_scorer", "66-stage-move-gate-scorer.py")
sheet = load("block66_sheet", "66-stage-move-command-sheet.py")


class QuantizedBridgeStage:
    """The device chooses its own landing; it does not inspect the scorer's rules.

    ``powered=False`` reproduces a controller that accepts the write and does
    nothing (the stationary-axis control); ``raises=True`` reproduces one whose
    adapter refuses the write with a multi-line Java message (the dispatch
    control). Both are the rig behaviours the control limb must recognise.
    """

    def __init__(self, start=150.0, powered=True, exact=False, raises=False):
        self.position = float(start)
        self.powered, self.exact, self.raises = powered, exact, raises

    def get_position(self, device=None):
        return self.position

    def get_focus_device(self):
        return "Z"

    def device_busy(self, device):
        return False

    def set_position(self, device, target=None):
        if target is None:
            target = device
        if self.raises:
            raise RuntimeError(
                "Error in device Adapter: TIRF Stage\n"
                "  at mmcorej.CMMCore.setPosition(CMMCore.java:1)\n"
                "  at java.base/java.lang.Thread.run(Thread.java:833)"
            )
        if self.powered:
            self.position = float(target) if self.exact else float(target) - 1.1


def guard_for(device="TIRF Stage"):
    from microclaw.safety import (NamedStageLimits, SafetyConstraints, SafetyGuard,
                                  StageConstraints)
    return SafetyGuard(SafetyConstraints(
        stage=StageConstraints(z_min=-1000, z_max=1000),
        named_stages=[NamedStageLimits(device, -1000, 1000)]))


def recorded_call(core, device, target, sequence=0, tool="move_named_stage"):
    """Drive the registered tool through ``execute_tool`` and return the exact
    ``tool_use``/``tool_result`` pair the transcript carries.

    These two messages are also, unchanged, what ``export_session_script`` takes
    as its ``records`` -- the transcript is the record -- so one fixture serves
    the history parser and the exporter, and neither is a restatement of the
    other."""
    from microclaw.tools import execute_tool
    arguments = ({"device": device, "um": target, "absolute": True}
                 if tool == "move_named_stage"
                 else {"z_um": target, "absolute": True})
    content = execute_tool(tool, dict(arguments),
                           SimpleNamespace(core=core), guard_for(device))
    identifier = f"toolu_selftest_{sequence:04d}"
    return [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": identifier,
             "name": tool, "input": arguments}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": identifier, "content": content}]},
    ]


def write_jsonl(path: Path, messages) -> Path:
    path.write_text("".join(json.dumps(row) + "\n" for row in messages),
                    encoding="utf-8")
    return path


def check(name: str, condition: bool, detail: str = "") -> None:
    """Every limb reports independently; one failure never hides the next."""
    print(f"  {name}: {'OK' if condition else 'FAILED'}{(' — ' + detail) if detail else ''}")
    if not condition:
        FAILURES.append(name)


def run_scorer(root: Path, name: str, argv: list[str]) -> tuple[int, str]:
    """The scorer's own report goes to its log; only this file's limbs print."""
    log = root / f"{name}.log"
    with contextlib.redirect_stdout(io.StringIO()):
        code = scorer.main(argv + ["--log", str(log)])
    return code, log.read_text(encoding="utf-8")


def main():
    from microclaw import controller
    controller.STAGE_MOVE_POLL_S = 0.0
    controller.STAGE_MOVE_STABILITY_WINDOW_S = 0.0
    controller.STAGE_MOVE_TIMEOUT_S = 0.002
    device, target = "TIRF Stage", 199.9

    with tempfile.TemporaryDirectory(prefix="block66-selftest-") as raw:
        root = Path(raw)

        # --- the M2 success limb, scored end to end on real exporter output ---
        messages = recorded_call(QuantizedBridgeStage(start=150.0), device, target)
        history = write_jsonl(root / "m2.jsonl", messages)
        from microclaw.tools import export_session_script
        script = root / "m2_routine.py"
        export_session_script(SimpleNamespace(core=None), guard_for(device),
                              str(script), messages,
                              tool_use_ids=["toolu_selftest_0000"])
        capture = root / "m2.txt"
        capture.write_text("\nEXIT_CODE=0\n", encoding="utf-8-sig")
        code, log = run_scorer(root, "m2", [
            "--history", str(history), "--emitted", str(script),
            "--capture", str(capture), "--device", device,
            "--target", str(target), "--mode", "m2-success"])
        check("m2 success limb passes on real product artifacts", code == 0,
              "" if code == 0 else log.strip())

        # The same history saved as a JSON array is the artifact operators mail
        # back; it must score identically.
        saved = root / "m2_saved.json"
        saved.write_text(json.dumps(list(messages)), encoding="utf-8")
        code, _ = run_scorer(root, "m2-saved", [
            "--history", str(saved), "--emitted", str(script),
            "--capture", str(capture), "--device", device,
            "--target", str(target), "--mode", "m2-success"])
        check("saved JSON-array history scores identically", code == 0)

        # A tool_result whose content is a list of blocks is 36 of 426 real
        # results; it must not be silently unreadable.
        listed = [messages[0], {"role": "user", "content": [{
            "type": "tool_result", "tool_use_id": "toolu_selftest_0000",
            "content": [{"type": "text",
                         "text": messages[1]["content"][0]["content"]}]}]}]
        code, _ = run_scorer(root, "m2-list", [
            "--history", str(write_jsonl(root / "m2_list.jsonl", listed)),
            "--emitted", str(script), "--capture", str(capture),
            "--device", device, "--target", str(target), "--mode", "m2-success"])
        check("list-shaped tool_result content is read", code == 0)

        # A whole-session export cannot satisfy the selection limb, however
        # well its move scores.
        whole = root / "whole_session.py"
        export_session_script(SimpleNamespace(core=None), guard_for(device),
                              str(whole), messages)
        code, log = run_scorer(root, "negative-unselected", [
            "--history", str(history), "--emitted", str(whole),
            "--capture", str(capture), "--device", device,
            "--target", str(target), "--mode", "m2-success"])
        check("an unselected export is NOT EXERCISED",
              code != 0 and "tool_use_ids was not exercised" in log)

        # --- the demo regression limb: exact instant arrival ---
        demo = recorded_call(QuantizedBridgeStage(start=150.0, exact=True),
                             device, target)
        code, log = run_scorer(root, "demo", [
            "--history", str(write_jsonl(root / "demo.jsonl", demo)),
            "--emitted", str(script), "--capture", str(capture),
            "--device", device, "--target", str(target), "--mode", "demo"])
        check("demo instant-arrival limb passes", code == 0,
              "" if code == 0 else log.strip())

        # The demo machine may declare no named stage; the runbook's fallback
        # limb runs on the core focus axis, so it is executed here rather than
        # only written down.
        focus = recorded_call(QuantizedBridgeStage(start=150.0, exact=True),
                              device, target, tool="move_stage_z")
        focus_script = root / "focus_routine.py"
        export_session_script(SimpleNamespace(core=None), guard_for(device),
                              str(focus_script), focus,
                              tool_use_ids=["toolu_selftest_0000"])
        code, log = run_scorer(root, "demo-focus", [
            "--history", str(write_jsonl(root / "focus.jsonl", focus)),
            "--emitted", str(focus_script), "--capture", str(capture),
            "--tool", "move_stage_z", "--target", str(target), "--mode", "demo"])
        check("demo fallback on the core focus axis passes", code == 0,
              "" if code == 0 else log.strip())

        # --- the two control shapes, from refusals the product actually wrote ---
        for name, stage in (("stationary", QuantizedBridgeStage(start=150.0, powered=False)),
                            ("dispatch", QuantizedBridgeStage(start=150.0, raises=True))):
            control = recorded_call(stage, device, target)
            code, log = run_scorer(root, f"control-{name}", [
                "--history", str(write_jsonl(root / f"control_{name}.jsonl", control)),
                "--device", device, "--target", str(target), "--mode", "m2-control"])
            check(f"{name} control limb passes", code == 0,
                  "" if code == 0 else log.strip())

        # M2's actual control, 2026-08-30: a disconnected controller fails every
        # bridge call, so the pre-dispatch read raises before the write is
        # attempted. The gate found this reported as an untyped exception.
        class LinkDownStage(QuantizedBridgeStage):
            def get_position(self, device=None):
                raise RuntimeError(
                    'java.lang.Exception: Error in device "TIRF Stage": '
                    "Serial command failed.  Is the device connected to the "
                    "serial port? (14)")

        link_down = recorded_call(LinkDownStage(), device, target)
        code, log = run_scorer(root, "control-linkdown", [
            "--history", str(write_jsonl(root / "control_linkdown.jsonl", link_down)),
            "--device", device, "--target", str(target), "--mode", "m2-control"])
        check("link-down control limb passes", code == 0,
              "" if code == 0 else log.strip())

        # A control that MOVED is the negative this limb exists to reject.
        moved = recorded_call(QuantizedBridgeStage(start=150.0), device, target)
        code, log = run_scorer(root, "control-moved", [
            "--history", str(write_jsonl(root / "control_moved.jsonl", moved)),
            "--device", device, "--target", str(target), "--mode", "m2-control"])
        check("a responsive axis fails the control limb", code != 0 and "FAIL" in log)

        # --- negatives: nothing may pass on absent, stale or foreign evidence ---
        empty = root / "empty"
        empty.write_text("", encoding="utf-8")
        code, log = run_scorer(root, "negative-empty", [
            "--history", str(empty), "--emitted", str(empty),
            "--capture", str(empty), "--device", device,
            "--target", str(target), "--mode", "m2-success"])
        check("empty artifacts fail closed", code != 0 and scorer.NE in log)

        legacy = [messages[0], {"role": "user", "content": [{
            "type": "tool_result", "tool_use_id": "toolu_selftest_0000",
            "content": json.dumps({
                "device": device, "requested_um": target, "measured_um": 198.8,
                "tolerance_um": 0.5, "within_tolerance": True, "elapsed_s": 0.4,
                "last_device_status": "idle"})}]}]
        code, log = run_scorer(root, "negative-legacy", [
            "--history", str(write_jsonl(root / "legacy.jsonl", legacy)),
            "--emitted", str(script), "--capture", str(capture),
            "--device", device, "--target", str(target), "--mode", "m2-success"])
        check("a pre-block-66 record cannot pass", code != 0 and "missing" in log)

        old_shape = [{"tool": "move_named_stage", "result": {
            "device": device, "requested_um": target, "start_um": 150.0}}]
        code, log = run_scorer(root, "negative-invented", [
            "--history", str(write_jsonl(root / "invented.jsonl", old_shape)),
            "--emitted", str(script), "--capture", str(capture),
            "--device", device, "--target", str(target), "--mode", "m2-success"])
        check("the invented {tool, result} row matches nothing",
              code != 0 and "found 0" in log)

        code, log = run_scorer(root, "negative-target", [
            "--history", str(history), "--emitted", str(script),
            "--capture", str(capture), "--device", device,
            "--target", "150.0", "--mode", "m2-success"])
        check("a different target matches no call", code != 0 and "found 0" in log)

        # A capture from a run that never reached a move is NOT EXERCISED, and a
        # missing trailer is never inferred from the absence of a traceback.
        broken = root / "broken.txt"
        broken.write_text("ModuleNotFoundError: pycromanager\nEXIT_CODE=1\n",
                          encoding="utf-8-sig")
        code, log = run_scorer(root, "negative-capture", [
            "--history", str(history), "--emitted", str(script),
            "--capture", str(broken), "--device", device,
            "--target", str(target), "--mode", "m2-success"])
        check("a pre-move standalone failure is NOT EXERCISED",
              code != 0 and "failed before a typed stage-move decision" in log)

        # --- the generated command sheet ---
        rendered = sheet.render(Path(sys.executable), script, root / "capture.txt")
        check("sheet resolves every placeholder",
              "«" not in rendered and "»" not in rendered)
        check("sheet normalises ErrorRecords before Out-String",
              "ForEach-Object { $_.ToString() }" in rendered)
        check("sheet pins the width", "Out-String -Width 4096" in rendered)
        check("sheet writes once, as UTF-8",
              rendered.count("Set-Content") == 1 and "-Encoding UTF8" in rendered)
        check("sheet quotes literal resolved paths",
              f"'{Path(sys.executable).resolve()}'" in rendered)

    if FAILURES:
        print(f"BLOCK 66 BRIDGE-SHAPED SELF-TEST FAILED: {len(FAILURES)} limb(s): "
              + ", ".join(FAILURES))
        return 1
    print("BLOCK 66 BRIDGE-SHAPED SELF-TEST PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
