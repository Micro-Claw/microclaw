import inspect
from pathlib import Path

import pytest

from microclaw import image_analysis, tools


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


@pytest.mark.parametrize("fn", [
    image_analysis.snr, image_analysis.tenengrad, image_analysis.compute_stats,
])
def test_inlined_analysis_function_is_byte_identical_to_source(tmp_path, fn):
    _, _, source = export(tmp_path, [])
    assert inspect.getsource(fn) in source


def test_unemittable_tool_refuses_and_script_cannot_run_past_it(tmp_path):
    _, _, source = export(tmp_path, [
        call("get_system_state", {}),
        call("move_stage_xy", {"x_um": 99, "y_um": 88}),
    ])

    assert "# NOT EMITTED: get_system_state" in source
    assert "raise RuntimeError('NOT EMITTED: get_system_state')" in source
    assert "core.set_xy_position(99, 88)" not in source

    prefix = source.replace("core = Core()", "core = object()")
    prefix = prefix.replace(
        "from pycromanager import Acquisition, Core, multi_d_acquisition_events",
        "Core = object\nAcquisition = object\nmulti_d_acquisition_events = object",
    )
    with pytest.raises(RuntimeError, match="NOT EMITTED: get_system_state"):
        exec(compile(prefix, "routine.py", "exec"), {})


def test_offline_mosaic_never_becomes_an_acquisition(tmp_path):
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
    assert "Acquisition(directory=" not in source
    assert "acq.acquire" not in source
