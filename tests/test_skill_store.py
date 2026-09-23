"""Offline real-uv transactions; assertions inspect the durable store files."""
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import time
import zipfile

import pytest
import uv

from microclaw import skill_packages as packages, skill_store as store, updates

FIXTURES = Path(__file__).parent / "fixtures" / "skill_packages"
_spec = importlib.util.spec_from_file_location("build_release", FIXTURES / "build_release.py")
builder = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(builder)
NOW = datetime(2026, 9, 23, tzinfo=timezone.utc)
BASE = sys._base_executable
WHEELS = FIXTURES / "wheels"
REAL_HOME = store.user_data_dir()


def read(path):
    return updates.load_state(path)


def write(path, value):
    updates.write_state(value, path)


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "cache"))
    write(store.store_dir() / "trust" / "roots.json", read(FIXTURES / "trust" / "roots-TEST-ONLY.json"))
    store.store_trust_policy(read(FIXTURES / "trust" / "policy-TEST-ONLY.json"))


def install(tmp_path, *, kind="executable", version="1.0.0", source=None, **kwargs):
    artifact = tmp_path / (kind + "-" + version + ".zip")
    intake = builder.build_release(source or FIXTURES / kind, artifact, version=version)
    return store.install(intake, artifact, policy=store.load_trust_policy(), now=NOW,
                         uv_executable=uv.find_uv_bin(), retained_digests=frozenset(),
                         find_links=WHEELS.resolve(), base_python=BASE, **kwargs)


def package(kind="executable"):
    return store.store_dir() / "packages" / (kind + "-fixture")


def directory(record, kind="executable"):
    return package(kind) / "installs" / record["install_id"]


def state(kind="executable"):
    return next(p for p in store.status()["packages"] if p["package_id"] == kind + "-fixture")


def mutate_source(tmp_path, change):
    source = tmp_path / "source"
    shutil.copytree(FIXTURES / "executable", source)
    manifest = read(source / "manifest.json")
    change(manifest)
    write(source / "manifest.json", manifest)
    return source


def test_wheel_and_release_are_reproducible_and_intake_still_verifies(tmp_path):
    assert builder.build_wheel() == (WHEELS / builder.WHEEL_NAME).read_bytes()
    manifest = read(FIXTURES / "executable" / "manifest.json")
    digest = hashlib.sha256(builder.build_wheel()).hexdigest()
    assert all(lock == [dict(requirement="fixture-dependency==1.2.3", hashes=[digest])]
               for lock in manifest["locks"].values())
    intake = read(FIXTURES / "executable-intake.json")
    packages.validate_intake(intake)
    packages._bound_release(manifest, intake)
    packages.check_release(intake, store.load_trust_policy(), purpose="execution", now=NOW)
    a, b = tmp_path / "a.zip", tmp_path / "b.zip"
    one = builder.build_release(FIXTURES / "executable", a, version="1.2.0")
    two = builder.build_release(FIXTURES / "executable", b, version="1.2.0")
    assert one == two and a.read_bytes() == b.read_bytes()
    assert one["version"] == "1.2.0"
    packages.check_release(one, store.load_trust_policy(), purpose="admission", now=NOW, artifact=a)
    with zipfile.ZipFile(a) as z:
        assert json.loads(z.read("manifest.json"))["version"] == "1.2.0"


def test_real_install_is_isolated_and_pins_are_installed(tmp_path):
    # Capture names/stat metadata only; never create anything in the real home.
    real_root = REAL_HOME / "skill-packages"
    before = {str(p): p.lstat().st_mtime_ns for p in real_root.rglob("*")} if real_root.exists() else {}
    record = install(tmp_path)
    saved = read(directory(record) / "install.json")
    assert saved == record and record["state"] == "ready"
    assert read(package() / "pointer.json") == dict(active=record["install_id"], previous=None)
    assert record["interpreter"]["distributions"] == {"fixture-dependency": "1.2.3"}
    assert record["self_check"]["state"] == "succeeded"
    assert read(directory(record) / "verdict.json")["eligible"]
    assert state()["installs"][0]["eligible"]
    def strings(value):
        if isinstance(value, dict):
            for v in value.values():
                yield from strings(v)
        elif isinstance(value, list):
            for v in value:
                yield from strings(v)
        elif isinstance(value, str):
            yield value
    for path in store.store_dir().rglob("*.json"):
        for value in strings(read(path)):
            if Path(value).is_absolute():
                assert any(Path(value).is_relative_to(root) for root in
                           [store.store_dir(), Path(BASE).resolve().parent.parent, WHEELS.resolve()]), value
    after = {str(p): p.lstat().st_mtime_ns for p in real_root.rglob("*")} if real_root.exists() else {}
    assert before == after


