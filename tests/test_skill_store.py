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
    marker = tmp_path / "source-build-ran"
    if failure == "hash":
        source = mutate_source(tmp_path, lambda manifest: [item.update(hashes=["a" * 64]) for lock in manifest["locks"].values() for item in lock])
    else:
        import tarfile
        import io
        wheel_dir = tmp_path / "sdists"
        wheel_dir.mkdir()
        archive = wheel_dir / "fixture_dependency-1.2.3.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            content = f"from pathlib import Path\nPath({str(marker)!r}).write_bytes(b'built')\n".encode("utf-8")
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
    if failure == "sdist":
        assert not marker.exists()
        detail = saved["reasons"][0]["detail"].lower()
        assert "no usable wheels" in detail or "--no-build" in detail, detail
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
        child_env = store._uv_env()
        child_env["UV_PYTHON_INSTALL_DIR"] = str(store.store_dir() / "python")
        child_env["UV_CHILD_ONLY"] = "not a user override"
        store._run(["uv", "pip"], timeout=30, field="locks", env=child_env)
    message = str(caught.value)
    assert "uv pip exit=" in message and "tail" in message and "UV_INDEX_URL" in message
    assert "secret" not in message and "x" * 2001 not in message
    assert "UV_CACHE_DIR" not in message and "UV_PYTHON_INSTALL_DIR" not in message
    assert "UV_CHILD_ONLY" not in message


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
    policy["revoked_releases"] = [{key: record["intake"][key] for key in ("package_id", "version", "artifact_digest")} | {"reason": "Test block"}]
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
        "import os\nfrom pathlib import Path\nfrom datetime import datetime, timezone\n"
        "from microclaw import skill_store as s\n"
        f"s.store_dir = lambda: Path({str(store.store_dir())!r})\n"
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
        f"s.store_dir = lambda: Path({str(store.store_dir())!r})\n"
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
    assert read(package("markdown") / "pointer.json") == dict(active=active["install_id"], previous=repaired["install_id"])
    assert not state("markdown")["broken"]


@pytest.mark.parametrize("target", ["previous", "neither"])
def test_repair_preserves_active_release(tmp_path, target):
    old = install(tmp_path, kind="markdown")
    active = install(tmp_path, kind="markdown", version="2.0.0")
    pointer = read(package("markdown") / "pointer.json")
    if target == "neither":
        pointer["previous"] = None
        write(package("markdown") / "pointer.json", pointer)
    repaired = store.repair("markdown-fixture", install_id=old["install_id"],
                            policy=store.load_trust_policy(), now=NOW,
                            retained_digests={old["artifact_digest"]})
    assert read(directory(repaired, "markdown") / "install.json")["state"] == "ready"
    expected = dict(pointer, previous=repaired["install_id"]) if target == "previous" else pointer
    assert read(package("markdown") / "pointer.json") == expected
    assert expected["active"] == active["install_id"]


