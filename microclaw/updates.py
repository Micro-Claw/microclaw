"""Managed-install update provenance, discovery, and source materialization.

This module deliberately has no connection to the agent tool surface.  It is a
Windows launcher/UI facility whose only authority is the managed install's
``update-state.json`` and the marker beside the running interpreter.
"""
from __future__ import annotations

import io
import json
import os
import random
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from microclaw.paths import user_data_dir

REPO_ID = 1238975695
REPO = "Micro-Claw/microclaw"
BRANCH = "main"

STATE_NAME = "update-state.json"
SLOT_NAME = "microclaw-slot.json"
STAGED_SOURCE_NAME = "microclaw-staged-source.json"
CHECK_INTERVAL_SECONDS = 24 * 60 * 60
CHECK_JITTER_SECONDS = 60 * 60
GIT_TIMEOUT_SECONDS = 15
HTTP_TIMEOUT_SECONDS = 8
MAX_REDIRECTS = 3
MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024
MAX_EXTRACTED_BYTES = 256 * 1024 * 1024
GITHUB_HOSTS = frozenset({"api.github.com", "codeload.github.com", "github.com"})
_SHA = re.compile(r"^[0-9a-fA-F]{40}$")
LAUNCHER_PROTOCOL = 1
ACTIVE_SLOT_NAME = "active-slot.txt"
PENDING_SLOT_NAME = "pending-slot.txt"
HEALTH_NAME = "launch-health.txt"
ROLLBACK_NAME = "rollback-report.txt"
LAUNCHER_OWNED_ENV = "MICROCLAW_LAUNCHER_OWNED"
LAUNCH_NONCE_ENV = "MICROCLAW_LAUNCH_NONCE"
LAUNCH_SLOT_ENV = "MICROCLAW_LAUNCH_SLOT"
LAUNCH_ROOT_ENV = "MICROCLAW_LAUNCH_ROOT"
LAUNCH_PROTOCOL_ENV = "MICROCLAW_LAUNCHER_PROTOCOL"
LAUNCHER_PROTOCOL_NAME = "launcher-protocol.txt"
RESTART_REQUEST_NAME = "restart-request.txt"
#: Shared state is written by the server, the launcher and the installer, and
#: read by every /api/update poll. See write_state for why this needs retries.
STATE_REPLACE_ATTEMPTS = 10
STATE_REPLACE_BACKOFF_SECONDS = 0.05


class UpdateError(Exception):
    """An update source was unavailable or failed validation."""


class ComparisonRefused(UpdateError):
    """Staging stopped because the candidate would weaken the reviewed config.

    A distinct type because the caller must be able to tell *this* attempt's
    refusal from any other failure.  webserve's staging job used to infer it by
    reading `comparison_refused_commit` back out of shared state, which is a
    record of *some* refusal for that commit, not this attempt's -- so once a
    commit had been refused once, every later failure of it was recorded as
    nothing at all: no error, no status, `staging` stuck on "running"."""


@dataclass(frozen=True)
class Candidate:
    sha: str
    subject: str
    source: str
    canonical_repo: str = REPO
    warning: str | None = None


def _read_slot_text(path: Path, *, required: bool) -> str | None:
    """Read one launcher-owned slot selector; JSON is intentionally not accepted."""
    try:
        value = path.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        if not required:
            return None
        raise UpdateError(f"missing launcher state: {path.name}") from None
    except (OSError, UnicodeError) as exc:
        raise UpdateError(f"invalid launcher state {path.name}: {exc}") from exc
    if value not in {"a", "b"}:
        raise UpdateError(f"{path.name} must contain exactly 'a' or 'b'")
    return value


