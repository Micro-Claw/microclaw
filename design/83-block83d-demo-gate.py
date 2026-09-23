"""83d: community skill-package store on the demo machine; score files and the panel.

Runs under a managed slot's python (the .ps1 picks it) and imports that slot's
installed microclaw, so the code under test is what install.bat put there.
Fixture releases are built from this checkout's tests/fixtures with the PUBLIC
TEST-ONLY seeds. Eligibility is read from the running serve's API, never
computed in this process: this process may be a different build than serve.
Only --selftest substitutes the launcher, serve, uv staging and the operator.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.util
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
FIXTURES = REPO / "tests" / "fixtures" / "skill_packages"
PHASES = ("prepare", "desktop", "updated", "rolledback", "reinstalled", "verify")
API = "http://127.0.0.1:8000/api/skill-packages"
MARKDOWN, EXECUTABLE = "markdown-fixture", "executable-fixture"
PATCHED_VERSION = "2.0.0"   # the fixtures declare microclaw ">=0.1,<2"


class NotExercised(RuntimeError):
    pass


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, indent=2, default=str) + "\n", encoding="utf-8")
    os.replace(temp, path)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def load_builder():
    spec = importlib.util.spec_from_file_location("build_release", FIXTURES / "build_release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def now():
    return datetime.now(timezone.utc)


# --- machine observations that must not change -------------------------------------------

def bin_listing():
    """%USERPROFILE%\\.local\\bin: where uv would drop a python link without --no-bin."""
    base = Path(os.environ.get("USERPROFILE") or Path.home()) / ".local" / "bin"
    if not base.is_dir():
        return {"exists": False, "entries": {}}
    return {"exists": True, "entries": {p.name: [p.lstat().st_size, p.lstat().st_mtime_ns]
                                        for p in sorted(base.iterdir())}}


def registry_listing():
    """Every key name under HKCU\\Software\\Python (PEP 514), plus a reader control."""
    import winreg

    def walk(key, prefix):
        names = []
        i = 0
        while True:
            try:
                sub = winreg.EnumKey(key, i)
            except OSError:
                break
            names.append(prefix + sub)
            with winreg.OpenKey(key, sub) as child:
                names += walk(child, prefix + sub + "\\")
            i += 1
        return names

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft") as control:
        control_ok = winreg.QueryInfoKey(control)[0] > 0
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Python") as key:
            names = walk(key, "")
    except FileNotFoundError:
        names = []
    return {"control_reads_software_microsoft": control_ok, "python_keys": names}


class Gate:
    def __init__(self, root, out, *, fake=None):
        self.root = Path(root)             # %LOCALAPPDATA%\microclaw, the launcher root
        self.out = Path(out)
        self.fake = fake
        self.out.mkdir(parents=True, exist_ok=True)
        from microclaw import skill_store, updates
        self.store, self.updates = skill_store, updates
        self.store_root = self.store.store_dir()
        if self.store_root.resolve() != (self.root / "skill-packages").resolve():
            raise NotExercised(f"store {self.store_root} is not under launcher root {self.root}")

    # plumbing ------------------------------------------------------------------------------
    def say(self, text):
        line = f"{datetime.now().isoformat(timespec='seconds')} {text}"
        print(line, flush=True)
        with (self.out / "gate.log").open("a", encoding="utf-8") as log:
            log.write(line + "\n")

    def save(self, phase, data):
        write_json(self.out / f"{phase}.json", data)

    def need(self, phase):
        path = self.out / f"{phase}.json"
        if not path.exists():
            raise NotExercised(f"phase {phase} was not captured")
        data = read_json(path)
        if data.get("phase_error"):
            raise NotExercised(f"phase {phase} failed: {data['phase_error']}")
        return data

    def ask(self, prompt, key):
        """A human judgement. The answer is recorded verbatim and scored later."""
        if self.fake:
            return self.fake.answer(key)
        self.say("OPERATOR: " + prompt)
        answer = input("> ").strip()
        self.say(f"ANSWER[{key}]: {answer}")
        if answer.upper() == "STOP":
            raise NotExercised("operator stopped the gate")
        return answer

    def get(self):
        if self.fake:
            return self.fake.get()
        with urllib.request.urlopen(API, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))

    def post(self, package_id, action):
        if self.fake:
            return self.fake.post(package_id, action)
        request = urllib.request.Request(f"{API}/{package_id}/{action}", data=b"", method="POST")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return {"status": response.status, "body": json.loads(response.read().decode("utf-8"))}
        except urllib.error.HTTPError as exc:
            return {"status": exc.code, "body": exc.read().decode("utf-8", "replace")}

    def launch_lines(self):
        path = self.root / "launcher.log"
        return [line for line in path.read_text(encoding="utf-8-sig").splitlines()
                if " slot=" in line and " nonce=" in line] if path.exists() else []

    def launch_desktop(self, why):
        """The operator launches the desktop icon; we wait for THAT launch's nonce."""
        before = self.launch_lines()
        if self.fake:
            self.fake.launch()
        else:
            self.ask(f"{why} Launch Microclaw from its DESKTOP ICON now, open the "
                     "'Community skill packages' panel in Firefox, then type DONE.", "launched")
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

    def close_microclaw(self, why):
        if self.fake:
            return self.fake.close()
        self.ask(f"{why} Close Microclaw's console window and its launcher window "
                 "(leave Micro-Manager open), then type DONE.", "closed")
        try:
            urllib.request.urlopen(API, timeout=3)
        except (urllib.error.URLError, OSError):
            return
        raise NotExercised("serve still answers on port 8000; Microclaw was not closed")

    def wait_current(self, timeout=180):
        """Poll serve until no ready install reads `unchecked` (its startup recheck ran)."""
        timeout = self.fake.timeout if self.fake else timeout
        deadline = time.perf_counter() + timeout
        last = None
        while time.perf_counter() < deadline:
            try:
                last = self.get()
                rows = [i for p in last["packages"] for i in p["installs"] if i["state"] == "ready"]
                if rows and not any(r["field"] == "unchecked" for i in rows for r in i["reasons"]):
                    return last
            except (urllib.error.URLError, OSError, ValueError, KeyError):
                pass
            time.sleep(0.5)
        raise NotExercised(f"serve's startup recheck did not finish within {timeout} s: {last}")

    def wait_job(self, package_id, timeout=600):
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            state = self.get()
            row = next((p for p in state["packages"] if p["package_id"] == package_id), None)
            if row and row.get("job") and not row["job"].get("running"):
                return state, row["job"]
            time.sleep(0.5)
        raise NotExercised(f"{package_id} job did not finish within {timeout} s")

    def files(self):
        """The store's durable state, read as files: pointers, install and verdict records."""
        result = {}
        packages = self.store_root / "packages"
        for package in sorted(packages.iterdir()) if packages.exists() else []:
            entry = {"pointer": None, "installs": {}, "entries": sorted(p.name for p in package.iterdir())}
            if (package / "pointer.json").exists():
                entry["pointer"] = read_json(package / "pointer.json")
            for install in sorted((package / "installs").iterdir()) if (package / "installs").exists() else []:
                item = {"files": sorted(p.name for p in install.iterdir())}
                for name in ("install.json", "verdict.json"):
                    if (install / name).exists():
                        item[name] = read_json(install / name)
                entry["installs"][install.name] = item
            result[package.name] = entry
        return result

    def independent_probe(self, python):
        """Execute the environment's python ourselves: existence is not evidence."""
        completed = subprocess.run([python, "-I", "-c", self.store.PROBE], stdin=subprocess.DEVNULL,
                                   capture_output=True, text=True, timeout=60)
        return {"exit": completed.returncode, "stderr": completed.stderr[-2000:],
                "identity": json.loads(completed.stdout) if completed.returncode == 0 else None}

    def active(self, files, package_id):
        pointer = files[package_id]["pointer"]
        return files[package_id]["installs"][pointer["active"]]["install.json"]

    # phases -------------------------------------------------------------------------------
    def prepare(self):
        if self.store_root.exists():
            raise NotExercised(f"{self.store_root} already exists; this gate creates and removes "
                               "its own store. Send the evidence folder instead of deleting it.")
        self.close_microclaw("Prepare installs releases in this process.")
        before = {"bin": self.bin(), "registry": self.registry()}
        builder = load_builder()
        releases = self.out / "releases"
        releases.mkdir(exist_ok=True)
        write_json(self.store_root / "trust" / "roots.json", read_json(FIXTURES / "trust" / "roots-TEST-ONLY.json"))
        policy = self.store.store_trust_policy(read_json(FIXTURES / "trust" / "policy-TEST-ONLY.json"))
        broken = self.out / "src-broken-self-check"
        shutil.copytree(FIXTURES / "executable", broken)
        runner = broken / "fixture_worker" / "runner.py"
        runner.write_text("raise SystemExit('83d gate: deliberately failing self_check')\n", encoding="utf-8")
        manifest = read_json(broken / "manifest.json")
        import hashlib
        for asset in manifest["assets"]:
            if asset["path"] == "fixture_worker/runner.py":
                asset["sha256"] = hashlib.sha256(runner.read_bytes()).hexdigest()
        write_json(broken / "manifest.json", manifest)
        plan = [("markdown-1.0.0", FIXTURES / "markdown", "1.0.0"),
                ("executable-1.0.0", FIXTURES / "executable", "1.0.0"),
                ("executable-1.1.0", FIXTURES / "executable", "1.1.0"),
                ("executable-1.2.0-broken", broken, "1.2.0")]
        results, spies = {}, {}
        for name, source, version in plan:
            artifact = releases / (name + ".zip")
            intake = builder.build_release(source, artifact, version=version)
            write_json(releases / (name + ".intake.json"), intake)
            calls = []
            start = time.perf_counter()
            try:
                with self.spy(calls):
                    record = self.store.install(
                        intake, artifact, policy=policy, now=now(), uv_executable=self.uv(),
                        retained_digests=frozenset(), find_links=(FIXTURES / "wheels").resolve(),
                        base_python=self.base_python())
                results[name] = {"ok": True, "install_id": record["install_id"]}
            except self.store.PackageRefusal as exc:
                results[name] = {"ok": False, "field": exc.field, "detail": str(exc)}
            results[name]["seconds"] = round(time.perf_counter() - start, 3)
            spies[name] = calls
            self.say(f"installed {name}: {results[name]}")
        files = self.files()
        executable = self.active(files, EXECUTABLE)
        self.save("prepare", {"before": before, "after": {"bin": self.bin(), "registry": self.registry()},
                              "results": results, "spies": spies, "files": files,
                              "probe": self.independent_probe(executable["python"]),
                              "pinned_dir": self.fake.pinned_dir() if self.fake else str(self.store_root / "python")})

    def desktop(self):
        launch = self.launch_desktop("First desktop launch with the store present.")
        panel = self.wait_current()
        banner = self.ask("In Firefox, expand 'Community skill packages'. Type the first line shown "
                          "under that heading, exactly as it reads.", "banner")
        files = self.files()
        before_active = files[EXECUTABLE]["pointer"]["active"]
        before_previous = files[EXECUTABLE]["pointer"]["previous"]
        start = time.perf_counter()
        response = self.post(EXECUTABLE, "rollback")
        after_panel, job = self.wait_job(EXECUTABLE)
        seconds = round(time.perf_counter() - start, 3)
        after = self.files()
        self.save("desktop", {"launch": launch, "panel": panel, "banner": banner,
                              "before_pointer": {"active": before_active, "previous": before_previous},
                              "rollback_response": response, "job": job, "job_seconds": seconds,
                              "after_panel": after_panel, "files": after})

    def updated(self):
        self.close_microclaw("Next the gate stages a MicroClaw build that reports version 2.0.0.")
        source = self.out / "src-2.0.0"
        if source.exists():
            shutil.rmtree(source)
        commit = self.stage_patched(source)
        active_before = (self.root / "active-slot.txt").read_text(encoding="ascii").strip()
        pending = (self.root / "pending-slot.txt").read_text(encoding="ascii").strip()
        launch = self.launch_desktop("The other slot now holds a 2.0.0 build and is pending.")
        panel = self.wait_current()
        reason = self.ask("In the panel, what does the executable-fixture (active) row say after "
                          "'disabled:'? Type it exactly.", "incompatible_reason")
        files = self.files()
        executable = self.active(files, EXECUTABLE)
        self.save("updated", {"commit": commit, "active_before": active_before, "pending": pending,
                              "launch": launch, "panel": panel, "reason": reason, "files": files,
                              "probe": self.independent_probe(executable["python"]),
                              "slot_markers": self.markers()})

    def rolledback(self):
        prior = self.need("updated")
        self.close_microclaw("Next the gate moves the running version back to the untouched old slot.")
        old = prior["active_before"]
        # The launcher's own activation path: activate_pending validates this slot's
        # marker and flips active-slot.txt. The old slot was never rebuilt.
        temporary = self.root / ".pending-slot.txt.tmp"
        temporary.write_text(old + "\n", encoding="ascii")
        os.replace(temporary, self.root / "pending-slot.txt")
        launch = self.launch_desktop("The old (0.1.0) slot is now pending.")
        panel = self.wait_current()
        files = self.files()
        executable = self.active(files, EXECUTABLE)
        self.save("rolledback", {"old_slot": old, "launch": launch, "panel": panel, "files": files,
                                 "probe": self.independent_probe(executable["python"]),
                                 "slot_markers": self.markers()})

    def reinstalled(self):
        self.close_microclaw("Next: leftovers are planted, one environment is broken, then install.bat.")
        before = self.files()
        planted = self.plant_leftovers(before)
        if self.fake:
            self.fake.install_bat()
        else:
            self.ask("In a SECOND PowerShell window run:  cd $env:USERPROFILE\\Documents\\GitHub\\microclaw; "
                     ".\\install.bat   If it pauses for bridge/setup, cancel with Ctrl+C (Y). "
                     "Do NOT launch Microclaw. Type DONE when the installer has finished or been cancelled.",
                     "install_bat")
        between = self.files()
        launch = self.launch_desktop("After the reinstall.")
        broken_panel = self.wait_current()
        after_launch = self.files()
        start = time.perf_counter()
        response = self.post(EXECUTABLE, "repair")
        repaired_panel, job = self.wait_job(EXECUTABLE)
        seconds = round(time.perf_counter() - start, 3)
        repaired = self.files()
        executable = self.active(repaired, EXECUTABLE)
        markdown_digest = self.active(repaired, MARKDOWN)["artifact_digest"]
        removal = {}
        try:
            self.store.remove(MARKDOWN, retained_digests={markdown_digest})
            removal["retained"] = {"refused": False}
        except self.store.PackageRefusal as exc:
            removal["retained"] = {"refused": True, "field": exc.field}
        removal["result"] = self.store.remove(MARKDOWN, retained_digests=frozenset())
        removal["panel"] = self.get()
        self.save("reinstalled", {"before": before, "planted": planted, "between": between,
                                  "launch": launch, "broken_panel": broken_panel, "after_launch": after_launch,
                                  "repair_response": response, "job": job, "job_seconds": seconds,
                                  "repaired_panel": repaired_panel, "files": repaired,
                                  "probe": self.independent_probe(executable["python"]),
                                  "removal": removal})

    def cleanup(self):
        """Remove the gate's store (and with it the TEST-ONLY roots). Microclaw must be closed."""
        self.close_microclaw("Final cleanup removes the gate's store and its TEST-ONLY roots.")
        roots = self.store_root / "trust" / "roots.json"
        try:
            roots.unlink(missing_ok=True)          # first: the security-relevant file
        finally:
            shutil.rmtree(self.store_root, ignore_errors=True)
        (self.root / "83d-gate-evidence.txt").unlink(missing_ok=True)
        return {"roots_present": roots.exists(), "store_present": self.store_root.exists(),
                "bin": self.bin(), "registry": self.registry()}

    # helpers that differ under --selftest ---------------------------------------------------
    def uv(self):
        return self.fake.uv() if self.fake else self.updates.locate_uv()

    def base_python(self):
        # None: provision the pinned interpreter for real (uv python install --install-dir).
        return self.fake.base_python() if self.fake else None

    def bin(self):
        return self.fake.bin() if self.fake else bin_listing()

    def registry(self):
        return self.fake.registry() if self.fake else registry_listing()

    def spy(self, calls):
        """Record every process and supervisor the store starts, without blocking them."""
        store, real_popen, real_supervisor = self.store, subprocess.Popen, self.store.Supervisor

        class Recorder:
            def __enter__(inner):
                def popen(args, *a, **k):
                    calls.append(["Popen", [str(x) for x in (args if isinstance(args, (list, tuple)) else [args])]])
                    return real_popen(args, *a, **k)

                def supervisor(*a, **k):
                    calls.append(["Supervisor", k])
                    return real_supervisor(*a, **k)
                subprocess.Popen, store.Supervisor = popen, supervisor

            def __exit__(inner, *exc):
                subprocess.Popen, store.Supervisor = real_popen, real_supervisor
        return Recorder()

    def markers(self):
        result = {}
        for slot in ("a", "b"):
            marker = self.root / f"env-{slot}" / "microclaw-slot.json"
            result[slot] = read_json(marker) if marker.exists() else None
        return result

    def stage_patched(self, source):
        if self.fake:
            return self.fake.stage_patched(source)
        git = self.updates.locate_git()
        commit = subprocess.run([git, "-C", str(REPO), "rev-parse", "HEAD"], stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, check=True).stdout.strip()
        # Absolute: `git -C <repo> archive -o <relative>` writes relative to <repo>.
        archive = (self.out / "src.zip").resolve()
        subprocess.run([git, "-C", str(REPO), "archive", "--format=zip", "-o", str(archive), "HEAD"],
                       stdin=subprocess.DEVNULL, capture_output=True, check=True)
        shutil.unpack_archive(archive, source)
        for relative, pattern, replacement in (
                ("pyproject.toml", r'(?m)^version = "[^"]+"', f'version = "{PATCHED_VERSION}"'),
                ("microclaw/__init__.py", r'(?m)^__version__ = "[^"]+"', f'__version__ = "{PATCHED_VERSION}"')):
            path = source / relative
            text, count = re.subn(pattern, replacement, path.read_text(encoding="utf-8"), count=1)
            if count != 1:
                raise NotExercised(f"could not patch the version in {relative}")
            path.write_text(text, encoding="utf-8")
        candidate = self.updates.Candidate(commit, "83d gate: version 2.0.0", "clone")
        self.updates.stage_inactive_slot(self.root, source, candidate, uv_executable=self.updates.locate_uv())
        return commit

    def plant_leftovers(self, files):
        """Real Windows files in the states recovery must clean up; recorded before launch."""
        markdown = self.store_root / "packages" / MARKDOWN
        orphan = markdown / "installs" / "0123456789abcdef-a0a0a0"
        orphan.mkdir(parents=True)
        (orphan / "partial.bin").write_bytes(b"interrupted extraction")
        temporary = markdown / ".pointer.json.gate-interrupted"
        temporary.write_bytes(b'{"active":')
        dead = subprocess.Popen([sys.executable, "-c", "pass"], stdin=subprocess.DEVNULL)
        dead.wait()
        lock = markdown / ".lock"
        lock.mkdir()
        write_json(lock / "owner.json", {"pid": dead.pid, "nonce": "83d-gate-dead-owner", "created_at": 0})
        executable = self.active(files, EXECUTABLE)
        python = Path(executable["python"])
        hidden = python.with_name(python.name + ".83d-gate-hidden")
        os.replace(python, hidden)
        return {"orphan": str(orphan), "temporary": str(temporary), "lock": str(lock),
                "dead_pid": dead.pid, "hidden_python": str(hidden), "python": str(python),
                "broken_install": files[EXECUTABLE]["pointer"]["active"]}


