"""Supervise trusted publisher workers over bounded NDJSON pipes.

Timing contract: submit performs only bounded data validation, one release
signature verification and put_nowait. Notifications validate small messages and
put_nowait; neither path launches, hashes assets, accesses the filesystem, writes
pipes, starts threads, or waits for a worker. Short locks protect in-memory
transitions only, never I/O. Fixed dispatchers own launch, deadlines, verification
and cleanup. Priority launch policy and at most two worker priority samples run only
in the dispatcher monitor, never on submit/notify or in pipe callbacks. Cleanup
labels unsampled phases without further OS reads or waits. Message bytes, pending
stdin, retained stdout and stderr, queued
jobs, live workers and reserved window slots have explicit bounds. Retained
windows release their worker slot at result; post-result pipes only drain bounded
chunks into separate in-memory diagnostics. Notification history retains the first
32 attempts and counts subsequent attempts without retaining them. There is no
total analysis deadline unless supplied. All timing uses perf_counter, including
deadlines. Measurements live in design/83-block83c-dispatch-timing.py.

Workers have the user's permissions, not an OS sandbox. Windows uses a Job
Object assigned before resume. Each Windows priority sample reads up to 64 job
members and records the highest scheduling class and number read; the launched
process can be a venv launcher rather than the worker. Query handles stay open
through the terminal sample so exited members remain readable. POSIX uses a new
session/process group; a POSIX descendant which calls setsid escapes (accepted off the shipping platform).
"""
from __future__ import annotations

from collections import deque
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import signal
import stat
import sys
from datetime import datetime, timezone
import subprocess
import tempfile
import threading
import time
from uuid import uuid4

from . import skill_packages as packages

MAX_STDERR_BYTES = 65536
MAX_RETAINED_STATUS = 256
MAX_PENDING_NOTIFICATIONS = 8
MAX_RECORDED_NOTIFICATIONS = 32
MAX_QUEUED_JOBS = 4
MAX_CONCURRENT_WORKERS = 2
MAX_OPEN_WINDOWS = 4
DESKTOP_REFUSAL = ("This operation opens a window and this MicroClaw cannot show one; "
                   "use an operation of the same package that does not open a window.")
STARTUP_DEADLINE_S = 60
SELF_CHECK_DEADLINE_S = 120
SHUTDOWN_GRACE_S = 10
MAX_JOB_PRIORITY_PROCESSES = 64
# Priority-class numbers are flags, not scheduling order.
WINDOWS_PRIORITY_RANK = {value: rank for rank, value in enumerate(
    (0x40, 0x4000, 0x20, 0x8000, 0x80, 0x100))}


