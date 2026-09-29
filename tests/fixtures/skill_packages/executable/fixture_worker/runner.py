"""Reference v1 worker: self-check and lifecycle observation, without hardware."""
import hashlib
import json
import io
import os
import struct
import threading
import time
from pathlib import Path
import sys

PROTOCOL = "microclaw.analysis.v1"


# Layout follows ndstorage.ndtiff_index.NDTiffIndexEntry; native uint32 fields.
# Pixel sizes follow ndstorage.ndtiff_file.SingleNDTiffReader.read_image.
POLL_INTERVAL_S = 0.03  # Responsive tailing without busy-waiting on the writer.


def index_path(dataset):
    # ndstorage._superclass.Dataset.__new__: prefer Full resolution when present.
    full = dataset / "Full resolution"
    return (full if full.is_dir() else dataset) / "NDTiff.index"


def entries(stream):
    """Yield complete records and their end offsets; leave partial tails for retry."""
    start = stream.tell()
    limit = stream.seek(0, os.SEEK_END)
    stream.seek(start)
    while True:
        raw = stream.read(4)
        if len(raw) != 4:
            return
        axes_size, = struct.unpack("I", raw)
        if axes_size == 0:  # ndstorage's termination sentinel
            return
        if stream.tell() + axes_size + 4 > limit:
            return
        axes = stream.read(axes_size)
        raw = stream.read(4)
        if len(axes) != axes_size or len(raw) != 4:
            return
        name_size, = struct.unpack("I", raw)
        if stream.tell() + name_size + 32 > limit:
            return
        name = stream.read(name_size)
        fields = stream.read(32)
        if len(name) != name_size or len(fields) != 32:
            return
        yield stream.tell(), axes, name, struct.unpack("IIIIIIII", fields)


def priority(requested):
    evidence = dict(requested=requested, applied=False, read_back=None, reason=None)
    try:
        if os.name == "nt":
            import ctypes as c
            from ctypes import wintypes as w
            kernel = c.WinDLL("kernel32", use_last_error=True)
            kernel.GetCurrentProcess.restype = w.HANDLE
            kernel.SetPriorityClass.argtypes = [w.HANDLE, w.DWORD]
            kernel.SetPriorityClass.restype = w.BOOL
            kernel.GetPriorityClass.argtypes = [w.HANDLE]
            kernel.GetPriorityClass.restype = w.DWORD
            process = kernel.GetCurrentProcess()
            before = kernel.GetPriorityClass(process)  # inherited; not always NORMAL
            if requested == "below_normal" and not kernel.SetPriorityClass(process, 0x4000):
                evidence["reason"] = str(c.WinError(c.get_last_error()))
            value = kernel.GetPriorityClass(process)
            if not value:
                raise c.WinError(c.get_last_error())
            evidence["read_back"] = value
            evidence["applied"] = value == (0x4000 if requested == "below_normal" else before)
        else:
            before = os.getpriority(os.PRIO_PROCESS, 0)
            if requested == "below_normal":
                try:
                    os.nice(10)
                except OSError as exc:
                    evidence["reason"] = str(exc)
            value = os.getpriority(os.PRIO_PROCESS, 0)
            evidence["read_back"] = value
            evidence["applied"] = value == (min(19, before + 10) if requested == "below_normal" else before)
        if not evidence["applied"] and evidence["reason"] is None:
            evidence["reason"] = "priority read-back did not match"
    except (OSError, AttributeError) as exc:
        evidence["reason"] = str(exc)
    return evidence


class Observer:
    def __init__(self, dataset, emit):
        self.dataset, self.emit = Path(dataset), emit
        self.offset = 0
        self.last_status = None
        self.latest = bytes(1024 * 1024)
        self.output = dict(observed=True, frames_read=0, bytes_read=0,
                           index_path=index_path(self.dataset).relative_to(self.dataset).as_posix(),
                           poll_interval_s=POLL_INTERVAL_S, read_errors=0)
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run)

    def error(self, exc):
        self.output["read_errors"] += 1
        self.output["last_read_error"] = str(exc)

    def drain(self, *, final=False):
        path = index_path(self.dataset)
        self.output["index_path"] = path.relative_to(self.dataset).as_posix()
        try:
            stream = path.open("rb")
        except FileNotFoundError:
            return  # Dataset/index creation can follow worker startup.
        except OSError as exc:
            self.error(exc)
            return
        with stream:
            stream.seek(self.offset)
            for end, axes, name, fields in entries(stream):
                if self.stop.is_set() and not final:
                    break
                try:
                    json.loads(axes)
                    filename = name.decode("utf-8")
                    pixel_offset, width, height, pixel_type, compression, *_ = fields
                    size = width * height * {0: 1, 1: 2, 2: 3, 3: 2, 4: 2, 5: 2, 6: 2}[pixel_type]
                    if compression:
                        raise ValueError("compressed pixels are unsupported")
                    with (path.parent / filename).open("rb") as frame:
                        if pixel_offset + size > os.fstat(frame.fileno()).st_size:
                            raise OSError("incomplete pixel data: " + filename)
                        frame.seek(pixel_offset)
                        pixels = frame.read(size)
                    if len(pixels) != size:
                        raise OSError("incomplete pixel data: " + filename)
                except (ValueError, KeyError, OSError, OverflowError, RecursionError) as exc:
                    self.error(exc)
                    # A malformed complete entry affects itself, not later frames.
                    self.offset = end
                    continue
                self.latest = pixels
                self.output["frames_read"] += 1
                self.output["bytes_read"] += len(pixels)
                self.offset = end
        status = f"frames_read={self.output['frames_read']}; index_offset={self.offset}; index_size={path.stat().st_size}"
        if status != self.last_status:
            self.emit("status", message=status)
            self.last_status = status

    def run(self):
        while not self.stop.is_set():
            try:
                self.drain()
            except OSError as exc:
                self.error(exc)
            self.stop.wait(POLL_INTERVAL_S)

    def finish(self, drain):
        self.stop.set()
        self.thread.join()
        if drain:
            try:
                self.drain(final=True)
            except OSError as exc:
                self.error(exc)