def _write_slot_text(path: Path, slot: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(slot + "\n", encoding="ascii")
    os.replace(temporary, path)


def installed_launcher_protocol(root: str | Path) -> int:
    path = Path(root) / LAUNCHER_PROTOCOL_NAME
    try:
        value = int(path.read_text(encoding="ascii").strip())
    except (FileNotFoundError, OSError, UnicodeError, ValueError) as exc:
        raise UpdateError(f"invalid installed launcher protocol: {exc}") from exc
    if value < 1:
        raise UpdateError("invalid installed launcher protocol")
    return value


def candidate_launcher_protocol(source: str | Path) -> int:
    """Read the candidate's source-controlled minimum before building it."""
    path = Path(source) / "scripts" / LAUNCHER_PROTOCOL_NAME
    try:
        value = int(path.read_text(encoding="ascii").strip())
    except (FileNotFoundError, OSError, UnicodeError, ValueError) as exc:
        raise UpdateError(f"candidate has no valid launcher protocol declaration: {exc}") from exc
    if value < 1:
        raise UpdateError("candidate has no valid launcher protocol declaration")
    return value


def valid_pending_slot(root: str | Path, launcher_protocol: int) -> str | None:
    """Return the distinct pending slot only when its marker fits this launcher."""
    base = Path(root)
    active = _read_slot_text(base / ACTIVE_SLOT_NAME, required=True)
    pending = _read_slot_text(base / PENDING_SLOT_NAME, required=False)
    if pending is None or pending == active:
        return None
    marker = read_slot_marker(executable=base / f"env-{pending}" / "Scripts" / "python.exe")
    required = marker.get("required_launcher_protocol") if marker else None
    if type(required) is not int or required > launcher_protocol:
        raise UpdateError("pending slot is incompatible with the installed launcher")
    return pending


def retract_pending_slot(root: str | Path) -> str | None:
    """Discard a staged-but-never-restarted slot.  The installer alone does this.

    `install.bat` builds the *active* slot and records its commit.  A
    `pending-slot.txt` left standing by an update that was staged and never
    restarted makes the very next launch switch away from that slot and
    reconcile `installed_commit` from its marker -- so the reinstall is
    silently discarded, at whatever commit the other slot happens to hold.
    Reinstalling is the documented recovery path from a broken update, and the
    state a broken update leaves behind must not be able to defeat it.

    The installer is outside both slots, which is what makes it the one thing
    allowed to retract a pending answer.  An unreadable file is removed too: a
    pending selector nobody can parse is not a pending update.  Returns the
    slot discarded, or None.
    """
    path = Path(root) / PENDING_SLOT_NAME
    try:
        pending = path.read_text(encoding="ascii").strip() or None
    except (FileNotFoundError, OSError, UnicodeError):
        pending = None
    path.unlink(missing_ok=True)
    return pending


def _reconcile_installed_commit(base: Path, slot: str) -> None:
    """Best-effort bookkeeping; selector changes must never depend on it."""
    try:
        marker = read_slot_marker(executable=base / f"env-{slot}" / "Scripts" / "python.exe")
        commit = marker.get("commit") if marker else None
        if commit == "unknown" or not isinstance(commit, str) or not _SHA.fullmatch(commit):
            return
        state = load_state(base / STATE_NAME)
        if state is None:
            return
        if state.get("installed_commit") == commit.lower():
            return
        state["installed_commit"] = commit.lower()
        # The cached discovery was computed against the *previous* install, and
        # `check_for_update` -- its only writer -- is not due again for a day.
        # Left standing it offers the commit that just became the running one,
        # and staging that answer rebuilds the other slot at the same SHA and
        # re-arms the same banner: the update that never registers.  A slot
        # change invalidates the cache and makes the next launch's check due.
        success = state.get("last_success")
        if isinstance(success, dict):
            success["candidate"] = None
        state.pop("next_check", None)
        state.pop("staging", None)
        # `discovery` is a diagnostic nothing in the product reads -- which is
        # exactly why it must not lie: it is what someone opens the state file
        # to consult, and after activation "A newer main commit is available"
        # names the commit now running.
        state.pop("discovery", None)
        write_state(state, base / STATE_NAME)
    except Exception:
        return


def activate_pending(root: str | Path, launcher_protocol: int) -> tuple[str, str | None]:
    """Consume pending before launch; invalid candidates leave known-good active."""
    base = Path(root)
    active_path = base / ACTIVE_SLOT_NAME
    active = _read_slot_text(active_path, required=True)
    pending_path = base / PENDING_SLOT_NAME
    try:
        pending = _read_slot_text(pending_path, required=False)
    except UpdateError:
        pending_path.unlink(missing_ok=True)
        return active, None
    if pending is None:
        return active, None
    try:
        pending = valid_pending_slot(base, launcher_protocol)
    except (UpdateError, OSError, UnicodeError):
        pending_path.unlink(missing_ok=True)
        return active, None
    if pending is None:
        pending_path.unlink(missing_ok=True)
        return active, None
    _write_slot_text(active_path, pending)
    pending_path.unlink()
    _reconcile_installed_commit(base, pending)
    return pending, active


def fresh_launch(root: str | Path, slot: str, *, nonce: str | None = None) -> tuple[str, Path]:
    """Remove stale health and return the fresh nonce and marker path for one child."""
    import secrets

    if slot not in {"a", "b"}:
        raise UpdateError("launch slot must be 'a' or 'b'")
    token = nonce or secrets.token_hex(16)
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", token):
        raise UpdateError("invalid launch nonce")
    marker = Path(root) / HEALTH_NAME
    marker.unlink(missing_ok=True)
    (Path(root) / RESTART_REQUEST_NAME).unlink(missing_ok=True)
    return token, marker


def write_restart_request(root: str | Path, nonce: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", nonce):
        raise UpdateError("invalid launch nonce")
    target = Path(root) / RESTART_REQUEST_NAME
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(nonce + "\n", encoding="ascii")
    os.replace(temporary, target)
    return target


def consume_restart_request(root: str | Path, nonce: str) -> bool:
    """Consume one request, accepting it only for the child that just exited."""
    path = Path(root) / RESTART_REQUEST_NAME
    try:
        requested = path.read_text(encoding="ascii").strip()
    except (FileNotFoundError, OSError, UnicodeError):
        return False
    path.unlink(missing_ok=True)
    return requested == nonce


def health_matches(path: str | Path, nonce: str) -> bool:
    """A marker is healthy only when its complete contents match this child nonce."""
    try:
        return Path(path).read_text(encoding="ascii").strip() == nonce
    except (FileNotFoundError, OSError, UnicodeError):
        return False


def _windows_process_alive(pid: int) -> bool:
    """Whether a Windows child still has STILL_ACTIVE as its exit code.

    Every uncertain answer here is reported as *alive*.  Reporting a live child
    as exited is the harmful direction: the launcher would roll back a slot
    whose server is still running and still holding the port.  Only
    ERROR_INVALID_PARAMETER — what Windows returns for a pid that no longer
    exists — is treated as proof of exit.
    """
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
    if not handle:
        return ctypes.get_last_error() != 87  # ERROR_INVALID_PARAMETER
    try:
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return True
        return exit_code.value == 259  # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def wait_for_launcher_health(
    path: str | Path, nonce: str, child_pid: int, *, timeout: float = 30,
    poll_interval: float = 0.1, clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    alive: Callable[[int], bool] | None = None,
) -> str:
    """Wait in one Python process; return healthy, child-exited, or timeout."""
    is_alive = alive or _windows_process_alive
    deadline = clock() + timeout
    while True:
        if health_matches(path, nonce):
            return "healthy"
        if not is_alive(child_pid):
            return "child-exited"
        if clock() >= deadline:
            return "timeout"
        sleep(poll_interval)


def rollback_slot(root: str | Path, failed: str, previous: str | None) -> str:
    """Restore the retained known-good slot and defer the report to its next launch."""
    if failed not in {"a", "b"} or previous not in {"a", "b"} or failed == previous:
        raise UpdateError("rollback requires distinct valid failed and previous slots")
    base = Path(root)
    _write_slot_text(base / ACTIVE_SLOT_NAME, previous)
    _reconcile_installed_commit(base, previous)
    (base / ROLLBACK_NAME).write_text(
        f"Microclaw rolled back from slot {failed} to slot {previous}.\n", encoding="utf-8"
    )
    return previous


def consume_rollback_report(root: str | Path) -> str | None:
    path = Path(root) / ROLLBACK_NAME
    try:
        report = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    path.unlink()
    return report


def validate_launch_environment(
    environ: dict[str, str] | None = None, *, executable: str | Path | None = None,
) -> tuple[Path, str, str] | None:
    """Validate launcher nonce and the executing slot's immutable metadata."""
    env = os.environ if environ is None else environ
    if env.get(LAUNCHER_OWNED_ENV) != "1":
        return None
    root_text, slot, nonce, protocol_text = (
        env.get(LAUNCH_ROOT_ENV), env.get(LAUNCH_SLOT_ENV), env.get(LAUNCH_NONCE_ENV),
        env.get(LAUNCH_PROTOCOL_ENV),
    )
    if not root_text or slot not in {"a", "b"} or not nonce:
        raise UpdateError("launcher health environment is incomplete")
    root = Path(root_text).resolve()
    exe = Path(executable or sys.executable).resolve()
    expected = (root / f"env-{slot}").resolve()
    marker_path = slot_marker_path(exe)
    if marker_path.parent != expected:
        raise UpdateError("launcher slot does not match the executing environment")
    marker = read_slot_marker(executable=exe)
    if not marker:
        raise UpdateError("executing slot has no metadata")
    try:
        launcher_protocol = int(protocol_text or "")
    except ValueError as exc:
        raise UpdateError("launcher health environment is incomplete") from exc
    required = marker.get("required_launcher_protocol")
    if type(required) is not int or required > launcher_protocol:
        raise UpdateError("executing slot requires a newer launcher protocol")
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", nonce):
        raise UpdateError("invalid launch nonce")
    return root, slot, nonce


def write_launcher_health(
    environ: dict[str, str] | None = None, *, executable: str | Path | None = None,
) -> Path | None:
    """Write health only for a valid launcher-owned, metadata-matched process."""
    launch = validate_launch_environment(environ, executable=executable)
    if launch is None:
        return None
    root, _slot, nonce = launch
    target = root / HEALTH_NAME
    temporary = root / f".{HEALTH_NAME}.tmp"
    temporary.write_text(nonce + "\n", encoding="ascii")
    os.replace(temporary, target)
    return target


def stage_inactive_slot(
    root: str | Path, source: str | Path, candidate: Candidate, *,
    uv_executable: str | Path, config_path: str | Path | None = None,
    now: float | None = None,
) -> Path:
    """Build only the managed inactive slot and publish pending after success.

    This deliberately derives no target from the running interpreter, PATH, a
    conda prefix, or clone provenance.  A failed dependency install leaves the
    selector files untouched and is cached as this interval's build failure.
    """
    base = Path(root).resolve()
    if not (base / STATE_NAME).is_file():
        raise UpdateError("staging is available only in a managed installation")
    state = load_state(base / STATE_NAME) or {}
    current = time.time() if now is None else now
    if (state.get("build_failed_commit") == candidate.sha
            and isinstance(state.get("next_check"), (int, float))
            and current < state["next_check"]):
        raise UpdateError("the update could not be built")
    required_launcher_protocol = candidate_launcher_protocol(source)
    if required_launcher_protocol > installed_launcher_protocol(base):
        raise UpdateError(
            "installed launcher is too old for this update; run the installer once "
            "to bootstrap the launcher"
        )
    active = _read_slot_text(base / ACTIVE_SLOT_NAME, required=True)
    inactive = "b" if active == "a" else "a"
    target = base / f"env-{inactive}"
    python = target / "Scripts" / "python.exe"
    commands = (
        # --clear because the inactive slot is normally already an environment:
        # every machine that has updated once has one here, and uv refuses to
        # create over an existing venv (exit 2, "A virtual environment already
        # exists at").  Staging always replaces rather than reuses -- the slot
        # is being rebuilt at a different commit -- so this is not install.bat's
        # probe-then-reuse case.  The known-good slot is the *active* one, which
        # staging never touches, so clearing the inactive slot cannot remove the
        # copy a rollback would return to.
        [str(uv_executable), "venv", "--clear", "--python", "3.12", str(target)],
        # `[serve]`, exactly as install.bat's MC_SPEC does.  Without the extra
        # the slot has no fastapi and no uvicorn, so `microclaw serve` -- which
        # is the only thing the desktop icon ever runs -- exits on its import
        # guard.  Block 58e's fifth demo gate activated such a slot: the update
        # succeeded, the restart succeeded, and the application could no longer
        # start.  An updater that bricks the thing it updates is the worst
        # failure this design can have.
        [str(uv_executable), "pip", "install", "--python", str(python),
         f"{source}[serve]"],
    )
    for command in commands:
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        if completed.returncode:
            state = load_state(base / STATE_NAME) or {}
            state["build_error"] = "the update could not be built"
            state["build_failed_commit"] = candidate.sha
            # The user-facing sentence stays deliberately plain; this is the
            # diagnostic, because a rig that reports only "could not be built"
            # cannot be debugged without another trip.
            detail = (completed.stderr or completed.stdout or "").strip()
            state["build_error_detail"] = f"uv {command[1]} exit {completed.returncode}: {detail[-2000:]}"
            write_state(state, base / STATE_NAME)
            raise UpdateError("the update could not be built")
    # The bounded smoke check the design has always called for and the code
    # never had: prove the staged slot can actually start before anything
    # publishes it as pending.  `serve` imports uvicorn lazily, so naming it
    # here is the difference between catching a missing extra and shipping it.
    smoke = subprocess.run(
        [str(python), "-I", "-c", "import microclaw, microclaw.webserve, uvicorn"],
        capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL,
    )
    if smoke.returncode:
        state = load_state(base / STATE_NAME) or {}
        state["build_error"] = "the update could not be built"
        state["build_failed_commit"] = candidate.sha
        state["build_error_detail"] = (
            f"staged slot failed its smoke check: {(smoke.stderr or smoke.stdout).strip()[-2000:]}"
        )
        write_state(state, base / STATE_NAME)
        raise UpdateError("the update could not be built")
    write_slot_marker(
        candidate.sha, required_launcher_protocol, executable=python,
    )
    # Each slot must classify through its own interpreter.  This belongs before
    # pending publish: pending-slot.txt is the launcher's activation command.
    from microclaw import config
    active_console = base / f"env-{active}" / "Scripts" / "microclaw.exe"
    candidate_console = target / "Scripts" / "microclaw.exe"
    comparison = config.compare_slot_configurations(
        active_console, candidate_console,
        config_path or config.default_safety_config(),
    )
    state = load_state(base / STATE_NAME) or {}
    if not comparison.proceed:
        state["comparison_refused_commit"] = candidate.sha
        state["comparison_refusal_reason"] = comparison.reason
        state["staging"] = {"status": "refused", "commit": candidate.sha}
        write_state(state, base / STATE_NAME)
        raise ComparisonRefused(comparison.reason or "the update needs the maintainer")
    state.pop("comparison_refused_commit", None)
    state.pop("comparison_refusal_reason", None)
    state["staging"] = {"status": "staged", "commit": candidate.sha}
    state.pop("build_error", None)
    state.pop("build_failed_commit", None)
    state.pop("build_error_detail", None)
    write_state(state, base / STATE_NAME)
    temporary = base / f".{PENDING_SLOT_NAME}.tmp"
    temporary.write_text(inactive + "\n", encoding="ascii")
    os.replace(temporary, base / PENDING_SLOT_NAME)
    return target


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _default_opener(request: urllib.request.Request, timeout: float):
    return urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout)