def test_failed_self_check_keeps_old_active_and_record(tmp_path):
    old = install(tmp_path)
    source = mutate_source(tmp_path, lambda m: None)
    runner = source / "fixture_worker" / "runner.py"
    runner.write_text("raise RuntimeError('fixture self check failed')\n", encoding="utf-8")
    manifest = read(source / "manifest.json")
    for asset in manifest["assets"]:
        if asset["path"] == "fixture_worker/runner.py":
            asset["sha256"] = hashlib.sha256(runner.read_bytes()).hexdigest()
    write(source / "manifest.json", manifest)
    with pytest.raises(store.PackageRefusal, match="self_check"):
        install(tmp_path, version="2.0.0", source=source)
    assert read(package() / "pointer.json")["active"] == old["install_id"]
    failed = next(row for row in state()["installs"] if row["state"] == "failed")
    saved = read(directory(failed) / "install.json")
    assert saved["self_check"]["state"] != "succeeded"
    assert saved["reasons"][0]["field"] == "self_check"


def test_rollback_repair_deleted_python_and_remove(tmp_path):
    one, two = install(tmp_path), install(tmp_path, version="2.0.0")
    policy = store.load_trust_policy()
    store.rollback("executable-fixture", policy=policy, now=NOW, retained_digests=frozenset())
    assert read(package() / "pointer.json") == dict(active=one["install_id"], previous=two["install_id"])
    Path(one["python"]).unlink()
    assert next(row for row in state()["installs"] if row["install_id"] == one["install_id"])["eligible"]
    store.recheck(now=NOW, retained_digests=frozenset())
    verdict = read(directory(one) / "verdict.json")
    assert not verdict["eligible"] and verdict["reasons"][0]["field"] == "interpreter"
    assert any(r["field"] == "interpreter" for row in state()["installs"] for r in row["reasons"])
    repaired = store.repair("executable-fixture", policy=policy, now=NOW,
                            uv_executable=uv.find_uv_bin(), retained_digests=frozenset(), base_python=BASE)
    assert repaired["install_id"] != one["install_id"]
    assert read(package() / "pointer.json") == dict(active=repaired["install_id"], previous=two["install_id"])
    assert store.probe(repaired["python"]) == repaired["interpreter"]
    assert repaired["find_links"] == one["find_links"]
    assert repaired["self_check"]["state"] == "succeeded"
    for install_id in [None, repaired["install_id"]]:
        with pytest.raises(store.PackageRefusal, match="retained"):
            store.remove("executable-fixture", install_id=install_id, retained_digests={repaired["artifact_digest"]})
    with pytest.raises(store.PackageRefusal, match="active"):
        store.remove("executable-fixture", install_id=repaired["install_id"], retained_digests=frozenset())
    store.remove("executable-fixture", install_id=two["install_id"], retained_digests=frozenset())
    assert not directory(two).exists()
    assert read(package() / "pointer.json")["previous"] is None
    store.remove("executable-fixture", retained_digests=frozenset())
    assert not package().exists()


