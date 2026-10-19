"""89a instruments only. Run p3 first on the demo machine; --selftest needs no MM.

Dependency transcription references (paths relative to site-packages):
pycromanager/acquisition/acq_constructor.py:13-49 (dispatch);
acquisition_superclass.py:164-198,306-338,368-371 (notification queues, exit);
java_backend_acquisitions.py:185-225,345-378 (notification decoding and joins);
acq_eng_py/main/acquisition_event.py:132-134,208-211 (property triples);
acq_eng_py/main/acquisition_event.py:10-60 (merged properties remain empty);
acq_eng_py/internal/engine.py:557-563,741 (additional properties, TODO);
acq_eng_py/main/acq_notification.py:7-40,78-91,118-125 (milestones);
ndstorage/ndtiff_index.py:93-115,142-153 (native uint32 entry layout);
ndstorage/ndtiff_dataset.py:168-224,227-231 (writer and coordinates).
The fake uses ndstorage's actual TIFF writer, with the Java index file behavior
from the operator's 2026-10-19 javap of NDTiffStorage-2.19.3 IndexWriter:
constructor setLength(26214400), addEntry one in-place channel write,
finishedWriting truncates to the channel position.
CoreLog grammar: design/78-block78a-m5-gate.py:42-49 and its excerpt:1-3.
Collections: tests/mda_fakes.py:14-18 (size/get, noniterable bridge vectors).
mmpycorex/core.py:64-86 and pyjavaz/bridge.py:238-265 (Core socket isolation).

No controller, product imports, GUI updates, or camera configuration writes.
Exposure is in acquisition events and restored to its entry value afterwards.
All bridge operations and teardown have foreground deadlines. A timed-out
third-party call cannot be killed safely; its daemon remains disclosed in report.
"""
import argparse
import contextlib
import copy
from decimal import Decimal, ROUND_HALF_EVEN
import json
import math
import os
import queue
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import traceback

# Operator decision 2026-10-18, R157: healthy 12.03–12.05 ms n=8;
# transient slow 21.56–22.33 ms n=4. This threshold is ONLY for 10 ms controls.
R157_CONTROL_P95_MS = 13.0
CALL_BOUND_S = 15.0
TEARDOWN_BOUND_S = 30.0
INDEX_CHUNK_BYTES = 64 * 1024
JAVA_INDEX_PREALLOCATED_BYTES = 26214400
WORKER_BATCH = 4
NOTIFICATION_CONTRACT = (
    "acq_events_finished means event execution finished, not data sink drained; "
    "dispatcher consumes one queue in arrival order until BOTH events-finished "
    "and data-sink-finished. Image callbacks use a separate queue/thread; no "
    "cross-thread order guaranteed. Contract 6 warns saved counts can lag until "
    "__exit__; their actual lag is measured here, never used as the index count."
)
CAMERA_MILESTONES = (
    "pre_sequence_started",
    "post_sequence_stopped",
    "pre_snap",
    "post_snap",
)


def now():
    return time.perf_counter()


def bounded(fn, seconds=CALL_BOUND_S):
    done = threading.Event()
    box = {}

    def work():
        try:
            box["value"] = fn()
        except BaseException as e:
            box["error"] = e
        finally:
            done.set()

    threading.Thread(target=work, daemon=True).start()
    if not done.wait(seconds):
        raise TimeoutError(f"operation exceeded {seconds}s")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def vector(v):
    return [v.get(i) for i in range(v.size())]


def percentile(values, p):
    if not values:
        return None
    a = sorted(values)
    x = (len(a) - 1) * p
    i = int(x)
    return a[i] + (a[min(i + 1, len(a) - 1)] - a[i]) * (x - i)


def dist(values):
    return dict(
        n=len(values),
        p50=percentile(values, 0.5),
        p95=percentile(values, 0.95),
        max=max(values) if values else None,
    )


def limb(status, reason, **numbers):
    return dict(status=status, reason=reason, **numbers)


def check(ok, reason, **numbers):
    return limb("PASS" if ok else "FAIL", reason, **numbers)


def exit_code(report):
    states = [x["status"] for x in report["limbs"].values()]
    return 1 if "FAIL" in states else 2 if "NOT EXERCISED" in states else 0


class IndexReader:
    """Resume at the end of the last complete, valid entry, never at file size.

    Java writes into a preallocated zero-filled index; Python appends. Discard
    read-ahead after each poll so either writer can complete an entry in place.
    Layout: ndstorage/ndtiff_index.py:97-111, particularly the eight uint32
    trailer fields at 109-111 (width=1, height=2, metadata length=6).
    """

    def __init__(self, path):
        self.path = Path(path)
        self.parsed_offset = 0
        self.count = 0
        self.last = -1
        self.sample_at = None

    def read(self):
        started = now()
        bytes_read = 0
        try:
            with self.path.open("rb") as f:
                if os.fstat(f.fileno()).st_size < self.parsed_offset:
                    self.parsed_offset = 0
                    self.count = 0
                    self.last = -1
                f.seek(self.parsed_offset)
                buffer = bytearray()

                def ensure(length):
                    nonlocal bytes_read
                    while len(buffer) < length:
                        chunk = f.read(INDEX_CHUNK_BYTES)
                        bytes_read += len(chunk)
                        if not chunk:
                            return False
                        buffer.extend(chunk)
                    return True

                while ensure(4):
                    axes_length = struct.unpack_from("I", buffer)[0]
                    if axes_length == 0:
                        break
                    axes_end = 4 + axes_length
                    if not ensure(axes_end):
                        break
                    try:
                        axes = json.loads(buffer[4:axes_end].decode("utf8"))
                    except (UnicodeError, ValueError):
                        break
                    if not isinstance(axes, dict) or not ensure(axes_end + 4):
                        break
                    filename_length = struct.unpack_from("I", buffer, axes_end)[0]
                    if filename_length == 0:
                        break
                    filename_start = axes_end + 4
                    filename_end = filename_start + filename_length
                    end = filename_end + 32
                    if not ensure(end):
                        break
                    try:
                        buffer[filename_start:filename_end].decode("utf8")
                    except UnicodeError:
                        break
                    trailer = struct.unpack_from("IIIIIIII", buffer, filename_end)
                    if trailer[1] == 0 or trailer[2] == 0 or trailer[6] == 0:
                        break
                    self.count += 1
                    self.last = axes.get("time", self.count - 1)
                    self.parsed_offset += end
                    del buffer[:end]
        except FileNotFoundError:
            pass
        self.sample_at = now()
        return dict(
            at=self.sample_at,
            duration_s=self.sample_at - started,
            count=self.count,
            last_ordinal=self.last,
            bytes_read=bytes_read,
            parsed_offset=self.parsed_offset,
        )


def worker_main(args):
    reader = IndexReader(args.worker_index)
    last = -1
    deadline = now() + args.worker_bound
    with open(args.worker_log, "w", buffering=1) as f:
        while now() < deadline and not Path(args.worker_stop).exists():
            row = reader.read()
            # Deliberately bounded processing capacity, not fabricated ordinals.
            last = (
                min(row["last_ordinal"], last + WORKER_BATCH)
                if args.behind
                else row["last_ordinal"]
            )
            row["worker_last_ordinal"] = last
            row["batch"] = WORKER_BATCH
            row["used_at"] = now()
            row["count_age_s"] = row["used_at"] - row["at"]
            f.write(json.dumps(row) + "\n")
            time.sleep(args.worker_delay if args.behind else 0.025)


def discovery(core, device, prop):
    def table(d, p):
        r = dict(device=d, property=p, exists=bool(core.has_property(d, p)))
        if not r["exists"]:
            return r
        r.update(
            read_only=bool(core.is_property_read_only(d, p)),
            pre_init=bool(core.is_property_pre_init(d, p)),
            has_limits=bool(core.has_property_limits(d, p)),
            allowed_values=[
                str(x) for x in vector(core.get_allowed_property_values(d, p))
            ],
            sequenceable=bool(core.is_property_sequenceable(d, p)),
            current=str(core.get_property(d, p)),
        )
        if r["has_limits"]:
            r["limits"] = [
                core.get_property_lower_limit(d, p),
                core.get_property_upper_limit(d, p),
            ]
        return r

    camera = core.get_camera_device()
    return dict(
        camera=camera,
        target=table(device, prop),
        camera_properties=[
            table(camera, str(p))
            for p in vector(core.get_device_property_names(camera))
        ],
    )


def target_values(target, count):
    if target["property"] == "SimulateCrash":
        return None, "SimulateCrash is never written"
    if not target["exists"]:
        return None, "target does not exist"
    if target["read_only"] or target["pre_init"]:
        return None, "read-only or pre-init target"
    allowed = target["allowed_values"]
    if len(allowed) >= count:
        return allowed[:count], None
    if target["has_limits"]:
        lo, hi = [Decimal(str(x).replace(",", ".")) for x in target["limits"]]
        if hi > lo:
            raw = [lo + (hi - lo) * i / (count - 1) for i in range(count)]
            # The coarsest decimal grid that retains distinct, in-limit values
            # avoids asking a device to preserve unnecessary float precision.
            for decimals in range(325):
                step = Decimal(1).scaleb(-decimals)
                rounded = [v.quantize(step, rounding=ROUND_HALF_EVEN) for v in raw]
                if len(set(rounded)) == count and all(lo <= v <= hi for v in rounded):
                    return [format(v, "f") for v in rounded], None
    if len(allowed) >= 2 and count == 2:
        return allowed[:2], None
    return None, f"target cannot provide {count} distinct valid values"


