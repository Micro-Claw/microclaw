"""Block 80c demo-machine gate: does the emitted plugin script actually run?

Every limb computes or drives an acquisition this program owns, so it ships as a
program rather than pasted PowerShell blocks (58a): each limb scores itself
independently, one FAIL cannot hide the rest behind a cascade, the gate owns its
log, and the exit status is nonzero on any FAIL *or* any NOT EXERCISED.

What this gate does NOT test, because it was settled off-rig and verified by
independent mutation during review. Do not add a limb for any of it:

* That the emitter renders the plugin hook and pins one `autofocus:<name>`
  token, that `mm_plugin_analyzer` still refuses, that the export imports no
  `microclaw`, contains no `input(`, and carries the disclosure sentence, and
  that an incomplete recorded Z envelope refuses before any frame. All of it is
  covered in `tests/test_session_script_export.py`; three separate mutations
  were watched failing by the coordinator on 2026-09-07.
* That a Java `String[]` needs `java.lang.reflect.Array` rather than the
  collection drain or `Arrays.asList`. Measured on M2 *and* the demo machine by
  `design/80-block80c-oughtafocus-probe.py`, n=2, recorded in design/80. A gate
  cannot say it better than two rigs already did.

What needs the bridge is exactly one thing, and it is the thing this block is
for: **an exported script that compiles is not an exported script that works**
(52b, which cost three M5 trips, two of whose defects survived compilation and
every grep in the runbook). Limb C execs the emitted script in a child process
against this same bridge and this same OughtaFocus, and compares it with the
live run field by field.

It requires **no configuration the product does not require** (60b): no
`--safety-config`, no workspace. Datasets go under the machine's configured
`workspace_dir` when it has one, else under `--out`. The one thing it does need
is the one thing the *product* needs -- `plugins.allow_hardware_motion`, which
`MMAutofocusPluginHook` gates on before it resolves the plugin at all. The gate
reads this machine's own value and reports NOT EXERCISED, naming the literal
line to add, rather than editing anything.

`NOT EXERCISED` is never a pass. If OughtaFocus is not installed, or hardware
motion is off, or the Z envelope cannot hold the plugin's own search range, the
limbs say exactly that and the gate exits nonzero. Do not tick them.

The plugin's own settings are read and obeyed, never substituted: the gate
derives its Z headroom from OughtaFocus's real `SearchRange_um` rather than the
built-in sweep's 15/0.5, which is the substitution design/80 forbids and which
on both measured machines would have been wrong (10 um, not 15).
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import traceback
from pathlib import Path

ROOT = Path(os.environ.get("MICROCLAW_TREE_UNDER_TEST") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(ROOT))

RESULTS = []
OBSERVATIONS = []
HOOKLESS_SHA = "9a56d7a9ca1e93e173cb4358712d94f4b837dfcfe7a933010af9f4df033b0826"
DISCLOSURE = "The plugin's live settings, not this script, decide the focus."


class NotExercised(Exception):
    """The limb could not run its mechanism. Never a pass (58a)."""


class Tee:
    def __init__(self, stream, path):
        self.stream, self.file = stream, open(path, "a", encoding="utf-8")

    def write(self, data):
        self.stream.write(data)
        self.file.write(data)
        return len(data)

    def flush(self):
        self.stream.flush()
        self.file.flush()


def limb(name, fails_if):
    def decorate(fn):
        try:
            detail, status = fn() or "", "PASS"
        except NotExercised as exc:
            detail, status = str(exc), "NOT EXERCISED"
        except Exception as exc:                    # noqa: BLE001 - reported
            detail, status = f"{type(exc).__name__}: {exc}", "FAIL"
            traceback.print_exc()
        RESULTS.append({"name": name, "status": status, "detail": detail,
                        "fails_if": fails_if})
        print(f"{status}: {name} - {detail}")
        return fn
    return decorate


def observe(name, detail):
    """A reading this trip collects but does not score.

    Deliberately NOT a limb: 58a's rule is that a limb which cannot fail is not
    a criterion, and these are readings whose value is the number, not a
    verdict. Reported separately so no one can read them as a pass.
    """
    OBSERVATIONS.append({"name": name, "detail": detail})
    print(f"OBSERVATION: {name} - {detail}")


def read_hook_log(path: Path) -> list[dict]:
    """The hook log in ARRIVAL order -- that order is the evidence."""
    if not path.exists():
        raise NotExercised(f"the hook wrote no log at {path}")
    text = path.read_text(encoding="utf-8")
    try:
        records = json.loads(text)
    except ValueError as exc:
        raise AssertionError(f"hook log at {path} is not readable JSON: {exc}") from exc
    if not isinstance(records, list):
        raise AssertionError(f"hook log at {path} is {type(records).__name__}, not a list")
    return records


def focused_records(records: list[dict]) -> list[dict]:
    """Records where the plugin ran and the passive guard accepted the result.

    `MMAutofocusPluginHook` writes `best_z_um` and `plugin` only on that path.
    """
    return [r for r in records if r.get("best_z_um") is not None]


def skipped_records(records: list[dict]) -> list[dict]:
    return [r for r in records if r.get("autofocus") == "skipped"]


def unsafe_records(records: list[dict]) -> list[dict]:
    return [r for r in records if r.get("autofocus") == "unsafe_abort"]


def comparable(records: list[dict]) -> list[dict]:
    """Hook records with the only field that cannot agree across runs removed.

    `observed_at` is a wall-clock arrival stamp. Everything else -- position,
    coordinates, the plugin's chosen Z and the plugin name -- must match. 80b
    round 2's lesson is that comparing one scalar is not comparing the record:
    it credited `best_z_um`, which on a contrast-free field is the same number
    in both arms whether or not the two runs did the same thing.
    """
    return [{k: v for k, v in r.items() if k != "observed_at"} for r in records]


def dataset_axes(path: Path) -> list[dict]:
    """Every frame's axes identity, sorted. The engine's own identity (contract 2).

    Read through ndstorage rather than by globbing filenames: block 60b's gate
    lost its one rig limb to a glob that matched what its fake wrote instead of
    what ndstorage writes.
    """
    try:
        from ndstorage import Dataset
    except ImportError:                             # older pycro-manager
        from pycromanager import Dataset
    ds = Dataset(str(path))
    return sorted(ds.get_image_coordinates_list(), key=lambda d: sorted(d.items()))


def emitter_probe(tools, strategy: str):
    """Does this build emit this hooked fixed plan? No bridge needed.

    The control, and it fires: on a pre-80c tree `autofocus_mm_plugin` raises
    CannotEmit, which is the whole behaviour the block changed.
    """
    params = tools.RecordedParams({
        "protocol": "timelapse",
        "positions": [{"name": "a", "x_um": 1.0, "y_um": 2.0, "z_um": 3.0}],
        "protocol_params": {"n_frames": 1, "interval_s": 0, "exposure_ms": 10},
        "save_dir": "/data", "name": "probe",
        "hook_strategy": strategy,
        "hook_params": {"plugin_name": "OughtaFocus"},
        "_export_safety_limits": {
            "x_um": (-1000.0, 1000.0), "y_um": (-1000.0, 1000.0),
            "z_um": (-1000.0, 1000.0), "exposure_ms": (0.0, None),
            "analysis_min_snr": None, "z_move_tolerance_um": None,
            "x_move_tolerance_um": None, "y_move_tolerance_um": None,
            "named_stage_move_tolerances_um": {},
        },
    }, {"results": [{"dataset_path": "/data/probe"}]})
    params["_tool_use_id"] = "toolu_probe"
    emitter = getattr(tools.run_multiposition_acquisition, "_microclaw_emitter", None)
    if emitter is None:
        raise AssertionError("run_multiposition_acquisition carries no @emits emitter")
    try:
        return emitter(params)
    except tools.CannotEmit as exc:
        return f"REFUSED: {exc}"


def choose_centre(z_now: float, z_min: float, z_max: float,
                  search_um: float) -> tuple[float, str]:
    """A Z the plugin's OWN search range fits inside, and say what was chosen.

    80b round 1 stood its whole gate down because it guessed a sweep: 4 um
    centred on a stage at Z=1.0 with z_min=0.0 reached -1.0. The gate holds the
    bounds, the current Z and -- here -- the plugin's real `SearchRange_um`, so
    guessing is not required and is not allowed. Only a rig where the plugin's
    own range cannot fit is NOT EXERCISED.
    """
    span = z_max - z_min
    if span <= search_um:
        raise NotExercised(
            f"OughtaFocus searches {search_um:.2f} um, which does not fit inside "
            f"this rig's {span:.2f} um Z envelope ({z_min}..{z_max}). The gate "
            "will not narrow the plugin's own setting to make room.")
    margin = search_um / 10
    low, high = z_min + search_um / 2 + margin, z_max - search_um / 2 - margin
    if low > high:
        low, high = z_min + search_um / 2, z_max - search_um / 2
    centre = min(max(z_now, low), high)
    if abs(centre - z_now) > 1e-9:
        note = (f"centre moved from {z_now:.2f} to {centre:.2f} um so the "
                f"plugin's {search_um:.2f} um search clears {z_min}..{z_max}")
    else:
        note = (f"the stage at {z_now:.2f} um already clears both bounds for a "
                f"{search_um:.2f} um search")
    return centre, note


def hookless_checksum(tools, guard, path: Path) -> tuple[str, int]:
    """The regression that matters most: a plain grid must not change at all."""
    records = [{"role": "assistant", "content": [{
        "type": "tool_use", "id": "toolu_hookless",
        "name": "run_multiposition_acquisition",
        "input": {"protocol": "timelapse",
                  "positions": [{"name": "a", "x_um": 1, "y_um": 2, "z_um": 3},
                                {"name": "b", "x_um": 11, "y_um": 2, "z_um": 3}],
                  "protocol_params": {"n_frames": 1, "interval_s": 0},
                  "save_dir": "/data", "name": "run"},
    }]}]
    tools.export_session_script(None, guard, str(path), records)
    source = path.read_text(encoding="utf-8")
    return hashlib.sha256(source.encode()).hexdigest(), len(source.splitlines())


def printed_snapshot(stdout: str, label: str):
    """The dict the emitted script printed after `label`, parsed.

    Parsed with `ast.literal_eval` rather than matched with a regex, because the
    values are opaque text that may contain a locale comma (`'2,5'` on the demo
    machine against `'2.5'` on M2) and, on a failure path, an embedded newline
    from a Java stack trace.
    """
    for line in stdout.splitlines():
        if line.startswith(label):
            try:
                return ast.literal_eval(line[len(label):].strip())
            except (ValueError, SyntaxError):
                return None
    return None


def finish(args):
    log = args.log or (args.out / "score.json")
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(json.dumps({"limbs": RESULTS, "observations": OBSERVATIONS},
                              indent=2), encoding="utf-8")
    print("\n=== BLOCK 80c DEMO GATE ===")
    for row in RESULTS:
        print(f"  {row['status']:14} {row['name']}")
    for row in OBSERVATIONS:
        print(f"  {'(observation)':14} {row['name']}")
    bad = [r for r in RESULTS if r["status"] != "PASS"]
    print(f"\n{len(RESULTS) - len(bad)}/{len(RESULTS)} PASS; score written to {log}")
    if bad:
        print("NOT A PASS. Report every non-PASS row above verbatim.")
    return 1 if bad else 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=4827)
    parser.add_argument("--out", type=Path, default=Path("block80c-evidence"))
    parser.add_argument("--log", type=Path, default=None)
    parser.add_argument("--plugin", default="OughtaFocus")
    parser.add_argument("--exposure-ms", type=float, default=10.0)
    parser.add_argument("--step-um", type=float, default=20.0,
                        help="XY offset between the gate's two fields.")
    args = parser.parse_args()
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=True)
    sys.stdout = sys.stderr = Tee(sys.__stdout__, args.out / "gate.txt")
    print(f"Tree under test: {ROOT}")

    from microclaw import tools
    from microclaw.config import load_safety_config_or_exit
    from microclaw.controller import MicroscopeController
    from microclaw.safety import SafetyGuard

    loaded = {}

    def safety():
        """This machine's own config, loaded once, never exiting the process.

        `load_safety_config_or_exit` raises SystemExit by design, so a gate that
        lets it through prints no score at all. Called lazily and AFTER the
        control, so a config problem cannot take limb E's answer with it.
        """
        if not loaded:
            try:
                config = load_safety_config_or_exit(None)
                loaded.update(config=config, guard=SafetyGuard(config.constraints))
            except (SystemExit, Exception) as exc:  # noqa: BLE001 - reported
                loaded.update(config=None, guard=None, error=str(exc) or type(exc).__name__)
        return loaded["config"], loaded["guard"]

    def score_hookless():
        _, offline_guard = safety()
        if offline_guard is None:
            raise NotExercised(
                "this machine's safety config could not be loaded: "
                + loaded.get("error", "unknown"))
        digest, lines = hookless_checksum(
            tools, offline_guard, args.out / "G-hookless.py")
        if digest != HOOKLESS_SHA:
            raise AssertionError(
                f"the hookless export is {digest} over {lines} lines, not the "
                f"recorded {HOOKLESS_SHA}. A plain SMLM grid changed.")
        return f"sha256 {digest[:16]}... over {lines} lines, unchanged"

    # G is deliberately NOT in this list: it reaches no microscope, so a
    # stand-down must not take its evidence with it. 80b round 1 lost exactly
    # this limb to exactly that coupling.
    LATER = ("0 - bridge, camera, focus device, the plugin, and motion allowed",
             "A - a live plugin autofocus run focuses and records its settings",
             "B - the exported script is standalone and inlines the real accessor",
             "C - the exported script RUNS and agrees with the live run",
             "D - post_hardware_hook_fn fired and the envelope disclosed",
             "H - live and standalone datasets carry the same frame identity")

    # E first, and it is the control: it needs no bridge and it FIRES on a
    # pre-80c tree, where this emitter refuses the plugin hook. 58a's lesson is
    # that a limb which cannot fail is not a criterion.
    @limb("E - this build emits the plugin hook and still refuses the analyzer (CONTROL)",
          "a pre-80c tree, where the fixed-plan emitter refuses both plugin hooks")
    def limb_e():
        rendered = emitter_probe(tools, "autofocus_mm_plugin")
        if rendered.startswith("REFUSED: "):
            raise AssertionError(
                "the fixed-plan emitter still refuses the plugin hook, so this is "
                "not a tree with 80c in it: " + rendered[len("REFUSED: "):])
        for needed in ("class MMAutofocusPluginHook", "mm.plugins = ",
                       "_PLUGIN_MOTION_TOKEN", DISCLOSURE):
            if needed not in rendered:
                raise AssertionError(f"it emitted, but without {needed!r} in it")
        analyzer = emitter_probe(tools, "mm_plugin_analyzer")
        if not analyzer.startswith("REFUSED: "):
            raise AssertionError(
                "mm_plugin_analyzer EMITTED. It must stay refused: get_object "
                "constructs an arbitrary class by name and check_plugin is a "
                "runtime blocklist, which is authorization state, not source.")
        if "get_object" not in analyzer:
            raise AssertionError(
                "the analyzer refusal no longer names get_object, so it has "
                "reverted to the stale reason 80c corrected: " + analyzer)
        return ("the emitter renders the plugin hook with its accessor, pinned "
                "token and disclosure; the analyzer still refuses, naming get_object")

    if RESULTS[0]["status"] != "PASS":
        for name in LATER:
            RESULTS.append({"name": name, "status": "NOT EXERCISED",
                            "detail": "stood down: limb E found no 80c in this build",
                            "fails_if": "n/a"})
            print(f"NOT EXERCISED: {name} - stood down by limb E")
        limb("G - the hookless grid is byte-identical",
             "80c having changed a plain multiposition export")(score_hookless)
        return finish(args)

    state = {}

    @limb("0 - bridge, camera, focus device, the plugin, and motion allowed",
          "no bridge, no camera, no focus device, the plugin absent, motion "
          "disabled, or the plugin's own search range not fitting this envelope")
    def limb_0():
        parsed, guard = safety()
        if parsed is None or guard is None:
            raise NotExercised(
                "this machine's safety config could not be loaded: "
                + loaded.get("error", "unknown"))
        # The product's own gate, checked before the bridge so its message is
        # about the config and not about a connection. This is configuration the
        # PRODUCT requires -- MMAutofocusPluginHook refuses without it before it
        # resolves the plugin at all -- so requiring it is not 60b's defect. The
        # gate reports it and edits nothing.
        if not parsed.constraints.plugins.allow_hardware_motion:
            raise NotExercised(
                "this machine's safety config sets "
                "`plugins.allow_hardware_motion: false`, which the PRODUCT "
                "refuses on before it resolves any plugin. Add "
                "`allow_hardware_motion: true` under `plugins:` in that file and "
                "re-run. The gate will not edit a production safety config.")
        ctrl = MicroscopeController(port=args.port)
        camera = str(ctrl.core.get_camera_device() or "")
        if not camera:
            raise NotExercised("Micro-Manager has no camera configured")
        stage_device = str(ctrl.core.get_xy_stage_device() or "")
        if not stage_device:
            raise NotExercised("Micro-Manager has no XY stage configured")
        focus = str(ctrl.core.get_focus_device() or "")
        if not focus:
            raise NotExercised(
                "Micro-Manager has no focus device; OughtaFocus with an empty "
                "FocusDrive falls back to Core's focus device, so there is "
                "nothing for it to move")
        # Ask the product's own accessor, which validates the name and turns a
        # wrong one into a message rather than a Java IllegalArgumentException.
        try:
            af = ctrl.plugins.get_autofocus_method(args.plugin)
        except Exception as exc:                    # noqa: BLE001 - reported
            raise NotExercised(
                f"{args.plugin!r} is not selectable on this Micro-Manager: {exc}. "
                "Run design/80-block80c-oughtafocus-probe.py to see what is "
                "installed; that is the reusable form of this question."
            ) from exc
        from microclaw.controller import _autofocus_settings_snapshot
        snapshot = _autofocus_settings_snapshot(af, args.port)
        if not snapshot.get("available"):
            raise NotExercised(
                "the plugin is selectable but its settings could not be read: "
                f"{snapshot.get('reason')}. Two rigs read all 13 through "
                "java.lang.reflect.Array, so this is worth reporting.")
        settings = snapshot["settings"]
        try:
            search_um = float(str(settings["SearchRange_um"]).replace(",", "."))
        except (KeyError, ValueError) as exc:
            raise NotExercised(
                "the plugin exposes no readable SearchRange_um, so the gate "
                f"cannot derive Z headroom from the plugin's own setting: {exc}. "
                "It will not substitute the built-in sweep's 15 um."
            ) from exc
        x0, y0 = float(ctrl.core.get_x_position()), float(ctrl.core.get_y_position())
        z0 = float(ctrl.core.get_position())
        stage = parsed.constraints.stage
        centre, note = choose_centre(
            z0, float(stage.z_min), float(stage.z_max), search_um)
        fields = [{"name": "gateA", "x_um": x0, "y_um": y0, "z_um": centre},
                  {"name": "gateB", "x_um": x0 + args.step_um, "y_um": y0,
                   "z_um": centre}]
        for field in fields:
            try:
                guard.check_xy(field["x_um"], field["y_um"])
                guard.check_z(field["z_um"])
            except Exception as exc:                # noqa: BLE001 - reported
                raise NotExercised(
                    f"{field['name']} at ({field['x_um']}, {field['y_um']}, "
                    f"{field['z_um']}) is outside this rig's configured bounds: {exc}"
                ) from exc
        # A check on choose_centre's arithmetic, not on the operator's input: if
        # the edges of the plugin's own search are outside the bounds it read,
        # that is this gate's defect and it must say so loudly.
        for edge in (centre - search_um / 2, centre + search_um / 2):
            try:
                guard.check_z(edge)
            except Exception as exc:                # noqa: BLE001 - reported
                raise AssertionError(
                    f"choose_centre returned {centre} for a {search_um} um "
                    f"search, whose edge {edge} is outside the very bounds it "
                    f"read: {exc}") from exc
        workspace = getattr(parsed.constraints, "workspace_dir", None)
        root = Path(workspace) / "block80c" if workspace else args.out / "data"
        root.mkdir(parents=True, exist_ok=True)
        state.update(ctrl=ctrl, guard=guard, fields=fields, root=root,
                     camera=camera, focus=focus, snapshot=snapshot,
                     search_um=search_um, home=(x0, y0, z0), parsed=parsed)
        # The passenger design/80 asked for: M2's round-2 FocusDrive and Channel
        # were empty with the rig's hardware OFF, and that run could not tell
        # "empty because nothing was loaded" from "empty in the saved profile".
        # This machine has a config loaded, so it is a third reading. Reported,
        # never scored -- the number is the point, not a verdict.
        observe("passenger: hardware-referencing settings with a config loaded",
                f"FocusDrive={settings.get('FocusDrive')!r} "
                f"Channel={settings.get('Channel')!r}; Core focus device is "
                f"{focus!r}. An empty FocusDrive means OughtaFocus falls back to "
                f"Core's current focus device, which the settings do not name.")
        observe("passenger: the settings the emitted script will disclose",
                json.dumps(settings, sort_keys=True))
        return (f"camera {camera!r}, focus {focus!r}, plugin {args.plugin!r} with "
                f"{len(settings)} readable settings; SearchRange_um {search_um} "
                f"read from the plugin itself; fields {args.step_um} um apart "
                f"from ({x0:.1f}, {y0:.1f}) at Z {centre:.2f} "
                f"(envelope {stage.z_min}..{stage.z_max}) - {note}; "
                f"saving under {root}")

    def need(*keys):
        for key in keys:
            if key not in state:
                raise NotExercised("stood down: limb 0 did not establish the rig")
        return [state[k] for k in keys]

    @limb("A - a live plugin autofocus run focuses and records its settings",
          "the plugin not running once per position, or its settings never "
          "reaching the tool result the emitter must render from")
    def limb_a():
        ctrl, guard, fields, root = need("ctrl", "guard", "fields", "root")
        log_path = args.out / "A-live-hook.json"
        # One dict drives the live call AND limb B's export, so the exported
        # script cannot be reproducing arguments the run did not make (52b).
        call_input = dict(
            protocol="timelapse", positions=fields, save_dir=str(root),
            name="live_plugin", hook_strategy="autofocus_mm_plugin",
            hook_params={"plugin_name": args.plugin},
            log_path=str(log_path),
            protocol_params={"n_frames": 1, "interval_s": 0,
                             "exposure_ms": args.exposure_ms},
        )
        result = tools.run_multiposition_acquisition(ctrl, guard, **call_input)
        if "error" in result:
            raise AssertionError(f"the live run returned an error: {result['error']}")
        records = read_hook_log(log_path)
        focused, skipped, unsafe = (focused_records(records),
                                    skipped_records(records),
                                    unsafe_records(records))
        state["A"] = {"result": result, "records": records, "log": log_path,
                      "focused": focused, "call_input": call_input}
        if unsafe:
            raise AssertionError(
                f"the plugin left Z outside the guard on {len(unsafe)} "
                f"position(s) and the hook aborted: {unsafe}. That is the passive "
                "guard working, but this limb measured the abort path.")
        if skipped:
            raise AssertionError(
                f"{len(skipped)} of {len(records)} positions SKIPPED autofocus: "
                f"{[r.get('reason') for r in skipped]}. The plugin raised, so "
                "this limb measured the skip path, not a focus run.")
        if len(focused) != len(fields):
            raise AssertionError(
                f"expected one plugin run per position ({len(fields)}), got "
                f"{len(focused)} from {len(records)} records")
        # The half 63a's lesson is about: an emitter may only render what the
        # record contains, so the snapshot must be IN the tool result, not just
        # on the hook object.
        recorded = result.get("autofocus_settings_snapshot")
        if recorded is None:
            raise AssertionError(
                "the tool result carries no autofocus_settings_snapshot, so the "
                "emitter has nothing to render and the exported script would "
                "disclose nothing. This is 63a's rule: a tool that reports a "
                "number must report where it sent it.")
        if not recorded.get("available"):
            raise AssertionError(
                "the live run recorded the snapshot as unavailable: "
                f"{recorded.get('reason')}. Limb 0 read it successfully moments "
                "earlier through the same accessor, so these disagree.")
        live_at_zero = state["snapshot"]["settings"]
        if recorded["settings"] != live_at_zero:
            raise AssertionError(
                "the snapshot recorded by the run differs from the one limb 0 "
                f"read directly: run={recorded['settings']} limb0={live_at_zero}")
        zs = [r["best_z_um"] for r in focused]
        plugins_named = sorted({r.get("plugin") for r in focused})
        if plugins_named != [args.plugin]:
            raise AssertionError(
                f"the hook logged plugin {plugins_named}, not [{args.plugin!r}]")
        return (f"{len(focused)} plugin runs, Z {zs}; the result records "
                f"{len(recorded['settings'])} settings, agreeing with limb 0's "
                f"direct read; hook logged plugin {plugins_named}")

    @limb("B - the exported script is standalone and inlines the real accessor",
          "an export that imports microclaw, hand-writes the accessor, drops "
          "the guard token, or prompts")
    def limb_b():
        ctrl, guard = need("ctrl", "guard")
        if "A" not in state:
            raise NotExercised(
                "stood down: limb A did not run, so there is no session to export")
        path = args.out / "C-exported.py"
        # export_session_script compiles a SUPPLIED record, and this process is
        # not an agent session, so the gate hands it the one call limb A made --
        # built from limb A's own call_input, never retyped (52b).
        records = [
            {"role": "assistant", "content": [{
                "type": "tool_use", "id": "toolu_gate_plugin",
                "name": "run_multiposition_acquisition",
                "input": state["A"]["call_input"],
            }]},
            {"role": "user", "content": [{
                "type": "tool_result", "tool_use_id": "toolu_gate_plugin",
                "content": json.dumps(state["A"]["result"]),
            }]},
        ]
        result = tools.export_session_script(ctrl, guard, str(path), records)
        state["B"] = {"result": result, "path": path}
        if not result.get("complete", False):
            raise AssertionError(
                f"the export reports incomplete: {result.get('not_emitted_calls')}")
        source = path.read_text(encoding="utf-8")
        ast.parse(source)
        leaked = [l for l in source.splitlines()
                  if re.match(r"\s*(from|import)\s+microclaw", l)]
        if leaked:
            raise AssertionError(f"the export imports microclaw: {leaked}")
        if "input(" in source:
            raise AssertionError(
                "the exported script prompts. Per the 2026-08-17 operator "
                "decision it prints its envelope and never asks: the print is "
                "the only disclosure, and running the script is the consent.")
        for needed in ("class MMAutofocusPluginHook",
                       "def _autofocus_settings_snapshot",
                       "def _new_static_java_class", "java.lang.reflect.Array",
                       "mm.plugins = ", "_PLUGIN_MOTION_TOKEN = ",
                       "def check_plugin_motion", "_LIMITS = ",
                       "guard.check_xy(", "post_hardware_hook_fn", DISCLOSURE):
            if needed not in source:
                raise AssertionError(f"the exported script is missing {needed!r}")
        if "asList" in source:
            raise AssertionError(
                "the exported script reaches for Arrays.asList, which pyjavaz "
                "cannot resolve for a String[] shadow -- measured on M2 and the "
                "demo machine, n=2. The reader must be java.lang.reflect.Array.")
        recorded = state["A"]["result"]["autofocus_settings_snapshot"]["settings"]
        for name, value in recorded.items():
            if repr(value) not in source:
                raise AssertionError(
                    f"the recorded setting {name}={value!r} does not appear in "
                    "the exported script, so its envelope cannot disclose it")
        state["B"]["source"] = source
        return (f"{len(source.splitlines())} lines, parses, imports no microclaw, "
                f"never prompts, carries the inlined accessor and array reader, "
                f"the pinned token, the preflight and all {len(recorded)} "
                f"recorded settings")

    @limb("C - the exported script RUNS and agrees with the live run",
          "an exported script that compiles but does not work (52b)")
    def limb_c():
        need("ctrl")
        if "B" not in state or "source" not in state.get("B", {}):
            raise NotExercised("stood down: limb B produced no runnable script")
        path = state["B"]["path"]
        # Run it in a CHILD process, from its own directory: the emitted script
        # resolves _HERE against its own file and must not need this process.
        # stdin is closed deliberately -- a subprocess inherits stdin even when
        # its output is captured, so a script that prompted would hang here.
        proc = subprocess.run(
            [sys.executable, str(path)], cwd=str(path.parent),
            capture_output=True, text=True, timeout=900, stdin=subprocess.DEVNULL,
        )
        state["C"] = {"proc": proc}
        (args.out / "C-stdout.txt").write_text(proc.stdout, encoding="utf-8")
        (args.out / "C-stderr.txt").write_text(proc.stderr, encoding="utf-8")
        if proc.returncode != 0:
            raise AssertionError(
                f"the exported script exited {proc.returncode}. stderr tail: "
                + " | ".join(proc.stderr.strip().splitlines()[-4:]))
        logs = sorted(path.parent.glob("A-live-hook*.json"))
        standalone = [p for p in logs if p != state["A"]["log"]]
        if not standalone:
            raise NotExercised(
                "the standalone run wrote no hook log beside the script; "
                f"looked for A-live-hook*.json in {path.parent}")
        records = read_hook_log(standalone[-1])
        focused = focused_records(records)
        live_focused = state["A"]["focused"]
        state["C"].update(records=records, focused=focused, log=standalone[-1])
        if len(focused) != len(live_focused):
            raise AssertionError(
                f"the standalone run completed {len(focused)} plugin runs against "
                f"the live run's {len(live_focused)}")
        mine, theirs = comparable(records), comparable(state["A"]["records"])
        if mine != theirs:
            raise AssertionError(
                "the standalone hook log differs from the live one. live="
                f"{theirs} standalone={mine}")
        return (f"exit 0; {len(focused)} plugin runs each; the hook logs are "
                f"IDENTICAL field by field; Z {[r['best_z_um'] for r in focused]}")

    @limb("D - post_hardware_hook_fn fired and the envelope disclosed",
          "a standalone run that saves frames while the plugin never runs, or "
          "an envelope that prints no disclosure")
    def limb_d():
        if "C" not in state:
            raise NotExercised("stood down: limb C did not run the exported script")
        out = state["C"]["proc"].stdout
        counts = [l for l in out.splitlines() if "HOOK ACQUISITION COUNTS" in l]
        if not counts:
            raise AssertionError(
                "the exported script printed no HOOK ACQUISITION COUNTS line, so "
                "its own accounting cannot be read")
        # Scored on hook_exposures, which the HOOK increments through its
        # reservation -- never on saved_frames, which arrives on the callback
        # CLAUDE.md's eighth contract says a predicate must not be gated on.
        exposures = [int(m) for m in re.findall(r"hook_exposures=\s*(\d+)", out)]
        if not exposures:
            raise AssertionError(f"could not read hook_exposures from: {counts}")
        if not any(n > 0 for n in exposures):
            raise AssertionError(
                "hook_exposures is 0 in the standalone run: real AcqEngJ never "
                "invoked post_hardware_hook_fn, so the frames are unfocused while "
                "the script reports success. That is 80b's lesson and the defect "
                "this block exists to prevent.")
        if DISCLOSURE not in out:
            raise AssertionError(
                "the envelope never printed the disclosure. This is the one "
                "exported script whose behaviour is not determined by its source, "
                "and per the 2026-08-17 decision the print is the only disclosure "
                "there is.")
        recorded = printed_snapshot(out, "Recorded settings snapshot:")
        live = printed_snapshot(out, "Live settings snapshot:")
        if recorded is None or live is None:
            raise AssertionError(
                "the envelope did not print both snapshots in a readable form; "
                f"recorded={recorded!r} live={live!r}")
        from_result = state["A"]["result"]["autofocus_settings_snapshot"]
        if recorded != from_result:
            raise AssertionError(
                "the script's printed RECORDED snapshot is not the one in the "
                f"session record: printed={recorded} record={from_result}")
        differs = "settings differ" in out
        if (recorded != live) != differs:
            raise AssertionError(
                "the difference flag disagrees with the two snapshots it "
                f"describes: equal={recorded == live} flagged={differs}")
        note = ("the live read agrees with the recorded one" if recorded == live
                else "the live read DIFFERS and the script flagged it, which is "
                     "information and not a refusal")
        return (f"hook_exposures {exposures}; the envelope disclosed and printed "
                f"both snapshots; {note}")

    @limb("H - live and standalone datasets carry the same frame identity",
          "two runs of the same plan writing different axes, or a dataset short "
          "of the frames its own accounting claims")
    def limb_h():
        if "C" not in state:
            raise NotExercised("stood down: limb C did not run the exported script")
        live_path = state["A"]["result"].get("dataset_path")
        if not live_path or not Path(live_path).exists():
            raise NotExercised(
                f"the live run reported no readable dataset ({live_path!r})")
        matches = [p for p in sorted(state["B"]["path"].parent.iterdir())
                   if p.is_dir() and p.name.startswith("run")]
        if not matches:
            raise NotExercised(
                "the standalone run wrote no dataset directory beside its script")
        try:
            live_axes = dataset_axes(Path(live_path))
            standalone_axes = dataset_axes(matches[-1])
        except Exception as exc:                    # noqa: BLE001 - reported
            raise NotExercised(
                f"neither dataset could be read through ndstorage: {exc}") from exc
        if not live_axes:
            raise AssertionError(f"the live dataset at {live_path} holds no frames")
        if live_axes != standalone_axes:
            raise AssertionError(
                "the two datasets carry different frame identities. live="
                f"{live_axes} standalone={standalone_axes}")
        if len({tuple(sorted(a.items())) for a in live_axes}) != len(live_axes):
            raise AssertionError(
                f"the live dataset has duplicate axes identities: {live_axes}")
        return (f"{len(live_axes)} uniquely indexed frames each, identical: "
                f"{live_axes}")

    limb("G - the hookless grid is byte-identical",
         "80c having changed a plain multiposition export")(score_hookless)

    if "home" in state:
        x0, y0, z0 = state["home"]
        print(f"\nEntry state was ({x0:.2f}, {y0:.2f}, {z0:.2f}); microclaw "
              f"restores it and writes nothing to Micro-Manager on exit. The "
              f"plugin moved Z itself -- check the stage before the next user.")
    return finish(args)


if __name__ == "__main__":
    raise SystemExit(main())
