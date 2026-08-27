"""Block 58d Windows gate: cached status, the banner, and the idle-aware routes.

Six phases.  Four of them only compute and are run by this program; the two
human steps in between are the ones a person performs and judges -- operating
the desktop icon, reading the banner, clicking Later, saving the network log.

The program owns its own log (`gate.txt`).  PowerShell 5.1's `Start-Transcript`
does not capture a native child process's stdout, and block 58a's transcript came
back empty twice.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from microclaw import config

BASE = "http://127.0.0.1:8000"
STATE_NAME = "update-state.json"
RESULTS: list[dict[str, str]] = []


class NotExercised(Exception):
    """The mechanism could not run. This is never a pass."""


class Tee:
    def __init__(self, stream, path: Path, mode: str = "a"):
        self.stream = stream
        self.file = path.open(mode, encoding="utf-8")

    def write(self, data):
        self.stream.write(data)
        self.file.write(data)
        return len(data)

    def flush(self):
        self.stream.flush()
        self.file.flush()


def limb(name: str, fails_if: str):
    def decorate(fn):
        try:
            detail, status = fn() or "", "PASS"
        except NotExercised as exc:
            detail, status = str(exc), "NOT EXERCISED"
        except Exception as exc:
            detail, status = f"{type(exc).__name__}: {exc}", "FAIL"
            traceback.print_exc()
        RESULTS.append({"name": name, "status": status, "detail": detail,
                        "fails_if": fails_if})
        return fn
    return decorate


def run(command: list[str], *, timeout=900, cwd=None):
    """Run one command. `cwd` is load-bearing for any `python -c` importing
    microclaw: `-c` puts the working directory first on `sys.path`, so running
    from the repository root imports the checkout's package no matter which
    interpreter was asked. Those callers pass `cwd` *and* `-I`."""
    try:
        return subprocess.run(command, capture_output=True, text=True,
                              timeout=timeout, check=False, cwd=cwd)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NotExercised(f"could not run {command[0]}: {exc}") from exc


def require_success(completed, mechanism: str):
    if completed.returncode:
        raise NotExercised(
            f"{mechanism} did not complete (exit {completed.returncode}): "
            f"{completed.stderr or completed.stdout}"
        )


def need(path: Path, mechanism: str) -> Path:
    """Absent evidence is NOT EXERCISED, never FAIL.

    A limb whose phase was never run in this directory has not failed its
    mechanism -- nothing ran it."""
    if not path.exists():
        raise NotExercised(f"{path.name} is absent; run {mechanism}")
    return path


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def tree_manifest(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return {
        str(item.relative_to(path)): hashlib.sha256(item.read_bytes()).hexdigest()
        for item in sorted(c for c in path.rglob("*") if c.is_file())
    }


def api(method: str, route: str, body=None) -> tuple[int, object]:
    """One loopback request. No Origin header: urllib sends none, and the
    cross-origin middleware only refuses a *foreign* one."""
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(BASE + route, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw or b"null")
        except ValueError:
            return exc.code, raw.decode("utf-8", "replace")
    except urllib.error.URLError as exc:
        raise NotExercised(f"no Microclaw server answering on {BASE}: {exc}") from exc


#: Which command produces each phase's artifacts. Verify needs all five.
PHASE_COMMANDS = {
    "prepare": "-Mode Prepare  (no server running)",
    "probe": "-Mode Probe    (server up, banner visible, no turn running)",
    "busy": "-Mode Busy     (server up, a long agent turn RUNNING)",
    "after": "-Mode After    (Later clicked, server relaunched)",
    "restore": "-Mode Restore  (production state put back)",
}


def record_phase(out: Path, phase: str) -> None:
    ledger = out / "phases.json"
    try:
        done = read_json(ledger)
    except (FileNotFoundError, ValueError):
        done = {}
    done[phase] = time.strftime("%Y-%m-%dT%H:%M:%S")
    write_json(ledger, done)


def missing_phases(out: Path) -> list[str]:
    try:
        done = read_json(out / "phases.json")
    except (FileNotFoundError, ValueError):
        done = {}
    return [phase for phase in PHASE_COMMANDS if phase not in done]


# --------------------------------------------------------------------------
# Phase 1: Prepare
# --------------------------------------------------------------------------

def prepare(repo: Path, out: Path, managed: Path, appdata: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    sys.stdout = sys.stderr = Tee(sys.__stdout__, out / "gate.txt")
    state_file = managed / STATE_NAME
    if not state_file.is_file():
        print(f"FATAL: no managed install at {state_file}. Run install.bat from "
              "this branch first, so the slot under test carries block 58d.")
        return 1

    # Backups first, before anything is rewritten. This gate edits one field of
    # real production state; Restore puts it back and Verify scores that it did.
    shutil.copy2(state_file, out / "update-state.before.json")
    backup = out / "appdata-backup"
    if not backup.exists():
        shutil.copytree(appdata, backup)
    write_json(out / "appdata-manifest.before.json", tree_manifest(appdata))

    tip = run(["git", "-C", str(repo), "rev-parse", "origin/main"])
    require_success(tip, "git rev-parse origin/main")
    tip_sha = tip.stdout.strip().lower()
    subject = run(["git", "-C", str(repo), "log", "-1", "--format=%s", tip_sha])
    require_success(subject, "git log origin/main subject")
    parent = run(["git", "-C", str(repo), "rev-parse", f"{tip_sha}~1"])
    require_success(parent, "git rev-parse origin/main~1")

    # The block branch is not merged, so a real clone install records a commit
    # that is NOT an ancestor of origin/main and discovery correctly reports
    # `diverged` -- no candidate, no banner, nothing to gate. The recorded
    # installed commit is therefore set to a real ancestor (origin/main~1) so
    # discovery has something to find. The slot's *code* is untouched: it stays
    # the branch build that is under test.
    state = read_json(state_file)
    original_installed = state.get("installed_commit")
    state["installed_commit"] = parent.stdout.strip().lower()
    state.pop("last_attempt", None)      # make the launch check due
    state.pop("next_check", None)
    state.pop("dismissal", None)         # a stale dismissal would hide the banner
    write_json(state_file, state)

    slot = managed / "env-a" / "Scripts" / "microclaw.exe"
    slot_python = managed / "env-a" / "Scripts" / "python.exe"
    routes = run([str(slot_python), "-I", "-c",
                  "import microclaw.webserve as w, pathlib;"
                  "text = pathlib.Path(w.__file__).read_text(encoding='utf-8');"
                  "print(sum(r in text for r in ('/api/update', '/api/update/check',"
                  "'/api/update/stage', '/api/update/restart', '/api/update/dismiss')))"],
                 cwd=out)
    write_json(out / "prepare.json", {
        "origin_main": tip_sha,
        "origin_main_short": tip_sha[:7],
        "origin_main_subject": subject.stdout.strip(),
        "installed_commit_original": original_installed,
        "installed_commit_for_gate": state["installed_commit"],
        "slot_exe_exists": slot.is_file(),
        "slot_route_count": routes.stdout.strip(),
        "slot_route_stderr": routes.stderr.strip()[:400],
        "env_b_exists": (managed / "env-b" / "Scripts" / "microclaw.exe").is_file(),
        "pending_before": (managed / "pending-slot.txt").exists(),
    })
    record_phase(out, "prepare")
    print(f"Prepared. origin/main is {tip_sha[:7]} — {subject.stdout.strip()}")
    print("Now launch Microclaw from the DESKTOP ICON and follow the runbook.")
    return 0


# --------------------------------------------------------------------------
# Phase 2: Probe -- server up, banner visible, nothing running
# --------------------------------------------------------------------------

def probe(out: Path, managed: Path) -> int:
    state_file = managed / STATE_NAME
    before = read_json(state_file)
    reads = []
    for _ in range(5):
        status, payload = api("GET", "/api/update")
        reads.append({"status": status, "payload": payload})
        time.sleep(0.2)
    between = read_json(state_file)

    idle_restart = api("POST", "/api/update/restart")

    first_check = api("POST", "/api/update/check")
    after_first = read_json(state_file)
    later_checks = [api("POST", "/api/update/check") for _ in range(3)]

    write_json(out / "probe.json", {
        "last_attempt_before_gets": before.get("last_attempt"),
        "last_attempt_after_gets": between.get("last_attempt"),
        "discovery_before_gets": before.get("discovery"),
        "discovery_after_gets": between.get("discovery"),
        "gets": reads,
        "idle_restart": {"status": idle_restart[0], "body": idle_restart[1]},
        "check_statuses": [first_check[0]] + [c[0] for c in later_checks],
        "last_attempt_after_check": after_first.get("last_attempt"),
        "state_after_checks": read_json(state_file),
    })
    record_phase(out, "probe")
    print("Probe captured. Now start the long turn from the runbook, and while "
          "it is still running run -Mode Busy.")
    return 0


# --------------------------------------------------------------------------
# Phase 3: Busy -- a long agent turn is running right now
# --------------------------------------------------------------------------

def busy(out: Path, managed: Path) -> int:
    status, payload = api("POST", "/api/update/restart")
    stop = api("GET", "/api/confirm")
    write_json(out / "busy.json", {
        "restart_status": status,
        "restart_body": payload,
        "confirm_probe": {"status": stop[0], "body": stop[1]},
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })
    record_phase(out, "busy")
    print(f"Busy captured: POST /api/update/restart -> {status} {payload}")
    return 0


# --------------------------------------------------------------------------
# Phase 4: After -- Later clicked, server stopped and relaunched
# --------------------------------------------------------------------------

def after(out: Path, managed: Path) -> int:
    status, payload = api("GET", "/api/update")
    write_json(out / "after.json", {
        "status": status,
        "payload": payload,
        "state": read_json(managed / STATE_NAME),
        "pending_now": (managed / "pending-slot.txt").exists(),
    })
    record_phase(out, "after")
    print(f"After relaunch: candidate = {(payload or {}).get('candidate')!r}")
    return 0


# --------------------------------------------------------------------------
# Phase 5: Restore -- put production state back
# --------------------------------------------------------------------------

def restore(out: Path, managed: Path, appdata: Path) -> int:
    state_file = managed / STATE_NAME
    saved = read_json(need(out / "update-state.before.json", PHASE_COMMANDS["prepare"]))
    live = read_json(state_file)
    original = saved.get("installed_commit")
    live["installed_commit"] = original
    pending = managed / "pending-slot.txt"
    removed = pending.exists()
    if removed:
        pending.unlink()
    write_json(state_file, live)
    write_json(out / "restore.json", {
        "installed_commit_restored_to": original,
        "installed_commit_now": read_json(state_file).get("installed_commit"),
        "pending_removed": removed,
        "appdata_manifest_after": tree_manifest(appdata),
    })
    record_phase(out, "restore")
    print(f"Restored installed_commit to {original}. Now run -Mode Verify.")
    return 0


# --------------------------------------------------------------------------
# Phase 6: Verify -- score everything from the artifacts
# --------------------------------------------------------------------------

def verify(repo: Path, out: Path, managed: Path, appdata: Path) -> int:
    owed = missing_phases(out)
    if owed:
        print("PREFLIGHT: these phases have no artifacts in this directory. A "
              "phase that already passed in an earlier directory at the same "
              "product commit does not need rerunning; anything else is owed:")
        for phase in owed:
            print(f"  {PHASE_COMMANDS[phase]}")

    prep = read_json(need(out / "prepare.json", PHASE_COMMANDS["prepare"]))

    @limb("the managed slot under test carries block 58d",
          "the slot's own webserve.py is missing any of the five /api/update routes")
    def _():
        if not prep["slot_exe_exists"]:
            raise NotExercised("env-a\\Scripts\\microclaw.exe is absent")
        if prep["slot_route_count"] != "5":
            raise AssertionError(
                f"slot reported {prep['slot_route_count']!r} of 5 routes; "
                f"stderr={prep['slot_route_stderr']!r}"
            )
        return "all five routes present in the installed slot"

    @limb("the cached candidate is the real origin/main tip, not a fixture",
          "the cached SHA or subject differs from git's, or no candidate was discovered")
    def _():
        data = read_json(need(out / "probe.json", PHASE_COMMANDS["probe"]))
        payload = data["gets"][0]["payload"] or {}
        candidate = payload.get("candidate")
        if not candidate:
            raise AssertionError(
                f"no candidate in /api/update; discovery={data['state_after_checks'].get('discovery')!r}"
            )
        if candidate["sha"].lower() != prep["origin_main"]:
            raise AssertionError(f"{candidate['sha']} != {prep['origin_main']}")
        if candidate["subject"] != prep["origin_main_subject"]:
            raise AssertionError(f"{candidate['subject']!r} != {prep['origin_main_subject']!r}")
        return f"{prep['origin_main_short']} — {prep['origin_main_subject']}"

    @limb("GET /api/update performs no network I/O",
          "five GETs move last_attempt or change the discovery record")
    def _():
        data = read_json(need(out / "probe.json", PHASE_COMMANDS["probe"]))
        if data["last_attempt_before_gets"] != data["last_attempt_after_gets"]:
            raise AssertionError(
                f"last_attempt moved {data['last_attempt_before_gets']} -> "
                f"{data['last_attempt_after_gets']} across five GETs"
            )
        if data["discovery_before_gets"] != data["discovery_after_gets"]:
            raise AssertionError("the discovery record changed across five GETs")
        if [read["status"] for read in data["gets"]] != [200] * 5:
            raise AssertionError([read["status"] for read in data["gets"]])
        return f"five GETs, last_attempt held at {data['last_attempt_after_gets']!r}"

    @limb("Check now re-checks and the attempt timestamp moves",
          "the first POST is not 200, or last_attempt does not strictly advance")
    def _():
        data = read_json(need(out / "probe.json", PHASE_COMMANDS["probe"]))
        if data["check_statuses"][0] != 200:
            raise AssertionError(f"first check returned {data['check_statuses'][0]}")
        before, after_ = data["last_attempt_after_gets"], data["last_attempt_after_check"]
        if not isinstance(after_, (int, float)):
            raise AssertionError(f"last_attempt is {after_!r} after Check now")
        if isinstance(before, (int, float)) and after_ <= before:
            raise AssertionError(f"last_attempt did not advance: {before} -> {after_}")
        return f"last_attempt {before!r} -> {after_!r}"

    @limb("CONTROL: the fourth Check now inside the window is refused",
          "an unbounded forced check is accepted, so the button can hammer GitHub")
    def _():
        data = read_json(need(out / "probe.json", PHASE_COMMANDS["probe"]))
        statuses = data["check_statuses"]
        if statuses[:3] != [200, 200, 200] or statuses[3] != 429:
            raise AssertionError(f"check statuses were {statuses}, expected [200,200,200,429]")
        return "200, 200, 200, 429"

    @limb("CONTROL: an idle restart request is honest rather than fabricated",
          "the idle seam claims a restart that 58d does not perform")
    def _():
        data = read_json(need(out / "probe.json", PHASE_COMMANDS["probe"]))
        idle = data["idle_restart"]
        if idle["status"] == 409:
            raise NotExercised(
                f"the session was not idle when Probe ran: {idle['body']!r}"
            )
        if idle["status"] != 501 or idle["body"].get("restart_requested") is not False:
            raise AssertionError(idle)
        return f"{idle['status']} {idle['body']}"

    @limb("POST /api/update/restart refuses while an agent turn is running",
          "the route accepts a restart mid-turn, or the refusal names another condition")
    def _():
        data = read_json(need(out / "busy.json", PHASE_COMMANDS["busy"]))
        if data["restart_status"] != 409:
            raise AssertionError(
                f"got {data['restart_status']} {data['restart_body']!r}; the turn "
                "may already have finished — rerun Busy with a longer prompt"
            )
        detail = str((data["restart_body"] or {}).get("detail", "")).lower()
        if "turn" not in detail:
            raise AssertionError(f"refused for the wrong reason: {detail!r}")
        return f"409 {detail}"

    @limb("Later hides the banner and a server restart does not bring it back",
          "the dismissal is not recorded for that commit, or the candidate returns after relaunch")
    def _():
        data = read_json(need(out / "after.json", PHASE_COMMANDS["after"]))
        dismissal = data["state"].get("dismissal")
        if not isinstance(dismissal, dict) or dismissal.get("action") != "later":
            # "Later was never clicked" and "the click did not record" are
            # different results, and after the fact the state file alone cannot
            # tell them apart. The saved network log can: a click puts a
            # POST /api/update/dismiss in it.
            har = out / "network.har"
            clicked = har.exists() and "/api/update/dismiss" in har.read_text(
                encoding="utf-8", errors="replace")
            if clicked:
                raise AssertionError(
                    f"the browser posted a dismissal but state records {dismissal!r}"
                )
            raise NotExercised(
                "no `later` dismissal was recorded and the network log shows no "
                "dismiss request — the Later button was never clicked"
            )
        if dismissal.get("commit", "").lower() != prep["origin_main"]:
            raise AssertionError(f"dismissed {dismissal.get('commit')} not {prep['origin_main']}")
        if (data["payload"] or {}).get("candidate") is not None:
            raise AssertionError("the candidate came back after the relaunch")
        return f"dismissed until {dismissal.get('until')!r}; candidate null after relaunch"

    @limb("the browser made no request to GitHub",
          "the saved network log names a GitHub host, or contains no /api/update call at all")
    def _():
        har = out / "network.har"
        if not har.exists():
            raise NotExercised(
                "network.har is absent; save it from the browser devtools "
                "Network tab into the evidence folder"
            )
        text = har.read_text(encoding="utf-8", errors="replace")
        hosts = [host for host in ("api.github.com", "codeload.github.com",
                                  "github.com") if host in text]
        if hosts:
            raise AssertionError(f"the browser talked to {hosts}")
        # The control: an empty or unrelated log must not pass this limb.
        if "/api/update" not in text:
            raise NotExercised("the saved log contains no /api/update request")
        return f"{len(text)} bytes of network log, no GitHub host, /api/update present"

    @limb("both real slot CLIs classify the shared config through the paths staging builds",
          "either slot's microclaw.exe is absent, classification fails, or the comparison refuses")
    def _():
        a = managed / "env-a" / "Scripts" / "microclaw.exe"
        b = managed / "env-b" / "Scripts" / "microclaw.exe"
        if not a.is_file() or not b.is_file():
            raise NotExercised(
                "a second real slot is absent; the comparison's rig evidence is 58e's"
            )
        shared = appdata / "safety_config.yaml"
        active = config.classify_config_with_slot(a, shared)
        candidate = config.classify_config_with_slot(b, shared)
        comparison = config.compare_slot_configurations(a, b, shared)
        if not comparison.proceed:
            raise AssertionError(comparison.reason)
        return json.dumps({"active": active, "candidate": candidate}, sort_keys=True)

    @limb("the Restart now BUTTON",
          "nothing — this limb cannot run in 58d and must not report a pass")
    def _():
        raise NotExercised(
            "the button appears only once a pending slot is staged, and 58d "
            "stages nothing on the rig. The route's refusal is gated above; the "
            "button belongs to 58e's end-to-end gate."
        )

    @limb("the gate left production state as it found it",
          "installed_commit was not restored, a pending slot survives, or %APPDATA%\\microclaw changed")
    def _():
        data = read_json(need(out / "restore.json", PHASE_COMMANDS["restore"]))
        if data["installed_commit_now"] != prep["installed_commit_original"]:
            raise AssertionError(
                f"installed_commit is {data['installed_commit_now']!r}, "
                f"not the original {prep['installed_commit_original']!r}"
            )
        if (managed / "pending-slot.txt").exists():
            raise AssertionError("a pending slot survives this gate")
        before = read_json(out / "appdata-manifest.before.json")
        if before != data["appdata_manifest_after"]:
            changed = sorted(set(before) ^ set(data["appdata_manifest_after"])) or [
                name for name in before
                if before[name] != data["appdata_manifest_after"].get(name)
            ]
            raise AssertionError(f"%APPDATA%\\microclaw changed: {changed[:8]}")
        return f"installed_commit back to {data['installed_commit_now']!r}; APPDATA unchanged"

    write_json(out / "results.json", RESULTS)
    for result in RESULTS:
        print(f"{result['status']}: {result['name']} — {result['detail']}; "
              f"FAILS IF: {result['fails_if']}")
    failed = any(result["status"] == "FAIL" for result in RESULTS)
    incomplete = any(result["status"] == "NOT EXERCISED" for result in RESULTS)
    banner = "FAILED" if failed else "INCOMPLETE" if incomplete else "PASSED"
    print(f"BLOCK 58d DEMO GATE {banner}")
    print("INCOMPLETE is the expected verdict: the Restart now button cannot be "
          "exercised without a staged slot and is 58e's. Read the limb list.")
    return 0 if banner == "PASSED" else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True, choices=(
        "prepare", "probe", "busy", "after", "restore", "verify"))
    parser.add_argument("--repo", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    repo, out = Path(args.repo).resolve(), Path(args.out).resolve()
    managed = Path(os.environ["LOCALAPPDATA"]) / "microclaw"
    appdata = Path(os.environ["APPDATA"]) / "microclaw"
    if args.mode == "prepare":
        return prepare(repo, out, managed, appdata)
    if not out.is_dir():
        raise NotExercised("the prepare phase did not create the evidence directory")
    sys.stdout = sys.stderr = Tee(sys.__stdout__, out / "gate.txt")
    if args.mode == "verify":
        return verify(repo, out, managed, appdata)
    if args.mode == "restore":
        return restore(out, managed, appdata)
    return {"probe": probe, "busy": busy, "after": after}[args.mode](out, managed)


if __name__ == "__main__":
    raise SystemExit(main())
