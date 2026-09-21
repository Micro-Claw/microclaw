"""71b/71c: observe the real desktop app; never host an ASGI server.

Files/argv follow updates.py, install.bat and the executed 58e/71a gates.
Only --selftest substitutes uv, slot interpreters, API and operator actions.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.request

REPO = Path(__file__).resolve().parents[1]
ACTIVE = "active-slot.txt"
PENDING = "pending-slot.txt"
MARKER = "microclaw-slot.json"  # updates.SLOT_NAME (not SLOT_MARKER_NAME)
STATE = "update-state.json"
RECORD = "extensions.json"
HEALTH = "launch-health.txt"
PHASES = ("prepare", "installed", "staged", "restarted", "reinstalled", "recovery", "restore", "verify")
LINE = ("The `ilastik` extension is not installed; tools using it will refuse until "
        "the user installs it from Microclaw's Extensions panel.\n")

# File payload avoids .pth banners corrupting JSON stdout. -I keeps the checkout
# off sys.path: every real probe imports the SLOT'S installed microclaw.
PROBE = r'''
import importlib, json, sys, traceback
from pathlib import Path
out, fixture = map(Path, sys.argv[1:3])
result = {"interpreter": sys.executable}
try:
    importlib.import_module("h5py")
    result["h5py"] = "ready"
except ModuleNotFoundError as exc:
    result["h5py"] = "absent" if exc.name == "h5py" else "broken"
    result["import_error"] = str(exc)
except Exception:
    result["h5py"] = "broken"
    result["import_error"] = traceback.format_exc()
try:
    import microclaw
    from microclaw import extensions
    result["microclaw"] = microclaw.__file__
    result["ready"] = extensions.ready("ilastik")
except Exception:
    result["readiness_error"] = traceback.format_exc()
if fixture.is_dir():
    try:
        from microclaw import skills
        original_catalog = skills.SKILL_CATALOG
        try:
            skills.SKILL_CATALOG = skills._build_catalog(fixture)
            skill = skills.SKILL_CATALOG[0]
            result["skill_original"] = skill.resource.read_text(encoding="utf-8")
            result["skill_text"] = skills.load_skill_text(skill.name)
        finally:
            skills.SKILL_CATALOG = original_catalog
    except Exception:
        result["skill_error"] = traceback.format_exc()
out.write_text(json.dumps(result), encoding="utf-8")
'''


class NotExercised(RuntimeError):
    pass


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def selector(root, name=ACTIVE):
    path = root / name
    if not path.exists():
        return None
    value = path.read_text(encoding="ascii").strip()
    if value not in ("a", "b"):
        raise ValueError(f"Invalid {name}: {value!r}")
    return value


def other(slot):
    if slot not in ("a", "b"):
        raise ValueError("No valid active slot")
    return "b" if slot == "a" else "a"


def run(argv, **kwargs):
    return subprocess.run([str(a) for a in argv], stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=120, check=False, **kwargs)


def launch_lines(root):
    path = root / "launcher.log"
    return [line for line in path.read_text(encoding="utf-8-sig").splitlines()
            if " slot=" in line and " nonce=" in line] if path.exists() else []


def clear_staging_verdict(root):
    state = read_json(root / STATE)
    cleared = {key: state.pop(key, None) for key in (
        "build_error", "build_failed_commit", "build_error_detail",
        "comparison_refused_commit", "comparison_refusal_reason", "staging")}
    write_json(root / STATE, state)
    return cleared


class Gate:
    def __init__(self, root, out, repo=REPO):
        self.root, self.out, self.repo = root.resolve(), out.resolve(), repo.resolve()
        self.out.mkdir(parents=True, exist_ok=True)
        self.fixture = self.out / "fixture-skills"

    def say(self, text):
        print(text, flush=True)
        with (self.out / "gate.log").open("a", encoding="utf-8") as log:
            log.write(text + "\n")

    def save_phase(self, name, data):
        write_json(self.out / f"{name}.json", data)
        path = self.out / "phases.json"
        phases = read_json(path) if path.exists() else {}
        phases[name] = time.time()
        write_json(path, phases)
        self.say(f"RECORDED: {name} — {self.out / (name + '.json')}")

    def need(self, phase):
        path = self.out / f"{phase}.json"
        if not path.exists():
            raise NotExercised(f"{phase} was not captured")
        value = read_json(path)
        return value

    def clean_artifacts(self):
        """Remove our launcher-root artifacts, preserving restoration snapshots locally."""
        pointer = self.root / "71b-gate-evidence.txt"
        journal_path = self.out / "recovery-journal.json"
        removed = []
        journal = read_json(journal_path) if journal_path.exists() else None
        backup = Path(journal.get("launcher_backup", journal["backup"])) if journal else None
        self.say(f"GATE CLEANUP: recovery files={backup}; evidence pointer={pointer} (removed last)")
        if backup and backup.parent == self.root:
            # Verify may follow a failed recovery. Preserve the restore input
            # outside production state before removing its launcher-root copy.
            archive = self.root.parent / backup.name
            if backup.exists():
                shutil.copytree(backup, archive, dirs_exist_ok=True)
                if (archive / "files.json").read_bytes() != (backup / "files.json").read_bytes():
                    raise RuntimeError(f"Recovery backup copy mismatch: {archive}")
                journal.update(backup=str(archive), launcher_backup=str(backup))
                write_json(journal_path, journal)
                shutil.rmtree(backup)
                removed.append(str(backup))
            self.say(f"Recovery snapshots retained outside launcher root: {archive}")
        if pointer.exists():
            recorded = Path(pointer.read_text(encoding="ascii").strip()).resolve()
            if recorded != self.out:
                raise RuntimeError(f"Pointer belongs to another evidence folder: {recorded}; current={self.out}")
            pointer.unlink()
            removed.append(str(pointer))
        write_json(self.out / "gate-cleanup.json", {"removed": removed,
                   "pointer": str(pointer), "recovery_files": str(backup) if backup else None})
        self.say(f"GATE CLEANUP COMPLETE: removed {removed}")

    def prompt(self, event, message):
        self.say(message)
        if event == "missing":
            return input("Type the exact button label beside ilastik (or STOP to abort): ")
        if input("Type DONE after completing this, or STOP to abort: ").strip() != "DONE":
            raise NotExercised(f"operator stopped at {event}")

    def api(self):
        try:
            with urllib.request.urlopen("http://127.0.0.1:8000/api/extensions", timeout=10) as response:
                return {"status": response.status, "body": json.load(response)}
        except (OSError, ValueError, urllib.error.URLError) as exc:
            return {"error": str(exc)}

    def processes(self):
        # Match only this managed root's executables and external launcher.
        command = ("Get-CimInstance Win32_Process | Select-Object ProcessId,"
                   "ExecutablePath,CommandLine | ConvertTo-Json -Compress")
        result = run(["powershell", "-NoProfile", "-Command", command])
        if result.returncode:
            raise NotExercised("Cannot enumerate Windows processes: " + result.stderr)
        rows = json.loads(result.stdout or "[]")
        rows = rows if isinstance(rows, list) else [rows]
        prefix = str(self.root).casefold() + os.sep
        return [row for row in rows if row["ProcessId"] != os.getpid() and (
            ((row.get("ExecutablePath") or "").casefold().startswith(prefix)
             and "serve" in (row.get("CommandLine") or "").casefold())
            or str(self.root / "updater-launcher.ps1").casefold()
            in (row.get("CommandLine") or "").casefold())]

    def require_stopped(self):
        if self.processes() or self.api().get("status") == 200:
            raise NotExercised("Close the Microclaw console AND its launcher before continuing")

    def stop_servers(self):
        rows = self.processes()
        # Terminate launcher trees before any standalone child so none can restart.
        rows.sort(key=lambda r: "updater-launcher.ps1" not in (r.get("CommandLine") or "").lower())
        for row in rows:
            run(["taskkill", "/PID", str(row["ProcessId"]), "/T", "/F"])
        deadline = time.monotonic() + 20
        while self.processes() and time.monotonic() < deadline:
            time.sleep(.2)
        self.require_stopped()
        return rows

    def launch(self):
        # The real external launcher, never a gate-hosted server.
        subprocess.Popen(["cmd.exe", "/c", str(self.root / "Microclaw.cmd")],
                         cwd=self.root, creationflags=subprocess.CREATE_NEW_CONSOLE)

    def slot_command(self, slot, payload):
        return [str(self.root / f"env-{slot}" / "Scripts" / "python.exe"),
                "-I", "-c", PROBE, str(payload), str(self.fixture)]

    def probe(self, slot):
        env = self.root / f"env-{slot}"
        if not env.exists():
            return {"environment_absent": True, "h5py": "absent"}
        payload = self.out / f"probe-{slot}-{time.time_ns()}.json"
        try:
            result = run(self.slot_command(slot, payload))
            data = read_json(payload) if payload.exists() else {}
            return {**data, "exit": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
            return {"probe_error": str(exc)}

    def snapshot(self):
        data = {"at": time.time(), "active": selector(self.root), "pending": selector(self.root, PENDING),
                "launch_lines": launch_lines(self.root), "slots": {}, "panel": self.api()}
        for key, filename in (("state", STATE), ("extensions", RECORD)):
            try:
                data[key] = read_json(self.root / filename) if (self.root / filename).exists() else {}
            except (OSError, ValueError) as exc:
                data[key] = {"read_error": str(exc)}
        for slot in ("a", "b"):
            marker = self.root / f"env-{slot}" / MARKER
            cfg = marker.parent / "pyvenv.cfg"
            data["slots"][slot] = {"probe": self.probe(slot),
                                   "marker": read_json(marker) if marker.exists() else None,
                                   "venv_mtime_ns": cfg.stat().st_mtime_ns if cfg.exists() else None}
        path = self.root / HEALTH
        data["health"] = path.read_text(encoding="ascii").strip() if path.exists() else None
        return data

    def backup_files(self, destination):
        destination.mkdir(parents=True, exist_ok=False)
        paths = [item for item in self.root.iterdir() if item.is_file()]
        paths += [self.root / f"env-{s}" / MARKER for s in ("a", "b")]
        saved = {}
        for path in paths:
            if path.is_file():
                relative = str(path.relative_to(self.root))
                saved[relative] = base64.b64encode(path.read_bytes()).decode("ascii")
        write_json(destination / "files.json", saved)
        return saved

    def restore_files(self, saved):
        # Final verify removes our pointer. A later recovery retry must not
        # resurrect it from the pre-recovery snapshot.
        saved = {name: data for name, data in saved.items() if name != "71b-gate-evidence.txt"}
        # Installer can create these even if absent before recovery.
        names = set(saved) | {ACTIVE, PENDING, STATE, RECORD, HEALTH, "restart-request.txt",
                             "rollback-report.txt", "launcher.log", "launcher-protocol.txt",
                             "Microclaw.cmd", "updater-launcher.ps1"}
        for name in names:
            path = self.root / name
            if name in saved:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(base64.b64decode(saved[name]))
            else:
                path.unlink(missing_ok=True)
        for name, encoded in saved.items():
            if (self.root / name).read_bytes() != base64.b64decode(encoded):
                raise RuntimeError(f"Restoration mismatch: {name}")

    def wait_health(self, before):
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            new = launch_lines(self.root)[len(before):]
            if new:
                # First new launch must become healthy; a rollback's later nonce
                # must not turn a failed activation into a successful restart.
                match = re.search(r" slot=([ab]) nonce=(\S+)\s*$", new[0])
                path = self.root / HEALTH
                if match and path.exists() and path.read_text(encoding="ascii").strip() == match[2]:
                    return {"slot": match[1], "nonce": match[2], "line": new[0]}
            time.sleep(.25)
        raise NotExercised("No health marker matching the first new launch's nonce within 180 s")

    # Restore is deliberately defined before the destructive test.
    def restore(self):
        journal_path = self.out / "recovery-journal.json"
        journal = read_json(journal_path)
        if journal.get("status") == "cleaned":
            raise NotExercised("Recovery was verified and its preserved environment already deleted")
        if journal.get("status") == "restored":
            return {"already_restored": True, **journal}
        preserved, target = Path(journal["preserved"]), Path(journal["target"])
        if journal.get("status") == "cleanup-started":
            # Deletion can fail part-way. Never roll back into that partial tree.
            require_ready(self.snapshot())
            if preserved.exists():
                shutil.rmtree(preserved)
            journal["status"] = "cleaned"
            write_json(journal_path, journal)
            result = {"status": "cleanup-retried", "deleted": str(preserved)}
            self.save_phase("restore", result)
            return result
        result = {"preserved": str(preserved), "target": str(target)}
        try:
            result["stopped"] = self.stop_servers()
            if preserved.exists():
                journal["status"] = "environment-restoring"
                write_json(journal_path, journal)
                if target.exists():
                    failed = self.root / f"71b-failed-{time.time_ns()}"
                    target.rename(failed)
                    result["failed_replacement"] = str(failed)
                    self.say(f"FAILED REPLACEMENT RETAINED: {failed}")
                preserved.rename(target)
            elif journal.get("status") not in ("backed-up", "environment-restoring", "environment-restored"):
                raise RuntimeError(f"Preserved environment missing: {preserved}")
            journal["status"] = "environment-restored"
            write_json(journal_path, journal)
            self.restore_files(read_json(Path(journal["backup"]) / "files.json"))
            result["files_restored"] = True
            result["probe"] = self.probe(journal["slot"])
            if result["probe"].get("h5py") != "ready":
                raise RuntimeError("Restored interpreter does not import h5py")
            before = launch_lines(self.root)
            self.launch()
            result["health"] = self.wait_health(before)
            if result["health"]["slot"] != journal["slot"]:
                raise RuntimeError("Restored launcher started the wrong slot")
            journal["status"] = "restored"
            write_json(journal_path, journal)
            result["status"] = "restored"
        except BaseException as exc:
            result["restore_error"] = str(exc)
            result["retained_environment"] = str(preserved if preserved.exists() else target)
            self.say(f"RESTORE FAILED: retained {result['retained_environment']}: {exc}")
            self.save_phase("restore", result)
            raise
        self.save_phase("restore", result)
        return result

    def recovery(self):
        self.prompt("stop", "Close Microclaw and its launcher. Leave Micro-Manager running.")
        self.require_stopped()
        slot = selector(self.root)
        target = self.root / f"env-{slot}"
        if Path(sys.executable).resolve().is_relative_to(target.resolve()):
            raise NotExercised("Recovery must run from the OTHER slot's interpreter")
        if (self.out / "recovery-journal.json").exists():
            raise NotExercised("Recovery already attempted here; use restore, or a new evidence folder")
        before = self.snapshot()
        if before["slots"][slot]["probe"].get("h5py") != "ready":
            raise NotExercised("No working extension environment to preserve")
        backup = self.root / f"71b-recovery-files-{time.time_ns()}"
        self.backup_files(backup)
        preserved = self.root / f"71b-preserved-{slot}-{time.time_ns()}"
        journal = {"slot": slot, "target": str(target), "preserved": str(preserved),
                   "backup": str(backup), "status": "backed-up"}
        write_json(self.out / "recovery-journal.json", journal)
        data = {"before": before, "preserved": str(preserved), "backup": str(backup)}
        self.save_phase("recovery", data)
        try:
            target.rename(preserved)
            journal["status"] = "preserved"
            write_json(self.out / "recovery-journal.json", journal)
            self.say(f"PRESERVED: {preserved}")
            self.prompt("rebuild", "In a second PowerShell window run .\\install.bat from this checkout. "
                        "Do NOT launch Microclaw. If the installer asks to press a key for the bridge/setup, "
                        "leave it PAUSED there; type DONE here before pressing any key in that window.")
            self.require_stopped()
            data["rebuilt"] = self.snapshot()
            self.save_phase("recovery", data)
            probe = data["rebuilt"]["slots"][slot]["probe"]
            if probe.get("exit") != 0 or probe.get("h5py") != "absent" or probe.get("ready") is not False:
                raise RuntimeError("Fresh environment did not prove h5py absent before launch")
            data["operator_missing_button"] = self.prompt("missing", "If the installer is paused, cancel it with Ctrl+C (answer Y if asked). "
                        "Now launch the desktop icon. In Firefox open Extensions. "
                        "Read the button label beside ilastik; do NOT press it yet.")
            data["missing"] = self.snapshot()
            self.save_phase("recovery", data)
            row = panel_row(data["missing"])
            if data["operator_missing_button"] != "Reinstall":
                raise RuntimeError(f"Expected Reinstall button; operator typed {data['operator_missing_button']!r}")
            if not row.get("recorded") or row.get("ready"):
                raise RuntimeError("Panel JSON is not recorded-but-missing")
            self.prompt("recover-install", "Press Reinstall for ilastik in Firefox; wait for Ready and no install job.")
            data["recovered"] = self.snapshot()
            self.save_phase("recovery", data)
            require_ready(data["recovered"])
            require_record(data["recovered"])
            if not panel_row(data["recovered"]).get("ready"):
                raise RuntimeError("Recovered panel is not ready")
            journal["status"] = "verified"
            write_json(self.out / "recovery-journal.json", journal)
        except BaseException as exc:
            data["recovery_error"] = f"{type(exc).__name__}: {exc}"
            try:
                data["restoration"] = self.restore()
            except BaseException as restore_error:
                data["restoration_error"] = str(restore_error)
            self.save_phase("recovery", data)
            raise
        # Only a verified replacement may outlive deletion of the original.
        # A cleanup error keeps that verified replacement, never restores a
        # potentially partly deleted directory. Report it as a failed cleanup.
        try:
            journal["status"] = "cleanup-started"
            write_json(self.out / "recovery-journal.json", journal)
            shutil.rmtree(preserved)
            data["cleanup"] = {"deleted": str(preserved), "exists": preserved.exists()}
            journal["status"] = "cleaned"
            write_json(self.out / "recovery-journal.json", journal)
        except OSError as exc:
            self.say(f"CLEANUP FAILED: retained remnants {preserved}; verified replacement remains at {target}: {exc}")
            data["cleanup"] = {"retained": str(preserved), "error": str(exc)}
            self.save_phase("recovery", data)
            raise
        self.save_phase("recovery", data)
        return data

    def revision(self, ref):
        result = run(["git", "rev-parse", ref], cwd=self.repo)
        if result.returncode:
            raise NotExercised(f"Cannot resolve {ref}: {result.stderr}")
        return result.stdout.strip()

    def prepare(self, fresh=False):
        self.require_stopped()
        if (self.out / "prepare.json").exists():
            raise NotExercised("Use a new evidence folder; prepare never overwrites its safety backup")
        if selector(self.root, PENDING):
            raise NotExercised("A pending update exists; resolve it before this gate")
        shutil.copytree(self.repo / "tests" / "fixtures" / "skills", self.fixture)
        backup = self.root.parent / f"71b-safety-{time.time_ns()}"
        self.backup_files(backup)
        roaming = Path(os.environ["APPDATA"]) / "microclaw"
        if roaming.exists():
            shutil.copytree(roaming, backup / "appdata")
        data = {"original": self.snapshot(), "backup": str(backup), "appdata_existed": roaming.exists()}
        self.save_phase("prepare", data)
        # Fresh is explicit operator consent, after the irreplaceable data backup.
        # Never clear an environment just to manufacture this control.
        if fresh:
            uv = shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv.exe")
            data["fresh"] = []
            for slot in ("a", "b"):
                env = self.root / f"env-{slot}"
                if not env.exists():
                    continue
                result = run([uv, "pip", "uninstall", "--python", env / "Scripts" / "python.exe", "h5py"])
                data["fresh"].append({"slot": slot, "exit": result.returncode,
                                      "stdout": result.stdout, "stderr": result.stderr})
                if result.returncode:
                    self.save_phase("prepare", data)
                    raise NotExercised(f"Could not reset h5py in slot {slot}: {result.stderr}")
            path = self.root / RECORD
            if path.exists():
                record = read_json(path)
                record.get("installed", {}).pop("ilastik", None)
                record.get("errors", {}).pop("ilastik", None)
                write_json(path, record)
        data["before"] = self.snapshot()
        for label, ref in (("head", "HEAD"), ("arranged_commit", "origin/main~1"), ("candidate", "origin/main")):
            data[label] = self.revision(ref)
        state = read_json(self.root / STATE)
        state["installed_commit"] = data["arranged_commit"]
        for key in ("last_attempt", "next_check", "dismissal", "last_success"):
            state.pop(key, None)
        write_json(self.root / STATE, state)
        data["cleared"] = clear_staging_verdict(self.root)
        write_json(self.out / "under-test.json", {"checkout": data["head"], "candidate_at_prepare": data["candidate"],
                   "gate_interpreter": sys.executable, "initial_slots": data["before"]["slots"]})
        self.save_phase("prepare", data)
        self.say(f"BACKUP: {backup}. Launch the desktop icon; wait for the update banner.")
        return data

    def installed(self):
        self.prompt("install", "In Firefox Extensions, install ilastik; wait for Ready and no install job.")
        data = {"after": self.snapshot()}
        self.save_phase("installed", data)
        require_ready(data["after"])
        require_record(data["after"])
        return data

    def staged(self):
        # This command is started BEFORE the human presses Update.
        data = {"cleared": clear_staging_verdict(self.root), "before": self.snapshot()}
        candidate = data["before"]["state"].get("last_success", {}).get("candidate")
        if not candidate:
            raise NotExercised("No update candidate is cached; launch/check now before staging")
        data["candidate"] = candidate
        self.save_phase("staged", data)
        self.prompt("stage", "Press Update in the Firefox banner. Wait for the restart controls, then DONE.")
        data["after"] = self.snapshot()
        self.save_phase("staged", data)
        return data

    def restarted(self):
        before = self.need("staged")["after"]
        self.say("Press Restart now in Firefox. Waiting for the new launch and its own health nonce.")
        self.restart_action()  # no-op outside the selftest
        health = self.wait_health(before["launch_lines"])
        data = {"health": health, "after": self.snapshot()}
        self.save_phase("restarted", data)
        return data

    def restart_action(self):
        pass

    def reinstalled(self):
        before = self.snapshot()
        self.prompt("reinstall", "Close Microclaw and its launcher; in a second PowerShell run .\\install.bat. "
                    "If the installer pauses for bridge/setup, cancel there with Ctrl+C (Y if asked). "
                    "Launch the desktop icon again and confirm ilastik remains Ready in Firefox.")
        data = {"before": before, "after": self.snapshot()}
        self.save_phase("reinstalled", data)
        return data


def active_probe(snapshot):
    return snapshot["slots"][snapshot["active"]]["probe"]


def require_ready(snapshot):
    probe = active_probe(snapshot)
    assert probe.get("exit") == 0 and probe.get("h5py") == "ready" and probe.get("ready") is True, probe
    return f"active={snapshot['active']}, h5py={probe['h5py']}, ready={probe['ready']}"


def require_record(snapshot):
    record = snapshot["extensions"]
    assert "ilastik" in record.get("installed", {}), record
    assert "ilastik" not in record.get("errors", {}), record
    return "installed.ilastik present; errors.ilastik absent"


def panel_row(snapshot):
    panel = snapshot["panel"]
    if panel.get("status") != 200:
        raise NotExercised(f"No panel JSON: {panel}")
    assert not panel["body"].get("job", {}).get("running"), panel
    return next(row for row in panel["body"]["extensions"] if row["name"] == "ilastik")


def require_previous_phase(gate, phase):
    """Refuse a phase whose predecessor was never captured.

    The 2026-09-21 gate lost the whole `restarted` phase: the runbook put its
    command under the same heading as `staged`, the operator ran the first one,
    and the three limbs it owned reported "not captured" at the end of the run
    -- by which time the restart had been performed and not observed.  Its three
    claims were recoverable off-rig only because the next phase's pre-snapshot
    happened to catch the new launcher line and its health nonce.

    design/69a already established that renumbering a skipped step does not stop
    it being skipped; that seed step was missed in three consecutive rounds
    through exactly that fix.  So the program refuses at the moment it matters
    rather than the runbook asking harder.
    """
    order = PHASES[:PHASES.index("restore")]
    if phase not in order or phase == "prepare":
        return None
    previous = order[order.index(phase) - 1]
    if not (gate.out / f"{previous}.json").exists():
        raise NotExercised(
            f"{previous} was never captured, so {phase} cannot be scored against it; "
            f"run -Phase {previous} first"
        )
    return previous


def verify(gate, only=None, *, cleanup=True):
    results = []
    def score(name, phase, fn):
        if only and phase != only:
            return
        try:
            detail = fn()
            status = "PASS"
        except NotExercised as exc:
            status, detail = "NOT EXERCISED", str(exc)
        except Exception as exc:
            status, detail = "FAIL", f"{type(exc).__name__}: {exc}"
        results.append({"name": name, "phase": phase, "status": status, "detail": detail})
        gate.say(f"{status}: {name} — {detail}")

    def before_skill():
        snap = gate.need("prepare")["before"]
        probe = active_probe(snap)
        if probe.get("h5py") != "absent":
            raise NotExercised("initial active slot was not h5py-absent")
        assert probe.get("ready") is False, probe
        assert probe["skill_text"] == LINE + probe["skill_original"], probe
        return f"ready=False; prefix={len(LINE)} characters; full file retained"

    def after_skill():
        before = active_probe(gate.need("prepare")["before"])
        after = active_probe(gate.need("installed")["after"])
        if before.get("h5py") != "absent":
            raise NotExercised("no absent-extension capture")
        assert after.get("ready") is True, after
        assert before["skill_original"] == after["skill_original"], {"before_file": before["skill_original"], "after_file": after["skill_original"]}
        assert before["skill_text"] == LINE + after["skill_text"], {"before": before["skill_text"], "expected": LINE + after["skill_text"]}
        assert after["skill_text"] == after["skill_original"], {"after": after["skill_text"], "expected": after["skill_original"]}
        return f"before/after differ by exactly {len(LINE)} prefix characters; after is file alone"

    def carried():
        prep = gate.need("prepare")["before"]
        stage = gate.need("staged")
        inactive = other(prep["active"])
        baseline = prep["slots"][inactive]["probe"]
        if baseline.get("h5py") != "absent":
            raise NotExercised(f"inactive {inactive} was not demonstrably h5py-absent: {baseline}")
        after = stage["after"]
        assert stage["before"]["active"] == prep["active"] == after["active"], {"prepare": prep["active"], "before_stage": stage["before"]["active"], "after_stage": after["active"]}
        assert after["pending"] == inactive, {"pending": after["pending"], "expected": inactive}
        probe = after["slots"][inactive]["probe"]
        assert probe.get("exit") == 0 and probe.get("h5py") == "ready" and probe.get("ready") is True, probe
        assert after["slots"][inactive]["marker"]["commit"] == stage["candidate"]["sha"], {"marker": after["slots"][inactive]["marker"], "candidate": stage["candidate"]}
        return f"inactive={inactive}: absent -> ready; pending={after['pending']}; commit={stage['candidate']['sha']}"

    def stage_state():
        stage = gate.need("staged")
        state = stage["after"]["state"]
        assert state.get("staging") == {"status": "staged", "commit": stage["candidate"]["sha"]}, state
        assert "build_failed_commit" not in state and "build_error" not in state, state
        return require_record(stage["after"])

    def restart():
        stage = gate.need("staged")
        data = gate.need("restarted")
        after = data["after"]
        assert after["active"] == other(stage["before"]["active"]), {"before": stage["before"]["active"], "after": after["active"]}
        assert after["slots"][after["active"]]["marker"]["commit"] == stage["candidate"]["sha"], {"marker": after["slots"][after["active"]]["marker"], "candidate": stage["candidate"]}
        assert data["health"]["nonce"] == after["health"] and data["health"]["slot"] == after["active"], {"launch": data["health"], "marker_nonce": after["health"], "active": after["active"]}
        return f"active={after['active']}; SHA={stage['candidate']['sha']}; nonce={after['health']}"

    def reinstall():
        data = gate.need("reinstalled")
        before, after = data["before"], data["after"]
        slot = before["active"]
        assert after["active"] == slot, {"before": slot, "after": after["active"]}
        old, new = before["slots"][slot]["venv_mtime_ns"], after["slots"][slot]["venv_mtime_ns"]
        row = panel_row(after)
        assert row["ready"], row
        assert after["slots"][slot]["marker"]["commit"] == gate.need("prepare")["head"], {"marker": after["slots"][slot]["marker"], "expected": gate.need("prepare")["head"]}
        require_record(after)
        return require_ready(after) + f"; pyvenv.cfg mtime before={old}, after={new} (observation only)"

    def absent():
        data = gate.need("recovery")
        probe = active_probe(data["rebuilt"])
        assert probe.get("h5py") == "absent" and probe.get("ready") is False and probe.get("exit") == 0, probe
        return "rebuilt interpreter ran; h5py absent before launch"

    def missing():
        data = gate.need("recovery")
        row = panel_row(data["missing"])
        assert row["recorded"] is True and row["ready"] is False, row
        assert data["operator_missing_button"] == "Reinstall", {"typed": data["operator_missing_button"], "expected": "Reinstall"}
        return "JSON recorded=True ready=False; operator saw Reinstall in Firefox"

    def recovered():
        data = gate.need("recovery")
        assert "recovery_error" not in data, data.get("recovery_error")
        require_ready(data["recovered"])
        require_record(data["recovered"])
        row = panel_row(data["recovered"])
        assert row["ready"], row
        assert data["cleanup"].get("exists") is False, data["cleanup"]
        return f"ready=True; deleted preserved environment {data['cleanup']['deleted']}"

    score("71c absent disclosure", "prepare", before_skill)
    def installed_panel():
        data = gate.need("installed")
        row = panel_row(data["after"])
        assert row["ready"] and row["recorded"], row
        return require_ready(data["after"]) + "; panel ready=True recorded=True"

    score("installed readiness", "installed", installed_panel)
    score("installed record", "installed", lambda: require_record(gate.need("installed")["after"]))
    score("71c before/after diff", "installed", after_skill)
    score("extras carried into clean inactive slot", "staged", carried)
    score("staged state and extras errors", "staged", stage_state)
    score("restart activation and health", "restarted", restart)
    score("restart extension readiness", "restarted", lambda: require_ready(gate.need("restarted")["after"]))
    score("restart extension record", "restarted", lambda: require_record(gate.need("restarted")["after"]))
    score("ordinary reinstall preserves extension", "reinstalled", reinstall)
    score("recovery genuine absence", "recovery", absent)
    score("recovery missing panel", "recovery", missing)
    score("recovery readiness and cleanup", "recovery", recovered)
    cleanup_error = None
    if cleanup and only is None:
        try:
            gate.clean_artifacts()
        except Exception as exc:
            cleanup_error = str(exc)
            gate.say(f"FAIL: gate artifact cleanup — {type(exc).__name__}: {exc}")
    failures = sum(row["status"] != "PASS" for row in results)
    gate.say(f"RESULT: {failures} failed or not exercised limbs / {len(results)}" + ("; gate artifact cleanup FAILED" if cleanup_error else ""))
    write_json(gate.out / ("results.json" if not only else f"{only}-results.json"), results)
    return int(bool(failures or cleanup_error))


# The fake reads the *installer's spec*. It never reads a limb's desired answer.
# Package presence follows pyproject optional-dependencies; --clear removes it.
STUB = r'''
import importlib.abc, json, sys, types, tomllib
from email.message import Message
from importlib import metadata
from pathlib import Path
slot = Path(__file__).resolve().parent.parent
repo = Path(sys.argv[1])
sys.path.insert(0, str(repo))
sys.executable = str(Path(__file__).resolve())
installed = json.loads((slot / 'fake-packages.json').read_text(encoding='utf-8'))
project = tomllib.loads((repo / 'pyproject.toml').read_text(encoding='utf-8'))['project']
original_metadata = metadata.metadata
original_version = metadata.version
original_packages = metadata.packages_distributions
from packaging.requirements import Requirement

def distribution(name):
    if name != 'microclaw':
        return original_metadata(name)
    result = Message()
    for extra, requirements in project['optional-dependencies'].items():
        result['Provides-Extra'] = extra
        for requirement in requirements:
            result['Requires-Dist'] = requirement + '; extra == "' + extra + '"'
    return result
metadata.metadata = distribution
metadata.version = lambda name: ('3.10.0' if name == 'h5py' else original_version(name))
metadata.packages_distributions = lambda: {'h5py': ['h5py']}
class Missing(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'h5py':
            raise ModuleNotFoundError("No module named 'h5py'", name='h5py')
if 'h5py' in installed:
    sys.modules['h5py'] = types.ModuleType('h5py')
else:
    sys.meta_path.insert(0, Missing())
# Host readiness is never consulted, but the product's extensions.ready and
# skills.load_skill_text still execute over this slot-shaped import environment.
code = sys.argv[2]
sys.argv = ['-c', *sys.argv[3:]]
exec(compile(code, '<slot-probe>', 'exec'))
'''

FAKE_UV = r'''
import json, shutil, sys, time, tomllib
from pathlib import Path
from packaging.requirements import Requirement
stub, repo = map(Path, sys.argv[1:3])
args = sys.argv[3:]
if args[0] == 'venv':
    target = Path(args[-1])
    if (target / 'pyvenv.cfg').exists() and '--clear' not in args:
        sys.stderr.write('A virtual environment already exists; use --clear\n')
        sys.exit(2)
    if target.exists():
        shutil.rmtree(target)
    (target / 'Scripts').mkdir(parents=True)
    shutil.copy2(stub, target / 'Scripts' / 'python.exe')
    (target / 'Scripts' / 'microclaw.exe').write_text('stub console', encoding='utf-8')
    (target / 'pyvenv.cfg').write_text('home = fixture\n', encoding='utf-8')
    (target / 'fake-packages.json').write_text('[]', encoding='utf-8')
elif args[:2] == ['pip', 'uninstall']:
    target = Path(args[args.index('--python') + 1]).parent.parent
    path = target / 'fake-packages.json'
    packages = set(json.loads(path.read_text(encoding='utf-8')))
    packages.difference_update(args[args.index('--python') + 2:])
    path.write_text(json.dumps(sorted(packages)), encoding='utf-8')
elif args[:2] == ['pip', 'install']:
    target = Path(args[args.index('--python') + 1]).parent.parent
    path = target / 'fake-packages.json'
    packages = set(json.loads(path.read_text(encoding='utf-8')))
    extras = args[-1].rsplit('[', 1)[-1].rstrip(']').split(',')
    optional = tomllib.loads((repo / 'pyproject.toml').read_text(encoding='utf-8'))['project']['optional-dependencies']
    for extra in extras:
        if extra not in optional:
            sys.stderr.write('No solution found: unknown fixture extra ' + extra)
            sys.exit(1)
        packages.update(Requirement(req).name for req in optional[extra])
    path.write_text(json.dumps(sorted(packages)), encoding='utf-8')
else:
    sys.stderr.write('Unsupported uv argv: ' + repr(args))
    sys.exit(2)
'''


class FakeGate(Gate):
    """Replace external actors only. Shared phases, snapshots and scoring run."""
    def __init__(self, root, out, updates, extensions):
        super().__init__(root, out)
        self.updates, self.extensions = updates, extensions
        self.interpreter = sys.executable
        self.running = False
        self.inject_failure = False
        self.inject_restore_failure = False
        self.stub = self.out / "stub-interpreter.py"
        self.uv = self.out / "fake-uv.py"
        self.stub.write_text(STUB, encoding="utf-8")
        self.uv.write_text(FAKE_UV, encoding="utf-8")
        self.real_run = subprocess.run
        self.uv_calls = []

    def revision(self, ref):
        return {"HEAD": "a" * 40, "origin/main": "b" * 40, "origin/main~1": "c" * 40}[ref]

    def processes(self):
        return [{"ProcessId": 999, "ExecutablePath": "fixture", "CommandLine": "fixture serve"}] if self.running else []

    def stop_servers(self):
        self.running = False
        return []

    def slot_command(self, slot, payload):
        return [self.interpreter, str(self.root / f"env-{slot}" / "Scripts" / "python.exe"),
                str(self.repo), PROBE, str(payload), str(self.fixture)]

    def api(self):
        if not self.running:
            return {"error": "fixture server stopped"}
        from unittest.mock import patch
        probe = self.probe(selector(self.root))
        with patch.object(self.extensions, "user_data_dir", lambda: self.root), \
             patch.object(self.extensions, "ready", lambda name: probe.get("ready", False)):
            return {"status": 200, "body": {"extensions": self.extensions.available(), "job": {"running": False}}}

    def uv_run(self, command, **kwargs):
        command = [str(x) for x in command]
        if command[0] == "fixture-uv":
            self.uv_calls.append(command)
            write_json(self.out / "uv-invocations.json", self.uv_calls)
            return self.real_run([self.interpreter, str(self.uv), str(self.stub), str(self.repo), *command[1:]],
                                 **kwargs)
        if "-c" in command:
            slot = Path(command[0]).parent.parent
            installed = read_json(slot / "fake-packages.json")
            return subprocess.CompletedProcess(command, 0 if "uvicorn" in installed else 1, "", "")
        # compare_slot_configurations runs the two console scripts' classify CLI.
        return subprocess.CompletedProcess(command, 0, json.dumps({"classification": "ready"}), "")

    def install(self, slot, extras, *, fresh=False):
        if fresh:
            result = self.uv_run(["fixture-uv", "venv", "--clear", "--python", "3.12", self.root / f"env-{slot}"],
                                 capture_output=True, text=True)
            assert result.returncode == 0, result.stderr
        result = self.uv_run(["fixture-uv", "pip", "install", "--python",
                              self.root / f"env-{slot}" / "Scripts" / "python.exe", f"{self.repo}[{extras}]"],
                             capture_output=True, text=True)
        assert result.returncode == 0, result.stderr

    def launch(self):
        if self.inject_restore_failure:
            raise RuntimeError("injected restored-launch failure")
        slot = selector(self.root)
        nonce, _ = self.updates.fresh_launch(self.root, slot)
        with (self.root / "launcher.log").open("a", encoding="utf-8") as log:
            log.write(f"2026-09-21T00:00:00 slot={slot} nonce={nonce}\n")
        # Use the product's writer/validation, including launcher-owned identity.
        env = {self.updates.LAUNCHER_OWNED_ENV: "1", self.updates.LAUNCH_ROOT_ENV: str(self.root),
               self.updates.LAUNCH_SLOT_ENV: slot, self.updates.LAUNCH_NONCE_ENV: nonce,
               self.updates.LAUNCH_PROTOCOL_ENV: "1"}
        self.updates.write_launcher_health(env, executable=self.root / f"env-{slot}" / "Scripts" / "python.exe")
        self.running = True

    def restart_action(self):
        self.updates.activate_pending(self.root, 1)
        self.launch()

    def prompt(self, event, message):
        from unittest.mock import patch
        slot = selector(self.root)
        if event == "stop":
            self.running = False
        elif event in ("install", "recover-install"):
            if event == "recover-install" and self.inject_failure:
                raise RuntimeError("injected Reinstall failure")
            self.install(slot, "ilastik")
            with patch.object(self.extensions, "user_data_dir", lambda: self.root):
                self.extensions.record("ilastik", ["h5py>=3.10"])
            self.running = True
        elif event == "stage":
            candidate = self.updates.Candidate("b" * 40, "fixture update", "clone")
            with patch.object(self.updates.subprocess, "run", self.uv_run), \
                 patch.object(self.extensions, "user_data_dir", lambda: self.root):
                self.updates.stage_inactive_slot(self.root, self.repo, candidate, uv_executable="fixture-uv")
        elif event in ("rebuild", "reinstall"):
            self.running = False
            self.install(slot, "serve", fresh=event == "rebuild")
            self.updates.retract_pending_slot(self.root)
            self.updates.write_state(self.updates.public_provenance("a" * 40), self.root / STATE)
            self.updates.write_slot_marker("a" * 40, 1, executable=self.root / f"env-{slot}" / "Scripts" / "python.exe")
            if event == "reinstall":
                self.launch()
        elif event == "missing":
            self.launch()
            return "Reinstall"
        else:
            raise AssertionError(event)


def selftest(out):
    # Product imports here only: real probes never see the checkout on sys.path.
    sys.path.insert(0, str(REPO))
    from microclaw import extensions, updates
    assert (ACTIVE, PENDING, MARKER, STATE, HEALTH) == (
        updates.ACTIVE_SLOT_NAME, updates.PENDING_SLOT_NAME, updates.SLOT_NAME, updates.STATE_NAME, updates.HEALTH_NAME)
    out = out / f"run-{time.time_ns()}"
    out.mkdir(parents=True, exist_ok=False)
    print(f"SELFTEST EVIDENCE: {out}", flush=True)
    with tempfile.TemporaryDirectory(prefix="71b-managed-") as temporary:
        base = Path(temporary)
        root = base / "microclaw"
        root.mkdir()
        gate = FakeGate(root, out / "cycle", updates, extensions)
        pointer = root / "71b-gate-evidence.txt"
        pointer.write_text(str(gate.out), encoding="ascii")
        old_appdata = os.environ.get("APPDATA")
        os.environ["APPDATA"] = str(base / "roaming")
        try:
            (base / "roaming" / "microclaw").mkdir(parents=True)
            for slot in ("a", "b"):
                gate.install(slot, "serve,ilastik", fresh=True)
                updates.write_slot_marker("a" * 40, 1, executable=root / f"env-{slot}" / "Scripts" / "python.exe")
            updates._write_slot_text(root / ACTIVE, "a")
            updates.write_state(updates.public_provenance("a" * 40), root / STATE)
            (root / "launcher-protocol.txt").write_text("1\n", encoding="ascii")
            from unittest.mock import patch
            def reset_run(argv, **kwargs):
                return gate.uv_run(argv, **kwargs) if str(argv[0]) == "fixture-uv" else gate.real_run(argv, **kwargs)
            with patch("shutil.which", return_value="fixture-uv"), patch("subprocess.run", side_effect=reset_run):
                gate.prepare(fresh=True)
            prep = gate.need("prepare")
            assert all(prep["original"]["slots"][s]["probe"]["h5py"] == "ready" for s in ("a", "b"))
            assert all(prep["before"]["slots"][s]["probe"]["h5py"] == "absent" for s in ("a", "b"))
            state = read_json(root / STATE)
            state["last_success"] = {"candidate": updates.Candidate("b" * 40, "fixture update", "clone").__dict__}
            updates.write_state(state, root / STATE)
            gate.launch()
            gate.installed()
            gate.staged()  # Calls the tree's REAL stage_inactive_slot.
            gate.restarted()
            gate.reinstalled()
            if active_probe(gate.snapshot()).get("h5py") != "ready":
                # Keep recovery independent of an earlier broken carry. The
                # failed update/reinstall observations are NEVER overwritten.
                gate.say("SELFTEST: seeding recovery prerequisite after failed carry; earlier artifacts unchanged")
                gate.install(selector(root), "ilastik")
            gate.recovery()
            cycle_result = verify(gate, cleanup=False)
            baseline_results = read_json(gate.out / "results.json")
            # A supported uv operation may touch this file; observe it without
            # changing the readiness/record verdict, on either product tree.
            path = gate.out / "reinstalled.json"
            original = path.read_bytes()
            try:
                data = read_json(path)
                data["after"]["slots"][data["after"]["active"]]["venv_mtime_ns"] = 123
                write_json(path, data)
                verify(gate, only="reinstalled", cleanup=False)
                observed = read_json(gate.out / "reinstalled-results.json")[0]
                baseline = next(r for r in baseline_results if r["phase"] == "reinstalled")
                assert observed["status"] == baseline["status"], observed
                if observed["status"] == "PASS":
                    assert "after=123" in observed["detail"], observed
            finally:
                path.write_bytes(original)
            gate.say("PASS: selftest changed mtime does not change the reinstall verdict")

            # The ordering guard is the fix for the 2026-09-21 skipped `restarted`
            # phase, and main() is not reachable from here, so drive it directly:
            # a guard nothing executes is 58a's `github:` branch again.
            for phase, previous in (("staged", "installed"), ("restarted", "staged"),
                                    ("reinstalled", "restarted"), ("recovery", "reinstalled")):
                path = gate.out / f"{previous}.json"
                original = path.read_bytes()
                path.unlink()
                try:
                    require_previous_phase(gate, phase)
                except NotExercised as exc:
                    assert previous in str(exc) and phase in str(exc), exc
                else:
                    raise AssertionError(f"{phase} ran with {previous} missing")
                finally:
                    path.write_bytes(original)
                assert require_previous_phase(gate, phase) == previous
            assert require_previous_phase(gate, "prepare") is None
            gate.say("PASS: selftest phase ordering refuses a skipped predecessor")

            # Controls mutate saved observations only, never product code.
            controls = []
            for label, phase, mutate, expected in (
                ("inactive already had h5py", "prepare", lambda d: d["before"]["slots"]["b"]["probe"].update(h5py="ready"), "extras carried into clean inactive slot"),
                ("unconditional skill prefix", "installed", lambda d: d["after"]["slots"]["a"]["probe"].update(skill_text=LINE + d["after"]["slots"]["a"]["probe"]["skill_original"]), "71c before/after diff"),
                ("rebuilt h5py never absent", "recovery", lambda d: active_probe(d["rebuilt"]).update(h5py="ready"), "recovery genuine absence"),
                ("wrong button label", "recovery", lambda d: d.update(operator_missing_button="Install"), "recovery missing panel"),
                ("instrument missing key", "staged", lambda d: d["candidate"].pop("sha"), "staged state and extras errors"),
            ):
                path = gate.out / f"{phase}.json"
                original = path.read_bytes()
                try:
                    gate.say(f"CONTROL BEGIN (expected non-pass): {label}")
                    data = read_json(path)
                    mutate(data)
                    write_json(path, data)
                    verify(gate, cleanup=False)
                    rows = read_json(gate.out / "results.json")
                    status = next(row["status"] for row in rows if row["name"] == expected)
                    assert status != "PASS", label
                    if label in ("wrong button label", "instrument missing key"):
                        assert status == "FAIL", (label, status)
                    controls.append({"control": label, "observed": status})
                    gate.say(f"CONTROL DETECTED: {label} -> {status}")
                finally:
                    path.write_bytes(original)
            write_json(out / "controls.json", controls)
            verify(gate, cleanup=False)  # Restore the unmutated score artifact.

            # The same recovery method must execute its restore, not a duplicate.
            for fail_restore in (False, True):
                failed = FakeGate(root, out / ("restore-failure" if fail_restore else "recovery-failure"), updates, extensions)
                shutil.copytree(gate.fixture, failed.fixture)
                pointer.write_text(str(failed.out), encoding="ascii")
                failed.inject_failure = True
                original_slot = selector(root)
                original_record = (root / RECORD).read_bytes()
                # Fail the *restoration* launch, not the missing-panel launch.
                original_restore = failed.restore
                def restoration():
                    failed.inject_restore_failure = fail_restore
                    return original_restore()
                failed.restore = restoration
                try:
                    failed.recovery()
                except RuntimeError as exc:
                    assert "injected Reinstall failure" in str(exc), str(exc)
                else:
                    raise AssertionError("Injected recovery failure did not fire")
                record = read_json(failed.out / "restore.json")
                assert (root / RECORD).read_bytes() == original_record
                assert selector(root) == original_slot
                assert failed.probe(original_slot)["h5py"] == "ready"
                if fail_restore:
                    assert Path(record["retained_environment"]).exists()
                    assert "injected restored-launch failure" in record["restore_error"]
                    # Exercise the documented retry after a retained environment.
                    # Verify cleanup must leave the journal usable even while
                    # a preserved environment still needs a restore retry.
                    failed.clean_artifacts()
                    failed.inject_restore_failure = False
                    original_restore()
                    assert not pointer.exists(), "restore resurrected the evidence pointer"
                else:
                    assert record["status"] == "restored" and record["files_restored"]
                failed.clean_artifacts()
                journal = read_json(failed.out / "recovery-journal.json")
                assert not Path(journal["launcher_backup"]).exists(), journal
                assert Path(journal["backup"]).exists(), journal
                assert not pointer.exists(), "pointer remained after cleanup"
                gate.say(f"PASS: selftest {'retained-environment retry' if fail_restore else 'recovery failure restoration'}")
            cleanup = FakeGate(root, out / "cleanup-failure", updates, extensions)
            shutil.copytree(gate.fixture, cleanup.fixture)
            pointer.write_text(str(cleanup.out), encoding="ascii")
            real_rmtree = shutil.rmtree
            def partial_delete(path, *args, **kwargs):
                path = Path(path)
                if path.name.startswith("71b-preserved-"):
                    (path / "Scripts" / "python.exe").unlink()
                    raise PermissionError("injected partial cleanup failure")
                return real_rmtree(path, *args, **kwargs)
            with patch("shutil.rmtree", side_effect=partial_delete):
                try:
                    cleanup.recovery()
                except PermissionError as exc:
                    assert "injected partial cleanup failure" in str(exc)
                else:
                    raise AssertionError("Cleanup failure did not fire")
            journal = read_json(cleanup.out / "recovery-journal.json")
            assert journal["status"] == "cleanup-started"
            assert not (Path(journal["preserved"]) / "Scripts" / "python.exe").exists()
            assert cleanup.probe(selector(root))["h5py"] == "ready"
            cleanup.restore()
            assert not Path(journal["preserved"]).exists()
            cleanup.clean_artifacts()
            assert not pointer.exists()
            gate.say("PASS: selftest partial cleanup retry retained the verified replacement")
            pointer.write_text(str(gate.out), encoding="ascii")
            real_cleanup = shutil.rmtree
            def check_pointer_last(path, *args, **kwargs):
                if Path(path).name.startswith("71b-recovery-files-"):
                    assert pointer.exists(), "pointer removed before recovery files"
                return real_cleanup(path, *args, **kwargs)
            with patch("shutil.rmtree", side_effect=check_pointer_last):
                assert verify(gate) == cycle_result
            assert not pointer.exists()
            assert not list(root.glob("71b-recovery-files-*"))
            # Both Python's entry guard and the launcher check require the named
            # field, not a dataclass field count that an unrelated field can match.
            from microclaw import skills
            require_branch()
            fields = dict(skills.SkillMetadata.__dataclass_fields__)
            fields["unrelated"] = fields.pop("requires")
            with patch.object(skills.SkillMetadata, "__dataclass_fields__", fields):
                try:
                    require_branch()
                except NotExercised as exc:
                    assert sys.executable in str(exc), str(exc)
                else:
                    raise AssertionError("Unrelated fourth field passed capability guard")
            import ast
            tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
            scorer = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "verify")
            assert all(n.msg is not None for n in ast.walk(scorer) if isinstance(n, ast.Assert))
            gate.say("PASS: selftest cleanup removes launcher-root artifacts, pointer last; capability guard and assertion diagnostics checked")
            gate.say("PASS: selftest controls fired (preinstalled inactive, unconditional prefix, false absence)")
            gate.say(f"SELFTEST RESULT: {'FAIL' if cycle_result else 'PASS'}; full cycle plus recovery/restore failures exercised")
            return cycle_result
        finally:
            if old_appdata is None:
                os.environ.pop("APPDATA", None)
            else:
                os.environ["APPDATA"] = old_appdata


def require_branch():
    try:
        from microclaw import extensions, skills
        if "requires" not in skills.SkillMetadata.__dataclass_fields__ or not callable(extensions.recorded_errors):
            raise ValueError("requires/recorded_errors capabilities absent")
    except Exception as exc:
        raise NotExercised(f"Control interpreter {sys.executable} lacks 71b/71c: {exc}") from exc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", nargs="?", choices=PHASES)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--fresh", action="store_true", help="prepare only: back up, then uninstall h5py in both slots")
    args = parser.parse_args()
    if args.selftest:
        return selftest(args.out)
    if os.name != "nt":
        parser.error("The demo gate needs Windows; use --selftest for a local fixture run")
    gate = Gate(Path(os.environ["LOCALAPPDATA"]) / "microclaw", args.out)
    if args.phase == "verify":
        return verify(gate)
    if not args.phase:
        parser.error("a phase is required")
    try:
        require_previous_phase(gate, args.phase)
        if args.phase in ("prepare", "reinstalled", "recovery", "restore"):
            require_branch()
        if args.phase == "prepare":
            gate.prepare(fresh=args.fresh)
        else:
            getattr(gate, args.phase)()
        return 0 if args.phase == "restore" else verify(gate, only=args.phase)
    except BaseException as exc:
        status = "NOT EXERCISED" if isinstance(exc, NotExercised) else "FAIL"
        gate.say(f"{status}: {args.phase} — {type(exc).__name__}: {exc}")
        path = gate.out / f"{args.phase}.json"
        data = read_json(path) if path.exists() else {}
        data["phase_error"] = f"{type(exc).__name__}: {exc}"
        gate.save_phase(args.phase, data)
        if args.phase == "recovery":
            gate.say("If restoration retained an environment, run: .\\design\\71-block71b-demo-gate.ps1 -Phase restore")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
