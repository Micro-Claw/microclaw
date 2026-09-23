"""Supervise trusted publisher workers over bounded NDJSON pipes.

Timing contract: submit performs only bounded data validation, one release
signature verification and put_nowait. Notifications validate small messages and
put_nowait; neither path launches, hashes assets, accesses the filesystem, writes
pipes, starts threads, or waits for a worker. Short locks protect in-memory
transitions only, never I/O. Fixed dispatchers own launch, deadlines, verification
and cleanup. Message bytes, pending stdin, retained stdout and stderr, queued
jobs and live workers have explicit bounds. There is no total analysis deadline
unless supplied. Measurements live in design/83-block83c-dispatch-timing.py.

Workers have the user's permissions, not an OS sandbox. Windows uses a Job
Object assigned before resume. POSIX uses a new session/process group; a POSIX
descendant which calls setsid escapes (accepted off the shipping platform).
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
import subprocess
import tempfile
import threading
import time
from uuid import uuid4

from . import skill_packages as packages

MAX_STDERR_BYTES = 65536
MAX_RETAINED_STATUS = 256
MAX_PENDING_NOTIFICATIONS = 8
MAX_QUEUED_JOBS = 4
MAX_CONCURRENT_WORKERS = 2
STARTUP_DEADLINE_S = 60
SELF_CHECK_DEADLINE_S = 120
SHUTDOWN_GRACE_S = 10


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
            "OpenProcess": ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
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

    def close(self):
        if self.handle:
            self.check(self.kernel.CloseHandle(self.handle))
            self.handle = None


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


class JobHandle:
    def __init__(self, supervisor):
        self.job_id = uuid4().hex
        self._supervisor = supervisor
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._stop = threading.Event()
        self._pending = queue.Queue(maxsize=supervisor.max_pending_notifications)
        self._status = deque(maxlen=supervisor.max_retained_status)
        self._stderr = bytearray()
        self._declared = {}
        self._accepted = {}
        self._created = time.monotonic()
        self._spawned = self._first = self._shutdown = self._cancel_sent = None
        self._stdin_closed = None
        self._violation = None
        self._record = dict(job_id=self.job_id, release={}, operation=None, state="queued",
                            result=None, failure=None, artifacts=[], rejected_artifacts=[],
                            lifecycle=dict(acquisition=None, writer=None), notifications=[],
                            status=[], status_dropped=0, stderr_tail="", exit_code=None,
                            duration_breakdown=dict(queued_s=0.0, startup_s=0.0, running_s=0.0,
                                                    shutdown_s=0.0, accounted_s=0.0))

    def record(self):
        with self._lock:
            value = deepcopy(self._record)
            value["status"] = list(self._status)
            value["stderr_tail"] = bytes(self._stderr).decode("utf-8", errors="replace")
            return value

    def wait(self, timeout=None):
        return self._done.wait(timeout)

    def _finish(self, state, failure=None):
        # Caller holds the lock. This is CPU-only, including queued cancellation.
        end = time.monotonic()
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
        while True:
            try:
                _, entry = self._pending.get_nowait()
            except queue.Empty:
                break
            entry.update(state="undelivered", reason="worker_exited")
        self._done.set()

    def _notify(self, kind, **fields):
        message = dict(protocol=packages.ANALYSIS_PROTOCOL, type=kind, job_id=self.job_id, **fields)
        with self._lock:
            entry = dict(type=kind, state="refused")
            self._record["notifications"].append(entry)
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
        return self._notify("cancel")

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
                            self._first = time.monotonic()
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
                            self._shutdown = self._shutdown or time.monotonic()
                            self._stop.set()
                if len(buffer) >= packages.MAX_MESSAGE_BYTES:
                    self._fail_protocol("message_too_large", "unterminated stdout exceeds byte bound")
                    return
        except (OSError, ValueError) as exc:
            self._fail_protocol("stdout_failed", str(exc))

    def _drain_stderr(self, pipe):
        try:
            while chunk := pipe.read(4096):
                with self._lock:
                    self._stderr.extend(chunk)
                    del self._stderr[:-self._supervisor.max_stderr_bytes]
        except (OSError, ValueError):
            pass

    def _stdin(self, pipe, line):
        entry = None
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
                        self._cancel_sent = time.monotonic()
                        self._shutdown = self._shutdown or self._cancel_sent
                entry = None
        except (OSError, ValueError):
            if entry is not None:
                with self._lock:
                    entry.update(state="undelivered", reason="stdin_closed")
        finally:
            pipe.close()
            with self._lock:
                self._stdin_closed = time.monotonic()
                self._shutdown = self._shutdown or self._stdin_closed


class Supervisor:
    def __init__(self, *, max_workers=MAX_CONCURRENT_WORKERS, max_queued=MAX_QUEUED_JOBS,
                 startup_deadline_s=STARTUP_DEADLINE_S, self_check_deadline_s=SELF_CHECK_DEADLINE_S,
                 shutdown_grace_s=SHUTDOWN_GRACE_S, max_stderr_bytes=MAX_STDERR_BYTES,
                 max_retained_status=MAX_RETAINED_STATUS,
                 max_pending_notifications=MAX_PENDING_NOTIFICATIONS):
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
        self._queue = queue.Queue(maxsize=max_queued)
        self._closing = threading.Event()
        self._lock = threading.Lock()
        self._threads = [threading.Thread(target=self._dispatch, name=f"skill-dispatch-{i}", daemon=True)
                         for i in range(max_workers)]
        for thread in self._threads:
            thread.start()

    def submit(self, release, policy, *, now, python, operation, parameters,
               dataset=None, output_dir=None, deadline_s=None):
        handle = JobHandle(self)
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
            with self._lock:
                if self._closing.is_set():
                    with handle._lock:
                        handle._finish("dispatch_failed", dict(reason="closed", detail="supervisor is closed"))
                else:
                    self._queue.put_nowait(handle)
        except queue.Full:
            with handle._lock:
                handle._finish("dispatch_failed", dict(reason="queue_full", detail="dispatch queue is full"))
        except Exception as exc:
            with handle._lock:
                handle._finish("refused", dict(reason="refused", field=getattr(exc, "field", "submit"), detail=str(exc)))
        return handle

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
                self._queue.task_done()
        end = time.monotonic() + max(0, timeout)
        for thread in self._threads:
            thread.join(max(0, end - time.monotonic()))

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
                self._queue.task_done()

    def _run(self, handle):
        process = job = temporary = None
        threads = []
        failure = None
        try:
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
            kwargs = dict(creationflags=0x00000004 | 0x08000000) if os.name == "nt" else dict(start_new_session=True)
            handle._spawned = time.monotonic()
            process = subprocess.Popen([handle._python, "-u", "-m", handle._manifest["entry_point"]["module"]],
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
            while process.poll() is None:
                now = time.monotonic()
                with handle._lock:
                    first, terminal = handle._first, handle._record["result"]
                    shutdown = handle._shutdown or handle._cancel_sent or handle._stdin_closed
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
            failure = dict(reason="asset_refused", field=exc.field, detail=str(exc))
        except Exception as exc:
            failure = dict(reason="launch_failed", detail=str(exc))
        finally:
            handle._stop.set()
            if process is not None:
                try:
                    _kill_tree(process, job)  # Also after a successful parent exit.
                    process.wait(timeout=5)
                except Exception as exc:
                    failure = dict(reason="cleanup_failed", detail=str(exc))
                end = time.monotonic() + 2
                for thread in threads:
                    thread.join(max(0, end - time.monotonic()))
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
            handle._shutdown = handle._shutdown or time.monotonic()
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
        with handle._lock:
            handle._record.update(artifacts=retained, rejected_artifacts=rejected)
            handle._finish(state, failure)
