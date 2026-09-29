"""83e-4: can a loaded analysis worker disturb an acquisition, on the demo machine?

A standalone program (D5): no model, no chat, no browser. `run` builds the
controller the way `microclaw.__main__.run_session` does and calls the real
`tools.execute_tool("run_timelapse", ...)` 42 times: burst and spaced timelapses,
each with no analysis, a loaded fixture worker, and a loaded below-normal worker
(D2), 7 repetitions in a carry-over-balanced order, the first a warm-up (D3).
Consent is answered by the program: it approves the package's analysis prompt
and the acquisition-size prompt only, and records every prompt.

`verify` scores only files in the evidence folder, so it re-scores off-rig.
Eight instrument limbs are PASS / FAIL / NOT EXERCISED, each independent; the
measurement is printed with D3's pre-registered separation rule and is never
pass/fail.

`selftest` runs prepare -> run -> verify against 83e-3's bridge-shaped fakes with
the real execute_tool, supervisor, store and fixture worker, at a reduced size,
then mutates one artifact per limb. The fakes cannot contend for CPU, so the
selftest proves the instrument, never the measurement.
"""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import importlib.util
import inspect
import itertools
import json
import math
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import traceback

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("gate83e3", HERE / "83-block83e3-demo-gate.py")
e3 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(e3)
gate83d = e3.gate83d
FIXTURES, NotExercised = gate83d.FIXTURES, gate83d.NotExercised
read_json, write_json, now = gate83d.read_json, gate83d.write_json, gate83d.now

PACKAGE, ADAPTER = e3.PACKAGE, e3.ADAPTER
POINTER = "83e4-gate-evidence.txt"
SERVE_PORT = 8000
TYPES = ("burst", "spaced")
CONDITIONS = ("none", "loaded", "below")
CELLS = tuple(itertools.product(TYPES, CONDITIONS))
# Williams design for six cells: over any six consecutive repetitions every cell
# holds every position once and every ordered pair of cells is adjacent once.
WILLIAMS = (0, 1, 5, 2, 4, 3)
PLAN_SIZES = (7, 1000, 100)          # repetitions, burst frames, spaced frames -- the gate's plan
SELFTEST_SIZES = (2, 20, 5)
JOB_WAIT_S = 120
LOAD_FRACTION = 0.75
DISK_MARGIN = 1.2
# Only to *find* D4's line in the product's source; the line itself is never retyped.
DISCLOSURE_PREFIX = "The analysis runs at the same time as the acquisition"
LIMBS = {1: "frames saved equal planned", 2: "job dispatch, succeeded, writer finished",
         3: "loaded runs were loaded", 4: "observer read every frame",
         5: "priority applied and read back", 6: "loaded window",
         7: "confirmations recorded; D4 disclosure line", 8: "jobs never overlapped"}
CAVEAT = ("n=6 per cell on this machine; a data point about the demo machine, not a property of "
          "rigs; its camera makes frames in software.")


# --- plan -------------------------------------------------------------------------------------
def plan(digest, save_dir, cpu, sizes=PLAN_SIZES):
    reps, burst, spaced = sizes
    runs = []
    for rep in range(reps):
        row = rep % len(CELLS)
        for kind, condition in (CELLS[(i + row) % len(CELLS)] for i in WILLIAMS):
            name = f"r{rep}-{kind}-{condition}"
            args = dict(n_frames=burst if kind == "burst" else spaced,
                        interval_s=0 if kind == "burst" else 0.1, exposure_ms=10,
                        save_dir=str(save_dir), name=name)
            if condition != "none":
                parameters = dict(cpu_threads=cpu, max_s=600)
                if condition == "below":
                    parameters["priority"] = "below_normal"
                args["analysis"] = dict(adapter=ADAPTER, release_digest=digest, parameters=parameters)
            runs.append(dict(id=name, repetition=rep, type=kind, condition=condition, input=args))
    return runs


def product_disclosure():
    """D4's line, read out of the installed microclaw.tools source; None if absent."""
    from microclaw import tools
    found = {node.value for node in ast.walk(ast.parse(inspect.getsource(tools)))
             if isinstance(node, ast.Constant) and isinstance(node.value, str)
             and node.value.startswith(DISCLOSURE_PREFIX)}
    return found.pop() if len(found) == 1 else None


def camera_frame(core):
    """Bytes per frame from bridge reads (plain numbers over pyjavaz)."""
    width, height, bpp = (int(core.get_image_width()), int(core.get_image_height()),
                          int(core.get_bytes_per_pixel()))
    if min(width, height, bpp) <= 0:
        raise NotExercised(f"camera reports width {width}, height {height}, bytes/pixel {bpp}")
    return dict(width=width, height=height, bytes_per_pixel=bpp, bytes_per_frame=width * height * bpp)


def disk_check(core, root, runs):
    frame = camera_frame(core)
    existing = Path(root)
    while not existing.exists():
        existing = existing.parent
    frames = sum(r["input"]["n_frames"] for r in runs)
    need = math.ceil(frame["bytes_per_frame"] * frames * DISK_MARGIN)
    free = shutil.disk_usage(existing).free
    evidence = dict(frame, frames=frames, margin=DISK_MARGIN, projected_bytes=need,
                    free_bytes=free, dataset_root=str(root))
    if free < need:
        raise NotExercised(f"not enough disk at {root}: free {free} bytes, projected need {need} bytes "
                           f"({frames} frames x {frame['bytes_per_frame']} bytes x {DISK_MARGIN})")
    return evidence


def recording_confirm(prompts, expected_subject, on_write):
    """CONFIRM_FN for the run: records every prompt, approves only the two D5 kinds."""
    def confirm(summary, kind="action", subject=None, **_):
        approved = ((kind == "analysis" and subject == expected_subject)
                    or (kind == "acquisition" and subject == "threshold"))
        prompts.append(dict(summary=summary, kind=kind, subject=subject, approved=approved,
                            timestamp=now().isoformat()))
        on_write(prompts)
        return approved
    return confirm