URLopener = Callable[[urllib.request.Request, float], Any]


def state_path() -> Path:
    return user_data_dir() / STATE_NAME


def load_state(path: str | Path | None = None) -> dict[str, Any] | None:
    target = Path(path) if path is not None else state_path()
    for attempt in range(STATE_REPLACE_ATTEMPTS):
        try:
            value = json.loads(target.read_text(encoding="utf-8"))
            break
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            # Measured on the demo machine: 581 transient read failures in a
            # million reads while another process was replacing this file.  Rare
            # -- but activate_pending treats a read failure as "no valid pending
            # slot" and discards the update, so one unlucky launch would throw
            # away a staged update the user had asked to install.
            if attempt == STATE_REPLACE_ATTEMPTS - 1:
                raise UpdateError(f"invalid update state: {exc}") from exc
            time.sleep(STATE_REPLACE_BACKOFF_SECONDS * (attempt + 1))
    if not isinstance(value, dict):
        raise UpdateError("invalid update state: expected an object")
    return value


def write_state(state: dict[str, Any], path: str | Path | None = None) -> Path:
    """Atomically replace shared update state."""
    return _write_json_atomic(state, Path(path) if path is not None else state_path())


def _write_json_atomic(state: dict[str, Any], target: Path) -> Path:
    """Replace a JSON file, including Windows reader/share-violation retries."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(state, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        # Windows refuses MoveFileEx onto a target another process has open --
        # Python's open() does not pass FILE_SHARE_DELETE -- so any concurrent
        # *reader* makes this fail with `[WinError 5] Access is denied`.  The
        # browser polls GET /api/update every 2s while staging, and that route
        # reads this file, so a staging job that writes it repeatedly loses the
        # race often.  Block 58e's second demo gate died exactly there: the
        # whole update failed on its first state write.  Retry briefly rather
        # than surface a transient share violation as a failed update.
        for attempt in range(STATE_REPLACE_ATTEMPTS):
            try:
                os.replace(temporary, target)
                break
            except OSError:
                if attempt == STATE_REPLACE_ATTEMPTS - 1:
                    raise
                time.sleep(STATE_REPLACE_BACKOFF_SECONDS * (attempt + 1))
    finally:
        temporary.unlink(missing_ok=True)
    return target


def locate_uv(*, path: str | None = None, user_profile: str | Path | None = None,
              platform: str | None = None) -> str:
    """Use PATH, then the Windows installer's bootstrap location."""
    found = shutil.which("uv", path=path)
    if found:
        return found
    if (platform or sys.platform) == "win32":
        profile = user_profile if user_profile is not None else os.environ.get("USERPROFILE")
        if profile:
            candidate = Path(profile) / ".local" / "bin" / "uv.exe"
            if candidate.is_file():
                return str(candidate)
    raise UpdateError("uv was not found on PATH or in the Windows bootstrap location.")


