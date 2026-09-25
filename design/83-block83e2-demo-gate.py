"""83e-2: the analysis confirmation and its session grant, in Firefox on the demo machine.

Reuses 83d's gate plumbing (launch detection by nonce, operator prompts, cleanup).
Scores from serve's own confirmation audit, the store's job records, the worker's
artifacts and the exported script; the operator's answers are human judgements
about what the banner showed, recorded verbatim. --selftest drives the real tool,
real supervisor and real exporter with a scripted operator, and runs each
sabotage to prove every limb can fail.
"""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import traceback

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("gate83d", HERE / "83-block83d-demo-gate.py")
gate83d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate83d)
FIXTURES, NotExercised = gate83d.FIXTURES, gate83d.NotExercised
read_json, write_json, now = gate83d.read_json, gate83d.write_json, gate83d.now

PHASES = ("prepare", "session", "verify")
PACKAGE = "fixture-lab/executable-fixture"
ADAPTER = PACKAGE + ":observe_dataset"
POINTER = "83e2-gate-evidence.txt"


def prompt_text(digest, dataset, output):
    return (f"Run the analysis {ADAPTER} with release_digest {digest} on the dataset at "
            f"{dataset}, with parameters {{}} and output directory {output}. "
            "Do not change any of these values.")


class Gate(gate83d.Gate):
    def cleanup(self):
        result = super().cleanup()
        (self.root / POINTER).unlink(missing_ok=True)
        return result

    def prepare(self):
        if self.store_root.exists():
            raise NotExercised(f"{self.store_root} already exists; this gate creates and removes "
                               "its own store. Send the evidence folder instead of deleting it.")
        self.close_microclaw("Prepare installs releases in this process.")
        builder = gate83d.load_builder()
        releases = self.out / "releases"
        releases.mkdir(exist_ok=True)
        write_json(self.store_root / "trust" / "roots.json", read_json(FIXTURES / "trust" / "roots-TEST-ONLY.json"))
        policy = self.store.store_trust_policy(read_json(FIXTURES / "trust" / "policy-TEST-ONLY.json"))
        digests = {}
        for version in ("1.0.0", "1.1.0"):
            artifact = releases / f"executable-{version}.zip"
            intake = builder.build_release(FIXTURES / "executable", artifact, version=version)
            self.store.install(intake, artifact, policy=policy, now=now(), uv_executable=self.uv(),
                               retained_digests=frozenset(), find_links=(FIXTURES / "wheels").resolve(),
                               base_python=self.base_python())
            digests[version] = intake["artifact_digest"]
            self.say(f"installed executable-fixture {version}: {intake['artifact_digest']}")
        dataset = self.out / "dataset"
        dataset.mkdir(exist_ok=True)
        (dataset / "README.txt").write_text("83e-2 gate: the fixture worker does not read pixels.\n",
                                            encoding="utf-8")
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        self.save("prepare", {"digests": digests, "dataset": str(dataset), "stamp": stamp,
                              "prepared_at": now().isoformat()})

    def session(self):
        prep = self.need("prepare")
        d1, d2 = prep["digests"]["1.0.0"], prep["digests"]["1.1.0"]
        out = lambda n: f"83e2-{prep['stamp']}-{n}"
        answers, started = {}, now().isoformat()
        launch = self.launch_desktop("Session phase.")

        def turn(n, digest, instruction):
            self.say(f"TURN {n}: type this into Microclaw's message box exactly, then press Enter:")
            self.say("    " + prompt_text(digest, prep["dataset"], out(n)))
            self.say("    " + instruction)

        turn(1, d1, "A confirmation banner should appear. Read it, then click 'Approve for this session'.")
        answers["t1_banner"] = self.ask(
            "Did a banner appear showing the digest, the dataset, the output directory and the line "
            "'Publisher code runs with your user permissions; not sandboxed.'? Type YES or NO.", "t1_banner")
        answers["t1_chip"] = self.ask(
            "Type the text of the new chip at the top right of the page, without the Revoke button.", "t1_chip")
        turn(2, d1, "Expect NO banner this time. Let the turn finish.")
        answers["t2_banner"] = self.ask("Did a confirmation banner appear on turn 2? Type YES or NO.", "t2_banner")
        turn(3, d2, "This is a different release. A banner should appear: click 'Decline'.")
        answers["t3_banner"] = self.ask("Did a banner appear on turn 3, and did you click Decline? "
                                        "Type YES or NO.", "t3_banner")
        self.say("Now click 'Revoke' on the analysis chip at the top right. The chip should disappear.")
        turn(4, d1, "A banner should appear again: click 'Approve' (the plain one, NOT for the session).")
        answers["t4_banner"] = self.ask("Did the chip disappear, and did a banner appear on turn 4? "
                                        "Type YES or NO.", "t4_banner")
        self.say("TURN 5: type this exactly: Check the status of every analysis job you started in this session.")
        answers["t5_status"] = self.ask("What state did the agent report for the jobs? Type its words.", "t5_status")
        export = out("export") + ".py"
        self.say(f"TURN 6: type this exactly: Export this session as a standalone script named {export}.")
        answers["t6_done"] = self.ask("Did the agent say it wrote the script? Type YES or NO.", "t6_done")
        self.save("session", {"started_at": started, "launch": launch, "answers": answers,
                              "export_name": export, "outputs": [out(n) for n in (1, 2, 3, 4)]})


