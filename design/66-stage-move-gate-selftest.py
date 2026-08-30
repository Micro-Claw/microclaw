"""Bridge-shaped end-to-end self-test for block 66's scorer and command sheet."""
from __future__ import annotations
import importlib.util, json, subprocess, sys, tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "design" / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


scorer = load("block66_scorer", "66-stage-move-gate-scorer.py")
sheet = load("block66_sheet", "66-stage-move-command-sheet.py")


class QuantizedBridgeStage:
    """The device quantizes successful landings; it does not inspect scorer rules."""
    def __init__(self, start=150.0, powered=True, exact=False):
        self.position = float(start)
        self.powered = powered
        self.exact = exact

    def get_position(self, device=None): return self.position
    def get_focus_device(self): return "Z"
    def device_busy(self, device): return False
    def set_position(self, device, target=None):
        if target is None: target = device
        if not self.powered:
            return
        self.position = float(target) if self.exact else float(target) - 1.1


def live_result(core, target):
    from microclaw.controller import StageMoveError
    from microclaw.safety import NamedStageLimits, SafetyConstraints, SafetyGuard
    from microclaw.tools import move_named_stage
    from types import SimpleNamespace
    guard = SafetyGuard(SafetyConstraints(named_stages=[
        NamedStageLimits("TIRF Stage", -1000, 1000)
    ]))
    try:
        return move_named_stage(SimpleNamespace(core=core), guard,
                                "TIRF Stage", target)
    except StageMoveError as exc:
        return {"device": "TIRF Stage", **exc.result}


def score_case(root, name, mode, result, exit_code, traceback=""):
    history = root / f"{name}.jsonl"
    emitted = root / f"{name}.py"
    capture = root / f"{name}.txt"
    log = root / f"{name}.log"
    history.write_text(json.dumps({"tool": "move_named_stage", "result": result}) + "\n",
                       encoding="utf-8")
    emitted.write_text("# selected TIRF Stage move\ntarget = 199.9\n", encoding="utf-8")
    capture.write_text(f"{traceback}\nEXIT_CODE={exit_code}\n", encoding="utf-8-sig")
    rc = scorer.main(["--history", str(history), "--emitted", str(emitted),
                      "--capture", str(capture), "--log", str(log),
                      "--device", "TIRF Stage", "--target", "199.9",
                      "--mode", mode])
    if rc != 0:
        raise AssertionError(f"{name} should pass its independent limbs:\n{log.read_text()}")


def main():
    from microclaw import controller
    controller.STAGE_MOVE_POLL_S = 0.0
    controller.STAGE_MOVE_STABILITY_WINDOW_S = 0.0
    controller.STAGE_MOVE_TIMEOUT_S = 0.002
    with tempfile.TemporaryDirectory(prefix="block66-selftest-") as raw:
        root = Path(raw)
        score_case(root, "m2", "m2-success",
                   {"device": "TIRF Stage", **live_result(
                       QuantizedBridgeStage(start=150), 199.9)}, 0)
        score_case(root, "demo", "demo",
                   {"device": "TIRF Stage", **live_result(
                       QuantizedBridgeStage(start=150, exact=True), 199.9)}, 0)
        score_case(root, "control", "m2-control",
                   {"device": "TIRF Stage", **live_result(
                       QuantizedBridgeStage(start=150, powered=False), 199.9)},
                   0)

        empty = root / "empty"
        empty.write_text("", encoding="utf-8")
        valid = root / "valid"
        valid.write_text("x", encoding="utf-8")
        rc = scorer.main(["--history", str(empty), "--emitted", str(valid),
                          "--capture", str(valid), "--log", str(root / "negative.log"),
                          "--device", "TIRF Stage", "--target", "199.9",
                          "--mode", "m2-success"])
        if rc == 0 or "NOT EXERCISED" not in (root / "negative.log").read_text():
            raise AssertionError("empty artifacts did not fail closed as NOT EXERCISED")

        rendered = sheet.render(Path(sys.executable), root / "m2.py", root / "capture.txt")
        assert "«" not in rendered and "»" not in rendered
        assert "ForEach-Object { $_.ToString() }" in rendered
        assert "Out-String -Width 4096" in rendered
        assert rendered.count("Set-Content") == 1 and "-Encoding UTF8" in rendered
        assert str(Path(sys.executable).resolve()) in rendered
    print("BLOCK 66 BRIDGE-SHAPED SELF-TEST PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
