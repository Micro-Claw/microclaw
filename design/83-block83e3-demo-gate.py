"""83e-3: plan-time package analysis on live acquisitions, on the demo machine.

Reuses 83d's gate plumbing (launch detection by nonce, operator prompts, cleanup)
and 83e-2's confirmation-audit reader. The operator drives seven short chat turns;
every expected count is derived from the session's own artifacts -- serve's
history, confirmation audit and acquisition diagnostics, the store's job records,
the datasets and the exported script -- never from a constant.

`selftest` drives the real execute_tool path, the real supervisor, the real fixture
worker and the real exporter against bridge-shaped fakes (a Core whose collections
are size()/get(i) with a raising __iter__; an Acquisition that dispatches like the
real constructor, writes a real NDTiff through ndstorage and fires image_saved_fn
inside __exit__). It then mutates one artifact per limb to show that limb FAILs.
`selftest --baseline-only` runs only the unmutated arm, for a pre-83e-3 tree.
"""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tokenize
import traceback

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("gate83e2", HERE / "83-block83e2-demo-gate.py")
gate83e2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate83e2)
gate83d = gate83e2.gate83d
FIXTURES, NotExercised = gate83d.FIXTURES, gate83d.NotExercised
read_json, write_json, now = gate83d.read_json, gate83d.write_json, gate83d.now

PHASES = ("prepare", "session", "verify")
PACKAGE = "fixture-lab/executable-fixture"
ADAPTER = PACKAGE + ":observe_dataset"
POINTER = "83e3-gate-evidence.txt"
FINAL = "dataset writer finished\n"      # the fixture's final artifact (fixture_worker/runner.py)
MOVIE = {"n_frames": 20, "interval_s": 0, "exposure_ms": 10}
POSITION_PROTOCOL = {"n_frames": 2, "interval_s": 1, "exposure_ms": 10}
SERVE_PORT = 8000
OPERATOR_JUDGED = "OPERATOR-JUDGED"


def plan_turns(digest, save_dir, xy):
    """The chat turns. The selftest executes exactly these tool inputs."""
    analysis = {"adapter": ADAPTER, "release_digest": digest, "parameters": {}}
    movie = dict(MOVIE, save_dir=save_dir)
    x, y = xy
    positions = [{"name": "e3p1", "x_um": x, "y_um": y}, {"name": "e3p2", "x_um": x, "y_um": y}]
    return [
        dict(n=1, tool="run_timelapse", input=dict(movie, name="e3decline", analysis=analysis),
             note="When it reports the analysis was declined, do not retry.",
             click="A banner appears: click 'Decline'."),
        dict(n=2, tool="run_timelapse", input=dict(movie, name="e3movie", analysis=analysis),
             note="", click="A banner appears: click 'Approve for this session'."),
        dict(n=3, tool="run_timelapse", input=dict(movie, name="e3movie", analysis=analysis),
             note="It repeats the previous call on purpose, name included.",
             click="Expect NO analysis banner: the session approval covers it."),
        dict(n=4, tool="run_timelapse", input=dict(movie, name="e3movie"),
             note="It repeats the previous call on purpose, without analysis.",
             click="Expect NO analysis banner: this call has no analysis."),
        dict(n=5, tool="run_multiposition_acquisition",
             input={"protocol": "timelapse", "positions": positions, "protocol_params": dict(POSITION_PROTOCOL),
                    "save_dir": save_dir, "name": "e3pos", "analysis": analysis},
             note="Both positions are the same point on purpose.",
             click="Expect NO analysis banner: the session approval covers it."),
        dict(n=6, tool=None),   # the package panel
        dict(n=7, tool="export_session_script", input={"output_path": save_dir + "-export.py"},
             note="", click="No banner."),
    ]


def turn_text(turn):
    text = (f"Call {turn['tool']} exactly once with exactly these arguments, and call no other tool "
            f"before it: {json.dumps(turn['input'])} . Do not change, add or drop any value.")
    return text + (" " + turn["note"] if turn.get("note") else "")