class Writer:
    def __init__(self, env, args, values, hz, imaging):
        self.env = env
        self.args = args
        self.values = values
        self.hz = hz
        self.imaging = imaging
        self.lock = threading.Lock()
        self.revoke = threading.Event()
        self.done = threading.Event()
        self.rows = []
        self.revoked_at = None
        self.start_at = None
        self.end_at = None
        self.imaging_at = None

    def stop(self):
        with self.lock:
            if self.revoked_at is None:
                self.revoked_at = now()
            self.revoke.set()

    def start(self):
        def work():
            try:
                core = self.env.Core(port=self.args.port, new_socket=True)
                if not self.imaging.wait(self.args.start_bound):
                    return
                self.start_at = now()
                due = self.start_at
                while not self.revoke.is_set():
                    if self.revoke.wait(max(0, due - now())):
                        break
                    # Submission and revoke serialize on this exact lock. Bridge call is
                    # outside the lock so a blocked write cannot block revocation.
                    with self.lock:
                        if self.revoke.is_set():
                            break
                        row = dict(start=now(), value=self.values[len(self.rows) % 2])
                        self.rows.append(row)
                    try:
                        core.set_property(
                            self.args.device, self.args.property, row["value"]
                        )
                    except Exception as e:
                        row["error"] = str(e)
                    finally:
                        row["duration_s"] = now() - row["start"]
                    due += 1 / self.hz
            except Exception as e:
                self.rows.append(dict(start=now(), error=str(e), duration_s=0))
            finally:
                self.end_at = now()
                self.done.set()

        threading.Thread(target=work, daemon=True).start()

    def report(self):
        starts = [r["start"] for r in self.rows]
        return dict(
            hz=self.hz,
            count=len(starts),
            writes=self.rows,
            failures=[r for r in self.rows if "error" in r],
            duration_s=dist([r["duration_s"] for r in self.rows if "duration_s" in r]),
            armed_at=self.start_at,
            revoked_at=self.revoked_at,
            imaging_at=self.imaging_at,
            expected_count=max(
                0, math.floor((self.revoked_at - self.imaging_at) * self.hz) + 1
            )
            if self.imaging_at is not None and self.revoked_at is not None
            else 0,
            span_s=starts[-1] - starts[0] if starts else None,
            after_revoke=[
                r
                for r in self.rows
                if self.revoked_at is not None and r["start"] > self.revoked_at
            ],
        )


def dataset_report(env, path, planned, property_key=None):
    ds = env.Dataset(str(path))
    failures = []
    times = []
    property_values = []
    first_metadata = None
    readable = 0
    try:
        axes = sorted(ds.get_image_coordinates_list(), key=lambda a: a.get("time", -1))
        for a in axes:
            try:
                ds.read_image(**a)
                metadata = ds.read_metadata(**a)
                readable += 1
                times.append(metadata.get("ElapsedTime-ms"))
                if first_metadata is None:
                    first_metadata = metadata
                if property_key is not None:
                    property_values.append(metadata.get(property_key))
            except Exception as e:
                failures.append(dict(axes=a, error=str(e)))
    finally:
        ds.close()
    missing = sorted(set(range(planned)) - {a.get("time") for a in axes})
    cadence = None
    if len(times) >= 2 and all(t is not None for t in times):
        times = [float(t) for t in times]
        cadence = dict(
            gap_ms=dist([b - a for a, b in zip(times, times[1:])]),
            span_ms=times[-1] - times[0],
        )
    result = dict(
        indexed=len(axes),
        readable=readable,
        failures=failures,
        planned_minus_indexed=planned - len(axes),
        missing_time_indices=missing,
        cadence=cadence,
        elapsed_ms=times,
        first_frame_metadata=first_metadata,
    )
    if property_key is not None:
        result["metadata_property_values"] = property_values
    return result


def disk_preflight(core, args, out):
    largest_run_frames = {
        "p1": 80 if args.fake else 5000,
        "p2": 80 if args.fake else 3000,
        "p3": 6 if args.fake else 50,
        "p4": 160 if args.fake else 3000,
    }[args.probe]
    width = bounded(core.get_image_width, args.call_bound)
    height = bounded(core.get_image_height, args.call_bound)
    bytes_per_pixel = bounded(core.get_bytes_per_pixel, args.call_bound)
    if min(width, height, bytes_per_pixel) <= 0:
        raise ValueError("camera image dimensions and bytes per pixel must be positive")
    frame_bytes = width * height * bytes_per_pixel
    needed_bytes = math.ceil(frame_bytes * largest_run_frames * 1.25)
    usage = shutil.disk_usage(out)
    return dict(
        image_width=width,
        image_height=height,
        bytes_per_pixel=bytes_per_pixel,
        frame_bytes=frame_bytes,
        largest_run_frames=largest_run_frames,
        margin=1.25,
        needed_bytes=needed_bytes,
        free_bytes=usage.free,
        enough_space=usage.free >= needed_bytes,
    )


def discard_scored_stacks(run, probe, index):
    run["deleted_bytes"] = 0
    run["deleted_files"] = []
    if probe == "p3":
        reason = "P3 datasets retained"
    elif not run.get("teardown_returned"):
        reason = "teardown did not return; dataset retained as evidence"
    elif not run.get("reader_stopped", True):
        reason = "reader did not stop; dataset retained as evidence"
    elif not run.get("acquisition_reader_closed", False):
        reason = (
            "acquisition dataset reader did not close; dataset retained as evidence"
        )
    elif index is None or "dataset" not in run or run.get("dataset_error"):
        reason = "dataset report failed or unavailable; dataset retained as evidence"
    elif (
        run["dataset"]["failures"]
        or run["dataset"]["indexed"] != run["dataset"]["readable"]
    ):
        reason = "dataset readability failed; dataset retained as evidence"
    else:
        try:
            for stack in sorted(index.parent.rglob("*.tif")):
                size = stack.stat().st_size
                stack.unlink()
                run["deleted_bytes"] += size
                run["deleted_files"].append(str(stack.relative_to(index.parent)))
            run[
                "stack_cleanup"
            ] = "scored image stacks deleted; index and logs retained"
        except Exception as e:
            run["stack_cleanup_error"] = f"{type(e).__name__}: {e}"
            run[
                "stack_cleanup"
            ] = "stack deletion incomplete; see deleted_files and stack_cleanup_error"
        return
    run["stack_cleanup"] = reason


def writer_limbs(w):
    expected = w["expected_count"]
    count = w["count"]
    after = [
        row
        for row in w["writes"]
        if w["revoked_at"] is not None and row["start"] > w["revoked_at"]
    ]
    return dict(
        writer_fired=check(
            True, "writer met expected count minus one", count=count, expected=expected
        )
        if count >= max(1, expected - 1)
        else limb(
            "NOT EXERCISED",
            "writer did not meet expected count minus one",
            count=count,
            expected=expected,
        ),
        zero_write_failures=check(
            not w["failures"], "zero writer failures", failures=w["failures"]
        ),
        no_write_after_revoke=check(
            not after, "zero writes started after revoke", count=len(after)
        ),
    )


