import inspect
import json
from itertools import count
from pathlib import Path
from types import SimpleNamespace

import pytest

import numpy as np

from microclaw import autofocus, image_analysis, tools
from microclaw.tools_schema import TOOLS


class Guard:
    def __init__(self, root):
        self.root = Path(root)
        self.seen = []

    def resolve_in_workspace(self, path):
        self.seen.append(path)
        return str(self.root / path)


def call(name, params):
    return {"role": "assistant", "content": [
        {"type": "tool_use", "id": name, "name": name, "input": params}
    ]}


_call_ids = count()


def completed_call(name, params, result):
    # Unique per call: a repeated tool name in one record must keep its own result.
    tool_id = f"{name}-id-{next(_call_ids)}"
    return [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": tool_id, "name": name, "input": params}
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tool_id,
             "content": json.dumps(result)}
        ]},
    ]


def export(tmp_path, records):
    guard = Guard(tmp_path)
    result = tools.export_session_script(None, guard, "routine.py", records)
    return guard, result, (tmp_path / "routine.py").read_text()


def test_recorded_stage_and_acquisition_emit_standalone_script(tmp_path):
    guard, result, source = export(tmp_path, [
        call("move_stage_xy", {"x_um": 12.5, "y_um": -4.0}),
        call("run_timelapse", {
            "n_frames": 2, "interval_s": 0, "save_dir": "/data/session",
            "name": "cells", "channel": "DAPI", "exposure_ms": 10,
        }),
    ])

    assert guard.seen == ["routine.py"]
    assert result["emitted_calls"] == 2
    assert "core.set_xy_position(12.5, -4.0)" in source
    assert "num_time_points': 2" in source
    assert "acq.acquire(events)" in source
    assert "import microclaw" not in source
    assert "TOOL_REGISTRY" not in source
    compile(source, str(tmp_path / "routine.py"), "exec")


def test_committed_example_is_an_actual_export():
    fixture = Path(__file__).parent / "fixtures" / "exported_stage_acquisition.py"
    source = fixture.read_text()
    assert "core.set_xy_position(12.5, -4.0)" in source
    assert "acq.acquire(events)" in source
    assert "import microclaw" not in source


def test_real_smiley_session_fixture_shows_whole_routine_and_holes():
    source = (
        Path(__file__).parent / "fixtures" / "exported_smiley_session.py"
    ).read_text()
    assert source.count("# RECORDED TOOL:") == 39
    assert "# RECORDED TOOL: move_stage_xy" in source
    assert "# RECORDED TOOL: run_multiposition_acquisition\nevents =" in source
    assert "# NOT EMITTED: build_stage_coordinate_mosaic" in source
    assert "# RECORDED TOOL: set_device_property" in source.split(
        "# NOT EMITTED: build_stage_coordinate_mosaic", 1
    )[1]
    assert "import microclaw" not in source
    assert "TOOL_REGISTRY" not in source
    assert source.index("# NOT EMITTED:") == source.index(
        "# NOT EMITTED: build_stage_coordinate_mosaic"
    )


@pytest.mark.parametrize("fn", [
    image_analysis.ImageStats, image_analysis.snr, image_analysis.tenengrad,
    image_analysis.compute_stats,
])
def test_inlined_analysis_function_is_byte_identical_to_source(tmp_path, fn):
    _, _, source = export(tmp_path, [call("snap_and_analyze", {})])
    assert inspect.getsource(fn) in source


def test_autofocus_emitter_passes_recorded_inputs_without_rederiving(tmp_path):
    _, _, source = export(tmp_path, [call(
        "run_autofocus", {"z_range_um": 20, "z_step_um": 0.5}
    )])
    emitted_call = source.split("# RECORDED TOOL: run_autofocus", 1)[1]
    assert (
        "autofocus_result = _run_autofocus_passes("
        "mm, 20, 0.5, 'coarse_then_fine', 50)"
    ) in emitted_call
    assert "max(" not in emitted_call
    assert inspect.getsource(tools._run_autofocus_passes) in source


def test_inlined_analysis_constant_comes_from_module(tmp_path):
    _, _, source = export(tmp_path, [call("snap_and_analyze", {})])
    assignment = (
        "UNCALIBRATED_MIN_SNR_FALLBACK = "
        f"{image_analysis.UNCALIBRATED_MIN_SNR_FALLBACK!r}"
    )
    assert assignment in source


@pytest.mark.parametrize("fn", [
    autofocus.SweepResult, autofocus.AutofocusResult,
    autofocus.sweep_autofocus, autofocus.coarse_then_fine_autofocus,
    autofocus.single_sweep_autofocus,
])
def test_inlined_autofocus_is_byte_identical_to_source(tmp_path, fn):
    _, _, source = export(tmp_path, [call(
        "run_autofocus", {"z_range_um": 2, "z_step_um": 0.5}
    )])
    assert inspect.getsource(fn) in source