def audit_rows(root, since):
    rows = []
    for path in sorted(Path(root).glob("*_microclaw_confirmations.jsonl")):
        if datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) < since:
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                if row.get("kind") == "analysis":
                    rows.append(dict(row, file=path.name))
    return sorted(rows, key=lambda r: r["timestamp"])


def strings(value):
    if isinstance(value, str):
        yield value
        try:
            inner = json.loads(value)
        except ValueError:
            return
        if not isinstance(inner, str):
            yield from strings(inner)
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def exported_script(root, since, name):
    for path in sorted(Path(root).glob("*_microclaw_history.jsonl")):
        if datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) < since:
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            for text in strings(json.loads(line)):
                if text.endswith(name) and Path(text).is_file():
                    return Path(text)
    return None


def verify(gate, *, cleanup=True):
    results = {}

    def score(name, fn):
        try:
            detail = fn()
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
        # Still clean up: a failed phase must not leave the TEST-ONLY roots behind.
        results["session evidence"] = ("NOT EXERCISED", str(exc))
        if cleanup:
            score("cleanup removed store and TEST-ONLY roots", lambda: _clean(gate))
        return _report(gate, results)
    d1, d2 = prep["digests"]["1.0.0"], prep["digests"]["1.1.0"]
    s1, s2 = f"{PACKAGE}@{d1}", f"{PACKAGE}@{d2}"
    since = datetime.fromisoformat(sess["started_at"])
    rows = audit_rows(gate.root, since)
    jobs = [r for _, r in gate.store._analysis_records()]
    yes = lambda key: sess["answers"].get(key, "").strip().upper() == "YES"
    decisions = lambda subject: [r["decision"] for r in rows if r.get("subject") == subject]

    def need_rows():
        if not rows:
            raise NotExercised(f"no analysis rows in any *_microclaw_confirmations.jsonl under {gate.root} "
                               "written after the session started (was history saving off?)")

    def first_call_granted():
        need_rows()
        seq = decisions(s1)
        assert seq[:2] and seq[0].startswith("granted:") and seq[1].startswith("approved:session:"), seq
        assert yes("t1_banner"), "operator did not see the full banner on turn 1"
        chip = sess["answers"].get("t1_chip", "")
        assert "analysis" in chip and d1[:12] in chip, f"chip text {chip!r} does not name analysis and the digest"
        return seq[:2]

    def second_call_auto():
        need_rows()
        seq = decisions(s1)
        assert len(seq) > 2 and seq[2].startswith("auto-approved:"), seq
        assert not yes("t2_banner"), "operator saw a banner on turn 2 under the grant"
        return seq[2]

    def other_digest_prompts():
        need_rows()
        seq = decisions(s2)
        assert seq == ["declined"], f"release 1.1.0 decisions {seq}; expected one human decline"
        assert yes("t3_banner"), "operator did not see a banner for the other release"
        assert not any(j.get("digest") == d2 for j in jobs), "a job was submitted for the declined release"
        return seq

    def revoke_restores_prompt():
        need_rows()
        seq = decisions(s1)
        revoked = [i for i, d in enumerate(seq) if d.startswith("revoked:")]
        assert revoked, f"no revocation row for {s1}: {seq}"
        after = seq[revoked[-1] + 1:]
        assert after == ["approved"], f"after revoke {after}; expected one plain human approval"
        assert yes("t4_banner"), "operator did not see the chip go and a banner return"
        return after

    def jobs_ran_and_recorded():
        mine = [j for j in jobs if j.get("digest") == d1]
        assert len(mine) == 3, f"{len(mine)} job records for release 1.0.0; expected 3"
        for job in mine:
            assert job["state"] == "succeeded", (job["job_id"], job["state"], job.get("failure"))
            assert type(job.get("owner", {}).get("pid")) is int, job.get("owner")
            text = (Path(job["output_dir"]) / "observation.txt").read_text(encoding="utf-8")
            assert text == "dataset writer finished\n", text
            assert [a["validity"] for a in job["artifacts"]] == ["final"], job["artifacts"]
        assert not any(j["state"] in gate.store.ANALYSIS_NONTERMINAL for j in jobs), "a job never finished"
        return sorted(Path(j["output_dir"]).name for j in mine)

    def export_discloses():
        path = exported_script(gate.root, since, sess["export_name"])
        if path is None:
            raise NotExercised(f"no history line names an existing {sess['export_name']}")
        source = path.read_text(encoding="utf-8")
        ast.parse(source)
        assert source.count("# Analysis was not reproduced") == 3, source.count("# Analysis was not reproduced")
        assert source.count("declined at confirmation") == 1, "the declined call is not disclosed as a decline"
        assert d1 in source and "NOT EMITTED" not in source, "digest missing or a step not emitted"
        for name in sess.get("outputs", []):
            assert name in source or name.endswith("-3"), f"output directory {name} is not disclosed"
        # Compiling is not working: run it. It connects to Micro-Manager's Core and
        # does nothing else, because every recorded step is a comment.
        env = dict(os.environ, **({"PYTHONPATH": str(gate.fake_pycromanager)} if gate.fake else {}))
        ran = subprocess.run([sys.executable, str(path)], cwd=path.parent, stdin=subprocess.DEVNULL,
                             capture_output=True, text=True, timeout=120, env=env)
        assert ran.returncode == 0, f"exported script exited {ran.returncode}: {ran.stderr[-1500:]}"
        return {"path": str(path), "ran": "exit 0"}

    for name, fn in [("first call prompts and grants", first_call_granted),
                     ("second call is auto-approved", second_call_auto),
                     ("another digest prompts under the grant", other_digest_prompts),
                     ("revoking restores the prompt", revoke_restores_prompt),
                     ("jobs ran and are recorded", jobs_ran_and_recorded),
                     ("export discloses every call", export_discloses)]:
        score(name, fn)
    if cleanup:
        score("cleanup removed store and TEST-ONLY roots", lambda: _clean(gate))
    return _report(gate, results)


