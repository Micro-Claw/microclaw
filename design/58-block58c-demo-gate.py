"""Block 58c Windows gate: prepare evidence, observe human launches, verify."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from microclaw import config

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


def run(command: list[str], *, env=None, timeout=900, input_text=None, cwd=None):
    """Run one command, optionally from a directory that is not this checkout.

    `cwd` is load-bearing for any `python -c` that imports microclaw: `-c` puts
    the working directory first on `sys.path`, so running from the repository
    root imports the checkout's package no matter which interpreter was asked.
    The gate's round-1 non-uv limb failed exactly that way — it reported the
    fixture importing `D:\\Code\\microclaw\\microclaw` and called it a
    changed environment. Those callers pass `cwd` *and* `-I`.
    """
    try:
        return subprocess.run(command, capture_output=True, text=True, env=env,
                              timeout=timeout, input=input_text, check=False, cwd=cwd)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NotExercised(f"could not run {command[0]}: {exc}") from exc


def require_success(completed, mechanism: str):
    if completed.returncode:
        raise NotExercised(
            f"{mechanism} did not complete (exit {completed.returncode}): "
            f"{completed.stderr or completed.stdout}"
        )


def tree_manifest(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return {
        str(item.relative_to(path)): hashlib.sha256(item.read_bytes()).hexdigest()
        for item in sorted(candidate for candidate in path.rglob("*") if candidate.is_file())
    }


def read_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        return []


#: Which command produces each phase's artifacts. Verify needs all four.
PHASE_COMMANDS = {
    "prepare": "-Mode Prepare",
    "healthy": "-Mode Healthy   (Micro-Manager OPEN)",
    "closed": "-Mode Closed    (Micro-Manager CLOSED)",
    "rollback": "-Mode Rollback",
}


def need(path: Path, mechanism: str) -> Path:
    """Absent evidence is NOT EXERCISED, never FAIL.

    A limb whose phase was never run in this directory has not failed its
    mechanism -- nothing ran it. Reporting that as FAIL is how a gate makes a
    missing observation look like a broken product.
    """
    if not path.exists():
        raise NotExercised(f"{path.name} is absent; run {mechanism}")
    return path


def record_phase(out: Path, phase: str) -> None:
    """Note that a phase finished, so Verify can say what is still owed."""
    ledger = out / "phases.json"
    try:
        done = json.loads(ledger.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        done = {}
    done[phase] = time.strftime("%Y-%m-%dT%H:%M:%S")
    ledger.write_text(json.dumps(done, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def missing_phases(out: Path) -> list[str]:
    try:
        done = json.loads((out / "phases.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        done = {}
    return [phase for phase in PHASE_COMMANDS if phase not in done]


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def wait_for_new_nonce(log: Path, old_count: int, timeout: float = 45) -> list[str]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        lines = [line for line in read_lines(log) if " nonce=" in line]
        if len(lines) > old_count:
            return lines
        time.sleep(0.2)
    raise NotExercised("desktop icon did not produce a launcher nonce within 45 seconds")


def wait_for_slot_processes_to_exit(timeout: float = 60) -> None:
    deadline = time.time() + timeout
    command = ["powershell", "-NoProfile", "-Command",
               "@(Get-CimInstance Win32_Process | Where-Object "
               "{$_.CommandLine -match 'env-[ab].*microclaw.exe.*serve'}).Count"]
    while time.time() < deadline:
        completed = run(command, timeout=10)
        if completed.returncode == 0 and completed.stdout.strip() in {"", "0"}:
            return
        time.sleep(0.5)
    raise NotExercised("slot serve child did not exit; close its console or press Enter")


def require_reviewed_config(appdata: Path) -> str:
    """Refuse to start before install.bat would block on its own setup server.

    `install.bat` only reaches `:installed_done` when a reviewed safety config is
    already present; otherwise it runs the bridge-readiness loop and then starts
    `serve` for browser setup, which never returns.  This phase drives the
    installer three times with piped stdin, so that path would hang the gate
    rather than fail it.  The two-slot limb classifies this same file.
    """
    document = appdata / "safety_config.yaml"
    if not document.is_file():
        raise RuntimeError(
            f"no safety config at {document}. Complete Microclaw setup on this "
            "machine first: install.bat would otherwise open a blocking setup "
            "server and this gate would hang instead of failing."
        )
    completed = run([sys.executable, "-m", "microclaw", "--safety-config",
                     str(document), "check-config", "--json"])
    require_success(completed, "checkout CLI classification of the machine config")
    classification = json.loads(completed.stdout)["classification"]
    if classification != "ready":
        raise RuntimeError(
            f"{document} classifies as {classification!r}, not 'ready'. install.bat "
            "would open a blocking setup server; repair or review the file first."
        )
    return classification


def prepare(repo: Path, out: Path, managed: Path, appdata: Path) -> int:
    if out.exists():
        raise RuntimeError(f"evidence directory already exists: {out}")
    out.mkdir(parents=True)
    sys.stdout = sys.stderr = Tee(sys.__stdout__, out / "gate.txt", "w")
    print(f"machine config classification: {require_reviewed_config(appdata)}")
    backup = out / "backup"
    backup.mkdir()
    if appdata.exists():
        shutil.copytree(appdata, backup / "appdata-microclaw")
    legacy = managed / "env"
    if legacy.exists():
        shutil.copytree(legacy, backup / "legacy-env")
    print(f"BACKUP COPY: {backup}")
    write_json(out / "appdata-before.json", tree_manifest(appdata))

    nonuv = out / "nonuv"
    completed = run(["uv", "venv", "--python", "3.12", str(nonuv)])
    require_success(completed, "non-uv fixture creation")
    completed = run(["uv", "pip", "install", "--python",
                     str(nonuv / "Scripts" / "python.exe"), "--no-deps", str(repo)])
    require_success(completed, "non-uv fixture install")
    purelib = run([str(nonuv / "Scripts" / "python.exe"), "-I", "-c",
                   "import sysconfig; print(sysconfig.get_path('purelib'))"], cwd=out)
    require_success(purelib, "non-uv site-packages resolution")
    site_packages = Path(purelib.stdout.strip())
    write_json(out / "nonuv.json", {
        "python": str(nonuv / "Scripts" / "python.exe"),
        "site_packages": str(site_packages),
        "before": tree_manifest(site_packages),
        "detection_control": "CONDA_PREFIX (fixture deliberately absent from PATH)",
    })

    # The migration is the item under test, so record what it was handed. A
    # machine where install.bat has already been run has no legacy `env` left,
    # and the layout limb would otherwise pass on a move that never happened.
    legacy_before = (managed / "env").is_dir()
    slot_a_before = (managed / "env-a").is_dir()

    install_env = dict(os.environ)
    install_env["CONDA_PREFIX"] = str(nonuv)
    install_env["PATH"] = os.pathsep.join(
        part for part in install_env.get("PATH", "").split(os.pathsep)
        if Path(part).resolve() != (nonuv / "Scripts").resolve()
    )
    install = repo / "install.bat"
    first = run(["cmd", "/c", str(install)], env=install_env, input_text="\n" * 8)
    (out / "install-first.txt").write_text(first.stdout + first.stderr, encoding="utf-8")
    require_success(first, "first install.bat migration")
    active_before = (managed / "active-slot.txt").read_text(encoding="ascii").strip()
    second = run(["cmd", "/c", str(install)], env=install_env, input_text="\n" * 8)
    (out / "install-second.txt").write_text(second.stdout + second.stderr, encoding="utf-8")
    require_success(second, "second install.bat idempotence")
    active_after = (managed / "active-slot.txt").read_text(encoding="ascii").strip()
    env_a_python = managed / "env-a" / "Scripts" / "python.exe"
    switch = run([str(env_a_python), "-I", "-c",
                  "from pathlib import Path; from microclaw.updates import "
                  "_write_slot_text; import sys; _write_slot_text(Path(sys.argv[1]), 'b')",
                  str(managed / "active-slot.txt")], cwd=out)
    require_success(switch, "controlled active-b installer fixture")
    third = run(["cmd", "/c", str(install)], env=install_env, input_text="\n" * 8)
    (out / "install-active-b.txt").write_text(third.stdout + third.stderr, encoding="utf-8")
    require_success(third, "install.bat with b active")
    env_b_python = managed / "env-b" / "Scripts" / "python.exe"
    restore = run([str(env_b_python), "-I", "-c",
                   "from pathlib import Path; from microclaw.updates import "
                   "_write_slot_text; import sys; _write_slot_text(Path(sys.argv[1]), 'a')",
                   str(managed / "active-slot.txt")], cwd=out)
    require_success(restore, "restore active-a after active-b installer control")
    write_json(out / "install-state.json", {
        "legacy_env_before_first": legacy_before,
        "slot_a_before_first": slot_a_before,
        "legacy_env_after_first": (managed / "env").is_dir(),
        "before_second": active_before, "after_second": active_after,
        "active_b_log_named_env_b": "Installing into active slot b" in third.stdout
                                    and str(managed / "env-b") in third.stdout,
        "final_active": (managed / "active-slot.txt").read_text(encoding="ascii").strip(),
    })

    sha = run(["git", "-C", str(repo), "rev-parse", "HEAD"])
    require_success(sha, "checkout commit resolution")
    write_json(out / "prepare.json", {"backup": str(backup), "commit": sha.stdout.strip()})
    record_phase(out, "prepare")
    print("PREPARE COMPLETE. Continue with the human runbook.")
    return 0


def observe_healthy(out: Path, managed: Path) -> int:
    log = managed / "launcher.log"
    old = len([line for line in read_lines(log) if " nonce=" in line])
    print("Double-click the Microclaw desktop icon now. Leave the server console open.")
    lines = wait_for_new_nonce(log, old)
    command = ["powershell", "-NoProfile", "-Command",
               "Get-CimInstance Win32_Process | Where-Object "
               "{$_.CommandLine -match 'env-[ab].*microclaw.exe.*serve'} | "
               "Select-Object ProcessId,ExecutablePath,CommandLine | Format-List"]
    process = run(command, timeout=20)
    (out / "process-command.txt").write_text(process.stdout + process.stderr,
                                               encoding="utf-8")
    if process.returncode or not re.search(r"env-[ab].*microclaw\.exe.*serve",
                                           process.stdout, re.I | re.S):
        raise NotExercised("real desktop child command line was not captured")
    record_phase(out, "healthy")
    write_json(out / "healthy-observation.json", {"nonce_line": lines[-1]})
    print("Healthy desktop child captured. Stop it with Ctrl+C before the next step.")
    return 0


def observe_closed(out: Path, managed: Path) -> int:
    log = managed / "launcher.log"
    before_lines = read_lines(log)
    active = (managed / "active-slot.txt").read_text(encoding="ascii").strip()
    (managed / "rollback-report.txt").unlink(missing_ok=True)
    print("With Micro-Manager closed, double-click the Microclaw icon now. "
          "After the bridge refusal appears, press Enter in that console.")
    wait_for_new_nonce(log, len([line for line in before_lines if " nonce=" in line]))
    wait_for_slot_processes_to_exit()
    after_lines = read_lines(log)
    # The absence of a rollback proves nothing on its own here: with no pending
    # slot the launcher has nothing to roll back to, so it writes no report
    # whether or not health was ever reached. The half of item 8 that says "the
    # marker IS written" needs the marker itself, read after the child has gone.
    nonce_lines = [line for line in after_lines if " nonce=" in line]
    match = re.search(r"nonce=([0-9a-f]{32})", nonce_lines[-1]) if nonce_lines else None
    try:
        health = (managed / "launch-health.txt").read_text(encoding="ascii").strip()
    except FileNotFoundError:
        health = None
    record_phase(out, "closed")
    write_json(out / "closed-mm-observation.json", {
        "before_nonce_count": len([line for line in before_lines if " nonce=" in line]),
        "after_nonce_count": len(nonce_lines),
        "active_before": active,
        "active_after": (managed / "active-slot.txt").read_text(encoding="ascii").strip(),
        "rollback_report_exists": (managed / "rollback-report.txt").exists(),
        "launch_nonce": match.group(1) if match else None,
        "health_after_child_exited": health,
    })
    return 0


def observe_rollback(out: Path, managed: Path) -> int:
    active = (managed / "active-slot.txt").read_text(encoding="ascii").strip()
    failed = "b" if active == "a" else "a"
    package = managed / f"env-{failed}" / "Lib" / "site-packages" / "microclaw"
    hidden = package.with_name("microclaw.block58c")
    if not package.is_dir():
        raise NotExercised(f"real candidate package missing: {package}")
    (managed / "pending-slot.txt").write_text(failed + "\n", encoding="ascii")
    package.rename(hidden)
    log = managed / "launcher.log"
    before = read_lines(log)
    try:
        print(f"Double-click the icon now. Slot {failed} will start but fail import before health.")
        wait_for_new_nonce(log, len([line for line in before if " nonce=" in line]))
        wait_for_slot_processes_to_exit()
    finally:
        hidden.rename(package)
    middle = read_lines(log)
    if (managed / "active-slot.txt").read_text(encoding="ascii").strip() != active:
        raise AssertionError("failed launch did not atomically restore the known-good slot")
    print("Double-click the icon once more. The successful launch must print the deferred rollback.")
    wait_for_new_nonce(log, len([line for line in middle if " nonce=" in line]))
    deadline = time.time() + 45
    while time.time() < deadline and not any(
            "rollback-reported=" in line for line in read_lines(log)[len(middle):]):
        time.sleep(0.2)
    after = read_lines(log)
    record_phase(out, "rollback")
    write_json(out / "rollback-observation.json", {
        "active": active, "failed": failed,
        "before": before, "after_failed": middle, "after_success": after,
    })
    print("Rollback report captured. Stop the healthy server with Ctrl+C.")
    return 0


def verify(repo: Path, out: Path, managed: Path, appdata: Path) -> int:
    # The marker records the commit the installer BUILT FROM, so it is compared
    # against the commit Prepare recorded at that moment -- not against whatever
    # HEAD is now. Reading HEAD here made an ordinary `git pull` between phases
    # look like a marker defect and forced a whole gate to be rerun.
    owed = missing_phases(out)
    if owed:
        # One list, up front. Discovering this one NOT EXERCISED limb at a time
        # is how an operator ends up rerunning phases in the wrong order.
        print("=" * 70)
        print("VERIFY CANNOT SCORE THIS EVIDENCE DIRECTORY YET")
        print("=" * 70)
        print(f"Evidence directory: {out}")
        print("Phases still owed here, each one command:")
        for phase in owed:
            print(f"  .\\design\\58-block58c-demo-gate.ps1 {PHASE_COMMANDS[phase]}")
        print("")
        print("If a phase already passed in an EARLIER evidence directory at the")
        print("same product commit, that evidence still stands -- send both")
        print("directories rather than rerunning it here.")
        return 1

    prepared = json.loads((out / "prepare.json").read_text(encoding="utf-8"))
    installed_sha = prepared["commit"]
    live = run(["git", "-C", str(repo), "rev-parse", "HEAD"])
    require_success(live, "checkout identity")
    if live.stdout.strip() != installed_sha:
        print(f"NOTE: HEAD is now {live.stdout.strip()}; this gate's slots were "
              f"installed from {installed_sha} and are scored against that.")

    @limb("migrated layout and immutable commit",
          "env-a is absent, active is invalid, active metadata differs from the "
          "commit Prepare installed, or no legacy env was actually migrated")
    def _():
        state = json.loads((out / "install-state.json").read_text(encoding="utf-8"))
        # A move that never happened must not read as a migration. This limb is
        # NOT EXERCISED, never PASS, on a machine whose legacy `env` was already
        # consumed by an install.bat run outside the gate.
        if not state["legacy_env_before_first"] or state["slot_a_before_first"]:
            raise NotExercised(
                "no legacy env to migrate: this machine had "
                f"env={state['legacy_env_before_first']}, "
                f"env-a={state['slot_a_before_first']} before the first install. "
                "Restore a legacy layout and rerun Prepare."
            )
        if state["legacy_env_after_first"]:
            raise AssertionError("the legacy env survived the migration")
        active = (managed / "active-slot.txt").read_text(encoding="ascii").strip()
        marker = json.loads((managed / f"env-{active}" / "microclaw-slot.json").read_text(
            encoding="utf-8"))
        if not (managed / "env-a").is_dir() or active not in {"a", "b"} or marker.get("commit") != installed_sha:
            raise AssertionError(f"active={active}; marker={marker}; installed={installed_sha}")
        return f"env migrated to env-a; active={active}; commit={installed_sha}"

    @limb("each slot stands on a Python outside every evidence folder",
          "a slot interpreter cannot start, or its base_prefix is missing or sits "
          "under a gate evidence directory")
    def _():
        proved = {}
        for slot in ("a", "b"):
            python = managed / f"env-{slot}" / "Scripts" / "python.exe"
            if not python.exists():
                continue
            completed = run([str(python), "-I", "-c",
                             "import sys, os; print(sys.base_prefix); "
                             "print(os.path.isdir(sys.base_prefix))"], cwd=out)
            if completed.returncode:
                raise AssertionError(
                    f"env-{slot} interpreter cannot start: "
                    f"{(completed.stderr or completed.stdout).strip()}"
                )
            base, exists = completed.stdout.strip().splitlines()
            if exists != "True":
                raise AssertionError(f"env-{slot} base_prefix does not exist: {base}")
            resolved = Path(base).resolve()
            if resolved == out or out in resolved.parents:
                raise AssertionError(
                    f"env-{slot} depends on a directory inside this gate's own "
                    f"evidence and will break when it is deleted: {resolved}"
                )
            proved[f"env-{slot}"] = base
        if not proved:
            raise NotExercised("no slot interpreter exists to interrogate")
        return json.dumps(proved, sort_keys=True)

    @limb("desktop command line uses selected slot",
          "the captured real process does not name an env-a/env-b microclaw.exe serve child")
    def _():
        text = need(out / "process-command.txt",
                    "-Mode Healthy in this evidence directory").read_text(
                        encoding="utf-8")
        if not re.search(r"env-[ab].*microclaw\.exe.*serve", text, re.I | re.S):
            raise AssertionError(text)
        return text.strip()

    @limb("nonce-matched health marker",
          "health is absent or differs from the last launcher-generated nonce")
    def _():
        nonce_lines = [line for line in read_lines(managed / "launcher.log") if " nonce=" in line]
        if not nonce_lines:
            # No launcher-driven launch has ever happened here, so there is no
            # marker to judge. Absent evidence, not a failed mechanism.
            raise NotExercised(
                "launcher.log records no launch; run -Mode Healthy or -Mode Closed"
            )
        try:
            health = (managed / "launch-health.txt").read_text(encoding="ascii").strip()
        except FileNotFoundError:
            raise AssertionError(
                f"a launch was recorded ({nonce_lines[-1]}) but left no health marker"
            ) from None
        match = re.search(r"nonce=([0-9a-f]{32})", nonce_lines[-1])
        if not match or health != match.group(1):
            raise AssertionError(f"health={health!r}; last={nonce_lines[-1]}")
        return f"health equals fresh launcher nonce {health}"

    @limb("a closed rig still reaches health, and that launch is final",
          "the launch adds other than one nonce, changes active, writes rollback, or "
          "leaves no marker carrying that launch's own nonce")
    def _():
        data = json.loads(need(out / "closed-mm-observation.json",
                               "-Mode Closed in this evidence directory").read_text(
                                   encoding="utf-8"))
        if "health_after_child_exited" not in data:
            raise NotExercised(
                "this observation predates the health capture; rerun -Mode Closed"
            )
        if (data["after_nonce_count"] - data["before_nonce_count"] != 1
                or data["active_before"] != data["active_after"]
                or data["rollback_report_exists"]):
            raise AssertionError(data)
        # Item 7: a closed bridge is not evidence that new code is defective, so
        # health must be reached before build_session ever contacts it.
        if not data["launch_nonce"] or data["health_after_child_exited"] != data["launch_nonce"]:
            raise AssertionError(
                f"the child exited on the bridge without leaving its own health: {data}"
            )
        return json.dumps(data, sort_keys=True)

    @limb("rollback is reported once on the next successful launch",
          "failed child does not start, active is not restored, or report is absent/duplicated/misordered")
    def _():
        data = json.loads(need(out / "rollback-observation.json",
                               "-Mode Rollback in this evidence directory").read_text(
                                   encoding="utf-8"))
        before, middle, after = data["before"], data["after_failed"], data["after_success"]
        failed_new = middle[len(before):]
        success_new = after[len(middle):]
        reports_failed = [line for line in failed_new if "rollback-reported=" in line]
        reports_success = [line for line in success_new if "rollback-reported=" in line]
        if (len([line for line in failed_new if " nonce=" in line]) != 1
                or reports_failed or len(reports_success) != 1):
            raise AssertionError({"failed": failed_new, "success": success_new})
        return reports_success[0]

    @limb("APPDATA config, key, and histories unchanged",
          "the complete before/after byte-hash manifests differ")
    def _():
        before = json.loads((out / "appdata-before.json").read_text(encoding="utf-8"))
        after = tree_manifest(appdata)
        if before != after:
            raise AssertionError("APPDATA manifests differ")
        return f"unchanged files={len(after)}"

    @limb("installer twice retains active slot",
          "either installer did not complete or the second run changed active-slot.txt")
    def _():
        data = json.loads((out / "install-state.json").read_text(encoding="utf-8"))
        if (data["before_second"] != data["after_second"]
                or not data["active_b_log_named_env_b"] or data["final_active"] != "a"):
            raise AssertionError(data)
        return json.dumps(data, sort_keys=True)

    @limb("two real slot validators classify one shared config",
          "either real CLI is absent, classification fails, or comparison refuses")
    def _():
        a = managed / "env-a" / "Scripts" / "microclaw.exe"
        b = managed / "env-b" / "Scripts" / "microclaw.exe"
        if not a.exists() or not b.exists():
            raise NotExercised("real second slot is absent; 58b debt remains")
        shared = appdata / "safety_config.yaml"
        active_class = config.classify_config_with_slot(a, shared)
        candidate_class = config.classify_config_with_slot(b, shared)
        comparison = config.compare_slot_configurations(a, b, shared)
        if not comparison.proceed:
            raise AssertionError(comparison.reason)
        return json.dumps({"active": active_class, "candidate": candidate_class}, sort_keys=True)

    @limb("non-uv CONDA_PREFIX environment remains untouched",
          "site-packages changes, import moves, or the explicit detection notice/control is absent")
    def _():
        data = json.loads((out / "nonuv.json").read_text(encoding="utf-8"))
        after = tree_manifest(Path(data["site_packages"]))
        imported = run([data["python"], "-I", "-c",
                        "import microclaw; print(microclaw.__file__)"], cwd=out)
        require_success(imported, "non-uv import control")
        notice = (out / "install-first.txt").read_text(encoding="utf-8")
        if (data["before"] != after or data["site_packages"] not in imported.stdout
                or "Detected through CONDA_PREFIX" not in notice
                or data["detection_control"] != "CONDA_PREFIX (fixture deliberately absent from PATH)"):
            raise AssertionError({"import": imported.stdout, "control": data["detection_control"]})
        return f"{data['detection_control']}; unchanged files={len(after)}"

    write_json(out / "results.json", RESULTS)
    for result in RESULTS:
        print(f"{result['status']}: {result['name']} — {result['detail']}; "
              f"FAILS IF: {result['fails_if']}")
    failed = any(result["status"] == "FAIL" for result in RESULTS)
    incomplete = any(result["status"] == "NOT EXERCISED" for result in RESULTS)
    banner = "FAILED" if failed else "INCOMPLETE" if incomplete else "PASSED"
    print(f"BLOCK 58c DEMO GATE {banner}")
    return 0 if banner == "PASSED" else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True,
                        choices=("prepare", "healthy", "closed", "rollback", "verify"))
    parser.add_argument("--repo", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    repo, out = Path(args.repo).resolve(), Path(args.out).resolve()
    managed = Path(os.environ["LOCALAPPDATA"]) / "microclaw"
    appdata = Path(os.environ["APPDATA"]) / "microclaw"
    if args.mode == "prepare":
        return prepare(repo, out, managed, appdata)
    if not out.is_dir():
        raise NotExercised("prepare phase did not create the evidence directory")
    sys.stdout = sys.stderr = Tee(sys.__stdout__, out / "gate.txt")
    if args.mode == "verify":
        return verify(repo, out, managed, appdata)
    return {"healthy": observe_healthy, "closed": observe_closed,
            "rollback": observe_rollback}[args.mode](out, managed)


if __name__ == "__main__":
    raise SystemExit(main())
