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
    thing the operator would notice first if the discriminator were wrong.

Limb D was deleted after round 1. It called `export_session_script(ctrl, guard)`
on a guessed signature — the real one needs `output_path` and `records` — and
the deeper problem was that its mechanism needs a *driven session's* records and
this gate drives no session, so it could never have done its job here. The
emitted `image_metric_reason` comment is settled off-rig instead, by a test that
compiles the exported source with an embedded newline in the assertion.

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
    """The negative control, executed on live hardware.

    Round 1 of this gate never reached the mechanism, twice over, and both
    causes were the gate's fault and not the product's:

      * it hardcoded `z_range_um=4.0` centred on the current Z. The demo stage
        sat at Z=0, so `guard.check_z(-2.0)` raised before `run_autofocus` had
        even read the focus lock. The window is now derived from this rig's own
        configured bounds.
      * `is_continuous_focus_enabled()` came back **True** on this machine's
        `DAutoFocus`, so the PRE-EXISTING engaged-lock refusal would have
        returned four lines above 74a's branch and this limb would have failed
        for a reason with nothing to do with the block.
        `design/61-block61a-system-state.json` recorded `engaged: false` and the
        gate assumed that was invariant. It is not, so the limb now disengages
        deliberately, says so, and restores the entry state afterwards.
    """
    from microclaw.tools import get_focus_lock_state, run_autofocus, set_focus_lock

    stage = getattr(getattr(guard, "_c", None), "stage", None)
    z_min, z_max = getattr(stage, "z_min", None), getattr(stage, "z_max", None)
    if z_min is None or z_max is None:
        return (NOT_EXERCISED,
                "this rig declares no stage.z_min/z_max, so no sweep window can "
                "be derived and run_autofocus refuses before reading the lock. "
                "Nothing about block 74a was measured.")
    entry_z = float(ctrl.core.get_position())
    span = min(4.0, float(z_max) - float(z_min))
    if span <= 0:
        return NOT_EXERCISED, f"empty configured Z window [{z_min}, {z_max}]"
    lo = min(max(entry_z - span / 2, float(z_min)), float(z_max) - span)
    hi = lo + span

    entry_lock = get_focus_lock_state(ctrl, guard)
    was_engaged = bool(entry_lock.get("engaged"))
    note = ""
    if was_engaged:
        # Deliberate, reported, and restored below. Without this the limb cannot
        # reach 74a's branch at all: the engaged refusal returns first.
        set_focus_lock(ctrl, guard, enabled=False)
        if get_focus_lock_state(ctrl, guard).get("engaged"):
            return (NOT_EXERCISED,
                    "the lock reported engaged and would not disengage, so the "
                    "pre-existing engaged-lock refusal masks 74a's branch and "
                    "this limb measured nothing about the discriminator.")
        note = "lock was engaged on entry, disengaged for this limb; "

    try:
        result = run_autofocus(ctrl, guard, z_min_um=lo, z_max_um=hi,
                               z_step_um=1.0, method="sweep",
                               return_thumbnail=False)
    finally:
        if was_engaged:
            set_focus_lock(ctrl, guard, enabled=True)

    final_z = float(ctrl.core.get_position())
    (out / "C_run_autofocus.json").write_text(json.dumps({
        "entry_z_um": entry_z, "window": [lo, hi], "final_z_um": final_z,
        "configured_bounds": [float(z_min), float(z_max)],
        "lock_engaged_on_entry": was_engaged,
        "result": result,
    }, indent=1, default=str), encoding="utf-8")

    if isinstance(result, dict) and "error" in result:
        error = str(result["error"])
        if "hardware focus lock" in error:
            return (FAIL,
                    "this machine's software autofocus adapter was CLASSIFIED "
                    "as a probeable hardware lock and the image sweep was "
                    f"refused. entry_z={entry_z} final_z={final_z}. "
                    f"error={error[:400]}")
        return (FAIL, f"{note}the sweep failed for another reason, so this limb "
                      f"did not measure the refusal: {error[:400]}")
    converged = result.get("converged") if isinstance(result, dict) else None
    return (PASS, f"{note}sweep ran UNREFUSED over [{lo}, {hi}] um: "
                  f"converged={converged!r} entry_z={entry_z} final_z={final_z}"
                  + ("; lock re-engaged" if was_engaged else ""))


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
