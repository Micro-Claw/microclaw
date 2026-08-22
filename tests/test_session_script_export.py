import inspect
import json
import queue
import re
from itertools import count
from pathlib import Path
from types import SimpleNamespace

import pytest

import numpy as np

from microclaw import autofocus, controller, image_analysis, tools
from microclaw.tools_schema import TOOLS


class Guard:
    def __init__(self, root):
        self.root = Path(root)
        self.seen = []
        self._c = SimpleNamespace(
            stage=SimpleNamespace(
                x_min=None, x_max=None, y_min=None, y_max=None,
                z_min=None, z_max=None,
            ),
            camera=SimpleNamespace(max_exposure_ms=None),
        )

    @property
    def analysis_min_snr(self):
        return None

    def resolve_in_workspace(self, path):
        self.seen.append(path)
        return str(self.root / path)


_MULTILINE_BRIDGE_ERROR = (
    'java.lang.Exception: Error in device "Thorlabs ELL17/ELL20": '
    "Serial command failed.  Is the device connected to the serial port? (14)\n"
    "mmcorej.MMCoreJJNI.CMMCore_setPosition__SWIG_0(Native Method)\n"
    "org.micromanager.pyjavaz.ZMQServer.runMethod(ZMQServer.java:431)"
)


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
    return guard, result, (tmp_path / "routine.py").read_text(encoding="utf-8")


def test_exported_non_ascii_source_requires_utf8_when_read_back(tmp_path):
    _, _, source = export(tmp_path, [call("snap_and_analyze", {})])
    expected = inspect.getsource(image_analysis.tenengrad)
    wrong = (tmp_path / "routine.py").read_text(encoding="cp1252")

    assert "—" in expected
    assert expected not in wrong
    assert expected in source


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


@pytest.mark.parametrize("name,params,expected", [
    ("set_roi", {"x": 0, "y": 0, "width": 128, "height": 64},
     "core.set_roi(0, 0, 128, 64)"),
    ("clear_roi", {}, "core.clear_roi()"),
])
def test_roi_writes_emit_the_bare_core_call(tmp_path, name, params, expected):
    """ROI is a typed capability since block 47, so it must reach a script.

    Measured on the demo machine 2026-08-12, before the emitters landed: a
    session that touched ROI exported a file whose third line was
    `raise RuntimeError('NOT EMITTED: clear_roi ...')`, so the whole script died
    before its first hardware call. The guard and the authorization map are
    microclaw policy rather than hardware effect and must not appear here;
    geometry an adapter would reject still fails, on the adapter.
    """
    _, result, source = export(tmp_path, [call(name, params)])
    assert result["emitted_calls"] == 1
    assert expected in source
    assert "NOT EMITTED" not in source
    assert "RuntimeError" not in source
    assert "check_roi" not in source
    assert "authorize_path" not in source
    assert "import microclaw" not in source
    compile(source, str(tmp_path / "routine.py"), "exec")


def test_committed_example_is_an_actual_export():
    fixture = Path(__file__).parent / "fixtures" / "exported_stage_acquisition.py"
    source = fixture.read_text(encoding="utf-8")
    assert "core.set_xy_position(12.5, -4.0)" in source
    assert "acq.acquire(events)" in source
    assert "import microclaw" not in source


def test_real_smiley_session_fixture_shows_whole_routine_and_holes():
    source = (
        Path(__file__).parent / "fixtures" / "exported_smiley_session.py"
    ).read_text(encoding="utf-8")
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
        "mm, 20, 0.5, 'coarse_then_fine', 50, None)"
    ) in emitted_call
    assert "max(" not in emitted_call
    assert inspect.getsource(tools._run_autofocus_passes) in source


def test_emitted_autofocus_actually_crops_the_metric_frames(tmp_path, monkeypatch):
    _, _, source = export(tmp_path, [call("run_autofocus", {
        "z_range_um": 2, "z_step_um": 1, "method": "sweep", "settle_ms": 0,
        "region": [0, 0, 4, 4],
    })])
    frames = [np.zeros((8, 8), dtype=np.uint16) for _ in range(3)]
    frames[1][:4, :4] = np.indices((4, 4)).sum(axis=0) % 2 * 100

    class FakeCore:
        def __init__(self): self._index, self.position = 0, 50.0
        def get_position(self, _device=None): return self.position
        def get_focus_device(self): return "Z"
        def set_position(self, z): self.position = float(z)
        def device_busy(self, _device): return False
        def wait_for_device(self, _device): pass
        def snap_image(self): pass
        def get_bytes_per_pixel(self): return 2
        def get_number_of_components(self): return 1
        def get_tagged_image(self):
            frame = frames[min(self._index, 2)]
            self._index += 1
            return SimpleNamespace(pix=frame, tags={"Width": 8, "Height": 8})

    monkeypatch.setattr("pycromanager.Core", FakeCore)
    runnable = re.sub(r"^from pycromanager import .*$", "", source, flags=re.M)
    namespace = {
        "__file__": str(tmp_path / "routine.py"), "Core": FakeCore,
        "Acquisition": object, "multi_d_acquisition_events": lambda **kwargs: [],
    }
    exec(compile(runnable, "routine.py", "exec"), namespace)

    curve = namespace["autofocus_result"].coarse.metric_values
    cropped_metric = image_analysis.tenengrad(frames[1][:4, :4])
    full_metric = image_analysis.tenengrad(frames[1])
    assert cropped_metric != full_metric
    assert curve[1] == pytest.approx(cropped_metric)


def test_session_with_drawn_run_and_ordinary_step_exports_resolved_box(tmp_path):
    records = [call("clear_roi", {})]
    records += completed_call(
        "run_autofocus",
        {"z_range_um": 2, "z_step_um": 1, "region": "drawn"},
        {"region": [1, 2, 3, 4]},
    )

    _, result, source = export(tmp_path, records)

    assert result["emitted_calls"] == 2
    assert "core.clear_roi()" in source
    assert "_autofocus_region = [1, 2, 3, 4]" in source
    assert "'drawn'" not in source


@pytest.mark.parametrize(
    "name,result", [
        ("run_autofocus", {}),
        ("snap_and_analyze", {}),
    ],
)
def test_drawn_call_without_recorded_box_raises_cannot_emit(
    tmp_path, name, result
):
    params = ({"z_range_um": 2, "z_step_um": 1, "region": "drawn"}
              if name == "run_autofocus" else {"region": "drawn"})
    with pytest.raises(tools.CannotEmit, match="no resolved region"):
        getattr(tools, name)._microclaw_emitter(
            tools.RecordedParams(params, result)
        )


def test_emitted_drawn_autofocus_execs_and_crops_resolved_literal(
    tmp_path, monkeypatch
):
    resolved = [2, 1, 4, 4]
    records = completed_call(
        "run_autofocus",
        {"z_range_um": 2, "z_step_um": 1, "method": "sweep",
         "settle_ms": 0, "region": "drawn"},
        {"region": resolved},
    )
    _, _, source = export(tmp_path, records)
    frames = [np.zeros((8, 8), dtype=np.uint16) for _ in range(3)]
    frames[1][1:5, 2:6] = np.indices((4, 4)).sum(axis=0) % 2 * 100

    class FakeCore:
        def __init__(self): self._index, self.position = 0, 50.0
        def get_position(self, _device=None): return self.position
        def get_focus_device(self): return "Z"
        def set_position(self, z): self.position = float(z)
        def device_busy(self, _device): return False
        def wait_for_device(self, _device): pass
        def snap_image(self): pass
        def get_bytes_per_pixel(self): return 2
        def get_number_of_components(self): return 1
        def get_tagged_image(self):
            frame = frames[min(self._index, 2)]
            self._index += 1
            return SimpleNamespace(pix=frame, tags={"Width": 8, "Height": 8})

    monkeypatch.setattr("pycromanager.Core", FakeCore)
    runnable = re.sub(r"^from pycromanager import .*$", "", source, flags=re.M)
    namespace = {
        "__file__": str(tmp_path / "routine.py"), "Core": FakeCore,
        "Acquisition": object, "multi_d_acquisition_events": lambda **kwargs: [],
    }
    exec(compile(runnable, "routine.py", "exec"), namespace)

    curve = namespace["autofocus_result"].coarse.metric_values
    assert curve[1] == pytest.approx(
        image_analysis.tenengrad(frames[1][1:5, 2:6])
    )
    assert curve[1] != image_analysis.tenengrad(frames[1])


def test_emitted_autofocus_settles_delayed_stage_and_prints_envelope(
    tmp_path, monkeypatch, capsys
):
    _, _, source = export(tmp_path, [call("run_autofocus", {
        "z_range_um": 2, "z_step_um": 1, "method": "sweep", "settle_ms": 0,
    })])

    assert source.count("def settle_stage_move") == 1
    assert "AUTOFOCUS ENVELOPE" in source
    assert "min_contrast" in source
    assert "AUTOFOCUS OUTCOME" in source

    class DelayedCore:
        last = None

        def __init__(self):
            type(self).last = self
            self.position = 50.0
            self.target = 50.0
            self.polls = 0
            self.snapped_at = []
        def get_image_width(self): return 1
        def get_image_height(self): return 1
        def get_focus_device(self): return "Z"
        def set_position(self, z):
            self.target = float(z)
            self.polls = 0
        def wait_for_device(self, _device): pass
        def device_busy(self, _device): return self.polls < 2
        def get_position(self, _device=None):
            self.polls += 1
            if self.polls > 2:
                self.position = self.target + 0.2
            return self.position
        def snap_image(self): self.snapped_at.append(self.position)
        def get_bytes_per_pixel(self): return 2
        def get_number_of_components(self): return 1
        def get_tagged_image(self):
            frame = np.array([[int(self.position)]], dtype=np.uint16)
            return SimpleNamespace(pix=frame, tags={"Width": 1, "Height": 1})

    monkeypatch.setattr("pycromanager.Core", DelayedCore)
    runnable = re.sub(r"^from pycromanager import .*$", "", source, flags=re.M)
    namespace = {
        "__file__": str(tmp_path / "routine.py"), "Core": DelayedCore,
        "Acquisition": object, "multi_d_acquisition_events": lambda **kwargs: [],
    }
    exec(compile(runnable, "routine.py", "exec"), namespace)

    assert DelayedCore.last.snapped_at == [49.2, 50.2, 51.2]
    assert namespace["autofocus_result"].coarse.measured_z_positions == [49.2, 50.2, 51.2]
    assert namespace["autofocus_result"].final_z_um == 50.2
    output = capsys.readouterr().out
    assert "AUTOFOCUS ENVELOPE" in output
    assert "49.0" in output and "51.0" in output
    assert "region: None" in output
    assert "min_contrast:" in output
    assert "AUTOFOCUS OUTCOME" in output
    assert "moved: False" in output
    assert "measured final Z: 50.2" in output


def test_emitted_property_probe_defines_and_drives_every_helper(
    tmp_path, monkeypatch, capsys
):
    class StrVector:
        """Bridge-shaped string vector: deliberately not Python-iterable."""
        def __init__(self, values): self._values = list(values)
        def size(self): return len(self._values)
        def get(self, index): return self._values[index]

    spec = {"device": "lock", "property": "status",
            "in_focus_values": ["in"]}
    _, _, source = export(tmp_path, [call("run_autofocus", {
        "z_range_um": 4, "z_step_um": 1, "method": "sweep",
        "settle_ms": 30, "probe": spec,
    })])
    for name in ("FocusProbe", "image_probe", "property_probe", "_strings",
                 "longest_true_run", "_band_admit", "_stable_read"):
        assert f"{'class' if name == 'FocusProbe' else 'def'} {name}" in source
    assert "MIN_BAND_PLANES = 3" in source

    class FakeCore:
        last = None
        def __init__(self):
            type(self).last = self
            self.position = 51.0
        def get_position(self, _device=None): return self.position
        def get_focus_device(self): return "Z"
        def set_position(self, z): self.position = float(z)
        def device_busy(self, _device): return False
        def wait_for_device(self, _device): pass
        def get_allowed_property_values(self, _device, _prop):
            return StrVector(["out", "in"])
        def get_property(self, _device, _prop):
            return "in" if 50.0 <= self.position <= 52.0 else "out"

    monkeypatch.setattr("pycromanager.Core", FakeCore)
    runnable = re.sub(r"^from pycromanager import .*$", "", source, flags=re.M)
    namespace = {
        "__file__": str(tmp_path / "routine.py"), "Core": FakeCore,
        "Acquisition": object, "multi_d_acquisition_events": lambda **kwargs: [],
    }
    exec(compile(runnable, "routine.py", "exec"), namespace)

    result = namespace["autofocus_result"]
    assert result.converged is True
    assert result.final_z_um == 51.0
    assert result.coarse.metric_values == ["out", "in", "in", "in", "out"]
    output = capsys.readouterr().out
    assert "criterion: centre of lock.status in-range band" in output
    assert "in_focus_values: ['in']" in output