def test_records_are_injected_and_absent_from_published_schema(tmp_path):
    schema = next(tool for tool in TOOLS if tool["name"] == "export_session_script")
    assert "records" not in schema["input_schema"]["properties"]
    records = [call("move_stage_xy", {"x_um": 3, "y_um": 4})]
    result = json.loads(tools.execute_tool(
        "export_session_script", {"output_path": "injected.py"}, None,
        Guard(tmp_path), records=records,
    ))
    assert result["emitted_calls"] == 1
    assert "core.set_xy_position(3, 4)" in (tmp_path / "injected.py").read_text()


def test_unemittable_tool_refuses_and_script_cannot_run_past_it(tmp_path):
    _, _, source = export(tmp_path, [
        call("get_system_state", {}),
        call("build_stage_coordinate_mosaic", {
            "dataset_path": "/data/source", "output_path": "/data/out.tif",
            "axis_selection": {"channel": "DAPI"},
        }),
        call("move_stage_xy", {"x_um": 99, "y_um": 88}),
    ])

    assert "# No hardware-routine effect." in source
    assert "# NOT EMITTED: build_stage_coordinate_mosaic" in source
    assert "core.set_xy_position(99, 88)" in source

    prefix = source.replace("core = Core()", "core = object()")
    prefix = prefix.replace(
        "from pycromanager import Acquisition, Core, multi_d_acquisition_events",
        "Core = object\nAcquisition = object\nmulti_d_acquisition_events = object",
    )
    with pytest.raises(RuntimeError, match="NOT EMITTED: build_stage_coordinate_mosaic"):
        exec(compile(prefix, "routine.py", "exec"), {"__file__": "routine.py"})


def _mosaic_dose_calls(source):
    section = source.split("# RECORDED TOOL: build_stage_coordinate_mosaic", 1)[1]
    section = section.split("# RECORDED TOOL:", 1)[0]
    return section.count("Acquisition(") + section.count("snap_image")


def _assert_zero_mosaic_dose(source):
    assert _mosaic_dose_calls(source) == 0


def test_dose_detector_fails_known_bad_mosaic_rendered_as_acquisition(tmp_path, monkeypatch):
    """Known-bad direction: the dose detector sees a fabricated mosaic exposure."""
    monkeypatch.setattr(
        tools.build_stage_coordinate_mosaic, "_microclaw_emitter",
        lambda _p: "with Acquisition(directory='bad', name='mosaic') as acq:\n    acq.acquire([])",
        raising=False,
    )
    _, _, source = export(tmp_path, [call("build_stage_coordinate_mosaic", {
        "dataset_path": "/data/already-acquired", "output_path": "/data/mosaic.tif",
        "axis_selection": {"channel": "DAPI"},
    })])
    with pytest.raises(AssertionError):
        _assert_zero_mosaic_dose(source)


def _multiposition_call(positions, results, status):
    return completed_call(
        "run_multiposition_acquisition",
        {"protocol": "timelapse", "positions": positions, "save_dir": "/data",
         "name": "run", "protocol_params": {"n_frames": 1, "interval_s": 0}},
        {"status": status, "results": results},
    )


