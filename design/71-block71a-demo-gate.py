"""71a: exercise a long-lived server in the active, non-editable environment.

The gate hosts the production ASGI app for its lifetime over loopback HTTP,
instruments uv dispatch to retain evidence, and adds one gate-only
HTTP probe for the real ilastik adapter. It never restarts the interpreter.
No microscope writes or model calls are needed: the turn limb holds a real
/api/prompt turn at a deterministic agent boundary. --selftest substitutes only
package installation/readiness; its results are NOT demo-machine evidence.
"""
import argparse
import builtins
from contextlib import ExitStack, contextmanager
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

from microclaw import credentials, tools, updates, webserve
from microclaw.controller import MicroscopeController
from microclaw.authorization import validate_live_rig
from microclaw.config import load_safety_config_or_exit


@contextmanager
def server_client(app, selftest):
    if selftest:
        from fastapi.testclient import TestClient
        with TestClient(app) as client:
            yield client
        return
    # Only the selftest needs httpx. Managed [serve] installs have uvicorn, and
    # the real gate exercises actual loopback HTTP using the standard library.
    import urllib.request
    import urllib.error
    import uvicorn
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    class Client:
        def request(self, method, route, json=None):
            body = globals()["json"].dumps(json).encode() if json is not None else None
            request = urllib.request.Request(f"http://127.0.0.1:{port}" + route, data=body,
                                             headers={"Content-Type": "application/json"}, method=method)
            try:
                response = urllib.request.urlopen(request, timeout=60)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                text = response.read().decode("utf-8")
                return SimpleNamespace(status_code=response.status, text=text,
                                       json=lambda: globals()["json"].loads(text))
        def get(self, route):
            return self.request("GET", route)
        def post(self, route, json=None):
            return self.request("POST", route, json)
    try:
        deadline = time.monotonic() + 20
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.01)
        if not server.started:
            raise RuntimeError("gate server did not start")
        yield Client()
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()


class NotExercised(RuntimeError):
    pass