def test_emitted_numeric_property_prints_the_probe_description(tmp_path):
    _, _, source = export(tmp_path, [call("run_autofocus", {
        "z_range_um": 4, "z_step_um": 1, "method": "sweep",
        "probe": {"device": "PFS", "property": "Offset"},
    })])
    emitted_call = source.split("# RECORDED TOOL: run_autofocus", 1)[1]
    assert "property_probe(" in emitted_call
    assert "_autofocus_probe.describe" in emitted_call
    assert "centre of PFS.Offset in-range band" not in emitted_call


def test_emitted_autofocus_applies_same_small_region_threshold_as_live_run(
    tmp_path, monkeypatch
):
    size = 20
    rng = np.random.default_rng(73)
    frames = [
        np.clip(rng.normal(1000, 25, (size, size)), 0, 65535).astype(np.uint16)
        for _ in range(5)
    ]
    assert autofocus.curve_contrast(
        [image_analysis.tenengrad(frame) for frame in frames]
    ) > autofocus.MIN_CONTRAST

    class FakeCore:
        def __init__(self):
            self._index = 0
            self.position = 50.0
        def get_position(self, _device=None): return self.position
        def device_busy(self, _device): return False
        def get_focus_device(self): return "Z"
        def set_position(self, z): self.position = float(z)
        def wait_for_device(self, _device): pass
        def snap_image(self): pass
        def get_bytes_per_pixel(self): return 2
        def get_number_of_components(self): return 1
        def get_tagged_image(self):
            frame = frames[self._index]
            self._index += 1
            return SimpleNamespace(pix=frame, tags={"Width": size, "Height": size})

    live_core = FakeCore()
    live = tools._run_autofocus_passes(
        SimpleNamespace(core=live_core), 4, 1, "sweep", 0,
        [0, 0, size, size],
    )
    _, _, source = export(tmp_path, [call("run_autofocus", {
        "z_range_um": 4, "z_step_um": 1, "method": "sweep", "settle_ms": 0,
        "region": [0, 0, size, size],
    })])
    monkeypatch.setattr("pycromanager.Core", FakeCore)
    runnable = re.sub(r"^from pycromanager import .*$", "", source, flags=re.M)
    namespace = {
        "__file__": str(tmp_path / "routine.py"), "Core": FakeCore,
        "Acquisition": object, "multi_d_acquisition_events": lambda **kwargs: [],
    }
    exec(compile(runnable, "routine.py", "exec"), namespace)
    emitted = namespace["autofocus_result"]

    assert live.converged is False
    assert emitted.converged is False
    assert live.moved == emitted.moved == False
    assert live.reason == emitted.reason


def test_emitted_snap_analysis_actually_crops_every_statistic(tmp_path, monkeypatch):
    _, _, source = export(tmp_path, [call(
        "snap_and_analyze", {"region": [2, 1, 3, 4]}
    )])
    frame = np.zeros((8, 8), dtype=np.uint16)
    frame[1:5, 2:5] = 40

    class FakeCore:
        def snap_image(self): pass
        def get_bytes_per_pixel(self): return 2
        def get_number_of_components(self): return 1
        def get_tagged_image(self):
            return SimpleNamespace(pix=frame, tags={"Width": 8, "Height": 8})

    monkeypatch.setattr("pycromanager.Core", FakeCore)
    runnable = re.sub(r"^from pycromanager import .*$", "", source, flags=re.M)
    namespace = {
        "__file__": str(tmp_path / "routine.py"), "Core": FakeCore,
        "Acquisition": object, "multi_d_acquisition_events": lambda **kwargs: [],
    }
    exec(compile(runnable, "routine.py", "exec"), namespace)

    assert namespace["stats"].mean_intensity == 40.0
    assert namespace["stats"].min_intensity == 40.0


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
    assert "core.set_xy_position(3, 4)" in (tmp_path / "injected.py").read_text(encoding="utf-8")


def test_export_can_select_recorded_tool_use_ids_without_hiding_exclusions(tmp_path):
    records = [
        call("move_stage_xy", {"x_um": 1, "y_um": 2}),
        call("run_timelapse", {
            "n_frames": 1, "interval_s": 0, "save_dir": "discarded",
        }),
        call("move_stage_z", {"z_um": 9}),
    ]
    guard = Guard(tmp_path)
    result = tools.export_session_script(
        None, guard, "selected.py", records, tool_use_ids=["run_timelapse"]
    )
    source = (tmp_path / "selected.py").read_text(encoding="utf-8")

    assert result["emitted_calls"] == 1
    assert result["emitted_tool_use_ids"] == ["run_timelapse"]
    assert "Hardware state is order- and history-dependent" in result["selection_warning"]
    assert "# SKIPPED: move_stage_xy" in source
    assert "# SKIPPED: move_stage_z" in source
    assert "core.set_xy_position" not in source
    assert "core.set_position" not in source
    assert "num_time_points': 1" in source
    assert "Hardware state is order- and history-dependent" in source


def test_export_without_selection_still_emits_the_whole_session(tmp_path):
    _, result, source = export(tmp_path, [
        call("move_stage_xy", {"x_um": 1, "y_um": 2}),
        call("move_stage_z", {"z_um": 9}),
    ])
    assert result["emitted_calls"] == 2
    assert "core.set_xy_position" in source
    assert "core.set_position" in source
    assert "selection_warning" not in result


def test_export_selection_refuses_unknown_tool_use_id(tmp_path):
    with pytest.raises(ValueError, match="unknown tool_use id.*typo"):
        tools.export_session_script(
            None, Guard(tmp_path), "selected.py",
            [call("move_stage_xy", {"x_um": 1, "y_um": 2})],
            tool_use_ids=["typo"],
        )


def test_write_text_file_resolves_writes_and_never_overwrites(tmp_path):
    guard = Guard(tmp_path)
    result = tools.write_text_file(None, guard, "kept/protocol.py", "print('kept')\n")
    path = tmp_path / "kept" / "protocol.py"

    assert guard.seen == ["kept/protocol.py"]
    assert path.read_text(encoding="utf-8") == "print('kept')\n"
    assert result["artifact"] == {"kind": "python", "path": str(path)}
    with pytest.raises(FileExistsError, match="already exists"):
        tools.write_text_file(None, guard, "kept/protocol.py", "replacement")
    assert path.read_text(encoding="utf-8") == "print('kept')\n"


def test_write_text_file_is_registered_and_emits_nothing():
    schema = next(tool for tool in TOOLS if tool["name"] == "write_text_file")
    assert set(schema["input_schema"]["required"]) == {"path", "text"}
    assert tools.TOOL_REGISTRY["write_text_file"] is tools.write_text_file
    assert tools.write_text_file._microclaw_emits_nothing is True


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

    # The refused one never reaches the rig's absent channel group. It may name
    # '640' in its skip text -- what must not exist is an executable event.
    assert "'channel_group': 'Channel'" not in source
    assert "channels': ['640']" not in source

    # The two that completed *nothing* are skipped, and the script carries on.
    skipped = [line for line in source.splitlines() if "# SKIPPED" in line]
    assert len(skipped) == 2, skipped
    assert any("trigger line is not armed" in line for line in skipped), skipped
    assert any("cannot drive a channel axis" in line for line in skipped), skipped

    # Only the partial one refuses, and it names the position that did not
    # complete. Something happened there that cannot be faithfully reproduced.
    refusals = [line for line in source.splitlines() if "# NOT EMITTED" in line]
    assert len(refusals) == 1, refusals
    assert "1 of 3 recorded results entries did not complete" in refusals[0]
    assert "'pos3'" in refusals[0]

    # Exercised, not merely compiled: it runs the first acquisition, walks past
    # both skips, and stops at the partial one.
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
    # The first acquisition's three positions were imaged; the failed run's and
    # the refused run's were not, and the partial one stopped the script before
    # the fifth call. A partial mid-session does still strand what follows --
    # that is the accepted cost of not reconstructing it.
    assert visited == [(1.0, 2.0), (3.0, 4.0), (5.0, 6.0)]
    assert len(acquisitions) == 3


def _rejected_then_successful_session():
    """The demo gate session: a call the tool layer rejected, then one that ran.

    The first multiposition call passed `channel` at the top level, which the
    tool does not accept -- it raised `TypeError` and did nothing. The second
    put the channel in `protocol_params`, where the tool takes it, and completed.
    """
    positions = [{"name": "spot_1", "x_um": 1.0, "y_um": 2.0}]
    records = completed_call("set_channel", {"preset": "FITC"}, {
        "status": "Channel set to 'FITC'.", "writes": 1,
        "effects": [["Emission", "Label", "Chroma-HQ535"]],
        "channel_source": "config-group",
    })
    records += completed_call(
        "run_multiposition_acquisition",
        {"protocol": "timelapse", "positions": positions, "channel": "DAPI",
         "save_dir": "/data", "name": "spot",
         "protocol_params": {"n_frames": 1, "interval_s": 0}},
        {"error": "TypeError: run_multiposition_acquisition() got an unexpected "
                  "keyword argument 'channel'",
         "hint": "This is an argument error, not a hardware fault."},
    )
    records += completed_call(
        "run_multiposition_acquisition",
        {"protocol": "timelapse", "positions": positions, "save_dir": "/data",
         "name": "spot",
         "protocol_params": {"n_frames": 1, "interval_s": 0, "channel": "DAPI"}},
        {"status": "1/1 positions completed.",
         "results": [{"position": "spot_1", "x_um": 1.0, "y_um": 2.0,
                      "dataset_path": "/data/spot_1"}]},
    )
    return records


def test_malformed_call_the_tool_layer_rejected_is_not_emitted_as_well_formed(tmp_path):
    """Demo gate round 1, 2026-08-06, measured physically rather than by eye.

    The exporter emitted **both** calls, leaving three datasets per position
    where the session made one. The phantom is worse than duplicate dose: the
    emitter reads the channel from `protocol_params`, so the rejected top-level
    `channel` was invisible to it and the step rendered with **no channel at
    all** -- acquiring in whatever state was current, which was FITC from the
    preceding `set_channel`, a channel the session never asked to image. File
    sizes corroborated it: the two real datasets matched at 532654/532655 bytes
    and the phantom stood alone at 532637.

    Arguments the tool layer rejected never took effect, so a step built from
    them is invention, not reproduction -- design/41 F1's failure arriving
    through the exporter itself.
    """
    _, result, source = export(tmp_path, _rejected_then_successful_session())

    # One acquisition ran; one is emitted.
    assert source.count("acq.acquire(events)") == 1
    # The rejected call is skipped, and says why.
    assert source.count("# SKIPPED: run_multiposition_acquisition") == 1
    assert "TypeError" in source
    # The one acquisition emitted is the real one, with its channel.
    assert "'channel_group': 'Channel', 'channels': ['DAPI']" in source
    # And crucially: no channel-less acquisition. That is the phantom -- the one
    # that imaged whatever channel happened to be current.
    assert "multi_d_acquisition_events(**{'num_time_points': 1, 'time_interval_s': 0})" \
        not in source
    assert result["emitted_calls"] == 2      # the set_channel and the good run


def test_script_runs_past_a_call_that_did_nothing_to_the_one_that_ran(tmp_path):
    """Demo gate round 2, 2026-08-06. Round 1's fix refused the rejected call,

        RuntimeError: NOT EMITTED: run_multiposition_acquisition — the recorded
        call did not succeed: TypeError: ... unexpected keyword argument 'channel'

    at line 97, which made the acquisition that *did* run -- lines 99-106 --
    unreachable. The script contributed zero acquisitions.

    A call that completed nothing is not the same as a call that cannot be
    emitted. The session did nothing there, so doing nothing is the *exact*
    reproduction, not a reconstruction; only a step that really happened and
    cannot be reproduced earns the halt. Failed calls are ordinary -- the M5
    gate session had three -- so refusing on them makes the export useless on
    precisely the sessions people have.

    Executed rather than compiled: round 2 shipped because a grep saw the line
    and nothing ran it.
    """
    _, _, source = export(tmp_path, _rejected_then_successful_session())

    # This session inlines no adaptive adapter, so no library `raise
    # RuntimeError` is legitimately present and the only way one appears is a
    # `# NOT EMITTED` refusal -- the failure that killed 43h's and 47's gates.
    # Block 52b briefly deleted this line while correctly observing that the
    # assertion is untenable for an *adaptive* export; measured here, it still
    # holds for this one.
    assert "raise RuntimeError" not in source
    assert "# SKIPPED: run_multiposition_acquisition" in source

    visited, acquisitions, writes = [], [], []

    class DemoCore:
        def set_xy_position(self, x, y): visited.append((x, y))
        def set_position(self, z): pass
        def wait_for_device(self, _device): pass
        def set_property(self, d, p, v): writes.append((d, p, v))
        def get_property_type(self, _d, _p): return "String"
        def get_property(self, d, p):
            return next(v for wd, wp, v in writes if (wd, wp) == (d, p))

    class FakeAcquisition:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def acquire(self, events): acquisitions.append(events)

    exec(compile(source.replace(
        "from pycromanager import Acquisition, Core, multi_d_acquisition_events", ""
    ), "routine.py", "exec"), {
        "__file__": str(tmp_path / "routine.py"), "Core": DemoCore,
        "Acquisition": FakeAcquisition, "multi_d_acquisition_events": dict,
    })

    # It ran to the end: the channel switch, then the acquisition that the
    # session actually performed. Round 2 reached neither.
    assert writes == [("Emission", "Label", "Chroma-HQ535")]
    assert visited == [(1.0, 2.0)]
    assert len(acquisitions) == 1
    assert acquisitions[0]["channels"] == ["DAPI"]


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