class Gate(gate83d.Gate):
    current_turn = None

    def launch_desktop(self, why):
        """83d's launch, rewritten: 83e-2's wording named a button that no longer exists."""
        if self.fake:
            return self.fake.launch()
        before = self.launch_lines()
        self.ask(f"{why} Launch Microclaw from its DESKTOP ICON now and wait for it to open in "
                 "Firefox. Leave the 'Community skill packages' panel closed until this program "
                 "asks for it. Then type DONE.", "launched")
        deadline = time.perf_counter() + 180
        while time.perf_counter() < deadline:
            new = self.launch_lines()[len(before):]
            if new:
                match = re.search(r" slot=([ab]) nonce=(\S+)\s*$", new[0])
                health = self.root / "launch-health.txt"
                if match and health.exists() and health.read_text(encoding="ascii").strip() == match[2]:
                    return {"slot": match[1], "nonce": match[2], "line": new[0]}
            time.sleep(0.25)
        raise NotExercised("no health marker matching the first new launch's nonce within 180 s")

    def cleanup(self):
        result = super().cleanup()
        (self.root / POINTER).unlink(missing_ok=True)
        return result

    def current_xy(self):
        if self.fake:
            return self.fake.xy()
        try:
            from pycromanager import Core
            core = Core()
            return round(float(core.get_x_position()), 3), round(float(core.get_y_position()), 3)
        except Exception as exc:
            raise NotExercised(f"could not read the XY stage over the Micro-Manager bridge: "
                               f"{type(exc).__name__}: {exc}. Is Micro-Manager open with the "
                               "bridge (port 4827) running?") from exc

    def serve_pid(self):
        """The process listening on serve's port. Locale-proof: no state column is read."""
        if self.fake:
            return self.fake.serve_pid()
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"], stdin=subprocess.DEVNULL,
                             capture_output=True, text=True, timeout=60).stdout
        pids = set()
        for line in out.splitlines():
            parts = line.split()
            if (len(parts) >= 5 and parts[0].upper() == "TCP" and parts[1].endswith(f":{SERVE_PORT}")
                    and parts[2] in ("0.0.0.0:0", "[::]:0") and parts[-1].isdigit()):
                pids.add(int(parts[-1]))
        if len(pids) != 1:
            raise NotExercised(f"expected one process listening on port {SERVE_PORT}, found {sorted(pids)}")
        return pids.pop()

    def prepare(self):
        if self.store_root.exists():
            raise NotExercised(f"{self.store_root} already exists; this gate creates and removes "
                               "its own store. Send the evidence folder instead of deleting it.")
        self.close_microclaw("Prepare installs a release in this process.")
        builder = gate83d.load_builder()
        releases = self.out / "releases"
        releases.mkdir(exist_ok=True)
        write_json(self.store_root / "trust" / "roots.json", read_json(FIXTURES / "trust" / "roots-TEST-ONLY.json"))
        policy = self.store.store_trust_policy(read_json(FIXTURES / "trust" / "policy-TEST-ONLY.json"))
        artifact = releases / "executable-1.0.0.zip"
        intake = builder.build_release(FIXTURES / "executable", artifact, version="1.0.0")
        self.store.install(intake, artifact, policy=policy, now=now(), uv_executable=self.uv(),
                           retained_digests=frozenset(), find_links=(FIXTURES / "wheels").resolve(),
                           base_python=self.base_python())
        digest = intake["artifact_digest"]
        self.say(f"installed executable-fixture 1.0.0: {digest}")
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        self.save("prepare", {"digest": digest, "stamp": stamp, "save_dir": f"block83e3-{stamp}",
                              "prepared_at": now().isoformat()})

    def session(self):
        prep = self.need("prepare")
        started = now().isoformat()
        xy = self.current_xy()
        turns = plan_turns(prep["digest"], prep["save_dir"], xy)
        launch = self.launch_desktop("Session phase.")
        answers = {}
        for turn in turns:
            self.current_turn = turn
            if turn["tool"] is None:
                self.say("TURN 6: in Firefox, click 'Community skill packages' to expand it. Only read "
                         "it: do not click any button inside it.")
                answers["panel"] = self.ask(
                    "Type the sentence in that panel that mentions run_mda, exactly as shown. "
                    "If there is none, type NONE. Then click 'Community skill packages' again to "
                    "close it.", "panel")
                continue
            self.say(f"TURN {turn['n']}: copy this into Microclaw's message box unchanged, then press Enter:")
            self.say("    " + turn_text(turn))
            self.say("    " + turn["click"] + " If the agent asks whether to go ahead, answer "
                     "'yes, go ahead'. If a separate banner about the acquisition's size appears, "
                     "click 'Approve'.")
            answers[f"t{turn['n']}"] = self.ask("Type DONE when the agent has finished replying.",
                                                f"t{turn['n']}")
        self.save("session", {"started_at": started, "launch": launch, "xy": xy, "turns": turns,
                              "answers": answers})


# --- the session's own artifacts -------------------------------------------------------------
def _ts(value):
    return datetime.fromisoformat(value)


def _jsonl(root, pattern, since):
    rows = []
    for path in sorted(Path(root).glob(pattern)):
        if datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) < since:
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def recorded_calls(root, since):
    """Every tool call in serve's history, in order, with its parsed result."""
    calls, by_id = [], {}
    for message in _jsonl(root, "*_microclaw_history.jsonl", since):
        content = message.get("content") if isinstance(message, dict) else None
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                call = dict(id=block["id"], name=block["name"], input=block.get("input") or {}, result=None)
                calls.append(call)
                by_id[call["id"]] = call
            elif block.get("type") == "tool_result" and block.get("tool_use_id") in by_id:
                raw = block.get("content")
                try:
                    parsed = json.loads(raw) if isinstance(raw, str) else None
                except ValueError:
                    parsed = None
                by_id[block["tool_use_id"]]["result"] = parsed if isinstance(parsed, dict) else {}
    return [c for c in calls if c["result"] is not None]


def history_jobs(call):
    analysis = call["result"].get("analysis")
    jobs = analysis.get("jobs") if isinstance(analysis, dict) else None
    return [j for j in jobs if isinstance(j, dict)] if isinstance(jobs, list) else []


def norm(path):
    return os.path.normcase(os.path.normpath(str(path)))


def count_frames(dataset):
    try:
        from ndstorage import Dataset
    except ImportError:
        from pycromanager import Dataset
    opened = Dataset(str(dataset))
    try:
        return len(opened.get_image_coordinates_list())
    finally:
        opened.close()


def code_without_comments(source):
    kept = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type not in (tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE):
            kept.append(token.string)
    return " ".join(kept)