def run_one(
    env,
    args,
    out,
    name,
    n,
    exposure=10,
    interval=0,
    values=None,
    hz=0,
    reader_hz=0,
    worker=False,
    behind=False,
    abort_after=None,
):
    folder = out / name
    folder.mkdir()
    r = dict(
        name=name,
        planned=n,
        exposure_ms=exposure,
        interval_s=interval,
        writer_start_witness="acq_started notification",
        notifications={},
        samples=[],
        reads=[],
        callback_errors=[],
        camera_milestones={key: 0 for key in CAMERA_MILESTONES},
    )
    imaging = threading.Event()
    complete = threading.Event()
    stopped = threading.Event()
    w = Writer(env, args, values, hz, imaging) if hz else None
    saved = dict(count=0, first_at=None, last_at=None)
    raw_callbacks = (folder / "callbacks.jsonl").open("w")
    callback_lock = threading.Lock()
    r["callback_stream"] = str(folder / "callbacks.jsonl")
    index = None
    acq = None
    proc = None
    reader_stop = threading.Event()
    reader_done = threading.Event()
    state = {}
    state_lock = threading.Lock()
    exit_done = threading.Event()
    abort_done = threading.Event()
    exit_started = False

    def teardown():
        try:
            if acq is not None:
                acq.__exit__(None, None, None)
        except BaseException as e:
            r["exit_error"] = f"{type(e).__name__}: {e}"
        finally:
            r["teardown_return_at"] = now()
            exit_done.set()

    def notification(note):
        try:
            row = dict(at=now(), milestone=note.milestone, payload=note.payload)
            with callback_lock:
                raw_callbacks.write(
                    json.dumps(dict(kind="notification", **row), default=str) + "\n"
                )
                summary = r["notifications"].setdefault(
                    note.milestone, dict(count=0, first_at=row["at"], last_at=row["at"])
                )
                summary["count"] += 1
                summary["last_at"] = row["at"]
                if note.milestone in CAMERA_MILESTONES:
                    r["camera_milestones"][note.milestone] += 1
            if note.milestone == "acq_started":
                r["imaging_started_at"] = row["at"]
                if w:
                    w.imaging_at = row["at"]
                imaging.set()
            if note.is_acquisition_finished_notification():
                complete.set()
                if w:
                    w.stop()
        except BaseException as e:
            r["callback_errors"].append(
                dict(
                    callback="notification", at=now(), error=f"{type(e).__name__}: {e}"
                )
            )

    def image_saved(axes, dataset):
        try:
            at = now()
            with callback_lock:
                raw_callbacks.write(
                    json.dumps(dict(kind="image_saved", at=at, axes=axes), default=str)
                    + "\n"
                )
                saved["count"] += 1
                if saved["first_at"] is None:
                    saved["first_at"] = at
                saved["last_at"] = at
        except BaseException as e:
            r["callback_errors"].append(
                dict(callback="image_saved", at=now(), error=f"{type(e).__name__}: {e}")
            )

    try:
        r["construct_at"] = now()
        acq = bounded(
            lambda: env.Acquisition(
                directory=str(folder),
                name="data",
                show_display=False,
                port=args.port,
                notification_callback_fn=notification,
                image_saved_fn=image_saved,
            )
        )
        bounded(acq.__enter__)
        events = []
        for k in range(n):
            e = dict(axes={"time": k}, exposure=exposure)
            if interval:
                e["min_start_time"] = k * interval
            if values and args.probe == "p3":
                e["properties"] = [[args.device, args.property, values[k]]]
            events.append(e)
        if interval:
            assert all(
                int(k * interval * 1000) != int((k + 1) * interval * 1000)
                for k in range(n - 1)
            )
        r["submitted_at"] = now()
        if w:
            w.start()
        bounded(lambda: acq.acquire(events))
        # Drive completion on exit, including schedules that deliver callbacks
        # only inside exit. P1/P2/control and worker arms never poll the index.
        r["teardown_start_at"] = now()
        exit_started = True
        threading.Thread(target=teardown, daemon=True).start()
        # Discover actual suffix chosen by the storage engine, never guess data_1.
        deadline = now() + args.start_bound
        while now() < deadline:
            matches = list(folder.rglob("NDTiff.index"))
            if len(matches) == 1:
                index = matches[0]
                break
            if len(matches) > 1:
                raise RuntimeError("ambiguous NDTiff index paths")
            time.sleep(0.005)
        if index is None:
            raise TimeoutError("no NDTiff.index before first-image deadline")
        r["index_path"] = str(index)
        if reader_hz:

            def read_loop():
                try:
                    rd = IndexReader(index)
                    while not reader_stop.is_set():
                        row = rd.read()
                        if args.fake:
                            # Fake clock: host scheduling of sub-ms reads is not
                            # a microscope performance assertion. Keep wall time
                            # too; parser, file growth and subprocesses are real.
                            row["wall_duration_s"] = row["duration_s"]
                            row["duration_s"] = 0.0001
                            row["duration_clock"] = "selftest constant-cost clock"
                        r["reads"].append(row)
                        with state_lock:
                            state.update(row)
                        if reader_stop.wait(1 / reader_hz):
                            break
                except Exception as e:
                    r["reader_error"] = str(e)
                finally:
                    reader_done.set()

            threading.Thread(target=read_loop, daemon=True).start()
        if worker:
            cmd = [
                sys.executable,
                "-I",
                str(Path(__file__).resolve()),
                "--worker-index",
                str(index),
                "--worker-log",
                str(folder / "worker.jsonl"),
                "--worker-stop",
                str(folder / "worker.stop"),
                "--worker-bound",
                str(args.run_bound),
                "--worker-delay",
                str(0.03 if args.fake else 0.15),
            ]
            if behind:
                cmd.append("--behind")
            with (folder / "worker-stderr.txt").open("w") as err:
                proc = subprocess.Popen(
                    cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=err
                )

        def aborter():
            try:
                if not imaging.wait(args.start_bound):
                    return
                r["abort_requested_deadline"] = r["imaging_started_at"] + abort_after
                if stopped.wait(max(0, r["abort_requested_deadline"] - now())):
                    return
                if w:
                    w.stop()
                r["abort_at"] = now()
                acq.abort()
                r["abort_returned_at"] = now()
            except Exception as e:
                r["abort_error"] = str(e)
            finally:
                abort_done.set()

        if abort_after is not None:
            threading.Thread(target=aborter, daemon=True).start()
        # Only an explicitly selected probe-side reader arm reads the index.
        deadline = now() + args.run_bound
        while now() < deadline:
            t = now()
            sample = dict(at=t, image_saved_count=saved["count"])
            if reader_hz:
                with state_lock:
                    cache = dict(state)
                sample["cached_index"] = cache
                sample["cache_used_at"] = now()
                sample["index_age_s"] = (
                    sample["cache_used_at"] - cache["at"] if cache else None
                )
                if cache:
                    sample["count"] = cache["count"]
                    sample["last_ordinal"] = cache["last_ordinal"]
            r["samples"].append(sample)
            if abort_after is not None and abort_done.is_set():
                break
            if complete.is_set() or exit_done.is_set():
                break
            if abort_after is None and sample.get("count", 0) >= n:
                break
            time.sleep(0.01 if args.fake else 0.05)
        else:
            r["imaging_timeout"] = True
    except BaseException as e:
        r["run_error"] = f"{type(e).__name__}: {e}"
        if isinstance(e, TimeoutError):
            r["pending_dependency_call"] = True
    finally:
        r["imaging_observation_end_at"] = now()
        stopped.set()
        if w:
            w.stop()
            r["writer_stopped"] = w.done.wait(args.call_bound)
            r["writer"] = w.report()
            for row in r["writer"]["writes"]:
                row["relative_to_submission_s"] = row["start"] - r.get(
                    "submitted_at", row["start"]
                )
        reader_stop.set()
        if reader_hz:
            r["reader_stopped"] = reader_done.wait(args.call_bound)
        if acq is not None and (r.get("run_error") or r.get("imaging_timeout")):
            try:
                bounded(acq.abort, args.call_bound)
                r["cleanup_abort_returned"] = True
            except BaseException as e:
                r["cleanup_abort_error"] = str(e)
                if isinstance(e, TimeoutError):
                    r["pending_dependency_call"] = True
        if not exit_started:
            r["teardown_start_at"] = now()
            threading.Thread(target=teardown, daemon=True).start()
        r["teardown_returned"] = exit_done.wait(args.teardown_bound)
        r["teardown_wait_s"] = now() - r["teardown_start_at"]
        if not r["teardown_returned"] and acq is not None:
            try:
                bounded(acq.abort, args.call_bound)
                r["timeout_abort_returned"] = True
            except BaseException as e:
                r["timeout_abort_error"] = str(e)
                if isinstance(e, TimeoutError):
                    r["pending_dependency_call"] = True
        if proc:
            (folder / "worker.stop").touch()
            try:
                proc.wait(timeout=args.call_bound)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=args.call_bound)
                r["worker_timeout"] = True
            r["worker_returncode"] = proc.returncode
            r["worker_samples"] = (
                [
                    json.loads(line)
                    for line in (folder / "worker.jsonl").read_text().splitlines()
                ]
                if (folder / "worker.jsonl").exists()
                else []
            )
        raw_callbacks.close()
        r["image_saved"] = saved
        r["notification_contract"] = NOTIFICATION_CONTRACT
        milestones = [
            dict(kind=key, at=value["first_at"])
            for key, value in r["notifications"].items()
            if key in ("acq_events_finished", "data_sink_finished")
        ]
        if saved["count"]:
            milestones.append(
                dict(kind="last_image_saved_callback", at=saved["last_at"])
            )
        if "teardown_return_at" in r:
            milestones.append(dict(kind="teardown_return", at=r["teardown_return_at"]))
        r["completion_order"] = sorted(milestones, key=lambda x: x["at"])
    if index is not None and r["teardown_returned"]:
        property_key = args.device + "-" + args.property if args.probe == "p3" else None
        try:
            r["dataset"] = bounded(
                lambda: dataset_report(env, index.parent, n, property_key),
                args.call_bound + max(30, n * 0.05),
            )
        except Exception as e:
            r["dataset_error"] = str(e)
            if isinstance(e, TimeoutError):
                r["pending_dependency_call"] = True
    if acq is not None and r["teardown_returned"]:
        # await_completion joins acquisition threads but does not close the
        # Python dataset view (acquisition_superclass.py:213-217). Release its
        # TIFF handles too before unlinking stacks, particularly on Windows.
        try:
            bounded(lambda: acq.get_dataset().close(), args.call_bound)
            r["acquisition_reader_closed"] = True
        except BaseException as e:
            r["dataset_close_error"] = f"{type(e).__name__}: {e}"
            if isinstance(e, TimeoutError):
                r["pending_dependency_call"] = True
    r["saved_vs_index"] = [
        dict(
            at=s["at"],
            indexed=s["count"],
            image_saved_count=s["image_saved_count"],
            index_age_s=s.get("index_age_s", 0),
        )
        for s in r["samples"]
        if "count" in s
    ]
    if "dataset" in r:
        for row in r.get("worker_samples", []):
            prior = [sample for sample in r["samples"] if sample["at"] <= row["at"]]
            r["saved_vs_index"].append(
                dict(
                    at=row["at"],
                    indexed=row["count"],
                    image_saved_count=prior[-1]["image_saved_count"] if prior else 0,
                    index_age_s=row["count_age_s"],
                )
            )
        r["saved_vs_index"].append(
            dict(
                at=now(),
                indexed=r["dataset"]["indexed"],
                image_saved_count=saved["count"],
                phase="after teardown",
            )
        )
    if args.probe == "p4" and not reader_hz and not worker:
        r[
            "online_index_comparison"
        ] = "not sampled in no-read control: measuring it would introduce index reads; final counts retained"
    if "abort_at" in r:
        r["abort_to_teardown_s"] = r.get("teardown_return_at", now()) - r["abort_at"]
        ds = r.get("dataset", {})
        cadence = ds.get("cadence")
        if cadence and "imaging_started_at" in r:
            # Acq-start notification is receipt time, not camera time. Report
            # this alignment explicitly; notification delivery latency is unknown.
            abort_ms = (r["abort_at"] - r["imaging_started_at"]) * 1000
            r["stop_overshoot"] = dict(
                alignment="ElapsedTime-ms versus abort relative to received acq_started; notification latency unknown",
                abort_relative_ms=abort_ms,
                last_elapsed_ms=ds["elapsed_ms"][-1],
                overshoot_ms=ds["elapsed_ms"][-1] - abort_ms,
                frames_after_abort=sum(t > abort_ms for t in ds["elapsed_ms"]),
            )
    discard_scored_stacks(r, args.probe, index)
    return r


def run_health(r):
    return (
        r.get("teardown_returned")
        and not r.get("run_error")
        and not r.get("callback_errors")
        and not r.get("imaging_timeout")
        and not r.get("dataset_error")
        and not r.get("dataset_close_error")
        and not r.get("exit_error")
        and not r.get("stack_cleanup_error")
        and r.get("writer_stopped", True)
        and r.get("reader_stopped", True)
        and bool(r.get("dataset", {}).get("readable"))
        and not r.get("dataset", {}).get("failures")
    )


def cadence_score(runs, arms, slow=False):
    controls = [r for r in runs if r["arm"] == "control" and not r.get("guard_only")]
    result = {}
    for arm in arms:
        selected = [r for r in runs if r["arm"] == arm]
        if slow:
            result[arm] = limb(
                "NOT EXERCISED", "demo machine in R157 slow state; rerun later"
            )
        elif (
            not controls
            or not selected
            or any(not r.get("dataset", {}).get("cadence") for r in controls + selected)
        ):
            result[arm] = limb(
                "NOT EXERCISED",
                "ElapsedTime-ms absent or insufficient dataset timestamps",
            )
        else:
            ranges = {
                p: [
                    min(r["dataset"]["cadence"]["gap_ms"][p] for r in controls),
                    max(r["dataset"]["cadence"]["gap_ms"][p] for r in controls),
                ]
                for p in ("p50", "p95")
            }
            ok = all(
                all(
                    ranges[p][0] <= r["dataset"]["cadence"]["gap_ms"][p] <= ranges[p][1]
                    for p in ranges
                )
                and r["dataset"]["planned_minus_indexed"] == 0
                and not r["dataset"]["missing_time_indices"]
                for r in selected
            )
            result[arm] = check(
                ok,
                "every run p50/p95 within control min–max and zero drops",
                control_ranges_ms=ranges,
            )
    return result


def is_slow(runs):
    return any(
        r["arm"] == "control"
        and r["exposure_ms"] == 10
        and r.get("dataset", {}).get("cadence")
        and r["dataset"]["cadence"]["gap_ms"]["p95"] > R157_CONTROL_P95_MS
        for r in runs
    )