def locate_git(*, path: str | None = None, local_app_data: str | Path | None = None) -> str:
    """Find and validate Git on PATH, then in installed GitHub Desktop apps."""
    candidates: list[Path] = []
    found = shutil.which("git", path=path)
    if found:
        candidates.append(Path(found))
    base = Path(local_app_data) if local_app_data else Path(
        os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")
    )
    desktop = base / "GitHubDesktop"
    if desktop.is_dir():
        candidates.extend(sorted(
            desktop.glob("app-*/resources/app/git/cmd/git.exe"), reverse=True
        ))
    for candidate in candidates:
        try:
            result = subprocess.run(
                [str(candidate), "--version"], capture_output=True, text=True,
                timeout=GIT_TIMEOUT_SECONDS, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0 and result.stdout.startswith("git version "):
            return str(candidate.resolve())
    raise UpdateError("Git was not found on PATH or in GitHub Desktop.")


def clone_provenance(
    clone: str | Path, installed_commit: str, *, git_executable: str | None = None,
) -> dict[str, Any]:
    """Build the installer record without using checkout state as update policy."""
    git = git_executable or locate_git()
    root = Path(clone).resolve()

    def query(*args: str, allowed=(0,)) -> str:
        result = subprocess.run(
            [git, "-C", str(root), *args], capture_output=True, text=True,
            timeout=GIT_TIMEOUT_SECONDS, check=False,
        )
        if result.returncode not in allowed:
            raise UpdateError((result.stderr or result.stdout).strip() or "git failed")
        return result.stdout.strip()

    branch = query("symbolic-ref", "--quiet", "--short", "HEAD", allowed=(0, 1)) or None
    upstream = None
    if branch:
        upstream = query(
            "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}",
            allowed=(0, 128),
        ) or None
    remote = query("config", "--get", f"branch.{BRANCH}.remote", allowed=(0, 1)) or "origin"
    remote_url = query("remote", "get-url", remote)
    remote_identity = _normalize_remote_url(remote_url, root)
    clone_repository_note = None
    if remote_identity.startswith("github:") and remote_identity != f"github:{REPO.casefold()}":
        recorded_name = remote_identity.removeprefix("github:")
        clone_repository_note = (
            f"This clone tracks {recorded_name}, which GitHub may redirect to {REPO}. "
            "Private Git transport does not expose the immutable repository id, so "
            "this name redirect is recorded but not numerically verified."
        )
    return {
        "provenance": "clone",
        "clone_path": str(root),
        "repo_id": REPO_ID,
        "repo": REPO,
        "canonical_repo": REPO,
        "branch": branch,
        "upstream": upstream,
        "remote": remote,
        "remote_url": remote_url,
        "remote_identity": remote_identity,
        "clone_repository_note": clone_repository_note,
        "tracked_branch": BRANCH,
        "git_executable": git,
        "installed_commit": installed_commit,
    }


def public_provenance(installed_commit: str = "unknown") -> dict[str, Any]:
    return {
        "provenance": "public-head", "repo_id": REPO_ID, "repo": REPO,
        "canonical_repo": REPO, "branch": BRANCH,
        "installed_commit": installed_commit,
    }


def installer_provenance(
    source: str | Path, installed_commit: str,
) -> tuple[dict[str, Any], str | None]:
    """Prefer clone provenance, but never fail installation for its absence."""
    root = Path(source).resolve()
    if not (root / ".git").exists():
        return public_provenance(installed_commit), None
    try:
        return clone_provenance(root, installed_commit), None
    except Exception as exc:
        return public_provenance(installed_commit), (
            f"Git provenance was unavailable ({exc}); updates will follow public head."
        )


def _git(state: dict[str, Any], *args: str, timeout: float = GIT_TIMEOUT_SECONDS):
    git_executable = state.get("git_executable")
    clone_path = state.get("clone_path")
    if not isinstance(git_executable, str) or not git_executable:
        raise UpdateError("clone provenance is missing git_executable")
    if not isinstance(clone_path, str) or not clone_path:
        raise UpdateError("clone provenance is missing clone_path")
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        return subprocess.run(
            [git_executable, "-C", clone_path, *args],
            capture_output=True, text=True, timeout=timeout, env=env, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise UpdateError(
            "Git timed out. Open GitHub Desktop, Fetch origin, then Check again."
        ) from exc


def _normalize_remote_url(url: str, clone: str | Path | None = None) -> str:
    """Canonicalize GitHub SSH/HTTPS names and exact filesystem remotes."""
    value = url.strip().rstrip("/")
    scp = re.fullmatch(r"git@github\.com:(.+)", value, re.IGNORECASE)
    parsed = urllib.parse.urlparse(value)
    if scp:
        name = scp.group(1)
    elif parsed.hostname and parsed.hostname.casefold() == "github.com":
        name = parsed.path.lstrip("/")
    else:
        path = Path(urllib.request.url2pathname(parsed.path)) if parsed.scheme == "file" else Path(value)
        if not path.is_absolute() and clone is not None:
            path = Path(clone) / path
        return f"file:{path.resolve()}"
    if name.casefold().endswith(".git"):
        name = name[:-4]
    return f"github:{name.casefold()}"


def _verify_clone_remote(state: dict[str, Any], remote: str, timeout: float) -> None:
    result = _git(state, "remote", "get-url", remote, timeout=timeout)
    if result.returncode != 0:
        raise UpdateError(f"recorded remote {remote!r} no longer exists")
    current = _normalize_remote_url(result.stdout, state.get("clone_path"))
    recorded = state.get("remote_identity")
    if isinstance(recorded, str):
        if current != recorded:
            raise UpdateError("recorded clone remote no longer matches repository identity")
        return
    expected = f"github:{str(state.get('repo') or REPO).casefold()}"
    if current != expected:
        raise UpdateError("recorded clone remote does not match repository identity")


def _discovery_status(state: dict[str, Any], status: str, message: str) -> None:
    state["discovery"] = {"status": status, "message": message}


def discover_clone(state: dict[str, Any], *, timeout: float = GIT_TIMEOUT_SECONDS) -> Candidate | None:
    """Fetch and compare the recorded remote's ``main`` without touching HEAD."""
    remote = state.get("remote") or "origin"
    _verify_clone_remote(state, remote, timeout)
    remote_ref = f"refs/remotes/{remote}/{BRANCH}"
    fetched = _git(state, "fetch", remote, BRANCH, timeout=timeout)
    warning = None
    if fetched.returncode != 0:
        warning = "Open GitHub Desktop, Fetch origin, then Check again."
    resolved = _git(state, "rev-parse", "--verify", remote_ref, timeout=timeout)
    if resolved.returncode != 0:
        if warning:
            raise UpdateError(warning)
        raise UpdateError((resolved.stderr or resolved.stdout).strip() or f"missing {remote_ref}")
    sha = resolved.stdout.strip().lower()
    if not _SHA.fullmatch(sha):
        raise UpdateError("clone returned a malformed commit SHA")
    installed = str(state.get("installed_commit", "unknown")).lower()
    if installed != "unknown":
        present = _git(state, "cat-file", "-e", f"{installed}^{{commit}}", timeout=timeout)
        if present.returncode != 0:
            _discovery_status(
                state, "installed-commit-missing",
                "The installed commit is missing from the recorded clone. "
                "Open GitHub Desktop, Fetch origin, then Check again.",
            )
            return None
        ancestor = _git(state, "merge-base", "--is-ancestor", installed, sha, timeout=timeout)
        if ancestor.returncode == 1:
            _discovery_status(
                state, "diverged",
                "The installed commit and the recorded clone's main history have diverged. "
                "Resolve the clone in GitHub Desktop, then Check again.",
            )
            return None
        if ancestor.returncode != 0:
            raise UpdateError("could not compare installed and fetched clone history")
        if installed == sha:
            _discovery_status(state, "current", "The installed commit is current.")
            return None
    subject_result = _git(state, "show", "-s", "--format=%s", sha, timeout=timeout)
    if subject_result.returncode != 0:
        raise UpdateError("could not read fetched commit subject")
    _discovery_status(state, "candidate", f"A newer main commit is available: {sha}.")
    return Candidate(sha, subject_result.stdout.strip(), "clone", state.get("canonical_repo", REPO), warning)


def _allowed_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in GITHUB_HOSTS:
        raise UpdateError(f"redirect host is not allowlisted: {parsed.hostname or url}")


def _open_manual(url: str, opener: URLopener, *, max_bytes: int) -> tuple[bytes, str]:
    current = url
    for redirects in range(MAX_REDIRECTS + 1):
        _allowed_url(current)
        request = urllib.request.Request(
            current, headers={"Accept": "application/vnd.github+json", "User-Agent": "microclaw-updater"}
        )
        try:
            response = opener(request, HTTP_TIMEOUT_SECONDS)
        except urllib.error.HTTPError as exc:
            if exc.code in {301, 302, 303, 307, 308}:
                location = exc.headers.get("Location")
                if not location:
                    raise UpdateError("redirect had no Location") from exc
                if redirects == MAX_REDIRECTS:
                    raise UpdateError("too many redirects") from exc
                current = urllib.parse.urljoin(current, location)
                continue
            if exc.code == 404:
                raise UpdateError("repository is not public (404)") from exc
            raise UpdateError(f"GitHub request failed: HTTP {exc.code}") from exc
        except (OSError, TimeoutError) as exc:
            raise UpdateError(f"GitHub request failed: {exc}") from exc
        status = getattr(response, "status", response.getcode())
        if status in {301, 302, 303, 307, 308}:
            location = response.headers.get("Location")
            response.close()
            if not location:
                raise UpdateError("redirect had no Location")
            if redirects == MAX_REDIRECTS:
                raise UpdateError("too many redirects")
            current = urllib.parse.urljoin(current, location)
            continue
        if status != 200:
            response.close()
            if status == 404:
                raise UpdateError("repository is not public (404)")
            raise UpdateError(f"GitHub request failed: HTTP {status}")
        chunks, total = [], 0
        try:
            while True:
                chunk = response.read(min(64 * 1024, max_bytes + 1 - total))
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise UpdateError("download exceeds size limit")
                chunks.append(chunk)
        finally:
            response.close()
        return b"".join(chunks), current
    raise UpdateError("too many redirects")


def _json_object(data: bytes, reason: str) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise UpdateError(reason) from exc
    if not isinstance(value, dict):
        raise UpdateError(reason)
    return value


def discover_public(
    state: dict[str, Any], *, opener: URLopener = _default_opener,
) -> Candidate | None:
    """Discover public ``main`` and verify redirects by immutable repository id."""
    repo = str(state.get("canonical_repo") or REPO)
    api = f"https://api.github.com/repos/{repo}/commits/{BRANCH}"
    data, final_url = _open_manual(api, opener, max_bytes=1024 * 1024)
    final_parts = urllib.parse.urlparse(final_url).path.strip("/").split("/")
    canonical = repo
    if len(final_parts) >= 3 and final_parts[0] == "repos":
        canonical = "/".join(final_parts[1:3])
    if canonical != repo:
        metadata, _ = _open_manual(
            f"https://api.github.com/repos/{canonical}", opener, max_bytes=1024 * 1024
        )
        info = _json_object(metadata, "malformed repository metadata")
        if info.get("id") != REPO_ID:
            raise UpdateError("redirected repository id does not match")
        full_name = info.get("full_name")
        if not isinstance(full_name, str):
            raise UpdateError("malformed repository metadata")
        canonical = full_name
        state["canonical_repo"] = canonical
    body = _json_object(data, "malformed API body")
    sha = body.get("sha")
    if not isinstance(sha, str) or not _SHA.fullmatch(sha):
        raise UpdateError("API returned a non-40-hex sha")
    installed = str(state.get("installed_commit", "unknown")).lower()
    if installed == sha.lower():
        return None
    commit = body.get("commit")
    message = commit.get("message") if isinstance(commit, dict) else None
    if not isinstance(message, str):
        raise UpdateError("malformed API body")
    return Candidate(sha.lower(), message.splitlines()[0], "public-head", canonical)


def _safe_extract_zip(data: bytes, destination: Path) -> None:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, EOFError) as exc:
        raise UpdateError("truncated or invalid archive") from exc
    total = 0
    with archive:
        for member in archive.infolist():
            path = PurePosixPath(member.filename)
            mode = member.external_attr >> 16
            if path.is_absolute() or ".." in path.parts:
                raise UpdateError("archive contains an unsafe path")
            kind = stat.S_IFMT(mode)
            if stat.S_ISLNK(mode) or (kind not in {0, stat.S_IFREG, stat.S_IFDIR}):
                raise UpdateError("archive contains a link or device entry")
            target = destination.joinpath(*path.parts)
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with archive.open(member) as source, target.open("xb") as output:
                    while True:
                        chunk = source.read(64 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > MAX_EXTRACTED_BYTES:
                            raise UpdateError("archive exceeds extracted size limit")
                        output.write(chunk)
            except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                raise UpdateError(f"archive extraction failed: {exc}") from exc


def _flatten_archive_root(destination: Path) -> None:
    children = list(destination.iterdir())
    if len(children) == 1 and children[0].is_dir():
        root = children[0]
        for child in list(root.iterdir()):
            child.replace(destination / child.name)
        root.rmdir()


def write_staged_source(destination: Path, sha: str) -> Path:
    if not _SHA.fullmatch(sha):
        raise UpdateError("staged source commit is not a full SHA")
    marker = destination / STAGED_SOURCE_NAME
    marker.write_text(json.dumps({"commit": sha.lower()}, sort_keys=True) + "\n", encoding="utf-8")
    return marker


def verify_staged_source(destination: str | Path, requested_sha: str) -> None:
    try:
        record = json.loads((Path(destination) / STAGED_SOURCE_NAME).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise UpdateError("staged source has no valid commit record") from exc
    if record.get("commit") != requested_sha.lower():
        raise UpdateError("staged source commit does not match requested SHA")


def materialize_clone(
    state: dict[str, Any], candidate: Candidate, destination: str | Path,
) -> Path:
    """Archive the fetched object itself; never use files from the worktree."""
    dest = Path(destination)
    dest.mkdir(parents=True, exist_ok=False)
    archive_path = dest.parent / f".{dest.name}-{candidate.sha}.zip"
    try:
        result = _git(
            state, "archive", "--format=zip", f"--output={archive_path}", candidate.sha
        )
        if result.returncode != 0:
            raise UpdateError((result.stderr or result.stdout).strip() or "git archive failed")
        data = archive_path.read_bytes()
        if len(data) > MAX_DOWNLOAD_BYTES:
            raise UpdateError("archive exceeds download size limit")
        _safe_extract_zip(data, dest)
        write_staged_source(dest, candidate.sha)
        verify_staged_source(dest, candidate.sha)
        return dest
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    finally:
        archive_path.unlink(missing_ok=True)


def materialize_public(
    state: dict[str, Any], candidate: Candidate, destination: str | Path,
    *, opener: URLopener = _default_opener,
) -> Path:
    repo = candidate.canonical_repo
    url = f"https://codeload.github.com/{repo}/zip/{candidate.sha}"
    data, _ = _open_manual(url, opener, max_bytes=MAX_DOWNLOAD_BYTES)
    dest = Path(destination)
    dest.mkdir(parents=True, exist_ok=False)
    try:
        _safe_extract_zip(data, dest)
        _flatten_archive_root(dest)
        write_staged_source(dest, candidate.sha)
        verify_staged_source(dest, candidate.sha)
        return dest
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        raise


def checks_enabled(no_update_check: bool = False) -> bool:
    return not no_update_check and os.environ.get("MICROCLAW_UPDATE_CHECK") != "0"


def start_due_check(no_update_check: bool = False):
    """Start the shared non-blocking due check used by serve and the REPL."""
    if not checks_enabled(no_update_check):
        return None
    import threading
    thread = threading.Thread(
        target=check_for_update, kwargs={"no_update_check": no_update_check},
        name="microclaw-update-check", daemon=True,
    )
    thread.start()
    return thread


def candidate_is_installed(
    candidate: Candidate | dict[str, Any] | None, state: dict[str, Any],
) -> bool:
    """Is this cached candidate the commit that is already running?

    Read-side companion to the invalidation in `_reconcile_installed_commit`:
    that is best-effort by design, and a state file written by an older build
    carries the stale candidate anyway.  Neither the banner nor the REPL notice
    may offer an update the user has already installed.
    """
    if candidate is None:
        return False
    sha = candidate.sha if isinstance(candidate, Candidate) else candidate.get("sha")
    installed = str(state.get("installed_commit", "unknown")).lower()
    return (
        isinstance(sha, str) and installed != "unknown" and sha.lower() == installed
    )


def candidate_is_suppressed(
    candidate: Candidate | dict[str, Any] | None, dismissal: Any, *, now: float | None = None,
) -> bool:
    if candidate is None or not isinstance(dismissal, dict):
        return False
    sha = candidate.sha if isinstance(candidate, Candidate) else candidate.get("sha")
    if dismissal.get("commit") != sha:
        return False
    return dismissal.get("action") == "skip" or (
        dismissal.get("action") == "later"
        and isinstance(dismissal.get("until"), (int, float))
        and (time.time() if now is None else now) < dismissal["until"]
    )


def terminal_update_notice(*, state_file: str | Path | None = None) -> tuple[str | None, Candidate | None]:
    """Read cached state only and return the REPL's single optional notice."""
    path = Path(state_file) if state_file is not None else state_path()
    try:
        state = load_state(path)
    except UpdateError:
        return None, None
    if state is None:
        return None, None
    success = state.get("last_success")
    raw = success.get("candidate") if isinstance(success, dict) else None
    candidate = None
    if isinstance(raw, dict):
        try:
            candidate = Candidate(**raw)
        except (TypeError, ValueError):
            pass
    if candidate_is_installed(candidate, state) or candidate_is_suppressed(
        candidate, state.get("dismissal")
    ):
        candidate = None
    if candidate:
        warning = f" {candidate.warning}" if candidate.warning else ""
        return (
            f"A newer Microclaw commit is available: {candidate.sha[:7]} — "
            f"{candidate.subject}.{warning}", candidate,
        )
    if (state.get("provenance") == "public-head"
            and state.get("last_error") == "repository is not public (404)"):
        return "Automatic updates become available when the repository is public.", None
    return None, None


def stage_cached_candidate(candidate: Candidate, *, config_path: str | Path | None = None) -> Path:
    """Materialize and stage the cached candidate synchronously for the REPL."""
    path = state_path()
    state = load_state(path)
    if state is None:
        raise UpdateError("updates are unavailable in this installation")
    root = path.parent
    downloads = root / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="microclaw-stage-", dir=downloads))
    try:
        source = work / "source"
        materialize = materialize_clone if candidate.source == "clone" else materialize_public
        materialize(state, candidate, source)
        uv = locate_uv()
        return stage_inactive_slot(
            root, source, candidate, uv_executable=uv, config_path=config_path,
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)


def check_for_update(
    *, state_file: str | Path | None = None, no_update_check: bool = False,
    opener: URLopener = _default_opener, now: float | None = None,
    jitter: Callable[[float, float], float] = random.uniform, force: bool = False,
) -> Candidate | None:
    """Run one due managed check, caching success or failure atomically."""
    if sys.platform != "win32" or not checks_enabled(no_update_check):
        return None
    current = time.time() if now is None else now
    path = Path(state_file) if state_file is not None else state_path()
    try:
        state = load_state(path)
    except Exception:
        # Provenance exists only in this file. If it cannot be read, preserving
        # its bytes is the only chance of manual recovery; never replace it with
        # a provenance-free error cache.
        return None
    if state is None:
        return None
    try:
        last_attempt = state.get("last_attempt")
        next_check = state.get("next_check")
        if (not force and isinstance(last_attempt, (int, float))
                and isinstance(next_check, (int, float))):
            if current < next_check:
                return None
        state["last_attempt"] = current
        state["next_check"] = current + CHECK_INTERVAL_SECONDS + jitter(0, CHECK_JITTER_SECONDS)
        provenance = state.get("provenance")
        if provenance == "clone":
            candidate = discover_clone(state)
        elif provenance == "public-head":
            candidate = discover_public(state, opener=opener)
        else:
            raise UpdateError("unknown update provenance")
        state["last_error"] = None
        state["last_success"] = {
            "checked_at": current,
            "candidate": candidate.__dict__ if candidate else None,
        }
    except Exception as exc:  # startup must survive truncated operational state
        state["last_attempt"] = current
        state["last_error"] = str(exc) or type(exc).__name__
        try:
            state["next_check"] = current + CHECK_INTERVAL_SECONDS + jitter(0, CHECK_JITTER_SECONDS)
        except Exception:
            state["next_check"] = current + CHECK_INTERVAL_SECONDS
        candidate = None
    try:
        write_state(state, path)
    except Exception:
        pass
    return candidate


def slot_marker_path(executable: str | Path | None = None) -> Path:
    """Resolve the marker in this interpreter's environment, never shared state."""
    exe = Path(executable or sys.executable).resolve()
    env_root = exe.parent.parent if exe.parent.name.lower() == "scripts" else exe.parent
    return env_root / SLOT_NAME


def write_slot_marker(
    commit: str, required_launcher_protocol: int, *, executable: str | Path | None = None,
) -> Path:
    if commit != "unknown" and not _SHA.fullmatch(commit):
        raise UpdateError("slot commit is neither a full SHA nor 'unknown'")
    if type(required_launcher_protocol) is not int or required_launcher_protocol < 1:
        raise UpdateError("required launcher protocol must be a positive integer")
    marker = slot_marker_path(executable)
    marker.parent.mkdir(parents=True, exist_ok=True)
    write_state(
        {"commit": commit.lower(), "required_launcher_protocol": required_launcher_protocol},
        marker,
    )
    return marker


def read_slot_marker(*, executable: str | Path | None = None) -> dict[str, Any] | None:
    return load_state(slot_marker_path(executable))
