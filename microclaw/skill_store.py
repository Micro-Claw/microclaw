"""Durable per-release environments, independent of MicroClaw update slots.

Verdicts are keyed to the build and recorded interpreter, not a process nonce.
On a same-build restart an interpreter broken between launches reads eligible
until the startup probe finishes. Dispatch (83e) still fails closed because the
supervisor records a launch failure. Startup never executes publisher code.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import threading
import time
import uuid
import zipfile

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name

import microclaw
from microclaw import skill_packages as packages, updates
from microclaw.paths import user_data_dir
from microclaw.skill_supervisor import Supervisor

PackageRefusal = packages.PackageRefusal
PINNED_PYTHON = "3.12"
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024
PROBE = """import sys, os, sysconfig, json, re
from importlib.metadata import distributions
print(json.dumps(dict(executable=sys.executable, base_prefix=os.path.realpath(sys.base_prefix),
 version=sys.version, cache_tag=sys.implementation.cache_tag, platform=sysconfig.get_platform(),
 distributions={re.sub(r'[-_.]+', '-', d.metadata['Name']).lower(): d.version for d in distributions()})))
"""


def store_dir():
    return user_data_dir() / "skill-packages"


def current_build():
    try:
        marker = updates.read_slot_marker() or {}
    except (updates.UpdateError, OSError):
        marker = {}
    return dict(version=microclaw.__version__, commit=marker.get("commit") or "unknown",
                protocols=sorted(packages.SUPPORTED_PROTOCOLS))


def _reason(exc):
    field = getattr(exc, "field", "store")
    detail = str(exc)
    if detail.startswith(field + ": "):
        detail = detail[len(field) + 2:]
    return dict(field=field, detail=detail or type(exc).__name__)


def _read(path):
    return updates.load_state(path)


def _write(path, document):
    return updates.write_state(document, path)


def _package(package_id):
    packages._identifier(package_id, "package_id", packages.MAX_PACKAGE_ID_LENGTH)
    return packages.safe_release_path(store_dir(), "packages/" + package_id)


def _install_dir(package, install_id):
    if not isinstance(install_id, str) or not re.fullmatch(r"[0-9a-f]{16}-[0-9a-f]{6}", install_id):
        raise PackageRefusal("install_id", "invalid install identity")
    return packages.safe_release_path(package, "installs/" + install_id)


def _alive(pid):
    if type(pid) is not int or pid <= 0:
        return False
    if os.name == "nt":
        return updates._windows_process_alive(pid)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


@contextmanager
def package_lock(package_id):
    package = _package(package_id)
    package.mkdir(parents=True, exist_ok=True)
    lock = package / ".lock"
    nonce = uuid.uuid4().hex
    while True:
        try:
            lock.mkdir()
            break
        except FileExistsError:
            try:
                owner = _read(lock / "owner.json")
            except (updates.UpdateError, OSError):
                owner = None
            # PID reuse remains an accepted residual. Nonces detect renaming a
            # replacement lock, but the live owner temporarily loses its lock
            # pathname before restoration; another contender can occupy it and
            # prevent restoration. Missing-owner locks also share nonce None.
            try:
                stale = (not _alive(owner["pid"]) if owner and type(owner.get("pid")) is int
                         else time.time() - lock.stat().st_mtime > 60)
            except FileNotFoundError:
                continue
            if not stale:
                raise PackageRefusal("lock", "another operation owns this package")
            stale_nonce = owner.get("nonce") if owner else None
            abandoned = package / (".lock-stale-" + uuid.uuid4().hex)
            try:
                lock.rename(abandoned)
            except FileNotFoundError:
                continue
            try:
                moved_owner = _read(abandoned / "owner.json")
            except (updates.UpdateError, OSError):
                moved_owner = None
            if (moved_owner.get("nonce") if moved_owner else None) != stale_nonce:
                try:
                    abandoned.rename(lock)
                except OSError:
                    pass
                raise PackageRefusal("lock", "lock owner changed during stale-lock recovery")
            _delete(abandoned, [])
    try:
        _write(lock / "owner.json", dict(pid=os.getpid(), nonce=nonce, created_at=time.time()))
        yield package
    finally:
        try:
            owner = _read(lock / "owner.json")
            if owner and owner.get("nonce") == nonce:
                shutil.rmtree(lock)
        except (OSError, updates.UpdateError):
            pass


def _roots():
    roots_file = store_dir() / "trust" / "roots.json"
    try:
        roots = _read(roots_file)
    except (updates.UpdateError, OSError):
        roots = None
    if roots and roots.get("environment") == "test":
        return roots, dict(test_roots_active=True, reason=None)
    return packages.PRODUCTION_ROOTS, dict(
        test_roots_active=False,
        reason="production roots are not configurable" if roots_file.exists() else None)


def load_trust_policy():
    roots, _ = _roots()
    try:
        document = _read(store_dir() / "trust" / "policy.json")
        if document is None:
            raise PackageRefusal("trust", "no verified policy")
        return packages.verify_trust_policy(document, roots)
    except (packages.PackageRefusal, updates.UpdateError, OSError) as exc:
        raise PackageRefusal("trust", str(exc)) from exc


def store_trust_policy(document):
    roots, _ = _roots()
    previous = _read(store_dir() / "trust" / "policy.json")
    if previous is not None:
        previous = packages.verify_trust_policy(previous, roots)
    verified = packages.verify_trust_policy(document, roots, previous=previous)
    _write(store_dir() / "trust" / "policy.json", verified)
    return verified


def _records(package):
    directory = package / "installs"
    if not directory.exists():
        return []
    result = []
    for path in sorted(directory.iterdir()):
        try:
            _install_dir(package, path.name)
            record = _read(path / "install.json")
            result.append((path, record, None))
        except (OSError, updates.UpdateError, PackageRefusal) as exc:
            result.append((path, None, _reason(exc)))
    return result


def _pointer(package, records):
    pointer = None
    try:
        pointer = _read(package / "pointer.json")
        if pointer is None:
            if any(record and record.get("state") == "ready" for _, record, _ in records):
                raise PackageRefusal("pointer", "pointer missing")
            return dict(active=None, previous=None), []
        if set(pointer) != {"active", "previous"}:
            raise PackageRefusal("pointer", "invalid pointer")
        available = {path.name: record for path, record, _ in records}
        for name in pointer.values():
            if name is not None:
                _install_dir(package, name)
                if not available.get(name) or available[name].get("state") != "ready":
                    raise PackageRefusal("pointer", "names a missing or non-ready install: " + name)
        return pointer, []
    except (OSError, updates.UpdateError, PackageRefusal) as exc:
        if not isinstance(pointer, dict) or set(pointer) != {"active", "previous"}:
            pointer = None
        return pointer, [dict(field="pointer", detail=str(exc))]


def _delete(path, failures):
    try:
        if path.is_symlink() or path.is_file():
            path.unlink()
        else:
            shutil.rmtree(path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        failures.append(dict(path=str(path), detail=str(exc)))


def _retention(package, *, retained_digests, failures):
    records = _records(package)
    pointer, broken = _pointer(package, records)
    if broken:
        return  # External damage: preserve all evidence; never substitute previous.
    failed = [(record.get("created_at", 0), path.name) for path, record, _ in records
              if record and record.get("state") == "failed"]
    keep = set(pointer.values()) | ({max(failed)[1]} if failed else set())
    for path, record, error in records:
        if error:
            continue
        if (path.name not in keep and record
                and record.get("artifact_digest") not in retained_digests):
            _delete(path, failures)


def _recover(package, *, retained_digests):
    failures = []
    records = _records(package)
    pointer, broken = _pointer(package, records)
    if not broken:
        for path, record, error in records:
            if error:
                continue
            if record is None:
                _delete(path, failures)
            elif record.get("state") == "staged" and path.name not in pointer.values():
                record.update(state="failed", reasons=[dict(field="interrupted", detail="interrupted")])
                _write(path / "install.json", record)
        for path in package.glob(".pointer.json.*"):
            _delete(path, failures)
        _retention(package, retained_digests=retained_digests, failures=failures)
    for path in package.glob(".lock-stale-*"):
        _delete(path, failures)
    # Recovery holds the package lock, so no job can be live: a `running` record
    # was left by a serve that exited mid-job, and would otherwise read as
    # running (and keep the panel polling fast) forever.
    try:
        job = _read(package / "job.json")
    except (updates.UpdateError, OSError):
        job = None
    if job and job.get("running"):
        job.update(running=False, phase="interrupted",
                   reasons=[dict(field="interrupted", detail="serve exited during " + str(job.get("operation")))])
        _write(package / "job.json", job)
    _write(package / "recovery.json", dict(deletion_failures=failures, broken=broken))
    return dict(package_id=package.name, deletion_failures=failures, broken=broken)


def recover(*, retained_digests):
    results = []
    root = store_dir() / "packages"
    for path in sorted(root.iterdir()) if root.exists() else []:
        try:
            with package_lock(path.name) as package:
                results.append(_recover(package, retained_digests=retained_digests))
        except Exception as exc:
            results.append(dict(package_id=path.name, reasons=[_reason(exc)]))
    return results


def _run(argv, *, timeout, field, env=None):
    child_env = os.environ.copy() if env is None else env.copy()
    try:
        result = subprocess.run([os.fspath(a) for a in argv], stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, encoding="utf-8", errors="replace",
                                timeout=timeout, env=child_env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        # subprocess.run kills and reaps a timed-out child before raising.
        stderr = getattr(exc, "stderr", "") or ""
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        detail = f"{Path(argv[0]).name} {argv[1]} exit=None: {exc}; {stderr[-2000:]}"
    else:
        if result.returncode == 0:
            return result.stdout
        detail = f"{Path(argv[0]).name} {argv[1]} exit={result.returncode}: {result.stderr[-2000:]}"
    variables = sorted(key for key, value in os.environ.items()
                       if key.startswith("UV_")
                       and key not in {"UV_CACHE_DIR", "UV_PYTHON_INSTALL_DIR"}
                       and child_env.get(key) == value)
    if variables:
        detail += "; environment set: " + ", ".join(variables)
    raise PackageRefusal(field, detail)


def _uv_env():
    env = os.environ.copy()
    # uv's shared cache must not be modified by store operations either.
    env["UV_CACHE_DIR"] = str(store_dir() / "python" / ".uv-cache")
    env.pop("UV_PYTHON_INSTALL_DIR", None)
    return env


def provision_python(*, uv_executable=None, base_python=None):
    if base_python is not None:
        return str(base_python)
    uv = uv_executable or updates.locate_uv()
    _run([uv, "python", "install", "--no-config", "--no-bin", "--no-registry",
          "--install-dir", store_dir() / "python", PINNED_PYTHON],
         timeout=600, field="python", env=_uv_env())
    env = _uv_env()
    env["UV_PYTHON_INSTALL_DIR"] = str(store_dir() / "python")
    selected = _run([uv, "python", "find", "--no-config", "--managed-python",
                 "--no-python-downloads", PINNED_PYTHON],
                timeout=600, field="python", env=env).strip()
    if not Path(selected).resolve().is_relative_to((store_dir() / "python").resolve()):
        raise PackageRefusal("python", "uv selected an interpreter outside the pinned store")
    return selected


def probe(python):
    try:
        identity = json.loads(_run([python, "-I", "-c", PROBE], timeout=30, field="interpreter"))
        if (set(identity) != {"executable", "base_prefix", "version", "cache_tag", "platform", "distributions"}
                or not isinstance(identity["distributions"], dict)):
            raise ValueError("invalid interpreter identity")
        return identity
    except (ValueError, TypeError) as exc:
        raise PackageRefusal("interpreter", str(exc)) from exc


def _platform(identity, manifest):
    platform = identity["platform"]
    if platform == "win-amd64":
        tag = "win_amd64"
    elif re.fullmatch(r"macosx-.+-arm64", platform):
        tag = "macosx_arm64"
    elif platform == "linux-x86_64":
        tag = "manylinux_x86_64"
    else:
        raise PackageRefusal("platforms", "unsupported platform: " + platform)
    if tag not in manifest["platforms"]:
        raise PackageRefusal("platforms", "release does not support " + tag)
    return tag


def _compatibility(manifest, build, identity=None):
    if build["version"] not in SpecifierSet(manifest["microclaw"]):
        raise PackageRefusal("microclaw", "incompatible with " + build["version"])
    if manifest["kind"] == "executable":
        packages.supported_executable(manifest)
        if PINNED_PYTHON not in SpecifierSet(manifest["python"]):
            raise PackageRefusal("python", "excludes pinned Python " + PINNED_PYTHON)
        if identity is not None:
            if identity["version"].split()[0] not in SpecifierSet(manifest["python"]):
                raise PackageRefusal("python", "environment version excluded by release")
            _platform(identity, manifest)


def build_environment(directory, manifest, *, uv_executable=None, find_links=None, base_python=None):
    uv = uv_executable or updates.locate_uv()
    base = provision_python(uv_executable=uv, base_python=base_python)
    _run([uv, "venv", "--no-config", "--no-python-downloads", "--python", base, directory / "env"],
         timeout=120, field="interpreter", env=_uv_env())
    python = directory / "env" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    identity = probe(python)
    lock = manifest["locks"][_platform(identity, manifest)]
    requirements = "".join(item["requirement"] + "".join(" --hash=sha256:" + h for h in item["hashes"]) + "\n"
                           for item in lock)
    (directory / "requirements.txt").write_text(requirements, encoding="utf-8")
    if lock:
        argv = [uv, "pip", "install", "--no-config", "--python", python,
                "--require-hashes", "--no-deps", "--no-build"]
        if find_links is not None:
            argv += ["--no-index", "--find-links", find_links]
        _run(argv + ["-r", directory / "requirements.txt"], timeout=600, field="locks", env=_uv_env())
    identity = probe(python)
    pins = {}
    for item in lock:
        requirement = Requirement(item["requirement"])
        pins[canonicalize_name(requirement.name)] = next(iter(requirement.specifier)).version
    if identity["distributions"] != pins:
        raise PackageRefusal("locks", "installed distributions do not equal lock pins")
    return str(python), identity


def _extract(artifact, directory, intake):
    release = directory / "release"
    release.mkdir()
    with zipfile.ZipFile(artifact) as archive:
        members = archive.infolist()
        if len(members) > 1024:
            raise PackageRefusal("artifact", "more than 1024 members")
        names = set()
        total = 0
        for member in members:
            name = member.filename.rstrip("/") if member.is_dir() else member.filename
            packages.safe_release_path(release, name, field=member.filename,
                                       is_symlink=stat.S_ISLNK(member.external_attr >> 16))
            if name in names:
                raise PackageRefusal(member.filename, "duplicate member")
            names.add(name)
            if member.flag_bits & 1:
                raise PackageRefusal(member.filename, "encrypted member")
            total += member.file_size
            if total > MAX_ARCHIVE_BYTES:
                raise PackageRefusal(member.filename, "declared uncompressed size exceeds 256 MiB")
        try:
            manifest_info = archive.getinfo("manifest.json")
        except KeyError as exc:
            raise PackageRefusal("manifest.json", "missing manifest") from exc
        if manifest_info.file_size > MAX_MANIFEST_BYTES:
            raise PackageRefusal("manifest.json", "manifest exceeds 1 MiB")
        try:
            document = json.loads(archive.read(manifest_info))
        except (ValueError, zipfile.BadZipFile, OSError) as exc:
            raise PackageRefusal("manifest.json", str(exc)) from exc
        manifest = packages.validate_manifest(document)
        packages._bound_release(manifest, intake)
        expected = {"manifest.json"} | {asset["path"] for asset in manifest["assets"]}
        files = {member.filename for member in members if not member.is_dir()}
        if files != expected:
            raise PackageRefusal(sorted(files ^ expected)[0], "undeclared or missing archive member")
        for member in members:
            destination = packages.safe_release_path(release, member.filename.rstrip("/"))
            if member.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            count = 0
            try:
                with archive.open(member) as source, destination.open("xb") as target:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        count += len(chunk)
                        if count > member.file_size:
                            raise PackageRefusal(member.filename, "actual bytes exceed declared size")
                        target.write(chunk)
                if count != member.file_size:
                    raise PackageRefusal(member.filename, "actual size differs from declared size")
            except (zipfile.BadZipFile, OSError) as exc:
                # ZipExtFile itself bounds reads to the central directory size;
                # a forged smaller size is normally reported as a CRC failure.
                raise PackageRefusal(member.filename, str(exc)) from exc
        packages.verify_release_assets(release, manifest)
        return manifest


def _self_check(directory, record, policy, now):
    if record["manifest"]["kind"] != "executable":
        return
    supervisor = Supervisor(max_workers=1, max_queued=1)
    try:
        result = supervisor.self_check(dict(manifest=record["manifest"], intake=record["intake"],
                                            release_dir=directory / "release"),
                                       policy, now=now, python=record["python"])
        record["self_check"] = result
        _write(directory / "install.json", record)
        if result["state"] != "succeeded":
            failure = result.get("failure") or {}
            raise PackageRefusal("self_check", ": ".join(
                str(failure[key]) for key in ("reason", "detail") if failure.get(key)) or result["state"])
    finally:
        supervisor.close()


def _verdict(directory, record, policy, now):
    start = time.perf_counter()
    build = current_build()
    reasons = []
    checks = [lambda: packages.check_release(record["intake"], policy, purpose="execution", now=now),
              lambda: _verify_assets(directory, record),
              lambda: _compatibility(record["manifest"], build, record.get("interpreter"))]
    if (record.get("manifest") or {}).get("kind") == "executable":
        def interpreter():
            if probe(record["python"]) != record["interpreter"]:
                raise PackageRefusal("interpreter", "identity differs from installed interpreter")
        checks.append(interpreter)
    for check in checks:
        try:
            check()
        except Exception as exc:
            reasons.append(_reason(exc))
    return dict(build=build, interpreter=record.get("interpreter"), eligible=not reasons,
                reasons=reasons, checked_at=now.isoformat(), duration_s=time.perf_counter() - start)


def _verify_assets(directory, record):
    try:
        manifest = packages.validate_manifest(_read(directory / "release" / "manifest.json"))
        if manifest != record["manifest"]:
            raise PackageRefusal("assets", "manifest differs from installed manifest")
        packages._bound_release(manifest, record["intake"])
        packages.verify_release_assets(directory / "release", manifest)
    except (PackageRefusal, updates.UpdateError, OSError) as exc:
        raise PackageRefusal("assets", str(exc)) from exc


def _transaction(package, intake, artifact_path, *, policy, now, uv_executable,
                 retained_digests, find_links=None, base_python=None, repairing=None):
    records = _records(package)
    pointer, broken = _pointer(package, records)
    if repairing is None and any(record and record.get("state") == "ready"
                                 and record.get("artifact_digest") == intake["artifact_digest"]
                                 for _, record, _ in records):
        raise PackageRefusal("artifact_digest", "already installed; repair or roll back")
    # A damaged pointer remains repairable. Preserve its named previous when readable.
    if broken:
        if repairing is None:
            raise PackageRefusal("pointer", broken[0]["detail"])
        try:
            pointer = _read(package / "pointer.json") or dict(active=None, previous=None)
        except (updates.UpdateError, OSError):
            pointer = dict(active=None, previous=None)
    if not (package / "pointer.json").exists():
        _write(package / "pointer.json", dict(active=None, previous=None))
    install_id = intake["artifact_digest"][:16] + "-" + uuid.uuid4().hex[:6]
    directory = _install_dir(package, install_id)
    directory.mkdir(parents=True)
    record = dict(install_id=install_id, artifact_digest=intake["artifact_digest"], intake=intake,
                  created_at=time.time(), state="staged", manifest=None, interpreter=None,
                  python=None, find_links=str(find_links) if find_links is not None else None)
    try:
        shutil.copyfile(artifact_path, directory / "artifact.zip")
        packages.check_release(intake, policy, purpose="admission", now=now, artifact=directory / "artifact.zip")
        manifest = _extract(directory / "artifact.zip", directory, intake)
        record["manifest"] = manifest
        _compatibility(manifest, current_build())
        if manifest["kind"] == "executable":
            record["python"], record["interpreter"] = build_environment(
                directory, manifest, uv_executable=uv_executable, find_links=find_links, base_python=base_python)
        _write(directory / "install.json", record)
        _self_check(directory, record, policy, now)
        verdict = _verdict(directory, record, policy, now)
        if not verdict["eligible"]:
            reason = verdict["reasons"][0]
            raise PackageRefusal(reason["field"], reason["detail"])
        record.update(state="ready", reasons=[])
        _write(directory / "install.json", record)
        _write(directory / "verdict.json", verdict)
    except Exception as exc:
        record.update(state="failed", reasons=[_reason(exc)])
        _write(directory / "install.json", record)
        failures = []
        _retention(package, retained_digests=retained_digests, failures=failures)
        _write(package / "recovery.json", dict(deletion_failures=failures, broken=[]))
        raise
    # Nothing before this single atomic replacement activates the candidate.
    if repairing is None:
        _write(package / "pointer.json", dict(active=install_id, previous=pointer.get("active")))
    else:
        repaired_pointer = {key: install_id if value == repairing else value
                            for key, value in pointer.items()}
        if repaired_pointer != pointer:
            _write(package / "pointer.json", repaired_pointer)
    failures = []
    _retention(package, retained_digests=retained_digests, failures=failures)
    _write(package / "recovery.json", dict(deletion_failures=failures, broken=[]))
    return record


def install(intake, artifact_path, *, policy, now, uv_executable=None, retained_digests,
            find_links=None, base_python=None):
    packages.check_release(intake, policy, purpose="admission", now=now, artifact=artifact_path)
    with package_lock(intake["package_id"]) as package:
        _recover(package, retained_digests=retained_digests)
        return _transaction(package, intake, artifact_path, policy=policy, now=now,
                            uv_executable=uv_executable, retained_digests=retained_digests,
                            find_links=find_links, base_python=base_python)


def _rollback(package, *, policy, now, retained_digests):
    pointer, broken = _pointer(package, _records(package))
    if broken or not pointer["previous"]:
        raise PackageRefusal("pointer", "no ready previous install" if not broken else broken[0]["detail"])
    directory = _install_dir(package, pointer["previous"])
    record = _read(directory / "install.json")
    verdict = _verdict(directory, record, policy, now)
    _write(directory / "verdict.json", verdict)
    try:
        if not verdict["eligible"]:
            reason = verdict["reasons"][0]
            raise PackageRefusal(reason["field"], reason["detail"])
        _self_check(directory, record, policy, now)
    except Exception as exc:
        record["reasons"] = [_reason(exc)]
        _write(directory / "install.json", record)
        verdict.update(eligible=False, reasons=record["reasons"])
        _write(directory / "verdict.json", verdict)
        raise
    record["reasons"] = []
    _write(directory / "install.json", record)
    _write(package / "pointer.json", dict(active=pointer["previous"], previous=pointer["active"]))
    return record


def rollback(package_id, *, policy, now, retained_digests):
    with package_lock(package_id) as package:
        _recover(package, retained_digests=retained_digests)
        return _rollback(package, policy=policy, now=now, retained_digests=retained_digests)


def _repair(package, *, policy, now, uv_executable, retained_digests, install_id=None, base_python=None):
    if install_id is None:
        try:
            install_id = (_read(package / "pointer.json") or {}).get("active")
        except (updates.UpdateError, OSError) as exc:
            raise PackageRefusal("pointer", "name an install to repair an unreadable pointer") from exc
    directory = _install_dir(package, install_id)
    record = _read(directory / "install.json")
    if record is None:
        raise PackageRefusal("install_id", "no stored release to repair")
    packages.check_release(record["intake"], policy, purpose="admission", now=now, artifact=directory / "artifact.zip")
    return _transaction(package, record["intake"], directory / "artifact.zip", policy=policy, now=now,
                        uv_executable=uv_executable, retained_digests=retained_digests,
                        find_links=record.get("find_links"), base_python=base_python, repairing=install_id)


def repair(package_id, *, policy, now, uv_executable=None, retained_digests, install_id=None, base_python=None):
    """Reinstall stored bytes into a new directory. Stale policy refuses admission.

    This freshness requirement is a stated residual: offline execution under a
    stale policy can work while repair under that same policy refuses.
    """
    with package_lock(package_id) as package:
        _recover(package, retained_digests=retained_digests)
        return _repair(package, policy=policy, now=now, uv_executable=uv_executable,
                       retained_digests=retained_digests, install_id=install_id, base_python=base_python)


def remove(package_id, *, retained_digests, install_id=None):
    with package_lock(package_id) as package:
        _recover(package, retained_digests=retained_digests)
        records = _records(package)
        targets = [(path, record) for path, record, _ in records if install_id is None or path.name == install_id]
        if any(record and record.get("artifact_digest") in retained_digests for _, record in targets):
            raise PackageRefusal("retained", "release is referenced by a job or pinned receipt")
        if install_id is not None:
            path = _install_dir(package, install_id)
            pointer = _read(package / "pointer.json")
            if pointer and pointer.get("active") == install_id:
                raise PackageRefusal("active", "roll back or remove the package")
            if pointer and pointer.get("previous") == install_id:
                pointer["previous"] = None
                _write(package / "pointer.json", pointer)
            failures = []
            _delete(path, failures)
        else:
            failures = []
            _delete(package, failures)
        if failures:
            _write(package / "recovery.json", dict(deletion_failures=failures, broken=[]))
        return dict(deletion_failures=failures)


def recheck(*, now, retained_digests):
    """Recompute ready verdicts without retention, publisher code or pointer writes."""
    results = []
    try:
        policy = load_trust_policy()
    except Exception:
        policy = None
    try:
        root = store_dir() / "packages"
        for path in sorted(root.iterdir()) if root.exists() else []:
            try:
                with package_lock(path.name) as package:
                    for directory, record, _ in _records(package):
                        if record and record.get("state") == "ready":
                            verdict = _verdict(directory, record, policy, now)
                            _write(directory / "verdict.json", verdict)
                            results.append(dict(package_id=path.name, install_id=directory.name, **verdict))
            except Exception as exc:
                results.append(dict(package_id=path.name, reasons=[_reason(exc)]))
    except Exception as exc:
        results.append(dict(reasons=[_reason(exc)]))
    return results


def _eligibility(directory, record):
    try:
        verdict = _read(directory / "verdict.json")
        if (not verdict or verdict.get("build") != current_build()
                or verdict.get("interpreter") != record.get("interpreter")):
            return False, [dict(field="unchecked", detail="not yet checked against this build")]
        return verdict.get("eligible") is True, verdict.get("reasons", [])
    except (OSError, updates.UpdateError) as exc:
        return False, [dict(field="unchecked", detail=str(exc))]


def status():
    """Read files only, without probes, locks or process-local eligibility state."""
    _, trust = _roots()
    try:
        policy = load_trust_policy()
        trust.update(verified=True, revision=policy["revision"], reasons=[])
    except PackageRefusal as exc:
        trust.update(verified=False, reasons=[_reason(exc)])
    result = dict(packages=[], trust=trust)
    root = store_dir() / "packages"
    for path in sorted(root.iterdir()) if root.exists() else []:
        try:
            package = _package(path.name)
            records = _records(package)
            pointer, broken = _pointer(package, records)
            row = dict(package_id=path.name, installs=[], broken=broken)
            for directory, record, error in records:
                record = record or dict(state="broken", reasons=[error] if error else [dict(field="interrupted", detail="missing install.json")])
                eligible, reasons = _eligibility(directory, record) if record.get("state") == "ready" else (False, record.get("reasons", []))
                reasons = reasons + broken + trust["reasons"]
                row["installs"].append(dict(record, install_id=directory.name,
                    active=bool(pointer and pointer["active"] == directory.name),
                    previous=bool(pointer and pointer["previous"] == directory.name),
                    eligible=eligible and not broken and trust["verified"], reasons=reasons))
            row["deletion_failures"] = (_read(package / "recovery.json") or {}).get("deletion_failures", [])
            row["job"] = _read(package / "job.json")
            result["packages"].append(row)
        except Exception as exc:
            result["packages"].append(dict(package_id=path.name, installs=[], broken=[_reason(exc)]))
    return result


def resolve(package_id, artifact_digest, *, now):
    policy = load_trust_policy()
    refusal = None
    for directory, record, _ in _records(_package(package_id)):
        if record and record.get("state") == "ready" and record.get("artifact_digest") == artifact_digest:
            eligible, reasons = _eligibility(directory, record)
            if not eligible:
                reason = reasons[0] if reasons else dict(field="unchecked", detail="not eligible")
                refusal = PackageRefusal(reason["field"], reason["detail"])
                continue
            packages.check_release(record["intake"], policy, purpose="execution", now=now)
            return dict(record, release_dir=str(directory / "release"))
    if refusal is not None:
        raise refusal
    raise PackageRefusal("artifact_digest", "no ready install with requested digest")


def start_job(package_id, action, *, retained_digests):
    """Reserve the durable package lock before HTTP 202; report conflicts as 409."""
    if action not in {"rollback", "repair"}:
        raise PackageRefusal("operation", "unknown store operation")
    lock = package_lock(package_id)
    package = lock.__enter__()
    job = dict(operation=action, running=True, phase="recovering", started_at=time.time())
    try:
        _write(package / "job.json", job)
        def work():
            try:
                _recover(package, retained_digests=retained_digests)
                policy = load_trust_policy()
                job["phase"] = action
                _write(package / "job.json", job)
                kwargs = dict(policy=policy, now=datetime.now(timezone.utc), retained_digests=retained_digests)
                if action == "repair":
                    record = _repair(package, uv_executable=updates.locate_uv(), **kwargs)
                else:
                    record = _rollback(package, **kwargs)
                job["install_id"] = record["install_id"]
            except Exception as exc:
                job["reasons"] = [_reason(exc)]
            finally:
                job.update(running=False, phase="finished")
                try:
                    _write(package / "job.json", job)
                finally:
                    lock.__exit__(None, None, None)
        threading.Thread(target=work, name="microclaw-skill-" + action, daemon=True).start()
    except BaseException:
        job.update(running=False, phase="failed to start")
        try:
            _write(package / "job.json", job)
        finally:
            lock.__exit__(None, None, None)
        raise
    return dict(package_id=package_id, operation=action)