def corelog_slice(core, offset):
    path = Path(core.get_primary_log_file())
    with path.open("rb") as f:
        f.seek(offset)
        return f.read().decode("utf8", errors="replace")


def log_score(text, device, prop):
    pattern = re.compile(
        r"\[dbg,Core:dev:"
        + re.escape(device)
        + r'\] Did set property "'
        + re.escape(prop)
        + r'" to "([^"]*)"'
    )
    values = pattern.findall(text)
    return dict(
        set_property_count=len(values),
        values=values,
        camera_sequenced=bool(
            re.search(
                r"startSequenceAcquisition|Did start continuous sequence acquisition|Starting (?:continuous )?sequence acquisition|Started (?:continuous )?sequence acquisition",
                text,
                re.I,
            )
        ),
        sequence_lines=[
            line for line in text.splitlines() if "equence" in line.lower()
        ],
        load_property_sequence=bool(
            re.search(
                r"loadPropertySequence|load property sequence|Loading property sequence",
                text,
                re.I,
            )
        ),
    )


def equivalent(a, b):
    if str(a) == str(b):
        return True
    try:
        return math.isclose(
            float(str(a).replace(",", ".")),
            float(str(b).replace(",", ".")),
            abs_tol=1e-8,
        )
    except (TypeError, ValueError):
        return False


def camera_burst_witness(run):
    counts = run["camera_milestones"]
    if any(counts.values()):
        return counts["pre_sequence_started"] >= 1 and counts["pre_snap"] == 0
    if run["log"]["camera_sequenced"]:
        return True
    return None


def nearest_event(value, wanted):
    if value is None:
        return None
    try:
        number = float(str(value).replace(",", "."))
        distances = [abs(number - float(str(v).replace(",", "."))) for v in wanted]
        if not math.isfinite(number) or not all(math.isfinite(d) for d in distances):
            return None
        best = min(distances)
        matches = [i for i, distance in enumerate(distances) if distance == best]
    except (TypeError, ValueError):
        matches = [i for i, event in enumerate(wanted) if str(value) == str(event)]
    return matches[0] if len(matches) == 1 else None


def score_p3(runs, device, prop):
    plain, burst, spaced = runs
    wanted = spaced["event_values"]
    metadata = spaced.get("dataset", {}).get("metadata_property_values", [])
    nearest = [nearest_event(value, wanted) for value in metadata]
    corelog_ok = spaced["log"]["values"] == wanted
    metadata_ok = nearest == list(range(len(wanted)))
    fired = corelog_ok and metadata_ok
    control = limb(
        "PASS" if fired else "NOT EXERCISED",
        "spaced CoreLog and metadata witnesses fired"
        if fired
        else "spaced control witness broken",
        corelog_witness=check(
            corelog_ok,
            "exact count, strings and order",
            observed=spaced["log"]["values"],
            expected=wanted,
        ),
        metadata_witness=check(
            metadata_ok,
            "frame k nearest event is event k",
            observed=metadata,
            nearest_event_indices=nearest,
        ),
    )
    plain_burst = camera_burst_witness(plain)
    properties_burst = camera_burst_witness(burst)
    plain_limb = limb(
        "PASS" if plain_burst else "NOT EXERCISED",
        "plain control sequenced"
        if plain_burst
        else "plain control did not establish a burst",
        camera_milestones=plain["camera_milestones"],
        corelog_sequence=plain["log"]["camera_sequenced"],
    )
    count = burst["log"]["set_property_count"]
    n = burst["planned"]
    if not plain_burst:
        verdict = limb("NOT EXERCISED", "plain control did not establish a burst")
    elif properties_burst is False:
        verdict = limb("FAIL", "fact 5 refuted: differing properties stopped batching")
    elif properties_burst is None:
        verdict = limb(
            "NOT EXERCISED",
            "neither camera notifications nor CoreLog established properties-burst mode",
        )
    elif not fired:
        verdict = limb("NOT EXERCISED", "spaced control witness broken")
    else:
        verdict = check(
            count < n,
            f"CONFIRMED: applied {count}/{n} values"
            if count < n
            else "fact 5 refuted: every frame value applied",
            applied_values=burst["log"]["values"],
            final_equals_last_event=equivalent(
                burst["final_value"], burst["event_values"][-1]
            ),
        )
    return dict(control_fired=control, burst_was_burst=plain_limb, verdict=verdict)


def growth_score(run):
    rows = run.get("reads", []) or run.get("worker_samples", [])
    indexed = run.get("dataset", {}).get("indexed", 0)
    final_count = rows[-1]["count"] if rows else 0
    start = run.get("imaging_started_at", run["submitted_at"])
    end = run["imaging_observation_end_at"]
    span = end - start
    early = [r["duration_s"] for r in rows if r["at"] <= start + 0.2 * span]
    late = [r["duration_s"] for r in rows if r["at"] >= start + 0.8 * span]
    ages = [s for s in run["samples"] if s.get("index_age_s") is not None]
    if not ages:
        ages = [
            dict(at=s["used_at"], index_age_s=s["count_age_s"])
            for s in rows
            if "used_at" in s
        ]
    numbers = dict(
        final_count=final_count,
        indexed_frames=indexed,
        required_count=math.ceil(indexed * 0.9),
        early_read_duration_s=dist(early),
        late_read_duration_s=dist(late),
        early_count_age_s=dist(
            [s["index_age_s"] for s in ages if s["at"] <= start + 0.2 * span]
        ),
        late_count_age_s=dist(
            [s["index_age_s"] for s in ages if s["at"] >= start + 0.8 * span]
        ),
    )
    if indexed <= 0 or final_count < math.ceil(indexed * 0.9):
        return limb(
            "NOT EXERCISED", "reader final count below 90% of indexed frames", **numbers
        )
    if not early or not late:
        return limb("NOT EXERCISED", "early or late 20% read window empty", **numbers)
    return check(
        percentile(late, 0.5) <= 2 * percentile(early, 0.5),
        "late read p50 <= 2x early read p50",
        **numbers,
    )


def behind_score(run):
    samples = run.get("worker_samples", [])
    comparisons = []
    for w in samples:
        comparisons.append(
            dict(
                at=w["at"],
                worker_last_ordinal=w["worker_last_ordinal"],
                index_last_ordinal=w["last_ordinal"],
                index_observation_at=w["at"],
                lag=w["last_ordinal"] - w["worker_last_ordinal"],
            )
        )
    run["worker_vs_index"] = comparisons
    return check(
        any(x["lag"] > 0 for x in comparisons),
        "behind worker visibly behind the index it read",
        observations=len(comparisons),
    )


def worker_score(run):
    rows = run.get("worker_samples", [])
    indexed = run.get("dataset", {}).get("indexed", 0)
    final_count = rows[-1]["count"] if rows else 0
    lags = [row["last_ordinal"] - row["worker_last_ordinal"] for row in rows]
    following_ok = run["arm"] != "worker" or all(
        0 <= lag <= row["batch"] for lag, row in zip(lags, rows)
    )
    return check(
        run.get("worker_returncode") == 0
        and indexed > 0
        and final_count >= math.ceil(indexed * 0.9)
        and following_ok,
        "worker reached 90% of indexed frames; following worker lag <= batch",
        final_count=final_count,
        indexed_frames=indexed,
        required_count=math.ceil(indexed * 0.9),
        max_lag=max(lags) if lags else None,
        batch=WORKER_BATCH,
    )


