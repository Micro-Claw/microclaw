"""Block 58a: managed update provenance and immutable source discovery."""
from __future__ import annotations

import io
import json
import os
import stat
import subprocess
import sys
import time
import urllib.error
import zipfile
from pathlib import Path

import pytest

from microclaw import updates


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=False,
        env={**os.environ, "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
             "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.invalid"},
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def commit(repo: Path, name: str, contents: str) -> str:
    (repo / name).write_text(contents, encoding="utf-8")
    git(repo, "add", name)
    git(repo, "commit", "-m", f"write {contents}")
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def clone_pair(tmp_path):
    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    clone = tmp_path / "clone"
    git(tmp_path, "init", "--bare", str(remote))
    git(tmp_path, "init", "-b", "main", str(seed))
    first = commit(seed, "tracked.txt", "one")
    git(seed, "remote", "add", "origin", str(remote))
    git(seed, "push", "-u", "origin", "main")
    git(tmp_path, "clone", "-b", "main", str(remote), str(clone))
    git(clone, "config", "user.name", "Test")
    git(clone, "config", "user.email", "test@example.invalid")
    return remote, seed, clone, first


def clone_state(clone: Path, installed: str) -> dict:
    return updates.clone_provenance(clone, installed, git_executable=shutil_git())


def shutil_git() -> str:
    import shutil
    found = shutil.which("git")
    assert found
    return found


class Response:
    def __init__(self, body=b"", status=200, headers=None):
        self._stream = io.BytesIO(body)
        self.status = status
        self.headers = headers or {}

    def getcode(self):
        return self.status

    def read(self, size=-1):
        return self._stream.read(size)

    def close(self):
        pass


class SequenceOpener:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.urls = []

    def __call__(self, request, timeout):
        self.urls.append(request.full_url)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def api_body(sha="a" * 40, message="Useful change\n\nDetails"):
    return json.dumps({"sha": sha, "commit": {"message": message}}).encode()


def zip_bytes(entries):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, value in entries:
            if isinstance(value, zipfile.ZipInfo):
                archive.writestr(value, b"target")
            else:
                archive.writestr(name, value)
    return stream.getvalue()


def http_error(url, code, location=None):
    return urllib.error.HTTPError(url, code, "test", {"Location": location} if location else {}, None)


def test_compiled_identity_and_atomic_state_are_not_configurable(tmp_path):
    assert (updates.REPO_ID, updates.REPO, updates.BRANCH) == (
        1238975695, "Micro-Claw/microclaw", "main",
    )
    path = tmp_path / updates.STATE_NAME
    updates.write_state(updates.public_provenance(), path)
    assert updates.load_state(path)["repo_id"] == updates.REPO_ID
    assert not list(tmp_path.glob(f".{updates.STATE_NAME}.*"))


def test_git_is_located_on_path_and_recorded(clone_pair):
    remote, seed, clone, first = clone_pair
    located = updates.locate_git(path=os.environ.get("PATH"))
    state = updates.clone_provenance(clone, first, git_executable=located)
    assert Path(state["git_executable"]).is_file()
    assert state["clone_path"] == str(clone.resolve())


@pytest.mark.parametrize(
    ("remote_url", "renamed_from"),
    [
        ("https://github.com/zacsimile/microclaw.git", "zacsimile/microclaw"),
        ("git@github.com:zacsimile/microclaw.git", "zacsimile/microclaw"),
        ("https://github.com/Micro-Claw/microclaw.git", None),
    ],
)
def test_github_clone_name_is_recorded_without_rejecting_transferred_remote(
    clone_pair, remote_url, renamed_from,
):
    remote, seed, clone, first = clone_pair
    git(clone, "remote", "set-url", "origin", remote_url)
    state = updates.clone_provenance(clone, first, git_executable=shutil_git())
    assert state["remote_identity"] == (
        "github:zacsimile/microclaw" if renamed_from else "github:micro-claw/microclaw"
    )
    assert state["clone_repository_note"] == (
        None if renamed_from is None else
        "This clone tracks zacsimile/microclaw, which GitHub may redirect to "
        "Micro-Claw/microclaw. Private Git transport does not expose the immutable "
        "repository id, so this name redirect is recorded but not numerically verified."
    )
    updates._verify_clone_remote(state, "origin", updates.GIT_TIMEOUT_SECONDS)


def test_private_404_is_cached_and_preserves_last_success(tmp_path, monkeypatch):
    path = tmp_path / updates.STATE_NAME
    previous = {"checked_at": 1, "candidate": {"sha": "b" * 40}}
    updates.write_state({**updates.public_provenance(), "last_success": previous}, path)
    monkeypatch.setattr(sys, "platform", "win32")
    opener = SequenceOpener(http_error("https://api.github.com/x", 404))
    assert updates.check_for_update(state_file=path, opener=opener, now=100, jitter=lambda a, b: 0) is None
    state = updates.load_state(path)
    assert state["last_attempt"] == 100
    assert "404" in state["last_error"]
    assert state["last_success"] == previous