class _WindowsJob:
    """Assign a suspended Popen child before any publisher code can execute."""

    def __init__(self, process):
        import ctypes as c
        from ctypes import wintypes as w

        class Basic(c.Structure):
            _fields_ = [("PerProcessUserTimeLimit", c.c_int64),
                        ("PerJobUserTimeLimit", c.c_int64), ("LimitFlags", w.DWORD),
                        ("MinimumWorkingSetSize", c.c_size_t), ("MaximumWorkingSetSize", c.c_size_t),
                        ("ActiveProcessLimit", w.DWORD), ("Affinity", c.c_size_t),
                        ("PriorityClass", w.DWORD), ("SchedulingClass", w.DWORD)]

        class IO(c.Structure):
            _fields_ = [(name, c.c_uint64) for name in
                        ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                         "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class Extended(c.Structure):
            _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", IO),
                        ("ProcessMemoryLimit", c.c_size_t), ("JobMemoryLimit", c.c_size_t),
                        ("PeakProcessMemoryUsed", c.c_size_t), ("PeakJobMemoryUsed", c.c_size_t)]

        self.c = c
        self.kernel = c.WinDLL("kernel32", use_last_error=True)
        ntdll = c.WinDLL("ntdll", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([c.c_void_p, w.LPCWSTR], w.HANDLE),
            "SetInformationJobObject": ([w.HANDLE, c.c_int, c.c_void_p, w.DWORD], w.BOOL),
            "QueryInformationJobObject": ([w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.c_void_p], w.BOOL),
            "OpenProcess": ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            "GetPriorityClass": ([w.HANDLE], w.DWORD),
            "AssignProcessToJobObject": ([w.HANDLE, w.HANDLE], w.BOOL),
            "TerminateJobObject": ([w.HANDLE, w.UINT], w.BOOL),
            "TerminateProcess": ([w.HANDLE, w.UINT], w.BOOL),
            "CloseHandle": ([w.HANDLE], w.BOOL),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(self.kernel, name)
            fn.argtypes, fn.restype = args, result
        ntdll.NtResumeProcess.argtypes = [w.HANDLE]
        ntdll.NtResumeProcess.restype = w.LONG
        self.handle = None
        self._priority_handles = {}
        child = None
        try:
            self.handle = self.check(self.kernel.CreateJobObjectW(None, None))
            info = Extended()
            info.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
            self.check(self.kernel.SetInformationJobObject(self.handle, 9, c.byref(info), c.sizeof(info)))
            child = self.check(self.kernel.OpenProcess(0x0100 | 0x0001 | 0x0800, False, process.pid))
            self.check(self.kernel.AssignProcessToJobObject(self.handle, child))
            status = ntdll.NtResumeProcess(child)
            if status < 0:
                raise OSError(f"NtResumeProcess failed: {status}")
            self.check(self.kernel.CloseHandle(child))
            child = None
        except BaseException:
            # Popen retains a PROCESS_ALL_ACCESS handle even if OpenProcess failed.
            # Closing our job in all failure paths also kills any descendants if
            # a handle-close failure occurred after a successful resume.
            try:
                self.check(self.kernel.TerminateProcess(process._handle, 1))
            finally:
                try:
                    if child:
                        self.check(self.kernel.CloseHandle(child))
                finally:
                    self.close()
            raise

    def check(self, result):
        if not result:
            raise self.c.WinError(self.c.get_last_error())
        return result

    def kill(self):
        self.check(self.kernel.TerminateJobObject(self.handle, 1))

    def _process_ids(self):
        c = self.c

        class ProcessIds(c.Structure):
            _fields_ = [("NumberOfAssignedProcesses", c.c_uint32),
                        ("NumberOfProcessIdsInList", c.c_uint32),
                        ("ProcessIdList", c.c_size_t * MAX_JOB_PRIORITY_PROCESSES)]

        info = ProcessIds()
        result = self.kernel.QueryInformationJobObject(self.handle, 3, c.byref(info), c.sizeof(info), None)
        more_data = not result and c.get_last_error() == 234  # ERROR_MORE_DATA
        if not result and not more_data:
            self.check(result)
        count = min(info.NumberOfProcessIdsInList, MAX_JOB_PRIORITY_PROCESSES)
        truncated = more_data or info.NumberOfAssignedProcesses > count
        reason = (f"job pid list truncated: {info.NumberOfAssignedProcesses} assigned, {count} listed "
                  f"(limit {MAX_JOB_PRIORITY_PROCESSES})") if truncated else None
        return list(info.ProcessIdList[:count]), reason

    def _pid_priority(self, pid):
        if pid not in self._priority_handles:
            self._priority_handles[pid] = self.check(
                self.kernel.OpenProcess(0x1000, False, pid))  # QUERY_LIMITED_INFORMATION
        return self.check(self.kernel.GetPriorityClass(self._priority_handles[pid]))

    def read_priority(self):
        """Read held members plus newly listed members, retaining at most 64 handles."""
        pids, reason = self._process_ids()
        reasons = [reason] if reason else []
        new = [pid for pid in pids if pid not in self._priority_handles]
        remaining = MAX_JOB_PRIORITY_PROCESSES - len(self._priority_handles)
        if len(new) > remaining:
            reasons.append(f"job priority handles truncated at {MAX_JOB_PRIORITY_PROCESSES}; "
                           f"{len(new) - remaining} new members omitted")
        reads = list(self._priority_handles) + new[:remaining]
        classes = []
        for pid in reads:
            try:
                value = self._pid_priority(pid)
                if value not in WINDOWS_PRIORITY_RANK:
                    reasons.append(f"pid {pid}: unknown priority class {value}")
                    continue
                classes.append(value)
            except OSError as exc:
                if getattr(exc, "winerror", None) in (6, 87):  # invalid handle/pid after exit
                    reasons.append(f"pid {pid} exited before priority read")
                else:
                    reasons.append(f"pid {pid}: {exc}")
        if not classes:
            reasons.append("job has no readable processes" if reads else "job has no processes (processes exited)")
        value = max(classes, key=WINDOWS_PRIORITY_RANK.__getitem__) if classes else None
        return value, "; ".join(reasons) or None, len(classes)

    def close(self):
        failure = None
        for pid, child in list(self._priority_handles.items()):
            del self._priority_handles[pid]
            try:
                self.check(self.kernel.CloseHandle(child))
            except OSError as exc:
                failure = failure or exc
        if self.handle:
            try:
                self.check(self.kernel.CloseHandle(self.handle))
            except OSError as exc:
                failure = failure or exc
            finally:
                self.handle = None
        if failure:
            raise failure


def _windows_priority(handle=None):
    import ctypes as c
    from ctypes import wintypes as w
    kernel = c.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.GetPriorityClass.argtypes = [w.HANDLE]
    kernel.GetPriorityClass.restype = w.DWORD
    value = kernel.GetPriorityClass(kernel.GetCurrentProcess() if handle is None else handle)
    if not value:
        raise c.WinError(c.get_last_error())
    return value


def _read_priority(process, job=None):
    """Best-effort evidence, including an explicit exited-before-read outcome."""
    try:
        if os.name == "nt":
            # The launched handle may belong to a venv launcher, not the worker.
            # The job remains queryable after launcher's exit until cleanup closes it.
            return job.read_priority() if job is not None else (None, "worker job unavailable", 0)
        if process.poll() is not None:
            return None, "process exited before priority read", 0
        return os.getpriority(os.PRIO_PROCESS, process.pid), None, 1
    except ProcessLookupError:
        return None, "process exited before priority read", 0
    except (OSError, AttributeError) as exc:
        return None, str(exc), 0


def _kill_tree(process, job):
    if os.name == "nt":
        if job is not None:
            job.kill()
        elif process.poll() is None:
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _write_pipe(pipe, line):
    # Unbuffered pipes can return a short write, especially for the job line.
    view = memoryview(line)
    while view:
        count = pipe.write(view)
        if not count:
            raise BrokenPipeError("stdin closed")
        view = view[count:]


def interactive_desktop(*, platform=None, environ=None):
    """Bounded preflight, not proof GUI initialization will succeed.

    Darwin is deliberately allowed: no portable macOS desktop policy is claimed.
    """
    platform = sys.platform if platform is None else platform
    environ = os.environ if environ is None else environ
    if platform.startswith("linux"):
        return bool(environ.get("DISPLAY") or environ.get("WAYLAND_DISPLAY"))
    if platform == "win32":
        import ctypes as c
        from ctypes import wintypes as w
        kernel = c.WinDLL("kernel32", use_last_error=True)
        kernel.ProcessIdToSessionId.argtypes = [w.DWORD, c.POINTER(w.DWORD)]
        kernel.ProcessIdToSessionId.restype = w.BOOL
        session = w.DWORD()
        if not kernel.ProcessIdToSessionId(os.getpid(), c.byref(session)):
            raise c.WinError(c.get_last_error())
        return session.value != 0
    return True


class JobHandle:
    def __init__(self, supervisor, job_id=None):
        self.job_id = job_id or uuid4().hex
        self._supervisor = supervisor
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._window_done = threading.Event()
        # Protected by the supervisor lifecycle lock. A reaper revokes new
        # kills and waits outside the lock for already-claimed kills to finish.
        self._window_reaping = False
        self._window_kills = 0
        self._window_kills_done = threading.Event()
        self._window_kills_done.set()
        self._stop = threading.Event()
        self._pending = queue.Queue(maxsize=supervisor.max_pending_notifications)
        self._status = deque(maxlen=supervisor.max_retained_status)
        self._stderr = bytearray()
        self._opens_window = False
        self._discard_stdout = False
        self._frozen = False
        self._diagnostics = dict(stderr_tail="", discarded_stdout_bytes=0, exit_code=None, cleanup_failures=[])
        self._window_stderr = bytearray()
        self._declared = {}
        self._accepted = {}
        self._created = time.perf_counter()
        self._spawned = self._first = self._shutdown = self._cancel_sent = None
        self._stdin_unavailable_reason = None
        self._violation = None
        self._record = dict(job_id=self.job_id, release={}, operation=None, state="queued",
                            priority=dict(requested=None, inherited=False, at_start=None, at_end=None,
                                          processes_read=dict(at_start=0, at_end=0), reason={}),
                            result=None, failure=None, artifacts=[], rejected_artifacts=[],
                            lifecycle=dict(acquisition=None, writer=None), notifications=[], notifications_dropped=0,
                            status=[], status_dropped=0, stderr_tail="", exit_code=None,
                            duration_breakdown=dict(queued_s=0.0, startup_s=0.0, running_s=0.0,
                                                    shutdown_s=0.0, accounted_s=0.0))

    def record(self):
        with self._lock:
            value = deepcopy(self._record)
            if not self._frozen:
                value["status"] = list(self._status)
                value["stderr_tail"] = bytes(self._stderr).decode("utf-8", errors="replace")
            return value

    def wait(self, timeout=None):
        return self._done.wait(timeout)

    def _finish(self, state, failure=None):
        # Caller holds the lock. This is CPU-only, including queued cancellation.
        end = time.perf_counter()
        spawn = self._spawned or end
        shutdown = self._shutdown or end
        first = min(self._first or shutdown, shutdown)
        self._record["state"] = state
        self._record["failure"] = failure
        self._record["duration_breakdown"] = dict(
            queued_s=spawn - self._created, startup_s=first - spawn,
            running_s=max(0.0, shutdown - first), shutdown_s=end - shutdown,
            accounted_s=end - self._created)
        self._stop.set()
        self._undeliver_pending(self._stdin_unavailable_reason or "worker_exited")
        self._record["status"] = list(self._status)
        self._record["stderr_tail"] = bytes(self._stderr).decode("utf-8", errors="replace")
        self._frozen = self._record.get("window_retained", False)
        self._done.set()

    def _undeliver_pending(self, reason):
        # Caller holds the lock; only the bounded stdin queue is traversed.
        while True:
            try:
                _, entry = self._pending.get_nowait()
            except queue.Empty:
                break
            entry.update(state="undelivered", reason=reason)

    def _notify(self, kind, **fields):
        message = dict(protocol=packages.ANALYSIS_PROTOCOL, type=kind, job_id=self.job_id, **fields)
        with self._lock:
            if self._frozen:
                return False
            entry = dict(type=kind, state="refused")
            if len(self._record["notifications"]) < MAX_RECORDED_NOTIFICATIONS:
                self._record["notifications"].append(entry)
            else:
                self._record["notifications_dropped"] += 1
            if self._stdin_unavailable_reason in ("stdin_closed", "stdin_failed"):
                entry.update(state="undelivered", reason=self._stdin_unavailable_reason)
                return False
            try:
                value = packages.validate_notification(message, job_id=self.job_id,
                                                       operation=self._record["operation"])
                if kind in self._accepted:
                    raise packages.PackageRefusal("type", "duplicate notification")
                if kind == "writer" and self._accepted.get("acquisition", {}).get("writer") != "unknown":
                    raise packages.PackageRefusal("type", "writer requires acquisition with unknown writer")
            except Exception as exc:
                entry.update(reason="refused", field=getattr(exc, "field", "notification"), detail=str(exc))
                return False
            if self._done.is_set() or self._stop.is_set():
                entry.update(state="undelivered", reason="worker_exited")
                return False
            if kind == "cancel" and self._record["state"] == "queued":
                self._accepted[kind] = value
                entry.update(state="undelivered", reason="cancelled_before_launch")
                self._finish("cancelled")
                return True
            try:
                self._pending.put_nowait((packages.encode_message(value), entry))
            except queue.Full:
                entry.update(state="undelivered", reason="queue_full")
                return False
            self._accepted[kind] = value
            entry.update(state="pending", message=value)
            return True

    def cancel(self):
        accepted = self._notify("cancel")
        if self._done.is_set():
            self._supervisor._release_reservation(self)
        return accepted

    def notify_acquisition(self, outcome, *, writer):
        return self._notify("acquisition", outcome=outcome, writer=writer)

    def notify_writer_finished(self):
        return self._notify("writer", state="finished")

    def _fail_protocol(self, reason, detail, field=None):
        with self._lock:
            if self._violation is None:
                self._violation = dict(reason=reason, detail=detail)
                if field is not None:
                    self._violation["field"] = field
        self._stop.set()

    def _stdout(self, pipe):
        buffer = bytearray()
        try:
            while True:
                chunk = pipe.read(min(4096, packages.MAX_MESSAGE_BYTES - len(buffer)))
                if not chunk:
                    if buffer:
                        self._fail_protocol("partial_line", "stdout ended without newline")
                    return
                if self._discard_stdout:
                    with self._lock:
                        self._diagnostics["discarded_stdout_bytes"] += len(chunk)
                    continue
                buffer.extend(chunk)
                while b"\n" in buffer:
                    index = buffer.index(b"\n") + 1
                    if index > packages.MAX_MESSAGE_BYTES:
                        self._fail_protocol("message_too_large", "stdout line exceeds byte bound")
                        return
                    line = bytes(buffer[:index])
                    del buffer[:index]
                    try:
                        message = json.loads(line.decode("utf-8"),
                                             parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
                    except UnicodeError:
                        self._fail_protocol("invalid_utf8", "stdout is not UTF-8")
                        return
                    except (ValueError, RecursionError):
                        self._fail_protocol("invalid_json", "stdout is not JSON")
                        return
                    try:
                        message = packages.validate_worker_message(message, job_id=self.job_id,
                                                                   operation=self._record["operation"])
                    except (packages.PackageRefusal, TypeError, RecursionError) as exc:
                        self._fail_protocol("protocol_violation", str(exc), getattr(exc, "field", "message"))
                        return
                    with self._lock:
                        if self._record["result"] is not None:
                            self._violation = dict(reason="message_after_terminal", detail="exactly one terminal required")
                            self._stop.set()
                            return
                        if self._first is None:
                            self._first = time.perf_counter()
                        kind = message["type"]
                        if kind == "status":
                            if len(self._status) == self._status.maxlen:
                                self._record["status_dropped"] += 1
                            self._status.append(message["message"])
                        elif kind == "artifact":
                            descriptor = message["artifact"]
                            if descriptor["path"] not in self._declared and len(self._declared) >= packages.MAX_ARTIFACTS:
                                self._violation = dict(reason="too_many_artifacts", detail="artifact limit exceeded")
                                self._stop.set()
                                return
                            self._declared[descriptor["path"]] = descriptor
                        else:
                            self._record["result"] = message
                            self._shutdown = self._shutdown or time.perf_counter()
                            if self._opens_window:
                                self._discard_stdout = True
                                self._diagnostics["discarded_stdout_bytes"] += len(buffer)
                                buffer.clear()
                            self._stop.set()
                    if self._discard_stdout:
                        break
                if len(buffer) >= packages.MAX_MESSAGE_BYTES:
                    self._fail_protocol("message_too_large", "unterminated stdout exceeds byte bound")
                    return
        except Exception as exc:
            if self._discard_stdout:
                with self._lock:
                    if len(self._diagnostics["cleanup_failures"]) < 8:
                        self._diagnostics["cleanup_failures"].append(f"stdout: {exc}"[:2048])
            else:
                self._fail_protocol("stdout_failed", str(exc))

    def _drain_stderr(self, pipe):
        try:
            while chunk := pipe.read(4096):
                with self._lock:
                    target = self._window_stderr if self._discard_stdout else self._stderr
                    target.extend(chunk)
                    del target[:-self._supervisor.max_stderr_bytes]
        except Exception as exc:
            if self._discard_stdout:
                with self._lock:
                    if len(self._diagnostics["cleanup_failures"]) < 8:
                        self._diagnostics["cleanup_failures"].append(f"stderr: {exc}"[:2048])
            else:
                self._fail_protocol("stderr_failed", str(exc))

    def _stdin(self, pipe, line):
        entry = None
        unavailable = None
        try:
            _write_pipe(pipe, line)
            while not self._stop.is_set():
                try:
                    data, entry = self._pending.get(timeout=0.05)
                except queue.Empty:
                    continue
                _write_pipe(pipe, data)
                with self._lock:
                    entry["state"] = "delivered"
                    msg = entry["message"]
                    if msg["type"] == "acquisition":
                        self._record["lifecycle"].update(acquisition=msg["outcome"], writer=msg["writer"])
                    elif msg["type"] == "writer":
                        self._record["lifecycle"]["writer"] = "finished"
                    else:
                        self._cancel_sent = time.perf_counter()
                        self._shutdown = self._shutdown or self._cancel_sent
                entry = None
        except OSError:
            # A disk observer may stop accepting notifications and keep working.
            # Delivery failure must never become a run deadline or cancellation.
            unavailable = "stdin_closed"
        except Exception as exc:
            unavailable = "stdin_failed"
            self._fail_protocol("stdin_failed", str(exc))
        finally:
            try:
                pipe.close()
            except Exception as exc:
                unavailable = "stdin_failed"
                self._fail_protocol("stdin_failed", str(exc))
            with self._lock:
                self._stdin_unavailable_reason = unavailable or "worker_exited"
                if unavailable is None:
                    # The supervisor elected to close stdin (terminal/cleanup).
                    self._shutdown = self._shutdown or time.perf_counter()
                if entry is not None and entry["state"] == "pending":
                    entry.update(state="undelivered", reason=self._stdin_unavailable_reason)
                self._undeliver_pending(self._stdin_unavailable_reason)


class Supervisor:
    def __init__(self, *, max_workers=MAX_CONCURRENT_WORKERS, max_queued=MAX_QUEUED_JOBS,
                 startup_deadline_s=STARTUP_DEADLINE_S, self_check_deadline_s=SELF_CHECK_DEADLINE_S,
                 shutdown_grace_s=SHUTDOWN_GRACE_S, max_stderr_bytes=MAX_STDERR_BYTES,
                 max_retained_status=MAX_RETAINED_STATUS,
                 max_pending_notifications=MAX_PENDING_NOTIFICATIONS,
                 desktop_probe=None):
        for value in (max_workers, max_queued, max_stderr_bytes, max_retained_status, max_pending_notifications):
            if type(value) is not int or value < 1:
                raise ValueError("bounds must be positive integers")
        for value in (startup_deadline_s, self_check_deadline_s, shutdown_grace_s):
            if not math.isfinite(value) or value <= 0:
                raise ValueError("deadlines must be positive and finite")
        self.startup_deadline_s = startup_deadline_s
        self.self_check_deadline_s = self_check_deadline_s
        self.shutdown_grace_s = shutdown_grace_s
        self.max_stderr_bytes = max_stderr_bytes
        self.max_retained_status = max_retained_status
        self.max_pending_notifications = max_pending_notifications
        self._desktop_probe = desktop_probe or interactive_desktop
        self._windows = {}
        self._window_reservations = {}
        self._queue = queue.Queue(maxsize=max_queued)
        self._closing = threading.Event()
        self._lock = threading.Lock()
        self._threads = [threading.Thread(target=self._dispatch, name=f"skill-dispatch-{i}", daemon=True)
                         for i in range(max_workers)]
        for thread in self._threads:
            thread.start()

    def submit(self, release, policy, *, now, python, operation, parameters,
               dataset=None, output_dir=None, deadline_s=None, job_id=None):
        handle = JobHandle(self, job_id=job_id)
        if isinstance(operation, str) and len(operation) <= packages.MAX_OPERATION_NAME_LENGTH:
            handle._record["operation"] = operation
        try:
            manifest = packages.validate_manifest(release["manifest"])
            intake = packages.validate_intake(release["intake"])
            identity = {key: intake[key] for key in ("publisher", "package_id", "version", "artifact_digest")}
            handle._record["release"] = identity
            packages._bound_release(manifest, intake)
            packages.supported_executable(manifest)
            packages.check_release(intake, policy, purpose="execution", now=now)
            if operation not in {op["name"] for op in manifest["operations"]}:
                raise packages.PackageRefusal("operation", "undeclared operation")
            if deadline_s is not None and (type(deadline_s) not in (int, float) or
                                           not math.isfinite(deadline_s) or deadline_s <= 0):
                raise packages.PackageRefusal("deadline_s", "expected positive finite deadline")
            message = dict(protocol=packages.ANALYSIS_PROTOCOL, type="job", job_id=handle.job_id,
                           release=identity, operation=operation, parameters=parameters)
            if operation != "self_check":
                message.update(input=dict(dataset=os.fspath(dataset) if dataset is not None else None),
                               output_dir=os.fspath(output_dir) if output_dir is not None else None)
            elif dataset is not None or output_dir is not None:
                raise packages.PackageRefusal("input" if dataset is not None else "output_dir", "self_check has no paths")
            message = packages.validate_job(message)
            handle._record.update(release=identity, operation=operation)
            handle._job = message
            handle._release_dir = os.fspath(release["release_dir"])
            handle._manifest = manifest
            handle._python = os.fspath(python)
            handle._deadline = deadline_s
            handle._opens_window = next(op for op in manifest["operations"] if op["name"] == operation).get("opens_window", False)
            with self._lock:
                if self._closing.is_set():
                    with handle._lock:
                        handle._finish("dispatch_failed", dict(reason="closed", detail="supervisor is closed"))
                else:
                    if handle._opens_window:
                        if handle.job_id in self._window_reservations:
                            raise packages.PackageRefusal("job_id", "window job id is already reserved")
                        if len(self._window_reservations) >= MAX_OPEN_WINDOWS:
                            opened = [f"{h._record['release']['package_id']}:{h._record['operation']} ({h.job_id})"
                                      for h, *_ in self._windows.values()]
                            pending = [f"{h._record['release']['package_id']}:{h._record['operation']} ({h.job_id})"
                                       for jid, h in self._window_reservations.items() if jid not in self._windows]
                            raise packages.PackageRefusal("opens_window", f"window limit {MAX_OPEN_WINDOWS}; open windows: {opened}; pending reservations: {pending}")
                        self._window_reservations[handle.job_id] = handle
                    try:
                        self._queue.put_nowait(handle)
                    except queue.Full:
                        self._window_reservations.pop(handle.job_id, None)
                        raise
        except queue.Full:
            with handle._lock:
                handle._finish("dispatch_failed", dict(reason="queue_full", detail="dispatch queue is full"))
        except Exception as exc:
            with handle._lock:
                handle._finish("refused", dict(reason="refused", field=getattr(exc, "field", "submit"), detail=str(exc)))
        return handle

    def check_desktop(self):
        if not self._desktop_probe():
            raise packages.PackageRefusal("opens_window", DESKTOP_REFUSAL)

    def _release_reservation(self, handle):
        with self._lock:
            if handle.job_id not in self._windows:
                self._window_reservations.pop(handle.job_id, None)

    def list_open_windows(self):
        with self._lock:
            return [dict(package=f"{h._record['release']['publisher']}/{h._record['release']['package_id']}",
                         operation=h._record['operation'], job_id=h.job_id,
                         dataset=h._job['input']['dataset'], opened_at=opened)
                    for h, process, job, threads, opened in self._windows.values()]

    def window_diagnostics(self, job_id):
        with self._lock:
            entry = self._windows.get(job_id)
        if entry is None:
            return None
        with entry[0]._lock:
            value = deepcopy(entry[0]._diagnostics)
            value["stderr_tail"] = bytes(entry[0]._window_stderr).decode("utf-8", errors="replace")
            return value

    def close_window(self, job_id):
        with self._lock:
            entry = self._windows.get(job_id)
            pending = self._window_reservations.get(job_id) if entry is None else None
            if entry is not None and entry[0]._window_reaping:
                return False
            if entry is not None:
                handle = entry[0]
                handle._window_kills += 1
                handle._window_kills_done.clear()
        if entry is None:
            try:
                return pending.cancel() if pending is not None else False
            except Exception:
                return False
        try:
            _kill_tree(entry[1], entry[2])
        except Exception as exc:
            with handle._lock:
                handle._diagnostics["close_failure_count"] = handle._diagnostics.get("close_failure_count", 0) + 1
                handle._diagnostics["cleanup_failures"].append(f"window kill: {exc}"[:2048])
                del handle._diagnostics["cleanup_failures"][:-8]
            return False
        finally:
            with self._lock:
                handle._window_kills -= 1
                if not handle._window_kills:
                    handle._window_kills_done.set()
        return True

    def _reap_window(self, entry):
        handle, process, job, threads, _ = entry
        failures = []
        try:
            process.wait()
        finally:
            with self._lock:
                handle._window_reaping = True
            # No handle close or group cleanup may race an OS kill already
            # claimed by a caller. No OS calls or waits hold the lifecycle lock.
            handle._window_kills_done.wait()
            try:
                _kill_tree(process, job)  # Inherited pipes must die before joins.
                process.wait(timeout=5)
            except Exception as exc:
                failures.append(str(exc)[:2048])
            end = time.perf_counter() + 2
            for thread in threads:
                thread.join(max(0, end - time.perf_counter()))
            if any(thread.is_alive() for thread in threads):
                failures.append("pipe thread did not stop")
            else:
                for pipe in (process.stdin, process.stdout, process.stderr):
                    try:
                        pipe.close()
                    except Exception as exc:
                        failures.append(str(exc)[:2048])
            if job is not None:
                try:
                    job.close()
                except OSError as exc:
                    failures.append(str(exc)[:2048])
            with handle._lock:
                handle._diagnostics.update(exit_code=process.returncode,
                    cleanup_failures=(handle._diagnostics["cleanup_failures"] + failures)[:8])
            with self._lock:
                self._windows.pop(handle.job_id, None)
                self._window_reservations.pop(handle.job_id, None)
            handle._window_done.set()

    def self_check(self, release, policy, *, now, python, timeout=None):
        handle = self.submit(release, policy, now=now, python=python, operation="self_check",
                             parameters={}, deadline_s=timeout)
        # The worker deadline starts at spawn; queued time is deliberately separate.
        handle.wait()
        return handle.record()

    def close(self, timeout=15):
        with self._lock:
            self._closing.set()
            while True:
                try:
                    handle = self._queue.get_nowait()
                except queue.Empty:
                    break
                with handle._lock:
                    if not handle._done.is_set():
                        handle._finish("cancelled")
                self._window_reservations.pop(handle.job_id, None)
                self._queue.task_done()
            windows = list(self._windows.values())
        for entry in windows:
            self.close_window(entry[0].job_id)
        end = time.perf_counter() + max(0, timeout)
        for thread in self._threads:
            thread.join(max(0, end - time.perf_counter()))
        # A dispatcher racing shutdown either cleans up normally or published
        # its entry before close took the snapshot. No later adoption is allowed.
        for entry in windows:
            entry[0]._window_done.wait(max(0, end - time.perf_counter()))

    def _dispatch(self):
        while not self._closing.is_set():
            try:
                handle = self._queue.get(timeout=0.05)
            except queue.Empty:
                continue
            try:
                with handle._lock:
                    if handle._done.is_set():
                        continue
                    if self._closing.is_set():
                        handle._finish("cancelled")
                        continue
                    handle._record["state"] = "running"
                self._run(handle)
            except Exception as exc:
                with handle._lock:
                    handle._finish("supervisor_failed", dict(reason="launch_failed", detail=str(exc)))
            finally:
                self._release_reservation(handle)
                self._queue.task_done()

    def _run(self, handle):
        process = job = temporary = None
        threads = []
        failure = None
        sampled = set()
        handed_off = False

        def sample_priority():
            with handle._lock:
                arrivals = dict(at_start=handle._first is not None,
                                at_end=handle._record["result"] is not None)
            for phase, arrived in arrivals.items():
                if arrived and phase not in sampled:
                    sampled.add(phase)
                    value, reason, count = _read_priority(process, job)
                    with handle._lock:
                        handle._record["priority"][phase] = value
                        handle._record["priority"]["processes_read"][phase] = count
                        if reason:
                            handle._record["priority"]["reason"][phase] = reason

        def label_unsampled():
            # Caller holds handle._lock. Labels phases monitoring never sampled.
            for phase, arrived in (("at_start", handle._first is not None),
                                   ("at_end", handle._record["result"] is not None)):
                if phase not in sampled:
                    handle._record["priority"]["reason"][phase] = (
                        "message arrived after priority monitoring ended" if arrived else
                        "worker message never arrived")

        try:
            if handle._opens_window:
                self.check_desktop()
            packages.verify_release_assets(handle._release_dir, handle._manifest)
            if self._closing.is_set():
                with handle._lock:
                    handle._finish("cancelled")
                return
            if handle._job["operation"] == "self_check":
                temporary = tempfile.TemporaryDirectory(prefix="microclaw-self-check-")
                cwd = temporary.name
            else:
                cwd = handle._job["output_dir"]
            env = {key: value for key, value in os.environ.items()
                   if not key.upper().startswith("MICROCLAW_") and
                   key.upper() not in {"PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"}}
            env.update(PYTHONPATH=os.path.abspath(handle._release_dir), PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
            argv = [handle._python, "-u", "-m", handle._manifest["entry_point"]["module"]]
            if os.name == "nt":
                idle = _windows_priority() == 0x40
                kwargs = dict(creationflags=0x00000004 | 0x08000000 | (0 if idle else 0x4000))
                requested, inherited = (0x40 if idle else 0x4000), idle
            else:
                try:
                    own = os.nice(0)
                except OSError:
                    # macOS sandbox can deny even nice(0); read without a write.
                    own = os.getpriority(os.PRIO_PROCESS, 0)
                increment = max(0, 10 - own)
                if increment:
                    argv = ["nice", "-n", str(increment), *argv]
                kwargs = dict(start_new_session=True)
                requested, inherited = own + increment, increment == 0
            with handle._lock:
                handle._record["priority"].update(requested=requested, inherited=inherited)
            handle._spawned = time.perf_counter()
            process = subprocess.Popen(argv,
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       cwd=cwd, env=env, bufsize=0, **kwargs)
            if os.name == "nt":
                job = _WindowsJob(process)
            for target, args in ((handle._stdout, (process.stdout,)),
                                 (handle._drain_stderr, (process.stderr,)),
                                 (handle._stdin, (process.stdin, packages.encode_message(handle._job)))):
                thread = threading.Thread(target=target, args=args, daemon=True, name=f"skill-pipe-{handle.job_id}")
                threads.append(thread)
                thread.start()
            while True:
                exited = process.poll() is not None
                sample_priority()
                if handle._opens_window:
                    with handle._lock:
                        terminal = handle._record["result"]
                        violation = handle._violation
                    if exited and terminal is None:
                        threads[0].join(2)  # Same bounded pipe budget as ordinary cleanup.
                        with handle._lock:
                            terminal = handle._record["result"]
                            violation = handle._violation
                    if terminal is not None and violation is None and not self._closing.is_set():
                        state = terminal["state"]
                        retained, rejected = self._retain_artifacts(handle, terminal["artifacts"], state)
                        # Pipe completion closes stdin; drain threads remain owned by the window.
                        threads[2].join(2)
                        if threads[2].is_alive():
                            raise RuntimeError("stdin handoff did not stop")
                        entry = (handle, process, job, threads, datetime.now(timezone.utc).isoformat())
                        with self._lock:
                            if not self._closing.is_set():
                                self._windows[handle.job_id] = entry
                                handed_off = True
                                with handle._lock:
                                    handle._record.update(artifacts=retained, rejected_artifacts=rejected,
                                                          exit_code=None, window_retained=True)
                                    label_unsampled()
                                    worker_failure = dict(reason="worker_failed", detail=terminal["failure"]["message"]) if state == "failed" else None
                                    handle._finish(state, worker_failure)
                        if handed_off:
                            try:
                                threading.Thread(target=self._reap_window, args=(entry,), daemon=True,
                                                 name=f"skill-window-{handle.job_id}").start()
                            except Exception:
                                _kill_tree(process, job)
                                self._reap_window(entry)
                            return
                if exited:
                    break
                now = time.perf_counter()
                with handle._lock:
                    first, terminal = handle._first, handle._record["result"]
                    shutdown = handle._shutdown
                    violation = handle._violation
                reason = None
                if violation:
                    failure = violation
                    break
                if self._closing.is_set():
                    reason = "supervisor_closed"
                elif shutdown is not None and now - shutdown >= self.shutdown_grace_s:
                    reason = "shutdown_deadline"
                elif first is None and now - handle._spawned >= self.startup_deadline_s:
                    reason = "startup_deadline"
                elif terminal is None:
                    deadline = handle._deadline
                    if handle._job["operation"] == "self_check":
                        deadline = min(deadline, self.self_check_deadline_s) if deadline else self.self_check_deadline_s
                    if deadline is not None and now - handle._spawned >= deadline:
                        reason = "self_check_deadline" if handle._job["operation"] == "self_check" else "deadline"
                if reason:
                    failure = dict(reason=reason, detail="worker deadline or supervisor closure")
                    break
                time.sleep(0.01)
        except packages.PackageRefusal as exc:
            failure = dict(reason="desktop_unavailable" if exc.field == "opens_window" else "asset_refused",
                           field=exc.field, detail=str(exc))
        except Exception as exc:
            failure = dict(reason="launch_failed", detail=str(exc))
        finally:
            handle._stop.set()
            if process is not None and not handed_off:
                try:
                    _kill_tree(process, job)  # Also after a successful parent exit.
                    process.wait(timeout=5)
                except Exception as exc:
                    failure = dict(reason="cleanup_failed", detail=str(exc))
                end = time.perf_counter() + 2
                for thread in threads:
                    thread.join(max(0, end - time.perf_counter()))
                if any(thread.is_alive() for thread in threads):
                    failure = dict(reason="pipe_cleanup_failed", detail="pipe thread did not stop")
                else:
                    for pipe in (process.stdin, process.stdout, process.stderr):
                        pipe.close()
                handle._record["exit_code"] = process.returncode
                if job is not None:
                    try:
                        job.close()
                    except OSError as exc:
                        failure = dict(reason="cleanup_failed", detail=str(exc))
            if not handed_off:
                with handle._lock:
                    label_unsampled()
            if temporary is not None:
                try:
                    temporary.cleanup()
                except OSError as exc:
                    failure = dict(reason="cleanup_failed", detail=str(exc))
        with handle._lock:
            result = handle._record["result"]
            failure = handle._violation or failure
            if failure is None:
                if result is None:
                    failure = dict(reason="exit_without_terminal", detail="worker exited without terminal")
                elif handle._record["exit_code"] != 0:
                    failure = dict(reason="nonzero_exit", detail="worker exited nonzero after terminal")
            state = "supervisor_failed" if failure else result["state"]
            if not failure and state == "failed":
                failure = dict(reason="worker_failed", detail=result["failure"]["message"])
            descriptors = result["artifacts"] if result else list(handle._declared.values())
            handle._shutdown = handle._shutdown or time.perf_counter()
        retained, rejected = self._retain_artifacts(handle, descriptors, state)
        with handle._lock:
            handle._record.update(artifacts=retained, rejected_artifacts=rejected)
            handle._finish(state, failure)

    def _retain_artifacts(self, handle, descriptors, state):
        retained, rejected = [], []
        for descriptor in descriptors:
            try:
                path = packages.safe_release_path(handle._job["output_dir"], descriptor["path"])
                info = path.stat()
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise packages.PackageRefusal("path", "artifact must be a regular file with one link")
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                if digest.hexdigest() != descriptor["sha256"]:
                    raise packages.PackageRefusal("sha256", "artifact digest mismatch")
                retained.append(dict(descriptor, validity=descriptor["validity"] if state == "succeeded" else "partial"))
            except (OSError, packages.PackageRefusal) as exc:
                rejected.append(dict(descriptor, reason=getattr(exc, "field", "path"), detail=str(exc)))
        return retained, rejected