# --- scoring: each limb independent; NOT EXERCISED is never a pass -------------------------

def rows(panel, package_id):
    package = next((p for p in panel["packages"] if p["package_id"] == package_id), None)
    if package is None:
        raise AssertionError(f"{package_id} is absent from the panel")
    return package


def active_row(panel, package_id):
    package = rows(panel, package_id)
    row = next((i for i in package["installs"] if i["active"]), None)
    if row is None:
        raise AssertionError(f"{package_id} has no active install in the panel")
    return row


def verify(gate, *, cleanup):
    results = []

    def score(name, fn):
        try:
            detail, status = fn(), "PASS"
        except NotExercised as exc:
            status, detail = "NOT EXERCISED", str(exc)
        except Exception as exc:
            status, detail = "FAIL", f"{type(exc).__name__}: {exc}"
        results.append({"name": name, "status": status, "detail": detail})
        gate.say(f"{status}: {name} — {detail}")

    def markdown_no_python():
        prep = gate.need("prepare")
        md, exe = prep["spies"]["markdown-1.0.0"], prep["spies"]["executable-1.0.0"]
        if not exe:
            raise NotExercised("control failed: the spy recorded nothing for the executable install")
        assert prep["results"]["markdown-1.0.0"]["ok"], prep["results"]["markdown-1.0.0"]
        assert md == [], md
        install = prep["files"][MARKDOWN]["installs"][prep["results"]["markdown-1.0.0"]["install_id"]]
        assert "env" not in install["files"] and install["install.json"]["interpreter"] is None, install
        return f"markdown: 0 processes/supervisors; executable control: {len(exe)} recorded"

    def pinned_interpreter():
        prep = gate.need("prepare")
        record = gate.active_record(prep["files"], EXECUTABLE)
        pinned = Path(prep["pinned_dir"]).resolve()
        base = Path(record["interpreter"]["base_prefix"]).resolve()
        assert base.is_relative_to(pinned), (str(base), str(pinned))
        probe = prep["probe"]
        assert probe["exit"] == 0 and probe["identity"] == record["interpreter"], probe
        assert record["interpreter"]["distributions"] == {"fixture-dependency": "1.2.3"}, record["interpreter"]
        assert record["self_check"]["state"] == "succeeded", record["self_check"]
        return f"base {base} under the pinned dir; lock installed; self_check succeeded"

    def nothing_outside_store():
        prep = gate.need("prepare")
        if not prep["before"]["registry"].get("control_reads_software_microsoft"):
            raise NotExercised("registry reader control failed")
        assert prep["before"]["bin"] == prep["after"]["bin"], (prep["before"]["bin"], prep["after"]["bin"])
        assert prep["before"]["registry"] == prep["after"]["registry"], "HKCU\\Software\\Python changed"
        return f"{len(prep['after']['bin']['entries'])} ~/.local/bin entries and " \
               f"{len(prep['after']['registry']['python_keys'])} Python registry keys unchanged"

    def failed_self_check():
        prep = gate.need("prepare")
        result = prep["results"]["executable-1.2.0-broken"]
        assert result["ok"] is False and result["field"] == "self_check", result
        pointer = prep["files"][EXECUTABLE]["pointer"]
        assert pointer["active"] == prep["results"]["executable-1.1.0"]["install_id"], pointer
        assert pointer["previous"] == prep["results"]["executable-1.0.0"]["install_id"], pointer
        failed = [i for i in prep["files"][EXECUTABLE]["installs"].values()
                  if i.get("install.json", {}).get("state") == "failed"]
        assert len(failed) == 1 and failed[0]["install.json"]["self_check"]["state"] != "succeeded", failed
        return "candidate recorded failed with its self_check record; 1.1.0 stayed active"

    def desktop_eligible():
        desk = gate.need("desktop")
        panel = desk["panel"]
        assert panel["trust"]["test_roots_active"] is True, panel["trust"]
        for package_id in (MARKDOWN, EXECUTABLE):
            row = active_row(panel, package_id)
            assert row["eligible"] and not row["reasons"], row
        assert "TEST-ONLY" in desk["banner"], f"operator saw: {desk['banner']!r}"
        return "both active releases eligible under a desktop serve; operator saw the TEST-ONLY banner"

    def desktop_worker():
        desk = gate.need("desktop")
        assert desk["rollback_response"]["status"] == 202, desk["rollback_response"]
        assert not desk["job"].get("reasons"), desk["job"]
        before = desk["before_pointer"]
        pointer = desk["files"][EXECUTABLE]["pointer"]
        assert pointer == {"active": before["previous"], "previous": before["active"]}, pointer
        record = desk["files"][EXECUTABLE]["installs"][pointer["active"]]["install.json"]
        prep = gate.need("prepare")
        earlier = prep["files"][EXECUTABLE]["installs"][pointer["active"]]["install.json"]["self_check"]
        assert record["self_check"]["state"] == "succeeded", record["self_check"]
        assert record["self_check"]["job_id"] != earlier["job_id"], "self_check record was not replaced"
        return f"serve ran a new self_check worker ({record['self_check']['job_id']}) in {desk['job_seconds']} s"

    def incompatible_after_update():
        upd, desk = gate.need("updated"), gate.need("desktop")
        assert upd["launch"]["slot"] == upd["pending"] != upd["active_before"], upd["launch"]
        for package_id in (MARKDOWN, EXECUTABLE):
            before = active_row(desk["after_panel"], package_id)
            if not before["eligible"]:
                raise NotExercised(f"control: {package_id} was not eligible before the update")
            row = active_row(upd["panel"], package_id)
            assert not row["eligible"], row
            fields = [r["field"] for r in row["reasons"]]
            assert fields == ["microclaw"], row["reasons"]
            assert PATCHED_VERSION in row["reasons"][0]["detail"], row["reasons"]
        for install in upd["files"][EXECUTABLE]["installs"].values():
            verdict = install.get("verdict.json")
            if verdict and install["install.json"]["state"] == "ready":
                assert verdict["build"]["version"] == PATCHED_VERSION, verdict["build"]
        assert "microclaw" in upd["reason"], f"operator saw: {upd['reason']!r}"
        return "both releases visible and disabled with only `microclaw` after the 2.0.0 slot started"

    def interpreter_survives():
        results_ = []
        for phase in ("updated", "rolledback"):
            data = gate.need(phase)
            record = gate.active_record(data["files"], EXECUTABLE)
            row = active_row(data["panel"], EXECUTABLE)
            assert not [r for r in row["reasons"] if r["field"] == "interpreter"], row["reasons"]
            probe = data["probe"]
            assert probe["exit"] == 0 and probe["identity"] == record["interpreter"], probe
            results_.append(phase)
        upd, prep = gate.need("updated"), gate.need("prepare")
        assert upd["slot_markers"][upd["pending"]] is not None, upd["slot_markers"]
        return f"environment probe matched its recorded identity after {', '.join(results_)}"

    def compatible_after_rollback():
        back, upd = gate.need("rolledback"), gate.need("updated")
        assert back["launch"]["slot"] == back["old_slot"] == upd["active_before"], back["launch"]
        for package_id in (MARKDOWN, EXECUTABLE):
            row = active_row(back["panel"], package_id)
            assert row["eligible"] and not row["reasons"], row
        for install in back["files"][EXECUTABLE]["installs"].values():
            verdict = install.get("verdict.json")
            if verdict and install["install.json"]["state"] == "ready":
                assert verdict["build"]["version"] != PATCHED_VERSION, verdict["build"]
        return "moving the version backwards re-enabled both releases with fresh verdicts"

    def recovery_on_windows():
        re_ = gate.need("reinstalled")
        planted = re_["planted"]
        orphan = Path(planted["orphan"]).name
        between, after = re_["between"][MARKDOWN], re_["after_launch"][MARKDOWN]
        if not ({".lock", ".pointer.json.gate-interrupted"} <= set(between["entries"])
                and orphan in between["installs"]):
            raise NotExercised(f"leftovers were not in place before launch: {between['entries']}")
        assert ".lock" not in after["entries"], after["entries"]
        assert not [e for e in after["entries"] if e.startswith((".pointer.json.", ".lock-stale-"))], after["entries"]
        assert orphan not in after["installs"], sorted(after["installs"])
        assert after["pointer"] == between["pointer"], (after["pointer"], between["pointer"])
        panel = re_["broken_panel"]
        md = rows(panel, MARKDOWN)
        assert active_row(panel, MARKDOWN)["eligible"], md
        assert not md["broken"], md
        assert all(i["install_id"] != orphan for i in md["installs"]), md["installs"]
        return "serve's startup recovery removed the orphan install, the pointer temporary and a dead-owner lock"

    def broken_visible_and_repaired():
        re_ = gate.need("reinstalled")
        broken = active_row(re_["broken_panel"], EXECUTABLE)
        assert broken["install_id"] == re_["planted"]["broken_install"], broken
        assert not broken["eligible"] and any(r["field"] == "interpreter" for r in broken["reasons"]), broken
        assert re_["repair_response"]["status"] == 202 and not re_["job"].get("reasons"), re_["job"]
        repaired = active_row(re_["repaired_panel"], EXECUTABLE)
        assert repaired["install_id"] != broken["install_id"] and repaired["eligible"], repaired
        probe = re_["probe"]
        assert probe["exit"] == 0, probe
        return f"missing python read disabled (`interpreter`); serve's repair built {repaired['install_id']}"

    def reinstall_kept_store():
        re_ = gate.need("reinstalled")
        before, between = re_["before"], re_["between"]
        for package_id in (MARKDOWN, EXECUTABLE):
            for install_id, item in before[package_id]["installs"].items():
                assert between[package_id]["installs"][install_id]["install.json"] == item["install.json"], install_id
        return "install.bat left every install record byte-identical"

    def remove_honours_retention():
        re_ = gate.need("reinstalled")
        removal = re_["removal"]
        assert removal["retained"] == {"refused": True, "field": "retained"}, removal["retained"]
        assert not removal["result"]["deletion_failures"], removal["result"]
        assert all(p["package_id"] != MARKDOWN for p in removal["panel"]["packages"]), removal["panel"]
        return "retained digest refused removal; unretained removal deleted the package"

    for name, fn in (("markdown installs without Python", markdown_no_python),
                     ("pinned interpreter and locked environment", pinned_interpreter),
                     ("nothing written outside the store", nothing_outside_store),
                     ("failed self_check keeps the old release live", failed_self_check),
                     ("desktop serve: releases eligible, TEST banner", desktop_eligible),
                     ("desktop serve runs a self_check worker (rollback)", desktop_worker),
                     ("incompatible after update reads disabled-and-why", incompatible_after_update),
                     ("worker interpreter survives slot replacement", interpreter_survives),
                     ("rollback re-enables with fresh verdicts", compatible_after_rollback),
                     ("startup recovery on real Windows files", recovery_on_windows),
                     ("broken interpreter visible, repaired by serve", broken_visible_and_repaired),
                     ("reinstall leaves the store untouched", reinstall_kept_store),
                     ("remove honours retained digests", remove_honours_retention)):
        score(name, fn)

    if cleanup:
        def cleaned():
            after = gate.cleanup()
            write_json(gate.out / "cleanup.json", after)
            assert not after["roots_present"], "TEST-ONLY roots.json survived cleanup"
            assert not after["store_present"], "the gate's store survived cleanup"
            prep = gate.need("prepare")
            assert after["bin"] == prep["before"]["bin"], "~/.local/bin differs from before the gate"
            assert after["registry"] == prep["before"]["registry"], "HKCU\\Software\\Python differs"
            return "TEST-ONLY roots and the gate's store removed; bin and registry as before"
        score("cleanup: no TEST-ONLY roots left behind", cleaned)

    write_json(gate.out / "results.json", results)
    bad = [r for r in results if r["status"] != "PASS"]
    gate.say(f"RESULT: {len(bad)} failed or not exercised limbs / {len(results)}")
    return 1 if bad else 0


