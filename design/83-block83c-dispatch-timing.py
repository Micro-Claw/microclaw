"""Offline dispatch timing; run with .venv/bin/python design/83-block83c-dispatch-timing.py.

Reports calling-thread p50/p99/max, never worker latency. The slow case keeps a
worker asleep before reading its job; the saturated case adds a full queue.
Fixture keys are public TEST-ONLY keys. No installation or network is involved.
"""
from pathlib import Path
import platform
import statistics
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from microclaw.skill_supervisor import Supervisor
from tests.test_skill_supervisor import release
from tests.test_skill_packages import NOW, policy


def report(label, values):
    values = sorted(value * 1000 for value in values)
    print(f"{label}: n={len(values)} p50={statistics.median(values):.3f} ms "
          f"p99={values[int(0.99 * (len(values) - 1))]:.3f} ms max={max(values):.3f} ms")


def measure(saturated, count=300):
    source, trust = release(), policy()
    with tempfile.TemporaryDirectory(prefix="83c-timing-") as directory:
        root = Path(directory)
        (root / "before-job.txt").write_text("never_read", encoding="utf-8")
        sup = Supervisor(max_workers=1, max_queued=1 if saturated else count + 1,
                         startup_deadline_s=20, shutdown_grace_s=0.5)
        try:
            def submit():
                return sup.submit(source, trust, now=NOW, python=sys.executable,
                                  operation="observe_dataset", parameters={"behaviour": "never_read"},
                                  dataset=root, output_dir=root)
            first = submit()
            end = time.perf_counter() + 8
            while not (root / "heartbeat.txt").exists():
                if time.perf_counter() >= end or first.wait(0.01):
                    raise RuntimeError("slow worker did not start")
            if saturated:
                queued = submit()
                assert queued.record()["state"] == "queued"
            samples = {key: [] for key in ("submit", "notify_acquisition", "notify_writer_finished")}
            for _ in range(count):
                start = time.perf_counter()
                handle = submit()
                samples["submit"].append(time.perf_counter() - start)
                if saturated:
                    assert handle.record()["state"] == "dispatch_failed"
                start = time.perf_counter()
                handle.notify_acquisition("unterminated", writer="unknown")
                samples["notify_acquisition"].append(time.perf_counter() - start)
                start = time.perf_counter()
                handle.notify_writer_finished()
                samples["notify_writer_finished"].append(time.perf_counter() - start)
            for name, values in samples.items():
                report(("saturated" if saturated else "slow") + " " + name, values)
        finally:
            sup.close(timeout=10)
            if any(thread.is_alive() for thread in sup._threads):
                raise RuntimeError("supervisor failed to close")


if __name__ == "__main__":
    print(f"Machine: {platform.platform()} {platform.machine()}, Python {platform.python_version()}")
    measure(False)
    measure(True)