def test_markdown_never_launches_python(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Markdown must never launch a subprocess or construct a supervisor")
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(store, "Supervisor", forbidden)
    one = install(tmp_path, kind="markdown")
    two = install(tmp_path, kind="markdown", version="2.0.0")
    policy = store.load_trust_policy()
    store.rollback("markdown-fixture", policy=policy, now=NOW, retained_digests=frozenset())
    repaired = store.repair("markdown-fixture", policy=policy, now=NOW, retained_digests=frozenset())
    store.recheck(now=NOW, retained_digests=frozenset())
    assert read(package("markdown") / "pointer.json") == dict(active=repaired["install_id"], previous=two["install_id"])
    for path in package("markdown").glob("installs/*"):
        assert not (path / "env").exists() and not (path / "requirements.txt").exists()
        assert read(path / "install.json")["interpreter"] is None
    store.remove("markdown-fixture", retained_digests=frozenset())
    assert not package("markdown").exists()


def test_digest_resolution_never_substitutes(tmp_path):
    one = install(tmp_path, kind="markdown")
    two = install(tmp_path, kind="markdown", version="2.0.0")
    resolved = store.resolve("markdown-fixture", one["artifact_digest"], now=NOW)
    assert resolved["install_id"] == one["install_id"]
    assert read(package("markdown") / "pointer.json")["active"] == two["install_id"]
    store.remove("markdown-fixture", install_id=one["install_id"], retained_digests=frozenset())
    with pytest.raises(store.PackageRefusal, match="artifact_digest"):
        store.resolve("markdown-fixture", one["artifact_digest"], now=NOW)


@pytest.mark.parametrize("change", ["build", "interpreter", "same-build"])
def test_verdict_structural_invalidation(tmp_path, monkeypatch, change):
    record = install(tmp_path, kind="markdown")
    target = directory(record, "markdown")
    verdict = read(target / "verdict.json")
    if change == "build":
        monkeypatch.setattr(store, "current_build", lambda: dict(verdict["build"], version="2.0.0"))
    elif change == "interpreter":
        saved = read(target / "install.json")
        saved["interpreter"] = {"changed": True}
        write(target / "install.json", saved)
    row = state("markdown")["installs"][0]
    if change == "same-build":
        assert row["eligible"] and not row["reasons"]
        (target / "release" / "SKILL.md").write_text("broken", encoding="utf-8")
        assert state("markdown")["installs"][0]["eligible"]
    else:
        assert not row["eligible"] and row["reasons"][0]["field"] == "unchecked"
    store.recheck(now=NOW, retained_digests=frozenset())
    updated = read(target / "verdict.json")
    assert updated != verdict
    if change == "build":
        assert not updated["eligible"] and updated["reasons"][0]["field"] == "microclaw"
    if change == "same-build":
        assert not updated["eligible"] and updated["reasons"][0]["field"] == "assets"


class Crash(BaseException):
    """Simulate process death, bypassing transaction exception handling."""


@pytest.mark.parametrize("point", ["extraction", "staged", "temporary", "ready", "activated"])
def test_interrupted_transaction_recovery_rows(tmp_path, monkeypatch, point):
    old = install(tmp_path, kind="markdown")
    real_extract, real_write = store._extract, store._write
    candidate = []
    def extract(artifact, target, intake):
        candidate.append(target)
        if point == "extraction":
            raise Crash()
        return real_extract(artifact, target, intake)
    def writing(path, doc):
        if candidate and path == candidate[0] / "install.json" and doc["state"] == "staged" and point == "staged":
            real_write(path, doc)
            raise Crash()
        if path.name == "pointer.json" and doc["active"] != old["install_id"] and point in {"temporary", "ready", "activated"}:
            if point == "temporary":
                (path.parent / ".pointer.json.interrupted").write_bytes(b'{"active":')
            if point == "activated":
                real_write(path, doc)
            raise Crash()
        return real_write(path, doc)
    with monkeypatch.context() as m:
        m.setattr(store, "_extract", extract)
        m.setattr(store, "_write", writing)
        with pytest.raises(Crash):
            install(tmp_path, kind="markdown", version="2.0.0")
    store.recover(retained_digests=frozenset())
    pointer = read(package("markdown") / "pointer.json")
    if point == "activated":
        assert pointer == dict(active=candidate[0].name, previous=old["install_id"])
        assert read(candidate[0] / "install.json")["state"] == "ready"
    else:
        assert pointer["active"] == old["install_id"]
        if point == "staged":
            saved = read(candidate[0] / "install.json")
            assert saved["state"] == "failed" and saved["reasons"][0]["field"] == "interrupted"
        else:
            assert not candidate[0].exists()
    assert not list(package("markdown").glob(".pointer.json.*"))


@pytest.mark.parametrize("damage", ["missing", "failed", "unreadable"])
def test_damaged_pointer_never_falls_back(tmp_path, damage):
    one = install(tmp_path, kind="markdown")
    two = install(tmp_path, kind="markdown", version="2.0.0")
    if damage == "missing":
        shutil.rmtree(directory(two, "markdown"))
    elif damage == "failed":
        two["state"] = "failed"
        write(directory(two, "markdown") / "install.json", two)
    else:
        (package("markdown") / "pointer.json").write_bytes(b"{")
    before = (package("markdown") / "pointer.json").read_bytes()
    store.recover(retained_digests=frozenset())
    assert (package("markdown") / "pointer.json").read_bytes() == before
    assert directory(one, "markdown").exists()
    assert state("markdown")["broken"][0]["field"] == "pointer"
    assert not any(row["eligible"] for row in state("markdown")["installs"])


@pytest.mark.parametrize("owner", ["dead", "live", "missing-old", "missing-new", "unreadable-old"])
def test_package_lock_stale_and_live(tmp_path, monkeypatch, owner):
    installed = install(tmp_path, kind="markdown")
    lock = package("markdown") / ".lock"
    lock.mkdir()
    if owner in {"dead", "live"}:
        write(lock / "owner.json", dict(pid=os.getpid(), nonce="leftover", created_at=0))
        if owner == "dead":
            monkeypatch.setattr(store, "_alive", lambda pid: False)
    elif owner == "unreadable-old":
        (lock / "owner.json").write_bytes(b"{")
    if owner.endswith("old"):
        os.utime(lock, (time.time() - 100, time.time() - 100))
    before = time.monotonic()
    result = store.recover(retained_digests=frozenset())
    if owner in {"live", "missing-new"}:
        assert time.monotonic() - before < 1
    if owner in {"live", "missing-new"}:
        assert result[0]["reasons"][0]["field"] == "lock"
        with pytest.raises(store.PackageRefusal, match="lock"):
            store.remove("markdown-fixture", retained_digests=frozenset())
    else:
        assert not lock.exists()
        assert not list(package("markdown").glob(".lock-stale-*"))
    assert read(package("markdown") / "pointer.json")["active"] == installed["install_id"]


def test_deletion_failure_retried_and_retention(tmp_path, monkeypatch):
    one = install(tmp_path, kind="markdown")
    install(tmp_path, kind="markdown", version="2.0.0")
    artifact = tmp_path / "three.zip"
    intake = builder.build_release(FIXTURES / "markdown", artifact, version="3.0.0")
    store.install(intake, artifact, policy=store.load_trust_policy(), now=NOW,
                  retained_digests={one["artifact_digest"]})
    assert directory(one, "markdown").exists()
    real = shutil.rmtree
    def locked(path, *args, **kwargs):
        if Path(path) == directory(one, "markdown"):
            raise PermissionError("Windows file locked")
        return real(path, *args, **kwargs)
    with monkeypatch.context() as m:
        m.setattr(shutil, "rmtree", locked)
        store.recover(retained_digests=frozenset())
    assert read(package("markdown") / "recovery.json")["deletion_failures"]
    assert state("markdown")["deletion_failures"]
    store.recover(retained_digests=frozenset())
    assert not directory(one, "markdown").exists()
    assert not read(package("markdown") / "recovery.json")["deletion_failures"]


@pytest.mark.parametrize("malice", ["undeclared", "symlink", "traversal", "oversize", "duplicate", "encrypted", "manifest-size", "member-count"])
def test_archive_refusals_are_durable(tmp_path, malice):
    artifact = tmp_path / "bad.zip"
    intake = builder.build_release(FIXTURES / "markdown", artifact)
    with zipfile.ZipFile(artifact, "a") as archive:
        if malice == "undeclared":
            archive.writestr("hidden.py", b"bad")
        elif malice == "symlink":
            info = zipfile.ZipInfo("link")
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, b"SKILL.md")
        elif malice == "traversal":
            archive.writestr("../escape", b"bad")
        elif malice == "duplicate":
            with pytest.warns(UserWarning, match="Duplicate name"):
                archive.writestr("SKILL.md", b"duplicate")
        elif malice == "member-count":
            for i in range(1025):
                archive.writestr(str(i) + "/", b"")
    if malice in {"oversize", "manifest-size", "encrypted"}:
        data = bytearray(artifact.read_bytes())
        index = data.index(b"PK\x01\x02")
        if malice == "encrypted":
            data[index + 8:index + 10] = (1).to_bytes(2, "little")
        else:
            if malice == "manifest-size":
                with zipfile.ZipFile(artifact) as z:
                    infos = z.infolist()
                for info in infos:
                    if info.filename == "manifest.json":
                        break
                    index = data.index(b"PK\x01\x02", index + 4)
            data[index + 24:index + 28] = (store.MAX_ARCHIVE_BYTES + 1 if malice == "oversize" else store.MAX_MANIFEST_BYTES + 1).to_bytes(4, "little")
        artifact.write_bytes(data)
    intake["artifact_digest"] = hashlib.sha256(artifact.read_bytes()).hexdigest()
    intake = builder.sign(intake)
    with pytest.raises(store.PackageRefusal):
        store.install(intake, artifact, policy=store.load_trust_policy(), now=NOW, retained_digests=frozenset())
    row = state("markdown")["installs"][0]
    assert read(directory(row, "markdown") / "install.json")["state"] == "failed"
    assert read(package("markdown") / "pointer.json")["active"] is None
    assert not (tmp_path / "escape").exists()