def test_failed_and_partial_acquisitions_are_not_replayed_as_successes(tmp_path):
    """M5 rig gate, 2026-08-06. The session made five multiposition calls; two
    completed, two failed on trigger arming, one was refused by the channel-axis
    guard. The export emitted all five, so the standalone script imaged each
    position five times instead of twice -- 2.5x the session's dose on a
    bleaching sample -- and then drove a channel axis the rig cannot drive.

    `_recorded_tool_calls` attached every result and never asked whether the
    call succeeded. This is the shape of that session, in order.
    """
    positions = [{"name": "pos1", "x_um": 1.0, "y_um": 2.0},
                 {"name": "pos2", "x_um": 3.0, "y_um": 4.0},
                 {"name": "pos3", "x_um": 5.0, "y_um": 6.0}]
    ok = [{"position": p["name"], "x_um": p["x_um"], "y_um": p["y_um"],
           "dataset_path": f"/data/{p['name']}"} for p in positions]

    records = []
    records += _multiposition_call(positions, ok, "3/3 positions completed.")
    # Trigger line not armed: every position failed.
    records += _multiposition_call(
        positions,
        [{"position": p["name"], "error": "trigger line is not armed"}
         for p in positions],
        "0/3 positions completed.",
    )
    # Refused outright by the channel-axis guard: execute_tool reports `error`.
    records += completed_call(
        "run_multiposition_acquisition",
        {"protocol": "timelapse", "positions": positions, "channel": "640",
         "save_dir": "/data", "name": "run",
         "protocol_params": {"n_frames": 1, "interval_s": 0}},
        {"error": "Safety constraint prevented this action: This acquisition "
                  "cannot drive a channel axis for '640'"},
    )
    # The hard case: two of three positions completed.
    records += _multiposition_call(
        positions, [*ok[:2], {"position": "pos3", "error": "trigger sequence is 0"}],
        "2/3 positions completed.",
    )
    records += _multiposition_call(positions, ok, "3/3 positions completed.")

    _, result, source = export(tmp_path, records)

    # Two acquisitions ran; two are emitted. Not five.
    assert source.count("acq.acquire(events)") == 2
    assert result["emitted_calls"] == 2
    assert source.count("# NOT EMITTED: run_multiposition_acquisition") == 3

    # The refused one never reaches the rig's absent channel group. It may name
    # '640' in its refusal text -- what must not exist is an executable event.
    assert "'channel_group': 'Channel'" not in source
    assert "channels': ['640']" not in source

    refusals = [line for line in source.splitlines() if "# NOT EMITTED" in line]
    assert len(refusals) == 3, refusals
    # Total failure names the rig's reason.
    assert any("trigger line is not armed" in line for line in refusals), refusals
    # Partial success refuses too, and names the position that did not complete.
    assert any("1 of 3 recorded results entries did not complete" in line
               and "'pos3'" in line for line in refusals), refusals

    # And the script stops at the first step it could not emit, having run the
    # one that did succeed -- exercised, not merely compiled.
    visited, acquisitions = [], []

    class StageCore:
        def set_xy_position(self, x, y): visited.append((x, y))
        def set_position(self, z): pass
        def wait_for_device(self, _device): pass

    class FakeAcquisition:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def acquire(self, events): acquisitions.append(events)

    executable = source.replace(
        "from pycromanager import Acquisition, Core, multi_d_acquisition_events", "")
    with pytest.raises(RuntimeError, match="NOT EMITTED: run_multiposition_acquisition"):
        exec(compile(executable, "routine.py", "exec"), {
            "__file__": str(tmp_path / "routine.py"), "Core": StageCore,
            "Acquisition": FakeAcquisition, "multi_d_acquisition_events": dict,
        })
    # It stopped at the second call, so only the first acquisition's three
    # positions were imaged -- not the failed run's, and not the refused run's.
    assert visited == [(1.0, 2.0), (3.0, 4.0), (5.0, 6.0)]
    assert len(acquisitions) == 3


def test_successful_move_reporting_error_um_is_still_emitted(tmp_path):
    """`error_um` is a measurement a successful move reports, not a failure.

    Matching it would refuse working steps, so the rule keys off the exact
    `error` key only.
    """
    _, _, source = export(tmp_path, completed_call(
        "go_to_position", {"name": "target"},
        {"status": "Moved to 'target'.", "name": "target", "x_um": 1.5,
         "y_um": -2.5, "error_um": 0.02}))
    assert "# NOT EMITTED" not in source
    assert "core.set_xy_position(1.5, -2.5)" in source


def test_dose_detector_passes_known_good_recorded_offline_mosaic(tmp_path):
    """Known-good direction: the saved-NDTiff read contributes zero exposures."""
    _, _, source = export(tmp_path, [
        call("build_stage_coordinate_mosaic", {
            "dataset_path": "/data/already-acquired",
            "output_path": "/data/mosaic.tif",
            "axis_selection": {"channel": "DAPI"},
        }),
        call("run_timelapse", {
            "n_frames": 1, "interval_s": 0, "save_dir": "/data",
        }),
    ])

    assert "# NOT EMITTED: build_stage_coordinate_mosaic" in source
    _assert_zero_mosaic_dose(source)
    assert "Acquisition(directory=str(_HERE)" in source  # later acquisition rendered


M5_CHANNEL_RESULT = {
    "status": "Channel set to '640'.",
    "writes": 4,
    # The reversed M5 slot order, exactly as execute_channel_plan recorded it.
    "effects": [
        ["iChrome-MLE-TCP", "Laser 4: 1. Enable", "0"],
        ["iChrome-MLE-TCP", "Laser 3: 1. Enable", "0"],
        ["iChrome-MLE-TCP", "Laser 2: 1. Enable", "0"],
        ["iChrome-MLE-TCP", "Laser 1: 1. Enable", "1"],
    ],
    "channel_source": "emu-laser-map",
}