def execute(env, args, out):
    report = dict(
        probe=args.probe,
        limbs={},
        runs=[],
        r157_threshold_ms=R157_CONTROL_P95_MS,
        notification_contract=NOTIFICATION_CONTRACT,
    )
    limbs = report["limbs"]
    core = None
    restores = []

    def expect(names):
        for name in names:
            limbs.setdefault(
                name, limb("NOT EXERCISED", "not reached; see execution/run failure")
            )

    if args.probe == "p3":
        expect(["control_fired", "burst_was_burst", "verdict"])
    if args.probe in ("p1", "p2"):
        if args.probe == "p1":
            expect(["cadence/1Hz", "cadence/5Hz"])
        for repeat in range(5 if args.probe == "p2" else 3):
            for arm in ["stop"] if args.probe == "p2" else ["control", "1Hz", "5Hz"]:
                prefix = f"{arm}-{repeat}/"
                expect([prefix + "run"])
                if arm != "control":
                    expect(
                        [
                            prefix + x
                            for x in (
                                "writer_fired",
                                "zero_write_failures",
                                "no_write_after_revoke",
                            )
                        ]
                    )
                if args.probe == "p2":
                    expect(
                        [
                            prefix + x
                            for x in (
                                "dataset_readable",
                                "teardown_bound",
                                "finished_notification",
                            )
                        ]
                    )

    def add_run(name, **kw):
        r = run_one(env, args, out, name, **kw)
        report["runs"].append(r)
        limbs[name + "/run"] = check(
            run_health(r),
            "bounded run, teardown and dataset reopen",
            run_error=r.get("run_error"),
            exit_error=r.get("exit_error"),
            teardown_wait_s=r["teardown_wait_s"],
        )
        if r.get("stack_cleanup_error"):
            raise RuntimeError("image stack cleanup failed; no further acquisitions")
        if (
            not r["teardown_returned"]
            or not r.get("writer_stopped", True)
            or r.get("pending_dependency_call")
        ):
            report["uncontained_dependency_thread"] = True
            limbs[name + "/teardown_bound"] = limb(
                "FAIL", "teardown or writer did not return within bound"
            )
            raise RuntimeError(
                "no further acquisitions: previous teardown/writer still pending; dependent limbs not exercised"
            )
        return r

    try:
        core = bounded(lambda: env.Core(port=args.port))
        space = disk_preflight(core, args, out)
        report["disk_preflight"] = space
        if not space["enough_space"]:
            if args.probe == "p4":
                expect(["fast_exposure", "10ms-control-post/run"])
                for exposure in ("10ms", "fast"):
                    for arm in ("2Hz", "5Hz", "10Hz", "worker", "behind"):
                        expect([f"cadence/{exposure}/{arm}"])
                    for repeat in range(3):
                        for arm in (
                            "control",
                            "2Hz",
                            "5Hz",
                            "10Hz",
                            "worker",
                            "behind",
                        ):
                            prefix = f"{exposure}-{arm}-{repeat}/"
                            expect([prefix + "run"])
                            if arm != "control":
                                expect([prefix + "read_growth"])
                            if arm in ("worker", "behind"):
                                expect([prefix + "worker"])
                            if arm == "behind":
                                expect([prefix + "behind_control"])
            expect(["disk_space"])
            for name in limbs:
                limbs[name] = limb(
                    "NOT EXERCISED",
                    "insufficient free space for the largest run with 1.25 margin",
                    needed_bytes=space["needed_bytes"],
                    free_bytes=space["free_bytes"],
                )
            return report
        entry_exposure = bounded(core.get_exposure)
        restores.append(("exposure", lambda: core.set_exposure(entry_exposure)))
        if args.probe in ("p1", "p2", "p3"):
            disc = bounded(lambda: discovery(core, args.device, args.property))
            report["discovery"] = disc
            count = (6 if args.fake else 50) if args.probe == "p3" else 2
            values, reason = target_values(disc["target"], count)
            if reason:
                names = {
                    "p1": [
                        "writer_fired",
                        "cadence/1Hz",
                        "cadence/5Hz",
                        "zero_write_failures",
                    ],
                    "p2": [
                        "dataset_readable",
                        "no_write_after_revoke",
                        "teardown_bound",
                        "finished_notification",
                    ],
                    "p3": ["control_fired", "burst_was_burst", "verdict"],
                }[args.probe]
                for name in names:
                    limbs[name] = limb("NOT EXERCISED", reason)
                return report
            original = disc["target"]["current"]
            restores.append(
                (
                    "target",
                    lambda: core.set_property(args.device, args.property, original),
                )
            )
        if args.probe == "p3":
            debug = bounded(core.debug_log_enabled)
            restores.append(("debug_log", lambda: core.enable_debug_log(debug)))
            bounded(lambda: core.enable_debug_log(True))
            for name, n, interval in [
                ("burst-plain", 6 if args.fake else 50, 0),
                ("burst", 6 if args.fake else 50, 0),
                ("spaced", 6 if args.fake else 20, 0.05),
            ]:
                vals = values[:n] if name != "burst-plain" else None
                path = Path(bounded(core.get_primary_log_file))
                offset = path.stat().st_size
                r = add_run(name, n=n, interval=interval, values=vals)
                text = bounded(lambda: corelog_slice(core, offset))
                (out / (name + "-corelog.txt")).write_text(text)
                r.update(
                    event_values=vals,
                    corelog_offset=offset,
                    log=log_score(text, args.device, args.property),
                    final_value=bounded(
                        lambda: core.get_property(args.device, args.property)
                    ),
                )
            limbs.update(score_p3(report["runs"], args.device, args.property))
        elif args.probe in ("p1", "p2"):
            arms = [
                ("control", 0),
                ("1Hz", 30 if args.fake else 1),
                ("5Hz", 60 if args.fake else 5),
            ]
            if args.probe == "p2":
                arms = [("stop", 60 if args.fake else 5)]
            for repeat in range(5 if args.probe == "p2" else 3):
                for arm, hz in arms:
                    r = add_run(
                        f"{arm}-{repeat}",
                        n=80 if args.fake else (3000 if args.probe == "p2" else 5000),
                        hz=hz,
                        values=values,
                        abort_after=(0.12 if args.fake else 10)
                        if args.probe == "p2"
                        else None,
                    )
                    r["arm"] = arm
                    if hz:
                        w = r["writer"]
                        prefix = r["name"] + "/"
                        limbs.update(
                            {prefix + k: v for k, v in writer_limbs(w).items()}
                        )
                    if args.probe == "p2":
                        ds = r.get("dataset", {})
                        limbs[r["name"] + "/dataset_readable"] = check(
                            bool(ds.get("indexed"))
                            and ds.get("indexed") == ds.get("readable")
                            and not ds.get("failures"),
                            "index count equals readable image count",
                            indexed=ds.get("indexed"),
                            readable=ds.get("readable"),
                        )
                        limbs[r["name"] + "/teardown_bound"] = check(
                            r["teardown_returned"]
                            and "abort_at" in r
                            and r.get("abort_to_teardown_s", math.inf)
                            <= args.teardown_bound,
                            "abort to teardown within bound",
                            duration_s=r.get("abort_to_teardown_s"),
                            bound_s=args.teardown_bound,
                        )
                        limbs[r["name"] + "/finished_notification"] = check(
                            "acq_events_finished" in r["notifications"],
                            "acquisition-finished observed; order recorded without constraint",
                        )
            if args.probe == "p1":
                limbs.update(
                    {
                        "cadence/" + a: l
                        for a, l in cadence_score(
                            report["runs"], ["1Hz", "5Hz"], is_slow(report["runs"])
                        ).items()
                    }
                )
        else:
            camera = bounded(core.get_camera_device)
            has_limits = bounded(lambda: core.has_property_limits(camera, "Exposure"))
            limits = (
                (
                    True,
                    *bounded(
                        lambda: (
                            core.get_property_lower_limit(camera, "Exposure"),
                            core.get_property_upper_limit(camera, "Exposure"),
                        )
                    ),
                )
                if has_limits
                else (False, None, None)
            )
            report["exposure_limits"] = limits
            fast = next(
                (e for e in (1, 2, 5) if limits[0] and limits[1] <= e <= limits[2]),
                None,
            )
            if fast is None:
                limbs["fast_exposure"] = limb(
                    "NOT EXERCISED",
                    "no exposure in {1,2,5} proven by camera Exposure limits",
                )
            for exposure in [10] + ([fast] if fast is not None else []):
                arms = ["control", "2Hz", "5Hz", "10Hz", "worker", "behind"]
                expect([f"cadence/{exposure}ms/{arm}" for arm in arms[1:]])
                for repeat in range(3):
                    for arm in arms:
                        prefix = f"{exposure}ms-{arm}-{repeat}/"
                        expect([prefix + "run"])
                        if arm != "control":
                            expect([prefix + "read_growth"])
                        if arm in ("worker", "behind"):
                            expect([prefix + "worker"])
                        if arm == "behind":
                            expect([prefix + "behind_control"])
                for repeat in range(3):
                    for arm in arms:
                        reader_hz = int(arm[:-2]) if arm.endswith("Hz") else 0
                        if args.fake and reader_hz:
                            reader_hz *= 40
                        r = add_run(
                            f"{exposure}ms-{arm}-{repeat}",
                            n=160 if args.fake else 3000,
                            exposure=exposure,
                            reader_hz=reader_hz,
                            worker=arm in ("worker", "behind"),
                            behind=arm == "behind",
                        )
                        r["arm"] = arm
                        if reader_hz:
                            limbs[r["name"] + "/read_growth"] = growth_score(r)
                        if arm in ("worker", "behind"):
                            if args.fake:
                                for row in r.get("worker_samples", []):
                                    row["wall_duration_s"] = row["duration_s"]
                                    row["duration_s"] = 0.0001
                                    row[
                                        "duration_clock"
                                    ] = "selftest constant-cost clock"
                            limbs[r["name"] + "/read_growth"] = growth_score(r)
                            result = behind_score(r)
                            if arm == "behind":
                                limbs[r["name"] + "/behind_control"] = result
                            limbs[r["name"] + "/worker"] = worker_score(r)
            if fast is not None:
                post = add_run(
                    "10ms-control-post", n=160 if args.fake else 3000, exposure=10
                )
                post["arm"] = "control"
                post["guard_only"] = True
            for exposure in [10] + ([fast] if fast is not None else []):
                runs = [r for r in report["runs"] if r["exposure_ms"] == exposure]
                limbs.update(
                    {
                        f"cadence/{exposure}ms/{a}": l
                        for a, l in cadence_score(
                            runs, arms[1:], is_slow(report["runs"])
                        ).items()
                    }
                )
    except BaseException as e:
        limbs["execution"] = limb(
            "FAIL", f"{type(e).__name__}: {e}", traceback=traceback.format_exc()
        )
        if isinstance(e, TimeoutError):
            report["uncontained_dependency_thread"] = True
    finally:
        for name, fn in reversed(restores):
            try:
                bounded(fn, args.call_bound)
                limbs["restore/" + name] = check(True, "entry value restored")
            except BaseException as e:
                limbs["restore/" + name] = limb("FAIL", str(e))
                if isinstance(e, TimeoutError):
                    report["uncontained_dependency_thread"] = True
    return report


class JavaVector:
    def __init__(self, items):
        self.items = items

    def size(self):
        return len(self.items)

    def get(self, i):
        return self.items[i]

    def __iter__(self):
        raise TypeError("bridge vector is not Python iterable")


class FakeJavaIndexWriter:
    """IndexWriter from the operator's NDTiffStorage-2.19.3 javap, 2026-10-19.

    Real TIFF pixels/metadata still use ndstorage; only the index file's I/O
    lifecycle is replaced: preallocate, one in-place write per entry, truncate.
    """

    def __init__(self, path):
        self.file = Path(path).open("w+b", buffering=0)
        self.file.truncate(JAVA_INDEX_PREALLOCATED_BYTES)
        self.file.seek(0)

    def write(self, entry):
        written = self.file.write(entry)
        if written != len(entry):
            raise OSError("fake Java index entry write was incomplete")
        return written

    def flush(self):
        self.file.flush()

    def close(self):
        if not self.file.closed:
            self.file.truncate(self.file.tell())
            self.file.close()