@pytest.mark.parametrize(
    ("response", "reason"),
    [(Response(b"not json"), "malformed API body"),
     (Response(api_body("short")), "non-40-hex")],
)
def test_public_malformed_metadata_is_distinguishable(response, reason):
    with pytest.raises(updates.UpdateError, match=reason):
        updates.discover_public(updates.public_provenance(), opener=SequenceOpener(response))


def test_truncated_archive_is_distinguishable(tmp_path):
    candidate = updates.Candidate("a" * 40, "x", "public-head")
    with pytest.raises(updates.UpdateError, match="truncated or invalid"):
        updates.materialize_public({}, candidate, tmp_path / "stage", opener=SequenceOpener(Response(b"PK")))


@pytest.mark.parametrize("name", ["/absolute.txt", "root/../escape.txt"])
def test_archive_rejects_unsafe_paths(tmp_path, name):
    candidate = updates.Candidate("a" * 40, "x", "public-head")
    data = zip_bytes([(name, b"bad")])
    with pytest.raises(updates.UpdateError, match="unsafe path"):
        updates.materialize_public({}, candidate, tmp_path / "stage", opener=SequenceOpener(Response(data)))


def test_archive_rejects_symlink(tmp_path):
    info = zipfile.ZipInfo("root/link")
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    candidate = updates.Candidate("a" * 40, "x", "public-head")
    with pytest.raises(updates.UpdateError, match="link or device"):
        updates.materialize_public(
            {}, candidate, tmp_path / "stage",
            opener=SequenceOpener(Response(zip_bytes([("ignored", info)]))),
        )


def test_plain_oversize_archive_is_rejected_before_extraction(tmp_path, monkeypatch):
    monkeypatch.setattr(updates, "MAX_DOWNLOAD_BYTES", 8)
    candidate = updates.Candidate("a" * 40, "x", "public-head")
    with pytest.raises(updates.UpdateError, match="download exceeds"):
        updates.materialize_public(
            {}, candidate, tmp_path / "stage", opener=SequenceOpener(Response(b"123456789"))
        )
    assert not (tmp_path / "stage").exists()


def test_public_materialization_is_pinned_to_full_sha(tmp_path):
    sha = "c" * 40
    candidate = updates.Candidate(sha, "x", "public-head", "Micro-Claw/microclaw")
    opener = SequenceOpener(Response(zip_bytes([("microclaw-root/file.txt", b"exact")])) )
    stage = updates.materialize_public({}, candidate, tmp_path / "stage", opener=opener)
    assert opener.urls == [f"https://codeload.github.com/Micro-Claw/microclaw/zip/{sha}"]
    assert (stage / "file.txt").read_bytes() == b"exact"
    updates.verify_staged_source(stage, sha)


def test_archive_rejects_extracted_size_over_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(updates, "MAX_EXTRACTED_BYTES", 3)
    candidate = updates.Candidate("a" * 40, "x", "public-head")

    class LyingArchive:
        """The member header claims one byte while its stream yields four."""
        def __init__(self, _stream):
            self.member = zipfile.ZipInfo("root/file.txt")
            self.member.file_size = 1

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def infolist(self):
            return [self.member]

        def open(self, _member):
            return io.BytesIO(b"four")

        def extractall(self, destination):
            target = Path(destination) / "root" / "file.txt"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"four")

    monkeypatch.setattr(updates.zipfile, "ZipFile", LyingArchive)
    with pytest.raises(updates.UpdateError, match="extracted size"):
        updates.materialize_public(
            {}, candidate, tmp_path / "stage", opener=SequenceOpener(Response(b"archive"))
        )


def test_redirect_to_different_repository_id_is_rejected():
    redirect = Response(status=301, headers={"Location": "https://api.github.com/repos/Else/other/commits/main"})
    opener = SequenceOpener(redirect, Response(api_body()), Response(json.dumps({"id": 7, "full_name": "Else/other"}).encode()))
    with pytest.raises(updates.UpdateError, match="id does not match"):
        updates.discover_public(updates.public_provenance(), opener=opener)


def test_redirect_with_matching_id_persists_canonical_name():
    redirect = Response(status=301, headers={"Location": "https://api.github.com/repos/New/home/commits/main"})
    metadata = json.dumps({"id": updates.REPO_ID, "full_name": "New/home"}).encode()
    opener = SequenceOpener(redirect, Response(api_body()), Response(metadata))
    state = updates.public_provenance()
    candidate = updates.discover_public(state, opener=opener)
    assert candidate.canonical_repo == "New/home"
    assert state["canonical_repo"] == "New/home"


def test_redirect_to_non_allowlisted_host_is_rejected_at_hop():
    opener = SequenceOpener(Response(status=302, headers={"Location": "https://evil.invalid/archive.zip"}))
    with pytest.raises(updates.UpdateError, match="not allowlisted"):
        updates.discover_public(updates.public_provenance(), opener=opener)
    assert opener.urls == [f"https://api.github.com/repos/{updates.REPO}/commits/main"]


