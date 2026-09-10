"""Measure synchronous hook logging for 200 converged events, without hardware.

Run: .venv/bin/python design/81-block81a2-log-cost.py
Uses the real callbacks at the branch point, reviewed commit, and working tree.
Includes JSON serialization and whole-file writes; excludes focus motion/snaps.
"""
import ast
import json
import statistics
import subprocess
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

from microclaw import hooks
from microclaw.autofocus import AutofocusResult, SweepResult


def callback(revision):
    if revision is None:
        return hooks.AutofocusHook.post_hardware_hook_fn
    source = subprocess.check_output(
        ["git", "show", f"{revision}:microclaw/hooks.py"], text=True)
    cls = next(node for node in ast.parse(source).body
               if isinstance(node, ast.ClassDef) and node.name == "AutofocusHook")
    method = next(node for node in cls.body
                  if isinstance(node, ast.FunctionDef) and node.name == "post_hardware_hook_fn")
    namespace = dict(vars(hooks))
    exec(compile(ast.Module(body=[method], type_ignores=[]), revision, "exec"), namespace)
    return namespace[method.name]


def sweep(lo, hi, count):
    zs = [lo + (hi - lo) * i / (count - 1) for i in range(count)]
    return SweepResult(
        z_positions=zs, metric_values=[1 / (1 + (z - 3.5)**2) for z in zs],
        best_z_um=3.5, peak_interior=True, measured_z_positions=zs,
        selected_commanded_z_um=3.5, selected_measured_z_um=3.5,
        sweep_window_um=[lo, hi], planes_planned=count,
    )


def main():
    result = AutofocusResult(
        coarse=sweep(1, 5, 5), fine=sweep(2.25, 4.75, 11),
        entry_z_um=3, final_z_um=3.5, converged=True, moved=True,
        reason=None, final_commanded_z_um=3.5)
    with tempfile.TemporaryDirectory() as directory:
        for label, revision in [("baseline", "b1c6d54"), ("reviewed", "73f87b9"), ("changed", None)]:
            method = callback(revision)
            elapsed = []
            for repeat in range(3):
                path = Path(directory) / f"{label}-{repeat}.json"
                hook = hooks.AutofocusHook(
                    SimpleNamespace(core=SimpleNamespace(get_position=lambda: 3.)),
                    SimpleNamespace(check_z=lambda z: None), 4, .25, 0, str(path))
                hook._autofocus_fn = lambda *a, **k: result
                start = time.perf_counter()
                for position in range(200):
                    method(hook, {"axes": {"position": position}, "x": position, "y": 0, "z": 3})
                elapsed.append(time.perf_counter() - start)
            seconds = statistics.median(elapsed)
            print(f"{label}: events=200 entries={len(json.loads(path.read_text()))} "
                  f"bytes={path.stat().st_size} median_s={seconds:.6f} "
                  f"ms_per_event={seconds * 5:.3f}")


if __name__ == "__main__":
    main()