def verify(gate, *, cleanup=True):
    results = {}

    def score(name, fn):
        try:
            detail = fn()
            if isinstance(detail, tuple) and detail and detail[0] == OPERATOR_JUDGED:
                results[name] = (OPERATOR_JUDGED, detail[1])
            else:
                results[name] = ("PASS", detail)
        except NotExercised as exc:
            results[name] = ("NOT EXERCISED", str(exc))
        except AssertionError as exc:
            results[name] = ("FAIL", str(exc) or traceback.format_exc(limit=1))
        except Exception as exc:
            results[name] = ("FAIL", f"{type(exc).__name__}: {exc}")

    try:
        prep, sess = gate.need("prepare"), gate.need("session")
    except NotExercised as exc:
        results["session evidence"] = ("NOT EXERCISED", str(exc))
        if cleanup:
            score("9 cleanup removed store and TEST-ONLY roots", lambda: _clean(gate))
        return _report(gate, results)

    digest, subject = prep["digest"], f"{PACKAGE}@{prep['digest']}"
    since = _ts(sess["started_at"])
    deadline = time.perf_counter() + (30 if gate.fake else 180)
    while (any(r.get("state") in gate.store.ANALYSIS_NONTERMINAL for _, r in gate.store._analysis_records())
           and time.perf_counter() < deadline):
        time.sleep(0.25)
    calls = recorded_calls(gate.root, since)
    diags = _jsonl(gate.root, "*_microclaw_acquisitions.jsonl", since)
    rows = gate83e2.audit_rows(gate.root, since)
    store_jobs = {r.get("job_id"): r for _, r in gate.store._analysis_records()}
    analysis_calls = [c for c in calls if isinstance(c["input"].get("analysis"), dict)]
    all_jobs = [(c, j) for c in calls for j in history_jobs(c)]

    def of(call, kind):
        return sorted((d for d in diags if d.get("tool_call_id") == call["id"] and d.get("type") == kind),
                      key=lambda d: d["timestamp"])

    def construction_for(dataset):
        match = [d for d in diags if d.get("type") == "acquisition_construction"
                 and norm(d.get("dataset_path")) == norm(dataset)]
        return match[0] if len(match) == 1 else None

    def teardown_for(dataset):
        match = [d for d in diags if d.get("type") == "acquisition_teardown_completion"
                 and norm(d.get("dataset_path")) == norm(dataset)]
        return match[0] if len(match) == 1 else None

    def need_jobs():
        if not all_jobs:
            raise NotExercised("no recorded call carries an analysis job (was history saving off, "
                               "or does this build not dispatch analysis?)")

    def consent():
        decisions = [r for r in rows if r.get("decision", "").startswith(("approved", "auto-approved", "declined"))]
        reached = [c for c in analysis_calls if of(c, "acquisition_construction")]
        declined = [c for c in analysis_calls if c["result"].get("cancelled") is True]
        if not reached or not declined:
            raise NotExercised(f"{len(reached)} analysis calls constructed an acquisition and "
                               f"{len(declined)} were declined; this limb needs at least one of each")
        detail = []
        for c in reached:
            earlier = calls[:calls.index(c)]
            ends = [d["timestamp"] for e in earlier for d in diags if d.get("tool_call_id") == e["id"]]
            lower = max([_ts(t) for t in ends], default=since)
            first = _ts(of(c, "acquisition_construction")[0]["timestamp"])
            window = [r for r in decisions if lower < _ts(r["timestamp"]) < first
                      and not r["decision"].startswith("declined")]
            assert len(window) == 1, (f"call {c['id']}: {len(window)} approving analysis decisions between "
                                      f"{lower.isoformat()} and its construction {first.isoformat()}; expected 1")
            assert window[0].get("subject") == subject, (c["id"], window[0].get("subject"))
            detail.append(f"{c['id'][-6:]}:{window[0]['decision'].split(':')[0]} "
                          f"{(first - _ts(window[0]['timestamp'])).total_seconds():.3f}s before construction")
        roots = {}
        for c in reached:
            for d in of(c, "acquisition_construction"):
                roots.setdefault(c["input"].get("save_dir"), set()).add(Path(d["dataset_path"]).parent)
        for c in declined:
            assert "error" in c["result"], f"declined call {c['id']} result has no top-level error: {c['result']}"
            assert not of(c, "acquisition_construction"), f"declined call {c['id']} constructed an acquisition"
            assert not history_jobs(c), f"declined call {c['id']} recorded jobs"
            name = c["input"].get("name", "")
            for root in roots.get(c["input"].get("save_dir"), ()):
                if c["name"] == "run_timelapse":
                    made = [p.name for p in Path(root).iterdir() if p.name.startswith(name)]
                    assert not made, f"declined call {c['id']} left datasets {made} in {root}"
        human_declines = [r for r in decisions if r["decision"].startswith("declined") and r.get("subject") == subject]
        assert len(human_declines) == len(declined), (f"{len(human_declines)} declined rows for "
                                                      f"{len(declined)} cancelled calls")
        return "; ".join(detail) + f"; {len(declined)} decline(s) acquired nothing"

    def collision():
        groups = {}
        for c in analysis_calls:
            if c["name"] == "run_timelapse" and of(c, "acquisition_construction"):
                groups.setdefault((c["input"].get("save_dir"), c["input"].get("name")), []).append(c)
        groups = {k: v for k, v in groups.items() if len(v) >= 2}
        if not groups:
            raise NotExercised("no two approved analysis timelapses share a save_dir and name")
        detail = []
        for (_, name), group in groups.items():
            seen = []
            for c in group:
                built = of(c, "acquisition_construction")
                jobs = history_jobs(c)
                assert len(built) == 1 and len(jobs) == 1, (c["id"], len(built), len(jobs))
                dataset = built[0]["dataset_path"]
                assert norm(jobs[0]["dataset"]) == norm(dataset), (jobs[0]["dataset"], dataset)
                assert norm(c["result"].get("dataset_path")) == norm(dataset), (c["result"].get("dataset_path"), dataset)
                record = store_jobs.get(jobs[0]["job_id"]) or {}
                assert norm(record.get("dataset")) == norm(dataset), ("job record", record.get("dataset"), dataset)
                assert Path(dataset).is_dir(), f"{dataset} does not exist"
                seen.append(dataset)
            assert len({norm(s) for s in seen}) == len(seen), f"datasets are not distinct: {seen}"
            requested = norm(Path(seen[0]).parent / name)
            assert all(norm(s) != requested for s in seen[1:]), f"a later call reused the requested path {requested}"
            detail.append(f"{name}: {[Path(s).name for s in seen]} "
                          f"(first {'suffixed' if norm(seen[0]) != requested else 'unsuffixed'})")
        return "; ".join(detail)

    def output_inside():
        need_jobs()
        detail = []
        for _, job in all_jobs:
            dataset, out = Path(job["dataset"]), Path(job["output_dir"])
            assert norm(out) == norm(dataset / "analysis" / job["job_id"]), (str(out), str(dataset))
            assert (out / "observation.txt").is_file(), f"{out} holds no observation.txt"
            built, torn = construction_for(dataset), teardown_for(dataset)
            assert built is not None and torn is not None, f"no single construction/teardown diagnostic for {dataset}"
            planned, accounted = built.get("frames_planned"), torn.get("frames_accounted")
            assert isinstance(planned, int) and planned > 0, f"{dataset}: frames_planned {planned!r}"
            frames = count_frames(dataset)
            assert frames == planned == accounted, f"{dataset}: NDTiff holds {frames}, planned {planned}, accounted {accounted}"
            detail.append(f"{dataset.name}:{frames}")
        return f"{len(all_jobs)} jobs inside their datasets; frames {detail}"

    def lifecycle():
        need_jobs()
        ids = {j["job_id"] for _, j in all_jobs}
        assert set(store_jobs) == ids, f"store jobs {sorted(store_jobs)} != history jobs {sorted(ids)}"
        sha = hashlib.sha256(FINAL.encode()).hexdigest()
        owners, deltas = set(), []
        for _, job in all_jobs:
            record = store_jobs[job["job_id"]]
            assert record.get("state") == "succeeded", (job["job_id"], record.get("state"), record.get("failure"))
            assert record.get("lifecycle") == {"acquisition": "completed", "writer": "finished"}, record.get("lifecycle")
            assert [(a.get("validity"), a.get("sha256")) for a in record.get("artifacts", [])] == [("final", sha)], \
                record.get("artifacts")
            path = Path(record["output_dir"]) / "observation.txt"
            assert path.read_text(encoding="utf-8") == FINAL, f"{path} is not the final artifact"
            # fixture_worker/runner.py writes FINAL only after echoing a writer-finished message.
            echoed = []
            for text in record.get("status", []):
                try:
                    echoed.append(json.loads(text))
                except ValueError:
                    pass
            assert any(m.get("type") == "writer" or (m.get("type") == "acquisition" and m.get("outcome") == "completed"
                                                     and m.get("writer") == "finished")
                       for m in echoed if isinstance(m, dict)), f"worker never echoed writer-finished: {record.get('status')}"
            owner = record.get("owner") or {}
            assert type(owner.get("pid")) is int and owner.get("nonce"), owner
            owners.add((owner["pid"], owner["nonce"]))
            torn = teardown_for(record["dataset"])
            if torn is not None:
                deltas.append(round(path.stat().st_mtime - _ts(torn["timestamp"]).timestamp(), 3))
        assert len(owners) == 1, f"jobs name {len(owners)} owners: {owners}"
        return (f"{len(ids)} jobs succeeded, completed/finished, final artifact after echo; one owner "
                f"{owners.pop()[0]}. REPORT ONLY, artifact mtime minus teardown diagnostic (s): {deltas}")

    def owner_is_serve():
        need_jobs()
        pid = gate.serve_pid()
        owners = {(store_jobs.get(j["job_id"]) or {}).get("owner", {}).get("pid") for _, j in all_jobs}
        assert owners == {pid}, f"job owners {owners}; serve listening on {SERVE_PORT} is pid {pid}"
        return f"pid {pid}"

    def per_dataset():
        multi = [c for c in analysis_calls if c["name"] == "run_multiposition_acquisition"
                 and of(c, "acquisition_construction")]
        if not multi:
            raise NotExercised("no approved analysis multiposition call constructed an acquisition")
        detail = []
        for c in multi:
            built = {norm(d["dataset_path"]) for d in of(c, "acquisition_construction")}
            jobs = history_jobs(c)
            positions = len(c["input"].get("positions") or c["input"].get("position_names") or [])
            assert len(jobs) == len(built) == positions, f"{len(jobs)} jobs, {len(built)} datasets, {positions} positions"
            assert {norm(j["dataset"]) for j in jobs} == built, "jobs do not point at the call's datasets"
            assert all(Path(j["dataset"]).is_dir() for j in jobs)
            detail.append(f"{c['id'][-6:]}: {len(jobs)} jobs / {len(built)} datasets")
        return "; ".join(detail)

    def cadence():
        groups = {}
        for c in calls:
            if c["name"] != "run_timelapse" or "error" in c["result"] or not of(c, "acquisition_construction"):
                continue
            key = json.dumps({k: v for k, v in c["input"].items() if k != "analysis"}, sort_keys=True)
            groups.setdefault(key, []).append(c)
        pairs = [g for g in groups.values()
                 if any("analysis" in c["input"] for c in g) and any("analysis" not in c["input"] for c in g)]
        if not pairs:
            raise NotExercised("no timelapse was run both with and without analysis")
        lines = []
        for group in pairs:
            for c in group:
                summary = c["result"].get("inter_frame_gap_summary")
                assert isinstance(summary, dict) and "count" in summary and "mean_s" in summary, \
                    f"call {c['id']} is missing its cadence data"
                built = of(c, "acquisition_construction")[0]["timestamp"]
                lag = (_ts(built) - _ts(c["result"]["started_at"])).total_seconds() if c["result"].get("started_at") else None
                fmt = lambda v: "None" if v is None else f"{v * 1000:.2f}ms"
                lines.append(f"{'WITH' if 'analysis' in c['input'] else 'WITHOUT'} analysis "
                             f"{Path(c['result'].get('dataset_path', '?')).name}: gaps n={summary['count']} "
                             f"min={fmt(summary.get('min_s'))} mean={fmt(summary.get('mean_s'))} "
                             f"median<={fmt(summary.get('median_le_s'))} p95<={fmt(summary.get('p95_le_s'))} "
                             f"max={fmt(summary.get('max_s'))}; duration_s={c['result'].get('duration_s')}; "
                             f"started_at->construction={fmt(lag)}")
        return "MEASUREMENT, not a criterion: " + " | ".join(lines)

    def panel():
        answer = sess.get("answers", {}).get("panel", "").strip()
        if not answer:
            raise NotExercised("the operator typed nothing for the panel line")
        assert "run_mda" in answer, f"operator saw no run_mda sentence: {answer!r}"
        return (OPERATOR_JUDGED, f"operator typed {answer!r}; the coordinator compares it with the shipped "
                                 "sentence 'run_mda uses Micro-Manager's own engine; MicroClaw cannot see its "
                                 "dataset creation, so it cannot attach package analysis.'")

    def export_runs():
        exports = [c for c in calls if c["name"] == "export_session_script" and c["result"].get("output_path")
                   and Path(c["result"]["output_path"]).is_file()]
        if not exports:
            raise NotExercised("no export_session_script result names an existing script")
        call = exports[-1]
        path = Path(call["result"]["output_path"])
        before = calls[:calls.index(call)]
        expected = [j for c in before for j in history_jobs(c)]
        if not expected:
            raise NotExercised("the exported session recorded no analysis job")
        source = path.read_text(encoding="utf-8")
        ast.parse(source)
        # Compiling is not working: run it first, then count (83e-2's lesson).
        run_dir = gate.out / "export-run"
        shutil.rmtree(run_dir, ignore_errors=True)
        run_dir.mkdir(parents=True)
        copy = run_dir / path.name
        copy.write_text(source, encoding="utf-8")
        env = dict(os.environ)
        if gate.fake:
            env.update(gate.fake.export_env())
        ran = subprocess.run([sys.executable, str(copy)], cwd=run_dir, stdin=subprocess.DEVNULL,
                             capture_output=True, text=True, timeout=900, env=env)
        (gate.out / "export-run.txt").write_text(f"exit {ran.returncode}\n--- stdout\n{ran.stdout}\n"
                                                 f"--- stderr\n{ran.stderr}\n", encoding="utf-8")
        problems = []
        if ran.returncode != 0:
            problems.append(f"exported script exited {ran.returncode}: {ran.stderr[-1500:]}")
        # Each acquisition the session constructed must be re-acquired once, and no other.
        live = sum(len(of(c, "acquisition_construction")) for c in before)
        standalone = sorted(str(p.parent.relative_to(run_dir)) for p in run_dir.rglob("NDTiff.index"))
        if len(standalone) != live:
            problems.append(f"standalone run wrote {len(standalone)} datasets {standalone}; "
                            f"the session constructed {live} acquisitions")
        comments = [line for line in source.splitlines() if line.startswith("# Package analysis not reproduced:")]
        if len(comments) != len(expected):
            problems.append(f"{len(comments)} 'Package analysis not reproduced' comments for {len(expected)} recorded jobs")
        missing = [j["job_id"] for j in expected if not any(j["job_id"] in line for line in comments)]
        if missing:
            problems.append(f"jobs without a comment: {missing}")
        if digest in code_without_comments(source):
            problems.append("the release digest appears outside a comment")
        for banned in ("NOT EXERCISED", "NOT EMITTED", "import microclaw", "from microclaw"):
            if banned in source:
                problems.append(f"script contains {banned!r}")
        sections = source.split("\n# RECORDED TOOL: ")[1:]
        order = [c for c in before]
        if len(sections) != len(order):
            problems.append(f"{len(sections)} RECORDED TOOL sections for {len(order)} recorded calls")
        else:
            for c, section in zip(order, sections):
                if c["result"].get("cancelled") is True:
                    if f"# SKIPPED: {c['name']}" not in section:
                        problems.append(f"declined call {c['id']} is not SKIPPED")
                    if "Acquisition(" in section or "acquire(" in section:
                        problems.append(f"declined call {c['id']} emits an acquisition")
        assert not problems, "; ".join(problems)
        return {"path": str(path), "ran": "exit 0", "comments": len(comments),
                "standalone_datasets": standalone}

    for name, fn in [("1 consent precedes construction; decline acquires nothing", consent),
                     ("2 collision-resolved dataset path", collision),
                     ("3 output inside the live dataset; dataset intact", output_inside),
                     ("4 lifecycle completed/finished, final artifact after writer-finished", lifecycle),
                     ("4b job owner is serve", owner_is_serve),
                     ("5 one job per dataset", per_dataset),
                     ("6 cadence with and without analysis (report)", cadence),
                     ("7 panel shows the run_mda sentence", panel),
                     ("8 exported script runs and discloses every job", export_runs)]:
        score(name, fn)
    if cleanup:
        score("9 cleanup removed store and TEST-ONLY roots", lambda: _clean(gate))
    return _report(gate, results)


