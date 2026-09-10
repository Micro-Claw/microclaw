r"""What can microclaw's timing instrument actually resolve on this machine?

`duration_breakdown` and every hook timing record measure with
`time.monotonic()`. Block 79c-1's demo-gate artifacts showed every individual
phase span landing on an exact integer millisecond, which says the instrument's
floor there was 1 ms -- and four of its nine phases (validation, write,
read_back, read_stage_start_position) are plausibly below that.

`time.get_clock_info()` reports what CPython *claims*, which on Windows is the
nominal granularity of the underlying counter and not necessarily what you get:
`GetTickCount64`'s granularity follows the system timer period, which any
process can lower to ~1 ms with `timeBeginPeriod`. A JVM is a likely candidate.
So this probe measures the granularity **empirically** as well as reading the
claim, because the question is what the instrument resolves, not what the docs
say.

Run it twice -- once with Micro-Manager running, once with it closed. If the
observed monotonic granularity changes between those two runs, microclaw's
timing floor depends on what else is on the machine.

    uv run python design\79-clock-resolution-probe.py
"""
from __future__ import annotations

import platform
import sys
import time


def observed_granularity(clock, samples: int = 200_000):
    """Smallest nonzero step this clock actually takes, and how many it took."""
    reads = [clock() for _ in range(samples)]
    deltas = {b - a for a, b in zip(reads, reads[1:]) if b > a}
    if not deltas:
        return None, 0, reads[-1] - reads[0]
    return min(deltas), len(deltas), reads[-1] - reads[0]


def main() -> int:
    print(f"python      {sys.version.split()[0]}  on  {platform.platform()}")
    print()
    for name in ("monotonic", "perf_counter"):
        clock = getattr(time, name)
        info = time.get_clock_info(name)
        step, distinct, span = observed_granularity(clock)
        claim_us = info.resolution * 1e6
        step_us = None if step is None else step * 1e6
        print(f"{name}")
        print(f"   claims       resolution {claim_us:>12.3f} us   "
              f"(monotonic={info.monotonic}, adjustable={info.adjustable})")
        print(f"   implementation          {info.implementation}")
        if step_us is None:
            print("   observed     no step at all over 200,000 reads -- "
                  "the loop finished inside one tick")
        else:
            print(f"   observed     smallest step {step_us:>9.3f} us   "
                  f"({distinct} distinct step sizes over {span * 1e3:.3f} ms)")
        print()

    # The question that matters for microclaw's spans: can the clock separate
    # two events a few tens of microseconds apart, which is what the CoreLog
    # measured a Duration0 write at (34-39 us)?
    # A coarse clock exits this busy-wait immediately, because `a + 40e-6` is
    # below its next tick -- so a zero span here means the clock cannot
    # REPRESENT the gap, not that no time passed. That is the failure mode:
    # a property write disappears into a single tick.
    print("can it represent a ~40 us gap? (the CoreLog measures a Duration0")
    print("write at 34-39 us, so this is the real question for the write span)")
    for name in ("monotonic", "perf_counter"):
        clock = getattr(time, name)
        measured = []
        for _ in range(50):
            a = clock()
            target = a + 40e-6
            while clock() < target:      # busy-wait, no sleep granularity
                pass
            measured.append(clock() - a)
        zero = sum(1 for m in measured if m <= 0)
        print(f"   {name:<13} {zero}/50 reads returned a zero-or-negative span; "
              f"median {sorted(measured)[25] * 1e6:.1f} us")
    return 0


if __name__ == "__main__":
    sys.exit(main())