class Load:
    def __init__(self, observer, count, max_s, emit):
        self.observer, self.emit = observer, emit
        self.count, self.max_s = count, max_s
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.output = dict(cpu_threads=0, frames_indexed_at_load_start=None)
        self.thread = threading.Thread(target=self.run)
        self.thread.start()

    def request_stop(self, reason):
        with self.lock:
            if not self.stop.is_set():
                self.output["load_stopped_by"] = reason
                self.stop.set()

    def run(self):
        barrier = threading.Barrier(self.count + 1)
        def hash_frame():
            hashlib.sha256(self.observer.latest).digest()
            try:
                barrier.wait()
            except threading.BrokenBarrierError:
                return
            while not self.stop.is_set():
                hashlib.sha256(self.observer.latest).digest()
        wall, cpu = time.perf_counter(), time.process_time()
        threads = [threading.Thread(target=hash_frame) for _ in range(self.count)]
        started = []
        try:
            for thread in threads:
                thread.start()
                started.append(thread)
            barrier.wait()
            self.output["cpu_threads"] = len(started)
        except RuntimeError as exc:
            barrier.abort()
            self.output["load_start_error"] = str(exc)
        try:
            with index_path(self.observer.dataset).open("rb") as stream:
                # Freeze the index extent once all hash threads are running.
                with io.BytesIO(stream.read(os.fstat(stream.fileno()).st_size)) as snapshot:
                    self.output["frames_indexed_at_load_start"] = sum(1 for _ in entries(snapshot))
        except FileNotFoundError:
            self.output["frames_indexed_at_load_start"] = 0
        except OSError as exc:
            self.output["load_start_index_error"] = str(exc)
        self.emit("status", message="load running: " + str(self.output["cpu_threads"]))
        if not self.stop.wait(max(0, self.max_s - (time.perf_counter() - wall))):
            self.request_stop("max_s")
        for thread in started:
            thread.join()
        elapsed, used = time.perf_counter() - wall, time.process_time() - cpu
        self.output.update(load_wall_s=elapsed, load_process_cpu_s=used,
                           load_cpu_ratio=used / elapsed if elapsed else 0)
        self.emit("status", message="load stopped: " + self.output["load_stopped_by"])

    def finish(self, reason):
        self.request_stop(reason)
        self.thread.join()


def main():
    job = json.loads(sys.stdin.buffer.readline(65536))
    if job["protocol"] != PROTOCOL or job["type"] != "job":
        raise ValueError("expected v1 job")

    emit_lock = threading.Lock()

    def emit(kind, **fields):
        with emit_lock:
            print(json.dumps(dict(protocol=PROTOCOL, type=kind, job_id=job["job_id"], **fields)), flush=True)

    output = {"observed": True}

    def result(state, artifacts, complete):
        fields = dict(state=state, output=output, artifacts=artifacts)
        if job["operation"] != "self_check":
            fields["input_complete"] = complete
        emit("result", **fields)

    emit("status", message="ready")
    if job["operation"] == "self_check":
        result("succeeded", [], True)
        return
    if job["operation"] != "observe_dataset":
        raise ValueError("unknown operation")
    params = job["parameters"]
    observer = Observer(job["input"]["dataset"], emit)
    output = observer.output
    if "cpu_threads" in params and "max_s" not in params:
        emit("result", state="failed", output=output, artifacts=[], input_complete=False,
             failure={"message": "max_s is required with cpu_threads"})
        return
    if "max_s" in params and not 0 < params["max_s"] <= 3600:
        emit("result", state="failed", output=output, artifacts=[], input_complete=False,
             failure={"message": "max_s must be > 0 and <= 3600"})
        return
    output["priority"] = priority(params.get("priority", "normal"))
    load = Load(observer, int(params["cpu_threads"]), params["max_s"], emit) if "cpu_threads" in params else None
    observer.thread.start()

    def finish(reason):
        observer.stop.set()
        if load:
            load.finish(reason)
            output.update(load.output)
        observer.finish(drain=reason == "writer")

    path = Path(job["output_dir"]) / "observation.txt"

    def artifact(validity, content):
        path.write_bytes(content)
        return dict(path=path.name, sha256=hashlib.sha256(content).hexdigest(), validity=validity)

    try:
        descriptor = artifact("partial", b"observing dataset\n")
        emit("artifact", artifact=descriptor)
        for line in sys.stdin.buffer:
            try:
                message = json.loads(line)
                if (not isinstance(message, dict) or message.get("protocol") != PROTOCOL
                        or message.get("job_id") != job["job_id"]):
                    raise ValueError("wrong lifecycle identity")
            except (ValueError, UnicodeError) as exc:
                emit("status", message="ignored lifecycle message: " + str(exc))
                continue
            emit("status", message=json.dumps(message, separators=(",", ":")))
            if message.get("type") == "cancel":
                finish("cancel")
                result("cancelled", [descriptor], False)
                return
            if message.get("type") == "writer" or (message.get("type") == "acquisition" and
                                                 message.get("outcome") == "completed" and message.get("writer") == "finished"):
                finish("writer")
                descriptor = artifact("final", b"dataset writer finished\n")
                result("succeeded", [descriptor], True)
                return
        finish("cancel")
        result("cancelled", [descriptor], False)
    finally:
        finish("cancel")


if __name__ == "__main__":
    main()