def _active_record(self, files, package_id):
    return self.active(files, package_id)


Gate.active_record = _active_record


# --- selftest: a fake machine written from updates.py, webserve.py and skill_store.py ----

class Fake:
    """Launcher files as updates.py writes them; serve as webserve's routes call the store."""

    def __init__(self, root, *, sabotage=None):
        self.root, self.sabotage = Path(root), sabotage
        self.running = False
        self.timeout = 5
        self.answers = {"banner": None, "incompatible_reason": None}
        for slot in ("a", "b"):
            self.marker(slot, "0.1.0")
        (self.root / "active-slot.txt").write_text("a\n", encoding="ascii")

    def marker(self, slot, version):
        marker = self.root / f"env-{slot}" / "microclaw-slot.json"
        marker.parent.mkdir(parents=True, exist_ok=True)
        write_json(marker, {"commit": ("a" if slot == "a" else "b") * 40, "required_launcher_protocol": 1,
                            "selftest_version": version})

    def build(self):
        from microclaw import skill_packages, updates
        slot = (self.root / "active-slot.txt").read_text(encoding="ascii").strip()
        marker = updates.load_state(self.root / f"env-{slot}" / "microclaw-slot.json")
        return dict(version=marker["selftest_version"], commit=marker["commit"],
                    protocols=sorted(skill_packages.SUPPORTED_PROTOCOLS))

    def launch(self):
        from microclaw import skill_store, updates
        import secrets
        slot, _ = updates.activate_pending(self.root, 1)    # the launcher's real selector code
        nonce = secrets.token_hex(16)
        with (self.root / "launcher.log").open("a", encoding="utf-8") as log:
            log.write(f"{datetime.now().isoformat()} launch slot={slot} nonce={nonce}\n")
        (self.root / "launch-health.txt").write_text(nonce + "\n", encoding="ascii")
        skill_store.current_build = self.build
        if self.sabotage != "no-startup-recheck":
            # webserve.build_app.lifespan's startup thread, called synchronously here.
            skill_store.recover(retained_digests=frozenset())
            skill_store.recheck(now=now(), retained_digests=frozenset())
        self.running = True

    def close(self):
        self.running = False

    def get(self):
        from microclaw import skill_store
        if not self.running:
            raise urllib.error.URLError("closed")
        state = skill_store.status()
        slot = (self.root / "active-slot.txt").read_text(encoding="ascii").strip()
        self.answers["banner"] = ("TEST-ONLY trust roots are active" if state["trust"]["test_roots_active"] else "none")
        exe = next((p for p in state["packages"] if p["package_id"] == EXECUTABLE), None)
        if exe:
            row = next((i for i in exe["installs"] if i["active"]), {"reasons": []})
            self.answers["incompatible_reason"] = "; ".join(r["field"] + ": " + r["detail"] for r in row["reasons"])
        return state

    def post(self, package_id, action):
        from microclaw import skill_store
        try:
            body = skill_store.start_job(package_id, action, retained_digests=frozenset())
            return {"status": 202, "body": body}
        except skill_store.PackageRefusal as exc:
            return {"status": 409 if exc.field == "lock" else 400, "body": str(exc)}

    def answer(self, key):
        return self.answers.get(key) or "DONE"

    def stage_patched(self, source):
        inactive = "b" if (self.root / "active-slot.txt").read_text(encoding="ascii").strip() == "a" else "a"
        self.marker(inactive, "0.1.0" if self.sabotage == "no-version-change" else PATCHED_VERSION)
        (self.root / "pending-slot.txt").write_text(inactive + "\n", encoding="ascii")
        return "c" * 40

    def install_bat(self):
        pass

    def uv(self):
        import uv
        return uv.find_uv_bin()

    def base_python(self):
        return sys._base_executable

    def pinned_dir(self):
        return os.path.realpath(sys.base_prefix)

    def bin(self):
        return {"exists": True, "entries": {"uv.exe": [1, 1]}}

    def registry(self):
        return {"control_reads_software_microsoft": True, "python_keys": ["PythonCore"]}