def test_set_channel_emits_the_recorded_writes_in_order(tmp_path):
    """Both sources emit as writes; neither is rebuilt from a live rig.

    A rig with no "Channel" config group is exactly the rig whose channels are
    not presets, so `core.set_config('Channel', ...)` would fail there. Assert
    against the recorded effect list, not against a plan re-derived here.
    """
    _, _, source = export(tmp_path, completed_call(
        "set_channel", {"preset": "640"}, M5_CHANNEL_RESULT))

    assert "# NOT EMITTED" not in source
    assert "set_config('Channel'" not in source
    written = [line for line in source.splitlines()
               if line.startswith("core.set_property(")]
    assert written == [
        f"core.set_property('iChrome-MLE-TCP', 'Laser {i}: 1. Enable', {v!r})"
        for i, v in ((4, "0"), (3, "0"), (2, "0"), (1, "1"))
    ]


FLOAT_CHANNEL_RESULT = {
    "status": "Channel set to 'FITC'.",
    "writes": 2,
    "effects": [
        ["Emission", "Label", "Chroma-HQ535"],
        ["Camera", "Exposure", "10"],
    ],
    "channel_source": "config-group",
}


class FakeCore:
    """Enough core to execute an emitted channel switch.

    `reformat` reproduces the measured driver behaviour that broke the first
    version of this emitter: a Float property requested as "10" reads back
    "10.0000". `liar` returns a genuinely different value for one pair.
    """

    def __init__(self, types=None, reformat=(), liar=None):
        self.values, self.calls = {}, []
        self.types = dict(types or {})
        self.reformat, self.liar = set(reformat), liar

    def set_property(self, d, p, v):
        self.calls.append(("set", d, p))
        self.values[(d, p)] = f"{float(v):.4f}" if (d, p) in self.reformat else str(v)

    def wait_for_device(self, d):
        self.calls.append(("wait", d))

    def get_property_type(self, d, p):
        return self.types.get((d, p), "String")

    def get_property(self, d, p):
        if self.liar and (d, p) == self.liar[0]:
            return self.liar[1]
        return self.values[(d, p)]


def run_emitted(source, core, tmp_path):
    executable = source.replace(
        "from pycromanager import Acquisition, Core, multi_d_acquisition_events", ""
    )
    exec(compile(executable, "routine.py", "exec"), {
        "__file__": str(tmp_path / "routine.py"), "Core": lambda: core,
        "Acquisition": object, "multi_d_acquisition_events": dict,
    })


def test_emitted_channel_switch_waits_and_verifies_like_the_executor(tmp_path):
    """The emitted script runs against a fake core and refuses a bad read-back."""
    _, _, source = export(tmp_path, completed_call(
        "set_channel", {"preset": "640"}, M5_CHANNEL_RESULT))

    good = FakeCore()
    run_emitted(source, good, tmp_path)
    assert good.values[("iChrome-MLE-TCP", "Laser 1: 1. Enable")] == "1"
    assert good.calls.count(("wait", "iChrome-MLE-TCP")) == 4

    lying = FakeCore(liar=(("iChrome-MLE-TCP", "Laser 1: 1. Enable"), "0"))
    with pytest.raises(Exception, match="Read-back verification failed"):
        run_emitted(source, lying, tmp_path)


def test_emitted_float_read_back_accepts_driver_reformatting(tmp_path):
    """Coordinator review, 2026-08-06. The first emitter compared read-back as

        assert str(core.get_property('Camera', 'Exposure')) == '10'

    which is not what `_verify_property` does. A Float property is compared
    numerically because Micro-Manager reformats it -- "10" reads back "10.0000",
    measured on a rig (design/33 Phase 4). Any `Channel` preset carrying a
    camera exposure would therefore have exported a script that died partway
    through, on the rig, standalone, with nothing around to explain it. That is
    block 41b's failure mode exactly, and none of the M5 enable fixtures could
    catch it because they are all categorical.
    """
    _, _, source = export(tmp_path, completed_call(
        "set_channel", {"preset": "FITC"}, FLOAT_CHANNEL_RESULT))

    exposure = ("Camera", "Exposure")
    reformatting = FakeCore(types={exposure: "Float"}, reformat=[exposure])
    run_emitted(source, reformatting, tmp_path)                 # must not raise
    assert reformatting.values[exposure] == "10.0000"

    # A genuinely wrong value is still refused, so the tolerance is not a hole.
    wrong = FakeCore(types={exposure: "Float"}, liar=(exposure, "11.0000"))
    with pytest.raises(Exception, match="Read-back verification failed"):
        run_emitted(source, wrong, tmp_path)

    # And the same reformatting on a String property is still a mismatch --
    # the emitted rule keys off the type, exactly as the executor's does.
    stringy = FakeCore(types={exposure: "String"}, reformat=[exposure])
    with pytest.raises(Exception, match="Read-back verification failed"):
        run_emitted(source, stringy, tmp_path)