@pytest.mark.parametrize("failure", ["hash", "sdist"])
def test_real_uv_refuses_wrong_hash_and_sdist(tmp_path, failure, monkeypatch):
    if failure == "hash":
        source = mutate_source(tmp_path, lambda manifest: [item.update(hashes=["a" * 64]) for lock in manifest["locks"].values() for item in lock])
    else:
        import tarfile
        import io
        wheel_dir = tmp_path / "sdists"
        wheel_dir.mkdir()
        archive = wheel_dir / "fixture_dependency-1.2.3.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            content = b"raise RuntimeError('must never build')\n"
            info = tarfile.TarInfo("fixture_dependency-1.2.3/setup.py")
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        source = mutate_source(tmp_path, lambda manifest: [item.update(hashes=[digest]) for lock in manifest["locks"].values() for item in lock])
        monkeypatch.setattr(sys.modules[__name__], "WHEELS", wheel_dir)
    with pytest.raises(store.PackageRefusal, match="locks"):
        install(tmp_path, source=source)
    saved = read(directory(state()["installs"][0]) / "install.json")
    assert saved["state"] == "failed" and saved["reasons"][0]["field"] == "locks"
    assert "exit=" in saved["reasons"][0]["detail"]
    assert read(package() / "pointer.json")["active"] is None