def selftest(out):
    """Run every phase against the fake, then prove two limbs can fail."""
    out = Path(out)
    codes = {}
    for sabotage in (None, "no-version-change", "no-startup-recheck"):
        with tempfile.TemporaryDirectory(prefix="83d-selftest-") as home:
            saved = {k: os.environ.get(k) for k in ("LOCALAPPDATA", "XDG_DATA_HOME", "UV_CACHE_DIR")}
            os.environ.update(LOCALAPPDATA=home, XDG_DATA_HOME=home, UV_CACHE_DIR=str(Path(home) / "uvc"))
            from microclaw import skill_store
            real_build = skill_store.current_build
            try:
                root = Path(home) / "microclaw"
                root.mkdir()
                gate = Gate(root, out / (sabotage or "clean"), fake=Fake(root, sabotage=sabotage))
                for phase in PHASES[:-1]:
                    try:
                        getattr(gate, phase)()
                    except Exception as exc:
                        gate.say(f"phase {phase} error: {type(exc).__name__}: {exc}")
                        gate.save(phase, {"phase_error": f"{type(exc).__name__}: {exc}",
                                          "traceback": traceback.format_exc()})
                codes[sabotage] = (verify(gate, cleanup=True), read_json(gate.out / "results.json"))
            finally:
                skill_store.current_build = real_build
                for key, value in saved.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
    clean_code, clean = codes[None]
    assert clean_code == 0, [r for r in clean if r["status"] != "PASS"]
    status = {s: {r["name"]: r["status"] for r in codes[s][1]} for s in codes}
    assert status["no-version-change"]["incompatible after update reads disabled-and-why"] == "FAIL", status
    assert status["no-startup-recheck"]["incompatible after update reads disabled-and-why"] != "PASS", status
    print("SELFTEST PASSED: clean run all PASS; both sabotaged runs fail the compatibility limb")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", nargs="?", choices=PHASES)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest(args.out)
    if os.name != "nt":
        parser.error("the demo gate needs Windows; use --selftest for a local fake-machine run")
    if not args.phase:
        parser.error("a phase is required")
    try:
        from microclaw import skill_store
        if not callable(getattr(skill_store, "start_job", None)):
            raise ImportError("skill_store.start_job is missing")
    except Exception as exc:
        print(f"NOT EXERCISED: {sys.executable} lacks block 83d ({exc}). Run install.bat on this branch.")
        return 2
    gate = Gate(Path(os.environ["LOCALAPPDATA"]) / "microclaw", args.out)
    if args.phase == "verify":
        return verify(gate, cleanup=True)
    order = PHASES.index(args.phase)
    if order:
        gate.need(PHASES[order - 1])
    try:
        getattr(gate, args.phase)()
        gate.say(f"RECORDED: {args.phase}")
        return 0
    except BaseException as exc:
        status = "NOT EXERCISED" if isinstance(exc, NotExercised) else "FAIL"
        gate.say(f"{status}: {args.phase} — {type(exc).__name__}: {exc}")
        gate.save(args.phase, {"phase_error": f"{type(exc).__name__}: {exc}",
                               "traceback": traceback.format_exc()})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
