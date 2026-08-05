import inspect
import json
from pathlib import Path

import pytest

from microclaw import image_analysis, tools
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
    assert "# NOT EMITTED: run_multiposition_acquisition" in source
    assert "# NOT EMITTED: build_stage_coordinate_mosaic" in source
    assert "# RECORDED TOOL: set_device_property" in source.split(
        "# NOT EMITTED: build_stage_coordinate_mosaic", 1
    )[1]
    assert "import microclaw" not in source
    assert "TOOL_REGISTRY" not in source


@pytest.mark.parametrize("fn", [
    image_analysis.ImageStats, image_analysis.snr, image_analysis.tenengrad,
    image_analysis.compute_stats,
])
def test_inlined_analysis_function_is_byte_identical_to_source(tmp_path, fn):
    _, _, source = export(tmp_path, [])
    assert inspect.getsource(fn) in source


def test_inlined_analysis_constant_comes_from_module(tmp_path):
    _, _, source = export(tmp_path, [])
    assignment = (
        "UNCALIBRATED_MIN_SNR_FALLBACK = "
        f"{image_analysis.UNCALIBRATED_MIN_SNR_FALLBACK!r}"
    )
    assert assignment in source


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