def test_provision_argv_and_child_only_environment(tmp_path, monkeypatch):
    calls = []
    original_env = dict(os.environ)
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, str(store.store_dir() / "python" / "base") + "\n", "")
    monkeypatch.setattr(subprocess, "run", run)
    store.provision_python(uv_executable="fixture-uv")
    assert calls[0][0] == ["fixture-uv", "python", "install", "--no-config", "--no-bin", "--no-registry",
                            "--install-dir", str(store.store_dir() / "python"), "3.12"]
    assert calls[1][0] == ["fixture-uv", "python", "find", "--no-config", "--managed-python", "--no-python-downloads", "3.12"]
    assert "UV_PYTHON_INSTALL_DIR" not in calls[0][1]["env"]
    assert calls[1][1]["env"]["UV_PYTHON_INSTALL_DIR"] == str(store.store_dir() / "python")
    store._run(["another", "child"], timeout=30, field="interpreter")
    assert "UV_PYTHON_INSTALL_DIR" not in calls[2][1]["env"]
    for _, kwargs in calls:
        assert kwargs["stdin"] == subprocess.DEVNULL and kwargs["capture_output"] and kwargs["timeout"] in {600, 30}
    assert dict(os.environ) == original_env


@pytest.mark.parametrize("mode", ["failure", "timeout"])
def test_subprocess_failure_records_environment_and_stderr(monkeypatch, mode):
    monkeypatch.setenv("UV_INDEX_URL", "https://secret@example.invalid")
    def run(argv, **kwargs):
        if mode == "timeout":
            raise subprocess.TimeoutExpired(argv, 30, stderr=b"tail")
        return subprocess.CompletedProcess(argv, 12, "", "x" * 2001 + "tail")
    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(store.PackageRefusal) as caught:
        store._run(["uv", "pip"], timeout=30, field="locks")
    message = str(caught.value)
    assert "uv pip exit=" in message and "tail" in message and "UV_INDEX_URL" in message
    assert "secret" not in message and "x" * 2001 not in message


