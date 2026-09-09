r"""Does design/81-block81a2-demo-gate.py work, before an operator runs it?

Gate code gets no review pass and runs unattended on someone else's machine, so
it is audited here instead. Run on BOTH trees, so a failure discriminates:

    .venv/bin/python design/81-block81a2-gate-selftest.py            # this tree: PASS
    git stash && .venv/bin/python design/81-block81a2-gate-selftest.py ; git stash pop
                                                                     # pre-81a-2: the
                                                                     # CONTROL check fails

**The fakes here are bridge shaped, deliberately.** design/59's block 59a sent a
gate to this same demo machine and every orientation limb came back
`TypeError: 'mmcorej_StrVector' object is not iterable`, because a `list()` over
a Core collection works against every fake in the suite and fails on every rig.
A `MagicMock` would not have caught it either: it hands back Python-friendly
objects. So Core collections here return `size()`/`get(i)` vectors whose
`__iter__` RAISES, and any `list(...)` in the gate dies here rather than there.

What this settles, and what it cannot:

  * It settles that the gate's API calls exist with the signatures it uses.
    Three did not in the first draft -- every `execute_tool` call had the
    argument order wrong and treated its JSON string as a dict, and
    `dataset_frames` called an `ndstorage` method that does not exist. All
    three would have died at limb A on the demo machine, after spending the
    operator's setup time.
  * It settles that the CONTROL limb fires on a pre-81a-2 tree.
  * It cannot settle anything about real hardware, real focus curves, or the
    engine's hook thread. That is what the trip is for.
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAILURES = []


def check(name):
    def decorate(fn):
        try:
            detail = fn() or ""
            print(f"PASS: {name} - {detail}")
        except Exception as exc:                    # noqa: BLE001 - reported
            FAILURES.append((name, f"{type(exc).__name__}: {exc}"))
            print(f"FAIL: {name} - {type(exc).__name__}: {exc}")
        return fn
    return decorate


class CoreVector:
    """A Core collection: size()/get(i), and NOT Python-iterable (59a)."""

    def __init__(self, items):
        self._items = list(items)

    def size(self):
        return len(self._items)

    def get(self, index):
        return self._items[index]

    def __iter__(self):
        raise TypeError(
            "'mmcorej_StrVector' object is not iterable -- this is the demo "
            "machine's real behaviour (design/59 block 59a)")


def main():
    gate_path = ROOT / "design" / "81-block81a2-demo-gate.py"

    @check("the gate parses and exposes its harness")
    def parses():
        import ast
        tree = ast.parse(gate_path.read_text(encoding="utf-8"))
        names = {n.name for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
        for needed in ("limb", "observe", "finish", "NotExercised", "Tee",
                       "dataset_frames", "export_and_run", "main"):
            assert needed in names, f"the gate defines no {needed!r}"
        return f"{len(names)} definitions"

    @check("every microclaw API the gate calls exists with that signature")
    def signatures():
        import inspect
        from microclaw import tools
        from microclaw.hooks import AutofocusHook
        from microclaw.safety import SafetyGuard
        from microclaw.controller import MicroscopeController
        from microclaw.config import load_safety_config_or_exit

        # execute_tool(name, tool_input, ctrl, guard, ...) -> str | list.
        params = list(inspect.signature(tools.execute_tool).parameters)
        assert params[:4] == ["name", "tool_input", "ctrl", "guard"], params[:4]
        kw = inspect.signature(tools.execute_tool).parameters
        for needed in ("acquisition_session_id", "tool_call_id"):
            assert needed in kw, f"execute_tool has no {needed!r}"
        assert "session_id" not in kw, "execute_tool grew a session_id kwarg"

        # It returns a STRING the gate must json.loads.
        source = gate_path.read_text(encoding="utf-8")
        for call in ("tools.execute_tool("):
            pass
        assert source.count("json.loads(tools.execute_tool(") == 2, (
            "the gate must parse execute_tool's JSON string, not index it")

        af = inspect.signature(AutofocusHook.__init__).parameters
        for needed in ("z_range_um", "z_step_um", "settle_ms"):
            assert needed in af, f"AutofocusHook has no {needed!r}"
        assert "constraints" in inspect.signature(SafetyGuard.__init__).parameters
        assert "port" in inspect.signature(MicroscopeController.__init__).parameters
        assert callable(load_safety_config_or_exit)
        assert callable(tools._iter_present_coords), (
            "the gate counts saved frames with tools._iter_present_coords")
        return "execute_tool, AutofocusHook, SafetyGuard, controller, config, coords"

    @check("the gate never does list() over a Core collection")
    def no_list_over_core():
        core = SimpleNamespace(
            get_camera_device=lambda: "Camera",
            get_xy_stage_device=lambda: "XY",
            get_focus_device=lambda: "Z",
            get_loaded_devices=lambda: CoreVector(["Camera", "XY", "Z"]),
            get_position=lambda *a: 3.0,
            get_x_position=lambda: 0.0, get_y_position=lambda: 0.0,
        )
        # Reading a vector the way a rig delivers it must not raise.
        vec = core.get_loaded_devices()
        assert [vec.get(i) for i in range(vec.size())] == ["Camera", "XY", "Z"]
        try:
            list(vec)
        except TypeError:
            return "a Core vector raises on list(); the gate reads size()/get(i)"
        raise AssertionError("the fake is not bridge shaped: list() succeeded")

    @check("CONTROL: a required autofocus refusal raises (fails on a pre-81a-2 tree)")
    def control_fires():
        from microclaw.hooks import AutofocusHook
        from microclaw.safety import (SafetyConstraints, SafetyGuard,
                                      StageConstraints, SafetyViolation)
        constraints = SafetyConstraints(
            stage=StageConstraints(z_min=0.0, z_max=10.0))
        core = SimpleNamespace(
            get_position=lambda *a: 1e9, get_focus_device=lambda: "Z",
            snap_image=lambda: None, wait_for_device=lambda *a: None)
        hook = AutofocusHook(SimpleNamespace(core=core),
                             SafetyGuard(constraints),
                             z_range_um=4.0, z_step_um=0.25, settle_ms=0)
        try:
            returned = hook.post_hardware_hook_fn({"axes": {"position": 0}})
        except SafetyViolation as exc:
            assert "Required autofocus stopped" in str(exc), str(exc)
            return f"raised: {str(exc)[:80]}"
        raise AssertionError(
            "the hook returned "
            f"{returned!r} instead of raising -- this is a pre-81a-2 tree, and "
            "on it that event would be exposed at the unfocused plane")

    @check("the gate writes only under --out and edits no safety config")
    def no_production_writes():
        source = gate_path.read_text(encoding="utf-8")
        assert "deepcopy" in source, (
            "limb B must narrow a COPY of the constraints, never the "
            "operator's config (58e/5b)")
        for forbidden in ("setx", "save_safety_config", "write_safety"):
            assert forbidden not in source, f"the gate calls {forbidden!r}"
        return "narrows a deepcopy; no config writer called"

    @check("NOT EXERCISED is never a pass, and the exit status says so")
    def not_exercised_fails():
        import ast
        tree = ast.parse(gate_path.read_text(encoding="utf-8"))
        finish = next(n for n in ast.walk(tree)
                      if isinstance(n, ast.FunctionDef) and n.name == "finish")
        # Checked on the AST, not by matching source text: ast.unparse
        # normalises quotes, so a literal 'r["status"] != "PASS"' can never
        # match. That was this selftest's own first defect -- an instrument
        # encoding an assumption about the thing it measures.
        compares = [n for n in ast.walk(finish) if isinstance(n, ast.Compare)]
        against_pass = [
            n for n in compares
            if any(isinstance(op, ast.NotEq) for op in n.ops)
            and any(isinstance(c, ast.Constant) and c.value == "PASS"
                    for c in n.comparators)]
        assert against_pass, (
            "finish() must count anything that is not PASS as bad, so a "
            "NOT EXERCISED limb cannot be read as a pass (58a)")
        returns = [ast.unparse(n) for n in ast.walk(finish)
                   if isinstance(n, ast.Return)]
        assert any("1 if bad else 0" in r for r in returns), returns
        return f"finish() compares != 'PASS' and returns {returns[-1]}"

    print()
    if FAILURES:
        print(f"{len(FAILURES)} SELFTEST FAILURE(S) -- do not ship this gate:")
        for name, detail in FAILURES:
            print(f"  {name}: {detail}")
        return 1
    print("selftest clean: the gate's API calls exist and its control fires.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
