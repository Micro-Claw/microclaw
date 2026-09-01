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
import contextlib, json, threading, time, types, sys
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
s._audit_confirmation = webserve.Session._audit_confirmation.__get__(s)
s.confirm = webserve.Session.confirm.__get__(s)
credentials.load_api_key = lambda: ("test-key", "selftest")
marker = sys.argv[3]
def turn(*args, **kwargs):
    yield {"type": "round_start"}
    for i in range(12): yield {"type": "text_delta", "text": "payload-token-" + str(i)}
    def decline_real_confirmation():
        while s.pending is None: time.sleep(0.001)
        s.pending.reply.put((False, "browser"))
    answer = threading.Thread(target=decline_real_confirmation)
    answer.start()
    s.confirm("Save knowledge rig/gate_marker: " + marker,
              kind="knowledge", subject="rig/gate_marker")
    answer.join()
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
# The browser console of a healthy gate session carries one `turn settled` line
# per completed turn and NO `stream silence` line: keepalives arrive every 10 s
# and the detector fires at 30 s, so silence cannot occur on loopback, which is
# why limb 2b is settled off-rig. Writing a silence line here made the fake
# encode an assumption the gate machine contradicts, and hid a scorer that
# demanded one -- which would have reported NOT EXERCISED forever.
Path(sys.argv[2]).write_text(
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


def status_of(report, limb):
    return next(
        line.split(":", 1)[0] for line in report.splitlines()
        if f": {limb} " in line
    )


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
        check("product artifact contains a real confirmation audit with its summary",
              "[microclaw] Confirmation audit:" in good_server.read_text(encoding="utf-8")
              and marker in good_server.read_text(encoding="utf-8"))
        code, report = run(root, "main", main_server, main_browser, marker)
        check("main tree: gate fails because its events have no seq",
              code != 0 and "COMPUTED GATE FAILED" in report,
              report.strip().replace("\n", " | "))

        # Each computed limb has a red control on the good tree.
        broken6 = root / "broken6.log"
        text = good_server.read_text(encoding="utf-8")
        summary = next(
            match for line in text.splitlines()
            if (match := gate.SUMMARY.fullmatch(line))
        )
        final = int(summary.group(4))
        broken6.write_text(
            text.replace(f"final seq {final}", f"final seq {final - 1}"),
            encoding="utf-8",
        )
        code, report = run(root, "fail-l6", broken6, good_browser, marker)
        check("L6 can fail", code != 0 and report.startswith("FAIL: L6"))

        broken7 = root / "broken7.log"
        broken7.write_text(good_browser.read_text(encoding="utf-8"), encoding="utf-8")
        lines = broken7.read_text(encoding="utf-8").splitlines()
        lines[-1] = lines[-1].rsplit(" ", 1)[0] + f" {final - 1}"
        broken7.write_text("\n".join(lines) + "\n", encoding="utf-8")
        code, report = run(root, "fail-l7", good_server, broken7, marker)
        check("L7 can fail", code != 0 and "FAIL: L7" in report)

        # A silence line is no longer required, but one that IS captured must
        # still be scored against its server turn -- otherwise dropping the
        # requirement would have dropped the check with it.
        silent7 = root / "silence7.log"
        silent7.write_text(
            good_browser.read_text(encoding="utf-8")
            + f"Microclaw stream silence; turn: {summary.group(1)} "
              f"last applied seq: {final + 1}\n",
            encoding="utf-8",
        )
        code, report = run(root, "fail-l7-silence", good_server, silent7, marker)
        check("a captured silence record is still checked against the server",
              code != 0 and "FAIL: L7" in report)

        broken8 = root / "broken8.log"
        turn_id = summary.group(1)
        broken8.write_text(
            text + f"[microclaw turn {turn_id}] diagnostic {marker}\n",
            encoding="utf-8",
        )
        code, report = run(root, "fail-l8", broken8, good_browser, marker)
        check("L8 can fail", code != 0 and "FAIL: L8" in report)

        delta8 = root / "delta8.log"
        delta8.write_text(
            text + f"[microclaw turn {turn_id}] seq 1 text_delta\n",
            encoding="utf-8",
        )
        code, report = run(root, "fail-l8-delta", delta8, good_browser, marker)
        check("L8 can fail on a per-delta event line",
              code != 0 and "FAIL: L8" in report)

        # Round 2's operator skipped the marker-seeding submit, so the marker
        # occurred nowhere in the session and L8 "passed" on an assertion that
        # could not fail. Scoring a real artifact against a marker it never
        # carried must report NOT EXERCISED.
        code, report = run(root, "unseeded-l8", good_server, good_browser,
                           "MARKER_THIS_SESSION_NEVER_CARRIED")
        check("an unseeded marker makes L8 NOT EXERCISED, never PASS",
              code != 0 and status_of(report, "L8") == "NOT EXERCISED",
              report.strip().replace("\n", " | "))

        code, report = run(root, "missing")
        check("missing artifacts are NOT EXERCISED and nonzero",
              code != 0 and report.count("NOT EXERCISED") == 3)
        check("scorer owns its log file", (root / "missing-score.log").is_file())

        empty = root / "empty-server.log"
        empty.write_text("", encoding="utf-8")
        code, report = run(root, "empty-server", empty, good_browser, marker)
        check("empty server log makes L6-L8 NOT EXERCISED and exits nonzero",
              code != 0 and all(status_of(report, limb) == "NOT EXERCISED"
                                for limb in ("L6", "L7", "L8")))

        eventless = root / "eventless-server.log"
        eventless.write_text(
            "[microclaw] Confirmation audit: " + marker + "\n",
            encoding="utf-8",
        )
        code, report = run(root, "eventless-server", eventless, good_browser, marker)
        check("eventless server log makes L6-L8 NOT EXERCISED and exits nonzero",
              code != 0 and all(status_of(report, limb) == "NOT EXERCISED"
                                for limb in ("L6", "L7", "L8")))

    failed = [name for name, ok in CHECKS if not ok]
    print(f"\n{len(CHECKS) - len(failed)}/{len(CHECKS)} selftest checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