def check_refusal(response, before, invocations):
    assert response.status_code == 400, f"Impossible extra accepted: HTTP {response.status_code}"
    assert len(invocations) == before, "Refused extra invoked uv"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    log = (args.out / "gate.log").open("w", encoding="utf-8", buffering=1)
    failures = []
    def say(text):
        print(text, flush=True)
        log.write(text + "\n")
    def limb(name, fn):
        try:
            detail = fn()
            say(f"PASS {name}: {detail or 'exercised'}")
        except NotExercised as exc:
            failures.append(name)
            say(f"NOT EXERCISED {name}: {exc}")
        except Exception as exc:
            failures.append(name)
            say(f"FAIL {name}: {type(exc).__name__}: {exc}")

    say(f"71a gate pid={os.getpid()} interpreter={sys.executable} selftest={args.selftest}")
    try:
        from microclaw import extensions
    except ImportError as exc:
        for name in ("managed target", "initial panel", "install", "no restart adapter", "record",
                     "repeat", "refusal", "refusal control", "in-flight", "constraints", "progress"):
            limb(name, lambda: (_ for _ in ()).throw(NotExercised(f"extension implementation absent: {exc}")))
        log.close()
        return 1

    invocations, observations = [], []
    baseline_pid = os.getpid()
    install_complete = False
    managed_target = args.selftest
    initially_absent = False
    entered, release = threading.Event(), threading.Event()
    real_popen = subprocess.Popen
    uv = None
    try:
        uv = updates.locate_uv()
    except updates.UpdateError as exc:
        say(f"uv discovery: {exc}")

    def invoke(argv, **kw):
        if argv and argv[0] == uv:
            entry = {"argv": argv, "at": time.time()}
            if "--constraint" in argv:
                entry["constraints"] = Path(argv[argv.index("--constraint") + 1]).read_text(encoding="utf-8")
            invocations.append(entry)
            (args.out / "uv-invocations.json").write_text(json.dumps(invocations, indent=2), encoding="utf-8")
            if args.selftest:
                argv = [sys.executable, str(args.out / "fake-uv.py"), *argv[1:]]
        return real_popen(argv, **kw)

    def turn(*a, **kw):
        entered.set()
        if not release.wait(30):
            raise RuntimeError("gate did not release its turn")
        yield {"type": "text", "text": "Gate turn completed without hardware writes."}

    # A minimal session is sufficient: this feature uses no bridge calls. Use
    # Session's actual initialization and turn path, with no saved conversation.
    session = webserve.Session.__new__(webserve.Session)
    session.ctrl = SimpleNamespace()
    session.guard = None
    session.mode = webserve.SessionMode.NORMAL
    session.tool_schemas = []
    session.tool_registry = {}
    session.safety_config_path = None

    with ExitStack() as stack:
        stack.enter_context(patch.object(credentials, "load_api_key", return_value=(None, None)))
        session._initialize(SimpleNamespace(model=None, save_history=False, host="127.0.0.1"))
        stack.enter_context(patch.object(credentials, "load_api_key", return_value=("gate-only-no-network", "gate")))
        stack.enter_context(patch.object(webserve, "run_agent_iter", turn))
        if args.selftest:
            uv = "gate-fake-uv"
            stack.enter_context(patch.object(updates, "locate_uv", return_value=uv))
            stack.enter_context(patch.object(extensions, "user_data_dir", return_value=args.out / "records"))
            stack.enter_context(patch.object(extensions, "ready", side_effect=lambda name: install_complete))
            # Captured uv vocabulary, stderr with a real running-process interval.
            (args.out / "fake-uv.py").write_text(
                "import sys,time\nassert sys.argv[1:3]==['pip','install']\n"
                "if '--dry-run' in sys.argv:\n print('Would install 1 package\\n + h5py==3.16.0',file=sys.stderr);sys.exit(0)\n"
                "for line in ['Resolved 2 packages in 214ms','Downloading h5py (2.9MiB)',"
                "' Downloaded h5py','Prepared 1 package in 370ms','Installed 1 package in 11ms',' + h5py==3.16.0']:\n"
                " print(line,file=sys.stderr,flush=True);time.sleep(.12)\n", encoding="utf-8")
        stack.enter_context(patch.object(subprocess, "Popen", side_effect=invoke))
        app = webserve.build_app(session)

        @app.get("/gate/ilastik-adapter")
        async def adapter_probe():
            from microclaw.ilastik_adapter import IlastikCompletedDatasetAdapter
            # Real HDF5 bytes and the real adapter, in this same server process.
            import h5py
            project = args.out / "gate.ilp"
            with h5py.File(project, "w") as file:
                file["PixelClassification/LabelNames"] = [b"background", b"numerator", b"denominator"]
                file["ilastikVersion"] = b"1.4"
            seen = []
            original = builtins.__import__
            def observe(name, *a, **kw):
                if name == "h5py":
                    seen.append(name)
                return original(name, *a, **kw)
            with patch.object(builtins, "__import__", side_effect=observe):
                adapter = IlastikCompletedDatasetAdapter(project, "background", "numerator", "denominator")
            return {"pid": os.getpid(), "imports": seen, "labels": adapter.label_names}

        with server_client(app, args.selftest) as client:
            def catalog():
                response = client.get("/api/extensions")
                assert response.status_code == 200, response.text
                return response.json()

            def managed():
                nonlocal managed_target
                if args.selftest:
                    raise NotExercised("selftest is not the non-editable managed slot")
                root = updates.user_data_dir()
                active = (root / "active-slot.txt").read_text().strip()
                assert active in {"a", "b"}
                expected = root / f"env-{active}" / "Scripts" / "python.exe"
                assert Path(sys.executable).resolve() == expected.resolve(), "wrong slot interpreter"
                direct = importlib.metadata.distribution("microclaw").read_text("direct_url.json")
                assert not direct or not json.loads(direct).get("dir_info", {}).get("editable"), "editable slot"
                managed_target = True
                return str(expected)
            limb("managed target", managed)

            def initial():
                nonlocal initially_absent
                state = catalog()
                row = next(r for r in state["extensions"] if r["name"] == "ilastik")
                if row["ready"] or row["recorded"]:
                    raise NotExercised("ilastik is already installed or recorded; use a disposable clean managed install")
                assert not row.get("error"), row["error"]
                initially_absent = True
                return "ilastik not installed"
            limb("initial panel", initial)

            def install():
                nonlocal install_complete
                if not managed_target or not initially_absent:
                    raise NotExercised("install requires an absent extension in the verified managed target")
                response = client.post("/api/extensions/install", json={"name": "ilastik"})
                assert response.status_code == 202, response.text
                deadline = time.monotonic() + 900
                while time.monotonic() < deadline:
                    state = catalog()
                    observations.append({"at": time.time(), "state": state})
                    if not state["job"].get("running"):
                        assert not state["job"].get("error"), state["job"].get("error")
                        result = state["job"].get("result", {})
                        assert result.get("added"), f"No packages added: {result}"
                        install_complete = True
                        return result
                    time.sleep(.025)
                raise RuntimeError("gate observation deadline exceeded; inspect the server and uv before closing")
            limb("install", install)
            (args.out / "observations.json").write_text(json.dumps(observations, indent=2), encoding="utf-8")

            def adapter():
                if not initially_absent or not install_complete:
                    raise NotExercised("no absent-to-installed transition in this server")
                response = client.get("/gate/ilastik-adapter")
                assert response.status_code == 200, response.text
                value = response.json()
                assert value["pid"] == baseline_pid and value["imports"] == ["h5py"], value
                assert value["labels"] == ["background", "numerator", "denominator"]
                return value
            limb("no restart adapter", adapter)

            def recorded():
                record = json.loads((extensions.user_data_dir() / "extensions.json").read_text(encoding="utf-8"))
                assert record["installed"]["ilastik"]["requirements"] == extensions.requirements_for("ilastik")
                return record["installed"]["ilastik"]
            limb("record", recorded)

            def repeat():
                before = len(invocations)
                response = client.post("/api/extensions/install", json={"name": "ilastik"})
                assert response.status_code == 202, response.text
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    state = catalog()
                    if not state["job"].get("running"):
                        assert "already installed" in state["job"].get("result", {}).get("message", ""), state
                        assert before == len(invocations), "repeat invoked uv"
                        return state["job"]["result"]
                    time.sleep(.025)
                raise RuntimeError("repeat never completed")
            limb("repeat", repeat)

            def refusal():
                before = len(invocations)
                response = client.post("/api/extensions/install", json={"name": "impossible-71a-extra-!"})
                check_refusal(response, before, invocations)
                say(f"refusal uv invocations before={before} after={len(invocations)}")
            limb("refusal", refusal)
            def control():
                try:
                    check_refusal(SimpleNamespace(status_code=202), 0, [])
                except AssertionError as exc:
                    return f"control fired: {exc}"
                raise AssertionError("refusal scorer accepted an impossible-extra success")
            limb("refusal control", control)

            def inflight():
                result = []
                worker = threading.Thread(target=lambda: result.append(client.post("/api/prompt", json={"message": "gate hold"})))
                worker.start()
                try:
                    if not entered.wait(10):
                        raise NotExercised("POST /api/prompt did not enter the turn")
                    response = client.post("/api/extensions/install", json={"name": "ilastik"})
                    assert response.status_code == 409, response.text
                finally:
                    release.set()
                    worker.join(10)
                assert result and result[0].status_code == 200, "control turn did not finish"
            limb("in-flight", inflight)

            def constraints():
                real = [c for c in invocations if "--dry-run" not in c["argv"]]
                if not real:
                    raise NotExercised("no real install invocation to inspect")
                for call in real:
                    pins = call.get("constraints", "").splitlines()
                    assert pins and all(re.fullmatch(r"[a-z0-9]+(?:[-.][a-z0-9]+)*==[^\s=]+", p) for p in pins), pins
                if not args.selftest:
                    managed()
                (args.out / "installed-constraints.txt").write_text(real[0]["constraints"], encoding="utf-8")
                return f"{len(pins)} exact pins captured at uv dispatch"
            limb("constraints", constraints)

            def progress():
                live = [o["state"] for o in observations if o["state"]["job"].get("running")]
                phases = {s["job"].get("phase") for s in live}
                uv_phases = phases - {None, "checking environment", "running package installer", "verifying"}
                if len(uv_phases) < 2:
                    raise NotExercised("install finished too quickly to observe two uv milestones")
                node = shutil.which("node")
                if not node:
                    raise NotExercised("node is needed to execute the actual pure panel view")
                js = Path(extensions.__file__).with_name("transcript.js")
                script = "global.window={};require(process.argv[1]);const states=JSON.parse(require('fs').readFileSync(process.argv[2],'utf8'));process.stdout.write(JSON.stringify(states.filter(o=>o.state.job.running).map(o=>window.Transcript.extensionsView(o.state)[0].text)));"
                result = subprocess.run([node, "-e", script, str(js), str(args.out / "observations.json")],
                                        capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL, check=True)
                rendered = json.loads(result.stdout)
                assert rendered == [s["job"]["phase"] for s in live], rendered
                return sorted(uv_phases)
            limb("progress", progress)
    say(f"RESULT: {len(failures)} failed or not exercised limbs: {failures}")
    log.close()
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
