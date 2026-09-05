"""Launch `microclaw serve` on a build whose acquisition teardown never returns.

**Gate code. It is never the product, and it must never be left running.** It
exists for one step of block 75b's runbook: the operator watches, in a real
browser session, the thing this whole block is for -- a hung acquisition that
used to spin "MicroClaw is working..." for fifteen minutes now reporting a
structured failure in about ten seconds.

Every acquisition started under this launcher hangs. That is the point. Close
the window when the step is done.

    uv run python design\\75-block75b-blocking-serve.py

`Acquisition.__exit__` in pycro-manager 1.0.2 is exactly `mark_finished()` then
`await_completion()`, read off `acquisition_superclass.py`. This calls both, in
that order, so the frame really lands and the supervisor really reports
`finalizing` -- and then simply does not return, which is what MicroClaw cannot
distinguish from the 2026-09-04 incident.
"""
from __future__ import annotations

import os
import sys
import time

from microclaw import tools

# Long enough that the operator can also send a second request and watch it be
# refused, and short enough that the session recovers on its own afterwards.
HOLD_S = float(os.environ.get("BLOCK75B_HOLD_S", "60"))
_REAL = tools.Acquisition


class BlockingTeardownAcquisition(_REAL):
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.mark_finished()
        self.await_completion()
        print(f"[block75b] teardown withheld for {HOLD_S:g}s", file=sys.stderr,
              flush=True)
        time.sleep(HOLD_S)


tools.Acquisition = BlockingTeardownAcquisition
print(f"[block75b] every acquisition in this session will hang for {HOLD_S:g}s "
      f"after its last frame. Gate use only.", file=sys.stderr, flush=True)

sys.argv = ["microclaw", "serve", *sys.argv[1:]]
from microclaw.__main__ import main  # noqa: E402 - after the injection, on purpose

sys.exit(main())