@pytest.mark.parametrize("environment", ["test", "production", "other"])
def test_roots_only_configurable_for_test(tmp_path, environment):
    record = install(tmp_path, kind="markdown")
    path = store.store_dir() / "trust" / "roots.json"
    roots = read(path)
    roots["environment"] = environment
    write(path, roots)
    result = store.status()
    assert result["trust"]["test_roots_active"] is (environment == "test")
    if environment != "test":
        assert result["trust"]["reason"] == "production roots are not configurable"
        assert not result["packages"][0]["installs"][0]["eligible"]
        assert result["packages"][0]["installs"][0]["reasons"][0]["field"] == "trust"
    assert read(directory(record, "markdown") / "install.json")["state"] == "ready"


def test_policy_every_load_verified_monotonic_and_stale_repair_refuses(tmp_path):
    record = install(tmp_path, kind="markdown")
    path = store.store_dir() / "trust" / "policy.json"
    policy = read(path)
    newer = builder.sign(dict(policy, revision=2), key="root")
    store.store_trust_policy(newer)
    with pytest.raises(store.PackageRefusal, match="revision"):
        store.store_trust_policy(policy)
    assert read(path) == newer
    with pytest.raises(store.PackageRefusal, match="expired"):
        store.repair("markdown-fixture", policy=store.load_trust_policy(), now=datetime(2031, 1, 1, tzinfo=timezone.utc), retained_digests=frozenset())
    assert read(package("markdown") / "pointer.json")["active"] == record["install_id"]
    write(path, dict(newer, revision=3))
    with pytest.raises(store.PackageRefusal, match="trust"):
        store.load_trust_policy()
    assert not state("markdown")["installs"][0]["eligible"]


def test_only_latest_failed_attempt_is_retained(tmp_path, monkeypatch):
    active = install(tmp_path, kind="markdown")
    def fail(*args):
        raise store.PackageRefusal("assets", "fixture extraction failed")
    monkeypatch.setattr(store, "_extract", fail)
    for version in ["2.0.0", "3.0.0"]:
        with pytest.raises(store.PackageRefusal):
            install(tmp_path, kind="markdown", version=version)
    failures = [row for row in state("markdown")["installs"] if row["state"] == "failed"]
    assert len(failures) == 1
    assert read(directory(failures[0], "markdown") / "install.json")["intake"]["version"] == "3.0.0"
    assert read(package("markdown") / "pointer.json")["active"] == active["install_id"]


def test_repair_retained_digest_resolves_healthy_attempt(tmp_path):
    old = install(tmp_path)
    Path(old["python"]).unlink()
    store.recheck(now=NOW, retained_digests={old["artifact_digest"]})
    repaired = store.repair("executable-fixture", policy=store.load_trust_policy(), now=NOW,
                            retained_digests={old["artifact_digest"]}, uv_executable=uv.find_uv_bin(), base_python=BASE)
    assert directory(old).exists() and directory(repaired).exists()
    resolved = store.resolve("executable-fixture", old["artifact_digest"], now=NOW)
    assert resolved["install_id"] == repaired["install_id"]
    assert not read(directory(old) / "verdict.json")["eligible"]
    assert read(directory(repaired) / "verdict.json")["eligible"]