def _report(gate, results):
    bad = judged = 0
    for name, (verdict, detail) in results.items():
        bad += verdict not in ("PASS", OPERATOR_JUDGED)
        judged += verdict == OPERATOR_JUDGED
        gate.say(f"{verdict}: {name} -- {detail}")
    gate.say(f"RESULT: {bad} failed or not exercised limbs / {len(results)}; {judged} operator-judged")
    write_json(gate.out / "verify.json", {k: list(v) for k, v in results.items()})
    gate.last_results = results
    return bad


def _clean(gate):
    state = gate.cleanup()
    assert not state["roots_present"] and not state["store_present"], state
    return state


# --- bridge-shaped fakes (no microclaw import: the exported script's child loads these) ---
class StrVector:
    """mmcorej_StrVector: size()/get(i) only; iterating it raises, as over pyjavaz."""

    def __init__(self, items):
        self._items = list(items)

    def size(self):
        return len(self._items)

    def get(self, index):
        return self._items[index]

    def __iter__(self):
        raise TypeError("'mmcorej_StrVector' object is not iterable")


class BridgeCore:
    def __init__(self):
        self.xy, self.z, self.exposure = (12.5, -3.0), 0.0, 10.0

    def __getattr__(self, name):
        raise AttributeError(f"bridge-shaped fake Core has no {name}")

    def get_camera_device(self): return "Camera"
    def get_xy_stage_device(self): return "XY"
    def get_focus_device(self): return "Z"
    def get_loaded_devices(self): return StrVector(["Camera", "XY", "Z", "Core"])
    def get_x_position(self, *_): return self.xy[0]
    def get_y_position(self, *_): return self.xy[1]
    def get_position(self, *_): return self.z
    def set_position(self, *args): self.z = float(args[-1])
    def set_xy_position(self, *args): self.xy = (float(args[-2]), float(args[-1]))
    def wait_for_device(self, *_): pass
    def device_busy(self, *_): return False
    def get_exposure(self): return self.exposure
    def set_exposure(self, *args): self.exposure = float(args[-1])
    def get_image_width(self): return 32
    def get_image_height(self): return 32
    def get_bytes_per_pixel(self): return 2
    def get_number_of_components(self): return 1
    def is_sequence_running(self): return False