@pytest.mark.parametrize("name", [
    "ChannelPlanError", "_property_type_name", "_verify_property",
])
def test_inlined_channel_verification_is_byte_identical_to_source(tmp_path, name):
    """The emitted check must *be* the executor's, not a paraphrase of it."""
    from microclaw import authorization

    _, _, source = export(tmp_path, completed_call(
        "set_channel", {"preset": "640"}, M5_CHANNEL_RESULT))
    assert inspect.getsource(getattr(authorization, name)) in source


def test_channel_verification_is_absent_when_nothing_replayed_writes(tmp_path):
    _, _, source = export(tmp_path, completed_call(
        "set_channel", {"preset": "DAPI"},
        {"status": "Channel set to 'DAPI'.", "config_group": "Channel"}))
    assert "_verify_property" not in source


def test_map_less_channel_delegation_emits_the_set_config_that_ran(tmp_path):
    _, _, source = export(tmp_path, completed_call(
        "set_channel", {"preset": "DAPI"},
        {"status": "Channel set to 'DAPI'.", "config_group": "Channel"}))
    assert "core.set_config('Channel', 'DAPI')" in source
    assert "core.wait_for_config('Channel', 'DAPI')" in source


def test_set_channel_without_a_recorded_result_refuses_rather_than_guessing(tmp_path):
    _, _, source = export(tmp_path, [call("set_channel", {"preset": "DAPI"})])
    assert "# NOT EMITTED: set_channel" in source
    assert "no executed channel effects" in source
    assert "set_config('Channel'" not in source


def test_go_to_position_emits_coordinates_from_recorded_result(tmp_path):
    records = completed_call(
        "go_to_position", {"name": "target"},
        {"status": "Moved", "name": "target", "x_um": 1.5,
         "y_um": 2.5, "z_um": 3.5},
    )
    _, _, source = export(tmp_path, records)
    assert "core.set_xy_position(1.5, 2.5)" in source
    assert "core.set_position(3.5)" in source


def test_observation_only_hook_emits_hardware_but_decision_hook_refuses(tmp_path):
    base = {
        "protocol": "timelapse",
        "positions": [{"name": "a", "x_um": 1, "y_um": 2, "z_um": 3}],
        "protocol_params": {"n_frames": 1, "interval_s": 0},
        "save_dir": "/data", "name": "run",
    }
    _, _, observed = export(tmp_path, [call(
        "run_multiposition_acquisition", {**base, "hook_strategy": "snr_observer"}
    )])
    assert "# NOT EMITTED:" not in observed
    assert "xyz_positions': [(1, 2, 3)]" in observed

    _, _, deciding = export(tmp_path, [call(
        "run_multiposition_acquisition", {**base, "hook_strategy": "position_filter"}
    )])
    assert "# NOT EMITTED: run_multiposition_acquisition — hooked acquisition" in deciding
    assert "HookBase" in deciding


@pytest.mark.parametrize("name", [
    "mark_position", "clear_position_list", "delete_position",
    "save_position_list", "load_position_list", "import_mm_positions",
])
def test_position_list_session_state_emits_nothing(tmp_path, name):
    _, result, source = export(tmp_path, [call(name, {})])
    assert f"# RECORDED TOOL: {name}\n# No hardware-routine effect." in source
    assert "# NOT EMITTED:" not in source
    assert result["emitted_calls"] == 0


def _position_snapshot_records(positions):
    return completed_call(
        "get_position_list", {}, {"positions": positions, "count": len(positions)},
    )


def _named_hooked_run(names):
    return call("run_multiposition_acquisition", {
        "protocol": "timelapse", "position_names": names,
        "protocol_params": {"n_frames": 1, "interval_s": 0},
        "hook_strategy": "snr_observer", "save_dir": "/recorded/session",
        "name": "mosaic_stack",
    })


def test_named_multiposition_resolves_snapshot_and_emits_session_coordinates(tmp_path):
    positions = [
        {"name": "pos_1", "x_um": 399.9, "y_um": 300.0, "z_um": 42.987},
        {"name": "pos_2", "x_um": 369.9, "y_um": 330.0, "z_um": 42.987},
        {"name": "pos_3", "x_um": 339.9, "y_um": 270.0, "z_um": 42.987},
    ]
    _, _, source = export(
        tmp_path,
        _position_snapshot_records(positions)
        + [_named_hooked_run(["pos_1", "pos_2", "pos_3"])],
    )
    assert "# NOT EMITTED: run_multiposition_acquisition" not in source
    assert "xyz_positions': [(399.9, 300.0, 42.987), (369.9, 330.0, 42.987), (339.9, 270.0, 42.987)]" in source


def test_named_multiposition_refuses_absent_name(tmp_path):
    records = _position_snapshot_records([
        {"name": "pos_1", "x_um": 1, "y_um": 2, "z_um": 3},
    ]) + [_named_hooked_run(["missing"])]
    _, _, source = export(tmp_path, records)
    assert "could not resolve named position 'missing' unambiguously" in source
    assert "Acquisition(directory=" not in source