def test_rollback_failure_does_not_move_pointer(tmp_path):
    old = install(tmp_path, kind="markdown")
    install(tmp_path, kind="markdown", version="2.0.0")
    pointer = read(package("markdown") / "pointer.json")
    (directory(old, "markdown") / "release" / "SKILL.md").write_bytes(b"broken")
    with pytest.raises(store.PackageRefusal, match="assets"):
        store.rollback("markdown-fixture", policy=store.load_trust_policy(), now=NOW, retained_digests=frozenset())
    assert read(package("markdown") / "pointer.json") == pointer
    target = read(directory(old, "markdown") / "install.json")
    assert target["reasons"][0]["field"] == "assets"
    assert not read(directory(old, "markdown") / "verdict.json")["eligible"]


def test_startup_recheck_probes_only_and_status_reads_only(tmp_path, monkeypatch):
    record = install(tmp_path)
    real_run = subprocess.run
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        assert argv == [record["python"], "-I", "-c", store.PROBE]
        return real_run(argv, **kwargs)
    monkeypatch.setattr(store, "Supervisor", lambda **kwargs: pytest.fail("startup publisher execution"))
    monkeypatch.setattr(subprocess, "run", run)
    store.recheck(now=NOW, retained_digests=frozenset())
    assert len(calls) == 1 and read(directory(record) / "verdict.json")["eligible"]
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("status subprocess"))
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("status subprocess"))
    monkeypatch.setattr(store, "package_lock", lambda *a, **k: pytest.fail("status lock"))
    assert state()["installs"][0]["eligible"]


def test_dedicated_supervisor_bounds_and_duplicate_admission(tmp_path, monkeypatch):
    real = store.Supervisor
    calls = []
    def supervisor(**kwargs):
        calls.append(kwargs)
        return real(**kwargs)
    monkeypatch.setattr(store, "Supervisor", supervisor)
    record = install(tmp_path)
    assert calls == [dict(max_workers=1, max_queued=1)]
    with pytest.raises(store.PackageRefusal, match="already installed; repair or roll back"):
        install(tmp_path)
    assert read(package() / "pointer.json")["active"] == record["install_id"]
    assert len(list((package() / "installs").iterdir())) == 1


def test_missing_policy_and_revocation_refuse_without_deleting(tmp_path):
    record = install(tmp_path, kind="markdown")
    policy = store.load_trust_policy()
    policy["revision"] += 1
    policy["revoked_releases"] = [{key: record["intake"][key] for key in ("package_id", "version", "artifact_digest")}]
    store.store_trust_policy(builder.sign(policy, key="root"))
    with pytest.raises(store.PackageRefusal, match="revoked"):
        store.resolve("markdown-fixture", record["artifact_digest"], now=NOW)
    (store.store_dir() / "trust" / "policy.json").unlink()
    assert state("markdown")["installs"][0]["reasons"][0]["field"] == "trust"
    store.recheck(now=NOW, retained_digests=frozenset())
    assert read(directory(record, "markdown") / "verdict.json")["reasons"][0]["field"] == "trust"
    assert read(directory(record, "markdown") / "install.json")["state"] == "ready"


@pytest.mark.parametrize("field", ["python", "platforms", "microclaw"])
def test_compatibility_refusals(tmp_path, field):
    def change(manifest):
        if field == "platforms":
            manifest["platforms"] = ["unsupported"]
            manifest["locks"] = {"unsupported": []}
        else:
            manifest[field] = ">=99"
    source = mutate_source(tmp_path, change)
    with pytest.raises(store.PackageRefusal) as caught:
        install(tmp_path, source=source)
    assert caught.value.field == field
    saved = read(directory(state()["installs"][0]) / "install.json")
    assert saved["state"] == "failed" and saved["reasons"][0]["field"] == field
    assert read(package() / "pointer.json")["active"] is None


