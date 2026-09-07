"""Block 80c's open design question, asked on a rig. Nothing is decided here.

design/80's 80c section defers exactly one decision to a measurement:

    before settling for a declared external precondition, check on a rig
    whether the returned `AutofocusMethod` exposes `get_property_names()` /
    `get_property_value()` over the bridge. If it does, the settings are
    recordable and the emitted script can print them in its envelope; if it
    does not, say so in the doc and declare the precondition.

Until that is answered, 80c's checklist would be guessing at its own central
decision -- and this notebook has already paid for one guessed parameter (the
80b gate's default sweep, which stood a whole trip down). So this probe runs
first and its output goes into design/80.

**It is a probe, not a gate.** It scores nothing, drives no acquisition, moves
no stage and spends no dose. It reads the autofocus manager, names the methods
it finds, selects the requested one, and then interrogates the returned object
about its own settings. Every question is asked in a `try` and reported as an
answer, because "this bridge does not expose that" is the finding, not an error.

Two rules it obeys:

* **A bridge collection is not Python-iterable** (design/59a, which cost a rig
  trip: `list()` over one raises `TypeError: 'mmcorej_StrVector' object is not
  iterable` on every rig and works against every fake). Every collection here is
  drained through the product's own `_drain_java_iterable`, never with `list()`.
* **Validate the method name before selecting it.** `setAutofocusMethodByName`
  rejects a class name with a cryptic Java `IllegalArgumentException`, so
  `PluginAccess.get_autofocus_method` validates first; this probe goes through
  that accessor rather than around it, so it measures the path 80c will inline.

Run it on a machine whose Micro-Manager has OughtaFocus, with the bridge on:

    uv run python design\\80-block80c-oughtafocus-probe.py > oughtafocus-probe.txt 2>&1

Send the whole file back. If `--plugin` is not installed the probe says which
methods *are*, which is itself the answer to "can this machine host 80c".
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FINDINGS: dict[str, object] = {}


def ask(label: str, fn):
    """Ask the bridge one question. An exception IS an answer here."""
    try:
        value = fn()
    except Exception as exc:                        # noqa: BLE001 - the finding
        FINDINGS[label] = {"answered": False,
                           "error": f"{type(exc).__name__}: {exc}"}
        print(f"  {label}: NOT AVAILABLE - {type(exc).__name__}: {exc}")
        return None
    FINDINGS[label] = {"answered": True, "value": value}
    print(f"  {label}: {value!r}")
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=4827)
    parser.add_argument("--plugin", default="OughtaFocus")
    parser.add_argument("--out", type=Path, default=Path("oughtafocus-probe.json"))
    args = parser.parse_args()

    from microclaw.controller import MicroscopeController, _drain_java_iterable

    print(f"Tree: {ROOT}")
    print(f"Asking about autofocus method {args.plugin!r} on port {args.port}\n")

    ctrl = MicroscopeController(port=args.port)

    print("1. What autofocus methods does this Micro-Manager have?")
    #    Drained, never list()-ed: design/59a.
    methods = ask("all_autofocus_methods", lambda: _drain_java_iterable(
        ctrl.plugins._studio.get_autofocus_manager().get_all_autofocus_methods()))
    if methods is None:
        print("\nThe manager itself is unreachable; 80c cannot be hosted here.")
        return finish(args)
    if args.plugin not in methods:
        print(f"\n{args.plugin!r} is NOT installed on this machine. Installed: "
              f"{methods}. 80c needs a machine that has it.")
        return finish(args)

    print(f"\n2. Select {args.plugin!r} through the product's own accessor.")
    #    Not through ask(): a Java proxy's repr is noise in output a person
    #    reads, and only its type is the finding.
    try:
        af = ctrl.plugins.get_autofocus_method(args.plugin)
    except Exception as exc:                        # noqa: BLE001 - the finding
        FINDINGS["get_autofocus_method"] = {
            "answered": False, "error": f"{type(exc).__name__}: {exc}"}
        print(f"  NOT AVAILABLE - {type(exc).__name__}: {exc}")
        return finish(args)
    FINDINGS["get_autofocus_method"] = {"answered": True,
                                        "value": f"<{type(af).__name__}>"}
    print(f"  returned a {type(af).__name__}")

    print("\n3. THE QUESTION: does the returned method expose its own settings?")
    names = ask("get_property_names", lambda: _drain_java_iterable(af.get_property_names()))
    if names:
        print(f"\n4. And can each one be read back? ({len(names)} properties)")
        values = {}
        for name in names:
            try:
                values[name] = str(af.get_property_value(name))
                print(f"  {name} = {values[name]!r}")
            except Exception as exc:                # noqa: BLE001 - the finding
                values[name] = f"UNREADABLE: {type(exc).__name__}: {exc}"
                print(f"  {name}: UNREADABLE - {type(exc).__name__}: {exc}")
        FINDINGS["property_values"] = values
        readable = [k for k, v in values.items()
                    if not str(v).startswith("UNREADABLE")]
        print(f"\nVERDICT: {len(readable)} of {len(names)} settings are recordable.")
        print("  -> 80c CAN print the plugin's settings in the emitted envelope."
              if len(readable) == len(names) else
              "  -> 80c can record only some settings; the doc must say which.")
    else:
        print("\nVERDICT: the returned method does not expose its property names "
              "over this bridge.\n  -> 80c must DECLARE the settings snapshot as "
              "an external precondition, and the emitted script must say in its "
              "envelope that its behaviour is not determined by its source.")

    # Whatever the answer, the emitted script's behaviour depends on rig state
    # the source does not carry, and the 2026-08-17 decision says the print is
    # the only disclosure there is. Record the fact, not an opinion about it.
    FINDINGS["disclosure_required"] = True
    return finish(args)


def finish(args):
    args.out.write_text(json.dumps(FINDINGS, indent=2, default=str),
                        encoding="utf-8")
    print(f"\nFindings written to {args.out}. Send this whole output back; "
          "design/80 records the answer and 80c's checklist is written from it.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:                               # noqa: BLE001 - reported
        traceback.print_exc()
        print("\nThe probe itself failed. That is a probe defect, not an answer "
              "about the plugin; report it as such.")
        raise SystemExit(2)