class _Backend:
    """What pycromanager's dispatcher returns: storage exists at construction, frames
    and image_saved_fn arrive inside __exit__ (engine contract 6), min_start_time is
    honoured from the acquisition's start, and ndstorage picks the suffixed directory."""

    def __init__(self, directory=None, name=None, image_saved_fn=None, **_):
        from ndstorage import NDTiffDataset
        self._storage = NDTiffDataset(str(directory), name=name, writable=True, summary_metadata={})
        self._dataset_disk_location = self._storage.path
        self._exception = None
        self._saved = image_saved_fn
        self._events, self._start = [], None

    def __enter__(self):
        return self

    def acquire(self, events):
        self._start = self._start or time.perf_counter()
        self._events.extend(events if isinstance(events, list) else list(events))

    def __exit__(self, *exc):
        import numpy as np
        for event in self._events:
            for single in event if isinstance(event, list) else [event]:
                wait = (self._start or 0) + float(single.get("min_start_time", 0)) - time.perf_counter()
                if wait > 0:
                    time.sleep(wait)
                self._storage.put_image(dict(single["axes"]), np.zeros((32, 32), np.uint16), {})
                if self._saved is not None:
                    self._saved(dict(single["axes"]), None)
        self._storage.finish()
        return False


class FakeAcquisition:
    def __new__(cls, **kwargs):
        return _Backend(**kwargs)

    def __init__(self, **kwargs):
        raise AssertionError("the dispatcher's __init__ never runs")