def test_emitted_interdependent_preset_writes_every_effect_before_verifying(tmp_path):
    result = {
        "status": "Config preset System.Normal Mode applied.",
        "writes": 2,
        "effects": [
            ["Camera", "Exposure", "100.0030"],
            ["Camera", "ScanMode", "3"],
        ],
        "channel_source": "config-group",
    }
    _, _, source = export(tmp_path, completed_call(
        "set_config_preset",
        {"group": "System", "preset": "Normal Mode"},
        result,
    ))

    exposure_set = source.index("core.set_property('Camera', 'Exposure', '100.0030')")
    mode_set = source.index("core.set_property('Camera', 'ScanMode', '3')")
    exposure_verify = source.index(
        "_verify_property(core, 'Camera', 'Exposure', '100.0030')"
    )
    mode_verify = source.index("_verify_property(core, 'Camera', 'ScanMode', '3')")
    assert exposure_set < mode_set < exposure_verify < mode_verify
    compile(source, "routine.py", "exec")


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


def test_set_config_preset_emits_recorded_effects_branch(tmp_path):
    result = {
        **FLOAT_CHANNEL_RESULT,
        "status": "Config preset Camera.Fast applied.",
        "config_group": "Camera",
    }
    _, _, source = export(tmp_path, completed_call(
        "set_config_preset", {"group": "Camera", "preset": "Fast"}, result))
    assert "core.set_property('Emission', 'Label', 'Chroma-HQ535')" in source
    assert "_verify_property(core, 'Camera', 'Exposure', '10')" in source
    compile(source, "routine.py", "exec")


# The emitter's map-less `set_config` branch is NOT tested through
# set_config_preset: review round 1 removed that tool's map-less route as
# ungated, so a result carrying `config_group` and no `effects` is a shape it can
# no longer produce, and a test asserting otherwise would document a route that
# does not exist. That emitter branch stays covered by
# test_map_less_channel_delegation_emits_the_set_config_that_ran, through
# set_channel, which does still have one.


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
    assert "# OBSERVATION HOOK NOT ATTACHED: 'snr_observer'" in observed
    assert "does not reproduce its measurements or hook log" in observed

    _, _, deciding = export(tmp_path, [call(
        "run_multiposition_acquisition", {**base, "hook_strategy": "position_filter"}
    )])
    assert "# NOT EMITTED: run_multiposition_acquisition — hooked acquisition" in deciding
    assert "HookBase" in deciding


def test_observation_only_tile_uses_same_documented_imaging_only_export(tmp_path):
    _, _, source = export(tmp_path, [call("run_tile_acquisition", {
        "rows": 1, "cols": 1, "step_um": 10, "center_x_um": 1,
        "center_y_um": 2, "protocol": "zstack",
        "protocol_params": {"z_start_um": -1, "z_end_um": 1, "z_step_um": 1},
        "hook_strategy": "snr_observer", "save_dir": "/data", "name": "tile",
    })])

    assert "# NOT EMITTED:" not in source
    assert "# OBSERVATION HOOK NOT ATTACHED: 'snr_observer'" in source


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


