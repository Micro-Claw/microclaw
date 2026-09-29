"""83e-5: supervisor priority versus a normal-priority control, on the demo machine.

Reuses 83e-4's real acquisition runner, store, bridge-shaped fakes and unchanged
limbs. Burst only: one none warm-up, then four repetitions of none/loaded/normal
(13 runs, 13,000 frames). The cyclic Latin order is position-balanced over each
complete three-repetition cycle, not carry-over balanced; repetition four repeats
the first row, so four repetitions are not exactly position-balanced. A Williams
carry-over-balanced design for three cells would need six sequences.

Measurements are printed, never pass/fail. Control versus loaded uses 83e-4's
strict separation rule; loaded versus none is ranges only, with no equality claim.
Selftest uses the real product and reduced acquisitions. POSIX priority evidence
is cross-checked with an independent subprocess probe; it is not Windows evidence.
Synthetic controls are labelled and used only to prove scorer discrimination.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import traceback

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("gate83e4", HERE / "83-block83e4-demo-gate.py")
e4 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(e4)
read_json, write_json, NotExercised = e4.read_json, e4.write_json, e4.NotExercised
CONDITIONS = ("none", "loaded", "normal")
PLAN_SIZES = (5, 1000)  # total repetitions including repetition 0's single warm-up
SELFTEST_SIZES = (3, 20)
LIMBS = dict(e4.LIMBS)
LIMBS[5] = "supervisor priority and fixture read-back"
LIMBS[7] = "confirmations recorded; below-normal CPU priority disclosed"
CAVEAT = ("n=4 per cell on this machine; a data point about the demo machine, not a property of "
          "rigs; its camera makes frames in software. Strict separation's chance level for n=4 "
          "is 2/70, approximately 2.9%; this is not a causal or rig-wide guarantee.")
_base_verify = e4.verify


def plan(digest, save_dir, cpu, sizes=PLAN_SIZES):
    reps, frames = sizes
    # 83e-4.prepare owns preparation; change its hard-coded dataset prefix here.
    save_dir = Path(save_dir)
    if save_dir.name.startswith("block83e4-"):
        save_dir = save_dir.with_name(save_dir.name.replace("block83e4-", "block83e5-", 1))
    runs = []
    for rep in range(reps):
        order = ("none",) if rep == 0 else tuple(CONDITIONS[(i + rep - 1) % 3] for i in range(3))
        for condition in order:
            name = f"r{rep}-burst-{condition}"
            args = dict(n_frames=frames, interval_s=0, exposure_ms=10, save_dir=str(save_dir), name=name)
            if condition != "none":
                parameters = dict(cpu_threads=cpu, max_s=600)
                if condition == "normal":
                    parameters["priority"] = "normal"
                args["analysis"] = dict(adapter=e4.ADAPTER, release_digest=digest, parameters=parameters)
            runs.append(dict(id=name, repetition=rep, type="burst", condition=condition, input=args))
    return runs


def own_priority():
    if os.name != "nt":
        return os.getpriority(os.PRIO_PROCESS, 0)
    import ctypes as c
    from ctypes import wintypes as w
    kernel = c.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.GetPriorityClass.argtypes, kernel.GetPriorityClass.restype = [w.HANDLE], w.DWORD
    value = kernel.GetPriorityClass(kernel.GetCurrentProcess())
    if not value:
        raise c.WinError(c.get_last_error())
    return value


class Gate(e4.Gate):
    def prepare(self, *, port=4827, sizes=PLAN_SIZES):
        return super().prepare(port=port, sizes=sizes)

    def save(self, phase, data):
        if phase == "prepare":
            data["save_dir"] = data["runs"][0]["input"]["save_dir"]
        elif phase == "run":
            data.update(priority_platform=os.name, microclaw_priority=own_priority())
            if self.fake and os.name != "nt":
                data["posix_selftest_probe"] = posix_probe()
        super().save(phase, data)

    def say(self, text):
        super().say(text.replace("block83e4-", "block83e5-"))


def posix_probe():
    """Independent launch/raise probe; nice may warn but still exec in a sandbox."""
    own = own_priority()
    script = '''import json, os
before = os.getpriority(os.PRIO_PROCESS, 0)
reason = None
try:
    os.setpriority(os.PRIO_PROCESS, 0, 0)
except OSError as exc:
    reason = str(exc)
print(json.dumps(dict(start=before, normal=os.getpriority(os.PRIO_PROCESS, 0),
                     applied=reason is None, reason=reason)))
'''
    argv = [sys.executable, "-c", script]
    if own < 10:
        argv = ["nice", "-n", str(10 - own), *argv]
    probe = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
    if probe.returncode:
        raise RuntimeError(f"independent priority probe failed: {probe.stderr}")
    evidence = json.loads(probe.stdout)
    evidence.update(requested=max(own, 10), stderr=probe.stderr)
    if evidence["start"] != evidence["requested"]:
        assert evidence["start"] == own and "Operation not permitted" in probe.stderr, evidence
    return evidence


def priority_detail(gate, row, prep, info):
    folder = gate.out / "runs" / row["id"]
    if row["condition"] == "none":
        result = e4._load(folder / "result.json")
        assert not (result.get("analysis") or {}).get("jobs"), "none run dispatched a job"
        assert not list(folder.glob("job-*.json")), "none run has job evidence"
        return "no job"
    job = e4._load(folder / "job-0.json")
    p = job.get("priority")
    assert isinstance(p, dict), "job record has no supervisor priority evidence"
    fixture = job["result"]["output"].get("priority")
    assert isinstance(fixture, dict), "fixture has no priority read-back"
    detail = f"supervisor={p}; fixture={fixture}; MicroClaw's class/niceness={info.get('microclaw_priority')}"
    assert type(p.get("requested")) is int and type(p.get("inherited")) is bool, detail
    reasons = p.get("reason")
    assert p.get("at_end") is not None or (isinstance(reasons, dict) and reasons.get("at_end")), (
        "missing at_end without its recorded reason: " + detail)
    probe = info.get("posix_selftest_probe") if prep.get("selftest") and info.get("priority_platform") != "nt" else None
    expected = probe["requested"] if probe else 0x4000
    if row["condition"] == "loaded":
        assert p.get("at_start") == fixture.get("read_back") and p.get("at_start") is not None, detail
        if p["inherited"]:
            assert p["requested"] == p["at_start"] == info.get("microclaw_priority"), detail
            return "inherited MicroClaw's class (reported, not a below-normal failure); " + detail
        assert p["requested"] == expected, detail
        assert p["at_start"] == (probe["start"] if probe else expected), detail
    else:
        assert p["requested"] == expected, detail
        assert fixture.get("requested") == "normal", detail
        assert fixture.get("applied") is (probe["applied"] if probe else True), detail
        assert fixture.get("read_back") == (probe["normal"] if probe else 0x20), detail
        if not fixture["applied"]:
            assert fixture.get("reason") and probe["reason"], detail
    return ("POSIX selftest only, independently probed; " if probe else "") + detail


def verify(gate, *, cleanup=True, echo=True, measure=True):
    # Keep the six unchanged limbs, including every per-run finding. The parent
    # verifier's old priority verdict is replaced, never used as an e5 criterion.
    _base_verify(gate, cleanup=False, echo=False, measure=False)
    report = read_json(gate.out / "verify.json")
    outcomes = {int(k): v for k, v in report["runs"].items()}
    outcomes[5] = []
    for row in outcomes[7]:
        row["detail"] = row["detail"].replace("D4", "D5")
    say = gate.say if echo else lambda text: None

    def check(limb, run, fn):
        try:
            status, detail = "PASS", fn()
        except NotExercised as exc:
            status, detail = "NOT EXERCISED", str(exc)
        except Exception as exc:
            status, detail = "FAIL", f"{type(exc).__name__}: {exc}"
        outcomes[limb].append(dict(run=run, status=status, detail=detail))

    try:
        prep = gate.need("prepare")
    except Exception as exc:
        prep = None
        outcomes[5].append(dict(run="prepare", status="NOT EXERCISED", detail=str(exc)))
    try:
        info = gate.need("run")
    except Exception:
        info = {}
    if prep:
        for row in prep["runs"]:
            check(5, row["id"], lambda r=row: priority_detail(gate, r, prep, info))

    def disclosure():
        line = info.get("disclosure_line")
        assert isinstance(line, str) and line.startswith(e4.DISCLOSURE_PREFIX), "no product disclosure line"
        assert "below-normal CPU priority" in line and "same priority as MicroClaw" not in line, line
        return line
    check(7, "product disclosure", disclosure)
    statuses = {limb: ("FAIL" if any(r["status"] == "FAIL" for r in rows) else
                      "NOT EXERCISED" if not rows or any(r["status"] == "NOT EXERCISED" for r in rows)
                      else "PASS") for limb, rows in outcomes.items()}
    for limb, rows in outcomes.items():
        say(f"{statuses[limb]}: limb {limb} {LIMBS[limb]}")
        for row in rows:
            say(f"    {row['status']}: {row['run']}: {row['detail']}")
    if measure and prep:
        print_measurement(gate, prep, e4._diagnostics(gate.out), say)
    cleaned = None
    if cleanup:
        try:
            cleaned = e4._gate_cleanup(gate)
            say(f"CLEANUP: {cleaned}")
        except Exception as exc:
            cleaned = False
            say(f"CLEANUP FAILED: {type(exc).__name__}: {exc}")
    bad = sum(v != "PASS" for v in statuses.values())
    write_json(gate.out / "verify.json", dict(statuses=statuses, runs=outcomes, cleanup=cleaned,
                                              scored_at=e4.now().isoformat()))
    gate.last_results = statuses
    say(f"RESULT: {bad} failed or not exercised limbs / {len(LIMBS)}; measurement printed above")
    return bad + (cleaned is False)


def print_measurement(gate, prep, diagnostics, say):
    say("MEASUREMENT (printed, not pass/fail). " + CAVEAT)
    if prep.get("selftest"):
        say("SELFTEST PLAN: reduced sizes, bridge fakes; these measurements are not rig evidence.")
    k = e4.window_starts(gate.out, prep)["burst"]
    say(f"One none warm-up excluded; K={k} is shared by all three cells. Gaps use frames at or after K; "
        "p95 is nearest rank. * marks a run not qualified as loaded, never silently omitted.")
    rows = [r for r in prep["runs"] if r["repetition"]]
    stats, good = {}, {}
    for row in rows:
        good[row["id"]] = row["condition"] == "none" or e4.qualified(gate.out, row, prep["cpu_count"])
        try:
            stats[row["id"]] = e4.measurements(gate.out, row, diagnostics, k)
        except Exception as exc:
            say(f"MEASUREMENT UNAVAILABLE {row['id']}: {type(exc).__name__}: {exc}")
    expected = prep["sizes"][0] - 1 if prep.get("selftest") else 4
    keys = {key for values in stats.values() for key in values}
    keys.update(("duration_s (whole run)", f"metadata_gap_mean_s (window from frame {k})",
                 f"metadata_gap_p95_s (window from frame {k})"))
    for key in sorted(keys):
        cells = {c: [stats.get(r["id"], {}).get(key) for r in rows if r["condition"] == c] for c in CONDITIONS}
        for c in CONDITIONS:
            values = [f"{r['id']}={'--' if v is None else format(v, '.6g')}{'' if good[r['id']] else '*'}"
                      for r, v in zip((r for r in rows if r["condition"] == c), cells[c])]
            say(f"burst | {key} | {c}=[{', '.join(values)}]")
        complete = lambda c: len(cells[c]) == expected and None not in cells[c]
        verdict = e4.separate(cells["loaded"], cells["normal"]) if all(complete(c) for c in ("loaded", "normal")) else "UNAVAILABLE (incomplete cell)"
        counts = "; ".join(f"{c}: {sum(good[r['id']] for r in rows if r['condition'] == c)} of {expected} loaded runs qualified"
                           for c in ("loaded", "normal"))
        say(f"burst | {key} | loaded/normal: {verdict} (normal relative to loaded; {counts})")
        ranges = "; ".join(f"{c}: [{min(cells[c]):.6g}, {max(cells[c]):.6g}]" if complete(c) else f"{c}: UNAVAILABLE (incomplete cell)"
                           for c in ("none", "loaded"))
        say(f"burst | {key} | loaded versus none ranges only: {ranges}; no equality claim")
    say(CAVEAT)


# Functions in the imported module resolve these globals at call time. Its source
# and acquisition/scoring implementation remain untouched.
e4.plan, e4.TYPES, e4.CONDITIONS = plan, ("burst",), CONDITIONS
e4.POINTER = "83e5-gate-evidence.txt"
e4.PLAN_SIZES = PLAN_SIZES


def plan_checks():
    runs = plan("d", "root", 4)
    assert len(runs) == len({r["id"] for r in runs}) == 13
    assert sum(r["input"]["n_frames"] for r in runs) == 13000
    assert [r["condition"] for r in runs if not r["repetition"]] == ["none"]
    orders = [[r["condition"] for r in runs if r["repetition"] == rep] for rep in range(1, 5)]
    assert orders[0] == orders[3]
    assert all({order[slot] for order in orders[:3]} == set(CONDITIONS) for slot in range(3))
    for row in runs:
        args = row["input"]
        assert args["interval_s"] == 0 and args["exposure_ms"] == 10
        if row["condition"] != "none":
            assert args["analysis"]["parameters"] == dict(cpu_threads=4, max_s=600, **(
                {"priority": "normal"} if row["condition"] == "normal" else {}))
    assert e4.separate([1, 2, 3, 4], [5, 6, 7, 8]) == "SEPARATED (higher)"
    assert e4.separate([5, 6, 7, 8], [1, 2, 3, 4]) == "SEPARATED (lower)"
    assert e4.separate([1, 2, 3, 4], [4, 5, 6, 7]) == "not separated"
    assert e4.p95(list(range(1, 21))) == 19


def mutations(control, prep):
    # The old mutations address files by id. Alias only their selector for the
    # old 'below' condition to this gate's normal control; keep all other limbs.
    selectors = deepcopy(prep)
    for row in selectors["runs"]:
        if row["condition"] == "normal":
            row["condition"] = "below"
    inherited = e4._mutations(control, selectors)
    rows = [(limb, label.replace("below", "normal").replace("D4", "D5"), mutate, *extra)
            for limb, label, mutate, *extra in inherited]
    loaded = next(r for r in prep["runs"] if r["condition"] == "loaded")

    def job_change(change):
        path = control / "runs" / loaded["id"] / "job-0.json"
        job = read_json(path)
        change(job)
        write_json(path, job)

    def old_disclosure():
        path = control / "run.json"
        info = read_json(path)
        old = info["disclosure_line"]
        info["disclosure_line"] = e4.DISCLOSURE_PREFIX + ", at the same priority as MicroClaw."
        write_json(path, info)
        for path in control.glob("runs/*/confirmations.json"):
            prompts = read_json(path)
            for prompt in prompts:
                prompt["summary"] = prompt["summary"].replace(old, info["disclosure_line"])
            write_json(path, prompts)

    rows += [
        (5, "supervisor evidence removed", lambda: job_change(lambda j: j.pop("priority", None))),
        (5, "supervisor requested normal", lambda: job_change(lambda j: j["priority"].update(requested=0x20))),
        (5, "supervisor start disagrees with fixture", lambda: job_change(lambda j: j["priority"].update(at_start=0x20))),
        (5, "missing end without reason", lambda: job_change(lambda j: j["priority"].update(at_end=None, reason={}))),
        (7, "old product disclosure also present in prompts", old_disclosure),
    ]
    return rows


def selftest(baseline_only=False, sizes=SELFTEST_SIZES):
    from unittest.mock import patch
    from types import SimpleNamespace
    assert sizes[0] >= 2 and sizes[1] >= 3, "selftest needs a measured repetition and at least three frames"
    plan_checks()
    failures = 0
    with tempfile.TemporaryDirectory(prefix="block83e5-") as temporary, ExitStack() as stack:
        base = Path(temporary)
        env = {name: str(base / name) for name in ("XDG_DATA_HOME", "LOCALAPPDATA", "XDG_CONFIG_HOME", "APPDATA")}
        env.update(UV_OFFLINE="1", UV_CACHE_DIR=str(base / "uv-cache"))
        stack.enter_context(patch.dict(os.environ, env))
        import microclaw
        from microclaw import paths, controller, tools
        from microclaw.completed_dataset import close_analysis_supervisor
        print(f"SELFTEST product under test: {Path(microclaw.__file__).parent}", flush=True)
        config = paths.default_safety_config()
        config.parent.mkdir(parents=True)
        config.write_text(e4.SAFETY_YAML, encoding="utf-8")  # No workspace_dir: optional in the product.
        mm_root = base / "fake-mm"
        (mm_root / "plugins").mkdir(parents=True)
        root = paths.user_data_dir()
        root.mkdir(parents=True)
        out = base / "evidence"
        fake = e4.OfflineInstall()
        gate = Gate(root, out, fake=fake)

        def check(ok, detail):
            nonlocal failures
            failures += not ok
            gate.say(f"SELFTEST {'ok' if ok else 'WRONG'}: {detail}")

        with patch.object(controller, "Core", e4.BridgeCore), patch.object(controller, "Studio", e4.BridgeStudio), \
                patch.object(controller.MicroscopeController, "get_mm_app_dir", return_value=str(mm_root)), \
                patch.object(tools, "Acquisition", e4.FakeAcquisition):
            gate.prepare(sizes=sizes)
            try:
                gate.prepare(sizes=sizes)
                refused = False
            except NotExercised as exc:
                refused = "already exists" in str(exc)
            check(refused, "existing package store refuses prepare")
            try:
                gate.run()
            except Exception as exc:
                gate.say(f"SELFTEST run stopped: {type(exc).__name__}: {exc}; score all available evidence")
        close_analysis_supervisor()
        prep = gate.need("prepare")
        backup = base / "unmutated"
        shutil.copytree(out, backup)
        bad = verify(gate, cleanup=True)
        gate.say(f"SELFTEST unmutated RESULT: {bad}; {json.dumps(gate.last_results)}")
        if baseline_only:
            return 0
        check(all(gate.last_results[i] == "PASS" for i in (1, 2, 4, 5, 7, 8)) and not gate.store_root.exists(),
              "real evidence: limbs 1, 2, 4, 5, 7, 8 PASS; store removed (3/6 are observations of fakes)")
        fake.listening = "a selftest listener"
        try:
            gate.close_microclaw("Probe.")
            refused = False
        except NotExercised as exc:
            refused = "8000" in str(exc)
        fake.listening = None
        check(refused, "port 8000 busy refuses the gate")
        with patch.object(e4.shutil, "disk_usage", return_value=SimpleNamespace(free=0)):
            try:
                e4.disk_check(e4.BridgeCore(), root, prep["runs"])
                refused = False
            except NotExercised as exc:
                refused = "not enough disk" in str(exc)
        check(refused, "insufficient disk refuses the gate")
        prompts = []
        confirm = e4.recording_confirm(prompts, "pkg@d", lambda p: None)
        answers = [confirm("a", kind="analysis", subject="pkg@d"),
                   confirm("b", kind="acquisition", subject="threshold"),
                   confirm("c", kind="analysis", subject="other@d"),
                   confirm("d", kind="illumination"), confirm("e", kind="acquisition")]
        check(answers == [True, True, False, False, False] and len(prompts) == 5, "consent scopes and records every prompt")

        # Synthetic Windows priority, disclosure and load prove the shipping scorer, independently of
        # the real POSIX read-back/probe above. Never present this as rig evidence.
        clean = base / "synthetic-clean"
        shutil.copytree(backup, clean)
        info = read_json(clean / "run.json")
        original_line = info["disclosure_line"]
        info.update(priority_platform="nt", microclaw_priority=0x20,
                    disclosure_line=e4.DISCLOSURE_PREFIX + ", at below-normal CPU priority.")
        for path in clean.glob("runs/*/confirmations.json"):
            prompts = read_json(path)
            for prompt in prompts:
                prompt["summary"] = prompt["summary"].replace(original_line, info["disclosure_line"])
            write_json(path, prompts)
        info.pop("posix_selftest_probe", None)
        write_json(clean / "run.json", info)
        for row in prep["runs"]:
            if row["condition"] == "none":
                continue
            path = clean / "runs" / row["id"] / "job-0.json"
            if not path.exists():
                check(False, f"missing job for synthetic control: {row['id']}")
                continue
            job = read_json(path)
            output = job["result"]["output"]
            normal = row["condition"] == "normal"
            end = 0x20 if normal else 0x4000
            output.update(load_cpu_ratio=float(prep["cpu_count"]), frames_indexed_at_load_start=0,
                          priority=dict(requested="normal" if normal else "inherit", applied=True, read_back=end, reason=None))
            job["priority"] = dict(requested=0x4000, inherited=False, at_start=0x4000, at_end=end, reason={})
            write_json(path, job)
        control = base / "synthetic-control"

        def reset():
            if control.exists():
                shutil.rmtree(control)
            shutil.copytree(clean, control)
            return Gate(root, control, fake=fake)

        arm = reset()
        check(verify(arm, cleanup=False, echo=False) == 0, "synthetic Windows control: all eight limbs PASS")
        for limb, label, mutate, *collateral in mutations(control, prep):
            arm = reset()
            mutate()
            bad = verify(arm, cleanup=False, echo=False, measure=False)
            allowed = collateral[0] if collateral else set()
            others = {k: v for k, v in arm.last_results.items() if v != "PASS" and k != limb and k not in allowed}
            check(arm.last_results[limb] != "PASS" and not others,
                  f"mutation limb {limb} ({label}) -> {arm.last_results[limb]}; other non-pass: {others}; RESULT: {bad}")
        arm = reset()
        loaded = next(r for r in prep["runs"] if r["condition"] == "loaded")
        path = control / "runs" / loaded["id"] / "job-0.json"
        job = read_json(path)
        job["priority"].update(at_end=None, reason={"at_end": "process exited before priority read"})
        write_json(path, job)
        check(verify(arm, cleanup=False, echo=False, measure=False) == 0, "missing at_end with a reason is reported, not failed")
        job["priority"].update(requested=0x40, inherited=True, at_start=0x40)
        job["result"]["output"]["priority"]["read_back"] = 0x40
        write_json(path, job)
        info = read_json(control / "run.json")
        info["microclaw_priority"] = 0x40
        write_json(control / "run.json", info)
        check(verify(arm, cleanup=False, echo=False, measure=False) == 0, "inherited idle MicroClaw class reported without failing loaded run")
        arm = reset()
        path.write_text("{broken", encoding="utf-8")
        verify(arm, cleanup=False, echo=False, measure=False)
        scored = read_json(control / "verify.json")["runs"]["5"]
        check(all(r["status"] == ("FAIL" if r["run"] == loaded["id"] else "PASS") for r in scored),
              "malformed job affects only its own priority score")
        arm = reset()
        measured = [r for r in prep["runs"] if r["repetition"]]
        late = sizes[1] - 3
        job = read_json(path)
        job["result"]["output"].update(frames_indexed_at_load_start=late, load_cpu_ratio=0.1)
        write_json(path, job)
        k = e4.window_starts(control, prep)["burst"]
        none = next(r for r in measured if r["condition"] == "none")
        gaps = e4._gaps(control / "runs" / none["id"], none)
        stats = e4.measurements(control, none, [], k)
        check(k == late and stats[f"metadata_gap_mean_s (window from frame {k})"] == sum(gaps[k:]) / len(gaps[k:]),
              "one shared loaded window also moves the none cell's statistics")
        lines = []
        print_measurement(arm, prep, e4._diagnostics(control), lines.append)
        check(any("*" in line and loaded["id"] in line for line in lines)
              and any(f"loaded: {sizes[0] - 2} of {sizes[0] - 1} loaded runs qualified" in line for line in lines)
              and any("loaded versus none ranges only" in line for line in lines)
              and not any("none/loaded: SEPARATED" in line for line in lines),
              "unqualified load marked and counted; loaded/none has ranges, no separation verdict")
        gate.say(f"SELFTEST {'PASSED' if not failures else f'FAILED ({failures})'}; no wall-clock assertions")
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("phase", choices=("prepare", "run", "verify", "selftest"))
    parser.add_argument("--out", type=Path)
    parser.add_argument("--port", type=int, default=4827)
    parser.add_argument("--baseline-only", action="store_true")
    parser.add_argument("--selftest-sizes", type=int, nargs=2, default=SELFTEST_SIZES, metavar=("REPS", "BURST"))
    args = parser.parse_args()
    if args.phase == "selftest":
        return int(bool(selftest(args.baseline_only, tuple(args.selftest_sizes))))
    if args.out is None:
        parser.error("--out is required")
    from microclaw import paths
    gate = Gate(paths.user_data_dir(), args.out)
    try:
        if args.phase == "verify":
            return int(bool(verify(gate)))
        gate.prepare(port=args.port) if args.phase == "prepare" else gate.run()
        gate.say(f"RECORDED: {args.phase}")
        return 0
    except (NotExercised, SystemExit) as exc:
        gate.say(f"NOT EXERCISED: {args.phase}: {exc}")
        write_json(gate.out / f"{args.phase}-error.json", dict(error=str(exc), traceback=traceback.format_exc()))
        return 2
    except Exception as exc:
        gate.say(f"FAILED: {args.phase}: {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
        write_json(gate.out / f"{args.phase}-error.json", dict(error=str(exc), traceback=traceback.format_exc()))
        return 1


if __name__ == "__main__":
    sys.exit(main())