FAKE_PYCROMANAGER = '''import importlib.util, os
__path__.append(os.environ["GATE83E3_REAL_PYCROMANAGER"])
from pycromanager.acquisition.acquisition_superclass import multi_d_acquisition_events
_spec = importlib.util.spec_from_file_location("gate83e3_fakes", os.environ["GATE83E3_FILE"])
_gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_gate)
Core, Acquisition = _gate.BridgeCore, _gate.FakeAcquisition
'''


# --- selftest ---------------------------------------------------------------------------------
class _Operator:
    """Stands in for the person, the launcher and serve. Tool calls go through the real
    execute_tool with serve's own writers; confirmations follow 83e-2's scripted audit."""
    timeout = 5

    def __init__(self, fake_pm):
        self.fake_pm, self.gate, self.sim = fake_pm, None, None
        self.pid_offset = 0

    def launch(self):
        return {"slot": "a", "nonce": "selftest", "line": "selftest"}

    def close(self):
        return None

    def uv(self):
        return None

    def base_python(self):
        return None

    def bin(self):
        return []

    def registry(self):
        return []

    def xy(self):
        core = BridgeCore()
        return core.xy

    def serve_pid(self):
        return os.getpid() + self.pid_offset

    def export_env(self):
        import pycromanager  # the real one, in this process
        return {"PYTHONPATH": str(self.fake_pm), "GATE83E3_FILE": str(Path(__file__).resolve()),
                "GATE83E3_REAL_PYCROMANAGER": str(Path(pycromanager.__file__).parent)}

    def panel(self):
        from microclaw import tools
        source = (Path(tools.__file__).parent / "transcript.js").read_text(encoding="utf-8")
        match = re.search(r'"(run_mda[^"]*)"', source)
        return match[1] if match else "NONE"

    def answer(self, key):
        if key == "panel":
            return self.panel()
        if key.startswith("t"):
            self.sim.call(self.gate.current_turn)
        return "DONE"


