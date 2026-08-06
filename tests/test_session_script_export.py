import inspect
import json
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


def completed_call(name, params, result):
    tool_id = f"{name}-id"
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
        exec(compile(prefix, "routine.py", "exec"), {})


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
    assert "Acquisition(directory='/data'" in source  # later recorded acquisition rendered


def test_set_channel_refuses_authorization_plan_instead_of_guessing(tmp_path):
    _, _, source = export(tmp_path, [call("set_channel", {"preset": "DAPI"})])
    assert "# NOT EMITTED: set_channel" in source
    assert "authorization-map channel plan" in source
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


def _exec_acquisition_source(source):
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
        "Core": lambda: core,
        "Acquisition": Acquisition,
        "multi_d_acquisition_events": lambda **kwargs: kwargs,
    })
    return core, acquired


def test_result_derived_per_position_protocol_executes_without_runtime_state(tmp_path):
    records = completed_call(
        "run_multiposition_acquisition",
        {
            "protocol": "timelapse",
            "protocol_params": {"n_frames": 2, "interval_s": 0},
            "save_dir": "/data", "name": "run", "mark_positions": False,
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

    core, acquired = _exec_acquisition_source(source)
    assert core.moves == [(50.0, 0.2), (150.0, 0.2)]
    assert core.z_moves == [6.318, 6.4]
    assert [entry[0]["name"] for entry in acquired] == ["pos_1", "pos_2"]


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
        "Core": lambda: core,
        "Acquisition": Acquisition,
        "multi_d_acquisition_events": lambda **kwargs: kwargs,
    })
    assert core.snaps >= 3
    assert core.moves == [(1, 2), (3, 4)]
    assert len(acquired) == 2