def serve_listening():
    """Who listens on serve's port, or None. Locale-proof on Windows: no state column read."""
    if os.name == "nt":
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"], stdin=subprocess.DEVNULL,
                             capture_output=True, text=True, timeout=60).stdout
        for line in out.splitlines():
            parts = line.split()
            if (len(parts) >= 5 and parts[0].upper() == "TCP" and parts[1].endswith(f":{SERVE_PORT}")
                    and parts[2] in ("0.0.0.0:0", "[::]:0")):
                return f"process {parts[-1]}"
        return None
    for host in ("127.0.0.1", "::1"):
        try:
            with socket.create_connection((host, SERVE_PORT), timeout=0.3):
                return f"a process on {host}"
        except OSError:
            continue
    return None


# --- the gate ---------------------------------------------------------------------------------
class Gate(gate83d.Gate):
    def close_microclaw(self, why):
        """No prompt: this gate has no chat. MicroClaw must simply not be running."""
        who = self.fake.serve_listening() if self.fake else serve_listening()
        if who:
            raise NotExercised(f"{why} {who} is listening on port {SERVE_PORT}: close MicroClaw's "
                               "console and launcher windows (leave Micro-Manager open) and rerun.")

    def connect(self, port):
        """microclaw.__main__.run_session's construction, minus the model and API key."""
        from microclaw.config import load_safety_config_or_exit
        from microclaw.safety import SafetyGuard
        from microclaw.controller import MicroscopeController
        from microclaw.authorization import RigAuthorizationError, validate_live_rig
        parsed = load_safety_config_or_exit(None)
        guard = SafetyGuard(parsed.constraints)
        ctrl = MicroscopeController(port=port, guard=guard)
        if not ctrl.is_connected():
            raise NotExercised(f"could not connect to Micro-Manager on port {port}. Is it open with "
                               "Tools > Options > 'Run server on port 4827' on?")
        try:
            validate_live_rig(ctrl, parsed, guard=guard)
        except RigAuthorizationError as exc:
            raise NotExercised(f"validate_live_rig refused this rig: {exc}") from exc
        return parsed, guard, ctrl

    def prepare(self, *, port=4827, sizes=PLAN_SIZES):
        if self.store_root.exists():
            raise NotExercised(f"{self.store_root} already exists; this gate creates and removes its own "
                               "store. Send the evidence folder instead of deleting it.")
        self.close_microclaw("Prepare installs a release in this process.")
        parsed, guard, ctrl = self.connect(port)
        cpu = os.cpu_count()
        if not cpu:
            raise NotExercised("os.cpu_count() is unavailable on this machine")
        workspace = parsed.constraints.workspace_dir
        base = Path(guard.resolve_in_workspace(".") if workspace else self.root).resolve()
        save_dir = base / ("block83e4-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
        disk = disk_check(ctrl.core, base, plan("", save_dir, cpu, sizes))
        self.say(f"Disk: free {disk['free_bytes']} bytes; projected need {disk['projected_bytes']} bytes.")
        # Ownership first, so verify's cleanup can prove this store is the gate's own.
        self.save("ownership", dict(store=str(self.store_root.resolve()), host=socket.gethostname(),
                                    evidence=str(self.out.resolve())))
        releases = self.out / "releases"
        releases.mkdir(exist_ok=True)
        write_json(self.store_root / "trust" / "roots.json", read_json(FIXTURES / "trust" / "roots-TEST-ONLY.json"))
        policy = self.store.store_trust_policy(read_json(FIXTURES / "trust" / "policy-TEST-ONLY.json"))
        artifact = releases / "executable-1.0.0.zip"
        intake = gate83d.load_builder().build_release(FIXTURES / "executable", artifact, version="1.0.0")
        self.store.install(intake, artifact, policy=policy, now=now(), uv_executable=self.uv(),
                           retained_digests=frozenset(), find_links=(FIXTURES / "wheels").resolve(),
                           base_python=self.base_python())
        digest = intake["artifact_digest"]
        self.say(f"installed executable-fixture 1.0.0: {digest}")
        runs = plan(digest, save_dir, cpu, sizes)
        self.save("prepare", dict(digest=digest, cpu_count=cpu, disk=disk, save_dir=str(save_dir), port=port,
                                  sizes=list(sizes), selftest=bool(self.fake), runs=runs,
                                  prepared_at=now().isoformat()))
        self.say(f"C = os.cpu_count() = {cpu}; {len(runs)} runs; datasets under {save_dir}")
        for rep in range(sizes[0]):
            self.say(f"PLAN repetition {rep}{' (warm-up, excluded from statistics)' if rep == 0 else ''}: "
                     + ", ".join(f"{r['type']}-{r['condition']}" for r in runs if r["repetition"] == rep))

    def wait_terminal(self, job, folder, index):
        """Between runs only: the next acquisition must not share the machine with this job."""
        nonterminal = self.store.ANALYSIS_NONTERMINAL
        path = job.get("job_record_path")
        if not path:
            if job.get("state") in nonterminal:
                raise NotExercised(f"job {job.get('job_id')} is {job.get('state')} with no record path")
            write_json(folder / f"job-{index}.json", job)
            return
        deadline = time.perf_counter() + JOB_WAIT_S
        record = None
        while True:
            try:
                seen = read_json(path)
                modified = os.stat(path).st_mtime
                record = seen if isinstance(seen, dict) else record
            except (OSError, ValueError):
                seen = None
            if isinstance(seen, dict) and isinstance(seen.get("state"), str) and seen["state"] not in nonterminal:
                write_json(folder / f"job-{index}.json", seen)
                write_json(folder / f"job-{index}-clock.json", dict(
                    terminal_record_utc=datetime.fromtimestamp(modified, timezone.utc).isoformat(),
                    observed_utc=now().isoformat(), observed_perf=time.perf_counter()))
                return
            if time.perf_counter() >= deadline:
                if record is not None:
                    write_json(folder / f"job-{index}.json", record)
                raise NotExercised(f"job {job.get('job_id')} not terminal after {JOB_WAIT_S} s "
                                   f"(last state {record and record.get('state')}); stopped so the next "
                                   "run is not contaminated")
            time.sleep(0.05)

    def run(self):
        from microclaw import tools
        from microclaw.completed_dataset import close_analysis_supervisor
        from microclaw.conversation import AcquisitionDiagnosticWriter, AuditLog
        prep = self.need("prepare")
        if (self.out / "run.json").exists():
            raise NotExercised("run already started in this evidence folder; verify it, then prepare afresh")
        self.close_microclaw("Run drives every acquisition from this process, with no serve CPU.")
        if os.cpu_count() != prep["cpu_count"]:
            raise NotExercised(f"os.cpu_count() is {os.cpu_count()} now, {prep['cpu_count']} at prepare")
        parsed, guard, ctrl = self.connect(prep["port"])
        disk = disk_check(ctrl.core, prep["disk"]["dataset_root"], prep["runs"])
        if disk["bytes_per_frame"] != prep["disk"]["bytes_per_frame"]:
            raise NotExercised("camera frame size changed since prepare; prepare a fresh gate")
        self.say(f"Disk: free {disk['free_bytes']} bytes; projected need {disk['projected_bytes']} bytes.")
        # __main__.run_session's names: the history stem is the session correlation id,
        # and the acquisition log is its sibling. No key, so no secret to redact.
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        session_id = f"{stamp}_microclaw_history"
        writer = AcquisitionDiagnosticWriter(
            AuditLog(self.out / f"{stamp}_microclaw_acquisitions.jsonl", enabled=True, secrets=()))
        subject = f"{PACKAGE}@{prep['digest']}"
        self.save("run", dict(started_at=now().isoformat(), disk=disk, session_id=session_id,
                              disclosure_line=product_disclosure(), python=sys.executable,
                              product=str(Path(tools.__file__).parent), cpu_count=os.cpu_count()))
        previous = tools.CONFIRM_FN
        tools.SESSION_GRANTS.clear()
        completed = 0
        try:
            for row in prep["runs"]:
                folder = self.out / "runs" / row["id"]
                folder.mkdir(parents=True)
                write_json(folder / "input.json", row["input"])
                prompts = []
                write_json(folder / "confirmations.json", prompts)
                tools.CONFIRM_FN = recording_confirm(prompts, subject,
                                                     lambda p, f=folder: write_json(f / "confirmations.json", p))
                self.say(f"RUN {completed + 1}/{len(prep['runs'])} {row['id']}")
                clock = dict(start_utc=now().isoformat(), start_perf=time.perf_counter())
                try:
                    raw = tools.execute_tool("run_timelapse", json.loads(json.dumps(row["input"])), ctrl, guard,
                                             acquisition_diagnostic_writer=writer,
                                             acquisition_session_id=session_id, tool_call_id=row["id"])
                finally:
                    clock.update(end_utc=now().isoformat(), end_perf=time.perf_counter())
                    write_json(folder / "clock.json", clock)
                result = json.loads(raw)
                write_json(folder / "result.json", result)
                jobs = (result.get("analysis") or {}).get("jobs") or []
                for index, job in enumerate(jobs):
                    self.wait_terminal(job, folder, index)
                try:
                    write_json(folder / "dataset.json", dataset_evidence(result))
                except Exception as exc:                       # scored as missing, not fatal
                    write_json(folder / "dataset.json", dict(error=f"{type(exc).__name__}: {exc}"))
                declined = [p for p in prompts if not p["approved"]]
                if declined:
                    raise RuntimeError(f"{row['id']}: declined an unexpected confirmation "
                                       f"kind={declined[0]['kind']} subject={declined[0]['subject']}")
                if "error" in result:
                    raise RuntimeError(f"{row['id']}: run_timelapse returned an error: {result['error']}")
                completed += 1
        finally:
            tools.CONFIRM_FN = previous
            writer.close()
            close_analysis_supervisor()
            self.save("run-end", dict(ended_at=now().isoformat(), runs_completed=completed,
                                      runs_planned=len(prep["runs"])))


def dataset_evidence(result):
    """Frame count and per-frame timestamp gaps, read with ndstorage after the run.

    The key is chosen by the product's own `_report_frame_spacing` (design/77);
    gaps it already put on the result are recorded, not re-derived.
    """
    from microclaw import tools
    path = result["dataset_path"]
    timing = result.get("timing") or {}
    source = "result timing"
    if not timing.get("observed_per_field_spacing"):
        timing = {"strategy": "gate_single_field"}
        tools._report_frame_spacing(timing, [dict(dataset_path=path)])
        source = "tools._report_frame_spacing on the saved dataset"
    return dict(dataset_path=path, frames=e3.count_frames(path), source=source,
                observed_per_field_spacing=timing.get("observed_per_field_spacing"),
                frame_timestamp_metadata_key=timing.get("frame_timestamp_metadata_key"),
                verification=timing.get("verification"))


def _gate_cleanup(gate):
    """83d's cleanup, only on the machine and store that prepare recorded as its own."""
    ownership = gate.out / "ownership.json"
    if not ownership.exists():
        return "no gate-owned store recorded; existing stores left alone"
    own = read_json(ownership)
    if (own.get("host") != socket.gethostname() or own.get("store") != str(gate.store_root.resolve())
            or own.get("evidence") != str(gate.out.resolve())):
        return "evidence from another machine or folder: cleanup skipped (re-score only)"
    state = gate83d.Gate.cleanup(gate)
    (gate.root / POINTER).unlink(missing_ok=True)
    if state["roots_present"] or state["store_present"]:
        raise RuntimeError(f"cleanup incomplete: {state}")
    return "gate store and TEST-ONLY roots removed; datasets left in place"


# --- verify -----------------------------------------------------------------------------------
def number(value, what="value"):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise NotExercised(f"{what} is {value!r}, not a finite number")
    return value


def separate(a, b):
    """Strict: separated only if every value of b lies beyond every value of a. Ties never separate."""
    if not a or not b:
        return "not separated"
    if min(b) > max(a):
        return "SEPARATED (higher)"
    if max(b) < min(a):
        return "SEPARATED (lower)"
    return "not separated"


def p95(values):
    return sorted(values)[math.ceil(0.95 * len(values)) - 1]    # nearest rank


def _diagnostics(out):
    rows = []
    for path in sorted(Path(out).glob("*_microclaw_acquisitions.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                continue                   # one corrupt line cannot hide the others
            if isinstance(item, dict):
                rows.append(item)
    return rows


def _norm(path):
    return os.path.normcase(os.path.normpath(str(path)))


def _load(path):
    if not Path(path).exists():
        raise NotExercised(f"missing {Path(path).parent.name}/{Path(path).name}")
    return read_json(path)


def _window_gaps(folder, row):
    """Metadata gaps (s) in time order, and the index of the first frame in the loaded window."""
    ds = _load(folder / "dataset.json")
    spacing = ds.get("observed_per_field_spacing")
    if not isinstance(spacing, dict) or len(spacing) != 1:
        raise NotExercised(f"no single-field metadata timestamps ({ds.get('verification') or ds.get('error')})")
    gaps = [number(v, "metadata gap") for v in next(iter(spacing.values()))]
    if len(gaps) != row["input"]["n_frames"] - 1:
        raise NotExercised(f"{len(gaps)} metadata gaps for {row['input']['n_frames']} planned frames")
    start = 0
    if row["condition"] != "none":
        start = _load(folder / "job-0.json")["result"]["output"]["frames_indexed_at_load_start"]
        if type(start) is not int or not 0 <= start < len(gaps):
            raise NotExercised(f"loaded window from frame {start!r} holds no gap")
    return gaps, start


def measurements(out, row, diagnostics):
    """Per-run statistics. Missing pieces are left out, never zero-filled."""
    folder = Path(out) / "runs" / row["id"]
    result = _load(folder / "result.json")
    stats = {}
    def put(key, fn):
        try:
            stats[key] = number(fn(), key)
        except (KeyError, TypeError, ValueError, NotExercised, OSError):
            pass
    put("duration_s", lambda: result["duration_s"])
    for label in ("mean_s", "p95_le_s", "max_s"):
        put(f"saved_callback_gap_{label} (whole run)", lambda l=label: result["inter_frame_gap_summary"][l])
    breakdown = result.get("duration_breakdown") or {}
    for phase in sorted((breakdown.get("phases") or {})):
        put(f"duration_breakdown.{phase}.total_s", lambda p=phase: breakdown["phases"][p]["total_s"])
    put("duration_breakdown.unaccounted_s (residual)", lambda: breakdown["unaccounted_s"])
    try:
        clock = _load(folder / "clock.json")
        built = [d for d in diagnostics if d.get("tool_call_id") == row["id"]
                 and d.get("type") == "acquisition_construction"]
        if len(built) == 1:
            put("tool_start_to_construction_s", lambda: (datetime.fromisoformat(built[0]["timestamp"])
                                                         - datetime.fromisoformat(clock["start_utc"])).total_seconds())
    except (NotExercised, ValueError, KeyError):
        pass
    try:
        gaps, start = _window_gaps(folder, row)
        window = gaps[start:]                  # both frames of each gap at or after the first loaded frame
        stats["metadata_gap_mean_s (window)"] = sum(window) / len(window)
        stats["metadata_gap_p95_s (window)"] = p95(window)
        stats["metadata_gap_max_s (window)"] = max(window)
        if row["type"] == "spaced":
            t = list(itertools.accumulate(gaps, initial=0.0))   # t_i - t_0, from the recorded gaps
            late = [t[i] - i * row["input"]["interval_s"] for i in range(start, len(t))]
            stats["lateness_p95_s (window)"] = p95(late)
            stats["lateness_max_s (window)"] = max(late)
    except (NotExercised, KeyError, TypeError, ValueError, OSError):
        pass
    return stats


def _check_plan(prep):
    runs = prep["runs"]
    if prep.get("selftest"):
        expected = plan(prep["digest"], prep["save_dir"], prep["cpu_count"], tuple(prep["sizes"]))
    else:
        expected = plan(prep["digest"], prep["save_dir"], prep["cpu_count"])
    if runs != expected:
        raise AssertionError("the recorded plan is not the gate's plan")
    return f"{len(runs)} runs as planned"


def verify(gate, *, cleanup=True, echo=True, measure=True):
    say = gate.say if echo else (lambda text: None)
    outcomes = {limb: [] for limb in LIMBS}

    def check(limb, run, fn):
        try:
            status, detail = "PASS", fn()
        except NotExercised as exc:
            status, detail = "NOT EXERCISED", str(exc)
        except AssertionError as exc:
            status, detail = "FAIL", str(exc) or "assertion failed"
        except Exception as exc:
            status, detail = "FAIL", f"{type(exc).__name__}: {exc}"
        outcomes[limb].append(dict(run=run, status=status, detail=detail))

    def need(condition, detail):
        if not condition:
            raise AssertionError(detail)
        return detail

    try:
        prep = gate.need("prepare")
        runs, cpu = prep["runs"], prep["cpu_count"]
    except Exception as exc:
        prep, runs, cpu = None, [], None
        for limb in outcomes:
            outcomes[limb].append(dict(run="prepare", status="NOT EXERCISED", detail=str(exc)))
    try:
        run_info = gate.need("run")
    except Exception:
        run_info = {}
    diagnostics = _diagnostics(gate.out)
    if prep is not None:
        check(1, "plan", lambda: _check_plan(prep))
    frame_bytes = (run_info.get("disk") or {}).get("bytes_per_frame")
    disclosure = run_info.get("disclosure_line")
    subject = f"{PACKAGE}@{prep['digest']}" if prep else None
    first_analysis = next((r["id"] for r in runs if r["condition"] != "none"), None)

    for position, row in enumerate(runs):
        folder = gate.out / "runs" / row["id"]
        n = row["input"]["n_frames"]
        loaded = row["condition"] != "none"
        result = lambda f=folder: _load(f / "result.json")
        output = lambda f=folder: _load(f / "job-0.json")["result"]["output"]

        def frames(row=row, folder=folder, n=n, result=result):
            r, ds = result(), _load(folder / "dataset.json")
            need(_load(folder / "input.json") == row["input"], "tool input differs from the plan")
            need("error" not in r, f"tool error: {r.get('error')}")
            mine = [d for d in diagnostics if d.get("tool_call_id") == row["id"]]
            built = [d for d in mine if d.get("type") == "acquisition_construction"]
            torn = [d for d in mine if d.get("type") == "acquisition_teardown_completion"]
            if not built or not torn:
                raise NotExercised("no construction/teardown diagnostic for this run")
            gaps = (r.get("inter_frame_gap_summary") or {}).get("count")
            need(len(built) == len(torn) == 1, f"{len(built)} constructions, {len(torn)} teardowns")
            need(built[0].get("frames_planned") == n and torn[0].get("frames_accounted") == n
                 and isinstance(gaps, int) and gaps + 1 == n and ds.get("frames") == n,
                 f"planned {n}; diagnostic planned {built[0].get('frames_planned')}, accounted "
                 f"{torn[0].get('frames_accounted')}; result gaps+1 {None if gaps is None else gaps + 1}; "
                 f"dataset holds {ds.get('frames')}")
            return f"{n} planned = accounted = result = dataset"
        check(1, row["id"], frames)

        def dispatch(row=row, folder=folder, loaded=loaded, result=result):
            r = result()
            jobs = (r.get("analysis") or {}).get("jobs") or []
            need(len(jobs) == int(loaded), f"{len(jobs)} jobs dispatched, expected {int(loaded)}")
            if not loaded:
                return "no job"
            record = _load(folder / "job-0.json")
            need(record.get("job_id") == jobs[0].get("job_id"), "job record is not this run's job")
            need(_norm(record.get("dataset")) == _norm(r.get("dataset_path")), "job dataset is not the run's")
            need(record.get("parameters") == row["input"]["analysis"]["parameters"],
                 f"job parameters {record.get('parameters')} differ from the plan")
            need(record.get("state") == "succeeded", f"state {record.get('state')}: {record.get('failure')}")
            need((record.get("lifecycle") or {}).get("writer") == "finished", f"lifecycle {record.get('lifecycle')}")
            return "one job, succeeded, writer finished"
        check(2, row["id"], dispatch)

        if loaded:
            def load_evidence(output=output):
                o = output()
                if not all(k in o for k in ("cpu_threads", "load_stopped_by", "load_cpu_ratio")):
                    raise NotExercised("the worker reported no load evidence")
                need(o["cpu_threads"] == cpu, f"cpu_threads {o['cpu_threads']}, C = {cpu}")
                need(o["load_stopped_by"] == "writer", f"load stopped by {o['load_stopped_by']}")
                ratio = number(o["load_cpu_ratio"], "load_cpu_ratio")
                detail = f"CPU ratio {ratio:.3f} vs {LOAD_FRACTION} x C = {LOAD_FRACTION * cpu:.2f}"
                if ratio < LOAD_FRACTION * cpu:
                    raise NotExercised(detail + ": this run was not loaded")
                return detail
            check(3, row["id"], load_evidence)

            def reads(folder=folder, output=output):
                o, saved = output(), _load(folder / "dataset.json").get("frames")
                if not isinstance(frame_bytes, int):
                    raise NotExercised("run.json records no bytes per frame")
                need(o.get("frames_read") == saved and o.get("read_errors") == 0
                     and isinstance(saved, int) and o.get("bytes_read") == saved * frame_bytes,
                     f"read {o.get('frames_read')} frames / {o.get('bytes_read')} bytes / "
                     f"{o.get('read_errors')} errors; saved {saved} frames = {saved and saved * frame_bytes} bytes")
                return f"{saved} frames, {o['bytes_read']} bytes, 0 errors"
            check(4, row["id"], reads)

            def priority(row=row, output=output):
                p = output().get("priority")
                if not isinstance(p, dict):
                    raise NotExercised("the worker reported no priority evidence")
                requested = "below_normal" if row["condition"] == "below" else "normal"
                need(p.get("requested") == requested and p.get("applied") is True
                     and p.get("read_back") is not None,
                     f"requested {p.get('requested')} (planned {requested}), applied {p.get('applied')}, "
                     f"read back {p.get('read_back')}, reason {p.get('reason')}")
                return f"{requested} applied, read back {p['read_back']}"
            check(5, row["id"], priority)

            def window(row=row, folder=folder, n=n, output=output):
                index = output().get("frames_indexed_at_load_start")
                if type(index) is not int:
                    raise NotExercised(f"frames_indexed_at_load_start is {index!r}")
                need(0 <= index <= n, f"frames_indexed_at_load_start {index} outside 0..{n}")
                detail = f"load began at frame {index}; {(n - index) / n:.1%} of frames after it"
                try:
                    _window_gaps(folder, row)
                except NotExercised as exc:
                    raise NotExercised(f"{detail}; {exc}") from None
                return detail
            check(6, row["id"], window)

        def consent(row=row, folder=folder, loaded=loaded):
            prompts = _load(folder / "confirmations.json")
            bad = [p for p in prompts if not p.get("approved")]
            need(not bad, f"declined: {[(p.get('kind'), p.get('subject')) for p in bad]}")
            analysis = [p for p in prompts if p.get("kind") == "analysis"]
            need(len(analysis) == int(loaded), f"{len(analysis)} analysis prompts, expected {int(loaded)}")
            need(all(p.get("subject") == subject for p in analysis), "analysis prompt names another release")
            detail = f"{len(prompts)} prompt(s): {[p.get('kind') for p in prompts]}"
            if row["id"] == first_analysis:
                if not disclosure:
                    raise NotExercised("the product under test has no D4 disclosure line")
                need(disclosure in analysis[0]["summary"].splitlines(),
                     "the first analysis confirmation lacks D4's disclosure line")
                detail += "; D4 line present exactly"
            return detail
        check(7, row["id"], consent)

        def overlap(position=position, folder=folder):
            clock = _load(folder / "clock.json")
            need(number(clock["end_perf"]) >= number(clock["start_perf"]), "run clock reversed")
            if not position:
                return "first run"
            earlier = gate.out / "runs" / runs[position - 1]["id"]
            before = _load(earlier / "clock.json")
            need(clock["start_perf"] > before["end_perf"], "started before the previous tool returned")
            if runs[position - 1]["condition"] == "none":
                return "after the previous tool returned"
            record = _load(earlier / "job-0.json")
            need(record.get("state") not in gate.store.ANALYSIS_NONTERMINAL,
                 f"previous job still {record.get('state')}")
            end = _load(earlier / "job-0-clock.json")
            start = datetime.fromisoformat(clock["start_utc"])
            need(start > datetime.fromisoformat(end["terminal_record_utc"])
                 and start > datetime.fromisoformat(end["observed_utc"])
                 and clock["start_perf"] > end["observed_perf"],
                 f"started {clock['start_utc']}, before the previous job's terminal record "
                 f"{end['terminal_record_utc']}")
            return f"{(start - datetime.fromisoformat(end['terminal_record_utc'])).total_seconds():.3f} s after the previous job ended"
        check(8, row["id"], overlap)

    statuses = {}
    for limb, rows in outcomes.items():
        statuses[limb] = ("FAIL" if any(r["status"] == "FAIL" for r in rows) else
                          "NOT EXERCISED" if not rows or any(r["status"] == "NOT EXERCISED" for r in rows)
                          else "PASS")
        say(f"{statuses[limb]}: limb {limb} {LIMBS[limb]}")
        for r in rows:
            say(f"    {r['status']}: {r['run']}: {r['detail']}")
    if measure and prep is not None:
        print_measurement(gate, prep, diagnostics, say)
    cleaned = None
    if cleanup:
        try:
            cleaned = _gate_cleanup(gate)
            say(f"CLEANUP: {cleaned}")
        except Exception as exc:
            cleaned = False
            say(f"CLEANUP FAILED: {type(exc).__name__}: {exc}")
    bad = sum(v != "PASS" for v in statuses.values())
    write_json(gate.out / "verify.json", dict(statuses=statuses, runs=outcomes, cleanup=cleaned,
                                              scored_at=now().isoformat()))
    gate.last_results = statuses
    say(f"RESULT: {bad} failed or not exercised limbs / {len(LIMBS)}; measurement printed above")
    return bad + (cleaned is False)


def print_measurement(gate, prep, diagnostics, say):
    say("MEASUREMENT (printed, not pass/fail). " + CAVEAT)
    if prep.get("selftest"):
        say("SELFTEST PLAN: reduced sizes and fake acquisitions that cannot contend; these numbers mean nothing.")
    say("Repetition 0 excluded. '(window)' = frames at or after the worker's frames_indexed_at_load_start "
        "(all frames for 'none'); '(whole run)' = every frame; p95 is nearest rank. Lateness is "
        "t_i - t_0 - i x interval_s from the recorded metadata gaps (rounded to 1 us by the product). "
        "A verdict names the second condition relative to the first; separated only if every run of one "
        "lies strictly beyond every run of the other. Limbs 3 and 6 say which loaded runs qualified.")
    stats = {}
    for row in prep["runs"]:
        if row["repetition"]:
            try:
                stats[row["id"]] = measurements(gate.out, row, diagnostics)
            except Exception as exc:
                say(f"    MEASUREMENT UNAVAILABLE {row['id']}: {type(exc).__name__}: {exc}")
    expected = prep["sizes"][0] - 1 if prep.get("selftest") else PLAN_SIZES[0] - 1
    fmt = lambda v: "--" if v is None else f"{v:.6g}"
    for kind in TYPES:
        rows = [r for r in prep["runs"] if r["type"] == kind and r["repetition"]]
        keys = sorted({k for r in rows for k in stats.get(r["id"], {})})
        for key in keys:
            cells = {c: [stats.get(r["id"], {}).get(key) for r in rows if r["condition"] == c]
                     for c in CONDITIONS}
            say(f"{kind} | {key} | " + " | ".join(
                f"{c}=[{', '.join(fmt(v) for v in cells[c])}]" for c in CONDITIONS))
            verdicts = []
            for a, b in itertools.combinations(CONDITIONS, 2):
                complete = all(len(cells[c]) == expected and None not in cells[c] for c in (a, b))
                verdicts.append(f"{a}/{b}: " + (separate(cells[a], cells[b]) if complete
                                                  else "UNAVAILABLE (incomplete cell)"))
            say(f"{kind} | {key} | " + "; ".join(verdicts))
    say(CAVEAT)


# --- selftest ---------------------------------------------------------------------------------
class BridgeCore(e3.BridgeCore):
    """83e-3's Core plus what MicroscopeController and validate_live_rig read at startup;
    every collection is a size()/get(i) vector whose __iter__ raises."""
    def __init__(self, **_):
        super().__init__()
    def get_version_info(self): return "selftest bridge"
    def get_device_type(self, device): return {"Camera": 2, "XY": 6, "Z": 5}.get(device, 0)
    def get_device_property_names(self, device): return e3.StrVector([])
    def get_available_config_groups(self): return e3.StrVector([])
    def get_available_configs(self, group): return e3.StrVector([])
    def get_available_pixel_size_configs(self): return e3.StrVector([])
    def get_shutter_device(self): return ""
    def get_property(self, device, prop): return ""


class BridgeStudio:
    def __init__(self, **_): pass
    def live(self): return self
    def app(self): return self
    def is_live_mode_on(self): return False
    def set_live_mode_on(self, value): pass
    def refresh_gui_from_cache(self): pass


class Backend(e3._Backend):
    """83e-3's dispatched backend, plus what this gate reads: an ElapsedTime-ms tag per
    frame and an index flushed per frame, so the worker tails a growing dataset
    (ndstorage's put_image writes pixels before the index entry). A frame takes one
    10 ms exposure, as the plan asks; frames still arrive inside __exit__."""
    FRAME_S = 0.01

    def __exit__(self, *exc):
        import numpy as np
        for event in self._events:
            for single in event if isinstance(event, list) else [event]:
                wait = self._start + float(single.get("min_start_time", 0)) - time.perf_counter()
                time.sleep(max(wait, 0) + self.FRAME_S)
                metadata = {"ElapsedTime-ms": (time.perf_counter() - self._start) * 1000}
                self._storage.put_image(dict(single["axes"]), np.zeros((32, 32), np.uint16), metadata)
                self._storage._index_file.flush()
                if self._saved is not None:
                    self._saved(dict(single["axes"]), None)
        self._storage.finish()
        return False


class FakeAcquisition:
    def __new__(cls, **kwargs):
        return Backend(**kwargs)

    def __init__(self, **kwargs):
        raise AssertionError("the dispatcher's __init__ never runs")


class OfflineInstall:
    listening = None
    def uv(self):
        import uv
        return uv.find_uv_bin()
    def base_python(self): return sys._base_executable
    def bin(self): return []
    def registry(self): return []
    def serve_listening(self): return self.listening


SAFETY_YAML = """schema_version: 3
reviewed: true
property_authorization: {mode: guaranteed, allowed_categorical: [], denied: []}
stage: {x_min: -100, x_max: 100, y_min: -100, y_max: 100, z_min: -100, z_max: 100}
camera: {max_exposure_ms: 500}
acquisition:
  max_frames: 10000
  max_duration_s: 3600
  max_bytes: 50000000000
  max_illuminated_ms: 600000
  max_session_illuminated_ms: 1800000
  confirm_above_frames: 10
  confirm_above_duration_s: 300
  confirm_above_bytes: 5000000000
  confirm_above_illuminated_ms: 60000
"""


def _plan_checks():
    runs = plan("d", "root", 4)
    assert len(runs) == 42 and len({r["id"] for r in runs}) == 42, "42 uniquely named runs"
    assert sum(r["input"]["n_frames"] for r in runs) == 21 * 1000 + 21 * 100
    measured = [[(r["type"], r["condition"]) for r in runs if r["repetition"] == rep] for rep in range(1, 7)]
    for slot in range(6):
        assert {order[slot] for order in measured} == set(CELLS), f"position {slot} is not a Latin column"
    pairs = [(o[i], o[i + 1]) for o in measured for i in range(5)]
    assert len(pairs) == len(set(pairs)) == 30, "every ordered pair of cells is adjacent exactly once"
    assert all(r["input"]["analysis"]["parameters"]["cpu_threads"] == 4 for r in runs if r["condition"] != "none")
    assert separate([1, 2, 3, 4, 5, 6], [7, 8, 9, 10, 11, 12]) == "SEPARATED (higher)"
    assert separate([7, 8, 9, 10, 11, 12], [1, 2, 3, 4, 5, 6]) == "SEPARATED (lower)"
    assert separate([1, 2, 3, 4, 5, 6], [6, 7, 8, 9, 10, 11]) == "not separated", "a tie never separates"
    assert separate([1, 3, 5], [2, 4, 6]) == "not separated"
    assert p95(list(range(1, 21))) == 19 and p95([5.0]) == 5.0


def _mutations(control, prep):
    runs = prep["runs"]
    below = next(r for r in runs if r["condition"] == "below")
    loaded = next(r for r in runs if r["condition"] == "loaded")
    first = next(r for r in runs if r["condition"] != "none")
    after_job = next(r for i, r in enumerate(runs) if i and runs[i - 1]["condition"] != "none")
    prior = runs[runs.index(after_job) - 1]

    def edit(run, name, change):
        path = control / "runs" / run["id"] / name
        data = read_json(path)
        change(data)
        write_json(path, data)

    def overlap(clock):
        end = read_json(control / "runs" / prior["id"] / "job-0-clock.json")
        clock["start_utc"] = (datetime.fromisoformat(end["terminal_record_utc"])
                              .replace(microsecond=0)).isoformat()
    out = lambda change: (lambda job: change(job["result"]["output"]))
    return [
        (1, "result reports one frame fewer", lambda: edit(runs[0], "result.json",
                                                           lambda d: d["inter_frame_gap_summary"].update(count=d["inter_frame_gap_summary"]["count"] - 1))),
        # A job left running also makes the next run overlap it: limb 8 is collateral.
        (2, "job record still running", lambda: edit(below, "job-0.json", lambda d: d.update(state="running")), {8}),
        (2, "writer unknown", lambda: edit(loaded, "job-0.json", lambda d: d["lifecycle"].update(writer="unknown"))),
        (3, "loaded ratio 0.1", lambda: edit(loaded, "job-0.json", out(lambda o: o.update(load_cpu_ratio=0.1)))),
        (3, "load stopped by max_s", lambda: edit(below, "job-0.json", out(lambda o: o.update(load_stopped_by="max_s")))),
        (4, "observer missed one frame", lambda: edit(loaded, "job-0.json", out(lambda o: o.update(frames_read=o["frames_read"] - 1)))),
        (5, "below run applied: false", lambda: edit(below, "job-0.json", out(lambda o: o["priority"].update(applied=False)))),
        (6, "load began after the last frame", lambda: edit(below, "job-0.json", out(
            lambda o: o.update(frames_indexed_at_load_start=below["input"]["n_frames"])))),
        (7, "D4 line removed", lambda: edit(first, "confirmations.json", lambda ps: [
            p.update(summary="\n".join(l for l in p["summary"].splitlines()
                                       if l != read_json(control / "run.json")["disclosure_line"])) for p in ps])),
        (7, "unexpected prompt declined", lambda: edit(runs[0], "confirmations.json", lambda ps: ps.append(
            dict(summary="x", kind="illumination", subject=None, approved=False, timestamp=now().isoformat())))),
        (8, "start before the previous job's terminal record", lambda: edit(after_job, "clock.json", overlap)),
    ]


def selftest(baseline_only=False, sizes=SELFTEST_SIZES):
    from unittest.mock import patch
    if sizes[0] < 2 or min(sizes[1:]) < 3:
        raise SystemExit("selftest needs at least 2 repetitions and 3 frames per run")
    _plan_checks()
    failures = 0
    with tempfile.TemporaryDirectory(prefix="block83e4-") as temporary:
        base = Path(temporary)
        env = {name: str(base / name) for name in ("XDG_DATA_HOME", "LOCALAPPDATA", "XDG_CONFIG_HOME", "APPDATA")}
        env.update(UV_OFFLINE="1", UV_CACHE_DIR=str(base / "uv-cache"))
        with patch.dict(os.environ, env):
            import microclaw
            from microclaw import paths, controller, tools
            from microclaw.completed_dataset import close_analysis_supervisor
            print(f"SELFTEST product under test: {Path(microclaw.__file__).parent}", flush=True)
            config = paths.default_safety_config()
            config.parent.mkdir(parents=True)
            config.write_text(SAFETY_YAML, encoding="utf-8")
            mm_root = base / "fake-mm"
            (mm_root / "plugins").mkdir(parents=True)
            root = paths.user_data_dir()
            root.mkdir(parents=True)
            out = base / "evidence"
            fake = OfflineInstall()
            gate = Gate(root, out, fake=fake)
            with patch.object(controller, "Core", BridgeCore), patch.object(controller, "Studio", BridgeStudio), \
                    patch.object(controller.MicroscopeController, "get_mm_app_dir", return_value=str(mm_root)), \
                    patch.object(tools, "Acquisition", FakeAcquisition):
                gate.prepare(sizes=sizes)
                try:
                    gate.run()
                except Exception as exc:        # a pre-block tree may stop; verify scores what exists
                    gate.say(f"SELFTEST run stopped: {type(exc).__name__}: {exc}")
            close_analysis_supervisor()
            prep = gate.need("prepare")
            backup = base / "evidence-unmutated"
            shutil.copytree(out, backup)
            gate.say("SELFTEST unmutated arm (real worker; its CPU ratios and load windows are observations of "
                     "fakes, never asserted)")
            bad = verify(gate, cleanup=True)
            store_gone = not gate.store_root.exists()
            gate.say(f"SELFTEST unmutated RESULT: {bad} failed or not exercised limbs / {len(LIMBS)}; "
                     f"store removed: {store_gone}; {json.dumps(gate.last_results)}")
            if baseline_only:
                return 0
            structural = all(gate.last_results[i] == "PASS" for i in (1, 2, 4, 5, 7, 8)) and store_gone
            failures += not structural
            gate.say(f"SELFTEST {'ok' if structural else 'WRONG'}: limbs 1, 2, 4, 5, 7, 8 PASS on real evidence "
                     "and cleanup removed the store")
            for row in prep["runs"]:
                if row["condition"] != "none":
                    o = read_json(out / "runs" / row["id"] / "job-0.json")["result"]["output"]
                    ok = (o["cpu_threads"] == prep["cpu_count"] and o["load_stopped_by"] == "writer"
                          and type(o["frames_indexed_at_load_start"]) is int)
                    failures += not ok
                    if not ok:
                        gate.say(f"SELFTEST WRONG: {row['id']} load evidence {o}")

            # Refusals that need no rig: serve listening, and the consent recorder.
            fake.listening = "a selftest listener"
            try:
                gate.close_microclaw("Probe.")
                ok = False
            except NotExercised as exc:
                ok = str(SERVE_PORT) in str(exc)
            fake.listening = None
            failures += not ok
            gate.say(f"SELFTEST {'ok' if ok else 'WRONG'}: a listener on port {SERVE_PORT} refuses the run")
            prompts = []
            confirm = recording_confirm(prompts, "pkg@d", lambda p: None)
            answers = [confirm("a", kind="analysis", subject="pkg@d"),
                       confirm("b", kind="acquisition", subject="threshold"),
                       confirm("c", kind="analysis", subject="other@d"),
                       confirm("d", kind="illumination"), confirm("e", kind="acquisition")]
            ok = answers == [True, True, False, False, False] and len(prompts) == 5
            failures += not ok
            gate.say(f"SELFTEST {'ok' if ok else 'WRONG'}: consent approves analysis of this release and the "
                     f"size prompt only, recording all five: {answers}")

            # Scorer discrimination on a synthetic positive control (never rig evidence):
            # the fakes cannot load a CPU, so ratio = C and a window from frame 0 are written in.
            control = base / "synthetic-control"
            shutil.copytree(backup, control)
            for path in control.glob("runs/*/job-0.json"):
                job = read_json(path)
                job["result"]["output"].update(load_cpu_ratio=float(prep["cpu_count"]),
                                               frames_indexed_at_load_start=0)
                write_json(path, job)
            shutil.copytree(control, base / "synthetic-control-clean")
            arm = Gate(root, control, fake=fake)
            bad = verify(arm, cleanup=False, echo=False)
            ok = bad == 0
            failures += not ok
            gate.say(f"SELFTEST {'ok' if ok else 'WRONG'}: synthetic control RESULT: {bad} failed or not "
                     f"exercised limbs / {len(LIMBS)} {json.dumps(arm.last_results)}")
            for limb, label, mutate, *collateral in _mutations(control, prep):
                shutil.rmtree(control)
                shutil.copytree(base / "synthetic-control-clean", control)
                mutate()
                bad = verify(arm, cleanup=False, echo=False, measure=False)
                verdict = arm.last_results[limb]
                allowed = collateral[0] if collateral else set()
                others = {k: v for k, v in arm.last_results.items() if v != "PASS" and k != limb and k not in allowed}
                ok = verdict in ("FAIL", "NOT EXERCISED") and not others
                failures += not ok
                gate.say(f"SELFTEST {'ok' if ok else 'WRONG'}: mutation limb {limb} ({label}) -> {verdict}; "
                         f"RESULT: {bad} failed or not exercised limbs / {len(LIMBS)}; other non-pass: {others}")
            # A malformed record affects only itself.
            shutil.rmtree(control)
            shutil.copytree(base / "synthetic-control-clean", control)
            broken = next(r for r in prep["runs"] if r["condition"] == "below")
            (control / "runs" / broken["id"] / "job-0.json").write_text("{broken", encoding="utf-8")
            verify(arm, cleanup=False, echo=False, measure=False)
            scored = read_json(control / "verify.json")["runs"]
            others_scored = all(r["status"] == "PASS" for r in scored["3"] if r["run"] != broken["id"])
            ok = (others_scored and len(scored["3"]) == sum(r["condition"] != "none" for r in prep["runs"])
                  and any(r["run"] == broken["id"] and r["status"] == "FAIL" for r in scored["3"]))
            failures += not ok
            gate.say(f"SELFTEST {'ok' if ok else 'WRONG'}: a malformed job record fails its own run and every "
                     "other run is still scored")
            gate.say(f"SELFTEST {'PASSED' if not failures else f'FAILED ({failures})'}; no wall-clock assertions")
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("phase", choices=("prepare", "run", "verify", "selftest"))
    parser.add_argument("--out", type=Path)
    parser.add_argument("--port", type=int, default=4827)
    parser.add_argument("--baseline-only", action="store_true", help="selftest: the unmutated arm only")
    parser.add_argument("--selftest-sizes", type=int, nargs=3, default=list(SELFTEST_SIZES),
                        metavar=("REPS", "BURST", "SPACED"), help="selftest only; the gate's plan is fixed")
    args = parser.parse_args()
    if args.phase == "selftest":
        return 1 if selftest(args.baseline_only, tuple(args.selftest_sizes)) else 0
    if args.out is None:
        parser.error("--out is required")
    from microclaw import paths
    gate = Gate(paths.user_data_dir(), args.out)
    try:
        if args.phase == "verify":
            return 1 if verify(gate) else 0
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