def test_partial_previous_removal_preserves_active_eligibility(tmp_path, monkeypatch):
    old = install(tmp_path, kind="markdown")
    active = install(tmp_path, kind="markdown", version="2.0.0")
    old_dir = directory(old, "markdown")
    original = shutil.rmtree
    def partial(path, *args, **kwargs):
        if Path(path) == old_dir:
            (old_dir / "install.json").unlink()
            raise PermissionError("locked artifact.zip")
        return original(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(shutil, "rmtree", partial)
        store.remove("markdown-fixture", install_id=old["install_id"], retained_digests=frozenset())
    assert read(package("markdown") / "pointer.json") == dict(active=active["install_id"], previous=None)
    snapshot = state("markdown")
    assert not snapshot["broken"] and snapshot["deletion_failures"]
    assert next(row for row in snapshot["installs"] if row["active"])["eligible"]
    store.recover(retained_digests=frozenset())
    assert not old_dir.exists()
    assert not state("markdown")["deletion_failures"]


def test_stale_lock_race_preserves_replacement_owner(tmp_path, monkeypatch):
    install(tmp_path, kind="markdown")
    lock = package("markdown") / ".lock"
    lock.mkdir()
    write(lock / "owner.json", dict(pid=123, nonce="dead", created_at=0))
    monkeypatch.setattr(store, "_alive", lambda pid: False)
    original = Path.rename
    live_owner = dict(pid=os.getpid(), nonce="live", created_at=time.time())
    def replace_before_rename(path, target):
        if path == lock:
            shutil.rmtree(lock)
            lock.mkdir()
            write(lock / "owner.json", live_owner)
        return original(path, target)
    monkeypatch.setattr(Path, "rename", replace_before_rename)
    with pytest.raises(store.PackageRefusal) as caught:
        with store.package_lock("markdown-fixture"):
            pytest.fail("must not acquire the replacement lock")
    assert caught.value.field == "lock"
    assert read(lock / "owner.json") == live_owner


@pytest.mark.parametrize("mismatch", ["extra", "missing"])
def test_distribution_mismatch_fails_transaction(tmp_path, monkeypatch, mismatch):
    original_probe, original_run = store.probe, store._run
    calls = []
    def probe(python):
        identity = original_probe(python)
        calls.append(identity)
        if mismatch == "extra" and len(calls) == 2:
            identity["distributions"]["unexpected-distribution"] = "1.0"
        return identity
    def run(argv, **kwargs):
        if mismatch == "missing" and argv[1:3] == ["pip", "install"]:
            return ""
        return original_run(argv, **kwargs)
    monkeypatch.setattr(store, "probe", probe)
    monkeypatch.setattr(store, "_run", run)
    with pytest.raises(store.PackageRefusal) as caught:
        install(tmp_path)
    assert caught.value.field == "locks"
    assert len(calls) == 2
    assert read(package() / "pointer.json") == dict(active=None, previous=None)
    row = state()["installs"][0]
    saved = read(directory(row) / "install.json")
    assert saved["state"] == "failed" and saved["reasons"][0]["field"] == "locks"


def test_running_interpreter_identity_change_requires_repair(tmp_path):
    installed = install(tmp_path)
    target = directory(installed)
    saved = read(target / "install.json")
    saved["interpreter"]["version"] += " changed base interpreter"
    write(target / "install.json", saved)
    assert store.probe(saved["python"]) != saved["interpreter"]
    store.recheck(now=NOW, retained_digests=frozenset())
    verdict = read(target / "verdict.json")
    assert not verdict["eligible"]
    assert any(reason["field"] == "interpreter" for reason in verdict["reasons"])
    row = state()["installs"][0]
    assert not row["eligible"] and row["reasons"] == verdict["reasons"]
    with pytest.raises(store.PackageRefusal) as caught:
        store.resolve("executable-fixture", installed["artifact_digest"], now=NOW)
    assert caught.value.field == "interpreter"
    repaired = store.repair("executable-fixture", policy=store.load_trust_policy(), now=NOW,
                            uv_executable=uv.find_uv_bin(), base_python=BASE, retained_digests=frozenset())
    assert read(directory(repaired) / "verdict.json")["eligible"]
    assert store.resolve("executable-fixture", installed["artifact_digest"], now=NOW)["install_id"] == repaired["install_id"]


def test_retention_preserves_unreferenced_ready_with_broken_pointer(tmp_path):
    orphan = install(tmp_path, kind="markdown")
    previous = install(tmp_path, kind="markdown", version="2.0.0")
    artifact = tmp_path / "three.zip"
    intake = builder.build_release(FIXTURES / "markdown", artifact, version="3.0.0")
    store.install(intake, artifact, policy=store.load_trust_policy(), now=NOW,
                  retained_digests={orphan["artifact_digest"]})
    assert orphan["install_id"] not in read(package("markdown") / "pointer.json").values()
    shutil.rmtree(directory(previous, "markdown"))
    before = (package("markdown") / "pointer.json").read_bytes()
    store.recover(retained_digests=frozenset())
    assert directory(orphan, "markdown").exists()
    # Recovery has its own damage guard. Pin retention's independent guard too:
    # failed transactions also call retention directly under the package lock.
    with store.package_lock("markdown-fixture") as locked:
        store._retention(locked, retained_digests=frozenset(), failures=[])
    assert directory(orphan, "markdown").exists()
    assert read(directory(orphan, "markdown") / "install.json")["state"] == "ready"
    assert (package("markdown") / "pointer.json").read_bytes() == before


def test_recovery_marks_a_job_left_running_by_an_exited_serve_as_interrupted(tmp_path):
    install(tmp_path, kind="markdown")
    write(package("markdown") / "job.json", dict(operation="repair", running=True, phase="repair", started_at=0))
    store.recover(retained_digests=frozenset())
    job = read(package("markdown") / "job.json")
    assert job["running"] is False and job["phase"] == "interrupted"
    assert job["reasons"] == [dict(field="interrupted", detail="serve exited during repair")]
    assert state("markdown")["job"]["running"] is False


def test_failed_self_check_reason_is_readable_text(tmp_path):
    source = mutate_source(tmp_path, lambda m: None)
    runner = source / "fixture_worker" / "runner.py"
    runner.write_text("raise SystemExit('exits before any terminal result')\n", encoding="utf-8")
    manifest = read(source / "manifest.json")
    for asset in manifest["assets"]:
        if asset["path"] == "fixture_worker/runner.py":
            asset["sha256"] = hashlib.sha256(runner.read_bytes()).hexdigest()
    write(source / "manifest.json", manifest)
    with pytest.raises(store.PackageRefusal) as caught:
        install(tmp_path, source=source)
    assert caught.value.field == "self_check"
    assert str(caught.value) == "self_check: exit_without_terminal: worker exited without terminal"
    saved = read(directory(state()["installs"][0]) / "install.json")
    assert saved["reasons"] == [dict(field="self_check", detail="exit_without_terminal: worker exited without terminal")]


def test_discovery_refresh_load_and_no_unnecessary_work(tmp_path, monkeypatch):
    from microclaw import agent, tools
    from unittest.mock import Mock
    monkeypatch.setattr(agent, "load_knowledge", lambda: {})
    monkeypatch.setattr(agent, "format_for_prompt", lambda _: "fixture KB")
    monkeypatch.setattr(agent, "rig_profile_gaps", lambda _: [])
    record = install(tmp_path, kind="markdown")
    name = "fixture-lab/markdown-fixture/workflow"
    store.set_discovery("markdown-fixture", False, now=NOW)  # installing turned it on
    baseline = agent._system_blocks()
    assert len(baseline) == 2 and baseline[1]["text"] == "fixture KB"
    assert "error" in tools.load_skill(None, None, name)
    decision = store.set_discovery("markdown-fixture", True, now=NOW)
    assert read(package("markdown") / "discovery.json") == decision == dict(
        enabled=True, decided_at="2026-09-23T00:00:00Z", artifact_digest=record["artifact_digest"],
        source="panel")
    real_lock = store.package_lock
    for module, attr in [(subprocess, "run"), (subprocess, "Popen"), (store, "package_lock"),
                         (store, "_start_discovery_recheck")]:
        monkeypatch.setattr(module, attr, Mock(side_effect=AssertionError("unnecessary work")))
    verify = Mock(wraps=store.load_trust_policy)
    monkeypatch.setattr(store, "load_trust_policy", verify)
    blocks = agent._system_blocks()
    assert verify.call_count == 1
    assert blocks[:2] == baseline
    assert blocks[2] == {"type": "text", "text": store.discovery_text()}
    assert name in blocks[2]["text"] and "grants no authority" in blocks[2]["text"]
    assert blocks == agent._system_blocks()
    loaded = tools.load_skill(None, None, name)
    assert loaded["publisher"] == "fixture-lab" and loaded["version"] == "1.0.0"
    assert loaded["artifact_digest"] == record["artifact_digest"]
    assert "Publisher-provided skill" in loaded["text"]
    assert "sha256:" + record["artifact_digest"] in loaded["text"]
    for module, attr in [(subprocess, "run"), (subprocess, "Popen"), (store, "package_lock"),
                         (store, "_start_discovery_recheck")]:
        getattr(module, attr).assert_not_called()
    monkeypatch.setattr(store, "package_lock", real_lock)
    store.set_discovery("markdown-fixture", False, now=NOW)
    assert agent._system_blocks() == baseline
    assert not store.discovery_text()
    assert "error" in tools.load_skill(None, None, name)


def test_discovery_follows_active_release_and_whole_remove(tmp_path):
    first = install(tmp_path, kind="markdown")
    store.set_discovery("markdown-fixture", True, now=NOW)
    decision = read(package("markdown") / "discovery.json")
    second = install(tmp_path, kind="markdown", version="2.0.0")
    assert second["artifact_digest"] in store.discovery_text()
    assert first["artifact_digest"] not in store.discovery_text()
    store.rollback("markdown-fixture", policy=store.load_trust_policy(), now=NOW, retained_digests=frozenset())
    assert first["artifact_digest"] in store.discovery_text()
    assert read(package("markdown") / "discovery.json") == decision
    store.remove("markdown-fixture", retained_digests=frozenset())
    assert not (package("markdown") / "discovery.json").exists()
    assert store.discovery_text() == ""


def test_install_turns_discovery_on_and_an_explicit_off_survives(tmp_path):
    # Operator decision 2026-09-28: installing is the decision to use a package.
    # Only a missing record is ever written, so the panel's "off" is never undone.
    first = install(tmp_path, kind="markdown")
    record = read(package("markdown") / "discovery.json")
    assert record == dict(enabled=True, decided_at="2026-09-23T00:00:00Z",
                          artifact_digest=first["artifact_digest"], source="install")
    assert first["artifact_digest"] in store.discovery_text()
    assert state("markdown")["installs"][0]["discoverable"] is True
    off = store.set_discovery("markdown-fixture", False, now=NOW)
    assert off["source"] == "panel" and store.discovery_text() == ""
    policy = store.load_trust_policy()
    install(tmp_path, kind="markdown", version="2.0.0")  # an update
    assert read(package("markdown") / "discovery.json") == off
    store.rollback("markdown-fixture", policy=policy, now=NOW, retained_digests=frozenset())
    assert read(package("markdown") / "discovery.json") == off
    store.repair("markdown-fixture", policy=policy, now=NOW, retained_digests=frozenset())
    assert read(package("markdown") / "discovery.json") == off
    assert store.discovery_text() == ""
    store.remove("markdown-fixture", retained_digests=frozenset())
    again = install(tmp_path, kind="markdown", version="3.0.0")
    assert read(package("markdown") / "discovery.json")["source"] == "install"
    assert again["artifact_digest"] in store.discovery_text()


def test_failed_install_records_no_discovery_decision(tmp_path):
    source = mutate_source(tmp_path, lambda manifest: manifest.update(microclaw=">=99"))
    with pytest.raises(store.PackageRefusal):
        install(tmp_path, source=source)
    assert not (package() / "discovery.json").exists()


@pytest.mark.parametrize("change", [lambda d: d.pop("source"), lambda d: d.update(source="agent")])
def test_discovery_record_without_a_known_source_is_invalid(tmp_path, change):
    install(tmp_path, kind="markdown")
    path = package("markdown") / "discovery.json"
    record = read(path)
    change(record)
    write(path, record)
    assert store.discovery_text() == ""
    assert state("markdown")["discovery_reasons"][0]["field"] == "discovery"


def test_unchecked_discovery_single_flight_and_later_render(tmp_path, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    record = install(tmp_path, kind="markdown")
    store.set_discovery("markdown-fixture", True, now=NOW)
    (directory(record, "markdown") / "verdict.json").unlink()
    entered, release = threading.Event(), threading.Event()
    real = store.recheck
    calls = []
    def blocked(**kwargs):
        calls.append(kwargs)
        entered.set()
        assert release.wait(10)
        return real(**kwargs)
    monkeypatch.setattr(store, "recheck", blocked)
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            texts = list(pool.map(lambda _: store.discovery_text(), range(16)))
        assert entered.wait(2)
        assert texts == [""] * 16
        assert len(calls) == 1 and calls[0]["retained_digests"] == frozenset()
        assert calls[0]["package_ids"] == frozenset({"markdown-fixture"})
        assert store._discovery_recheck_thread.daemon
    finally:
        release.set()
        store._discovery_recheck_thread.join(10)
    assert "fixture-lab/markdown-fixture/workflow" in store.discovery_text()


def test_locked_unchecked_discovery_retries_later(tmp_path):
    record = install(tmp_path, kind="markdown")
    store.set_discovery("markdown-fixture", True, now=NOW)
    (directory(record, "markdown") / "verdict.json").unlink()
    with store.package_lock("markdown-fixture"):
        assert store.discovery_text() == ""
        store._discovery_recheck_thread.join(10)
        assert not (directory(record, "markdown") / "verdict.json").exists()
    assert store.discovery_text() == ""
    store._discovery_recheck_thread.join(10)
    assert "fixture-lab/markdown-fixture/workflow" in store.discovery_text()


def test_discovery_isolates_bad_record_and_reports_reason(tmp_path):
    good = install(tmp_path, kind="markdown")
    store.set_discovery("markdown-fixture", True, now=NOW)
    # A second signed Markdown fixture, not an executable environment.
    source = tmp_path / "other"
    shutil.copytree(FIXTURES / "markdown", source)
    manifest = read(source / "manifest.json")
    manifest["package_id"] = "other-fixture"
    write(source / "manifest.json", manifest)
    bad = install(tmp_path, kind="markdown", source=source)
    store.set_discovery("other-fixture", True, now=NOW)
    path = store.store_dir() / "packages" / "other-fixture" / "installs" / bad["install_id"] / "install.json"
    bad["manifest"]["skills"] = None
    write(path, bad)
    assert good["artifact_digest"] in store.discovery_text()
    row = next(p for p in store.status()["packages"] if p["package_id"] == "other-fixture")
    assert row["installs"][0]["discovery_exclusions"]
    assert not row["installs"][0]["discoverable"]
    assert "other-fixture/workflow" not in store.discovery_text()


def test_unreadable_store_cannot_abort_turn(tmp_path, monkeypatch):
    from microclaw import agent
    monkeypatch.setattr(agent, "load_knowledge", lambda: {})
    monkeypatch.setattr(agent, "format_for_prompt", lambda _: "")
    monkeypatch.setattr(agent, "rig_profile_gaps", lambda _: [])
    def unreadable():
        raise PermissionError("fixture unreadable store")
    monkeypatch.setattr(store, "store_dir", unreadable)
    assert agent._system_blocks() == [dict(type="text", text=agent.SYSTEM_PROMPT,
        cache_control={"type": "ephemeral", "ttl": "1h"})]


def test_discovery_render_timings(tmp_path, monkeypatch):
    from statistics import median
    from unittest.mock import Mock
    measurements = {}
    for count in range(11):
        if count:
            source = tmp_path / ("fixture-" + str(count))
            shutil.copytree(FIXTURES / "markdown", source)
            manifest = read(source / "manifest.json")
            manifest["package_id"] = "fixture-" + str(count)
            write(source / "manifest.json", manifest)
            install(tmp_path, kind="markdown", source=source)
            store.set_discovery(manifest["package_id"], True, now=NOW)
        if count in (0, 1, 10):
            with monkeypatch.context() as patch:
                checks = []
                for module, attr in [(subprocess, "run"), (subprocess, "Popen"),
                                     (store, "package_lock"), (store, "_start_discovery_recheck")]:
                    mock = Mock(side_effect=AssertionError("unnecessary render work"))
                    patch.setattr(module, attr, mock)
                    checks.append(mock)
                times = []
                for _ in range(30):
                    start = time.perf_counter()
                    text = store.discovery_text()
                    times.append((time.perf_counter() - start) * 1000)
                    assert text.count("\n- ") == count
                for check in checks:
                    check.assert_not_called()
            measurements[count] = round(median(times), 3)
    print("D7 render median milliseconds (30 renders):", measurements)


def test_store_duplicates_exclude_all_carriers(tmp_path):
    from microclaw import tools
    good = install(tmp_path, kind="markdown")
    store.set_discovery("markdown-fixture", True, now=NOW)
    # Corrupt copied store metadata can claim another installed qualified name.
    duplicate = store.store_dir() / "packages" / "duplicate-fixture"
    shutil.copytree(package("markdown"), duplicate)
    text = store.discovery_text()
    assert text == ""
    for row in store.status()["packages"]:
        assert row["installs"][0]["discovery_exclusions"][0]["field"] == "name"
    assert "error" in tools.load_skill(None, None, "fixture-lab/markdown-fixture/workflow")
    # Even an ineligible carrier cannot let the other record win the name.
    verdict_path = duplicate / "installs" / good["install_id"] / "verdict.json"
    verdict = read(verdict_path)
    verdict.update(eligible=False, reasons=[dict(field="microclaw", detail="incompatible")])
    write(verdict_path, verdict)
    assert store.discovery_text() == ""


def test_ineligible_and_disabled_unchecked_records_do_not_start_recheck(tmp_path, monkeypatch):
    from unittest.mock import Mock
    record = install(tmp_path, kind="markdown")
    store.set_discovery("markdown-fixture", False, now=NOW)  # installing turned it on
    verdict_path = directory(record, "markdown") / "verdict.json"
    verdict = read(verdict_path)
    start = Mock(side_effect=AssertionError("unnecessary recheck"))
    monkeypatch.setattr(store, "_start_discovery_recheck", start)
    verdict_path.unlink()
    assert store.discovery_text() == ""  # unchecked but not enabled
    store.set_discovery("markdown-fixture", True, now=NOW)
    verdict.update(eligible=False, reasons=[dict(field="microclaw", detail="incompatible")])
    write(verdict_path, verdict)
    assert store.discovery_text() == ""
    assert dict(field="microclaw", detail="incompatible") in state("markdown")["installs"][0]["discovery_exclusions"]
    start.assert_not_called()


@pytest.mark.parametrize('state,retained', [('queued', True), ('running', True),
    ('succeeded', False), ('failed', False), ('dispatch_failed', False),
    ('supervisor_failed', False), ('cancelled', False), ('refused', False), ('abandoned', False)])
def test_d3_live_job_retention_and_startup_abandonment(state, retained):
    record = dict(job_id='a'*32, state=state, digest='b'*64, parameters={'text': 'a\nb'},
                  owner=dict(pid=os.getpid(), nonce='live-process'))
    path = store.analysis_job_path(record['job_id'])
    store._write(path, record)
    assert store.retained_digests() == (frozenset({'b'*64}) if retained else frozenset())
    store.abandon_analysis_jobs()
    assert store.analysis_job_status(record['job_id']) == record
    assert store.retained_digests() == (frozenset({'b'*64}) if retained else frozenset())


def test_f2_live_owner_survives_sweep_dead_owner_stops_pinning():
    child = subprocess.Popen([sys.executable, '-c', 'pass'])
    child.wait()
    assert not store._alive(child.pid)
    live = dict(job_id='a'*32, state='running', digest='b'*64,
                owner=dict(pid=os.getpid(), nonce='live-process'))
    dead = dict(job_id='c'*32, state='queued', digest='d'*64,
                owner=dict(pid=child.pid, nonce='dead-process'))
    for record in (live, dead):
        store._write(store.analysis_job_path(record['job_id']), record)
    assert store.retained_digests() == frozenset({live['digest']})
    store.abandon_analysis_jobs()
    assert store.analysis_job_status(live['job_id']) == live
    assert store.analysis_job_status(dead['job_id']) == dict(dead, state='abandoned')


@pytest.mark.parametrize('content', ['{invalid', '[]', '{"state": []}'])
def test_f3_unreadable_record_isolated_from_live_jobs_and_package_management(tmp_path, content):
    bad = store.analysis_job_path('e'*32)
    bad.parent.mkdir(parents=True)
    bad.write_text(content, encoding='utf-8')
    live = dict(job_id='a'*32, state='running', digest='b'*64,
                owner=dict(pid=os.getpid(), nonce='live-process'))
    store._write(store.analysis_job_path(live['job_id']), live)
    store.abandon_analysis_jobs()
    assert bad.read_text(encoding='utf-8') == content
    assert store.retained_digests() == frozenset({live['digest']})
    with pytest.raises(store.PackageRefusal, match='unreadable'):
        store.analysis_job_status('e'*32)
    install(tmp_path, kind='markdown')
    store.remove('markdown-fixture', retained_digests=store.retained_digests())
    assert not package('markdown').exists()


def test_installed_observer_evidence_survives_durable_lifecycle_records(tmp_path, monkeypatch):
    from ndstorage import NDTiffDataset
    from microclaw import completed_dataset as cd, tools
    from tests.test_skill_supervisor import write_frame, wait_observation, wait_status
    record = install(tmp_path)
    monkeypatch.setattr(tools, 'CONFIRM_FN', lambda *a, **k: True)
    prepared = cd.prepare_package_analysis(
        'fixture-lab/executable-fixture:observe_dataset', record['artifact_digest'],
        {'cpu_threads': 2, 'max_s': 60}, disclosure=[])
    dataset = tmp_path / 'dataset'
    dataset.mkdir()
    writer = NDTiffDataset(str(dataset), writable=True)
    writer.initialize({})
    job = cd.AcquisitionAnalysisJob(prepared, str(dataset))
    path = Path(job.metadata['job_record_path'])
    assert read(path)['job_id'] == job.handle.job_id  # submit's initial pin
    try:
        wait_status(job.handle, 'load running: 2')
        total = write_frame(writer, 0, sixteen=True)
        wait_observation(job.handle, 1)
        job.acquisition_finished('unterminated')
        end = time.monotonic() + 8
        while time.monotonic() < end:
            if read(path)['lifecycle']['writer'] == 'unknown':
                break
            # Persistence may precede delivery; a later lifecycle snapshot must
            # copy the delivered state without changing the result's shape.
            job._persist()
            time.sleep(0.01)
        else:
            pytest.fail('lifecycle record was not published')
        total += write_frame(writer, 1)
        writer.finish()
        job.writer_finished()
        assert job.handle.wait(10), job.record()
    finally:
        writer.finish()
        cd.close_analysis_supervisor()
    saved = read(path)
    assert saved['state'] == 'succeeded', saved
    assert saved['lifecycle'] == {'acquisition': 'unterminated', 'writer': 'finished'}
    evidence = saved['result']['output']
    assert evidence == job.handle.record()['result']['output']
    assert evidence['observed'] is True
    assert evidence['frames_read'] == 2 and evidence['bytes_read'] == total
    assert evidence['cpu_threads'] == 2 and evidence['load_stopped_by'] == 'writer'
    assert evidence['frames_indexed_at_load_start'] == 0
    assert evidence['read_errors'] == 0
    # A subsequent lifecycle publication also preserves the terminal evidence.
    job._persist()
    cd.close_analysis_supervisor()
    assert read(path)['result']['output'] == evidence


# Catalog fakes implement the updater's Request/timeout/response contract.
class CatalogResponse:
    def __init__(self, document=None, *, status=200, headers=None, data=None):
        import io
        self.status, self.headers = status, headers or {}
        self.stream = io.BytesIO(data if data is not None else json.dumps(document).encode())
        self.closed = False

    def read(self, n):
        return self.stream.read(n)

    def close(self):
        self.closed = True


def catalog(releases=(), withdrawals=()):
    return dict(type=packages.CATALOG_TYPE, releases=list(releases), withdrawals=list(withdrawals))


def release():
    return read(FIXTURES / 'markdown-intake.json')


def withdrawal():
    return builder.sign(dict(type=packages.WITHDRAWAL_TYPE,
                             **{k: release()[k] for k in ('publisher', 'package_id', 'version', 'artifact_digest')},
                             reason='Publisher stopped offering this release'))


def catalog_opener(document, *, policy=None):
    def open(request, timeout):
        assert timeout == updates.HTTP_TIMEOUT_SECONDS
        return CatalogResponse(policy or read(FIXTURES / 'trust/policy-TEST-ONLY.json')
                               if request.full_url.endswith('policy.json') else document)
    return open


def test_catalog_union_withdrawal_offline_and_read_time(monkeypatch):
    first = catalog([release()], [withdrawal()])
    assert store.refresh_catalog(opener=catalog_opener(first), now=NOW)['error'] is None
    assert store.refresh_catalog(opener=catalog_opener(catalog()), now=NOW)['error'] is None
    result = store.catalog_entries(now=NOW)
    assert len(result['releases']) == len(result['withdrawals']) == 1
    assert result['releases'][0]['withdrawn']
    assert result['releases'][0]['withdrawal_reason'] == withdrawal()['reason']
    before = (store.store_dir() / 'catalog/catalog.json').read_bytes()
    def offline(request, timeout):
        raise OSError('connection unavailable')
    error = store.refresh_catalog(opener=offline, now=NOW)['error']['detail']
    assert 'could not be reached' in error and 'policy.json' in error and 'catalog.json' in error
    assert (store.store_dir() / 'catalog/catalog.json').read_bytes() == before
    monkeypatch.setattr(store, 'current_build', lambda: dict(version='0.0.0', protocols=[]))
    assert not store.catalog_entries(now=NOW)['releases'][0]['compatible']
    assert store.status()['catalog']['state'] == 'unreachable'


def test_catalog_entry_isolation_duplicates_and_tampering():
    malformed = dict(release(), license='bad\nlicense')
    different = builder.sign(dict(release(), artifact_digest='b'*64))
    accepted, excluded = packages.verify_catalog(catalog([release(), release(), malformed, different]),
                                                 store.load_trust_policy(), now=NOW)
    assert accepted['releases'] == [different]
    assert len(excluded) == 3
    assert all(e['reason']['field'] == 'artifact_digest' for e in excluded)
    accepted, excluded = packages.verify_catalog(catalog([None, release(), release()]),
                                                 store.load_trust_policy(), now=NOW)
    assert accepted['releases'] == [release()] and len(excluded) == 1
    changed = dict(release(), license='changed')
    accepted, excluded = packages.verify_catalog(catalog([changed, different]),
                                                 store.load_trust_policy(), now=NOW)
    assert accepted['releases'] == [different]
    assert excluded[0]['reason']['field'] == 'signature.value'


@pytest.mark.parametrize('change,field', [('reason', 'reason'), ('type', 'type'), ('signature', 'signature.value')])
def test_withdrawal_fields_and_signature(change, field):
    document = withdrawal()
    if change == 'reason':
        document['reason'] = 'bad\u2028reason'
    elif change == 'type':
        document['type'] = packages.RELEASE_TYPE
    else:
        document['signature'] = release()['signature']
    with pytest.raises(packages.PackageRefusal) as exc:
        packages.verify_withdrawal(document, store.load_trust_policy())
    assert exc.value.field == field


def test_catalog_rechecks_revocations_and_keeps_signed_cache():
    store.refresh_catalog(opener=catalog_opener(catalog([release()], [withdrawal()])), now=NOW)
    path = store.store_dir() / 'catalog/catalog.json'
    before = path.read_bytes()
    policy = store.load_trust_policy()
    policy['revision'] += 1
    policy['publishers']['fixture-lab']['state'] = 'revoked'
    store.store_trust_policy(builder.sign(policy, 'root'))
    result = store.catalog_entries(now=NOW)
    assert not result['releases'] and not result['withdrawals'] and len(result['exclusions']) == 2
    assert path.read_bytes() == before


def test_catalog_envelope_refusal_preserves_cache_and_corruption_recovers():
    store.refresh_catalog(opener=catalog_opener(catalog([release()])), now=NOW)
    path = store.store_dir() / 'catalog/catalog.json'
    before = path.read_bytes()
    state = store.refresh_catalog(opener=catalog_opener(dict(type='wrong', releases=[], withdrawals=[])), now=NOW)
    assert state['error'] and path.read_bytes() == before
    path.write_text('{invalid', encoding='utf-8')
    (path.parent / 'state.json').write_text('{invalid', encoding='utf-8')
    assert store.refresh_catalog(opener=catalog_opener(catalog([release()])), now=NOW)['error'] is None
    write(path, catalog([None, release()]))
    assert len(store.catalog_entries(now=NOW)['releases']) == 1


def test_catalog_unpublished_never_requests(monkeypatch):
    (store.store_dir() / 'trust/roots.json').unlink()
    def forbidden(*args):
        pytest.fail('unpublished catalog attempted network')
    result = store.refresh_catalog(opener=forbidden, now=NOW)
    assert result['error']['detail'] == 'no community catalog is published yet'
    assert store.status()['catalog']['state'] == 'unpublished'


def test_catalog_single_flight():
    assert store._catalog_refresh_lock.acquire(blocking=False)
    try:
        assert 'in progress' in store.refresh_catalog(opener=lambda *args: pytest.fail("concurrent refresh attempted network"), now=NOW)['error']['detail']
    finally:
        store._catalog_refresh_lock.release()


def test_catalog_redirect_and_forbidden_redirect():
    import urllib.error
    roots = read(store.store_dir() / 'trust/roots.json')
    roots['catalog_url'] = 'https://raw.githubusercontent.com/test/catalog/'
    write(store.store_dir() / 'trust/roots.json', roots)
    urls = []
    def open(request, timeout):
        urls.append(request.full_url)
        if '/test/catalog/' in request.full_url:
            raise urllib.error.HTTPError(request.full_url, 302, 'redirect',
                                         {'Location': '/next/' + request.full_url.rsplit('/', 1)[-1]}, None)
        return CatalogResponse(store.load_trust_policy() if request.full_url.endswith('policy.json') else catalog([release()]))
    assert store.refresh_catalog(opener=open, now=NOW)['error'] is None
    assert len(urls) == 4
    def forbidden(request, timeout):
        return CatalogResponse(status=302, headers={'Location': 'https://evil.example/catalog.json'})
    result = store.refresh_catalog(opener=forbidden, now=NOW)
    assert 'not allowlisted' in result['error']['detail']


def test_catalog_failed_policy_can_merge_cached_policy_and_expired_refuses():
    import urllib.error
    def open(request, timeout):
        if request.full_url.endswith('policy.json'):
            raise urllib.error.HTTPError(request.full_url, 404, 'missing', {}, None)
        return CatalogResponse(catalog([release()]))
    result = store.refresh_catalog(opener=open, now=NOW)
    assert 'HTTP 404' in result['error']['detail'] and 'repository is not public' not in result['error']['detail']
    assert len(store.catalog_entries(now=NOW)['releases']) == 1
    policy = store.load_trust_policy()
    policy.update(revision=2, expires_at='2020-01-01T00:00:00Z')
    result = store.refresh_catalog(opener=catalog_opener(catalog(), policy=builder.sign(policy, 'root')), now=NOW)
    assert 'policy expired' in result['error']['detail']
    assert len(store.catalog_entries(now=NOW)['releases']) == 1
    assert store._catalog_status(now=NOW, policy=store.load_trust_policy())['stale']


@pytest.mark.parametrize('previous', ['verified', 'invalid_signature', 'corrupt', 'different_environment', 'missing'])
def test_policy_recovery_branches(previous):
    path = store.store_dir() / 'trust/policy.json'
    policy = store.load_trust_policy()
    if previous == 'invalid_signature':
        write(path, dict(policy, signature=dict(policy['signature'], value='A'*86+'==')))
        with pytest.raises(packages.PackageRefusal, match='strictly greater'):
            store.store_trust_policy(policy)
    elif previous == 'corrupt':
        path.write_text('{broken', encoding='utf-8')
    elif previous == 'different_environment':
        write(path, dict(policy, environment='production', revision=999))
    elif previous == 'missing':
        path.unlink()
    else:
        store.store_trust_policy(policy)
        conflicting = builder.sign(dict(policy, expires_at='2029-01-01T00:00:00Z'), 'root')
        with pytest.raises(packages.PackageRefusal, match='conflicting'):
            store.store_trust_policy(conflicting)
    next_policy = builder.sign(dict(policy, revision=2), 'root')
    assert store.store_trust_policy(next_policy)['revision'] == 2


def test_discovery_never_reads_catalog_or_requests(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('per-turn discovery touched catalog or network')
    monkeypatch.setattr(store, 'catalog_entries', forbidden)
    monkeypatch.setattr(store, '_catalog_file', forbidden)
    monkeypatch.setattr(updates, '_default_opener', forbidden)
    assert store.discovery_text() == ''
    with pytest.raises(packages.PackageRefusal):
        store.load_discovered_skill('fixture-lab/markdown-fixture/workflow')


def test_catalog_bounds_and_state_isolation(monkeypatch):
    monkeypatch.setattr(packages, 'MAX_CATALOG_BYTES', 32)
    result = store.refresh_catalog(opener=catalog_opener(catalog([release()])), now=NOW)
    assert 'size limit' in result['error']['detail']
    assert not (store.store_dir() / 'catalog/catalog.json').exists()
    monkeypatch.setattr(packages, 'MAX_CATALOG_RELEASES', 1)
    with pytest.raises(packages.PackageRefusal, match='exceeds 1'):
        packages.verify_catalog(catalog([release(), release()]), store.load_trust_policy(), now=NOW)
    write(store.store_dir() / 'catalog/state.json', {'error': [], 'exclusions': 'invalid'})
    assert store.status()['catalog']['fetch_exclusions'] == []


def test_catalog_installed_and_build_fields_are_not_cached(monkeypatch):
    document = release()
    write(store.store_dir() / 'catalog/catalog.json', catalog([document]))
    path = store._package(document['package_id']) / 'installs' / ('a'*16 + '-aaaaaa')
    write(path / 'install.json', dict(intake=document, state='ready'))
    result = store.catalog_entries(now=NOW)['releases'][0]
    assert result['installed'] and result['compatible']
    (path / 'install.json').unlink()
    assert not store.catalog_entries(now=NOW)['releases'][0]['installed']
    assert set(read(store.store_dir() / 'catalog/catalog.json')['releases'][0]) == set(document)


@pytest.mark.parametrize('state', ['retired', 'revoked'])
def test_catalog_withdrawal_key_rotation(state):
    policy = store.load_trust_policy()
    policy['publishers']['fixture-lab']['keys'][0]['state'] = state
    if state == 'retired':
        assert packages.verify_withdrawal(withdrawal(), policy) == withdrawal()
    else:
        with pytest.raises(packages.PackageRefusal):
            packages.verify_withdrawal(withdrawal(), policy)


def test_catalog_root_rotation_recovery():
    roots = read(store.store_dir() / 'trust/roots.json')
    # A shipped replacement root; the previous root signature is no longer valid.
    key = read(FIXTURES / 'trust/publisher-b-TEST-ONLY-public.json')
    roots['keys'] = [key]
    write(store.store_dir() / 'trust/roots.json', roots)
    document = read(FIXTURES / 'trust/policy-TEST-ONLY.json')
    with pytest.raises(packages.PackageRefusal, match='strictly greater'):
        store.store_trust_policy(builder.sign(document, 'publisher-b'))
    document['revision'] = 2
    assert store.store_trust_policy(builder.sign(document, 'publisher-b'))['revision'] == 2


def test_catalog_one_signature_per_entry_per_read(monkeypatch):
    document = catalog([release(), builder.sign(dict(release(), artifact_digest='a'*64))], [withdrawal()])
    write(store.store_dir() / 'catalog/catalog.json', document)
    real = packages._verify_signature
    calls = []
    def verify(document, *args, **kwargs):
        calls.append(document['type'])
        return real(document, *args, **kwargs)
    monkeypatch.setattr(packages, '_verify_signature', verify)
    store.catalog_entries(now=NOW)
    assert calls.count(packages.RELEASE_TYPE) == 2
    assert calls.count(packages.WITHDRAWAL_TYPE) == 1


def test_catalog_read_time_block_reason():
    write(store.store_dir() / 'catalog/catalog.json', catalog([release()]))
    policy = store.load_trust_policy()
    policy.update(revision=2, revoked_releases=[{k: release()[k] for k in
                  ('package_id', 'version', 'artifact_digest')} | {'reason': 'Unsafe publisher release'}])
    store.store_trust_policy(builder.sign(policy, 'root'))
    result = store.catalog_entries(now=NOW)
    assert not result['releases']
    assert result['exclusions'][0]['block_reason']['detail'] == 'Unsafe publisher release'


def test_catalog_status_reports_last_fetch_exclusions():
    document = dict(release(), license='tampered')
    store.refresh_catalog(opener=catalog_opener(catalog([document])), now=NOW)
    result = store.status()['catalog']
    assert len(result['fetch_exclusions']) == 1
    assert result['fetch_exclusions'][0]['reason']['field'] == 'signature.value'


def test_catalog_malformed_is_excluded_without_claiming_operator_block():
    write(store.store_dir() / 'catalog/catalog.json', catalog([None, dict(release(), license='tampered')]))
    result = store.catalog_entries(now=NOW)
    assert all(not e.get('blocked', False) for e in result['exclusions'])


def test_catalog_union_can_outgrow_one_fetch_bound(monkeypatch):
    monkeypatch.setattr(packages, 'MAX_CATALOG_RELEASES', 1)
    one, two = release(), builder.sign(dict(release(), artifact_digest='a'*64))
    store.refresh_catalog(opener=catalog_opener(catalog([one])), now=NOW)
    store.refresh_catalog(opener=catalog_opener(catalog([two])), now=NOW)
    assert len(store.catalog_entries(now=NOW)['releases']) == 2


def test_catalog_merge_re_reads_another_process_union():
    other = builder.sign(dict(release(), artifact_digest='a'*64))
    def open(request, timeout):
        if request.full_url.endswith('policy.json'):
            return CatalogResponse(store.load_trust_policy())
        # Simulate another process publishing while this process fetches.
        write(store.store_dir() / 'catalog/catalog.json', catalog([other]))
        return CatalogResponse(catalog([release()]))
    store.refresh_catalog(opener=open, now=NOW)
    assert {r['artifact_digest'] for r in store.catalog_entries(now=NOW)['releases']} == {
        release()['artifact_digest'], other['artifact_digest']}


def test_catalog_verification_is_pure(monkeypatch):
    policy = store.load_trust_policy()
    document = catalog([release()], [withdrawal()])
    def forbidden(*args, **kwargs):
        pytest.fail('trust verification performed I/O')
    import socket
    monkeypatch.setattr(Path, 'open', forbidden)
    monkeypatch.setattr(Path, 'read_text', forbidden)
    monkeypatch.setattr(Path, 'read_bytes', forbidden)
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(store, '_write', forbidden)
    verified, exclusions = packages.verify_catalog(document, policy, now=NOW)
    assert len(verified['releases']) == len(verified['withdrawals']) == 1
    assert not exclusions


def test_catalog_malformed_cached_metadata_is_isolated():
    write(store.store_dir() / 'catalog/catalog.json', catalog([
        dict(release(), publisher=[], artifact_digest='b'*64), dict(release(), artifact_digest=[]), release()]))
    result = store.catalog_entries(now=NOW)
    assert len(result['releases']) == 1 and len(result['exclusions']) == 2


def test_catalog_conflicting_history_cannot_unwithdraw():
    store.refresh_catalog(opener=catalog_opener(catalog([release()], [withdrawal()])), now=NOW)
    changed = builder.sign(dict(withdrawal(), reason='A changed withdrawal reason'))
    state = store.refresh_catalog(opener=catalog_opener(catalog([release()], [changed])), now=NOW)
    result = store.catalog_entries(now=NOW)
    assert result['releases'][0]['withdrawn']
    assert result['releases'][0]['withdrawal_reason'] == withdrawal()['reason']
    assert state['exclusions'][0]['reason']['field'] == 'artifact_digest'


@pytest.mark.parametrize('field', ['publisher', 'package_id', 'version', 'artifact_digest', 'absent_release'])
def test_withdrawal_matches_entire_release_identity(field):
    target = release()
    policy = store.load_trust_policy()
    # Give B its own admitted key, distinct from A's existing key.
    key_b = policy['publishers']['fixture-lab']['keys'].pop()
    policy['publishers']['other-lab'] = dict(state='active', keys=[key_b])
    policy['revision'] += 1
    store.store_trust_policy(builder.sign(policy, 'root'))
    if field == 'publisher':
        target = builder.sign(dict(target, publisher='other-lab'), 'publisher-b')
    attempted = withdrawal()
    if field == 'package_id':
        attempted = builder.sign(dict(attempted, package_id='another-package'))
    elif field == 'version':
        attempted = builder.sign(dict(attempted, version='2.0.0'))
    elif field == 'artifact_digest':
        attempted = builder.sign(dict(attempted, artifact_digest='a'*64))
    write(store.store_dir() / 'catalog/catalog.json', catalog(
        [] if field == 'absent_release' else [target], [attempted]))
    result = store.catalog_entries(now=NOW)
    assert not result['withdrawals']
    assert len(result['exclusions']) == 1
    assert result['exclusions'][0]['reason']['field'] == 'withdrawal.release'
    if result['releases']:
        assert not result['releases'][0]['withdrawn']


@pytest.mark.parametrize('attempt_state', ['failed', 'staged'])
def test_catalog_failed_attempt_is_not_installed(attempt_state):
    document = release()
    write(store.store_dir() / 'catalog/catalog.json', catalog([document]))
    path = store._package(document['package_id']) / 'installs' / ('a'*16 + '-aaaaaa')
    write(path / 'install.json', dict(intake=document, state=attempt_state))
    assert not store.catalog_entries(now=NOW)['releases'][0]['installed']


@pytest.mark.parametrize('count', [0, 1, 100, 1000])
def test_status_never_verifies_or_reads_catalog_entries(monkeypatch, count):
    write(store.store_dir() / 'catalog/catalog.json', catalog(
        [builder.sign(dict(release(), artifact_digest=f'{i:064x}')) for i in range(count)]))
    real_verify, real_read = packages._verify_signature, store._catalog_file
    calls = []
    def verify(document, *args, **kwargs):
        calls.append(document['type'])
        return real_verify(document, *args, **kwargs)
    def read_catalog(name):
        assert name == 'state.json', 'status read catalog entries'
        return real_read(name)
    monkeypatch.setattr(packages, '_verify_signature', verify)
    monkeypatch.setattr(store, '_catalog_file', read_catalog)
    result = store.status()['catalog']
    assert calls == [packages.TRUST_POLICY_TYPE]
    assert set(result) == {'state', 'last_attempt', 'last_success', 'error', 'revision',
                           'expires_at', 'stale', 'fetch_exclusions'}


@pytest.mark.parametrize('failure', ['transport', 'bad_policy_and_transport', 'bad_envelope', 'expired', 'bad_json', 'rollback'])
def test_catalog_failure_kind_is_recorded(failure):
    policy = store.load_trust_policy()
    if failure == 'rollback':
        store.store_trust_policy(builder.sign(dict(policy, revision=2), 'root'))
    def open(request, timeout):
        if failure == 'transport' or (failure == 'bad_policy_and_transport' and request.full_url.endswith('catalog.json')):
            raise OSError('connection unavailable')
        if request.full_url.endswith('policy.json'):
            if failure == 'bad_policy_and_transport':
                return CatalogResponse(dict(policy, expires_at='2029-01-01T00:00:00Z'))
            if failure == 'expired':
                return CatalogResponse(builder.sign(dict(policy, revision=2, expires_at='2020-01-01T00:00:00Z'), 'root'))
            return CatalogResponse(policy)
        if failure == 'bad_json':
            return CatalogResponse(data=b'{invalid')
        return CatalogResponse(dict(type='wrong', releases=[], withdrawals=[])
                               if failure == 'bad_envelope' else catalog())
    state = store.refresh_catalog(opener=open, now=NOW)
    expected = 'unreachable' if failure == 'transport' else 'refused'
    assert state['error']['kind'] == expected
    assert store.status()['catalog']['state'] == expected
    kinds = [error['kind'] for error in state['error']['errors']]
    assert kinds == (['refused', 'unreachable'] if failure == 'bad_policy_and_transport' else
                     ['unreachable', 'unreachable'] if failure == 'transport' else ['refused'])


@pytest.mark.parametrize('field,value', [('microclaw', '>=999'), ('python', '>=999')])
def test_catalog_and_installed_compatibility_share_checks(monkeypatch, field, value):
    document = read(FIXTURES / 'executable-intake.json')
    document[field] = value
    document = builder.sign(document)
    manifest = read(FIXTURES / 'executable/manifest.json')
    manifest[field] = value
    build = store.current_build()
    write(store.store_dir() / 'catalog/catalog.json', catalog([document]))
    calls = []
    real = store._intake_compatibility
    def shared(*args, **kwargs):
        calls.append(args)
        return real(*args, **kwargs)
    monkeypatch.setattr(store, '_intake_compatibility', shared)
    with pytest.raises(packages.PackageRefusal) as exc:
        store._compatibility(manifest, build)
    catalog_reason = store.catalog_entries(now=NOW)['releases'][0]['compatibility_reason']
    assert catalog_reason == store._reason(exc.value)
    assert len(calls) == 2


def test_search_catalog_cards_matching_and_cost(monkeypatch):
    one = release()
    one['skills'][0]['description'] = '  Alpha  \u00a0Beta ' + 'x' * 205 + ' tailword'
    one = builder.sign(one)
    newer = builder.sign(dict(one, version='2.0.0', artifact_digest='a'*64, microclaw='>=999'))
    incompatible = builder.sign(dict(newer, package_id='incompatible', artifact_digest='b'*64))
    withdrawn = builder.sign(dict(release(), package_id='withdrawn', artifact_digest='c'*64))
    notice = builder.sign(dict(withdrawal(), **{k: withdrawn[k] for k in
                         ('publisher', 'package_id', 'version', 'artifact_digest')}))
    blocked = builder.sign(dict(release(), package_id='blocked', artifact_digest='d'*64))
    policy = store.load_trust_policy()
    policy.update(revision=2, revoked_releases=[{k: blocked[k] for k in
                  ('package_id', 'version', 'artifact_digest')} | {'reason': 'Blocked fixture'}])
    store.store_trust_policy(builder.sign(policy, 'root'))
    write(store.store_dir() / 'catalog/catalog.json', catalog([one, newer, incompatible, withdrawn, blocked], [notice]))
    monkeypatch.setattr(updates, '_open_manual', lambda *a, **kw: pytest.fail('search requested network'))
    monkeypatch.setattr(store, 'refresh_catalog', lambda **kw: pytest.fail('search refreshed'))
    real = packages._verify_signature
    calls = []
    def verify(document, *args, **kwargs):
        calls.append(document['type'])
        return real(document, *args, **kwargs)
    monkeypatch.setattr(packages, '_verify_signature', verify)
    result = store.search_catalog(now=NOW)
    assert result['total'] == 2
    assert calls.count(packages.RELEASE_TYPE) == 5
    assert calls.count(packages.WITHDRAWAL_TYPE) == 1
    cards = {c['qualified_name']: c for c in result['cards']}
    card = cards['fixture-lab/markdown-fixture/workflow']
    assert card['version'] == '1.0.0' and card['compatible']
    assert card['description'].startswith('Alpha Beta ') and len(card['description']) == 200
    assert card['description'].endswith('…')
    bad = cards['fixture-lab/incompatible/workflow']
    assert not bad['compatible'] and bad['compatibility_reason'] == (
        'needs MicroClaw >=999; this is ' + store.current_build()['version'])
    assert card['next_step'] == dict(state='not_installed', instruction='The user can install it from the Skills panel.')
    assert all(set(c) == {'qualified_name', 'publisher', 'version', 'description', 'compatible',
                          'compatibility_reason', 'next_step'} for c in result['cards'])
    body = (FIXTURES / 'markdown/SKILL.md').read_text(encoding='utf-8')
    assert json.dumps(body)[1:-1] not in json.dumps(result)
    assert 'Publisher-owned instructions for a format test.' not in json.dumps(result)
    assert store.search_catalog('FIXTURE-LAB markdown alpha tailword', now=NOW)['total'] == 1
    assert store.search_catalog('alpha absent', now=NOW)['total'] == 0
    assert 'error' in store.search_catalog('x' * 513, now=NOW)


def test_search_catalog_limit_and_fetch_state():
    entries = [builder.sign(dict(release(), package_id=f'package-{i:02}', artifact_digest=f'{i:064x}'))
               for i in range(12)]
    write(store.store_dir() / 'catalog/catalog.json', catalog(entries))
    result = store.search_catalog(now=NOW)
    assert len(result['cards']) == 10 and result['total'] == 12
    assert [c['qualified_name'] for c in result['cards']] == sorted(c['qualified_name'] for c in result['cards'])
    assert result['catalog']['state'] == 'never_fetched'
    assert result['catalog']['last_success'] is None
    assert 'stale' in result['catalog']


@pytest.mark.parametrize('unpublished', [False, True])
def test_search_catalog_without_saved_catalog(unpublished):
    if unpublished:
        write(store.store_dir() / 'trust/roots.json', packages.PRODUCTION_ROOTS)
    result = store.search_catalog(now=NOW)
    assert result['cards'] == [] and result['total'] == 0
    assert result['catalog']['state'] == ('unpublished' if unpublished else 'never_fetched')


def test_search_catalog_discovery_states(tmp_path, monkeypatch):
    installed = install(tmp_path, kind='markdown')
    document = builder.sign(dict(installed['intake'], version='2.0.0', artifact_digest='a'*64))
    write(store.store_dir() / 'catalog/catalog.json', catalog([document]))
    real_discovery = store._discovery_state
    snapshot = real_discovery(now=NOW)
    monkeypatch.setattr(store, '_discovery_state', lambda **kw: snapshot)
    result = store.search_catalog(now=NOW)
    step = result['cards'][0]['next_step']
    assert step['state'] == 'enabled'
    assert step['instruction'] == 'Load it with load_skill. A newer version is available in the Skills panel.'
    assert store.load_discovered_skill(result['cards'][0]['qualified_name'])['version'] == '1.0.0'
    monkeypatch.setattr(store, '_discovery_state', real_discovery)
    store.set_discovery('markdown-fixture', False, now=NOW)
    step = store.search_catalog(now=NOW)['cards'][0]['next_step']
    assert step == dict(state='installed_off', instruction='The user can turn it on in the Skills panel.')
    with pytest.raises(packages.PackageRefusal):
        store.load_discovered_skill('fixture-lab/markdown-fixture/workflow')


def test_search_catalog_unreadable_store(monkeypatch):
    monkeypatch.setattr(store, '_read', lambda path: (_ for _ in ()).throw(PermissionError('unreadable fixture')))
    assert 'unreadable fixture' in store.search_catalog(now=NOW)['error']


def test_search_catalog_enabled_install_with_altered_asset(tmp_path):
    installed = install(tmp_path, kind='markdown')
    write(store.store_dir() / 'catalog/catalog.json', catalog([installed['intake']]))
    skill = installed['manifest']['skills'][0]
    asset = directory(installed, 'markdown') / 'release' / skill['path']
    asset.write_text('Altered publisher instructions', encoding='utf-8')
    result = store.search_catalog(now=NOW)
    card = result['cards'][0]
    assert card['next_step'] == dict(state='installed_off', instruction=
        "It is installed, but the agent can't load it. The Skills panel shows why.")
    assert 'Altered publisher instructions' not in json.dumps(result)
    with pytest.raises(packages.PackageRefusal, match='asset digest mismatch'):
        store.load_discovered_skill(card['qualified_name'])


@pytest.mark.parametrize('intake', [None, [], {'publisher': []}])
def test_search_catalog_isolates_damaged_install(intake):
    write(store.store_dir() / 'catalog/catalog.json', catalog([release()]))
    record = dict(state='ready')
    if intake is not None:
        record['intake'] = intake
    write(store.store_dir() / 'packages/damaged/installs/0000000000000000-000000/install.json', record)
    result = store.search_catalog(now=NOW)
    assert 'error' not in result
    assert result['total'] == 1
    assert result['cards'][0]['qualified_name'] == 'fixture-lab/markdown-fixture/workflow'


def test_search_catalog_enabled_install_lacks_new_skill(tmp_path):
    installed = install(tmp_path, kind='markdown')
    document = dict(installed['intake'], version='2.0.0', artifact_digest='a'*64,
                    skills=[dict(name='new-skill', description='Added in the newer release')])
    write(store.store_dir() / 'catalog/catalog.json', catalog([builder.sign(document)]))
    card = store.search_catalog(now=NOW)['cards'][0]
    assert card['next_step'] == dict(state='installed_off', instruction=
        "It is installed, but the agent can't load it. The Skills panel shows why.")
    with pytest.raises(packages.PackageRefusal):
        store.load_discovered_skill(card['qualified_name'])
    store.set_discovery('markdown-fixture', False, now=NOW)
    assert store.search_catalog(now=NOW)['cards'][0]['next_step'] == dict(
        state='installed_off', instruction='The user can turn it on in the Skills panel.')
    (package('markdown') / 'discovery.json').unlink()
    assert store.search_catalog(now=NOW)['cards'][0]['next_step'] == dict(
        state='installed_off', instruction='The user can turn it on in the Skills panel.')


def test_search_catalog_load_checks_only_returned_cards(tmp_path, monkeypatch):
    installed = install(tmp_path, kind='markdown')
    document = dict(installed['intake'], version='2.0.0', artifact_digest='a'*64,
                    skills=[dict(name=f'skill-{i:02}', description='New skill') for i in range(12)])
    write(store.store_dir() / 'catalog/catalog.json', catalog([builder.sign(document)]))
    real_load = packages.load_external_skill
    calls = []
    def load(name, candidates, policy, *, now):
        calls.append(name)
        return real_load(name, candidates, policy, now=now)
    monkeypatch.setattr(packages, 'load_external_skill', load)
    result = store.search_catalog(now=NOW)
    assert result['total'] == 12
    assert calls == [card['qualified_name'] for card in result['cards']]
    assert len(calls) == 10
    calls.clear()
    result = store.search_catalog('skill-11', now=NOW)
    assert result['total'] == 1
    assert calls == [result['cards'][0]['qualified_name']]


def wait_delivery(package_id):
    path = store._package(package_id) / 'job.json'
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        job = read(path)
        if job and not job['running']:
            # The final write precedes lock release by a few instructions.
            while (path.parent / '.lock').exists() and time.monotonic() < deadline:
                time.sleep(.005)
            return job
        time.sleep(.005)
    pytest.fail('delivery job did not finish')


@pytest.mark.parametrize('mode', ['mismatch', 'oversize', 'redirect', 'http'])
def test_artifact_download_bounds_redirects_and_cleanup(monkeypatch, mode):
    data = b'fixture artifact'
    entry = dict(release(), artifact_digest=hashlib.sha256(data).hexdigest())
    calls, installs = [], []
    monkeypatch.setattr(packages, 'MAX_ARTIFACT_DOWNLOAD_BYTES', 20)
    monkeypatch.setattr(updates, 'locate_uv', lambda: 'fixture-uv')
    monkeypatch.setattr(store, '_install', lambda *a, **k: installs.append((a, k)) or {'install_id': 'ok'})
    def open(request, timeout):
        calls.append(request)
        assert timeout == updates.HTTP_TIMEOUT_SECONDS
        assert request.get_header('Accept') == 'application/octet-stream'
        if len(calls) == 1 and mode in {'redirect', 'http'}:
            return CatalogResponse(status=302, headers={'Location':
                ('https' if mode == 'redirect' else 'http') + '://another.example/artifact'})
        return CatalogResponse(data=b'wrong' if mode == 'mismatch' else b'x'*21 if mode == 'oversize' else data)
    with store.package_lock(entry['package_id']) as path:
        kwargs = dict(package=path, opener=open)
        if mode == 'redirect':
            with store.download_release(entry, **kwargs) as artifact:
                assert artifact.read_bytes() == data
                assert store._install(path, entry, artifact, find_links=None)['install_id'] == 'ok'
            assert calls[-1].full_url == 'https://another.example/artifact'
            assert installs[0][1]['find_links'] is None
        else:
            with pytest.raises((packages.PackageRefusal, updates.UpdateError)) as exc:
                with store.download_release(entry, **kwargs) as artifact:
                    store._install(path, entry, artifact)
            assert not installs
            if mode == 'mismatch':
                assert exc.value.field == 'artifact_digest'
            if mode == 'http':
                assert len(calls) == 1
        assert not list(path.glob('.download-*'))


@pytest.mark.parametrize('kind', ['withdrawn', 'blocked', 'incompatible', 'older', 'unknown'])
def test_install_job_refuses_before_download_without_refresh(monkeypatch, kind):
    entry = release()
    releases, notices = [entry], []
    if kind == 'withdrawn':
        notices = [withdrawal()]
    elif kind == 'blocked':
        policy = store.load_trust_policy()
        policy.update(revision=2, revoked_releases=[{k: entry[k] for k in
            ('package_id', 'version', 'artifact_digest')} | {'reason': 'unsafe'}])
        store.store_trust_policy(builder.sign(policy, 'root'))
    elif kind == 'incompatible':
        entry = builder.sign(dict(entry, microclaw='>=999'))
        releases = [entry]
    elif kind == 'older':
        releases.append(builder.sign(dict(entry, version='2.0.0', artifact_digest='a'*64)))
    write(store.store_dir() / 'catalog/catalog.json', catalog(releases, notices))
    identity = {k: entry[k] for k in ('publisher', 'package_id', 'version', 'artifact_digest')}
    if kind == 'unknown':
        identity['artifact_digest'] = 'f'*64
    monkeypatch.setattr(store, 'refresh_catalog', lambda **k: pytest.fail('refetch'))
    store.start_job(entry['package_id'], 'install', release=identity, retained_digests=frozenset(),
                    opener=lambda *a: pytest.fail('download before refusal'))
    job = wait_delivery(entry['package_id'])
    assert job['phase'] == 'finished' and job['reasons'][0]['field'] == 'release'


@pytest.mark.parametrize("kind", ["markdown", "executable"])
def test_install_job_production_route_and_previous(tmp_path, monkeypatch, kind):
    old = install(tmp_path, kind=kind)
    artifact = tmp_path / 'new.zip'
    entry = builder.build_release(FIXTURES / kind, artifact, version='2.0.0')
    argv_calls = []
    if kind == 'executable':
        interpreter_identity = old['interpreter']
        monkeypatch.setattr(store, 'provision_python', lambda **k: 'fake-python')
        def run(argv, **kwargs):
            argv_calls.append([str(v) for v in argv])
            return json.dumps(interpreter_identity) if store.PROBE in argv else ''
        monkeypatch.setattr(store, '_run', run)
        monkeypatch.setattr(store, '_self_check', lambda *a: None)
    write(store.store_dir() / 'catalog/catalog.json', catalog([old['intake'], entry]))
    original = store._install
    calls = []
    def installing(*args, **kwargs):
        calls.append(kwargs)
        return original(*args, **kwargs)
    monkeypatch.setattr(store, '_install', installing)
    monkeypatch.setattr(updates, 'locate_uv', lambda: 'fixture-uv')
    monkeypatch.setattr(store, 'refresh_catalog', lambda **k: pytest.fail('refetch'))
    phases = []
    original_write = store._write
    def writing(path, document):
        if path.name == 'job.json':
            phases.append(document['phase'])
        return original_write(path, document)
    monkeypatch.setattr(store, '_write', writing)
    identity = {k: entry[k] for k in ('publisher', 'package_id', 'version', 'artifact_digest')}
    requests = []
    def open(request, timeout):
        requests.append(request.full_url)
        return CatalogResponse(data=artifact.read_bytes())
    store.start_job(entry['package_id'], 'install', release=identity,
                    retained_digests=frozenset(), opener=open)
    job = wait_delivery(entry['package_id'])
    assert not job.get('reasons'), job
    assert requests == [entry['artifact']]
    assert calls[0]['find_links'] is None and calls[0]['uv_executable'] == 'fixture-uv'
    assert phases[-3:] == ['downloading', 'installing', 'finished']
    pointer = read(package(kind) / 'pointer.json')
    assert pointer == {'active': job['install_id'], 'previous': old['install_id']}
    assert not list(package(kind).glob('.download-*'))
    if kind == 'executable':
        argv = next(argv for argv in argv_calls if 'pip' in argv)
        assert '--find-links' not in argv and '--no-index' not in argv


def test_panel_listing_one_verification_per_entry_and_poll_none(monkeypatch):
    entries = [builder.sign(dict(release(), package_id=f'pkg-{i}', artifact_digest=f'{i:064x}'))
               for i in range(10)]
    write(store.store_dir() / 'catalog/catalog.json', catalog(entries))
    calls = []
    original = packages.verify_catalog
    def verify(*args, **kwargs):
        calls.append(len(args[0]['releases']))
        return original(*args, **kwargs)
    monkeypatch.setattr(packages, 'verify_catalog', verify)
    for _ in range(3):
        store.status()
    assert calls == []
    rows = store.panel_catalog(now=NOW)['packages']
    assert len(rows) == 10 and calls == [10]
    assert all(row['offered_release'] and row['skills'] == release()['skills'] for row in rows)


def test_index_environment_argv_has_no_find_links_or_no_index(tmp_path, monkeypatch):
    manifest = read(FIXTURES / 'executable/manifest.json')
    calls = []
    identity = {'version': '3.12.0', 'distributions': {'fixture-dependency': '1.2.3'}}
    monkeypatch.setattr(store, 'provision_python', lambda **k: 'fake-python')
    monkeypatch.setattr(store, 'probe', lambda python: identity)
    monkeypatch.setattr(store, '_platform', lambda *a: next(iter(manifest['locks'])))
    monkeypatch.setattr(store, '_run', lambda argv, **k: calls.append([str(v) for v in argv]))
    store.build_environment(tmp_path, manifest, uv_executable='fake-uv', find_links=None)
    argv = next(argv for argv in calls if 'pip' in argv)
    assert '--find-links' not in argv and '--no-index' not in argv
    assert '--require-hashes' in argv


def test_panel_withdrawn_blocked_installed_offer(tmp_path):
    installed = install(tmp_path, kind='markdown')
    entry = installed['intake']
    newer = builder.sign(dict(entry, version='2.0.0', artifact_digest='a'*64))
    notice = builder.sign(dict(withdrawal(), **{k: entry[k] for k in
        ('publisher', 'package_id', 'version', 'artifact_digest')}))
    write(store.store_dir() / 'catalog/catalog.json', catalog([entry, newer], [notice]))
    row = store.panel_catalog(now=NOW)['packages'][0]
    assert row['withdrawn'] and row['installed_version'] == '1.0.0'
    assert row['offered_release']['version'] == '2.0.0' and row['update_available']
    policy = store.load_trust_policy()
    policy.update(revision=2, revoked_releases=[{k: entry[k] for k in
        ('package_id', 'version', 'artifact_digest')} | {'reason': 'unsafe'}])
    store.store_trust_policy(builder.sign(policy, 'root'))
    row = store.panel_catalog(now=NOW)['packages'][0]
    assert row['blocked'] and row['block_reason']['detail'] == 'unsafe'
    assert row['installed_version'] == '1.0.0' and row['offered_release']['version'] == '2.0.0'


def test_install_job_refuses_other_publisher_without_replacing_pointer(tmp_path, monkeypatch):
    policy = store.load_trust_policy()
    keys = policy['publishers'].pop('fixture-lab')['keys']
    policy['publishers'].update({
        'publisher-a': dict(state='active', keys=[keys[0]]),
        'publisher-b': dict(state='active', keys=[keys[1]])})
    policy['revision'] += 1
    store.store_trust_policy(builder.sign(policy, 'root'))
    entries, artifacts = {}, {}
    for publisher in ('publisher-a', 'publisher-b'):
        source = tmp_path / publisher
        shutil.copytree(FIXTURES / 'markdown', source)
        manifest = read(source / 'manifest.json')
        manifest.update(publisher=publisher, package_id='analysis-tools')
        write(source / 'manifest.json', manifest)
        artifact = tmp_path / (publisher + '.zip')
        entries[publisher] = builder.sign(builder.build_release(source, artifact), publisher)
        artifacts[publisher] = artifact.read_bytes()
    a = entries['publisher-a']
    installed = store.install(a, tmp_path / 'publisher-a.zip', policy=store.load_trust_policy(),
                              now=NOW, retained_digests=frozenset())
    directory = store._package('analysis-tools')
    pointer = read(directory / 'pointer.json')
    assert pointer['active'] == installed['install_id']
    write(store.store_dir() / 'catalog/catalog.json', catalog(entries.values()))
    monkeypatch.setattr(updates, 'locate_uv', lambda: 'fixture-uv')
    b = entries['publisher-b']
    identity = {k: b[k] for k in ('publisher', 'package_id', 'version', 'artifact_digest')}
    downloads = []
    def open(request, timeout):
        downloads.append(request.full_url)
        return CatalogResponse(data=artifacts['publisher-b'])
    store.start_job('analysis-tools', 'install', release=identity,
                    retained_digests=frozenset(), opener=open)
    job = wait_delivery('analysis-tools')
    assert downloads == [b['artifact']]
    assert job['release'] == identity
    assert job['reasons'] == [dict(field='publisher', detail=
        "another publisher's package with this name is installed; remove it first")]
    assert read(directory / 'pointer.json') == pointer
    assert len(store._records(directory)) == 1
    rows = store.panel_catalog(now=NOW)['packages']
    assert [(r['publisher'], r['installed_version']) for r in rows] == [
        ('publisher-a', '1.0.0'), ('publisher-b', None)]
    assert not list(directory.glob('.download-*'))


@pytest.mark.parametrize('broken_pointer', [False, True])
def test_recovery_deletes_interrupted_download(tmp_path, broken_pointer):
    installed = install(tmp_path, kind='markdown')
    directory = package('markdown')
    temporary = directory / '.download-interrupted.zip'
    temporary.write_bytes(b'partial artifact')
    if broken_pointer:
        (directory / 'pointer.json').write_text('{', encoding='utf-8')
    with store.package_lock('markdown-fixture'):
        result = store._recover(directory, retained_digests=frozenset())
    assert not temporary.exists()
    assert not result['deletion_failures']
    assert store._install_dir(directory, installed['install_id']).is_dir()
