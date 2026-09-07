"""Reproduce AcqEngJ's hardware-sequencing deadline predicate off-rig.

design/78 §"The Java mechanism, confirmed" is scored from this. AcqEngJ-0.39.4's
`AcquisitionEvent.fromJSON` stores `min_start_time` as
`(long)(min_start_time * 1000.0)` -- truncation toward zero, which Python's
`int()` matches -- and `Engine.isSequencable` refuses a pair of differing
t-indices only when those absolute millisecond deadlines differ.

Run it with the repo venv:  .venv/bin/python design/78-sequencing-deadline-scan.py

The point of the second scan is that `interval_s = 0.001` is NOT safe: it
collides at frames 4006/4007 through double rounding, so no constant threshold
is a correct rule. Evaluate the predicate over the run's actual frame count.
"""

def ms(t, iv):
    return int(t * iv * 1000.0)

# 1) Long runs at and just above the 1 ms boundary.
print("--- 200000-frame scan, interval >= 0.001 ---")
for iv in (0.001, 0.0010000001, 0.00123, 0.0017, 0.002, 0.00333, 0.01, 0.0333, 0.1):
    n = 200000
    bad = None
    prev = ms(0, iv)
    for k in range(1, n):
        cur = ms(k, iv)
        if cur == prev:
            bad = (k-1, k, cur); break
        prev = cur
    print(f"  {iv:<12} first equal-consecutive pair: {bad}")

# 2) Just below the boundary.
print("--- just below 0.001 ---")
for iv in (0.0009999999, 0.0009, 0.0005, 0.0001, 1e-6, 1e-9):
    print(f"  {iv:<12} frames 0,1 -> {ms(0,iv)}, {ms(1,iv)}  equal={ms(0,iv)==ms(1,iv)}")

# 3) interval_s == 0: multi_d_acquisition_events behaviour
import inspect
from pycromanager.acquisition.acquisition_superclass import multi_d_acquisition_events
src = inspect.getsource(multi_d_acquisition_events)
for line in src.splitlines():
    if "min_start_time" in line or "time_interval_s" in line:
        print("  SRC:", line.strip())
