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

It prints `hang installed on ...` the first time an acquisition is built. **If
you never see that line, the injection did not take and nothing you observe
afterwards is evidence** -- say so rather than reporting the session as healthy.
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
_PATCHED: dict = {}


def _blocking_acquisition(**kwargs):
    """Build the real acquisition, then make its teardown never return.

    **Wrapping, not subclassing.** `pycromanager.Acquisition` is a dispatching
    constructor: its `__new__` ignores `cls` and returns a
    `JavaBackendAcquisition` (or, under pymmcore, a `PythonBackendAcquisition`).
    A subclass of it compiles, answers every reasonable question about itself,
    and is never instantiated -- which is exactly how block 75b's first demo
    gate lost both of its blocked limbs, and this launcher with them: every
    acquisition simply ran normally. So patch the *returned type's* `__exit__`,
    which is where the lookup lands.
    """
    acq = _REAL(**kwargs)
    cls = type(acq)
    if cls not in _PATCHED:
        original = cls.__exit__

        def blocking_exit(self, exc_type, exc_val, exc_tb):
            self.mark_finished()
            self.await_completion()
            print(f"[block75b] teardown withheld for {HOLD_S:g}s",
                  file=sys.stderr, flush=True)
            time.sleep(HOLD_S)
            return None

        cls.__exit__ = blocking_exit
        _PATCHED[cls] = original
        print(f"[block75b] hang installed on {cls.__module__}.{cls.__name__}",
              file=sys.stderr, flush=True)
    return acq


def install() -> None:
    tools.Acquisition = _blocking_acquisition
    print(f"[block75b] every acquisition in this session will hang for "
          f"{HOLD_S:g}s after its last frame. Gate use only.",
          file=sys.stderr, flush=True)


# Guarded so the selftest can import this module and exercise `install()` and
# `_blocking_acquisition` against a dispatching fake without starting a server.
# The injection is the half that broke; it should not be the half nobody can
# execute off-rig.
if __name__ == "__main__":
    install()
    sys.argv = ["microclaw", "serve", *sys.argv[1:]]
    from microclaw.__main__ import main  # noqa: E402 - after install(), on purpose

    sys.exit(main())