def _marked(name, x_um, y_um, z_um):
    return completed_call(
        "mark_position", {"name": name},
        {"status": f"Position {name!r} marked.", "x_um": x_um, "y_um": y_um,
         "z_um": z_um, "imaged": False, "stage_moved": True},
    )


def test_named_multiposition_takes_the_later_mark_over_the_snapshot(tmp_path):
    records = _position_snapshot_records([
        {"name": "pos_1", "x_um": 1, "y_um": 2, "z_um": 3},
    ])
    records += _marked("pos_1", 9, 8, 7)
    records += [_named_hooked_run(["pos_1"])]
    _, _, source = export(tmp_path, records)
    assert "# NOT EMITTED: run_multiposition_acquisition" not in source
    assert "xyz_positions': [(9, 8, 7)]" in source


def test_named_multiposition_resolves_marks_without_any_snapshot(tmp_path):
    records = _marked("pos_1", 10.0, 20.0, 1.0) + _marked("pos_2", 30.0, 40.0, 2.0)
    records += [_named_hooked_run(["pos_1", "pos_2"])]
    _, _, source = export(tmp_path, records)
    assert "# NOT EMITTED: run_multiposition_acquisition" not in source
    assert "xyz_positions': [(10.0, 20.0, 1.0), (30.0, 40.0, 2.0)]" in source


def test_named_multiposition_still_refuses_a_name_never_marked(tmp_path):
    records = _marked("pos_1", 10.0, 20.0, 1.0) + [_named_hooked_run(["pos_2"])]
    _, _, source = export(tmp_path, records)
    assert "could not resolve named position 'pos_2' unambiguously" in source
    assert "xyz_positions" not in source


def test_named_multiposition_refuses_a_marked_then_deleted_position(tmp_path):
    records = _marked("pos_1", 10.0, 20.0, 1.0)
    records += completed_call(
        "delete_position", {"name": "pos_1"},
        {"status": "Position 'pos_1' deleted.", "count": 0},
    )
    records += [_named_hooked_run(["pos_1"])]
    _, _, source = export(tmp_path, records)
    assert "could not resolve named position 'pos_1' unambiguously" in source
    assert "xyz_positions" not in source


def test_named_multiposition_refuses_after_a_failed_mark(tmp_path):
    records = _marked("pos_1", 10.0, 20.0, 1.0)
    records += completed_call(
        "mark_position", {"name": "pos_2"},
        {"status": "Cancelled by the user.", "x_um": 5, "y_um": 6, "z_um": 7},
    )
    records += [_named_hooked_run(["pos_1"])]
    _, _, source = export(tmp_path, records)
    assert "could not resolve named position 'pos_1' unambiguously" in source
    assert "xyz_positions" not in source


@pytest.mark.parametrize("records", [
    [call("run_timelapse", {"n_frames": 1, "interval_s": 0,
                            "save_dir": "/recorded"})],
    [call("run_zstack", {"z_start_um": 0, "z_end_um": 1, "z_step_um": 1,
                         "save_dir": "/recorded"})],
    [call("run_multiposition_acquisition", {
        "protocol": "timelapse",
        "positions": [{"name": "p", "x_um": 1, "y_um": 2, "z_um": 3}],
        "protocol_params": {"n_frames": 1, "interval_s": 0},
        "hook_strategy": "snr_observer", "save_dir": "/recorded",
    })],
])
def test_every_acquisition_emitter_anchors_beside_script(tmp_path, records):
    _, _, source = export(tmp_path, records)
    assert "Acquisition(directory=str(_HERE)" in source
    assert "directory='/recorded'" not in source


def test_export_without_acquisition_does_not_define_unused_here(tmp_path):
    _, _, source = export(tmp_path, [call("move_stage_xy", {"x_um": 1, "y_um": 2})])
    assert "_HERE" not in source


def test_result_derived_multiposition_refuses_incomplete_coordinates(tmp_path):
    records = completed_call(
        "run_multiposition_acquisition",
        {
            "protocol": "timelapse",
            "protocol_params": {"n_frames": 1, "interval_s": 0},
            "save_dir": "/data", "name": "run",
        },
        {"results": [
            {"position": "complete", "x_um": 1, "y_um": 2},
            {"position": "missing-y", "x_um": 3},
        ]},
    )
    _, _, source = export(tmp_path, records)
    reason = "the record contains no resolved position coordinates"
    assert f"# NOT EMITTED: run_multiposition_acquisition — {reason}" in source
    assert "for position in" not in source
    assert "Acquisition(directory=" not in source