class FakeEnvironment:
    """Source-shaped acquisition dispatch, real TIFFs and Java-shaped index I/O.

    Deliberately defer saved callbacks and events-finished until backend exit.
    This is a possible schedule of the separate dependency threads, not a claimed
    universal ordering. Frame generation starts on acquire, as on the Java side.
    """

    def __init__(self, path, mutation=None):
        from ndstorage import NDTiffDataset
        from ndstorage.ndtiff_file import SingleNDTiffWriter

        self.Dataset = NDTiffDataset
        self.path = Path(path)
        self.log = self.path / "CoreLog.txt"
        self.log.touch()
        self.mutation = mutation
        self.value = "0.5" if mutation == "rounded_metadata" else "17"
        self.entry_value = self.value
        self.debug = False
        self.exposure = 7
        self.writes = []
        self.cores = []
        self.dispatched_milestones = []
        self.acquisition_constructions = 0
        env = self

        class Core:
            def __init__(self, port=4827, new_socket=False):
                self.port = port
                self.new_socket = new_socket
                env.cores.append(self)

            def get_camera_device(self):
                return "Camera"

            def get_image_width(self):
                return 4

            def get_image_height(self):
                return 4

            def get_bytes_per_pixel(self):
                return 2

            def get_device_property_names(self, d):
                return JavaVector(
                    ["TestProperty1", "Exposure", "SimulateCrash", "ReadOnly"]
                )

            def has_property(self, d, p):
                return p in self.get_device_property_names(d).items

            def is_property_read_only(self, d, p):
                return p == "ReadOnly" or env.mutation == "readonly"

            def is_property_pre_init(self, d, p):
                return False

            def has_property_limits(self, d, p):
                return p in ("Exposure", "TestProperty1")

            def get_property_lower_limit(self, d, p):
                if env.mutation == "rounded_metadata" and p == "TestProperty1":
                    return 0.123456
                return 0 if p == "TestProperty1" else 1

            def get_property_upper_limit(self, d, p):
                return (
                    1.23456
                    if env.mutation == "rounded_metadata" and p == "TestProperty1"
                    else 100
                )

            def get_allowed_property_values(self, d, p):
                return JavaVector([])

            def is_property_sequenceable(self, d, p):
                return False

            def get_property(self, d, p):
                return str(env.exposure) if p == "Exposure" else env.value

            def set_property(self, d, p, v):
                assert (
                    p not in ("SimulateCrash", "ReadOnly")
                    and env.mutation != "readonly"
                )
                env.value = str(v)
                env.writes.append((d, p, str(v)))
                if env.debug:
                    with env.log.open("a") as f:
                        f.write(
                            f'2026-10-18T10:00:00.000001 tid1 [dbg,Core:dev:{d}] Will set property "{p}" to "{v}"\n'
                        )
                        f.write(
                            f'2026-10-18T10:00:00.000002 tid1 [dbg,Core:dev:{d}] Did set property "{p}" to "{v}"\n'
                        )

            def get_exposure(self):
                return env.exposure

            def set_exposure(self, v):
                env.exposure = v

            def debug_log_enabled(self):
                return env.debug

            def enable_debug_log(self, v):
                env.debug = v

            def get_primary_log_file(self):
                return str(env.log)

        self.Core = Core

        class Backend:
            def __init__(self, **kw):
                env.acquisition_constructions += 1
                self.kw = kw
                self.stop = threading.Event()
                self.done = threading.Event()
                self.error = None
                self.axes = []
                self.ds = NDTiffDataset(kw["directory"], name=kw["name"], writable=True)
                self.ds.initialize({})
                self.ds.current_writer = SingleNDTiffWriter(
                    self.ds.path, self.ds.name + "_NDTiffStack.tif", {}
                )
                self.ds.file_index = 1
                self.ds._index_file = FakeJavaIndexWriter(
                    Path(self.ds.path) / "NDTiff.index"
                )
                self.notification_queue = queue.Queue()
                self.dispatcher_done = threading.Event()

                def dispatch():
                    events_finished = False
                    sink_finished = False
                    try:
                        while True:
                            note = self.notification_queue.get(timeout=5)
                            if note.is_acquisition_finished_notification():
                                events_finished = True
                            elif note.is_data_sink_finished_notification():
                                sink_finished = True
                            # Source shape: deliberately no catch around callback.
                            self.kw["notification_callback_fn"](note)
                            env.dispatched_milestones.append(note.milestone)
                            if events_finished and sink_finished:
                                break
                    finally:
                        self.dispatcher_done.set()

                threading.Thread(target=dispatch, daemon=True).start()

            def __enter__(self):
                return self

            def get_dataset(self):
                return self.ds

            def acquire(self, events):
                from pycromanager.acquisition.acq_eng_py.main.acq_notification import (
                    AcqNotification as N,
                )
                import numpy as np

                def produce():
                    try:
                        self.notification_queue.put(N.create_acq_started_notification())
                        if env.mutation == "callback_error":

                            class BadNotification:
                                milestone = "bad_notification"

                                @property
                                def payload(self):
                                    raise RuntimeError(
                                        "injected notification processing failure"
                                    )

                                def is_acquisition_finished_notification(self):
                                    return False

                                def is_data_sink_finished_notification(self):
                                    return False

                            self.notification_queue.put(BadNotification())
                        burst = "min_start_time" not in events[-1]
                        if (
                            env.mutation == "properties_stop_batching"
                            and "properties" in events[-1]
                        ):
                            burst = False
                        if burst:
                            if env.mutation != "no_sequence_log":
                                with env.log.open("a") as f:
                                    f.write(
                                        "2026-10-18T10:00:00.000002 tid1 [dbg,Core] Did start continuous sequence acquisition\n"
                                    )
                            else:
                                with env.log.open("a") as f:
                                    f.write(
                                        "2026-10-18T10:00:00.000002 tid1 [dbg,Core] sequence diagnostic without recognized start grammar\n"
                                    )
                            self.notification_queue.put(
                                N(N.Camera, {}, N.Camera.PRE_SEQUENCE_STARTED)
                            )
                        for k, e in enumerate(events):
                            if self.stop.wait(0.004):
                                break
                            if not burst:
                                self.notification_queue.put(
                                    N(N.Camera, e["axes"], N.Camera.PRE_SNAP)
                                )
                            if "properties" in e and (
                                not burst or env.mutation == "refuted"
                            ):
                                if not (not burst and env.mutation == "no_spaced_log"):
                                    for d, p, v in e["properties"]:
                                        Core().set_property(d, p, v)
                                else:
                                    env.value = e["properties"][0][2]
                            gap = (
                                21
                                if env.mutation == "slow"
                                or (
                                    env.mutation == "post_slow"
                                    and "10ms-control-post" in self.kw["directory"]
                                )
                                else 12
                            )
                            value = (
                                format(float(env.value), ".4g")
                                if env.mutation == "rounded_metadata"
                                else env.value
                            )
                            md = {
                                "ElapsedTime-ms": k * gap,
                                "Camera-TestProperty1": value,
                            }
                            if env.mutation == "missing_elapsed":
                                md.pop("ElapsedTime-ms")
                            self.ds.put_image(
                                e["axes"], np.zeros((4, 4), np.uint16), md
                            )
                            self.ds._index_file.flush()
                            self.axes.append(e["axes"])
                            if not burst:
                                self.notification_queue.put(
                                    N(N.Camera, e["axes"], N.Camera.POST_SNAP)
                                )
                        if burst:
                            self.notification_queue.put(
                                N(N.Camera, {}, N.Camera.POST_SEQUENCE_STOPPED)
                            )
                    except BaseException as e:
                        self.error = e
                    finally:
                        self.ds.finish()
                        self.done.set()

                threading.Thread(target=produce, daemon=True).start()

            def abort(self):
                self.stop.set()

            def __exit__(self, *exc):
                from pycromanager.acquisition.acq_eng_py.main.acq_notification import (
                    AcqNotification as N,
                )

                if not self.done.wait(5):
                    raise RuntimeError("fake producer hung")
                if self.error:
                    raise self.error
                self.notification_queue.put(N.create_acq_events_finished_notification())
                if env.mutation == "image_callback_error":

                    class BadAxes:
                        def __str__(self):
                            raise RuntimeError(
                                "injected image callback processing failure"
                            )

                    self.kw["image_saved_fn"](BadAxes(), self.ds)
                for a in self.axes:
                    self.kw["image_saved_fn"](a, self.ds)
                    self.notification_queue.put(N.create_image_saved_notification(a))
                self.notification_queue.put(N.create_data_sink_finished_notification())
                if not self.dispatcher_done.wait(5):
                    raise RuntimeError("fake notification dispatcher did not finish")

        class Acquisition:
            def __new__(cls, **kw):
                return Backend(**kw)

            def __init__(self, **kw):
                raise AssertionError("dispatcher init must not run")

        self.Acquisition = Acquisition


