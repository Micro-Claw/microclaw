"""Block 7b R7: exercise the plan-time refusals directly, with no agent.

Written because the 2026-07-28 gate could not reach two of them. The agent
declined to dispatch `max_power_percent: 110` (case 1) and refused twice to put
`device` / `out_path` / `illumination_envelope` into `hook_params` (case 3),
including after the operator said plainly that they wanted it done. Its stated
reasons for case 3 were wrong -- it believed the hook would receive those keys,
when `_resolve_hook` strips exactly them before the hook is constructed. A gate
cannot verify a refusal path the model will not approach, so this bypasses it.

>>> NO HARDWARE IS TOUCHED. <<<
Every check here fails before the code reaches a device. `_configure_hook_capabilities`
validates the envelope's shape, its declaration in `illumination.power_properties`,
and its ceiling against the configured one BEFORE it reads any property or prompts.
`_resolve_hook` builds a hook object and never starts an acquisition. Micro-Manager
does not need to be running and `--port` is never opened.

Run on the rig, from the repo root:

    uv run python design\32-block7b-r7-refusal-probe.py ^
        --config <reviewed M5 safety config> ^
        --hook mosaic_stitcher_v2 > r7.txt 2>&1

Retain r7.txt. Every case prints PASS or FAIL and the exact message the code
produced; the messages are the evidence, not the verdicts.
"""

import argparse
import sys

from microclaw.config import load_safety_config_or_exit
from microclaw.safety import SafetyGuard, SafetyViolation


sys.stdout.reconfigure(line_buffering=True)

_RESULTS: list[tuple[str, str]] = []


def record(status: str, name: str, detail: str = "") -> None:
    _RESULTS.append((status, name))
    print(f"[{status:4}] {name}")
    if detail:
        for line in str(detail).splitlines():
            print(f"         {line}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="reviewed rig safety config")
    parser.add_argument("--hook", required=True,
                        help="a saved analyze_frame hook, e.g. mosaic_stitcher_v2")
    args = parser.parse_args()

    from microclaw import tools
    from microclaw.hook_decisions import UntrustedHookAdapter
    try:
        # Only exists once feat/describe-hook lands; the probe must run on the
        # Block 7b branch without it.
        from microclaw.hook_manager import FORBIDDEN_SAVED_HOOK_PARAMS
    except ImportError:
        FORBIDDEN_SAVED_HOOK_PARAMS = ()

    parsed = load_safety_config_or_exit(args.config)
    guard = SafetyGuard(parsed.constraints)
    illumination = parsed.constraints.illumination
    configured = illumination.max_power_percent
    if not illumination.power_properties:
        record("FAIL", "config declares an illumination power property",
               "illumination.power_properties is empty; R7 case 1 cannot run.")
        return 1
    declared = illumination.power_properties[0]
    print(f"Config: {args.config}")
    print(f"  declared power property : {declared.device}.{declared.property}")
    print(f"  max_power_percent       : {configured}")
    print(f"  max_power_step_factor   : {illumination.max_power_step_factor}\n")

    # --- Case 1: envelope ceiling above the configured ceiling -------------
    # This is the check the agent would not let run. It raises before any
    # property read, so no device is contacted even if one is connected.
    if configured is None:
        record("SKIP", "case 1: ceiling above configured ceiling",
               "max_power_percent is unset, so no configured ceiling exists to exceed.")
    else:
        over = float(configured) + 10.0
        try:
            tools._configure_hook_capabilities(
                UntrustedHookAdapter(object()), None, guard, ".", "probe",
                {"device": declared.device, "property": declared.property,
                 "max_power_percent": over, "max_writes": 1},
                None,
            )
        except ValueError as exc:
            ok = "exceeds configured" in str(exc)
            record("PASS" if ok else "FAIL",
                   f"case 1: envelope ceiling {over} refused", exc)
        except Exception as exc:  # noqa: BLE001 - any other failure is a defect
            record("FAIL", "case 1: refused for the WRONG reason", repr(exc))
        else:
            record("FAIL", f"case 1: ceiling {over} was ACCEPTED",
                   "A ceiling above the configured maximum must never be authorized.")

    # --- Case 2: device/property not declared ------------------------------
    try:
        tools._configure_hook_capabilities(
            UntrustedHookAdapter(object()), None, guard, ".", "probe",
            {"device": "NoSuchDevice", "property": "NoSuchProperty",
             "max_power_percent": 1.0, "max_writes": 1},
            None,
        )
    except ValueError as exc:
        ok = "not declared" in str(exc)
        record("PASS" if ok else "FAIL", "case 2: undeclared pair refused", exc)
    else:
        record("FAIL", "case 2: undeclared pair was ACCEPTED")

    # --- Case 3: hook_params cannot smuggle capabilities -------------------
    # The keys the agent believed would reach the hook. They are popped in
    # _resolve_hook before the class is constructed; `log_path` is excluded
    # because a saved hook DECLARING it is refused earlier, by a different rule.
    smuggled = {
        "ctrl": "SMUGGLED", "guard": "SMUGGLED", "credentials": "SMUGGLED",
        "candidates": "SMUGGLED", "progress": "SMUGGLED",
        "survey_events": "SMUGGLED", "event_queue": "SMUGGLED",
        "device": "iBeamSmartCW-1", "out_path": r"C:\temp\x.tif",
        "illumination_envelope": {}, "save_dir": r"C:\temp",
        "path": r"C:\temp\y", "output_path": r"C:\temp\z",
        "artifact_dir": r"C:\temp\a", "property": "Power (mW)",
        "illumination_device": "x", "illumination_property": "y",
    }
    # A key that survives stripping is passed to `hook_cls(**params)`. For a hook
    # whose __init__ does not declare it that raises TypeError, so an unexpected
    # TypeError here means a key got through -- which is itself the finding.
    try:
        adapter = tools._resolve_hook(None, guard, args.hook, dict(smuggled), None)
    except TypeError as exc:
        record("FAIL", "case 3: a smuggled key reached the constructor", exc)
    except Exception as exc:  # noqa: BLE001
        record("FAIL", f"case 3: could not resolve saved hook '{args.hook}'", repr(exc))
    else:
        leaked = sorted(
            key for key, value in smuggled.items()
            if getattr(adapter.hook, key, None) == value
        )
        record("PASS" if not leaked else "FAIL",
               "case 3: smuggled hook_params do not reach the hook",
               "the hook constructed cleanly and holds none of the smuggled keys"
               if not leaked else f"LEAKED onto the instance: {leaked}")
        if FORBIDDEN_SAVED_HOOK_PARAMS:
            uncovered = sorted(set(smuggled) - set(FORBIDDEN_SAVED_HOOK_PARAMS))
            print(f"         keys NOT on the strip list: {uncovered or 'none'}")
        print("         Note: a key is harmless either because it was stripped or")
        print("         because this hook's __init__ does not accept it. A hook that")
        print("         DOES declare an unstripped key would receive it, so re-run")
        print("         this against each saved hook you actually intend to use.")

    print("\n" + "=" * 68)
    for status, name in _RESULTS:
        print(f"[{status:4}] {name}")
    failed = sum(1 for status, _ in _RESULTS if status == "FAIL")
    print("=" * 68)
    print("R7 PASS" if not failed else f"R7 FAIL ({failed} case(s))")
    print("No hardware was contacted by this script.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
