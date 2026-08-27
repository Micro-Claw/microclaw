"""Block 58e Windows evidence collector and independent scorer.

Each phase records raw state. Verify reads those artifacts and scores every
mechanism independently. Missing evidence is NOT EXERCISED, never a pass.
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
from microclaw import config, updates

BASE = "http://127.0.0.1:8000"
SLOT_MARKER_NAME = "microclaw-slot.json"
RESULTS: list[dict[str, str]] = []
MODES = (
    "prepare", "direct", "stage", "notready", "restart", "later",
    "ordinary", "rollback", "failure", "offline", "closed", "restore",
    "publiczip", "verify",
)
MODE_LABELS = {
    "notready": "NotReady",
    "publiczip": "PublicZip",
}
PHASE_COMMANDS = {
    name: ".\\design\\58-block58e-demo-gate.ps1 -Mode "
    + MODE_LABELS.get(name, name.title())
    for name in MODES if name != "verify"
}


class NotExercised(Exception):
    """The mechanism did not run; this is never a pass."""


class Tee:
    def __init__(self, stream, path: Path):
        self.stream = stream
        self.file = path.open("a", encoding="utf-8")

    def write(self, data):
        self.stream.write(data)
        self.file.write(data)
        return len(data)

    def flush(self):
        self.stream.flush()
        self.file.flush()


def limb(name: str, *, fails_if: str):
    def decorate(fn):
        try:
            detail = fn() or ""
            status = "PASS"
        except NotExercised as exc:
            detail = str(exc)
            status = "NOT EXERCISED"
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            status = "FAIL"
            traceback.print_exc()
        RESULTS.append({
            "name": name,
            "status": status,
            "detail": detail,
            "fails_if": fails_if,
        })
        return fn
    return decorate


def write_json(path: Path, value) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def need(out: Path, name: str):
    path = out / f"{name}.json"
    if not path.exists():
        raise NotExercised(
            f"{path.name} is absent; run {PHASE_COMMANDS[name]}"
        )
    return read_json(path)


def record_phase(out: Path, name: str) -> None:
    path = out / "phases.json"
    data = read_json(path) if path.exists() else {}
    data[name] = time.strftime("%Y-%m-%dT%H:%M:%S")
    write_json(path, data)


def api(method: str, route: str) -> tuple[int, object]:
    """Call the cached loopback API and classify an absent server honestly."""
    request = urllib.request.Request(BASE + route, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"null")
    except urllib.error.URLError as exc:
        raise NotExercised(
            f"no Microclaw server answering on {BASE}: {exc}"
        ) from exc


def run(command: list[str], *, cwd: Path | None = None, timeout=900):
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NotExercised(f"could not run {command[0]}: {exc}") from exc


def sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    if root.exists():
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            digest.update(str(path.relative_to(root)).encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()


def process_command_lines() -> list[dict]:
    command = [
        "powershell", "-NoProfile", "-Command",
        "Get-CimInstance Win32_Process | "
        "Where-Object {$_.CommandLine -like '*microclaw*serve*'} | "
        "Select-Object ProcessId,ExecutablePath,CommandLine | "
        "ConvertTo-Json -Compress",
    ]
    completed = run(command)
    if completed.returncode:
        raise NotExercised(
            completed.stderr.strip() or "Win32_Process query failed"
        )
    value = json.loads(completed.stdout or "[]")
    return value if isinstance(value, list) else [value]


def launch_lines(root: Path) -> list[str]:
    path = root / "launcher.log"
    if not path.exists():
        return []
    return [
        line for line in path.read_text(encoding="utf-8-sig").splitlines()
        if " slot=" in line and " nonce=" in line
    ]


def launcher_log_lines(root: Path) -> list[str]:
    path = root / "launcher.log"
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8-sig").splitlines()


def state_snapshot(root: Path) -> dict:
    active_path = root / "active-slot.txt"
    pending_path = root / "pending-slot.txt"
    health_path = root / "launch-health.txt"
    active = active_path.read_text(encoding="ascii").strip()
    active_exe = root / f"env-{active}" / "Scripts" / "microclaw.exe"
    active_marker = root / f"env-{active}" / "microclaw-slot.json"
    inactive = "b" if active == "a" else "a"
    inactive_exe = root / f"env-{inactive}" / "Scripts" / "microclaw.exe"
    inactive_marker = root / f"env-{inactive}" / "microclaw-slot.json"
    return {
        "at": time.time(),
        "active": active,
        "inactive": inactive,
        "pending": pending_path.read_text(encoding="ascii").strip()
        if pending_path.exists() else None,
        "health": health_path.read_text(encoding="ascii").strip()
        if health_path.exists() else None,
        "restart_request_exists": (root / "restart-request.txt").exists(),
        "rollback_report": (root / "rollback-report.txt").read_text(
            encoding="utf-8"
        ).strip() if (root / "rollback-report.txt").exists() else None,
        "state": read_json(root / "update-state.json"),
        "launcher_lines": launch_lines(root),
        "launcher_log": launcher_log_lines(root),
        "active_exe_hash": sha256(active_exe),
        "active_marker_hash": sha256(active_marker),
        "inactive_exe_hash": sha256(inactive_exe),
        "inactive_marker_hash": sha256(inactive_marker),
        # Content hashes cannot prove a rebuild: staging the SAME commit twice
        # produces a byte-identical console script and a byte-identical marker,
        # which is exactly what round 7 saw after Direct and Stage both staged
        # origin/main. The venv's own file timestamp is what moves.
        "inactive_venv_mtime": (
            (root / f"env-{inactive}" / "pyvenv.cfg").stat().st_mtime
            if (root / f"env-{inactive}" / "pyvenv.cfg").is_file() else None
        ),
        "active_slot_commit": (
            read_json(root / f"env-{active}" / SLOT_MARKER_NAME).get("commit")
            if (root / f"env-{active}" / SLOT_MARKER_NAME).is_file() else None
        ),
        "appdata_hash": tree_hash(Path(os.environ["APPDATA"]) / "microclaw"),
    }


def save_phase(out: Path, name: str, data: dict) -> None:
    write_json(out / f"{name}.json", data)
    record_phase(out, name)
def prepare(repo: Path, out: Path, root: Path) -> int:
    """Back up the irreplaceable state and arrange a real origin/main candidate.

    Deliberately **not** a copy of the two slot environments.  Round 2 spent
    minutes here copying several hundred megabytes of `env-a` and `env-b` to a
    Documents folder that is redirected to a network share, and the operator
    paid that cost on every retry.  The environments are the one part of this
    layout that `install.bat` rebuilds from scratch, and the runbook already
    ends by reinstalling.  What cannot be rebuilt is `%APPDATA%\microclaw` --
    config, API key, histories -- and the launcher root's own small files, so
    those are what get copied, to a **local** path.
    """
    roaming = Path(os.environ["APPDATA"]) / "microclaw"
    backup = Path(os.environ["LOCALAPPDATA"]) / f"{out.name}-SAFETY-BACKUP"
    backup.mkdir(parents=True, exist_ok=True)
    shutil.copytree(roaming, backup / "appdata", dirs_exist_ok=True)
    (backup / "launcher-root").mkdir(exist_ok=True)
    for item in sorted(root.iterdir()):
        if item.is_file():
            shutil.copy2(item, backup / "launcher-root" / item.name)
    for slot in ("a", "b"):
        marker = root / f"env-{slot}" / SLOT_MARKER_NAME
        if marker.is_file():
            shutil.copy2(marker, backup / "launcher-root" / f"env-{slot}-{SLOT_MARKER_NAME}")

    original = read_json(root / "update-state.json")
    write_json(out / "original-state.json", original)
    completed = run(["git", "rev-parse", "origin/main~1"], cwd=repo)
    if completed.returncode:
        raise NotExercised(completed.stderr.strip() or "origin/main~1 is absent")
    arranged = completed.stdout.strip()
    state = dict(original)
    state["installed_commit"] = arranged
    write_json(root / "update-state.json", state)

    data = state_snapshot(root)
    data["arranged_commit"] = arranged
    data["branch_active"] = data["active"]
    data["backup"] = str(backup)
    save_phase(out, "prepare", data)
    print(f"BACKUP COPIED TO: {backup}")
    print("  (config, key, histories and the launcher's own files. The two slot "
          "environments are NOT copied -- install.bat rebuilds those.)")
    return 0


def clear_staging_verdict(root: Path) -> dict:
    """Un-poison staging before a phase that intends to build.

    `stage_inactive_slot`'s first check refuses immediately when
    `build_failed_commit` matches the candidate and the interval has not
    elapsed.  That is correct product behaviour -- and it means one failed build
    silently short-circuits every later staging phase in this gate, which is
    exactly what happened on round 1: four limbs produced no evidence because
    the first phase had failed.  Each staging phase clears it and records what
    it cleared, so a cascade cannot hide a mechanism that was never run.  The
    Failure phase clears it too: it needs a genuine build attempt of its own.
    """
    path = root / "update-state.json"
    state = read_json(path)
    # Both poisons, not one.  Round 3 cleared only the build keys, so the
    # NotReady phase's `comparison_refused_commit` survived into the Stage
    # phase: the banner showed "this update needs the maintainer" instead of
    # the Restart controls, and -- worse -- the staging job's own failure was
    # swallowed by a product guard that read that stale record as this
    # attempt's. Clearing one of two poisons is clearing neither.
    cleared = {key: state.pop(key, None) for key in (
        "build_error", "build_failed_commit", "build_error_detail",
        "comparison_refused_commit", "comparison_refusal_reason", "staging",
    )}
    if any(value is not None for value in cleared.values()):
        write_json(path, state)
    return cleared


def require_accepted(response: tuple[int, object], phase: str) -> None:
    """A refused POST means there is no job; polling for one hangs for 15 minutes.

    -Mode Closed did exactly that after the public-ZIP install left no cached
    candidate: the stage request came back 409 and the phase then waited for a
    terminal state that could never arrive.
    """
    if response[0] != 202:
        raise NotExercised(
            f"{phase}: /api/update/stage was refused ({response[0]} {response[1]!r}); "
            "there is no candidate to stage, so nothing was built"
        )


def poll_stage_until_terminal() -> list[tuple[int, object]]:
    """Wait for THIS attempt to finish, not for a leftover verdict to be seen.

    Every staging phase clears the verdict keys first, so any refusal or error
    observed here belongs to the attempt just started.  Round 3's version would
    have returned immediately on a refusal cached by an earlier phase.
    """
    samples = []
    deadline = time.time() + 900
    while time.time() < deadline:
        sample = api("GET", "/api/update")
        samples.append(sample)
        payload = sample[1]
        if not payload.get("staging") and (
            payload.get("pending_staged")
            or payload.get("comparison_refused")
            or payload.get("last_error")
        ):
            return samples
        time.sleep(0.25)
    raise NotExercised("staging did not reach a terminal cached state in 15 minutes")


def direct(out: Path, root: Path) -> int:
    """Stage through a direct executable and leave its pending slot intact."""
    cleared_build_failure = clear_staging_verdict(root)
    before = state_snapshot(root)
    first = api("POST", "/api/update/stage")
    require_accepted(first, "direct executable")
    samples = poll_stage_until_terminal()
    after = state_snapshot(root)
    status, payload = api("GET", "/api/update")
    data = {
        "cleared_build_failure": cleared_build_failure,
        "before": before,
        "after": after,
        "first_stage": first,
        "samples": samples,
        "final_status": status,
        "final_update": payload,
        "processes": process_command_lines(),
    }
    save_phase(out, "direct", data)
    return 0


def slot_comparison(root: Path, out: Path, active: str) -> dict:
    """Invoke the exact comparison mechanism and retain isolated import proof."""
    inactive = "b" if active == "a" else "a"
    active_exe = root / f"env-{active}" / "Scripts" / "microclaw.exe"
    candidate_exe = root / f"env-{inactive}" / "Scripts" / "microclaw.exe"
    shared = Path(os.environ["APPDATA"]) / "microclaw" / "safety_config.yaml"
    isolated = {}
    for slot in (active, inactive):
        python = root / f"env-{slot}" / "Scripts" / "python.exe"
        completed = run(
            [str(python), "-I", "-c", "import microclaw; print(microclaw.__file__)"],
            cwd=out,
        )
        isolated[slot] = {
            "returncode": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
        }
    active_class = config.classify_config_with_slot(active_exe, shared)
    candidate_class = config.classify_config_with_slot(candidate_exe, shared)
    comparison = config.compare_slot_configurations(
        active_exe, candidate_exe, shared
    )
    return {
        "active": active_class,
        "candidate": candidate_class,
        "proceed": comparison.proceed,
        "reason": comparison.reason,
        "isolated": isolated,
    }


def stage(out: Path, root: Path) -> int:
    """Stage successfully from a launcher-owned server and record locked files."""
    cleared_build_failure = clear_staging_verdict(root)
    before = state_snapshot(root)
    first = api("POST", "/api/update/stage")
    second = api("POST", "/api/update/stage")
    samples = poll_stage_until_terminal()
    after = state_snapshot(root)
    comparison = slot_comparison(root, out, before["active"])
    save_phase(out, "stage", {
        "cleared_build_failure": cleared_build_failure,
        "before": before,
        "after": after,
        "first_stage": first,
        "second_stage": second,
        "samples": samples,
        "comparison": comparison,
    })
    return 0


def notready(out: Path, root: Path) -> int:
    """Run production staging with a gate stub reporting incompatible config.

    The fake uv executable only supplies the already-built inactive slot and
    swaps its CLI for a deterministic `blocked` classifier. The production
    `stage_inactive_slot` function performs the actual comparison and writes the
    refusal. The original executable and marker are restored in `finally`.
    """
    cleared_build_failure = clear_staging_verdict(root)
    before = state_snapshot(root)
    state = before["state"]
    success = state.get("last_success") or {}
    raw_candidate = success.get("candidate") or {}
    try:
        candidate = updates.Candidate(**raw_candidate)
    except TypeError as exc:
        raise NotExercised(f"no cached candidate for NotReady: {raw_candidate!r}") from exc

    inactive = before["inactive"]
    inactive_exe = root / f"env-{inactive}" / "Scripts" / "microclaw.exe"
    inactive_marker = root / f"env-{inactive}" / "microclaw-slot.json"
    exe_backup = out / "notready-microclaw.exe.before"
    marker_backup = out / "notready-slot.json.before"
    shutil.copy2(inactive_exe, exe_backup)
    shutil.copy2(inactive_marker, marker_backup)

    fixture = out / "notready-fixture"
    (fixture / "scripts").mkdir(parents=True, exist_ok=True)
    (fixture / "scripts" / updates.LAUNCHER_PROTOCOL_NAME).write_text(
        "1\n", encoding="ascii"
    )
    classifier = out / "notready-classifier.exe"
    source = (
        "using System; public class GateClassifier { public static void Main() "
        "{ Console.WriteLine(\"{\\\"classification\\\":\\\"blocked\\\"}\"); } }"
    )
    compiled = run([
        "powershell",
        "-NoProfile",
        "-Command",
        "Add-Type -OutputType ConsoleApplication -OutputAssembly "
        f"'{classifier}' -TypeDefinition '{source}'",
    ], cwd=out)
    if compiled.returncode:
        raise NotExercised(
            f"could not compile incompatible classifier: {compiled.stderr!r}"
        )
    fake_uv = out / "notready-uv.cmd"
    fake_uv.write_text(
        "@echo off\r\n"
        f'copy /Y "{classifier}" "{inactive_exe}" >nul\r\n'
        "exit /b 0\r\n",
        encoding="ascii",
    )
    refusal = None
    try:
        updates.stage_inactive_slot(
            root,
            fixture,
            candidate,
            uv_executable=fake_uv,
            config_path=Path(os.environ["APPDATA"]) / "microclaw" / "safety_config.yaml",
        )
    except updates.UpdateError as exc:
        refusal = str(exc)
    finally:
        shutil.copy2(exe_backup, inactive_exe)
        shutil.copy2(marker_backup, inactive_marker)
        (root / "pending-slot.txt").unlink(missing_ok=True)
    status_code, payload = api("GET", "/api/update")
    after = state_snapshot(root)
    # Capture the refusal, then take it back out of production state.  This
    # phase exists to *cause* a refusal; leaving it behind made the next phase's
    # banner read "needs the maintainer" and silenced that phase's real failure.
    # A phase cleans up the state it deliberately breaks.
    left_behind = clear_staging_verdict(root)
    save_phase(out, "notready", {
        "cleaned_up_after_itself": left_behind,
        "cleared_build_failure": cleared_build_failure,
        "before": before,
        "after": after,
        "refusal": refusal,
        "status_code": status_code,
        "update": payload,
        "restored_exe_hash": sha256(inactive_exe),
        "restored_marker_hash": sha256(inactive_marker),
    })
    return 0


def restart(out: Path, root: Path) -> int:
    """Observe one Restart-now request through the second healthy process."""
    before = state_snapshot(root)
    started = time.time()
    print("Click Restart now in the browser. Waiting for a new launch line.")
    deadline = started + 180
    while time.time() < deadline:
        if len(launch_lines(root)) >= len(before["launcher_lines"]) + 1:
            break
        time.sleep(0.25)
    # The launcher writes its log line BEFORE it starts the child, so snapshotting
    # here catches the health marker still absent -- which is what made rounds 5,
    # 6 and 7 all report an empty `health` and blame the product. Wait for the
    # marker to carry the new launch's own nonce.
    new_lines = launch_lines(root)[len(before["launcher_lines"]):]
    nonce = new_lines[0].rsplit("nonce=", 1)[-1].strip() if new_lines else None
    health_deadline = time.time() + 120
    while nonce and time.time() < health_deadline:
        marker = root / "launch-health.txt"
        if marker.is_file() and marker.read_text(encoding="ascii").strip() == nonce:
            break
        time.sleep(0.25)
    health_wait = time.time() - started
    after = state_snapshot(root)
    processes = process_command_lines()
    running_marker = None
    for process in processes:
        executable = process.get("ExecutablePath")
        if executable and "env-" in executable.lower():
            marker = Path(executable).parent.parent / "microclaw-slot.json"
            if marker.exists():
                running_marker = read_json(marker)
    save_phase(out, "restart", {
        "awaited_nonce": nonce,
        "health_wait_s": health_wait,
        "before": before,
        "after": after,
        "elapsed_s": time.time() - started,
        "processes": processes,
        "running_marker": running_marker,
    })
    return 0


def ordinary(out: Path, root: Path) -> int:
    """Prove the ordinary exit child stays alive until Enter, then exits."""
    print("Ctrl-C the desktop child. Wait for its Press Enter prompt, then press Enter here.")
    input()
    while_prompt = process_command_lines()
    started = time.time()
    print("Now press Enter in the child console, then press Enter here.")
    input()
    after_enter = process_command_lines()
    save_phase(out, "ordinary", {
        "while_prompt": while_prompt,
        "after_enter": after_enter,
        "elapsed_s": time.time() - started,
    })
    return 0


def rollback(out: Path, root: Path) -> int:
    """Distinguish deferred rollback reporting from a report never written."""
    before = state_snapshot(root)
    print("Launch the deliberately broken pending slot; press Enter after it exits.")
    input()
    after_failure = state_snapshot(root)
    hidden = list(root.glob("env-*/Lib/site-packages/microclaw.58e-gate-hidden"))
    if len(hidden) != 1:
        raise NotExercised(f"expected one hidden package to restore, found {hidden!r}")
    package = hidden[0].with_name("microclaw")
    hidden[0].replace(package)
    print("Package restored. Launch once; press Enter after the report appears.")
    input()
    after_success = state_snapshot(root)
    save_phase(out, "rollback", {
        "before": before,
        "after_failure": after_failure,
        "after_success": after_success,
    })
    return 0


def failure(out: Path, root: Path) -> int:
    """Stage under the PowerShell-owned unreachable index and cache failure."""
    cleared_build_failure = clear_staging_verdict(root)
    deadline = time.time() + 30
    while True:
        try:
            api("GET", "/api/update")
            break
        except NotExercised:
            if time.time() >= deadline:
                raise
            time.sleep(0.25)
    before = state_snapshot(root)
    response = api("POST", "/api/update/stage")
    samples = poll_stage_until_terminal()
    after = state_snapshot(root)
    save_phase(out, "failure", {
        "cleared_build_failure": cleared_build_failure,
        "before": before,
        "after": after,
        "stage_response": response,
        "samples": samples,
    })
    return 0


def offline(out: Path, root: Path) -> int:
    """Record a network-failed due check and nonce health on one offline launch."""
    original = read_json(root / "update-state.json")
    arranged = dict(original)
    # Only the two fields that make this launch's check due. Provenance stays
    # the clone: rewriting the update channel is not something a gate that can
    # brick the install should do, and the clone is the channel this machine runs.
    arranged.pop("last_attempt", None)
    arranged.pop("next_check", None)
    write_json(root / "update-state.json", arranged)
    before = state_snapshot(root)
    print("Disconnect the network, launch the desktop icon once, then press Enter here.")
    input()
    after = state_snapshot(root)
    write_json(root / "update-state.json", original)
    save_phase(out, "offline", {"before": before, "after": after})
    return 0


def closed(out: Path, root: Path) -> int:
    """Record exactly one launch with Micro-Manager closed and no relaunch."""
    cleared_build_failure = clear_staging_verdict(root)
    response = api("POST", "/api/update/stage")
    require_accepted(response, "Micro-Manager closed")
    samples = poll_stage_until_terminal()
    initial = state_snapshot(root)
    print("Staged. Stop the current server, then launch the desktop icon once.")
    deadline = time.time() + 60
    while time.time() < deadline:
        if len(launch_lines(root)) == len(initial["launcher_lines"]) + 1:
            break
        time.sleep(0.25)
    during = state_snapshot(root)
    print("After the bridge refusal, press Enter in its console and then here.")
    input()
    after = state_snapshot(root)
    save_phase(out, "closed", {
        "cleared_build_failure": cleared_build_failure,
        "stage_response": response,
        "samples": samples,
        "initial": initial,
        "during": during,
        "after": after,
    })
    return 0


def later(out: Path, root: Path) -> int:
    """Prove a pending selector activates only on the next desktop launch."""
    cleared_build_failure = clear_staging_verdict(root)
    response = api("POST", "/api/update/stage")
    require_accepted(response, "Restart later")
    samples = poll_stage_until_terminal()
    before = state_snapshot(root)
    if before["pending"] is None:
        raise NotExercised("Restart later requires a genuinely staged pending slot")
    print("Click Restart later, stop the server, and launch the icon once; press Enter when healthy.")
    input()
    after = state_snapshot(root)
    save_phase(out, "later", {
        "cleared_build_failure": cleared_build_failure,
        "stage_response": response,
        "samples": samples,
        "before": before,
        "after": after,
    })
    return 0


def restore(out: Path, root: Path) -> int:
    """Restore original shared state, branch selector, and remove pending state."""
    original_path = out / "original-state.json"
    if not original_path.exists():
        raise NotExercised("original-state.json is absent; run Prepare")
    original = read_json(original_path)
    prepared = need(out, "prepare")
    write_json(root / "update-state.json", original)
    (root / "active-slot.txt").write_text(
        prepared["branch_active"] + "\n", encoding="ascii"
    )
    (root / "pending-slot.txt").unlink(missing_ok=True)
    save_phase(out, "restore", state_snapshot(root))
    return 0


def publiczip(out: Path, root: Path) -> int:
    """Capture the fresh public ZIP provenance before clone reinstall."""
    save_phase(out, "publiczip", state_snapshot(root))
    return 0
def verify(out: Path, root: Path) -> int:
    """Score every artifact independently and print exact missing commands."""
    phases_path = out / "phases.json"
    phases = read_json(phases_path) if phases_path.exists() else {}
    missing = [name for name in PHASE_COMMANDS if name not in phases]
    if missing:
        print("PREFLIGHT — exact commands still owed:")
        for name in missing:
            print(f"  {PHASE_COMMANDS[name]}")

    @limb(
        "direct executable stages and offers Restart later only",
        fails_if="pending is not staged, automatic_restart is true, or process is launcher-owned",
    )
    def _direct():
        data = need(out, "direct")
        final = data["final_update"]
        if final.get("pending_staged") is not True:
            raise AssertionError(f"pending_staged={final.get('pending_staged')!r}")
        if final.get("automatic_restart") is not False:
            raise AssertionError(
                f"automatic_restart={final.get('automatic_restart')!r} with pending staged"
            )
        commands = [str(item.get("CommandLine", "")) for item in data["processes"]]
        if not any("env-" in command.lower() for command in commands):
            raise AssertionError(f"no direct slot process in {commands!r}")
        if any("updater-launcher" in command.lower() for command in commands):
            raise AssertionError(f"launcher-owned process found in {commands!r}")
        return "pending_staged=true and automatic_restart=false on direct slot process"

    @limb(
        "one-job refusal during a real build",
        fails_if="first request is not 202 or second is not 409 while staging=true",
    )
    def _one_job():
        data = need(out, "stage")
        if data["first_stage"][0] != 202:
            raise AssertionError(f"first stage response={data['first_stage']!r}")
        if data["second_stage"][0] != 409:
            raise AssertionError(f"second stage response={data['second_stage']!r}")
        running = [sample for _, sample in data["samples"] if sample.get("staging")]
        if not running:
            raise AssertionError(f"no staging=true sample: {data['samples']!r}")
        return f"second response 409; {len(running)} staging=true samples"

    @limb(
        "progress and restart cached banner states",
        fails_if="the samples do not contain staging=true followed by pending_staged=true",
    )
    def _progress():
        samples = [sample for _, sample in need(out, "stage")["samples"]]
        building = next((index for index, item in enumerate(samples) if item.get("staging")), None)
        ready = next((index for index, item in enumerate(samples) if item.get("pending_staged")), None)
        if building is None:
            raise AssertionError(f"no staging=true sample: {samples!r}")
        if ready is None:
            raise AssertionError(f"no pending_staged=true sample: {samples!r}")
        if ready < building:
            raise AssertionError(f"ready index {ready} precedes building index {building}")
        return f"building sample {building}; ready sample {ready}"

    @limb(
        "not-ready comparison cached banner state "
        "(candidate CLI = gate stub, not a second code version)",
        fails_if="the separate incompatible-config attempt lacks refusal and reason",
    )
    def _not_ready():
        data = need(out, "notready")
        refused = data["update"]
        if refused.get("comparison_refused") is not True:
            raise AssertionError(f"comparison_refused={refused!r}")
        reason = refused.get("comparison_refusal_reason")
        if not reason:
            raise AssertionError(f"comparison refusal has no reason: {refused!r}")
        if refused.get("pending_staged"):
            raise AssertionError(f"refused comparison published pending: {refused!r}")
        if data["restored_exe_hash"] != data["before"]["inactive_exe_hash"]:
            raise AssertionError(
                f"inactive executable not restored: {data['restored_exe_hash']!r} != "
                f"{data['before']['inactive_exe_hash']!r}"
            )
        return f"{reason} (candidate CLI was a gate stub, not a second code version)"

    @limb(
        "real staging compares two real slot CLIs over one shared config",
        fails_if="isolated imports fail, classifications differ unexpectedly, or comparison refuses",
    )
    def _comparison():
        comparison = need(out, "stage")["comparison"]
        isolated = comparison["isolated"]
        for slot, result in isolated.items():
            if result["returncode"] != 0:
                raise AssertionError(f"slot {slot} isolated import={result!r}")
        if comparison["proceed"] is not True:
            raise AssertionError(f"comparison={comparison!r}")
        return json.dumps(comparison, sort_keys=True)

    @limb(
        "active locked files are byte-identical and only inactive slot changes",
        fails_if="active executable/marker changes or inactive executable/marker does not change",
    )
    def _locked():
        data = need(out, "stage")
        before = data["before"]
        after = data["after"]
        for field in ("active_exe_hash", "active_marker_hash"):
            if before[field] != after[field]:
                raise AssertionError(
                    f"{field} changed: {before[field]!r} -> {after[field]!r}"
                )
        # NOT a content comparison: staging the same commit twice produces a
        # byte-identical console script and marker, so identical bytes are the
        # expected result of a real rebuild, not evidence against one. The venv's
        # own timestamp is what proves `uv venv --clear` ran.
        if "inactive_venv_mtime" not in after:
            raise NotExercised(
                "this evidence predates the rebuild timestamp; re-run -Mode Stage"
            )
        rebuilt_at, was_at = after.get("inactive_venv_mtime"), before.get("inactive_venv_mtime")
        if rebuilt_at is None:
            raise AssertionError("the inactive slot has no pyvenv.cfg after staging")
        if was_at is not None and rebuilt_at <= was_at:
            raise AssertionError(
                f"inactive slot was not rebuilt: pyvenv.cfg mtime {was_at} -> {rebuilt_at}"
            )
        return (f"active exe and marker byte-identical; inactive venv rebuilt "
                f"({was_at} -> {rebuilt_at})")

    @limb(
        "Restart now performs one nonce-matched relaunch",
        fails_if="no new launch, health mismatch, request survives, or running marker/commit disagrees",
    )
    def _restart():
        data = need(out, "restart")
        before = data["before"]
        after = data["after"]
        new_lines = after["launcher_lines"][len(before["launcher_lines"]):]
        if len(new_lines) != 1:
            raise AssertionError(f"new launch lines={new_lines!r}")
        if "awaited_nonce" not in data:
            raise NotExercised(
                "this evidence predates waiting for nonce-matched health; "
                "re-run -Mode Restart"
            )
        health = after.get("health")
        if not health:
            raise AssertionError(
                f"no health marker after {data.get('health_wait_s', 0):.1f}s; the "
                f"relaunched slot never reported startup health (launch {new_lines[0]!r})"
            )
        if health != data.get("awaited_nonce"):
            raise AssertionError(
                f"health {health!r} is not this launch's nonce {data.get('awaited_nonce')!r}"
            )
        if after["restart_request_exists"]:
            raise AssertionError("restart-request.txt survives the relaunch")
        installed = after["state"].get("installed_commit")
        marker = data.get("running_marker") or {}
        if marker.get("commit") != installed:
            raise AssertionError(f"running marker={marker!r}; installed={installed!r}")
        return f"one relaunch in {data['elapsed_s']:.2f}s; commit={installed}"

    @limb(
        "ordinary Ctrl-C waits at the exit prompt until Enter",
        fails_if="server child is absent at prompt or still present after Enter",
    )
    def _ordinary():
        data = need(out, "ordinary")
        while_pids = {item.get("ProcessId") for item in data["while_prompt"]}
        after_pids = {item.get("ProcessId") for item in data["after_enter"]}
        if not while_pids:
            raise AssertionError("no server child listed while exit prompt displayed")
        survivors = sorted(pid for pid in while_pids if pid in after_pids)
        if survivors:
            raise AssertionError(f"server children still alive after Enter: {survivors}")
        return f"children {sorted(while_pids)} exited; operator interval {data['elapsed_s']:.2f}s"

    @limb(
        "Restart later activates at the next desktop launch",
        fails_if="no pending selector existed or next launch did not consume and activate it",
    )
    def _later():
        data = need(out, "later")
        before = data["before"]
        after = data["after"]
        if before["pending"] is None:
            raise AssertionError(f"pending before launch={before['pending']!r}")
        if after["active"] != before["pending"]:
            raise AssertionError(
                f"active after={after['active']!r}; pending before={before['pending']!r}"
            )
        if after["pending"] is not None:
            raise AssertionError(f"pending survives next launch: {after['pending']!r}")
        return f"activated pending slot {after['active']} on next launch"

    @limb(
        "offline launch reaches health and caches the attempt",
        fails_if="health is absent/mismatched, last_attempt does not move, or last_success changes",
    )
    def _offline():
        data = need(out, "offline")
        before = data["before"]
        after = data["after"]
        new_lines = after["launcher_lines"][len(before["launcher_lines"]):]
        if len(new_lines) != 1:
            raise AssertionError(f"offline new launch lines={new_lines!r}")
        if after["health"] not in new_lines[0]:
            raise AssertionError(f"offline health={after['health']!r}; line={new_lines[0]!r}")
        before_attempt = before["state"].get("last_attempt")
        after_attempt = after["state"].get("last_attempt")
        if not isinstance(after_attempt, (int, float)) or after_attempt == before_attempt:
            raise AssertionError(
                f"last_attempt did not move: {before_attempt!r} -> {after_attempt!r}"
            )
        if after["state"].get("last_success") != before["state"].get("last_success"):
            raise AssertionError("last_success changed during offline failure")
        return (f"health matched; last_attempt {before_attempt!r} -> {after_attempt!r}; "
                f"last_error={after['state'].get('last_error')!r}")

    @limb(
        "Micro-Manager closed keeps the slot without rollback or relaunch",
        fails_if="not exactly one new launch or active selector changes back",
    )
    def _closed():
        data = need(out, "closed")
        initial = data["initial"]
        during = data["during"]
        after = data["after"]
        new_lines = after["launcher_lines"][len(initial["launcher_lines"]):]
        if len(new_lines) != 1:
            raise AssertionError(f"closed-rig new launch lines={new_lines!r}")
        if after["active"] != during["active"]:
            raise AssertionError(
                f"active changed after child start: {during['active']!r} -> "
                f"{after['active']!r}"
            )
        if after["health"] not in new_lines[0]:
            raise AssertionError(f"health={after['health']!r}; launch={new_lines[0]!r}")
        return f"one healthy launch; active remained {after['active']}"

    @limb(
        "failed start reports rollback only on the next healthy launch",
        fails_if="rollback-reported appears after failure, is absent after success, or selector is not restored",
    )
    def _rollback():
        data = need(out, "rollback")
        before = data["before"]
        failed = data["after_failure"]
        success = data["after_success"]
        failed_added = failed["launcher_log"][len(before["launcher_log"]):]
        success_added = success["launcher_log"][len(failed["launcher_log"]):]
        if any("rollback-reported=" in line for line in failed_added):
            raise AssertionError(f"rollback reported on failing launch: {failed_added!r}")
        reports = [line for line in success_added if "rollback-reported=" in line]
        if len(reports) != 1:
            raise AssertionError(f"next-launch rollback report lines={reports!r}")
        if success["active"] != before["active"]:
            raise AssertionError(
                f"selector not restored: before={before['active']!r}, after={success['active']!r}"
            )
        return reports[0]

    @limb(
        "unreachable PyPI failure is cached without selector change",
        fails_if="build error or its uv diagnostic absent, pending published, active changed, or retry deadline moved",
    )
    def _failure():
        data = need(out, "failure")
        before = data["before"]
        after = data["after"]
        if not after["state"].get("build_error"):
            raise AssertionError(f"build_error={after['state'].get('build_error')!r}")
        # The diagnostic, not the user-facing sentence. Round 1 recorded only
        # "the update could not be built" and cost a second trip to learn that
        # `uv venv` had refused an existing slot.
        # `build_error_detail` ships in this block, so it exists only when the
        # slot that ran the staging job is the branch build. After the Restart
        # phase the active slot is the *staged* commit, which predates it --
        # round 7 scored that as a product failure when it was a phase-ordering
        # one. State the precondition instead of asserting through it.
        detail = after["state"].get("build_error_detail")
        prepared = need(out, "prepare")
        if "active_slot_commit" not in after:
            raise NotExercised(
                "this evidence does not record which slot ran the staging job; "
                "re-run -Mode Failure from the branch slot"
            )
        ran_branch_build = after.get("active_slot_commit") == prepared.get("active_slot_commit")
        if not detail and not ran_branch_build:
            raise NotExercised(
                f"the staging job ran in slot commit {after.get('active_slot_commit')!r}, "
                f"not the branch build {prepared.get('active_slot_commit')!r}; "
                "return to the branch slot before this phase"
            )
        if not detail or not str(detail).startswith("uv "):
            raise AssertionError(f"build_error_detail={detail!r}")
        if after["pending"] is not None:
            raise AssertionError(f"pending published after failed build: {after['pending']!r}")
        if after["active"] != before["active"]:
            raise AssertionError(
                f"active changed: {before['active']!r} -> {after['active']!r}"
            )
        if after["state"].get("next_check") != before["state"].get("next_check"):
            raise AssertionError(
                f"next_check moved: {before['state'].get('next_check')!r} -> "
                f"{after['state'].get('next_check')!r}"
            )
        return f"{after['state']['build_error']} — {after['state']['build_error_detail']}"

    @limb(
        "Restore returns production state and APPDATA hash",
        fails_if="commit, selector, pending state, or roaming hash differs",
    )
    def _restore():
        prepared = need(out, "prepare")
        restored = need(out, "restore")
        original = read_json(out / "original-state.json")
        expected = original.get("installed_commit")
        actual = restored["state"].get("installed_commit")
        if actual != expected:
            raise AssertionError(f"installed_commit={actual!r}; expected={expected!r}")
        if restored["active"] != prepared["branch_active"]:
            raise AssertionError(
                f"active={restored['active']!r}; expected={prepared['branch_active']!r}"
            )
        if restored["pending"] is not None:
            raise AssertionError(f"pending survives Restore: {restored['pending']!r}")
        if restored["appdata_hash"] != prepared["appdata_hash"]:
            raise AssertionError(
                f"APPDATA hash changed: {prepared['appdata_hash']} -> "
                f"{restored['appdata_hash']}"
            )
        return "installed_commit, selector, pending state, and APPDATA restored"

    @limb(
        "public ZIP records public-head unknown and cached private 404",
        fails_if="any of provenance, installed commit, or cached error differs",
    )
    def _public_zip():
        state = need(out, "publiczip")["state"]
        if state.get("provenance") != "public-head":
            raise AssertionError(f"provenance={state.get('provenance')!r}")
        if state.get("installed_commit") != "unknown":
            raise AssertionError(f"installed_commit={state.get('installed_commit')!r}")
        expected = "repository is not public (404)"
        if state.get("last_error") is None and state.get("last_attempt") is None:
            raise NotExercised(
                "this install has never run its update check, so nothing could be "
                "cached; start Microclaw once from the icon and re-run -Mode PublicZip"
            )
        if state.get("last_error") != expected:
            raise AssertionError(f"last_error={state.get('last_error')!r}")
        return "public-head, unknown, repository is not public (404)"

    write_json(out / "results.json", RESULTS)
    for result in RESULTS:
        print(
            f"{result['status']}: {result['name']} — {result['detail']}; "
            f"FAILS IF: {result['fails_if']}"
        )
    failed = any(item["status"] == "FAIL" for item in RESULTS)
    incomplete = any(item["status"] == "NOT EXERCISED" for item in RESULTS)
    banner = "FAILED" if failed else "INCOMPLETE" if incomplete else "PASSED"
    print(f"BLOCK 58e DEMO GATE {banner}")
    return 0 if banner == "PASSED" else 1
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True, choices=MODES)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    repo = args.repo.resolve()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    root = Path(os.environ["LOCALAPPDATA"]) / "microclaw"
    tee = Tee(sys.__stdout__, out / "gate.txt")
    sys.stdout = tee
    sys.stderr = tee

    functions = {
        "prepare": lambda: prepare(repo, out, root),
        "direct": lambda: direct(out, root),
        "stage": lambda: stage(out, root),
        "notready": lambda: notready(out, root),
        "restart": lambda: restart(out, root),
        "later": lambda: later(out, root),
        "ordinary": lambda: ordinary(out, root),
        "rollback": lambda: rollback(out, root),
        "failure": lambda: failure(out, root),
        "offline": lambda: offline(out, root),
        "closed": lambda: closed(out, root),
        "restore": lambda: restore(out, root),
        "publiczip": lambda: publiczip(out, root),
        "verify": lambda: verify(out, root),
    }
    try:
        return functions[args.mode]()
    except NotExercised as exc:
        print(f"NOT EXERCISED: {args.mode} — {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