def selftest():
    with tempfile.TemporaryDirectory(prefix="probe89-selftest-") as temp:
        root = Path(temp)
        reports = {}

        def run(probe, mutation=None, prop="TestProperty1"):
            folder = root / (probe + "-" + str(mutation) + "-" + prop)
            folder.mkdir()
            args = parser().parse_args([probe, "--out", str(folder)])
            args.fake = True
            args.property = prop
            args.start_bound = 2
            args.run_bound = 3
            args.call_bound = 3
            args.teardown_bound = 3
            env = FakeEnvironment(folder, mutation)
            from unittest.mock import patch

            usage = shutil.disk_usage(folder)
            disk_control = (
                patch.object(shutil, "disk_usage", return_value=usage._replace(free=0))
                if mutation == "low_disk"
                else contextlib.nullcontext()
            )
            with disk_control, open(
                os.devnull, "w"
            ) as sink, contextlib.redirect_stdout(sink):
                report = execute(env, args, folder)
            (folder / "report.json").write_text(json.dumps(report, indent=2))
            assert (
                env.value == env.entry_value
                and env.debug is False
                and env.exposure == 7
            ), "restoration"
            return report, env

        # Cheapest design first, then every entire command including real workers.
        for probe in ("p3", "p1", "p2", "p4"):
            report, env = run(probe)
            reports[probe] = report
            failed = {k: v for k, v in report["limbs"].items() if v["status"] != "PASS"}
            assert not failed, (probe, failed)
            if probe in ("p1", "p2"):
                assert any(
                    c.new_socket for c in env.cores
                ), "writer own Core connection"
            print(f'ok end-to-end: {probe} ({len(report["runs"])} runs)')
            assert report["disk_preflight"]["enough_space"]
            assert report["disk_preflight"]["frame_bytes"] == 4 * 4 * 2
        for probe in ("p1", "p2", "p3", "p4"):
            r, env = run(probe, "low_disk")
            assert env.acquisition_constructions == 0 and not r["runs"]
            space = r["disk_preflight"]
            assert space["needed_bytes"] == math.ceil(
                space["frame_bytes"] * space["largest_run_frames"] * 1.25
            )
            assert space["free_bytes"] < space["needed_bytes"]
            assert r["limbs"] and all(
                item["status"] == "NOT EXERCISED"
                and item["needed_bytes"] == space["needed_bytes"]
                and item["free_bytes"] == space["free_bytes"]
                for item in r["limbs"].values()
            )
        print(
            "ok control: insufficient disk space -> every limb NOT EXERCISED; zero acquisition constructions"
        )
        for arm in reports["p1"]["runs"]:
            dataset_folder = Path(arm["index_path"]).parent
            assert not list(dataset_folder.rglob("*.tif"))
            assert (dataset_folder / "NDTiff.index").is_file()
            assert Path(arm["callback_stream"]).is_file()
            assert arm["acquisition_reader_closed"]
            assert arm["deleted_bytes"] > 0 and arm["deleted_files"]
        assert all(
            list(Path(arm["index_path"]).parent.rglob("*.tif"))
            and arm["deleted_bytes"] == 0
            for arm in reports["p3"]["runs"]
        )
        print(
            "ok control: healthy P1 removes image stacks and keeps index/logs/scoring fields; P3 keeps stacks"
        )
        r, _ = run("p3", "refuted")
        assert (
            r["limbs"]["verdict"]["status"] == "FAIL"
            and "refuted" in r["limbs"]["verdict"]["reason"]
        )
        print("ok control: P3 every burst value applied -> FAIL fact 5 refuted")
        r, _ = run("p3", "properties_stop_batching")
        assert r["limbs"]["burst_was_burst"]["status"] == "PASS"
        assert r["limbs"]["verdict"]["status"] == "FAIL"
        assert (
            r["limbs"]["verdict"]["reason"]
            == "fact 5 refuted: differing properties stopped batching"
        )
        print(
            "ok control: P3 differing properties stopped batching -> FAIL fact 5 refuted"
        )
        r, _ = run("p3", "no_sequence_log")
        assert r["limbs"]["verdict"]["status"] == "PASS"
        assert all(not arm["log"]["camera_sequenced"] for arm in r["runs"])
        assert r["runs"][0]["camera_milestones"]["pre_sequence_started"] == 1
        assert r["runs"][0]["log"]["sequence_lines"]
        print(
            "ok control: P3 camera notifications establish bursts without recognized CoreLog sequence lines"
        )
        r, _ = run("p3", "rounded_metadata")
        assert r["limbs"]["control_fired"]["status"] == "PASS"
        control = r["limbs"]["control_fired"]
        assert (
            control["corelog_witness"]["status"]
            == control["metadata_witness"]["status"]
            == "PASS"
        )
        spaced = r["runs"][-1]
        assert spaced["event_values"] != spaced["dataset"]["metadata_property_values"]
        assert any(
            float(sent) != float(observed)
            for sent, observed in zip(
                spaced["event_values"], spaced["dataset"]["metadata_property_values"]
            )
        )
        assert nearest_event("0.06", ["0", "0.03", "0.06"]) == 2
        print(
            "ok control: P3 device metadata rounded to 4 significant digits -> both control witnesses PASS"
        )
        r, env = run("p1", "callback_error")
        assert all(arm["callback_errors"] and not run_health(arm) for arm in r["runs"])
        assert "bad_notification" in env.dispatched_milestones
        assert env.dispatched_milestones[-1] == "data_sink_finished"
        assert all(
            arm["notifications"]["image_saved"]["count"] == arm["dataset"]["indexed"]
            for arm in r["runs"]
        )
        print(
            "ok control: notification processing raises -> unhealthy run; fake dispatcher delivers later notifications"
        )
        r, _ = run("p2", "image_callback_error")
        assert all(not run_health(arm) for arm in r["runs"])
        assert all(
            arm["callback_errors"][0]["callback"] == "image_saved"
            and arm["image_saved"]["count"] == arm["dataset"]["indexed"]
            for arm in r["runs"]
        )
        print(
            "ok control: image callback processing raises -> unhealthy run; later images still delivered"
        )
        r, _ = run("p3", "no_spaced_log")
        assert r["limbs"]["verdict"]["status"] == "NOT EXERCISED"
        print("ok control: P3 missing spaced setProperty witness -> NOT EXERCISED")
        r, _ = run("p1", "slow")
        assert all(
            v["status"] == "NOT EXERCISED"
            for k, v in r["limbs"].items()
            if k.startswith("cadence/")
        )
        print("ok control: 21 ms slow control -> cadence NOT EXERCISED")
        r, _ = run("p4", "post_slow")
        post = r["runs"][-1]
        assert post["name"] == "10ms-control-post" and post["guard_only"]
        assert all(
            v["status"] == "NOT EXERCISED"
            for k, v in r["limbs"].items()
            if k.startswith("cadence/")
        )
        assert not is_slow(r["runs"][:-1]) and is_slow(r["runs"])
        ten_ms = [arm for arm in r["runs"] if arm["exposure_ms"] == 10]
        rescored = cadence_score(ten_ms, ["2Hz"])["2Hz"]
        assert rescored["control_ranges_ms"]["p95"] == [12, 12]
        print(
            "ok control: slow 10ms-control-post guards fast exposure; excluded from acceptance ranges"
        )
        r, _ = run("p1", "missing_elapsed")
        assert r["limbs"]["cadence/1Hz"]["status"] == "NOT EXERCISED"
        print("ok control: missing ElapsedTime-ms -> NOT EXERCISED")
        r = copy.deepcopy(reports["p2"]["runs"][0])
        w = r["writer"]
        injected = dict(start=w["revoked_at"] + 0.001, duration_s=0)
        w["writes"].append(injected)
        assert writer_limbs(w)["no_write_after_revoke"]["status"] == "FAIL"
        print("ok control: write started after revoke -> FAIL")
        # Actual byte format from dependency serializer; split every byte boundary.
        from ndstorage.ndtiff_index import NDTiffIndexEntry

        entry = (
            NDTiffIndexEntry(
                frozenset({"time": 0}.items()),
                1,
                100,
                4,
                4,
                200,
                5,
                "data_NDTiffStack.tif",
            )
            .as_byte_buffer()
            .getvalue()
        )
        path = root / "partial.index"
        for cut in range(1, len(entry)):
            path.write_bytes(entry[:cut])
            rd = IndexReader(path)
            assert rd.read()["count"] == 0
            with path.open("ab") as f:
                f.write(entry[cut:])
            assert rd.read()["count"] == 1 and rd.read()["count"] == 1
        print(
            "ok control: partial final index entry never counted; every split resumes exactly once"
        )
        print(
            "ok control: Python append layout remains readable across every partial-entry split"
        )
        writer = FakeJavaIndexWriter(path)
        rd = IndexReader(path)
        first = rd.read()
        assert path.stat().st_size == JAVA_INDEX_PREALLOCATED_BYTES
        assert first["count"] == 0 and first["parsed_offset"] == 0
        assert first["bytes_read"] == INDEX_CHUNK_BYTES
        writer.write(entry)
        row = rd.read()
        assert row["count"] == 1 and row["parsed_offset"] == len(entry)
        assert row["bytes_read"] <= len(entry) + INDEX_CHUNK_BYTES
        writer.close()
        assert path.stat().st_size == len(entry)
        assert rd.read()["count"] == 1
        print(
            "ok control: Java zero-filled preallocation resumes in place; 64 KiB reads and parsed_offset recorded"
        )
        # Leave metadata_length unwritten so the zero-filled trailer is invalid.
        # Zero compression padding itself cannot distinguish partial from complete.
        for cut in (3, 8, len(entry) - 8):
            writer = FakeJavaIndexWriter(path)
            writer.file.write(entry[:cut])
            rd = IndexReader(path)
            row = rd.read()
            assert row["count"] == row["parsed_offset"] == 0
            writer.file.write(entry[cut:])
            assert rd.read()["count"] == 1
            assert rd.read()["count"] == 1
            writer.close()
        print(
            "ok control: partial entry inside preallocated zeros is re-read and counted exactly once after completion"
        )
        axes = json.dumps({"time": 0}).encode("utf8")
        filename = b"data_NDTiffStack.tif"
        trailer = [100, 4, 4, 1, 0, 200, 5, 0]

        def index_bytes(axes_bytes, filename_bytes, fields):
            return (
                struct.pack("I", len(axes_bytes))
                + axes_bytes
                + struct.pack("I", len(filename_bytes))
                + filename_bytes
                + struct.pack("IIIIIIII", *fields)
            )

        invalid = [
            index_bytes(b"{", filename, trailer),
            index_bytes(b"[]", filename, trailer),
            index_bytes(axes, b"", trailer),
            index_bytes(axes, b"\xff", trailer),
        ]
        for field in (1, 2, 6):
            fields = list(trailer)
            fields[field] = 0
            invalid.append(index_bytes(axes, filename, fields))
        for bad in invalid:
            writer = FakeJavaIndexWriter(path)
            writer.write(bad)
            rd = IndexReader(path)
            row = rd.read()
            assert row["count"] == row["parsed_offset"] == 0
            writer.file.seek(0)
            writer.write(entry)
            assert rd.read()["count"] == 1
            writer.close()
        print(
            "ok control: invalid axes/filename/trailer fields do not advance parsed_offset; in-place repair is observed"
        )
        large = index_bytes(
            json.dumps({"time": 1, "channel": "x" * INDEX_CHUNK_BYTES}).encode("utf8"),
            filename,
            trailer,
        )
        writer = FakeJavaIndexWriter(path)
        writer.write(large)
        rd = IndexReader(path)
        assert rd.read()["count"] == 1 and rd.parsed_offset == len(large)
        writer.close()
        path.write_bytes(entry + entry)
        rd = IndexReader(path)
        assert rd.read()["count"] == 2
        path.write_bytes(entry[:8])
        assert rd.read()["count"] == 0
        with path.open("ab") as f:
            f.write(entry[8:])
        assert rd.read()["count"] == 1
        print(
            "ok control: multi-chunk entries and truncated/appended files remain readable"
        )

        class OldOffsetReader(IndexReader):
            """The pre-revision reader, intentionally preserving its file-size bug."""

            def __init__(self, path):
                super().__init__(path)
                self.offset = 0
                self.tail = b""

            def read(self):
                started = now()
                with self.path.open("rb") as f:
                    f.seek(self.offset)
                    data = f.read()
                self.offset += len(data)
                buffer = self.tail + data
                p = 0
                while len(buffer) - p >= 4:
                    length = struct.unpack_from("I", buffer, p)[0]
                    if length == 0:
                        break
                    q = p + 4 + length
                    if len(buffer) < q + 4:
                        break
                    filename_length = struct.unpack_from("I", buffer, q)[0]
                    end = q + 4 + filename_length + 32
                    if len(buffer) < end:
                        break
                    axes = json.loads(buffer[p + 4 : q].decode("utf8"))
                    self.count += 1
                    self.last = axes.get("time", self.count - 1)
                    p = end
                self.tail = buffer[p:]
                at = now()
                return dict(
                    at=at,
                    duration_s=at - started,
                    count=self.count,
                    last_ordinal=self.last,
                    bytes_read=len(data),
                )

        folder = root / "old-offset-java"
        folder.mkdir()
        env = FakeEnvironment(folder)
        acq = env.Acquisition(
            directory=str(folder),
            name="data",
            show_display=False,
            notification_callback_fn=lambda note: None,
            image_saved_fn=lambda axes, dataset: None,
        )
        index_path = Path(acq.get_dataset().path) / "NDTiff.index"
        old = OldOffsetReader(index_path)
        first = old.read()
        assert (
            first["bytes_read"] == JAVA_INDEX_PREALLOCATED_BYTES and first["count"] == 0
        )
        acq.acquire([dict(axes={"time": k}, exposure=10) for k in range(160)])
        bounded(lambda: acq.__exit__(None, None, None), 3)
        last = old.read()
        assert last["bytes_read"] == last["count"] == 0
        with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink):
            dataset = dataset_report(env, index_path.parent, 160)
        acq.get_dataset().close()
        mutation = dict(
            arm="behind",
            submitted_at=first["at"],
            imaging_started_at=first["at"],
            imaging_observation_end_at=last["at"],
            reads=[first, last],
            samples=[],
            dataset=dataset,
            worker_returncode=0,
            worker_samples=[
                dict(row, worker_last_ordinal=row["last_ordinal"], batch=WORKER_BATCH)
                for row in (first, last)
            ],
        )
        assert growth_score(mutation)["status"] == "NOT EXERCISED"
        assert worker_score(mutation)["status"] == "FAIL"
        assert behind_score(mutation)["status"] == "FAIL"
        print(
            "ok control: old file-size offset reader on Java preallocation -> read_growth NOT EXERCISED, worker FAIL, behind_control FAIL"
        )
        following = copy.deepcopy(
            next(r for r in reports["p4"]["runs"] if r["arm"] == "worker")
        )
        last = following["worker_samples"][-1]
        last["worker_last_ordinal"] = last["last_ordinal"] - last["batch"] - 1
        assert worker_score(following)["status"] == "FAIL"
        print("ok control: following worker max lag exceeds its batch -> worker FAIL")
        r = copy.deepcopy(
            next(r for r in reports["p4"]["runs"] if r["arm"] == "behind")
        )
        for s in r["worker_samples"]:
            s["worker_last_ordinal"] = 100000
        assert behind_score(r)["status"] == "FAIL"
        print("ok control: worker not behind -> behind-worker FAIL")
        # Only the explicitly selected reader arm may call IndexReader in this
        # process. Real worker subprocesses have their own reader and log.
        original_read = IndexReader.read

        def forbidden_read(self):
            raise AssertionError("unexpected probe-side index read")

        IndexReader.read = forbidden_read
        try:
            for probe in ("p1", "p2"):
                r, _ = run(probe, "no_probe_reads")
                assert all(run_health(arm) for arm in r["runs"])
            folder = root / "worker-no-probe-reads"
            folder.mkdir()
            args = parser().parse_args(["p4"])
            args.fake = True
            args.run_bound = 3
            args.start_bound = 2
            env = FakeEnvironment(folder)
            with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink):
                arm = run_one(
                    env, args, folder, "worker", 160, worker=True, behind=True
                )
            assert run_health(arm) and behind_score(arm)["status"] == "PASS"
        finally:
            IndexReader.read = original_read
        print(
            "ok control: P1/P2 and P4 worker arm pass with probe-side index reads forbidden"
        )
        compact = reports["p4"]
        for arm in compact["runs"]:
            assert isinstance(arm["notifications"], dict)
            assert isinstance(arm["image_saved"], dict)
            assert "frames" not in arm["dataset"]
            assert len(arm["dataset"]["elapsed_ms"]) == arm["dataset"]["indexed"]
            assert "metadata_property_values" not in arm["dataset"]
            assert arm["dataset"]["first_frame_metadata"]
            raw = [
                json.loads(line)
                for line in Path(arm["callback_stream"]).read_text().splitlines()
            ]
            assert (
                sum(row["kind"] == "image_saved" for row in raw)
                == arm["image_saved"]["count"]
            )
        assert len(json.dumps(compact)) < 2_000_000
        print(
            "ok control: compact report keeps summaries, elapsed times and first metadata; raw image callbacks only in JSONL"
        )
        r = copy.deepcopy(next(r for r in reports["p4"]["runs"] if r["arm"] == "2Hz"))
        midpoint = (r["imaging_started_at"] + r["imaging_observation_end_at"]) / 2
        for row in r["reads"]:
            if row["at"] > midpoint:
                row["duration_s"] *= 3
        assert growth_score(r)["status"] == "FAIL"
        print(
            "ok control: late read cost grows 3x -> read-growth FAIL (selftest clock; real parser and workers)"
        )
        for mutation, prop in [("readonly", "TestProperty1"), (None, "SimulateCrash")]:
            r, env = run("p3", mutation, prop)
            assert not env.writes and r["limbs"]["verdict"]["status"] == "NOT EXERCISED"
            print(f"ok control: {mutation or prop} target never written")
        # Exit exception and hung teardown exercise the actual foreground deadline.
        base = FakeEnvironment(root)
        original = base.Acquisition
        hang_release = threading.Event()
        hang_done = threading.Event()
        hung_objects = []

        def hang_factory(**kw):
            obj = original(**kw)
            real_exit = type(obj).__exit__

            def hung_exit(self, *exc):
                try:
                    real_exit(self, *exc)
                    hang_release.wait(10)
                finally:
                    hang_done.set()

            obj.__class__ = type(
                "HungBackend",
                (type(obj),),
                {"__exit__": hung_exit},
            )
            hung_objects.append(obj)
            return obj

        base.Acquisition = hang_factory
        args = parser().parse_args(["p1"])
        args.fake = True
        args.start_bound = 1
        args.run_bound = 1
        args.call_bound = 1
        args.teardown_bound = 0.03
        r = run_one(base, args, root, "hung", 3)
        assert not r["teardown_returned"] and not run_health(r)
        print("ok control: hung teardown returns bounded FAIL via finally Event")
        assert list(Path(r["index_path"]).parent.rglob("*.tif"))
        assert r["deleted_bytes"] == 0 and not r["deleted_files"]
        assert "teardown did not return" in r["stack_cleanup"]
        print(
            "ok control: hung teardown keeps image stacks as evidence; zero deleted bytes"
        )
        hang_release.set()
        assert hang_done.wait(1)
        hung_objects[0].get_dataset().close()
        for p in ("p1", "p2", "p3", "p4"):
            launcher = Path(__file__).with_name(f"89-probe-{p}.ps1").read_text()
            assert len(launcher.strip().splitlines()) == 1
            assert "--property" not in launcher and "TestProperty" not in launcher
            assert (
                "placeholder" not in launcher.lower()
                and "-I" in launcher
                and "active-slot.txt" in launcher
            )
        print(
            "ok control: launchers use installed slot, -I, no property override or placeholder"
        )
        import ast
        import io
        import tokenize

        source = Path(__file__).read_text()
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        assert not any(
            token.type == tokenize.OP and token.string == ";" for token in tokens
        )
        statement_lines = [
            node.lineno
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.stmt)
        ]
        assert len(statement_lines) == len(set(statement_lines))
        print(
            "ok control: readable source has one statement per line and no semicolon chains"
        )
        assert exit_code({"limbs": {"a": limb("NOT EXERCISED", "x")}}) == 2
        assert (
            exit_code(
                {"limbs": {"a": limb("FAIL", "x"), "b": limb("NOT EXERCISED", "x")}}
            )
            == 1
        )
    print("SELFTEST PASSED")


class Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, text):
        for s in self.streams:
            s.write(text)
            s.flush()

    def flush(self):
        for s in self.streams:
            s.flush()


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("probe", nargs="?", choices=["p1", "p2", "p3", "p4"])
    p.add_argument("--selftest", action="store_true")
    p.add_argument("--out", type=Path)
    p.add_argument("--port", type=int, default=4827)
    p.add_argument("--device", default="Camera")
    p.add_argument("--property", default="TestProperty1")
    p.add_argument("--start-bound", type=float, default=30)
    p.add_argument("--run-bound", type=float, default=180)
    p.add_argument("--call-bound", type=float, default=CALL_BOUND_S)
    p.add_argument("--teardown-bound", type=float, default=TEARDOWN_BOUND_S)
    p.add_argument("--worker-index")
    p.add_argument("--worker-log")
    p.add_argument("--worker-stop")
    p.add_argument("--worker-bound", type=float, default=180)
    p.add_argument("--behind", action="store_true")
    p.add_argument("--worker-delay", type=float, default=0.15)
    p.set_defaults(fake=False)
    return p


def main():
    args = parser().parse_args()
    if args.worker_index:
        worker_main(args)
        return 0
    if args.selftest:
        selftest()
        return 0
    if not args.probe or args.out is None:
        parser().error("probe and --out are required")
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=True)
    with (args.out / "probe.log").open("w", buffering=1) as logfile:
        with contextlib.redirect_stdout(
            Tee(sys.stdout, logfile)
        ), contextlib.redirect_stderr(Tee(sys.stderr, logfile)):
            print(f"89a {args.probe}; output {args.out}; port {args.port}", flush=True)
            try:
                from types import SimpleNamespace
                from pycromanager import Core, Acquisition
                from ndstorage import NDTiffDataset

                report = execute(
                    SimpleNamespace(
                        Core=Core, Acquisition=Acquisition, Dataset=NDTiffDataset
                    ),
                    args,
                    args.out,
                )
            except BaseException as e:
                report = dict(
                    probe=args.probe,
                    runs=[],
                    limbs={
                        "startup": limb(
                            "FAIL", str(e), traceback=traceback.format_exc()
                        )
                    },
                )
            (args.out / "report.json").write_text(
                json.dumps(report, indent=2, default=str)
            )
            for name, result in report["limbs"].items():
                print(name, result["status"], result["reason"])
            code = exit_code(report)
            print(f'exit {code}; report: {args.out / "report.json"}')
            if report.get("uncontained_dependency_thread"):
                # pycro-manager itself owns non-daemon threads. sys.exit would
                # wait for those indefinitely after our bounded foreground exit.
                sys.stdout.flush()
                sys.stderr.flush()
                logfile.flush()
                os._exit(code)
            return code


if __name__ == "__main__":
    raise SystemExit(main())
