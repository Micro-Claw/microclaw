"""Score block 74a on the Windows demo machine, against the live ZMQ bridge.

Every limb here only *computes*, so per `CLAUDE.md` §6 this is a program rather
than copy-paste PowerShell: block 58a's gate reported `PASSED` over five failed
limbs because a pasted `throw` ends the current pipeline, not the session. Each
limb is scored INDEPENDENTLY, the script owns its own log (PowerShell 5.1 does
not capture a native child's stdout), and it exits nonzero unless every limb
PASSed. A limb that could not run its mechanism reports NOT EXERCISED, which is
never a pass.

    uv run python design/74-block74a-demo-gate.py --out block74a-evidence

**What this gate is for, and what it deliberately does not touch.** Block 74a's
positive case — a Nikon Ti's disengaged hardware lock being offered before an
image sweep — cannot be exercised here and is settled off-rig by the suite, from
that rig's own recorded payload. What needs a real bridge is the half a fake
cannot establish:

  * that `get_device_library` / `get_device_name` — two calls this block newly
    puts on the pre-sweep path — answer over real pyjavaz at all, and return
    exactly `DemoCamera` / `DAutoFocus` here. design/59 block 59a shipped a
    `list()` over a Core collection that worked against every fake in the suite
    and raised `TypeError: 'mmcorej_StrVector' object is not iterable` on every
    rig. A `MagicMock` cannot catch that class of defect;
  * that this machine's software autofocus adapter is **not** classified, so an
    ordinary image sweep still runs. That is the negative control, and it is the
    thing the operator would notice first if the discriminator were wrong;
  * that a session carrying `run_autofocus` still exports a script that parses.

Limb E is the control that fires. Run this same program on `main` and the
new-field limbs must FAIL there — a gate that cannot fail on the pre-change tree
measures nothing (design/59: the selftest is run on both trees so its failure
discriminates).

M5 and M2 cannot gate this block at all and should not be booked for it. M5
takes the EMU branch of `get_focus_lock_state`, which never returns
`status_properties`, so the refusal is structurally unreachable there; M2 has no
autofocus device configured.
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

PASS, FAIL, NOT_EXERCISED = "PASS", "FAIL", "NOT EXERCISED"
RESULTS: list[dict] = []
LIMBS: list = []


def limb(name: str, mechanism: str):
    """Register a limb. `mechanism` names what is under test, not the outcome.

    CLAUDE.md §6: an outcome-shaped step gets satisfied by a better route. 52b's
    mandatory limb asked an agent to "set <stage> Position (um)", it correctly
    used the named-stage route instead, and the refusal being gated never fired.
    """
    def decorate(fn):
        fn._limb = (name, mechanism)
        LIMBS.append(fn)
        return fn
    return decorate


def record(name: str, mechanism: str, status: str, detail: str) -> None:
    RESULTS.append({"limb": name, "mechanism": mechanism,
                    "status": status, "detail": detail})
    print(f"[{status:<13}] {name}\n                {mechanism}\n"
          f"                {detail}\n", flush=True)


# --------------------------------------------------------------------------
# Limb A -- the two new bridge calls answer, and say what the .cfg says.
# --------------------------------------------------------------------------

@limb("A_adapter_identity_over_the_bridge",
      "core.get_device_library / core.get_device_name on the autofocus device")
def limb_adapter_identity(ctrl, guard, out: Path):
    device = str(ctrl.core.get_auto_focus_device() or "")
    if not device:
        return (NOT_EXERCISED,
                "no autofocus device is configured on this machine, so the "
                "identity read has nothing to answer about. The demo config "
                "normally loads Autofocus/DemoCamera/DAutoFocus.")
    library = ctrl.core.get_device_library(device)
    adapter = ctrl.core.get_device_name(device)
    detail = (f"device={device!r} library={library!r} ({type(library).__name__}) "
              f"adapter={adapter!r} ({type(adapter).__name__})")
    (out / "A_adapter_identity.json").write_text(json.dumps({
        "device": device, "library": str(library), "adapter": str(adapter),
        "library_python_type": type(library).__name__,
        "adapter_python_type": type(adapter).__name__,
    }, indent=1), encoding="utf-8")
    # The discriminator compares tuples against a frozenset of plain strings, so
    # a bridge shadow that is not exactly `str` would never match and the
    # refusal would silently never fire. Check the TYPE, not just the value.
    if not (isinstance(library, str) and isinstance(adapter, str)):
        return (FAIL, detail + " -- one of these is not a Python str over this "
                               "bridge, so the identity comparison can never "
                               "match and no rig would ever be classified.")
    if (library, adapter) != ("DemoCamera", "DAutoFocus"):
        return (FAIL, detail + " -- expected ('DemoCamera', 'DAutoFocus') from "
                               "this machine's MMConfig 'Device,Autofocus,"
                               "DemoCamera,DAutoFocus' line.")
    return PASS, detail


# --------------------------------------------------------------------------
# Limb B -- the payload carries the identity it read, and names no Nikon skill.
# --------------------------------------------------------------------------

@limb("B_focus_lock_payload",
      "get_focus_lock_state's non-EMU payload on a live rig")
def limb_focus_lock_payload(ctrl, guard, out: Path):
    from microclaw.tools import get_focus_lock_state
    state = get_focus_lock_state(ctrl, guard)
    (out / "B_focus_lock_state.json").write_text(
        json.dumps(state, indent=1, default=str), encoding="utf-8")
    if "device" not in state:
        return (NOT_EXERCISED,
                f"no lock device in the payload: {json.dumps(state, default=str)}")
    missing = [k for k in ("adapter_library", "adapter_name") if k not in state]
    if missing:
        return (FAIL, f"payload is missing {missing}; this is the field block "
                      f"74a adds, so on `main` this limb FAILS by design. "
                      f"payload={json.dumps(state, default=str)}")
    hint = state.get("probe_hint", "")
    if "nikon-pfs" in hint:
        return (FAIL, f"the hint names the Nikon skill on a device labelled "
                      f"{state['device']!r}, which contains no 'PFS'. D4 gates "
                      f"case-insensitively on the DEVICE label. hint={hint!r}")
    return (PASS, f"device={state['device']!r} "
                  f"adapter=({state['adapter_library']!r}, "
                  f"{state['adapter_name']!r}) engaged={state.get('engaged')!r}; "
                  f"probe_hint present={bool(hint)}, names nikon-pfs=False")


# --------------------------------------------------------------------------
# Limb C -- the negative control, live: an image sweep is NOT refused here.
# --------------------------------------------------------------------------

@limb("C_image_sweep_still_runs",
      "run_autofocus with no probe on a software autofocus adapter")
def limb_image_sweep_runs(ctrl, guard, out: Path):
    from microclaw.tools import run_autofocus
    entry_z = float(ctrl.core.get_position())
    result = run_autofocus(ctrl, guard, z_range_um=4.0, z_step_um=1.0,
                           method="sweep", return_thumbnail=False)
    (out / "C_run_autofocus.json").write_text(
        json.dumps(result, indent=1, default=str), encoding="utf-8")
    final_z = float(ctrl.core.get_position())
    if isinstance(result, dict) and "error" in result:
        error = str(result["error"])
        if "hardware focus lock" in error:
            return (FAIL,
                    "this machine's software autofocus adapter was classified "
                    "as a probeable hardware lock and the image sweep was "
                    f"refused. entry_z={entry_z} final_z={final_z}. "
                    f"error={error[:400]}")
        return (FAIL, f"the sweep failed for another reason, so this limb did "
                      f"not measure the refusal: {error[:400]}")
    converged = result.get("converged") if isinstance(result, dict) else None
    return (PASS, f"sweep ran unrefused: converged={converged!r} "
                  f"entry_z={entry_z} final_z={final_z}")


# --------------------------------------------------------------------------
# Limb D -- an exported script still parses with the new argument in the tool.
# --------------------------------------------------------------------------

@limb("D_session_export_compiles",
      "export_session_script over this run's own recorded run_autofocus call")
def limb_export_compiles(ctrl, guard, out: Path):
    from microclaw.tools import export_session_script
    result = export_session_script(ctrl, guard)
    if not isinstance(result, dict) or "path" not in result:
        return (NOT_EXERCISED,
                f"no script was written, so nothing was parsed: "
                f"{json.dumps(result, default=str)[:400]}")
    source = Path(result["path"]).read_text(encoding="utf-8")
    (out / "D_exported_script.py").write_text(source, encoding="utf-8")
    if "run_autofocus" not in source:
        return (NOT_EXERCISED,
                "the export carries no run_autofocus step -- limb C must run "
                "in the same process, before this one, or there is nothing "
                "here to check. A fresh session emits a 13-line stub.")
    try:
        ast.parse(source)
    except SyntaxError as error:
        return FAIL, f"the emitted script does not parse: {error}"
    if "NOT EMITTED" in source:
        line = next(l for l in source.splitlines() if "NOT EMITTED" in l)
        return FAIL, f"an undecorated tool reached the export: {line.strip()}"
    return (PASS, f"{len(source.splitlines())} lines, parses, no NOT EMITTED; "
                  f"path={result['path']}")


# --------------------------------------------------------------------------
# Limb E -- the control that fires: is this the tree under test at all?
# --------------------------------------------------------------------------

@limb("E_running_build_is_the_branch",
      "the installed build carries 74a's discriminator and argument")
def limb_running_build(ctrl, guard, out: Path):
    """CLAUDE.md §6: a limb that cannot fail is not a criterion.

    Limbs A and C pass on `main` too -- an unchanged tool that never refuses
    anything also lets an image sweep run. This limb is what makes the run
    discriminate: on `main` it FAILS, naming what is absent.
    """
    import inspect
    from microclaw import tools
    from microclaw.tools_schema import TOOLS
    findings = []
    adapters = getattr(tools, "PROBEABLE_HARDWARE_FOCUS_LOCK_ADAPTERS", None)
    if not adapters:
        findings.append("microclaw.tools has no non-empty "
                        "PROBEABLE_HARDWARE_FOCUS_LOCK_ADAPTERS")
    params = inspect.signature(tools.run_autofocus).parameters
    if "image_metric_reason" not in params:
        findings.append("run_autofocus has no image_metric_reason parameter")
    schema = next((t for t in TOOLS if t["name"] == "run_autofocus"), None)
    if schema is None or "image_metric_reason" not in schema["input_schema"]["properties"]:
        findings.append("the run_autofocus schema does not declare "
                        "image_metric_reason")
    where = Path(inspect.getfile(tools)).resolve()
    if findings:
        return FAIL, f"NOT the block's build ({where}): " + "; ".join(findings)
    return (PASS, f"build at {where} carries {sorted(adapters)} and "
                  f"image_metric_reason in both signature and schema")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="block74a-evidence")
    parser.add_argument("--safety-config", default=None,
                        help="Leave unset. This gate deliberately requires no "
                             "configuration the product does not require "
                             "(design/60 block 60b).")
    parser.add_argument("--port", type=int, default=4827)
    parser.add_argument("--log", default=None)
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    from microclaw.authorization import RigAuthorizationError, validate_live_rig
    from microclaw.config import load_safety_config_or_exit
    from microclaw.controller import MicroscopeController
    from microclaw.safety import SafetyGuard

    parsed = load_safety_config_or_exit(args.safety_config)
    guard = SafetyGuard(parsed.constraints)
    ctrl = MicroscopeController(port=args.port, guard=guard)
    if not ctrl.is_connected():
        print("FAIL  could not connect to Micro-Manager; is the ZMQ server on?")
        return 2
    try:
        validate_live_rig(ctrl, parsed, guard=guard)
    except RigAuthorizationError as error:
        print(f"FAIL  this rig is not authorized for a session: {error}")
        return 2

    print(f"block 74a demo gate — {datetime.now().isoformat(timespec='seconds')}\n",
          flush=True)
    for fn in LIMBS:
        name, mechanism = fn._limb
        try:
            status, detail = fn(ctrl, guard, out)
        except Exception as error:          # one limb, one failure, no cascade
            status, detail = FAIL, f"{type(error).__name__}: {error}"
            (out / f"{name}_traceback.txt").write_text(
                traceback.format_exc(), encoding="utf-8")
        record(name, mechanism, status, detail)

    (out / "results.json").write_text(json.dumps(RESULTS, indent=1),
                                      encoding="utf-8")
    failed = [r for r in RESULTS if r["status"] == FAIL]
    unexercised = [r for r in RESULTS if r["status"] == NOT_EXERCISED]
    print(f"{len(RESULTS)} limbs: {len(RESULTS) - len(failed) - len(unexercised)} "
          f"PASS, {len(failed)} FAIL, {len(unexercised)} NOT EXERCISED")
    if unexercised:
        print("NOT EXERCISED is never a pass. Report these to the coordinator.")
    text = json.dumps(RESULTS, indent=1)
    if args.log:
        Path(args.log).write_text(text, encoding="utf-8")
    return 1 if (failed or unexercised) else 0


if __name__ == "__main__":
    raise SystemExit(main())