def _exec_acquisition_source(source, script_path=None):
    class Core:
        def __init__(self):
            self.moves = []
            self.z_moves = []

        def set_xy_position(self, x, y): self.moves.append((x, y))
        def set_position(self, z): self.z_moves.append(z)
        def set_exposure(self, _exposure): pass

    acquired = []

    class Acquisition:
        def __init__(self, **kwargs): self.kwargs = kwargs
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def acquire(self, events): acquired.append((self.kwargs, events))

    core = Core()
    executable = source.replace(
        "from pycromanager import Acquisition, Core, multi_d_acquisition_events",
        "",
    )
    exec(compile(executable, "routine.py", "exec"), {
        "__file__": str(script_path or Path("routine.py").resolve()),
        "Core": lambda: core,
        "Acquisition": Acquisition,
        "multi_d_acquisition_events": lambda **kwargs: kwargs,
    })
    return core, acquired


def test_result_derived_per_position_protocol_executes_without_runtime_state(
    tmp_path, monkeypatch,
):
    records = completed_call(
        "run_multiposition_acquisition",
        {
            "protocol": "timelapse",
            "protocol_params": {"n_frames": 2, "interval_s": 0},
            "save_dir": "~/microclaw_data/multipos_3sites", "name": "run",
            "mark_positions": False,
        },
        {"results": [
            {"position": "pos_1", "x_um": 50.0, "y_um": 0.2, "z_um": 6.318,
             "status": "Timelapse complete.", "dataset_path": "/session/pos_1",
             "declared_illumination_properties": [{"device": "Laser", "value": "On"}]},
            {"position": "pos_2", "x_um": 150.0, "y_um": 0.2, "z_um": 6.4,
             "status": "Timelapse complete.", "dataset_path": "/session/pos_2"},
        ]},
    )
    _, _, source = export(tmp_path, records)
    assert "'name': 'pos_1'" in source
    assert "'position': 'pos_1'" not in source
    assert "dataset_path" not in source
    assert "declared_illumination_properties" not in source
    assert "Timelapse complete" not in source
    assert "~" not in source

    script_path = tmp_path / "export" / "session_script.py"
    cwd = tmp_path / "unrelated-cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    core, acquired = _exec_acquisition_source(source, script_path)
    assert core.moves == [(50.0, 0.2), (150.0, 0.2)]
    assert core.z_moves == [6.318, 6.4]
    assert [entry[0]["name"] for entry in acquired] == ["pos_1", "pos_2"]
    assert [entry[0]["directory"] for entry in acquired] == [
        str(script_path.parent / "pos_1"),
        str(script_path.parent / "pos_2"),
    ]


def test_tile_per_position_protocol_executes_against_fake_core(tmp_path):
    _, _, source = export(tmp_path, [call("run_tile_acquisition", {
        "rows": 1, "cols": 2, "step_um": 10,
        "center_x_um": 5, "center_y_um": 20,
        "protocol": "timelapse",
        "protocol_params": {"n_frames": 1, "interval_s": 0},
        "save_dir": "/data", "name": "tile", "mark_positions": False,
    })])
    core, acquired = _exec_acquisition_source(source)
    assert core.moves == [(0.0, 20.0), (10.0, 20.0)]
    assert [entry[0]["name"] for entry in acquired] == [
        "tile_r0_c0", "tile_r0_c1",
    ]


def test_realistic_emitted_routine_runs_to_completion_against_fake_core(tmp_path):
    """Move, snap/analyze, autofocus, and unhooked acquisition all execute."""
    records = [call("start_live_view", {})]
    records += completed_call("snap_and_analyze", {}, {"min_snr": 3.1})
    records += completed_call(
        "run_autofocus",
        {"z_range_um": 2, "z_step_um": 0.5, "settle_ms": 0},
        {"converged": False},
    )
    records += [call("run_multiposition_acquisition", {
        "protocol": "timelapse",
        "positions": [
            {"name": "a", "x_um": 1, "y_um": 2, "z_um": 0},
            {"name": "b", "x_um": 3, "y_um": 4, "z_um": 0},
        ],
        "protocol_params": {"n_frames": 1, "interval_s": 0},
        "save_dir": "/data", "name": "run",
    }), call("stop_live_view", {})]
    _, _, source = export(tmp_path, records)
    assert "# NOT EMITTED:" not in source

    class Core:
        def __init__(self):
            self.z = 0.0
            self.snaps = 0
            self.moves = []

        def get_focus_device(self): return "Z"
        def get_position(self): return self.z
        def set_position(self, z): self.z = float(z)
        def set_xy_position(self, x, y): self.moves.append((x, y))
        def wait_for_device(self, _device): pass
        def snap_image(self): self.snaps += 1
        def get_bytes_per_pixel(self): return 2
        def get_number_of_components(self): return 1
        def get_tagged_image(self):
            image = np.array([[0, 10], [10, 0]], dtype=np.uint16)
            return SimpleNamespace(pix=image, tags={"Width": 2, "Height": 2})

    acquired = []

    class Acquisition:
        def __init__(self, **kwargs): self.kwargs = kwargs
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def acquire(self, events): acquired.append(events)

    core = Core()
    executable = source.replace(
        "from pycromanager import Acquisition, Core, multi_d_acquisition_events",
        "",
    )
    exec(compile(executable, "routine.py", "exec"), {
        "__file__": str(tmp_path / "routine.py"),
        "Core": lambda: core,
        "Acquisition": Acquisition,
        "multi_d_acquisition_events": lambda **kwargs: kwargs,
    })
    assert core.snaps >= 3
    assert core.moves == [(1, 2), (3, 4)]
    assert len(acquired) == 2