def _report(gate, results):
    bad = 0
    for name, (verdict, detail) in results.items():
        bad += verdict != "PASS"
        gate.say(f"{verdict}: {name} -- {detail}")
    gate.say(f"RESULT: {bad} failed or not exercised limbs / {len(results)}")
    write_json(gate.out / "verify.json", {k: list(v) for k, v in results.items()})
    return bad


def _clean(gate):
    state = gate.cleanup()
    assert not state["roots_present"] and not state["store_present"], state
    return state


# --- selftest: real tool, supervisor and exporter; scripted operator and audit ----------------
class ScriptedSession:
    """Writes the audit rows webserve.Session.confirm writes, with its decision strings."""

    def __init__(self, root, plan):
        from microclaw import tools
        self.tools, self.plan = tools, list(plan)
        self.path = Path(root) / "20260925_000000_000000_microclaw_confirmations.jsonl"
        self.path.touch()

    def row(self, subject, decision, summary="x"):
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(dict(timestamp=now().isoformat(), kind="analysis", subject=subject,
                                    decision=decision, summary=summary)) + "\n")
        time.sleep(0.002)

    def confirm(self, summary, kind="action", subject=None, **_):
        grants = self.tools.SESSION_GRANTS
        grant = grants.granted(kind, subject)
        if grant is not None:
            self.row(subject, f"auto-approved:{grant['id']}", summary)
            return True
        answer = self.plan.pop(0)
        if answer == "session":
            grant = grants.grant(kind, subject, summary, identity="selftest")
            self.row(subject, f"granted:{grant['id']}")
            self.row(subject, f"approved:session:{grant['id']}", summary)
            return True
        self.row(subject, "approved" if answer else "declined", summary)
        return bool(answer)

    def revoke(self):
        for grant in self.tools.SESSION_GRANTS.active():
            self.tools.SESSION_GRANTS.revoke(grant["id"])
            self.row(grant["subject"], f"revoked:{grant['id']}")