class SimServe:
    def __init__(self, root):
        from types import SimpleNamespace
        from microclaw import tools
        from microclaw.conversation import AcquisitionDiagnosticWriter, AuditLog
        from microclaw.safety import SafetyConstraints, SafetyGuard, StageConstraints
        self.tools = tools
        stem = "20260928_000000_000000_microclaw"
        self.history = AuditLog(Path(root) / f"{stem}_history.jsonl")
        self.writer = AcquisitionDiagnosticWriter(AuditLog(Path(root) / f"{stem}_acquisitions.jsonl"))
        self.scripted = gate83e2.ScriptedSession(root, [False, "session"])
        tools.CONFIRM_FN = self.scripted.confirm
        tools.SESSION_GRANTS.clear()
        tools.Acquisition = FakeAcquisition

        class Live:
            def is_live_mode_on(self): return False
            def set_live_mode_on(self, _): pass
        self.ctrl = SimpleNamespace(core=BridgeCore(), studio=SimpleNamespace(live=lambda: Live()),
                                    refresh_gui=lambda: None)
        bound = dict(x_min=-1000, x_max=1000, y_min=-1000, y_max=1000, z_min=-1000, z_max=1000)
        self.guard = SafetyGuard(SafetyConstraints(workspace_dir=str(root), stage=StageConstraints(**bound)))
        self.messages, self.stem = [], stem

    def append(self, message):
        self.history.append(message)
        self.messages.append(message)

    def call(self, turn):
        tool_id = f"toolu_selftest{turn['n']:02d}"
        self.append({"role": "assistant", "content": [{"type": "tool_use", "id": tool_id,
                                                       "name": turn["tool"], "input": turn["input"]}]})
        result = self.tools.execute_tool(
            turn["tool"], json.loads(json.dumps(turn["input"])), self.ctrl, self.guard,
            records=list(self.messages), acquisition_diagnostic_writer=self.writer,
            acquisition_session_id=self.stem, tool_call_id=tool_id)
        self.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_id,
                                                  "content": result}]})


def _rewrite_jsonl(path, edit):
    lines = [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]
    Path(path).write_text("".join(json.dumps(edit(row)) + "\n" for row in lines), encoding="utf-8")


def _edit_result(root, tool_id, edit):
    def change(message):
        for block in message.get("content") or []:
            if isinstance(block, dict) and block.get("tool_use_id") == tool_id:
                result = json.loads(block["content"])
                edit(result)
                block["content"] = json.dumps(result)
        return message
    for path in Path(root).glob("*_microclaw_history.jsonl"):
        _rewrite_jsonl(path, change)


def _job_records(gate):
    return [(p, r) for p, r in gate.store._analysis_records()]


def _mutations(gate):
    root = gate.root
    tid = lambda n: f"toolu_selftest{n:02d}"
    history = lambda: [(c, j) for c in recorded_calls(root, datetime.min.replace(tzinfo=timezone.utc))
                       for j in history_jobs(c)]

    def consent_late():
        def edit(row):
            if row.get("decision", "").startswith("approved"):
                row["timestamp"] = (_ts(row["timestamp"]).replace(year=2099)).isoformat()
            return row
        for path in root.glob("*_microclaw_confirmations.jsonl"):
            _rewrite_jsonl(path, edit)

    def decline_acquired():
        path = next(root.glob("*_microclaw_acquisitions.jsonl"))
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"timestamp": now().isoformat(), "type": "acquisition_construction",
                                "tool_call_id": tid(1), "dataset_path": str(root / "e3decline_1")}) + "\n")

    def collision_unresolved():
        def edit(result):
            first = result["analysis"]["jobs"][0]
            requested = str(Path(first["dataset"]).parent / "e3movie")
            result["dataset_path"] = first["dataset"] = requested
        _edit_result(root, tid(3), edit)

    def output_outside():
        shutil.rmtree(history()[0][1]["output_dir"])

    def frames_short():
        target = history()[0][1]["dataset"]

        def edit(row):
            if row.get("type") == "acquisition_construction" and norm(row.get("dataset_path")) == norm(target):
                row["frames_planned"] += 1
            return row
        _rewrite_jsonl(next(root.glob("*_microclaw_acquisitions.jsonl")), edit)

    def writer_unknown():
        path, record = _job_records(gate)[0]
        record["lifecycle"]["writer"] = "unknown"
        write_json(path, record)

    def partial_artifact():
        (Path(history()[0][1]["output_dir"]) / "observation.txt").write_text("observing dataset\n", encoding="utf-8")

    def other_owner():
        gate.fake.pid_offset = 1

    def job_dropped():
        _edit_result(root, tid(5), lambda r: r["analysis"]["jobs"].pop())

    def cadence_missing():
        _edit_result(root, tid(4), lambda r: r.pop("inter_frame_gap_summary"))

    def panel_missing():
        session = read_json(gate.out / "session.json")
        session["answers"]["panel"] = "NONE"
        write_json(gate.out / "session.json", session)

    def _script():
        call = [c for c in recorded_calls(root, datetime.min.replace(tzinfo=timezone.utc))
                if c["name"] == "export_session_script"][-1]
        return Path(call["result"]["output_path"])

    def export_raises():
        with _script().open("a", encoding="utf-8") as f:
            f.write("raise SystemExit(3)\n")

    def export_digest_leak():
        with _script().open("a", encoding="utf-8") as f:
            f.write(f"DIGEST = {gate.need('prepare')['digest']!r}\n")

    def export_comment_dropped():
        path = _script()
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        index = next(i for i, l in enumerate(lines) if l.startswith("# Package analysis not reproduced:"))
        path.write_text("".join(lines[:index] + lines[index + 1:]), encoding="utf-8")

    def export_declined_emitted():
        path = _script()
        source = path.read_text(encoding="utf-8")
        marker = "# SKIPPED: run_timelapse"
        index = source.index(marker)
        end = source.index("\n", index) + 1
        path.write_text(source[:end] + '_emitted = (lambda: Acquisition(directory=".", name="x"))\n'
                        + source[end:], encoding="utf-8")

    def export_extra_acquisition():
        with _script().open("a", encoding="utf-8") as f:
            f.write("with Acquisition(directory=str(_HERE), name='extra') as acq:\n"
                    "    acq.acquire(multi_d_acquisition_events(num_time_points=1))\n")

    def session_failed():
        write_json(gate.out / "session.json", {"phase_error": "selftest"})

    def cleanup_leaves_roots():
        gate.cleanup = lambda: {"roots_present": True, "store_present": True}

    L = ["1 consent precedes construction; decline acquires nothing", "2 collision-resolved dataset path",
         "3 output inside the live dataset; dataset intact",
         "4 lifecycle completed/finished, final artifact after writer-finished", "4b job owner is serve",
         "5 one job per dataset", "6 cadence with and without analysis (report)",
         "7 panel shows the run_mda sentence", "8 exported script runs and discloses every job",
         "9 cleanup removed store and TEST-ONLY roots"]
    return [(consent_late, L[0]), (decline_acquired, L[0]), (collision_unresolved, L[1]),
            (output_outside, L[2]), (frames_short, L[2]), (writer_unknown, L[3]), (partial_artifact, L[3]),
            (other_owner, L[4]), (job_dropped, L[5]), (cadence_missing, L[6]), (panel_missing, L[7]),
            (export_raises, L[8]), (export_digest_leak, L[8]), (export_comment_dropped, L[8]),
            (export_declined_emitted, L[8]), (export_extra_acquisition, L[8]), (session_failed, "session evidence"),
            (cleanup_leaves_roots, L[9])]


