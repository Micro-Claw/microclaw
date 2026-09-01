"""End-to-end discrimination selftest for the block 69a computed gate.

Run on this tree and an archived ``main`` source tree. The server artifact is
captured from a real POST through ``build_app``; no server log line is fabricated.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve()
ROOT = HERE.parents[1]
spec = importlib.util.spec_from_file_location("gate69a", HERE.with_name("69a-gate.py"))
gate = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(gate)

CHECKS = []


def check(name, condition, detail=""):
    CHECKS.append((name, condition))
    print(("ok   " if condition else "FAIL ") + name + (" - " + detail if detail else ""))


TURN_SCRIPT = r'''
import contextlib, json, threading, types, sys
from pathlib import Path
from fastapi.testclient import TestClient
from microclaw import credentials, webserve
from microclaw.safety import SafetyConstraints, SafetyGuard

class Lock:
    def __init__(self): self.value = False
    def locked(self): return self.value
    async def acquire(self): self.value = True
    def release(self): self.value = False

s = types.SimpleNamespace(
    ctrl=object(), guard=SafetyGuard(SafetyConstraints()), model=None, history=[],
    history_fn="unused", save=False, editable=True, lock=Lock(),
    cancel=threading.Event(), _emit=None, pending=None, last_resolution=None,
    current_turn_id=None, audit_records=[], current_identity="loopback",
    mode=webserve.SessionMode.NORMAL, tool_schemas=[], tool_registry={})
credentials.load_api_key = lambda: ("test-key", "selftest")
marker = sys.argv[3]
def turn(*args, **kwargs):
    yield {"type": "round_start"}
    for i in range(12): yield {"type": "text_delta", "text": "payload-token-" + str(i)}
    yield {"type": "confirm_request", "id": "confirm-selftest", "summary": marker}
    yield {"type": "done", "reply": "complete"}
webserve.run_agent_iter = turn
with Path(sys.argv[1]).open("w", encoding="utf-8") as output, contextlib.redirect_stdout(output):
    response = TestClient(webserve.build_app(s)).post("/api/prompt", json={"message": "gate"})
events = []
for frame in response.text.split("\n\n"):
    for line in frame.splitlines():
        if line.startswith("data:"): events.append(json.loads(line[5:]))
turn_id = response.headers["X-Microclaw-Turn-ID"]
last = events[-1].get("seq")
Path(sys.argv[2]).write_text(
    f"Microclaw stream silence; turn: {turn_id} last applied seq: {last}\n"
    f"Microclaw turn settled; turn: {turn_id} last applied seq: {last}\n",
    encoding="utf-8")
'''


def product_artifacts(tree: Path, root: Path, marker: str):
    server, browser = root / "server.log", root / "browser.log"
    env = dict(os.environ, PYTHONPATH=str(tree))
    subprocess.run(
        [sys.executable, "-c", TURN_SCRIPT, str(server), str(browser), marker],
        cwd=root, env=env, check=True, capture_output=True, text=True,
    )
    return server, browser


def run(root, name, server=None, browser=None, marker="PAYLOAD_69A_SELFTEST"):
    log = root / f"{name}-score.log"
    argv = ["--log", str(log), "--forbidden", marker]
    if server is not None: argv += ["--server-log", str(server)]
    if browser is not None: argv += ["--browser-log", str(browser)]
    code = gate.main(argv)
    return code, log.read_text(encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--main-tree", required=True, type=Path)
    args = parser.parse_args()
    marker = "PAYLOAD_69A_SELFTEST"
    with tempfile.TemporaryDirectory(prefix="69a-gate-selftest-") as raw:
        root = Path(raw)
        good_dir, main_dir = root / "good", root / "main"
        good_dir.mkdir(); main_dir.mkdir()
        good_server, good_browser = product_artifacts(ROOT, good_dir, marker)
        main_server, main_browser = product_artifacts(args.main_tree.resolve(), main_dir, marker)

        code, report = run(root, "good", good_server, good_browser, marker)
        check("69a tree: gate passes on real product output",
              code == 0 and "COMPUTED GATE PASSED" in report)
        code, report = run(root, "main", main_server, main_browser, marker)
        check("main tree: gate fails because its events have no seq",
              code != 0 and "COMPUTED GATE FAILED" in report,
              report.strip().replace("\n", " | "))

        # Each computed limb has a red control on the good tree.
        broken6 = root / "broken6.log"
        text = good_server.read_text(encoding="utf-8")
        broken6.write_text(text.replace("final seq 15", "final seq 14"), encoding="utf-8")
        code, report = run(root, "fail-l6", broken6, good_browser, marker)
        check("L6 can fail", code != 0 and report.startswith("FAIL: L6"))

        broken7 = root / "broken7.log"
        broken7.write_text(good_browser.read_text(encoding="utf-8").replace(
            "turn settled;", "turn settled;").replace("last applied seq: 15", "last applied seq: 14", 1),
            encoding="utf-8")
        # Make the settled record red regardless of which replacement was first.
        lines = broken7.read_text(encoding="utf-8").splitlines()
        lines[-1] = lines[-1].rsplit(" ", 1)[0] + " 14"
        broken7.write_text("\n".join(lines) + "\n", encoding="utf-8")
        code, report = run(root, "fail-l7", good_server, broken7, marker)
        check("L7 can fail", code != 0 and "FAIL: L7" in report)

        broken8 = root / "broken8.log"
        broken8.write_text(text + marker + "\n", encoding="utf-8")
        code, report = run(root, "fail-l8", broken8, good_browser, marker)
        check("L8 can fail", code != 0 and "FAIL: L8" in report)

        delta8 = root / "delta8.log"
        turn_id = next(
            match.group(1) for line in text.splitlines()
            if (match := gate.SUMMARY.fullmatch(line))
        )
        delta8.write_text(
            text + f"[microclaw turn {turn_id}] seq 1 text_delta\n",
            encoding="utf-8",
        )
        code, report = run(root, "fail-l8-delta", delta8, good_browser, marker)
        check("L8 can fail on a per-delta event line",
              code != 0 and "FAIL: L8" in report)

        code, report = run(root, "missing")
        check("missing artifacts are NOT EXERCISED and nonzero",
              code != 0 and report.count("NOT EXERCISED") == 3)
        check("scorer owns its log file", (root / "missing-score.log").is_file())

    failed = [name for name, ok in CHECKS if not ok]
    print(f"\n{len(CHECKS) - len(failed)}/{len(CHECKS)} selftest checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