def selftest():
    """Run the whole program against the real product on this machine, then sabotage it."""
    failures = 0
    fake_pm = Path(tempfile.mkdtemp()) / "fake-pycromanager"
    (fake_pm / "pycromanager").mkdir(parents=True)
    # The three names an exported script imports; Core() is the only one it calls here.
    (fake_pm / "pycromanager" / "__init__.py").write_text(
        "class Core:\n    pass\nclass Acquisition:\n    pass\n"
        "def multi_d_acquisition_events(*a, **k):\n    return []\n", encoding="utf-8")
    for sabotage in (None, "no_grant", "grant_leaks_digest", "no_revoke", "no_export",
                     "silent_export", "broken_export", "session_failed"):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["XDG_DATA_HOME"] = os.environ["LOCALAPPDATA"] = tmp
            for name in [m for m in sys.modules if m.startswith("microclaw")]:
                del sys.modules[name]
            from microclaw import paths, tools
            root = paths.user_data_dir()
            root.mkdir(parents=True)
            gate = Gate(root, Path(tmp) / "evidence", fake=_Operator())
            gate.fake_pycromanager = fake_pm
            if sabotage == "silent_export":
                tools.run_analysis_on_saved_dataset._microclaw_emitter = lambda params: "# (nothing)"
            gate.prepare()
            prep = gate.need("prepare")
            d1, d2 = prep["digests"]["1.0.0"], prep["digests"]["1.1.0"]
            plan = [True, True, False, True] if sabotage == "no_grant" else ["session", False, True]
            scripted = ScriptedSession(root, plan)
            tools.CONFIRM_FN = scripted.confirm
            tools.SESSION_GRANTS.clear()
            guard = _Guard(root)
            started, records = now().isoformat(), []
            for n, digest in [(1, d1), (2, d1), (3, d2), (4, d1)]:
                if n == 3 and sabotage == "grant_leaks_digest":
                    tools.SESSION_GRANTS._granted[("analysis", f"{PACKAGE}@{d2}")] = \
                        dict(tools.SESSION_GRANTS.active()[0], subject=f"{PACKAGE}@{d2}")
                if n == 4 and sabotage != "no_revoke":
                    scripted.revoke()
                params = dict(dataset_path=prep["dataset"], adapter=ADAPTER, release_digest=digest,
                              parameters={}, output_dir=str(root / f"83e2-{prep['stamp']}-{n}"))
                result = tools.run_analysis_on_saved_dataset(None, guard, **params)
                records += [{"role": "assistant", "content": [{"type": "tool_use", "id": f"t{n}",
                             "name": "run_analysis_on_saved_dataset", "input": params}]},
                            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"t{n}",
                             "content": json.dumps(result)}]}]
            deadline = time.time() + 60
            while any(j["state"] in gate.store.ANALYSIS_NONTERMINAL for _, j in gate.store._analysis_records()):
                assert time.time() < deadline, "selftest jobs did not finish"
                time.sleep(0.1)
            export = f"83e2-{prep['stamp']}-export.py"
            if sabotage != "no_export":
                exported = tools.export_session_script(None, guard, str(root / export), records)
                if sabotage == "broken_export":
                    with (root / export).open("a", encoding="utf-8") as f:
                        f.write("raise SystemExit(3)\n")
                (root / "20260925_000000_000000_microclaw_history.jsonl").write_text(
                    json.dumps({"role": "user", "content": [{"type": "tool_result",
                                "content": json.dumps(exported)}]}) + "\n", encoding="utf-8")
            answers = dict(t1_banner="YES", t1_chip=f"analysis: {PACKAGE}@{d1} approved",
                           t2_banner="YES" if sabotage == "no_grant" else "NO",
                           t3_banner="YES", t4_banner="YES", t5_status="succeeded", t6_done="YES")
            gate.save("session", {"started_at": started, "answers": answers, "export_name": export,
                                  "outputs": [f"83e2-{prep['stamp']}-{n}" for n in (1, 2, 3, 4)]}
                      if sabotage != "session_failed" else {"phase_error": "selftest"})
            from microclaw.completed_dataset import close_analysis_supervisor
            close_analysis_supervisor()
            bad = verify(gate, cleanup=True)
            ok = (bad == 0) if sabotage is None else (bad > 0)
            ok = ok and not (root / "skill-packages").exists()  # every path cleans up
            failures += not ok
            print(f"SELFTEST {'ok' if ok else 'WRONG'}: sabotage={sabotage} bad_limbs={bad}", flush=True)
    return failures


class _Operator:
    timeout = 5

    def answer(self, key):
        return "DONE"

    def close(self):
        return None

    def launch(self):
        return None

    def uv(self):
        return None

    def base_python(self):
        return None

    def bin(self):
        return []

    def registry(self):
        return []


class _Guard:
    def __init__(self, root):
        self.root = Path(root)

    def resolve_readable_path(self, path):
        return os.path.abspath(path)

    def resolve_in_workspace(self, path):
        return os.path.abspath(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=PHASES + ("selftest",))
    parser.add_argument("--out")
    args = parser.parse_args()
    if args.phase == "selftest":
        sys.exit(1 if selftest() else 0)
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