def test_dirty_feature_checkout_is_unchanged_and_discovers_main(clone_pair):
    remote, seed, clone, first = clone_pair
    git(clone, "switch", "-c", "feature")
    (clone / "tracked.txt").write_text("dirty", encoding="utf-8")
    before = (git(clone, "status", "--porcelain=v1"), git(clone, "rev-parse", "HEAD"),
              git(clone, "branch", "--show-current"), (clone / "tracked.txt").read_bytes())
    newest = commit(seed, "new.txt", "two")
    git(seed, "push", "origin", "main")
    candidate = updates.discover_clone(clone_state(clone, first))
    after = (git(clone, "status", "--porcelain=v1"), git(clone, "rev-parse", "HEAD"),
             git(clone, "branch", "--show-current"), (clone / "tracked.txt").read_bytes())
    assert candidate.sha == newest
    assert after == before


def test_diverged_installed_commit_offers_nothing(clone_pair, tmp_path, monkeypatch):
    remote, seed, clone, first = clone_pair
    git(clone, "switch", "--orphan", "unrelated")
    divergent = commit(clone, "other.txt", "unrelated")
    state = clone_state(clone, divergent)
    path = tmp_path / updates.STATE_NAME
    updates.write_state(state, path)
    monkeypatch.setattr(sys, "platform", "win32")
    assert updates.check_for_update(state_file=path, now=10, jitter=lambda a, b: 0) is None
    cached = updates.load_state(path)
    assert cached["discovery"]["status"] == "diverged"
    assert "GitHub Desktop" in cached["discovery"]["message"]
    assert cached["last_error"] is None


def test_missing_installed_commit_is_reported_separately(clone_pair):
    remote, seed, clone, first = clone_pair
    state = clone_state(clone, "f" * 40)
    assert updates.discover_clone(state) is None
    assert state["discovery"]["status"] == "installed-commit-missing"
    assert "missing" in state["discovery"]["message"]


def test_current_clone_is_reported_separately(clone_pair):
    remote, seed, clone, first = clone_pair
    state = clone_state(clone, first)
    assert updates.discover_clone(state) is None
    assert state["discovery"] == {
        "status": "current", "message": "The installed commit is current.",
    }


@pytest.mark.parametrize("checkout", ["tracking-feature", "detached", "no-upstream"])
def test_clone_discovery_always_tracks_remote_main(clone_pair, checkout):
    remote, seed, clone, first = clone_pair
    if checkout == "tracking-feature":
        git(clone, "switch", "-c", "feature")
        git(clone, "remote", "add", "fork", str(remote))
        git(clone, "push", "-u", "fork", "feature")
    elif checkout == "detached":
        git(clone, "switch", "--detach", first)
    else:
        git(clone, "switch", "-c", "local-only")
    newest = commit(seed, f"{checkout}.txt", checkout)
    git(seed, "push", "origin", "main")
    state = clone_state(clone, first)
    assert state["tracked_branch"] == "main"
    assert state["remote"] == "origin"
    if checkout == "tracking-feature":
        assert state["upstream"] == "fork/feature"
    assert updates.discover_clone(state).sha == newest


def test_clone_repointed_to_another_repository_is_refused(clone_pair, tmp_path):
    remote, seed, clone, first = clone_pair
    other = tmp_path / "other.git"
    git(tmp_path, "init", "--bare", str(other))
    state = clone_state(clone, first)
    git(clone, "remote", "set-url", "origin", str(other))
    with pytest.raises(updates.UpdateError, match="repository identity"):
        updates.discover_clone(state)


def test_github_clone_repointed_after_provenance_is_refused(clone_pair):
    remote, seed, clone, first = clone_pair
    git(clone, "remote", "set-url", "origin", "https://github.com/Micro-Claw/microclaw.git")
    state = updates.clone_provenance(clone, first, git_executable=shutil_git())
    git(clone, "remote", "set-url", "origin", "git@github.com:someone-else/microclaw.git")
    with pytest.raises(updates.UpdateError, match="repository identity"):
        updates._verify_clone_remote(state, "origin", updates.GIT_TIMEOUT_SECONDS)


def test_noninteractive_fetch_failure_uses_desktop_guidance(tmp_path, monkeypatch):
    script = tmp_path / "git"
    script.write_text(
        "#!/bin/sh\n"
        "if test \"$GIT_TERMINAL_PROMPT\" != 0; then sleep 5; fi\n"
        "case \" $* \" in *' fetch '*) exit 1;; *' rev-parse '*) exit 1;; esac\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    state = {**updates.clone_provenance.__annotations__, "git_executable": str(script),
             "clone_path": str(tmp_path), "remote": "origin", "installed_commit": "a" * 40,
             "remote_identity": updates._normalize_remote_url("", tmp_path)}
    started = time.monotonic()
    with pytest.raises(updates.UpdateError, match="Open GitHub Desktop, Fetch origin, then Check again"):
        updates.discover_clone(state, timeout=1)
    assert time.monotonic() - started < 0.75