def selftest(baseline_only=False):
    tmp = Path(tempfile.mkdtemp())
    base, backup = tmp / "w", tmp / "w.bak"
    base.mkdir()
    os.environ["XDG_DATA_HOME"] = os.environ["LOCALAPPDATA"] = str(base)
    for name in [m for m in sys.modules if m.startswith("microclaw")]:
        del sys.modules[name]
    import microclaw
    from microclaw import paths
    from microclaw.completed_dataset import close_analysis_supervisor
    print(f"SELFTEST product under test: {Path(microclaw.__file__).parent}", flush=True)
    fake_pm = tmp / "fake-pycromanager"
    (fake_pm / "pycromanager").mkdir(parents=True)
    (fake_pm / "pycromanager" / "__init__.py").write_text(FAKE_PYCROMANAGER, encoding="utf-8")
    root = paths.user_data_dir()
    root.mkdir(parents=True)
    operator = _Operator(fake_pm)
    gate = Gate(root, base / "evidence", fake=operator)
    operator.gate = gate
    gate.prepare()
    operator.sim = SimServe(root)
    gate.session()
    deadline = time.time() + 60
    while any(r.get("state") in gate.store.ANALYSIS_NONTERMINAL for _, r in gate.store._analysis_records()):
        assert time.time() < deadline, "selftest jobs did not finish"
        time.sleep(0.1)
    close_analysis_supervisor()
    operator.sim.writer.close()
    shutil.copytree(base, backup)

    def arm():
        shutil.rmtree(base)
        shutil.copytree(backup, base)
        fresh = Gate(root, base / "evidence", fake=operator)
        operator.gate, operator.pid_offset = fresh, 0
        return fresh

    failures = 0
    clean = arm()
    bad = verify(clean, cleanup=True)
    verdicts = {k: v[0] for k, v in clean.last_results.items()}
    ok = bad == 0 and not (root / "skill-packages").exists()
    failures += not ok
    print(f"SELFTEST {'ok' if ok else 'WRONG'}: unmutated bad_limbs={bad} {json.dumps(verdicts)}", flush=True)
    if baseline_only:
        shutil.rmtree(tmp, ignore_errors=True)
        return failures
    for index in range(len(_mutations(clean))):
        gate = arm()
        mutate, target = _mutations(gate)[index]
        mutate()
        verify(gate, cleanup=True)
        verdict = gate.last_results.get(target, ("MISSING",))[0]
        others = [k for k, v in gate.last_results.items() if v[0] not in ("PASS", OPERATOR_JUDGED) and k != target]
        ok = verdict in ("FAIL", "NOT EXERCISED")
        failures += not ok
        print(f"SELFTEST {'ok' if ok else 'WRONG'}: mutation={mutate.__name__} target={target!r} -> {verdict}"
              f"; other non-pass limbs: {others}", flush=True)
    shutil.rmtree(tmp, ignore_errors=True)
    return failures


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=PHASES + ("selftest",))
    parser.add_argument("--out")
    parser.add_argument("--baseline-only", action="store_true")
    args = parser.parse_args()
    if args.phase == "selftest":
        sys.exit(1 if selftest(args.baseline_only) else 0)
    from microclaw import paths
    gate = Gate(paths.user_data_dir(), args.out)
    try:
        if args.phase == "verify":
            sys.exit(1 if verify(gate) else 0)
        getattr(gate, args.phase)()
        gate.say(f"RECORDED: {args.phase}")
    except NotExercised as exc:
        gate.say(f"NOT EXERCISED: {args.phase}: {exc}")
        gate.save(args.phase, {"phase_error": str(exc)})
        sys.exit(2)
    except Exception:
        gate.say(f"FAILED: {args.phase}:\n{traceback.format_exc()}")
        gate.save(args.phase, {"phase_error": traceback.format_exc()})
        sys.exit(1)


if __name__ == "__main__":
    main()