def _undefined_emitted_names(source):
    """Return runtime global loads not bound by an emitted top-level block."""
    import ast, builtins

    tree = ast.parse(source)
    definitions = tuple(
        n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))
    )
    statements = tuple(n for n in tree.body if n not in definitions)
    defined = {n.name for n in definitions}
    defined |= {
        n.id for statement in statements for n in ast.walk(statement)
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)
    }
    defined |= {
        a.asname or a.name.split(".")[0]
        for statement in statements for n in ast.walk(statement)
        if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names
    }
    # `except E as name` binds through a plain string attribute, not a Name
    # node, so this checker used to report emitted handler variables as
    # undefined -- which pushed the emitter into writing
    # `__import__('sys').exc_info()[1]` to dodge it.
    defined |= {
        n.name for statement in statements for n in ast.walk(statement)
        if isinstance(n, ast.ExceptHandler) and n.name
    }

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

    undefined = set()
    # Only top-level definitions begin a scope scan. Walking the whole tree
    # would visit event_stream independently and lose parameters bound by its
    # enclosing survey-event-stream and factory closures.
    for node in definitions:
        local = {a.arg for f in ast.walk(node)
                 if isinstance(f, ast.FunctionDef)
                 for a in (*f.args.posonlyargs, *f.args.args, *f.args.kwonlyargs,
                           *(x for x in (f.args.vararg, f.args.kwarg)
                             if x is not None))}
        local |= {n.id for n in ast.walk(node)
                  if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
        local |= {n.name for n in ast.walk(node)
                  if isinstance(n, ast.ExceptHandler) and n.name}
        local |= {
            n.name for n in ast.walk(node)
            if isinstance(n, (ast.FunctionDef, ast.ClassDef))
        }
        local |= {
            a.asname or a.name.split(".")[0]
            for n in ast.walk(node)
            if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names
        }
        undefined.update(
            n.id for n in ast.walk(node)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
            and id(n) not in annotated
            and n.id not in defined and n.id not in local
            and not hasattr(builtins, n.id)
        )
    undefined.update(
        n.id for statement in statements for n in ast.walk(statement)
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
        and id(n) not in annotated
        and n.id not in defined and n.id != "__file__"
        and not hasattr(builtins, n.id)
    )
    return undefined


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
        def get_position(self, _device=None): return self.z
        def set_position(self, z): self.z = float(z)
        def device_busy(self, _device): return False
        def set_xy_position(self, x, y): self.moves.append((x, y))
        def wait_for_device(self, _device): pass
        def snap_image(self): self.snaps += 1
        def get_bytes_per_pixel(self): return 2
        def get_number_of_components(self): return 1
        # The emitted script scales the flat-curve guard by the live frame's
        # pixel count, so a fake Core must answer these the way a real one does.
        def get_image_width(self): return 2
        def get_image_height(self): return 2
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


@pytest.mark.parametrize(("params", "reason"), [
    ({"hook_strategy": ["snr_observer"]}, "composition is not supported"),
    ({"hook_strategy": "mm_plugin_analyzer"}, "plugin capabilities"),
    ({"hook_strategy": "missing_saved_hook"}, "source is unavailable"),
    ({"hook_strategy": "snr_observer", "illumination_envelope": {}},
     "rig-configured raw-value conversions"),
])
def test_adaptive_runs_refuse_only_with_a_specific_reason(tmp_path, params, reason):
    """M5 round-4 guard, narrowed now that adaptive programs are emittable."""
    base = {"n_frames": 2, "interval_s": 0, "save_dir": "session", **params}
    _, _, source = export(tmp_path, [call("run_timelapse", base)])
    assert "# NOT EMITTED: run_timelapse" in source
    assert reason in source
    assert "chosen at runtime by its hook" not in source
    assert "no standalone emitter has been implemented" not in source


@pytest.mark.parametrize(("tool", "params", "seed"), [
    ("run_zstack", {
        "z_start_um": -2, "z_end_um": 2, "z_step_um": 0.5,
        "save_dir": "session", "hook_strategy": "snr_observer",
    }, "'z_start': -2"),
    ("run_timelapse", {
        "n_frames": 3, "interval_s": 1.5, "save_dir": "session",
        "hook_strategy": "snr_observer",
    }, "'num_time_points': 3"),
    ("run_adaptive_survey", {
        "protocol": "timelapse", "protocol_params": {"n_frames": 1},
        "positions": [{"name": "p0", "x_um": 1.25, "y_um": 2.5}],
        "save_dir": "session", "hook_strategy": "snr_observer",
    }, "'xy_positions': [(1.25, 2.5)]"),
])
def test_all_adaptive_program_shapes_emit_seed_hook_and_runner(
    tmp_path, tool, params, seed
):
    _, result, source = export(tmp_path, [call(tool, params)])
    assert result["emitted_calls"] == 1
    assert seed in source
    assert inspect.getsource(tools._survey_event_stream) in source
    assert inspect.getsource(tools.SurveyProgress) in source
    assert "class SNRObservationHook" in source
    assert "directory=str(_HERE)" in source
    if tool == "run_zstack":
        assert "guard.check_z(-2)" in source
        assert "guard.check_z(2)" in source
    elif tool == "run_adaptive_survey":
        assert "guard.check_xy(1.25, 2.5)" in source
    assert "# NOT EMITTED:" not in source
    compile(source, str(tmp_path / "routine.py"), "exec")


@pytest.mark.parametrize(("tool", "shape"), [
    ("run_timelapse", {"n_frames": 3, "interval_s": 0}),
    ("run_zstack", {"z_start_um": -1, "z_end_um": 1, "z_step_um": 1}),
])
@pytest.mark.parametrize("channel", [None, "DAPI"])
def test_folded_hooked_acquisitions_emit_recorded_exposure(tmp_path, tool, shape, channel):
    params = {**shape, "save_dir": "session", "hook_strategy": "snr_observer",
              "exposure_ms": 17.5, "channel": channel}
    _, result, source = export(tmp_path, [call(tool, params)])
    assert result["emitted_calls"] == 1
    assert "guard.check_exposure(17.5)" in source
    if channel:
        assert "'channel_exposures_ms': [17.5]" in source
    else:
        assert "core.set_exposure(17.5)" in source
    assert "class SNRObservationHook" in source
    assert "# NOT EMITTED:" not in source
    compile(source, str(tmp_path / "routine.py"), "exec")


@pytest.mark.parametrize(("tool", "shape", "expected"), [
    ("run_timelapse", {"n_frames": 2, "interval_s": 0}, "timelapse"),
    ("run_zstack", {"z_start_um": 0, "z_end_um": 1, "z_step_um": 1}, "zstack"),
])
def test_hooked_export_names_the_dataset_the_tool_would_have_named(
    tmp_path, tool, shape, expected
):
    """A hooked run that named no dataset must emit the TOOL's default name.

    The emitter's fallback was "adaptive", which agreed with the two adaptive
    twins by coincidence. Block 43j folded them into run_timelapse/run_zstack,
    whose defaults are "timelapse"/"zstack" — so the hooked branch would have
    emitted a differently named dataset than both the live run and its own
    hookless branch, silently breaking the reproduce-the-run comparison 43h's
    gate rests on.
    """
    _, _, hooked = export(tmp_path, [call(tool, {
        **shape, "save_dir": "session", "hook_strategy": "snr_observer",
    })])
    _, _, plain = export(tmp_path, [call(tool, {**shape, "save_dir": "session"})])
    assert f"name={expected!r}" in hooked
    assert f"name={expected!r}" in plain
    assert "name='adaptive'" not in hooked


def test_named_adaptive_survey_resolves_full_precision_position_list_seed(tmp_path):
    records = completed_call("get_position_list", {}, {
        "positions": [{"name": "p0", "x_um": 1.23456, "y_um": 8.76543}]
    }) + completed_call("run_adaptive_survey", {
        "protocol": "timelapse", "protocol_params": {"n_frames": 1},
        "position_names": ["p0"], "save_dir": "session",
        "hook_strategy": "snr_observer",
    }, {"tiles_planned": [{"position": "p0", "x_um": 1.235, "y_um": 8.765}]})
    _, result, source = export(tmp_path, records)
    assert result["emitted_calls"] == 1
    assert "'xy_positions': [(1.23456, 8.76543)]" in source
    assert "'xy_positions': [(1.235, 8.765)]" not in source


def test_adaptive_inline_is_exact_live_decision_source(tmp_path):
    from microclaw import hook_decisions

    _, _, source = export(tmp_path, [call("run_timelapse", {
        "n_frames": 2, "interval_s": 0, "save_dir": "session",
        "hook_strategy": "snr_observer",
    })])
    assert inspect.getsource(tools._survey_event_stream) in source
    adapter_source = inspect.getsource(hook_decisions.UntrustedHookAdapter)
    available = tools._source_bound_names(source)
    assert tools._without_microclaw_imports(adapter_source, available) in source
    assert inspect.getsource(tools._note_budget_exhausted) in source


def test_acquire_on_hit_emits_fresh_hit_program_with_per_hit_z(tmp_path, monkeypatch):
    from microclaw.hook_manager import save_hook
    import microclaw.hook_manager as manager

    hooks_dir = tmp_path / "hooks"
    monkeypatch.setattr(manager, "HOOKS_DIR", hooks_dir)
    monkeypatch.setattr(manager, "MANIFEST", hooks_dir / "manifest.json")
    source_text = (
        "from microclaw.hook_decisions import AcquireAt, HookResult\n"
        "class Hit:\n"
        "    def analyze_frame(self, image, metadata):\n"
        "        return HookResult({}, (AcquireAt('p0'),))\n"
    )
    save_hook("hit", source_text, "hit", source="claude_generated")
    params = {
        "protocol": "timelapse",
        "protocol_params": {"n_frames": 1, "channel": "561", "exposure_ms": 5},
        "positions": [{"name": "p0", "x_um": 1, "y_um": 2}],
        "save_dir": "session", "hook_strategy": "hit",
        "acquire_on_hit": {
            "channel": "488", "protocol": "timelapse", "max_hits": 2,
            "protocol_params": {"n_frames": 3, "interval_s": 0,
                                "exposure_ms": 20},
        },
    }
    result_record = {"channel_effects": {
        "search": {"config_group": "Channel"},
        "acquire": {"config_group": "Channel"},
    }}
    _, result, source = export(
        tmp_path, completed_call("run_adaptive_survey", params, result_record)
    )
    assert result["emitted_calls"] == 1
    assert "hits = []" in source
    assert "acquire_hits=hits" in source
    assert "read_z=core.get_position" in source
    assert "for hit in hits:" in source
    assert "_event['z'] = hit['z_um']" in source
    assert "guard.check_exposure(20)" in source
    assert "'x_um': 1" not in source.split("acquire_events = []", 1)[1]
    assert not _undefined_emitted_names(source)
    compile(source, str(tmp_path / "routine.py"), "exec")


def test_acquire_on_hit_effect_triples_inline_channel_verifier(tmp_path, monkeypatch):
    from microclaw.hook_manager import save_hook
    import microclaw.hook_manager as manager

    hooks_dir = tmp_path / "hooks-effects"
    monkeypatch.setattr(manager, "HOOKS_DIR", hooks_dir)
    monkeypatch.setattr(manager, "MANIFEST", hooks_dir / "manifest.json")
    save_hook("hit", (
        "from microclaw.hook_decisions import AcquireAt, HookResult\n"
        "class Hit:\n"
        "    def analyze_frame(self, image, metadata):\n"
        "        return HookResult({}, (AcquireAt('p0'),))\n"
    ), "hit", source="claude_generated")
    params = {
        "protocol": "timelapse",
        "protocol_params": {"n_frames": 1, "channel": "Rhodamine"},
        "positions": [{"name": "p0", "x_um": 1, "y_um": 2}],
        "save_dir": "session", "hook_strategy": "hit",
        "acquire_on_hit": {
            "channel": "FITC", "protocol": "timelapse", "max_hits": 2,
            "protocol_params": {"n_frames": 3, "interval_s": 0},
        },
    }
    result_record = {"channel_effects": {
        "search": {"channel_source": "config-group", "effects": [
            ["Dichroic", "Label", "Q585LP"],
            ["Emission", "Label", "Chroma-HQ620"],
        ]},
        "acquire": {"channel_source": "config-group", "effects": [
            ["Dichroic", "Label", "Q505LP"],
            ["Emission", "Label", "Chroma-HQ535"],
            ["Excitation", "Label", "Chroma-HQ480"],
        ]},
    }}
    _, result, source = export(
        tmp_path, completed_call("run_adaptive_survey", params, result_record)
    )
    assert result["emitted_calls"] == 1
    assert "def _verify_property(" in source
    assert "_verify_property(core, 'Dichroic', 'Label', 'Q585LP')" in source
    assert "_verify_property(core, 'Dichroic', 'Label', 'Q505LP')" in source
    assert not _undefined_emitted_names(source)


def test_acquire_on_hit_refuses_only_when_executed_phase_lacks_effects(
    tmp_path, monkeypatch
):
    from microclaw.hook_manager import save_hook
    import microclaw.hook_manager as manager

    hooks_dir = tmp_path / "hooks"
    monkeypatch.setattr(manager, "HOOKS_DIR", hooks_dir)
    monkeypatch.setattr(manager, "MANIFEST", hooks_dir / "manifest.json")
    save_hook("hit", (
        "from microclaw.hook_decisions import AcquireAt, HookResult\n"
        "class Hit:\n"
        "    def analyze_frame(self, image, metadata):\n"
        "        return HookResult({}, (AcquireAt('p0'),))\n"
    ), "hit", source="claude_generated")
    params = {
        "protocol": "timelapse", "protocol_params": {"n_frames": 1, "channel": "561"},
        "positions": [{"name": "p0", "x_um": 1, "y_um": 2}],
        "save_dir": "session", "hook_strategy": "hit",
        "acquire_on_hit": {"channel": "488", "protocol": "timelapse",
                           "protocol_params": {"n_frames": 1}, "max_hits": 1},
    }
    _, result, source = export(tmp_path, completed_call(
        "run_adaptive_survey", params,
        {"acquire_phase_ran": True,
         "channel_effects": {"search": {"config_group": "Channel"}}},
    ))
    assert result["emitted_calls"] == 0
    assert "executed acquire phase has no recorded executable channel effects" in source


def test_zero_hit_session_emits_the_next_runs_acquire_program(tmp_path, monkeypatch):
    from microclaw.hook_manager import save_hook
    import microclaw.hook_manager as manager

    hooks_dir = tmp_path / "hooks-zero"
    monkeypatch.setattr(manager, "HOOKS_DIR", hooks_dir)
    monkeypatch.setattr(manager, "MANIFEST", hooks_dir / "manifest.json")
    save_hook("no_hit", (
        "from microclaw.hook_decisions import HookResult\n"
        "class NoHit:\n"
        "    def analyze_frame(self, image, metadata):\n"
        "        return HookResult({})\n"
    ), "no hit", source="claude_generated")
    params = {
        "protocol": "timelapse",
        "protocol_params": {"n_frames": 1, "channel": "561", "exposure_ms": 5},
        "positions": [{"name": "p0", "x_um": 1, "y_um": 2}],
        "save_dir": "session", "hook_strategy": "no_hit",
        "acquire_on_hit": {"channel": "488", "protocol": "timelapse",
                           "protocol_params": {"n_frames": 2, "exposure_ms": 20},
                           "max_hits": 1},
    }
    recorded = {"hits_recorded": 0, "hits_acquired": 0,
                "acquire_phase_ran": False,
                "channel_effects": {
                    "search": {"config_group": "Channel"},
                    "acquire": {"config_group": "Channel", "planned": True},
                }}
    _, result, source = export(
        tmp_path, completed_call("run_adaptive_survey", params, recorded)
    )
    assert result["emitted_calls"] == 1
    assert "hits = []" in source
    assert "core.set_config('Channel', '488')" in source
    assert "if acquire_events:" in source
    compile(source, str(tmp_path / "routine.py"), "exec")


def test_absent_acquire_on_hit_keeps_adaptive_program_byte_identical(tmp_path):
    params = {
        "protocol": "timelapse", "protocol_params": {"n_frames": 1},
        "positions": [{"name": "p0", "x_um": 1, "y_um": 2}],
        "save_dir": "session", "hook_strategy": "snr_observer",
    }
    _, _, absent = export(tmp_path / "absent", [call("run_adaptive_survey", params)])
    _, _, explicit_none = export(
        tmp_path / "none", [call("run_adaptive_survey", {**params, "acquire_on_hit": None})]
    )
    assert absent == explicit_none
    assert "hits = []" not in absent
    assert "hook.survey_events = events" in absent
    assert "hook.candidates = candidates" in absent
    assert "hook.progress = progress" in absent
    assert "event_source = _survey_event_stream(events, candidates, progress, 60.0, hook, adaptive=True, max_events=len(events))" in absent
    assert "with Acquisition(directory=str(_HERE), name='survey', show_display=True, **_hook_callbacks) as acq:" in absent
    assert "    acq.acquire(event_source(acq))" in absent


@pytest.mark.parametrize("guard_body", [
    pytest.param(
        "        try:\n"
        "            from microclaw.hook_decisions import ContinueSurvey, HookResult\n"
        "        except ImportError:\n"
        "            raise\n",
        id="try-wrapped",
    ),
    pytest.param(
        "        if True:\n"
        "            from microclaw.hook_decisions import ContinueSurvey, HookResult\n",
        id="if-guarded",
    ),
])
def test_stripping_a_block_sole_package_import_still_emits_valid_python(
    tmp_path, monkeypatch, guard_body
):
    """Coordinator fix, review round 4.

    Package imports are removed by line number at any nesting depth. Where the
    import is the ONLY statement of its block, deleting it left an empty block
    and the exporter wrote a file that could not be parsed -- while reporting
    `Session script exported.` with `emitted_calls: 1`. The operator would have
    found out by running it.

    A saved hook is arbitrary code, and `try: from microclaw... except
    ImportError:` is exactly what someone writes when they intend the hook to be
    portable, so this is a likely shape rather than an exotic one. `pass` is the
    correct residue: the name IS bound at module level in the emitted script, so
    the import succeeded and the fallback must not run.
    """
    from microclaw.hook_manager import save_hook
    import microclaw.hook_manager as manager

    hooks_dir = tmp_path / "hooks"
    monkeypatch.setattr(manager, "HOOKS_DIR", hooks_dir)
    monkeypatch.setattr(manager, "MANIFEST", hooks_dir / "manifest.json")
    hook_source = (
        "class Portable:\n"
        "    def analyze_frame(self, image, metadata):\n"
        + guard_body
        + "        return HookResult({}, actions=(ContinueSurvey(),))\n"
    )
    save_hook("portable", hook_source, "portable", source="user_provided")

    _, result, source = export(tmp_path, [call("run_timelapse", {
        "n_frames": 2, "interval_s": 0, "save_dir": "session",
        "hook_strategy": "portable",
    })])

    assert result["emitted_calls"] == 1
    compile(source, "routine.py", "exec")      # the assertion that was failing
    assert "from microclaw" not in source
    assert not _undefined_emitted_names(source)


@pytest.mark.parametrize(("tool", "shape"), [
    ("run_timelapse", {"n_frames": 2, "interval_s": 0}),
    ("run_zstack", {"z_start_um": 0, "z_end_um": 1, "z_step_um": 1}),
])
def test_saved_fixed_run_exports_named_stage_envelope_and_indexed_plan(
    tmp_path, monkeypatch, tool, shape
):
    from microclaw.hook_manager import save_hook
    import microclaw.hook_manager as manager

    hooks_dir = tmp_path / "hooks"
    monkeypatch.setattr(manager, "HOOKS_DIR", hooks_dir)
    monkeypatch.setattr(manager, "MANIFEST", hooks_dir / "manifest.json")
    save_hook(
        "planned_stage", "class PlannedStage:\n"
        "    def analyze_frame(self, image, metadata):\n"
        "        from microclaw.hook_decisions import HookResult\n"
        "        return HookResult({'score': 1})\n",
        "planned_stage", source="user_provided",
    )
    plan = [
        {"hook_event_index": 0, "actions": [
            {"kind": "MoveNamedStage", "position_um": 10}
        ]},
        {"hook_event_index": 1, "actions": []},
    ]
    _, result, source = export(tmp_path, [call(tool, {
        **shape, "save_dir": "session", "hook_strategy": "planned_stage",
        "named_stage_envelope": {
            "device": "fixture-stage", "min_um": 10, "max_um": 20,
            "max_writes": 2, "restore": "leave",
        },
        "hook_action_plan": plan,
    })])
    assert result["emitted_calls"] == 1, result
    assert "_NAMED_STAGE_ENVELOPE" in source
    assert repr(plan) in source
    assert "_event['hook_event_index']" not in source
    assert "_axes_plan" in source
    assert "pre_hardware_hook_fn" in source
    assert "print('Dataset:', getattr(acq, '_dataset_disk_location'" in source
    assert "# NOT EMITTED" not in source
    assert "import microclaw" not in source
    assert not _undefined_emitted_names(source)
    compile(source, str(tmp_path / "routine.py"), "exec")


@pytest.mark.parametrize(("tool", "shape"), [
    ("run_timelapse", {"n_frames": 2, "interval_s": 1}),
    ("run_zstack", {"z_start_um": 0, "z_end_um": 1, "z_step_um": 1}),
])
def test_saved_fixed_run_exports_property_envelope_and_plan(
    tmp_path, monkeypatch, tool, shape
):
    from microclaw.hook_manager import save_hook
    import microclaw.hook_manager as manager

    hooks_dir = tmp_path / "hooks"
    monkeypatch.setattr(manager, "HOOKS_DIR", hooks_dir)
    monkeypatch.setattr(manager, "MANIFEST", hooks_dir / "manifest.json")
    save_hook(
        "planned_property", "class PlannedProperty:\n"
        "    def analyze_frame(self, image, metadata):\n"
        "        return None\n",
        "planned_property", source="user_provided",
    )
    plan = [
        {"hook_event_index": 0, "actions": [
            {"kind": "SetDeviceProperty", "value": "B"}]},
        {"hook_event_index": 1, "actions": []},
    ]
    _, result, source = export(tmp_path, [call(tool, {
        **shape, "save_dir": "session", "hook_strategy": "planned_property",
        "property_envelope": {"device": "Wheel", "property": "State",
                              "allowed_values": ["B"], "max_writes": 1,
                              "restore": "leave"},
        "hook_action_plan": plan,
    })])
    assert result["emitted_calls"] == 1, result
    assert "_PROPERTY_ENVELOPE" in source
    assert repr(plan) in source
    assert "hook.configure_property" in source
    assert "('property', 'restore_property', '_property_restoration')" in source
    assert "# NOT EMITTED" not in source
    assert "import microclaw" not in source
    assert not _undefined_emitted_names(source)
    compile(source, str(tmp_path / "routine.py"), "exec")


def test_emitted_property_guard_pins_the_exact_approved_pair():
    namespace = {"math": __import__("math")}
    exec(tools._export_guard_source({
        "x_um": (None, None), "y_um": (None, None),
        "z_um": (None, None), "exposure_ms": (None, None),
        "analysis_min_snr": None,
    }), namespace)
    namespace["_PROPERTY_ENVELOPE"] = {
        "device": "Wheel", "property": "State", "allowed_values": ["B"],
    }
    guard = namespace["guard"]
    guard.check_device_property(None, "Wheel", "State", "B", approved_envelope=True)
    with pytest.raises(namespace["SafetyViolation"], match="no recorded envelope"):
        guard.check_device_property(
            None, "OtherWheel", "State", "B", approved_envelope=True,
        )


def test_unresolvable_survey_names_fall_back_to_the_recorded_tiles(tmp_path):
    """M5 gate, 2026-08-11. The whole export came back `emitted_calls: 0`.

    A real session marked and validated its positions and STILL failed name
    resolution — an intervening `validate_positions`/`mark_position` sequence
    leaves state that cannot resolve `pos_1`. Refusing was safe but useless: the
    operator got no runnable script, and the agent hand-wrote an acquisition
    script to fill the gap, which is the fabrication path this exporter exists
    to remove.

    The run recorded the coordinates it actually resolved, so a name this
    exporter cannot re-derive is not a dead end. Same result-derived route
    `_emit_multiposition` already takes.
    """
    records = completed_call(
        "run_adaptive_survey",
        {"protocol": "timelapse", "protocol_params": {"n_frames": 3, "interval_s": 0},
         "position_names": ["pos_1", "pos_2"], "save_dir": "session",
         "hook_strategy": "snr_observer"},
        {"status": "Adaptive survey: 6 frame(s) acquired from a 2-tile plan.",
         "tiles_planned": [{"position": "pos_1", "x_um": -819.7, "y_um": 566.0},
                           {"position": "pos_2", "x_um": -784.7, "y_um": 566.0}]},
    )
    _, result, source = export(tmp_path, records)

    assert result["emitted_calls"] == 1
    assert "# NOT EMITTED:" not in source
    assert "'xy_positions': [(-819.7, 566.0), (-784.7, 566.0)]" in source
    assert "'position_labels': ['pos_1', 'pos_2']" in source
    compile(source, "routine.py", "exec")


def test_a_survey_with_neither_names_nor_recorded_tiles_still_refuses(tmp_path):
    """The fallback must not become a licence to invent coordinates."""
    records = completed_call(
        "run_adaptive_survey",
        {"protocol": "timelapse", "protocol_params": {"n_frames": 1},
         "position_names": ["pos_1"], "save_dir": "session",
         "hook_strategy": "snr_observer"},
        {"status": "Adaptive survey: 0 frame(s) acquired from a 1-tile plan."},
    )
    _, _, source = export(tmp_path, records)
    assert "# NOT EMITTED: run_adaptive_survey" in source


def test_the_exporter_never_writes_a_file_it_cannot_parse(tmp_path, monkeypatch):
    """The global guard, independent of any one emitter.

    Same round. A malformed emitter must refuse loudly and write nothing rather
    than hand the operator a file that fails at the first line Python reads.
    """
    # Patch the emitter the decorator captured, not the module attribute:
    # `@emits(_emit_snap_and_analyze)` bound the function object at import time,
    # so replacing `tools._emit_snap_and_analyze` reaches nothing.
    monkeypatch.setattr(
        tools.snap_and_analyze, "_microclaw_emitter",
        lambda params: "def broken(:\n",
    )
    with pytest.raises(tools.CannotEmit, match="does not parse"):
        tools.export_session_script(
            None, Guard(tmp_path), "routine.py", [call("snap_and_analyze", {})],
        )
    assert not (tmp_path / "routine.py").exists()


def test_saved_adaptive_hook_source_and_manifest_pin_are_inlined(
    tmp_path, monkeypatch
):
    from microclaw.hook_manager import save_hook
    import microclaw.hook_manager as manager

    hooks_dir = tmp_path / "hooks"
    monkeypatch.setattr(manager, "HOOKS_DIR", hooks_dir)
    monkeypatch.setattr(manager, "MANIFEST", hooks_dir / "manifest.json")
    hook_source = (
        "from microclaw.hook_decisions import ContinueSurvey, HookResult\n"
        "class Saved:\n"
        "    def analyze_frame(self, image, metadata):\n"
        "        return HookResult({}, actions=(ContinueSurvey(),))\n"
    )
    save_hook("saved", hook_source, "continue", source="user_provided")
    manifest = json.loads((hooks_dir / "manifest.json").read_text(encoding="utf-8"))
    _, result, source = export(tmp_path, [call("run_timelapse", {
        "n_frames": 2, "interval_s": 0, "save_dir": "session",
        "hook_strategy": "saved",
    })])
    assert result["emitted_calls"] == 1
    assert hook_source not in source
    assert "class Saved:" in source
    assert "from microclaw" not in source
    assert manifest["saved"]["sha256"] in source
    assert "UntrustedHookAdapter(Saved(" in source


def test_adaptive_export_has_no_microclaw_runtime_references(tmp_path):
    _, _, source = export(tmp_path, [call("run_timelapse", {
        "n_frames": 2, "interval_s": 0, "save_dir": "session",
        "hook_strategy": "snr_observer",
    })])
    # These are durable data identifiers, not runtime dependencies. They are
    # the only deliberate occurrences of the project name in the artifact.
    assert source.count("microclaw") == 3
    assert '"microclaw.analysis-observation/v1"' in source
    assert '"microclaw.image_analysis.compute_stats"' in source
    assert '"microclaw_refocused"' in source
    import ast
    assert not [node for node in ast.walk(ast.parse(source))
                if isinstance(node, (ast.Import, ast.ImportFrom))
                and ((isinstance(node, ast.ImportFrom)
                      and (node.module or "").startswith("microclaw"))
                     or (isinstance(node, ast.Import)
                         and any(a.name.startswith("microclaw")
                                 for a in node.names)))]


def test_emitted_adaptive_log_is_beside_script_and_preserves_first_run(tmp_path):
    recorded_log = tmp_path / "recorded" / "quality_survey_hook.log"
    _, _, source = export(tmp_path, [call("run_timelapse", {
        "n_frames": 1, "interval_s": 0, "save_dir": "session",
        "hook_strategy": "snr_observer", "log_path": str(recorded_log),
    })])
    executable = source.replace(
        "from pycromanager import Acquisition, Core, multi_d_acquisition_events", ""
    )

    class Acquisition:
        def __init__(self, **_kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def acquire(self, _events): pass

    def run():
        namespace = {
            "__file__": str(tmp_path / "routine.py"),
            "Core": lambda: SimpleNamespace(), "Acquisition": Acquisition,
            "multi_d_acquisition_events": lambda **kwargs: [kwargs],
        }
        exec(compile(executable, "routine.py", "exec"), namespace)
        return Path(namespace["_log_path"])

    first = run()
    first.write_text("first run", encoding="utf-8")
    second = run()
    second.write_text("second run", encoding="utf-8")

    assert first.parent == tmp_path
    assert first.name == "quality_survey_hook.log"
    assert second.parent == tmp_path
    assert second != first
    assert first.read_text(encoding="utf-8") == "first run"


def test_adaptive_export_refuses_a_stripped_import_it_cannot_resolve(
    tmp_path, monkeypatch
):
    from microclaw.hook_manager import save_hook
    import microclaw.hook_manager as manager

    hooks_dir = tmp_path / "hooks"
    monkeypatch.setattr(manager, "HOOKS_DIR", hooks_dir)
    monkeypatch.setattr(manager, "MANIFEST", hooks_dir / "manifest.json")
    save_hook(
        "missing_import",
        "from microclaw.image_analysis import genuinely_absent\n"
        "class MissingImport:\n"
        "    def analyze_frame(self, image, metadata):\n"
        "        return genuinely_absent(image)\n",
        "missing import", source="user_provided",
    )
    _, result, source = export(tmp_path, [call("run_timelapse", {
        "n_frames": 2, "interval_s": 0, "save_dir": "session",
        "hook_strategy": "missing_import",
    })])
    assert result["emitted_calls"] == 0
    assert "# NOT EMITTED: run_timelapse" in source
    assert "genuinely_absent" in source


def test_adaptive_survey_without_channel_replays_recorded_exposure(tmp_path):
    _, _, source = export(tmp_path, [call("run_adaptive_survey", {
        "protocol": "timelapse",
        "protocol_params": {"n_frames": 1, "interval_s": 0, "exposure_ms": 200},
        "positions": [{"name": "p0", "x_um": 1, "y_um": 2}],
        "save_dir": "session", "hook_strategy": "snr_observer",
    })])
    assert "guard.check_exposure(200)" in source
    assert "core.set_exposure(200)" in source


def test_refocusing_survey_emits_the_same_budgeted_second_look_program(
    tmp_path, monkeypatch
):
    """One assertion boundary pins the live budget contract to its export."""
    from microclaw.hook_manager import save_hook
    import microclaw.hook_manager as manager

    hooks_dir = tmp_path / "hooks"
    monkeypatch.setattr(manager, "HOOKS_DIR", hooks_dir)
    monkeypatch.setattr(manager, "MANIFEST", hooks_dir / "manifest.json")
    save_hook(
        "refocus",
        "from microclaw.hook_decisions import ContinueSurvey, HookResult, RequestAutofocus\n"
        "class Refocus:\n"
        "    def analyze_frame(self, image, metadata):\n"
        "        if metadata.get('microclaw_refocused'):\n"
        "            return HookResult({'second_look': True}, actions=(ContinueSurvey(),))\n"
        "        return HookResult({}, actions=(RequestAutofocus(),))\n",
        "refocus once", source="user_provided",
    )
    budget = {"max_exposures": 4, "z_range_um": 2.0, "z_step_um": 1.0,
              "method": "single_sweep", "settle_ms": 0}
    # Drive the live adapter with the same budget before inspecting its emitted
    # program. Four exposures buy exactly one 3-plane sweep plus one second look.
    import queue
    from microclaw.hook_decisions import UntrustedHookAdapter
    live_hook = manager.load_hook_class("refocus")()
    adapter = UntrustedHookAdapter(live_hook)
    candidates = queue.Queue()
    progress = tools.SurveyProgress(2)
    events = [{"axes": {"position": "p0"}, "x": 1.0, "y": 2.0},
              {"axes": {"position": "p1"}, "x": 3.0, "y": 4.0}]
    class LiveGuard:
        def check_z(self, _z): pass
        def check_xy(self, _x, _y): pass
    ctrl = SimpleNamespace(core=SimpleNamespace(get_position=lambda: 10.0))
    sweep = autofocus.SweepResult([9, 10, 11], [1, 2, 1], 10, True, [9, 10, 11])
    real_autofocus_passes = tools._run_autofocus_passes
    monkeypatch.setattr(tools, "_run_autofocus_passes", lambda *a: autofocus.AutofocusResult(
        sweep, None, 10, 10, True, False, None
    ))
    adapter.configure_autofocus(
        ctrl=ctrl, guard=LiveGuard(), sweep_exposures=3,
        focus_lock_check=lambda: {"engaged": False}, **budget,
    )
    adapter.configure_adaptive(events=events, candidates=candidates,
                               progress=progress, guard=LiveGuard(), max_events=3)
    metadata = {"PositionName": "p0", "XPosition_um_Intended": 1.0,
                "YPosition_um_Intended": 2.0}
    adapter.image_process_fn(np.zeros((2, 2)), metadata, object())
    adapter.image_process_fn(np.zeros((2, 2)), metadata, object())
    assert candidates.get_nowait()["axes"] == {"position": "p0", "refocus": 1}
    assert candidates.get_nowait()["axes"]["position"] == "p1"
    assert live_hook.__dict__ == {}  # no ctrl/guard/queue leaked to saved source
    assert any(r.get("reason") == "refocused and re-queued this tile"
               for r in adapter._log)
    assert any(r.get("result") == {"second_look": True} for r in adapter._log)
    assert adapter._log[-1]["reason"] == "planned event passed guard and committed reservation"
    monkeypatch.setattr(tools, "_run_autofocus_passes", real_autofocus_passes)

    _, result, source = export(tmp_path, [call("run_adaptive_survey", {
        "protocol": "timelapse", "protocol_params": {"n_frames": 1},
        "positions": [{"name": "p0", "x_um": 1, "y_um": 2}],
        "save_dir": "session", "hook_strategy": "refocus",
        "autofocus_budget": budget,
    })])

    assert result["emitted_calls"] == 1
    assert "# NOT EMITTED:" not in source
    assert "from microclaw" not in source
    assert "configure_autofocus(ctrl=mm, guard=guard, focus_lock_check=None" in source
    assert "Standalone scripts cannot query focus-lock state" in source
    assert "'max_exposures': 4" in source
    assert "sweep_exposures=3" in source
    assert "max_events=len(events) + 1" in source
    # Completion is sized the same way the live runner sizes it: the plan, plus
    # whatever the adapter's expect_one_more() adds as re-exposures are actually
    # queued. Sizing it by the authorized budget instead stalled three of four
    # budgeted surveys on M5 2026-08-11, and the emitted script carried the same
    # arithmetic, so the divergence would have been latent here too.
    assert "progress = SurveyProgress(len(events))" in source
    assert "expect_one_more" in source, (
        "the emitted decision loop must raise its own completion total"
    )
    assert "microclaw_refocused" in source
    compile(source, "routine.py", "exec")
    assert not _undefined_emitted_names(source)


def test_emitted_multiframe_survey_counts_the_event_plan(tmp_path):
    _, _, source = export(tmp_path, [call("run_adaptive_survey", {
        "protocol": "timelapse",
        "protocol_params": {"n_frames": 3, "interval_s": 0},
        "positions": [
            {"name": "p0", "x_um": 1, "y_um": 2},
            {"name": "p1", "x_um": 3, "y_um": 4},
        ],
        "save_dir": "session", "hook_strategy": "snr_observer",
    })])
    assert "progress = SurveyProgress(len(events))" in source
    assert "progress = SurveyProgress(2)" not in source


@pytest.mark.parametrize("tool, params", [
    ("run_timelapse", {
        "n_frames": 2, "interval_s": 0, "save_dir": "session",
        "hook_strategy": "snr_observer", "channel": "DAPI",
    }),
    ("run_adaptive_survey", {
        "protocol": "timelapse",
        "protocol_params": {"n_frames": 1, "channel": "DAPI"},
        "positions": [{"name": "p0", "x_um": 1, "y_um": 2}],
        "save_dir": "session", "hook_strategy": "snr_observer",
    }),
])
def test_adaptive_emitters_use_shared_channel_group(
    tmp_path, monkeypatch, tool, params
):
    import microclaw.authorization as authorization

    monkeypatch.setattr(authorization, "CHANNEL_CONFIG_GROUP", "RigChannels")
    _, _, source = export(tmp_path, [call(tool, params)])
    assert "'channel_group': 'RigChannels'" in source
    assert "'channel_group': 'Channel'" not in source


def test_emitted_adaptive_seed_check_refuses_out_of_bounds_before_acquisition(
    tmp_path,
):
    import sys
    guard = Guard(tmp_path)
    guard._c = SimpleNamespace(
        stage=SimpleNamespace(
            x_min=-10, x_max=10, y_min=-10, y_max=10, z_min=-5, z_max=5,
        ),
        camera=SimpleNamespace(max_exposure_ms=100),
        analysis=SimpleNamespace(min_snr=None),
    )
    records = [call("run_adaptive_survey", {
        "protocol": "timelapse", "protocol_params": {"n_frames": 1},
        "positions": [{"name": "unsafe", "x_um": 11, "y_um": 0}],
        "save_dir": "session", "hook_strategy": "snr_observer",
    })]
    tools.export_session_script(None, guard, "routine.py", records)
    source = (tmp_path / "routine.py").read_text(encoding="utf-8").replace(
        "from pycromanager import Acquisition, Core, multi_d_acquisition_events", ""
    )

    class Acquisition:
        entered = False
        def __init__(self, **_kwargs): pass
        def __enter__(self):
            self.entered = True
            return self
        def __exit__(self, *_args): pass
        def acquire(self, _events): pass

    module_names = (
        "microclaw", "microclaw.hooks", "microclaw.hook_decisions",
        "microclaw.autofocus",
    )
    original_modules = {name: sys.modules.get(name) for name in module_names}
    try:
        with pytest.raises(Exception, match="recorded maximum 10"):
            exec(compile(source, "routine.py", "exec"), {
                "__file__": str(tmp_path / "routine.py"),
                "Core": lambda: SimpleNamespace(),
                "Acquisition": Acquisition,
                "multi_d_acquisition_events": lambda **kwargs: [kwargs],
            })
    finally:
        for name, module in original_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
    assert Acquisition.entered is False


@pytest.mark.parametrize("records", [
    pytest.param([call("snap_and_analyze", {})], id="analysis"),
    pytest.param(
        [call("run_autofocus", {"z_range_um": 2, "z_step_um": 0.5})], id="autofocus"
    ),
    pytest.param(
        completed_call("set_channel", {"preset": "640"}, M5_CHANNEL_RESULT),
        id="channel-verification",
    ),
    pytest.param(
        completed_call(
            "set_config_preset", {"group": "Camera", "preset": "Fast"},
            {**FLOAT_CHANNEL_RESULT, "config_group": "Camera"},
        ),
        id="config-preset-verification",
    ),
    *[
        pytest.param([call("run_timelapse", {
            "n_frames": 2, "interval_s": 0, "save_dir": "session",
            "hook_strategy": strategy, "hook_params": hook_params,
        })], id=f"adaptive-runner-{strategy}")
        for strategy, hook_params in (
            ("snr_observer", {}),
            ("position_filter", {}),
            ("intensity_adaptive", {"target_mean": 100}),
            ("focus_feedback", {}),
            ("autofocus_per_position", {"z_range_um": 2, "z_step_um": 0.5}),
        )
    ],
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
    _, _, source = export(tmp_path, records)
    assert "# NOT EMITTED:" not in source
    assert not _undefined_emitted_names(source)


def test_emitted_free_name_guard_detects_a_removed_inline(tmp_path):
    _, _, source = export(tmp_path, [call("run_timelapse", {
        "n_frames": 2, "interval_s": 0, "save_dir": "session",
        "hook_strategy": "snr_observer",
    })])
    broken = source.replace(inspect.getsource(image_analysis.resolve_min_snr), "")
    assert "resolve_min_snr" in _undefined_emitted_names(broken)

    _, _, survey_source = export(tmp_path, [call("run_adaptive_survey", {
        "protocol": "timelapse", "protocol_params": {"n_frames": 1},
        "positions": [{"name": "p0", "x_um": 1, "y_um": 2}],
        "save_dir": "session", "hook_strategy": "snr_observer",
    })])
    broken = survey_source.replace(inspect.getsource(tools.SurveyProgress), "")
    assert "SurveyProgress" in _undefined_emitted_names(broken)

    broken = survey_source.replace(inspect.getsource(controller.settle_stage_move), "")
    assert "settle_stage_move" in _undefined_emitted_names(broken)


def test_stage_move_contract_is_defined_once_however_many_moves(tmp_path):
    """The settlement helpers are preamble helpers, not per-call boilerplate.

    Emitting the contract inside each move emitter put a fresh copy of the
    constants, the exception class and both functions in front of every move; a
    25-tile session emits a wall of identical blocks. `_analysis_source` and
    `_adaptive_runner_source` are already inlined once from a body predicate --
    the comment at the assembly site says that is exactly why the predicate is
    computed from the rendered body.
    """
    _, _, source = export(tmp_path, [
        call("move_stage_z", {"z_um": 100.0, "absolute": True}),
        call("move_stage_z", {"z_um": 120.0, "absolute": True}),
        *completed_call(
            "move_named_stage", {"device": "TIRF Stage", "um": 5.0},
            {"device": "TIRF Stage", "requested_um": 5.0, "measured_um": 5.0,
             "tolerance_um": 0.5, "within_tolerance": True,
             "elapsed_s": 0.1, "last_device_status": "idle"},
        ),
    ])
    assert source.count("def settle_stage_move") == 1
    assert source.count("class StageMoveError") == 1
    assert source.count("STAGE_MOVE_TOLERANCE_UM = ") == 1
    assert not _undefined_emitted_names(source)


def test_emitted_stage_settle_uses_live_policy_constants(tmp_path, monkeypatch):
    monkeypatch.setattr(controller, "STAGE_MOVE_TOLERANCE_UM", 0.321)
    monkeypatch.setattr(controller, "STAGE_MOVE_TIMEOUT_S", 7.654)
    _, _, source = export(tmp_path, completed_call(
        "move_named_stage", {"device": "TIRF Stage", "um": 5.0},
        {"device": "TIRF Stage", "requested_um": 5.0, "measured_um": 5.0},
    ))
    assert "0.321" in source
    assert "7.654" in source


def test_a_session_that_writes_its_own_hook_still_exports_a_runnable_script(
    tmp_path, monkeypatch
):
    """The demo gate of 2026-08-10, reduced to a record.

    The agent was asked for a run that stops itself and a script to keep. It
    wrote a hook, saved it, ran an adaptive survey with it, and exported -- the
    exact workflow F14 exists for. The exporter emitted a complete adaptive
    program, and the script died three lines before reaching it, because
    `generate_and_save_hook` carried no export decoration and collected the
    default `raise RuntimeError` refusal.

    No offline test caught it because none exported a session that CREATED the
    hook it then used; every fixture referenced a hook that already existed.
    """
    from microclaw.hook_manager import save_hook
    import microclaw.hook_manager as manager

    hooks_dir = tmp_path / "hooks"
    monkeypatch.setattr(manager, "HOOKS_DIR", hooks_dir)
    monkeypatch.setattr(manager, "MANIFEST", hooks_dir / "manifest.json")
    hook_source = (
        "from microclaw.hook_decisions import ContinueSurvey, HookResult\n"
        "class Repeat:\n"
        "    def analyze_frame(self, image, metadata):\n"
        "        return HookResult({}, actions=(ContinueSurvey(),))\n"
    )
    save_hook("repeat", hook_source, "repeat", source="claude_generated")

    _, result, source = export(tmp_path, [
        call("generate_and_save_hook", {"name": "repeat", "code": hook_source,
                                        "description": "repeat"}),
        call("run_adaptive_survey", {
            "protocol": "timelapse", "protocol_params": {"n_frames": 1},
            "positions": [{"name": "Pos1", "x_um": 0.0, "y_um": 0.0}],
            "save_dir": "session", "hook_strategy": "repeat"}),
    ])

    assert "# RECORDED TOOL: generate_and_save_hook\n# No hardware-routine effect." in source
    assert "# NOT EMITTED:" not in source
    # The refusal's own shape, not a bare `raise RuntimeError`: the inlined
    # DeniedEventQueue legitimately raises one, and matching that read as a
    # failure against a correct fix.
    assert "raise RuntimeError('NOT EMITTED" not in source
    # The adaptive program is present AND reachable -- the ordering is the whole
    # finding, so assert the program rather than only the absence of the raise.
    assert result["emitted_calls"] == 1
    assert hook_source not in source
    assert "class Repeat:" in source
    assert "_survey_event_stream" in source
    assert not _undefined_emitted_names(source)


def top_level_assignments(source):
    """Names bound by a top-level assignment in an emitted script."""
    import ast

    return {t.id for node in ast.parse(source).body
            if isinstance(node, ast.Assign) for t in node.targets
            if isinstance(t, ast.Name)}


def test_a_session_without_an_adaptive_run_carries_no_adaptive_preamble(tmp_path):
    """The adaptive imports are conditional, like the blocks that need them.

    Found in the 2026-08-10 demo gate: a snap-only session exported a script
    carrying `hashlib`, `io`, `json`, `logging`, `queue`, `threading`, `sys`,
    `ModuleType`, `asdict`, `datetime` and an unused `logger`, none of which
    anything in it reached. Harmless to run and wrong for an artifact whose
    whole point is being readable and keepable.
    """
    import ast

    def top_level_imports(source):
        # Parsed, not substring-matched: `io` occurs inside `annotations`, which
        # is how the first version of this test failed against a correct fix.
        names = set()
        for node in ast.parse(source).body:
            if isinstance(node, ast.Import):
                names |= {a.asname or a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                names |= {a.asname or a.name for a in node.names}
        return names

    adaptive_only = {"hashlib", "io", "json", "logging", "queue", "threading",
                     "asdict", "datetime", "timezone"}

    _, _, snap = export(tmp_path, [call("snap_and_analyze", {})])
    assert not (top_level_imports(snap) & adaptive_only)
    assert "logger" not in top_level_assignments(snap)
    assert not _undefined_emitted_names(snap)

    # ...and the adaptive export still has every one of them, because the
    # inlined runner does reach them. Both directions, or this test would pass
    # on an exporter that simply stopped emitting the imports.
    _, _, adaptive = export(tmp_path, [call("run_timelapse", {
        "n_frames": 2, "interval_s": 0, "save_dir": "session",
        "hook_strategy": "snr_observer",
    })])
    assert adaptive_only <= top_level_imports(adaptive)
    assert not ({"sys", "ModuleType"} & top_level_imports(adaptive))
    assert "logger" in top_level_assignments(adaptive)
    assert not _undefined_emitted_names(adaptive)


def test_adaptive_export_refuses_when_safety_constraints_are_unavailable(tmp_path):
    """Unreadable limits refuse the adaptive STEP, not the whole export.

    Coordinator fix, review round 2. The first version of this raised
    `CannotEmit` from the top of `export_session_script`, so a session with
    forty good calls and one adaptive call wrote no file at all. Every other
    refusal in this exporter degrades to a `# NOT EMITTED` line inside an
    otherwise complete script, and this one now does too -- while still never
    emitting an unbounded `_LIMITS` under a header that claims recorded bounds.
    """
    guard = Guard(tmp_path)
    del guard._c
    records = (
        completed_call("go_to_position", {"name": "p1"},
                       {"x_um": 1.5, "y_um": 2.5, "z_um": 3.5})
        + [call("run_timelapse", {
            "n_frames": 2, "interval_s": 0, "save_dir": "session",
            "hook_strategy": "snr_observer"})]
    )
    tools.export_session_script(None, guard, "routine.py", records)
    source = (tmp_path / "routine.py").read_text(encoding="utf-8")

    assert "# NOT EMITTED: run_timelapse" in source
    assert "safety constraints are unavailable" in source
    # The unrelated step still exported, and no unbounded guard was written.
    assert "core.set_xy_position(1.5, 2.5)" in source
    assert "_LIMITS" not in source


def test_offline_analysis_does_not_kill_the_script_it_follows(tmp_path):
    """M5 2026-08-11 round 2: the adaptive program ran, then line 1908 raised.

    `run_analysis_on_saved_dataset` was one of the undecorated registry tools,
    so it collected the default refusal and planted a RuntimeError at the end of
    a script whose acquisition had already succeeded. It reads saved pixels and
    is documented as never forwarding `ctrl`, so it has no hardware-routine
    effect to reproduce -- the same call 43h made for `generate_and_save_hook`.
    """
    from microclaw.tools import run_analysis_on_saved_dataset
    assert run_analysis_on_saved_dataset._microclaw_emits_nothing is True

    _, result, source = export(tmp_path, [
        call("run_adaptive_survey", {
            "protocol": "timelapse", "protocol_params": {"n_frames": 1},
            "positions": [{"name": "p0", "x_um": 1, "y_um": 2}],
            "save_dir": "session", "hook_strategy": "snr_observer",
        }),
        call("run_analysis_on_saved_dataset", {
            "dataset_path": "session/survey_1", "adapter": "frame_statistics",
            "axis_selection": {}, "input_kind": "frame", "parameters": {},
            "output_dir": "session",
        }),
    ])
    assert "# NOT EMITTED:" not in source
    # Narrow on purpose: the inlined DeniedEventQueue raises a RuntimeError of
    # its own, and asserting on the bare class name matches that instead.
    assert "raise RuntimeError('NOT EMITTED" not in source
    assert result["emitted_calls"] == 1
    compile(source, "routine.py", "exec")


def test_move_named_stage_emits_its_resolved_absolute_target(tmp_path):
    # A relative call resolves against the live position before writing, so the
    # emitted script must carry the resolved target rather than the raw `um` --
    # re-resolving in the standalone script would land somewhere else.
    _, _, source = export(tmp_path, completed_call(
        "move_named_stage",
        {"device": "TIRF Stage", "um": -40.0, "absolute": False},
        {"device": "TIRF Stage", "requested_um": 1460.0,
         "achieved_um": 1461.2, "error_um": 1.2},
    ))
    assert "core.set_position('TIRF Stage', 1460.0)" in source
    assert "settle_stage_move(core, 'TIRF Stage', 1460.0)" in source
    assert "-40.0" not in source
    assert "# NOT EMITTED" not in source
    assert '"within_tolerance": False' in source


def test_emitted_named_stage_move_runs_success_and_failure_paths(tmp_path):
    _, _, source = export(tmp_path, completed_call(
        "move_named_stage",
        {"device": "TIRF Stage", "um": 5.0, "absolute": True},
        {"device": "TIRF Stage", "requested_um": 5.0,
         "measured_um": 5.0, "tolerance_um": 0.5,
         "within_tolerance": True},
    ))
    runnable = source.replace(
        "from pycromanager import Acquisition, Core, multi_d_acquisition_events", ""
    )

    class FakeCore:
        measured = 5.0
        def set_position(self, _device, _target): pass
        def device_busy(self, _device): return False
        def get_position(self, _device): return self.measured

    exec(compile(runnable, "routine.py", "exec"), {
        "__file__": str(tmp_path / "routine.py"), "Core": FakeCore,
    })

    FakeCore.measured = 27.85
    fast_failure = runnable.replace("STAGE_MOVE_TIMEOUT_S = 10.0", "STAGE_MOVE_TIMEOUT_S = 0.0")
    with pytest.raises(RuntimeError) as caught:
        exec(compile(fast_failure, "routine.py", "exec"), {
            "__file__": str(tmp_path / "routine.py"), "Core": FakeCore,
        })
    assert type(caught.value).__name__ == "StageMoveError"
    assert caught.value.result["within_tolerance"] is False


@pytest.mark.parametrize(("label", "result"), [
    # "nothing" -> the SKIPPED comment; "partial" -> refuse()'s NOT EMITTED
    # comment. Both interpolate the recorded reason, and both used to break.
    ("nothing", {"error": _MULTILINE_BRIDGE_ERROR}),
    ("partial", {"results": [{"error": _MULTILINE_BRIDGE_ERROR}]}),
])
def test_a_multiline_recorded_error_stays_inside_its_comment(tmp_path, label, result):
    """A Java bridge exception must not make the whole session unexportable.

    Measured on M5, 2026-08-17, during block 52b's gate: a serial timeout on
    `Thorlabs ELL17/ELL20` recorded a multi-line Java stack trace, and the
    `# SKIPPED` comment carried only its first line -- so every frame after it
    was emitted as bare Python and `ast.parse` refused the entire export. The
    agent then hand-wrote a script, which is the failure design/52 exists to
    remove. Pre-existing on `main`, found by this gate.
    """
    _, _, source = export(
        tmp_path,
        completed_call("run_multiposition_acquisition", {"positions": [0]}, result),
    )

    compile(source, str(tmp_path / "routine.py"), "exec")
    for line in source.splitlines():
        if "ZMQServer.runMethod" in line:
            stripped = line.lstrip()
            assert stripped.startswith("#") or stripped.startswith("raise "), line
    assert "Serial command failed" in source


def test_emitted_property_run_actually_dispatches_its_writes(tmp_path, monkeypatch):
    """Run the emitted script, do not just compile it.

    M5, 2026-08-17: the exported script compiled, passed every grep in the
    runbook, and then died on its FIRST property write with
    `AttributeError: 'types.SimpleNamespace' object has no attribute
    'refresh_gui'`. `_apply_property` calls `ctrl.refresh_gui()`, which the live
    controller has and the emitted stand-in did not. Compilation could never
    have caught it; executing the dispatch does.
    """
    from microclaw.hook_manager import save_hook
    import microclaw.hook_manager as manager

    hooks_dir = tmp_path / "hooks"
    monkeypatch.setattr(manager, "HOOKS_DIR", hooks_dir)
    monkeypatch.setattr(manager, "MANIFEST", hooks_dir / "manifest.json")
    save_hook(
        "planned_property", "class PlannedProperty:\n"
        "    def analyze_frame(self, image, metadata):\n"
        "        return None\n",
        "planned_property", source="user_provided",
    )
    _, _, source = export(tmp_path, [call("run_timelapse", {
        "n_frames": 2, "interval_s": 1, "save_dir": "session", "name": "props",
        "hook_strategy": "planned_property",
        "property_envelope": {"device": "Wheel", "property": "State",
                              "allowed_values": ["A", "B"], "max_writes": 2,
                              "restore": "leave"},
        "hook_action_plan": [
            {"hook_event_index": 0, "actions": [{"kind": "SetDeviceProperty", "value": "A"}]},
            {"hook_event_index": 1, "actions": [{"kind": "SetDeviceProperty", "value": "B"}]},
        ],
    })])

    writes, repaints = [], []

    class DemoCore:
        def set_property(self, d, p, v): writes.append((d, p, v))
        def get_property(self, d, p): return writes[-1][2] if writes else "A"
        def get_property_type(self, _d, _p): return "String"
        def wait_for_device(self, _d): pass

    class DemoStudio:
        def app(self): return self
        def refresh_gui_from_cache(self): repaints.append(True)

    # The emitted helper imports Studio lazily, so patch it where it is looked up.
    monkeypatch.setattr("pycromanager.Studio", DemoStudio)

    class FakeAcquisition:
        """Drives pre_hardware_hook_fn per event, as the engine does."""
        def __init__(self, **kwargs): self._hooks = kwargs
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def acquire(self, events):
            for event in events:
                self._hooks["pre_hardware_hook_fn"](event)

    def fake_events(**kwargs):
        return [{"axes": {"time": i}} for i in range(kwargs.get("num_time_points", 2))]

    # Strip the real pycromanager import so the fakes above are what runs, the
    # same way the rejected-then-successful test does.
    runnable = re.sub(r"^from pycromanager import .*$", "", source, flags=re.M)
    exec(compile(runnable, "routine.py", "exec"), {
        "__file__": str(tmp_path / "routine.py"),
        "Core": DemoCore, "Acquisition": FakeAcquisition,
        "multi_d_acquisition_events": fake_events,
    })

    assert writes == [("Wheel", "State", "A"), ("Wheel", "State", "B")]
    assert repaints, "the emitted script never repainted; an EMU rig needs this"


def test_emitted_adaptive_run_executes_decision_loop_and_pre_hardware_move(tmp_path, monkeypatch):
    """The export carries the rule, not a trace, and runs in engine order."""
    from microclaw.hook_manager import save_hook
    import microclaw.hook_manager as manager

    hooks_dir = tmp_path / "hooks"
    monkeypatch.setattr(manager, "HOOKS_DIR", hooks_dir)
    monkeypatch.setattr(manager, "MANIFEST", hooks_dir / "manifest.json")
    save_hook(
        "adaptive_stage",
        "from microclaw.hook_decisions import (ContinueSurvey, HookResult, MoveNamedStage, StopSurvey)\n"
        "class AdaptiveStage:\n"
        "    def analyze_frame(self, image, metadata):\n"
        "        if metadata['PositionName'] == 'p0':\n"
        "            target = float(image[0, 0]) + 5.0\n"
        "            return HookResult({'target': target}, (MoveNamedStage(target), ContinueSurvey()))\n"
        "        return HookResult({}, (StopSurvey(),))\n",
        "adaptive_stage", source="user_provided",
    )
    _, result, source = export(tmp_path, [call("run_adaptive_survey", {
        "protocol": "timelapse", "protocol_params": {"n_frames": 1, "interval_s": 0},
        "positions": [{"name": "p0", "x_um": 0.0, "y_um": 0.0},
                      {"name": "p1", "x_um": 1.0, "y_um": 0.0}],
        "save_dir": "session", "name": "adaptive", "hook_strategy": "adaptive_stage",
        "named_stage_envelope": {"device": "Axis", "min_um": 0.0, "max_um": 10.0,
                                 "max_writes": 1, "restore": "leave"},
    })])
    assert result["emitted_calls"] == 1, (result, source)
    assert "class AdaptiveStage" in source and "_survey_event_stream" in source
    assert "_axes_plan =" not in source and "input(" not in source

    writes = []
    class DemoCore:
        def set_position(self, device, value): writes.append((device, value))
        def get_position(self, device=None): return writes[-1][1] if writes else 0.0
        def wait_for_device(self, device): pass
        def set_exposure(self, value): pass

    class FakeAcquisition:
        def __init__(self, **kwargs):
            self._hooks, self._events = kwargs, None
            self._event_queue = queue.Queue()
            self._acq = type("State", (), {"is_finished": lambda self: False})()
        def __enter__(self): return self
        def acquire(self, events): self._events = events
        def __exit__(self, *_args):
            for original in self._events:
                event = {key: original[key] for key in ("axes", "x", "y") if key in original}
                self._hooks["pre_hardware_hook_fn"](event)
                label = event["axes"]["position"]
                value = 2 if label == "p0" else 9
                self._hooks["image_process_fn"](
                    np.array([[value]], dtype=np.uint16),
                    {"PositionName": label, "Axes": {},
                     "XPosition_um_Intended": event.get("x"),
                     "YPosition_um_Intended": event.get("y")}, None)
            return False

    def fake_events(**kwargs):
        return [{"axes": {"position": label}, "x": xy[0], "y": xy[1]}
                for label, xy in zip(kwargs["position_labels"], kwargs["xy_positions"])]

    runnable = re.sub(r"^from pycromanager import .*$", "", source, flags=re.M)
    exec(compile(runnable, "routine.py", "exec"), {
        "__file__": str(tmp_path / "routine.py"), "Core": DemoCore,
        "Acquisition": FakeAcquisition, "multi_d_acquisition_events": fake_events,
    })
    assert writes == [("Axis", 7.0)]


@pytest.mark.parametrize("emitter", ["adaptive", "fixed"])
@pytest.mark.parametrize("restoration_fails", [False, True])
def test_emitted_hardware_restoration_preserves_acquisition_failure(
    tmp_path, monkeypatch, emitter, restoration_fails
):
    """A standalone run restores after an acquisition fault without hiding it."""
    from microclaw.hook_manager import save_hook
    import microclaw.hook_manager as manager

    hooks_dir = tmp_path / "hooks"
    monkeypatch.setattr(manager, "HOOKS_DIR", hooks_dir)
    monkeypatch.setattr(manager, "MANIFEST", hooks_dir / "manifest.json")
    save_hook(
        "faulting_stage",
        "from microclaw.hook_decisions import (ContinueSurvey, HookResult, MoveNamedStage)\n"
        "class FaultingStage:\n"
        "    def analyze_frame(self, image, metadata):\n"
        "        if metadata['PositionName'] == 'p0':\n"
        "            return HookResult({}, (MoveNamedStage(7.0), ContinueSurvey()))\n"
        "        return HookResult({}, (ContinueSurvey(),))\n",
        "faulting_stage", source="user_provided",
    )
    envelope = {"device": "Axis", "min_um": 0.0, "max_um": 10.0,
                "max_writes": 2, "restore": "entry"}
    if emitter == "adaptive":
        tool_name = "run_adaptive_survey"
        params = {
            "protocol": "timelapse",
            "protocol_params": {"n_frames": 1, "interval_s": 0},
            "positions": [{"name": "p0", "x_um": 0.0, "y_um": 0.0},
                          {"name": "p1", "x_um": 1.0, "y_um": 0.0}],
            "save_dir": "session", "name": "adaptive",
            "hook_strategy": "faulting_stage", "named_stage_envelope": envelope,
        }
    else:
        tool_name = "run_timelapse"
        params = {
            "n_frames": 2, "interval_s": 0, "save_dir": "session",
            "name": "fixed", "hook_strategy": "faulting_stage",
            "named_stage_envelope": envelope,
            "hook_action_plan": [
                {"hook_event_index": 0, "actions": [
                    {"kind": "MoveNamedStage", "position_um": 7.0}]},
                {"hook_event_index": 1, "actions": []},
            ],
        }
    _, result, source = export(tmp_path, [call(tool_name, params)])
    assert result["emitted_calls"] == 1, (result, source)

    attempts = []
    position = 3.0

    class DemoCore:
        def set_position(self, device, value):
            nonlocal position
            attempts.append((device, value))
            if value == 7.0:
                raise RuntimeError("acquisition serial fault")
            if restoration_fails:
                raise RuntimeError("restoration serial fault")
            position = value
        def get_position(self, device=None): return position
        def wait_for_device(self, device): pass
        def set_exposure(self, value): pass

    class FakeAcquisition:
        def __init__(self, **kwargs):
            self._hooks = kwargs
            self._deferred_events = None
            self._event_queue = queue.Queue()
            self._acq = type("State", (), {"is_finished": lambda self: False})()
        def __enter__(self): return self
        def acquire(self, events):
            if isinstance(events, list):
                self._drive(events)
            else:
                self._deferred_events = events
        def __exit__(self, *_args):
            if self._deferred_events is not None:
                self._drive(self._deferred_events, analyze=True)
            return False
        def _drive(self, events, analyze=False):
            for original in events:
                event = {key: original[key] for key in ("axes", "x", "y")
                         if key in original}
                self._hooks["pre_hardware_hook_fn"](event)
                if analyze:
                    self._hooks["image_process_fn"](
                        np.array([[1]], dtype=np.uint16),
                        {"PositionName": "p0", "Axes": {},
                         "XPosition_um_Intended": event.get("x"),
                         "YPosition_um_Intended": event.get("y")}, None)

    def fake_events(**kwargs):
        if "position_labels" in kwargs:
            return [{"axes": {"position": label}, "x": xy[0], "y": xy[1]}
                    for label, xy in zip(kwargs["position_labels"],
                                         kwargs["xy_positions"])]
        return [{"axes": {"time": i}}
                for i in range(kwargs.get("num_time_points", 1))]

    runnable = re.sub(r"^from pycromanager import .*$", "", source, flags=re.M)
    namespace = {
        "__file__": str(tmp_path / "routine.py"), "Core": DemoCore,
        "Acquisition": FakeAcquisition, "multi_d_acquisition_events": fake_events,
    }
    with pytest.raises(RuntimeError, match="acquisition serial fault") as caught:
        exec(compile(runnable, "routine.py", "exec"), namespace)

    assert attempts == [("Axis", 7.0), ("Axis", 3.0)]
    if restoration_fails:
        assert "restoration serial fault" in str(caught.value)
        assert not hasattr(namespace["hook"], "_named_stage_restoration")
        assert namespace["hook"]._log[-1]["restoration"] is True
        assert namespace["hook"]._log[-1]["decision"] == "failed"
    else:
        assert namespace["hook"]._named_stage_restoration == {
            "policy": "entry", "entry_um": 3.0,
            "last_known_um": 3.0, "restored": True,
        }


@pytest.mark.parametrize(("envelope_key", "envelope", "expected"), [
    ("property_envelope",
     {"device": "Wheel", "property": "State", "allowed_values": ["A", "B"],
      "max_writes": 2, "restore": "leave"},
     "_PROPERTY_ENVELOPE"),
    ("named_stage_envelope",
     {"device": "Axis", "min_um": 0.0, "max_um": 10.0, "max_writes": 2,
      "restore": "leave"},
     "_NAMED_STAGE_ENVELOPE"),
])
def test_emitted_script_states_its_envelope_and_never_blocks_on_stdin(
    tmp_path, monkeypatch, envelope_key, envelope, expected
):
    """Print the envelope; do not prompt.

    Operator decision, 2026-08-17, after the M5 gate: `Type YES to continue:` is
    invisible under output redirection -- the runbook's own `| Out-File` swallowed
    it and the script looked hung -- and a run carrying both envelopes prompted
    twice. Running the script is the consent; the bounds, budget, guard and
    read-back are what make it safe, and they are unchanged. The *print* stays,
    because it is now the only place the script says what it will move and within
    what limits (design/38 F9: nothing silent).
    """
    from microclaw.hook_manager import save_hook
    import microclaw.hook_manager as manager

    hooks_dir = tmp_path / "hooks"
    monkeypatch.setattr(manager, "HOOKS_DIR", hooks_dir)
    monkeypatch.setattr(manager, "MANIFEST", hooks_dir / "manifest.json")
    save_hook(
        "planned", "class Planned:\n"
        "    def analyze_frame(self, image, metadata):\n"
        "        return None\n",
        "planned", source="user_provided",
    )
    action = ({"kind": "SetDeviceProperty", "value": "A"}
              if envelope_key == "property_envelope"
              else {"kind": "MoveNamedStage", "position_um": 1.0})
    _, _, source = export(tmp_path, [call("run_timelapse", {
        "n_frames": 1, "interval_s": 1, "save_dir": "s", "name": "r",
        "hook_strategy": "planned", envelope_key: envelope,
        "hook_action_plan": [{"hook_event_index": 0, "actions": [action]}],
    })])

    assert "input(" not in source
    assert "HOOK HARDWARE CONTROL FOR THIS RUN" in source
    # Declarative, not a request: nothing is being asked any more.
    assert "ALLOW HOOK HARDWARE" not in source
    assert expected in source
    # The bound itself must survive, not just the device name: the print is the
    # whole disclosure now.
    assert ("approved {_property_bound}" in source
            or "approved interval" in source)


def test_emitted_crop_refuses_a_frame_the_region_does_not_fit(tmp_path, monkeypatch):
    """The live tools refuse an out-of-frame region; the exported script must too.

    numpy slicing TRUNCATES rather than raising, so an emitted crop with no
    bounds check measures the metric over whatever pixels happen to exist and
    reports the number as if nothing were wrong. `_validate_metric_region` lives
    in `run_autofocus`/`snap_and_analyze`, neither of which is emitted, so the
    standalone script carries no check of its own unless the inlined code has
    one. A rig whose camera ROI is smaller than it was when the session ran is
    exactly the case design/54 refuses.
    """
    _, _, source = export(tmp_path, [call("run_autofocus", {
        "z_range_um": 2, "z_step_um": 1, "method": "sweep", "settle_ms": 0,
        "region": [0, 0, 16, 16],
    })])
    frame = np.zeros((8, 8), dtype=np.uint16)          # smaller than the region

    class FakeCore:
        def __init__(self): self.position = 50.0
        def get_position(self, _device=None): return self.position
        def get_focus_device(self): return "Z"
        def set_position(self, z): self.position = float(z)
        def device_busy(self, _device): return False
        def wait_for_device(self, _device): pass
        def snap_image(self): pass
        def get_bytes_per_pixel(self): return 2
        def get_number_of_components(self): return 1
        def get_tagged_image(self):
            return SimpleNamespace(pix=frame, tags={"Width": 8, "Height": 8})

    monkeypatch.setattr("pycromanager.Core", FakeCore)
    runnable = re.sub(r"^from pycromanager import .*$", "", source, flags=re.M)
    namespace = {
        "__file__": str(tmp_path / "routine.py"), "Core": FakeCore,
        "Acquisition": object, "multi_d_acquisition_events": lambda **kwargs: [],
    }
    with pytest.raises(RuntimeError) as excinfo:
        exec(compile(runnable, "routine.py", "exec"), namespace)
    assert "[0, 0, 16, 16]" in str(excinfo.value)
    assert "[8, 8]" in str(excinfo.value)


def test_emitted_snap_crop_refuses_a_frame_the_region_does_not_fit(
    tmp_path, monkeypatch
):
    """Same guarantee on the snap path, whose emitted crop is a bare slice."""
    _, _, source = export(tmp_path, [call(
        "snap_and_analyze", {"region": [4, 4, 16, 16]}
    )])
    frame = np.zeros((8, 8), dtype=np.uint16)

    class FakeCore:
        def snap_image(self): pass
        def get_bytes_per_pixel(self): return 2
        def get_number_of_components(self): return 1
        def get_tagged_image(self):
            return SimpleNamespace(pix=frame, tags={"Width": 8, "Height": 8})

    monkeypatch.setattr("pycromanager.Core", FakeCore)
    runnable = re.sub(r"^from pycromanager import .*$", "", source, flags=re.M)
    namespace = {
        "__file__": str(tmp_path / "routine.py"), "Core": FakeCore,
        "Acquisition": object, "multi_d_acquisition_events": lambda **kwargs: [],
    }
    with pytest.raises(RuntimeError) as excinfo:
        exec(compile(runnable, "routine.py", "exec"), namespace)
    assert "[4, 4, 16, 16]" in str(excinfo.value)
    assert "[8, 8]" in str(excinfo.value)


def test_emitted_regionless_run_scales_its_guard_to_the_live_frame(
    tmp_path, monkeypatch
):
    """The standalone script must refuse noise on a cropped sensor too.

    A regionless sweep on a small camera ROI averages the metric over as few
    pixels as a small software region does. The threshold is derived at runtime
    from the frame the script actually finds, not baked in at export, so the
    same script is correct on a rig whose ROI differs from the session's.
    """
    _, _, source = export(tmp_path, [call("run_autofocus", {
        "z_range_um": 4, "z_step_um": 1, "method": "sweep", "settle_ms": 0,
    })])
    size = 20
    rng = np.random.default_rng(73)
    frames = [
        np.clip(rng.normal(1000, 25, (size, size)), 0, 65535).astype(np.uint16)
        for _ in range(5)
    ]
    # The fixture must be noise that WOULD have converged, or this proves nothing.
    metrics = [image_analysis.tenengrad(f) for f in frames]
    assert autofocus.curve_contrast(metrics) > autofocus.MIN_CONTRAST

    class FakeCore:
        def __init__(self): self.z, self._i = 50.0, 0
        def get_position(self, _device=None): return self.z
        def device_busy(self, _device): return False
        def get_focus_device(self): return "Z"
        def set_position(self, z): self.z = float(z)
        def wait_for_device(self, _device): pass
        def snap_image(self): pass
        def get_bytes_per_pixel(self): return 2
        def get_number_of_components(self): return 1
        def get_image_width(self): return size
        def get_image_height(self): return size
        def get_tagged_image(self):
            frame = frames[min(self._i, len(frames) - 1)]
            self._i += 1
            return SimpleNamespace(pix=frame, tags={"Width": size, "Height": size})

    core = FakeCore()
    monkeypatch.setattr("pycromanager.Core", lambda *a, **k: core)
    runnable = re.sub(r"^from pycromanager import .*$", "", source, flags=re.M)
    namespace = {
        "__file__": str(tmp_path / "routine.py"), "Core": lambda *a, **k: core,
        "Acquisition": object, "multi_d_acquisition_events": lambda **kwargs: [],
    }
    exec(compile(runnable, "routine.py", "exec"), namespace)

    assert namespace["autofocus_result"].converged is False
    assert namespace["autofocus_result"].moved is False
    assert core.z == 50.0