def test_git_archive_materializes_exact_sha_and_mismatch_refuses(clone_pair, tmp_path):
    remote, seed, clone, first = clone_pair
    second = commit(seed, "tracked.txt", "two")
    stage = updates.materialize_clone(
        clone_state(clone, first), updates.Candidate(first, "first", "clone"), tmp_path / "stage"
    )
    assert (stage / "tracked.txt").read_text(encoding="utf-8") == "one"
    assert second != first
    marker = stage / updates.STAGED_SOURCE_NAME
    marker.write_text(json.dumps({"commit": second}), encoding="utf-8")
    with pytest.raises(updates.UpdateError, match="does not match"):
        updates.verify_staged_source(stage, first)


def test_source_checkout_without_state_offers_nothing(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(sys, "platform", "win32")
    opener = SequenceOpener(Response(api_body()))
    assert updates.check_for_update(state_file=tmp_path / "missing.json", opener=opener) is None
    assert opener.urls == []


def test_corrupt_state_never_interrupts_startup_or_changes_bytes(tmp_path, monkeypatch):
    path = tmp_path / updates.STATE_NAME
    original = b"{broken\r\nprovenance might still be recoverable"
    path.write_bytes(original)
    monkeypatch.setattr(sys, "platform", "win32")
    assert updates.check_for_update(state_file=path, now=10, jitter=lambda a, b: 0) is None
    assert path.read_bytes() == original


def test_truncated_clone_state_never_interrupts_startup_and_caches_failure(tmp_path, monkeypatch):
    path = tmp_path / updates.STATE_NAME
    updates.write_state({"provenance": "clone", "clone_path": str(tmp_path)}, path)
    monkeypatch.setattr(sys, "platform", "win32")
    assert updates.check_for_update(state_file=path, now=10, jitter=lambda a, b: 0) is None
    state = updates.load_state(path)
    assert state["last_attempt"] == 10
    assert "missing git_executable" in state["last_error"]


def test_every_update_entry_point_is_silent_off_windows(tmp_path, monkeypatch):
    path = tmp_path / updates.STATE_NAME
    updates.write_state(updates.public_provenance(), path)
    monkeypatch.setattr(sys, "platform", "darwin")
    opener = SequenceOpener(Response(api_body()))
    assert updates.check_for_update(state_file=path, opener=opener) is None
    assert opener.urls == []


def test_slot_identity_follows_own_executable_through_activation_and_rollback(tmp_path):
    sha_a, sha_b = "a" * 40, "b" * 40
    exe_a, exe_b = tmp_path / "env-a" / "Scripts" / "python.exe", tmp_path / "env-b" / "Scripts" / "python.exe"
    exe_a.parent.mkdir(parents=True)
    exe_b.parent.mkdir(parents=True)
    updates.write_slot_marker(sha_a, 1, executable=exe_a)
    updates.write_slot_marker(sha_b, 1, executable=exe_b)
    assert updates.read_slot_marker(executable=exe_a)["commit"] == sha_a
    assert updates.read_slot_marker(executable=exe_b)["commit"] == sha_b  # activation
    assert updates.read_slot_marker(executable=exe_a)["commit"] == sha_a  # rollback


def _slot(root, name, sha="a" * 40, protocol=1):
    exe = root / f"env-{name}" / "Scripts" / "python.exe"
    updates.write_slot_marker(sha, protocol, executable=exe)
    return exe


def test_launcher_state_activates_and_consumes_pending(tmp_path):
    (tmp_path / updates.ACTIVE_SLOT_NAME).write_text("a\n", encoding="ascii")
    _slot(tmp_path, "b")
    (tmp_path / updates.PENDING_SLOT_NAME).write_text("b\n", encoding="ascii")
    assert updates.activate_pending(tmp_path, 1) == ("b", "a")
    assert (tmp_path / updates.ACTIVE_SLOT_NAME).read_text(encoding="ascii").strip() == "b"
    assert not (tmp_path / updates.PENDING_SLOT_NAME).exists()


def test_launcher_discards_pending_candidate_requiring_newer_protocol(tmp_path):
    (tmp_path / updates.ACTIVE_SLOT_NAME).write_text("a\n", encoding="ascii")
    _slot(tmp_path, "b", protocol=updates.LAUNCHER_PROTOCOL + 1)
    (tmp_path / updates.PENDING_SLOT_NAME).write_text("b\n", encoding="ascii")
    assert updates.activate_pending(tmp_path, 1) == ("a", None)
    assert (tmp_path / updates.ACTIVE_SLOT_NAME).read_text(encoding="ascii").strip() == "a"
    assert not (tmp_path / updates.PENDING_SLOT_NAME).exists()


def test_launcher_discards_pending_with_missing_metadata(tmp_path):
    (tmp_path / updates.ACTIVE_SLOT_NAME).write_text("a\n", encoding="ascii")
    (tmp_path / updates.PENDING_SLOT_NAME).write_text("b\n", encoding="ascii")
    assert updates.activate_pending(tmp_path, 1) == ("a", None)
    assert not (tmp_path / updates.PENDING_SLOT_NAME).exists()


def test_launcher_discards_corrupt_pending_selector_and_runs_known_good(tmp_path):
    (tmp_path / updates.ACTIVE_SLOT_NAME).write_text("a\n", encoding="ascii")
    (tmp_path / updates.PENDING_SLOT_NAME).write_text("not-a-slot\n", encoding="ascii")
    assert updates.activate_pending(tmp_path, 1) == ("a", None)
    assert not (tmp_path / updates.PENDING_SLOT_NAME).exists()


def test_fresh_nonce_removes_stale_marker_and_only_matching_health_passes(tmp_path):
    marker = tmp_path / updates.HEALTH_NAME
    marker.write_text("old-nonce\n", encoding="ascii")
    nonce, resolved = updates.fresh_launch(tmp_path, "a", nonce="new_nonce_123456")
    assert resolved == marker and not marker.exists()
    marker.write_text("old-nonce\n", encoding="ascii")
    assert not updates.health_matches(marker, nonce)
    marker.write_text(nonce + "\n", encoding="ascii")
    assert updates.health_matches(marker, nonce)


def test_health_wait_returns_child_exited_without_sleeping(tmp_path):
    sleeps = []
    verdict = updates.wait_for_launcher_health(
        tmp_path / "missing", "nonce_abcdefghijkl", 42,
        clock=lambda: 0, sleep=sleeps.append, alive=lambda pid: False,
    )
    assert verdict == "child-exited"
    assert sleeps == []


def test_health_wait_times_out_with_injected_clock(tmp_path):
    now = [0.0]
    sleeps = []

    def sleep(interval):
        sleeps.append(interval)
        now[0] += interval

    verdict = updates.wait_for_launcher_health(
        tmp_path / "missing", "nonce_abcdefghijkl", 42, timeout=0.2,
        poll_interval=0.1, clock=lambda: now[0], sleep=sleep, alive=lambda pid: True,
    )
    assert verdict == "timeout"
    assert sleeps == [0.1, 0.1]


def test_health_wait_accepts_marker_even_after_child_exits(tmp_path):
    marker = tmp_path / updates.HEALTH_NAME
    marker.write_text("nonce_abcdefghijkl\n", encoding="ascii")
    assert updates.wait_for_launcher_health(
        marker, "nonce_abcdefghijkl", 42, clock=lambda: 0,
        sleep=lambda interval: pytest.fail("healthy marker must not sleep"),
        alive=lambda pid: False,
    ) == "healthy"


def test_slot_metadata_for_other_slot_refuses_health(tmp_path):
    exe = _slot(tmp_path, "b")
    env = {updates.LAUNCHER_OWNED_ENV: "1", updates.LAUNCH_ROOT_ENV: str(tmp_path),
           updates.LAUNCH_SLOT_ENV: "a", updates.LAUNCH_NONCE_ENV: "nonce_abcdefghijkl"}
    with pytest.raises(updates.UpdateError, match="does not match"):
        updates.write_launcher_health(env, executable=exe)
    assert not (tmp_path / updates.HEALTH_NAME).exists()


@pytest.mark.parametrize("missing", [updates.LAUNCH_NONCE_ENV, updates.LAUNCH_SLOT_ENV])
def test_missing_launcher_identity_writes_no_health(tmp_path, missing):
    exe = _slot(tmp_path, "a")
    env = {updates.LAUNCHER_OWNED_ENV: "1", updates.LAUNCH_ROOT_ENV: str(tmp_path),
           updates.LAUNCH_SLOT_ENV: "a", updates.LAUNCH_NONCE_ENV: "nonce_abcdefghijkl"}
    del env[missing]
    with pytest.raises(updates.UpdateError, match="incomplete"):
        updates.write_launcher_health(env, executable=exe)
    assert not (tmp_path / updates.HEALTH_NAME).exists()


def test_rollback_restores_known_good_and_defers_report(tmp_path):
    (tmp_path / updates.ACTIVE_SLOT_NAME).write_text("b\n", encoding="ascii")
    assert updates.rollback_slot(tmp_path, "b", "a") == "a"
    assert (tmp_path / updates.ACTIVE_SLOT_NAME).read_text(encoding="ascii").strip() == "a"
    assert "rolled back from slot b" in (tmp_path / updates.ROLLBACK_NAME).read_text(encoding="utf-8")
    assert "rolled back" in updates.consume_rollback_report(tmp_path)
    assert updates.consume_rollback_report(tmp_path) is None


def test_failed_uv_stage_keeps_active_selector_and_publishes_no_pending(tmp_path, monkeypatch):
    updates.write_state({"provenance": "public-head", "next_check": 100},
                        tmp_path / updates.STATE_NAME)
    (tmp_path / updates.LAUNCHER_PROTOCOL_NAME).write_text("1\n", encoding="ascii")
    source = tmp_path / "source"
    (source / "scripts").mkdir(parents=True)
    (source / "scripts" / updates.LAUNCHER_PROTOCOL_NAME).write_text("1\n", encoding="ascii")
    active_path = tmp_path / updates.ACTIVE_SLOT_NAME
    active_path.write_bytes(b"a\n")
    calls = []

    def fail(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 1, "", "PyPI unavailable")

    monkeypatch.setattr(updates.subprocess, "run", fail)
    candidate = updates.Candidate("b" * 40, "candidate", "public-head")
    with pytest.raises(updates.UpdateError, match="the update could not be built"):
        updates.stage_inactive_slot(tmp_path, source, candidate,
                                    uv_executable="uv.exe", now=10)
    assert calls and all(str(tmp_path / "env-b") in " ".join(call) for call in calls)
    assert active_path.read_bytes() == b"a\n"
    assert not (tmp_path / updates.PENDING_SLOT_NAME).exists()
    assert updates.load_state(tmp_path / updates.STATE_NAME)["build_failed_commit"] == candidate.sha
    calls.clear()
    with pytest.raises(updates.UpdateError, match="the update could not be built"):
        updates.stage_inactive_slot(tmp_path, source, candidate,
                                    uv_executable="uv.exe", now=11)
    assert calls == []


def test_staging_reads_candidate_protocol_and_refuses_old_installed_launcher(tmp_path):
    updates.write_state({"provenance": "public-head"}, tmp_path / updates.STATE_NAME)
    (tmp_path / updates.ACTIVE_SLOT_NAME).write_text("a\n", encoding="ascii")
    (tmp_path / updates.LAUNCHER_PROTOCOL_NAME).write_text("1\n", encoding="ascii")
    source = tmp_path / "source"
    (source / "scripts").mkdir(parents=True)
    (source / "scripts" / updates.LAUNCHER_PROTOCOL_NAME).write_text("2\n", encoding="ascii")
    with pytest.raises(updates.UpdateError, match="bootstrap the launcher"):
        updates.stage_inactive_slot(
            tmp_path, source, updates.Candidate("c" * 40, "new", "clone"),
            uv_executable="uv.exe",
        )


def test_config_comparison_refusal_is_before_pending_publish(tmp_path, monkeypatch):
    updates.write_state({"provenance": "public-head"}, tmp_path / updates.STATE_NAME)
    (tmp_path / updates.ACTIVE_SLOT_NAME).write_text("a\n", encoding="ascii")
    (tmp_path / updates.PENDING_SLOT_NAME).write_text("a\n", encoding="ascii")
    (tmp_path / updates.LAUNCHER_PROTOCOL_NAME).write_text("1\n", encoding="ascii")
    source = tmp_path / "source"
    (source / "scripts").mkdir(parents=True)
    (source / "scripts" / updates.LAUNCHER_PROTOCOL_NAME).write_text("1\n", encoding="ascii")

    commands = []

    def run(command, **kwargs):
        if command[1] == "venv":
            (tmp_path / "env-b" / "Scripts").mkdir(parents=True)
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[0] == "uv.exe":
            return subprocess.CompletedProcess(command, 0, "", "")
        commands.append(command)
        classification = "ready" if "env-a" in command[0] else "blocked"
        return subprocess.CompletedProcess(
            command, 0, json.dumps({"classification": classification}), ""
        )

    monkeypatch.setattr(updates.subprocess, "run", run)
    candidate = updates.Candidate("d" * 40, "unsafe", "public-head")
    with pytest.raises(updates.UpdateError, match="downgrade a reviewed config"):
        updates.stage_inactive_slot(tmp_path, source, candidate, uv_executable="uv.exe")
    assert [command[0] for command in commands] == [
        str(tmp_path / "env-a" / "Scripts" / "microclaw.exe"),
        str(tmp_path / "env-b" / "Scripts" / "microclaw.exe"),
    ]
    assert (tmp_path / updates.PENDING_SLOT_NAME).read_text(encoding="ascii") == "a\n"
    state = updates.load_state(tmp_path / updates.STATE_NAME)
    assert state["comparison_refused_commit"] == candidate.sha
    assert "downgrade a reviewed config" in state["comparison_refusal_reason"]


def test_successful_stage_clears_stale_build_failure(tmp_path, monkeypatch):
    updates.write_state({
        "provenance": "public-head", "build_error": "old failure",
        "build_failed_commit": "c" * 40,
    }, tmp_path / updates.STATE_NAME)
    (tmp_path / updates.ACTIVE_SLOT_NAME).write_text("a\n", encoding="ascii")
    (tmp_path / updates.LAUNCHER_PROTOCOL_NAME).write_text("1\n", encoding="ascii")
    source = tmp_path / "source"
    (source / "scripts").mkdir(parents=True)
    (source / "scripts" / updates.LAUNCHER_PROTOCOL_NAME).write_text("1\n", encoding="ascii")

    def run(command, **kwargs):
        if command[1] == "venv":
            (tmp_path / "env-b" / "Scripts").mkdir(parents=True)
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[0] == "uv.exe":
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.CompletedProcess(command, 0, '{"classification":"ready"}', "")

    monkeypatch.setattr(updates.subprocess, "run", run)
    updates.stage_inactive_slot(
        tmp_path, source, updates.Candidate("d" * 40, "fixed", "public-head"),
        uv_executable="uv.exe",
    )
    state = updates.load_state(tmp_path / updates.STATE_NAME)
    assert "build_error" not in state
    assert "build_failed_commit" not in state
    assert (tmp_path / updates.PENDING_SLOT_NAME).read_text(encoding="ascii") == "b\n"


def test_slot_marker_deliberately_accepts_unknown_for_public_zip(tmp_path):
    exe = tmp_path / "env-a" / "Scripts" / "python.exe"
    updates.write_slot_marker("unknown", 1, executable=exe)
    assert updates.read_slot_marker(executable=exe)["commit"] == "unknown"


def test_installer_provenance_degrades_visibly_when_git_is_unavailable(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(
        updates, "clone_provenance",
        lambda *args, **kwargs: (_ for _ in ()).throw(updates.UpdateError("Git was not found")),
    )
    state, note = updates.installer_provenance(tmp_path, "d" * 40)
    assert state["provenance"] == "public-head"
    assert state["installed_commit"] == "d" * 40
    assert "Git was not found" in note
    assert "updates will follow public head" in note


def test_interval_jitter_and_cached_failure_suppress_retry(tmp_path, monkeypatch):
    path = tmp_path / updates.STATE_NAME
    updates.write_state(updates.public_provenance(), path)
    monkeypatch.setattr(sys, "platform", "win32")
    opener = SequenceOpener(http_error("https://api.github.com/x", 404))
    updates.check_for_update(state_file=path, opener=opener, now=10, jitter=lambda a, b: 123)
    state = updates.load_state(path)
    assert state["next_check"] == 10 + updates.CHECK_INTERVAL_SECONDS + 123
    assert updates.check_for_update(state_file=path, opener=opener, now=11) is None
    assert len(opener.urls) == 1


def test_opt_out_flag_and_environment_make_no_attempt(tmp_path, monkeypatch):
    path = tmp_path / updates.STATE_NAME
    updates.write_state(updates.public_provenance(), path)
    monkeypatch.setattr(sys, "platform", "win32")
    opener = SequenceOpener(Response(api_body()), Response(api_body()))
    assert updates.check_for_update(state_file=path, no_update_check=True, opener=opener, now=1) is None
    monkeypatch.setenv("MICROCLAW_UPDATE_CHECK", "0")
    assert updates.check_for_update(state_file=path, opener=opener, now=2) is None
    assert "last_attempt" not in updates.load_state(path)
    assert opener.urls == []


def test_update_facility_is_not_an_agent_tool_or_schema():
    from microclaw.tools import TOOL_REGISTRY
    from microclaw.tools_schema import TOOLS
    names = set(TOOL_REGISTRY)
    schema_names = {item["name"] for item in TOOLS}
    assert names == schema_names
    assert not any("update" in name for name in names)


def test_top_level_parser_defines_no_update_check_beside_safety_config():
    source = (Path(__file__).parents[1] / "microclaw" / "__main__.py").read_text(encoding="utf-8")
    assert source.index('"--safety-config"') < source.index('"--no-update-check"') < source.index("sub = parser.add_subparsers")


def test_restart_request_is_consumed_only_by_matching_child(tmp_path):
    nonce = "child_nonce_123456"
    assert updates.consume_restart_request(tmp_path, nonce) is False
    updates.write_restart_request(tmp_path, "different_nonce_1234")
    assert updates.consume_restart_request(tmp_path, nonce) is False
    assert not (tmp_path / updates.RESTART_REQUEST_NAME).exists()
    updates.write_restart_request(tmp_path, nonce)
    assert updates.consume_restart_request(tmp_path, nonce) is True
    assert updates.consume_restart_request(tmp_path, nonce) is False


def test_fresh_launch_removes_stale_restart_request(tmp_path):
    stale = tmp_path / updates.RESTART_REQUEST_NAME
    stale.write_text("stale_nonce_123456\n", encoding="ascii")
    updates.fresh_launch(tmp_path, "a", nonce="fresh_nonce_123456")
    assert not stale.exists()


def test_activation_and_rollback_reconcile_commit_from_slot_marker(tmp_path):
    old, new = "a" * 40, "b" * 40
    updates.write_state(updates.public_provenance(old), tmp_path / updates.STATE_NAME)
    (tmp_path / updates.ACTIVE_SLOT_NAME).write_text("a\n", encoding="ascii")
    (tmp_path / updates.PENDING_SLOT_NAME).write_text("b\n", encoding="ascii")
    updates.write_slot_marker(old, 1, executable=tmp_path / "env-a" / "Scripts" / "python.exe")
    updates.write_slot_marker(new, 1, executable=tmp_path / "env-b" / "Scripts" / "python.exe")
    assert updates.activate_pending(tmp_path, 1) == ("b", "a")
    assert updates.load_state(tmp_path / updates.STATE_NAME)["installed_commit"] == new
    updates.rollback_slot(tmp_path, "b", "a")
    assert updates.load_state(tmp_path / updates.STATE_NAME)["installed_commit"] == old


def test_unknown_slot_marker_does_not_replace_arranged_installed_commit(tmp_path):
    arranged = "c" * 40
    updates.write_state(updates.public_provenance(arranged), tmp_path / updates.STATE_NAME)
    (tmp_path / updates.ACTIVE_SLOT_NAME).write_text("a\n", encoding="ascii")
    (tmp_path / updates.PENDING_SLOT_NAME).write_text("b\n", encoding="ascii")
    updates.write_slot_marker("unknown", 1, executable=tmp_path / "env-b" / "Scripts" / "python.exe")
    updates.activate_pending(tmp_path, 1)
    assert updates.load_state(tmp_path / updates.STATE_NAME)["installed_commit"] == arranged


def test_malformed_marker_commit_does_not_block_pending_activation(tmp_path):
    old = "a" * 40
    updates.write_state(updates.public_provenance(old), tmp_path / updates.STATE_NAME)
    (tmp_path / updates.ACTIVE_SLOT_NAME).write_text("a\n", encoding="ascii")
    (tmp_path / updates.PENDING_SLOT_NAME).write_text("b\n", encoding="ascii")
    marker = updates.slot_marker_path(tmp_path / "env-b" / "Scripts" / "python.exe")
    marker.parent.mkdir(parents=True)
    marker.write_text(
        json.dumps({"commit": "malformed", "required_launcher_protocol": 1}),
        encoding="utf-8",
    )
    assert updates.activate_pending(tmp_path, 1) == ("b", "a")
    assert (tmp_path / updates.ACTIVE_SLOT_NAME).read_text(encoding="ascii").strip() == "b"
    assert not (tmp_path / updates.PENDING_SLOT_NAME).exists()
    assert updates.load_state(tmp_path / updates.STATE_NAME)["installed_commit"] == old


def test_reconciliation_write_failure_does_not_block_rollback_report(tmp_path, monkeypatch):
    updates.write_state(updates.public_provenance("b" * 40), tmp_path / updates.STATE_NAME)
    (tmp_path / updates.ACTIVE_SLOT_NAME).write_text("b\n", encoding="ascii")
    updates.write_slot_marker(
        "a" * 40, 1, executable=tmp_path / "env-a" / "Scripts" / "python.exe",
    )
    monkeypatch.setattr(updates, "write_state", lambda *a, **k: (_ for _ in ()).throw(OSError("locked")))
    assert updates.rollback_slot(tmp_path, "b", "a") == "a"
    assert (tmp_path / updates.ACTIVE_SLOT_NAME).read_text(encoding="ascii").strip() == "a"
    assert "rolled back" in (tmp_path / updates.ROLLBACK_NAME).read_text(encoding="utf-8")


def test_terminal_notice_reads_cached_candidate_and_public_404(tmp_path):
    path = tmp_path / updates.STATE_NAME
    candidate = updates.Candidate("a" * 40, "Useful change", "public-head")
    state = updates.public_provenance()
    state["last_success"] = {"candidate": candidate.__dict__}
    updates.write_state(state, path)
    assert updates.terminal_update_notice(state_file=path) == (
        "A newer Microclaw commit is available: aaaaaaa — Useful change.", candidate,
    )
    state.pop("last_success")
    state["last_error"] = "repository is not public (404)"
    updates.write_state(state, path)
    assert updates.terminal_update_notice(state_file=path) == (
        "Automatic updates become available when the repository is public.", None,
    )


def test_terminal_notice_unmanaged_install_is_silent_and_untouched(tmp_path):
    path = tmp_path / updates.STATE_NAME
    assert updates.terminal_update_notice(state_file=path) == (None, None)
    assert not path.exists()


def test_start_due_check_is_shared_nonblocking_opt_out_boundary(monkeypatch):
    started = []
    monkeypatch.setattr(updates, "check_for_update", lambda **kwargs: started.append(kwargs))
    thread = updates.start_due_check(False)
    thread.join(timeout=1)
    assert started == [{"no_update_check": False}]
    assert updates.start_due_check(True) is None


def test_stage_cached_candidate_uses_shared_materialize_and_slot_builder(tmp_path, monkeypatch):
    path = tmp_path / updates.STATE_NAME
    updates.write_state(updates.public_provenance(), path)
    monkeypatch.setattr(updates, "state_path", lambda: path)
    calls = []
    monkeypatch.setattr(
        updates, "materialize_public",
        lambda state, candidate, source: calls.append(("materialize", candidate.sha)) or source.mkdir(),
    )
    monkeypatch.setattr(updates.shutil, "which", lambda name: "uv.exe")
    monkeypatch.setattr(
        updates, "stage_inactive_slot",
        lambda root, source, candidate, **kwargs: calls.append(
            ("stage", root, candidate.sha, kwargs["config_path"])
        ) or (root / "env-b"),
    )
    candidate = updates.Candidate("a" * 40, "Useful", "public-head")
    config = tmp_path / "safety.yaml"
    assert updates.stage_cached_candidate(candidate, config_path=config) == tmp_path / "env-b"
    assert calls == [
        ("materialize", candidate.sha),
        ("stage", tmp_path, candidate.sha, config),
    ]
    assert list((tmp_path / "downloads").iterdir()) == []
