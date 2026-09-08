r"""Rig-side helper for block 78a's M5 gate: log level, log path, tree identity.

A script rather than an inline `python -c` here-string, for two reasons. The
established convention in this repo's runbooks is `uv run python design\<file>.py`
(design/80's demo gate), and a PowerShell here-string is a form the coordinator
cannot test off-rig -- while this file is exercised by
`design/78-block78a-m5-gate-selftest.py` before it ships.

The three Core method names are the snake_case forms pyjavaz's
`_camel_case_2_snake_case` produces from MMCoreJ's `enableDebugLog`,
`debugLogEnabled` and `getPrimaryLogFile`, all three confirmed present in
MMCoreJ.jar. They are not guesses.

    uv run python design\78-block78a-m5-probe.py --show
    uv run python design\78-block78a-m5-probe.py --set-debug on
    uv run python design\78-block78a-m5-probe.py --set-debug off
"""
from __future__ import annotations

import argparse
import inspect
import sys


def tree_has_per_write_refresh() -> bool:
    """True if THIS checkout still refreshes the GUI inside a property write.

    The gate's `refresh_in_source` field, read from the tree rather than from
    the operator's memory of which branch was checked out. The scorer fails the
    run if the two write arms report the same value.
    """
    from microclaw.hook_decisions import UntrustedHookAdapter
    return "refresh_gui" in inspect.getsource(UntrustedHookAdapter._apply_property)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set-debug", choices=("on", "off"))
    ap.add_argument("--show", action="store_true")
    args = ap.parse_args(argv)

    print("refresh_in_source", str(tree_has_per_write_refresh()).lower())

    from pycromanager import Core
    core = Core()
    was = core.debug_log_enabled()
    print("debug_log_enabled_was", was)
    if args.set_debug is not None:
        core.enable_debug_log(args.set_debug == "on")
        print("debug_log_enabled_now", core.debug_log_enabled())
    print("CORELOG", core.get_primary_log_file())
    return 0


if __name__ == "__main__":
    sys.exit(main())