def test_real_process_death_leaves_stale_lock_and_unrecorded_install(tmp_path):
    old = install(tmp_path, kind="markdown")
    artifact = tmp_path / "killed.zip"
    intake = builder.build_release(FIXTURES / "markdown", artifact, version="2.0.0")
    script = (
        "import os\nfrom datetime import datetime, timezone\n"
        "from microclaw import skill_store as s\n"
        "s._extract = lambda *a: os._exit(7)\n"
        f"s.install({intake!r}, {str(artifact)!r}, policy=s.load_trust_policy(), "
        "now=datetime(2026,9,23,tzinfo=timezone.utc), retained_digests=frozenset())\n"
    )
    child = subprocess.run([sys.executable, "-c", script], stdin=subprocess.DEVNULL,
                           capture_output=True, timeout=30)
    assert child.returncode == 7, child.stderr
    owner = read(package("markdown") / ".lock" / "owner.json")
    assert not store._alive(owner["pid"])
    leftovers = [p for p in (package("markdown") / "installs").iterdir() if p.name != old["install_id"]]
    assert len(leftovers) == 1 and not (leftovers[0] / "install.json").exists()
    store.recover(retained_digests=frozenset())
    assert not leftovers[0].exists() and not (package("markdown") / ".lock").exists()
    assert read(package("markdown") / "pointer.json")["active"] == old["install_id"]
    assert not read(package("markdown") / "recovery.json")["deletion_failures"]


def test_archive_actual_size_mismatch_names_member(tmp_path):
    artifact = tmp_path / "truncated.zip"
    intake = builder.build_release(FIXTURES / "markdown", artifact)
    data = bytearray(artifact.read_bytes())
    central = data.index(b"PK\x01\x02")
    data[central + 24:central + 28] = (1).to_bytes(4, "little")
    artifact.write_bytes(data)
    intake = builder.sign(dict(intake, artifact_digest=hashlib.sha256(data).hexdigest()))
    with pytest.raises(store.PackageRefusal) as caught:
        store.install(intake, artifact, policy=store.load_trust_policy(), now=NOW, retained_digests=frozenset())
    assert caught.value.field == "SKILL.md"
    saved = read(directory(state("markdown")["installs"][0], "markdown") / "install.json")
    assert saved["state"] == "failed" and saved["reasons"][0]["field"] == "SKILL.md"


@pytest.mark.parametrize("after_replace", [False, True])
def test_process_death_at_real_atomic_activation(tmp_path, after_replace):
    old = install(tmp_path, kind="markdown")
    artifact = tmp_path / "activation.zip"
    intake = builder.build_release(FIXTURES / "markdown", artifact, version="2.0.0")
    pointer_file = package("markdown") / "pointer.json"
    script = (
        "import os\nfrom pathlib import Path\nfrom datetime import datetime, timezone\n"
        "from microclaw import skill_store as s\n"
        "replace = os.replace\n"
        "def interrupt(source, target):\n"
        f" if Path(target) == Path({str(pointer_file)!r}):\n"
        f"  if {after_replace!r}: replace(source, target)\n"
        "  os._exit(7)\n"
        " return replace(source, target)\n"
        "os.replace = interrupt\n"
        f"s.install({intake!r}, {str(artifact)!r}, policy=s.load_trust_policy(), "
        "now=datetime(2026,9,23,tzinfo=timezone.utc), retained_digests=frozenset())\n"
    )
    child = subprocess.run([sys.executable, "-c", script], stdin=subprocess.DEVNULL,
                           capture_output=True, timeout=30)
    assert child.returncode == 7, child.stderr
    candidate = next(p for p in (package("markdown") / "installs").iterdir() if p.name != old["install_id"])
    assert read(candidate / "install.json")["state"] == "ready"
    if not after_replace:
        assert len(list(package("markdown").glob(".pointer.json.*"))) == 1
        assert read(pointer_file)["active"] == old["install_id"]
    store.recover(retained_digests=frozenset())
    assert read(pointer_file)["active"] == (candidate.name if after_replace else old["install_id"])
    assert candidate.exists() is after_replace
    assert not list(package("markdown").glob(".pointer.json.*"))
    assert not (package("markdown") / ".lock").exists()


def test_repair_named_nonactive_failed_previous(tmp_path):
    old = install(tmp_path, kind="markdown")
    active = install(tmp_path, kind="markdown", version="2.0.0")
    old["state"] = "failed"
    write(directory(old, "markdown") / "install.json", old)
    assert state("markdown")["broken"]
    repaired = store.repair("markdown-fixture", install_id=old["install_id"],
                            policy=store.load_trust_policy(), now=NOW, retained_digests=frozenset())
    assert read(package("markdown") / "pointer.json") == dict(active=repaired["install_id"], previous=active["install_id"])
    assert not state("markdown")["broken"]