def test_adaptive_runs_refuse_with_the_architectural_reason(tmp_path):
    """M5 rig gate round 4, 2026-08-06. The exported script stopped at

        NOT EMITTED: run_adaptive_survey - no standalone emitter has been
        implemented for this tool

    which understates it. An adaptive run's events are chosen at runtime by its
    hook, so it is not an unwritten emitter -- it is the same architectural
    refusal as the offline mosaic. Emitting the positions it happened to visit
    would silently convert an adaptive run into a fixed one, which is the
    reconstruct-from-memory defect this block exists to remove.
    """
    for tool in ("run_adaptive_survey", "run_adaptive_zstack",
                 "run_adaptive_timelapse"):
        _, _, source = export(tmp_path, [call(tool, {})])
        assert f"# NOT EMITTED: {tool}" in source
        assert "chosen at runtime by its hook" in source
        assert "no standalone emitter has been implemented" not in source


@pytest.mark.parametrize("records", [
    pytest.param([call("snap_and_analyze", {})], id="analysis"),
    pytest.param(
        [call("run_autofocus", {"z_range_um": 2, "z_step_um": 0.5})], id="autofocus"
    ),
    pytest.param(
        completed_call("set_channel", {"preset": "640"}, M5_CHANNEL_RESULT),
        id="channel-verification",
    ),
])
def test_emitted_inline_defines_every_name_it_uses(tmp_path, records):
    """Recurrence guard for the block-13/41b integration defect (2026-08-06).

    `_analysis_source` inlines a hand-listed set of helpers. Block 13 added
    `snr_validity()` to `image_analysis` and `compute_stats` began calling it;
    both branches stayed green alone, and merged they emitted scripts that
    raised `NameError: name 'snr_validity' is not defined` at runtime. Pin the
    invariant structurally rather than by extending the list again: every global
    an inlined block references must be defined in the emitted source.

    **Parametrized over every record that triggers an inline**, not just the
    analysis one. Block 41c added `_channel_verification_source` and this guard
    could not see it, which is the position blocks 13 and 41b were both in
    before they merged. A byte-identity test does not close that: it still
    passes when the inlined function starts calling a helper that was never
    inlined, and the script `NameError`s on the rig. Add a param here whenever
    the exporter learns to inline something new.
    """
    import ast, builtins

    _, _, source = export(tmp_path, records)
    tree = ast.parse(source)
    defined = {n.name for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
    defined |= {t.id for n in ast.walk(tree) if isinstance(n, ast.Assign)
                for t in n.targets if isinstance(t, ast.Name)}
    defined |= {a.asname or a.name.split(".")[0]
                for n in ast.walk(tree)
                if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}

    # Annotations never evaluate. The emitted script opens with
    # `from __future__ import annotations`, so `ctrl: MicroscopeController` is a
    # string at runtime, not a load -- scanning it would fail a script that runs
    # perfectly (it did, the moment this guard was widened past the analysis).
    # The invariant is "would this NameError on the rig", so model that.
    annotated: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            slots = [arg.annotation for arg in (
                *args.posonlyargs, *args.args, *args.kwonlyargs,
                *(a for a in (args.vararg, args.kwarg) if a is not None),
            )] + [node.returns]
        elif isinstance(node, ast.AnnAssign):
            slots = [node.annotation]
        else:
            continue
        for slot in slots:
            if slot is not None:
                annotated.update(id(item) for item in ast.walk(slot))

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            continue
        # Every name bound anywhere in the body: assignment, tuple unpacking,
        # for-targets, comprehensions, with-as. Store context covers them all.
        local = {a.arg for f in ast.walk(node)
                 if isinstance(f, ast.FunctionDef) for a in f.args.args}
        local |= {n.id for n in ast.walk(node)
                  if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
        for name in (n.id for n in ast.walk(node)
                     if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
                     and id(n) not in annotated):
            assert (name in defined or name in local
                    or hasattr(builtins, name)), (
                f"emitted script references {name!r} but never defines it")
