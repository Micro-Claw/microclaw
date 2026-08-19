import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, call

import numpy as np
import pytest
import tifffile

from microclaw import tools
from microclaw.hook_decisions import EmitArtifact, HookResult, UntrustedHookAdapter
from microclaw.hook_manager import saved_hook_source_refusal, validate_hook_contract
from microclaw.safety import (
    ForbiddenProperty, IlluminationConstraints, NamedStageLimits,
    SafetyConstraints, SafetyGuard, SafetyViolation,
)


FIXTURES = Path(__file__).parent / "fixtures" / "hooks"


def _load(group, name):
    path = FIXTURES / group / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"{group}_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_m5_fixture_registry_source_survey():
    surveyed = {}
    for group in ("m5_legacy", "m5_migrated"):
        surveyed[group] = {}
        for path in sorted((FIXTURES / group).glob("*.py")):
            code = path.read_text(encoding="utf-8")
            surveyed[group][path.stem] = (
                saved_hook_source_refusal(code)["reasons"]
                + validate_hook_contract(code)
            )

    assert set(surveyed["m5_legacy"]) == {
        "filament_position_filter", "mosaic_cell_counter",
        "mosaic_stitcher", "mosaic_stitcher_rot",
    }
    assert all(reasons for reasons in surveyed["m5_legacy"].values())
    assert set(surveyed["m5_migrated"]) == {
        "filament_position_filter", "mosaic_cell_counter",
        "mosaic_stitcher", "mosaic_stitcher_rot",
        "uv_activation", "uv_activation_wind_down",
    }
    assert surveyed["m5_migrated"] == {
        name: [] for name in surveyed["m5_migrated"]
    }


def _guard(max_power=20, factor=2):
    return SafetyGuard(SafetyConstraints(illumination=IlluminationConstraints(
        power_properties=[ForbiddenProperty("Laser", "Power")],
        max_power_percent=max_power, max_power_step_factor=factor,
    )))


def test_envelope_confirmation_precedes_reservation(monkeypatch, tmp_path):
    order = []
    ctrl = MagicMock()
    ctrl.core.get_property.return_value = "1"
    hook = UntrustedHookAdapter(type("Hook", (), {"analyze_frame": lambda *a: HookResult({})})())
    monkeypatch.setattr(tools, "_resolve_hook", lambda *a: hook)
    monkeypatch.setattr(tools, "_build_acquisition_events", lambda **k: [{}])
    monkeypatch.setattr(tools, "plan_events", lambda *a: object())
    monkeypatch.setattr(tools, "CONFIRM_FN", lambda text, kind: order.append("confirm") or True)
    reservation = MagicMock(has_overrun=False)
    monkeypatch.setattr(tools, "_authorize_acquisition", lambda *a: order.append("reserve") or reservation)
    monkeypatch.setattr(tools, "_acquire_with_hooks", lambda *a, **k: str(tmp_path / "data"))
    tools.run_timelapse(
        ctrl, _guard(), 1, 0, str(tmp_path), hook_strategy="generated",
        illumination_envelope={"device": "Laser", "property": "Power",
                               "max_power_percent": 10, "max_writes": 2},
    )
    assert order == ["confirm", "reserve"]


def test_declined_envelope_stops_before_reservation(monkeypatch, tmp_path):
    ctrl = MagicMock()
    ctrl.core.get_property.return_value = "1"
    hook = UntrustedHookAdapter(type("Hook", (), {"analyze_frame": lambda *a: HookResult({})})())
    monkeypatch.setattr(tools, "_resolve_hook", lambda *a: hook)
    monkeypatch.setattr(tools, "_build_acquisition_events", lambda **k: [{}])
    monkeypatch.setattr(tools, "CONFIRM_FN", lambda *a, **k: False)
    reserve = MagicMock()
    monkeypatch.setattr(tools, "_authorize_acquisition", reserve)
    with pytest.raises(SafetyViolation, match="not started"):
        tools.run_timelapse(
                ctrl, _guard(), 1, 0, str(tmp_path), hook_strategy="generated",
            illumination_envelope={"device": "Laser", "property": "Power",
                                   "max_power_percent": 10, "max_writes": 2},
        )
    reserve.assert_not_called()


def test_illumination_only_confirmation_contract_is_unchanged(monkeypatch, tmp_path):
    ctrl = MagicMock()
    ctrl.core.get_property.return_value = "1"
    hook = UntrustedHookAdapter(object())
    seen = []
    monkeypatch.setattr(
        tools, "CONFIRM_FN",
        lambda text, kind: seen.append((text, kind)) or False,
    )
    with pytest.raises(
        SafetyViolation,
        match=r"User declined hook illumination envelope for Laser\.Power; acquisition was not started",
    ):
        tools._configure_hook_capabilities(
            hook, ctrl, _guard(), str(tmp_path), "run",
            {"device": "Laser", "property": "Power",
             "max_power_percent": 10, "max_writes": 2}, None,
        )
    assert seen == [(
        "AUTHORIZE UNATTENDED HOOK ILLUMINATION: Laser.Power\n"
        "Ceiling: 10% (2 accepted writes maximum).\n"
        "Generated hook code will drive this power unattended, per frame, "
        "for the duration of the run. It cannot enable a shutter or turn light on.",
        "illumination",
    )]


def test_declined_named_stage_confirmation_means_no_acquisition_or_write(
    monkeypatch, tmp_path
):
    ctrl = MagicMock()
    ctrl.core.get_position.return_value = 15
    hook = UntrustedHookAdapter(object())
    acquire = MagicMock()
    monkeypatch.setattr(tools, "_resolve_hook", lambda *args: hook)
    monkeypatch.setattr(tools, "_build_acquisition_events", lambda **kwargs: [{"axes": {}}])
    monkeypatch.setattr(tools, "CONFIRM_FN", lambda *args, **kwargs: False)
    monkeypatch.setattr(tools, "_acquire_with_hooks", acquire)
    guard = SafetyGuard(SafetyConstraints(
        named_stages=[NamedStageLimits("fixture-stage", 10, 20)]
    ))
    with pytest.raises(SafetyViolation, match="declined.*not started"):
        tools.run_timelapse(
            ctrl, guard, 1, 0, str(tmp_path), hook_strategy="saved",
            named_stage_envelope={
                "device": "fixture-stage", "min_um": 10, "max_um": 20,
                "max_writes": 1, "restore": "leave",
            },
            hook_action_plan=[{
                "hook_event_index": 0,
                "actions": [{"kind": "MoveNamedStage", "position_um": 15}],
            }],
        )
    acquire.assert_not_called()
    ctrl.core.set_position.assert_not_called()


def test_fixed_named_stage_plan_over_budget_refuses_during_validation(
    monkeypatch, tmp_path
):
    ctrl = MagicMock()
    ctrl.core.get_position.return_value = 15
    confirm = MagicMock(return_value=True)
    monkeypatch.setattr(tools, "CONFIRM_FN", confirm)
    guard = SafetyGuard(SafetyConstraints(
        named_stages=[NamedStageLimits("fixture-stage", 10, 20)]
    ))
    with pytest.raises(ValueError, match="reserved for restoration"):
        tools._configure_hook_capabilities(
            UntrustedHookAdapter(object()), ctrl, guard, str(tmp_path), "run",
            None, None,
            {"device": "fixture-stage", "min_um": 10, "max_um": 20,
             "max_writes": 2, "restore": "entry"},
            [
                {"hook_event_index": 0, "actions": [
                    {"kind": "MoveNamedStage", "position_um": 11}
                ]},
                {"hook_event_index": 1, "actions": [
                    {"kind": "MoveNamedStage", "position_um": 12}
                ]},
            ],
            [{"axes": {"time": 0}}, {"axes": {"time": 1}}],
        )
    confirm.assert_not_called()
    ctrl.core.set_position.assert_not_called()


def test_fixed_plan_duplicate_generated_axes_refuses_during_validation(
    monkeypatch, tmp_path
):
    ctrl = MagicMock()
    ctrl.core.get_position.return_value = 15
    confirm = MagicMock(return_value=True)
    monkeypatch.setattr(tools, "CONFIRM_FN", confirm)
    guard = SafetyGuard(SafetyConstraints(
        named_stages=[NamedStageLimits("fixture-stage", 10, 20)]
    ))

    with pytest.raises(ValueError, match=r"duplicate axes signature \{\}"):
        tools._configure_hook_capabilities(
            UntrustedHookAdapter(object()), ctrl, guard, str(tmp_path), "run",
            None, None,
            {"device": "fixture-stage", "min_um": 10, "max_um": 20,
             "max_writes": 2, "restore": "leave"},
            [
                {"hook_event_index": 0, "actions": []},
                {"hook_event_index": 1, "actions": []},
            ],
            [{"axes": {}}, {"axes": {}}],
        )

    confirm.assert_not_called()
    ctrl.core.set_position.assert_not_called()


@pytest.mark.parametrize(
    ("events", "message"),
    [
        ([{"axes": {}}, {"axes": {}}], r"duplicate axes signature \{\}"),
        ([{"axes": []}], r"generated event 0 has invalid axes"),
    ],
)
def test_property_only_plan_reports_indexed_invalid_or_duplicate_axes(
    monkeypatch, tmp_path, events, message
):
    ctrl = MagicMock()
    ctrl.core.get_allowed_property_values.return_value = MagicMock(size=lambda: 0)
    ctrl.core.get_property.return_value = "A"
    confirm = MagicMock(return_value=True)
    monkeypatch.setattr(tools, "CONFIRM_FN", confirm)
    plan = [
        {"hook_event_index": index, "actions": []}
        for index in range(len(events))
    ]
    with pytest.raises(ValueError, match=message):
        tools._configure_hook_capabilities(
            UntrustedHookAdapter(object()), ctrl, SafetyGuard(SafetyConstraints()),
            str(tmp_path), "run", None, None, None, plan, events,
            property_envelope={
                "device": "Wheel", "property": "State",
                "allowed_values": ["B"], "max_writes": 1,
                "restore": "leave",
            },
        )
    confirm.assert_not_called()


@pytest.mark.parametrize(
    ("restore", "positions", "expected_calls"),
    [
        ("leave", [15, 11, 12], [11.0, 12.0]),
        ("entry", [15, 11, 12, 15], [11.0, 12.0, 15.0]),
    ],
)
def test_fixed_plan_survives_queued_engine_closed_key_round_trip(
    monkeypatch, tmp_path, restore, positions, expected_calls
):
    """Exercise the same closed event-key boundary as AcqEng before callbacks."""
    ctrl = MagicMock()
    position = positions[0]
    def set_position(_device, target):
        nonlocal position
        position = target
    ctrl.core.set_position.side_effect = set_position
    ctrl.core.get_position.side_effect = lambda _device: position
    hook = UntrustedHookAdapter(object())
    monkeypatch.setattr(tools, "_resolve_hook", lambda *args: hook)
    monkeypatch.setattr(tools, "CONFIRM_FN", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        tools, "_build_acquisition_events",
        lambda **kwargs: [{"axes": {"time": 0}}, {"axes": {"time": 1}}],
    )

    class KeyStrippingAcquisition:
        _keys = {
            "special", "min_start_time", "config_group", "exposure",
            "slm_pattern", "timeout_ms", "axes", "stage_positions", "z",
            "x", "y", "camera", "tags", "properties",
        }

        def __init__(self, **kwargs):
            self.callbacks = kwargs
            self._dataset_disk_location = str(tmp_path / "dataset")
            self.queued_events = None

        def __enter__(self): return self
        def __exit__(self, *_exc):
            for event in self.queued_events:
                round_tripped = {key: value for key, value in event.items()
                                 if key in self._keys}
                self.callbacks["pre_hardware_hook_fn"](round_tripped)
            return False

        def acquire(self, events):
            self.queued_events = events

    monkeypatch.setattr(tools, "Acquisition", KeyStrippingAcquisition)
    guard = SafetyGuard(SafetyConstraints(
        named_stages=[NamedStageLimits("fixture-stage", 10, 20)],
    ))
    monkeypatch.setattr(guard, "resolve_in_workspace", lambda path: path)

    result = tools.run_timelapse(
        ctrl, guard, 2, 1, str(tmp_path), hook_strategy="saved",
        named_stage_envelope={
            "device": "fixture-stage", "min_um": 10, "max_um": 20,
            "max_writes": 2 if restore == "leave" else 3, "restore": restore,
        },
        hook_action_plan=[
            {"hook_event_index": 0, "actions": [
                {"kind": "MoveNamedStage", "position_um": 11},
            ]},
            {"hook_event_index": 1, "actions": [
                {"kind": "MoveNamedStage", "position_um": 12},
            ]},
        ],
    )

    assert "error" not in result
    assert [call.args for call in ctrl.core.set_position.call_args_list] == [
        *(('fixture-stage', position) for position in expected_calls),
    ]
    assert result["named_stage_restoration"] == {
        "policy": restore,
        "entry_um": 15.0,
        "last_known_um": 12.0 if restore == "leave" else 15.0,
        "restored": restore == "entry",
    }


def test_adaptive_tool_restores_named_stage_exactly_once_after_engine_exit(
    monkeypatch, tmp_path
):
    """Drive the live tool through deferred callbacks and an entry restore."""
    import queue
    import numpy as np
    from microclaw.hook_decisions import (
        ContinueSurvey, HookResult, MoveNamedStage, StopSurvey,
    )

    class Hook:
        def analyze_frame(self, _image, metadata):
            if metadata["PositionName"] == "p0":
                return HookResult({}, (MoveNamedStage(110), ContinueSurvey()))
            return HookResult({}, (StopSurvey(),))

    position = {"value": 100.0}
    writes = []
    ctrl = MagicMock()
    ctrl.core.get_position.side_effect = lambda device=None: position["value"]
    ctrl.core.set_position.side_effect = lambda device, value: (
        writes.append((device, float(value))), position.__setitem__("value", float(value))
    )[-1]
    hook = UntrustedHookAdapter(Hook())
    monkeypatch.setattr(tools, "_resolve_hook", lambda *args: hook)
    monkeypatch.setattr(tools, "CONFIRM_FN", lambda *args, **kwargs: True)

    class EngineOrderedAcquisition:
        _keys = {"axes", "x", "y", "z", "exposure", "config_group",
                 "min_start_time", "timeout_ms", "camera", "tags",
                 "properties", "slm_pattern", "special", "stage_positions"}
        def __init__(self, **callbacks):
            self.callbacks = callbacks
            self._event_queue = queue.Queue()
            self._acq = type("State", (), {"is_finished": lambda self: False})()
            self._dataset_disk_location = str(tmp_path / "dataset")
        def __enter__(self): return self
        def acquire(self, events): self.events = events
        def __exit__(self, *_exc):
            for original in self.events:
                event = {k: v for k, v in original.items() if k in self._keys}
                self.callbacks["pre_hardware_hook_fn"](event)
                label = event["axes"]["position"]
                self.callbacks["image_process_fn"](
                    np.zeros((1, 1)),
                    {"PositionName": label, "Axes": {},
                     "XPosition_um_Intended": event.get("x"),
                     "YPosition_um_Intended": event.get("y")}, None)
            return False

    monkeypatch.setattr(tools, "Acquisition", EngineOrderedAcquisition)
    monkeypatch.setattr(tools, "_authorize_acquisition", lambda *args, **kwargs: None)
    guard = SafetyGuard(SafetyConstraints(
        named_stages=[NamedStageLimits("Axis", 90, 120)],
    ))
    monkeypatch.setattr(guard, "resolve_in_workspace", lambda path: path)
    result = tools.run_adaptive_survey(
        ctrl, guard, protocol="timelapse", save_dir=str(tmp_path),
        hook_strategy="saved", positions=[
            {"name": "p0", "x_um": 0.0, "y_um": 0.0},
            {"name": "p1", "x_um": 1.0, "y_um": 0.0},
        ], protocol_params={"n_frames": 1, "interval_s": 0},
        named_stage_envelope={"device": "Axis", "min_um": 90, "max_um": 120,
                              "max_writes": 3, "restore": "entry"},
    )
    assert "error" not in result
    assert writes == [("Axis", 110.0), ("Axis", 100.0)]
    assert result["named_stage_restoration"]["restored"] is True


def test_property_plan_survives_engine_key_stripping_and_deferred_callbacks(
    monkeypatch, tmp_path
):
    ctrl = MagicMock()
    state = {"value": "A"}
    ctrl.core.get_allowed_property_values.return_value = MagicMock(
        size=lambda: 2, get=lambda index: ("A", "B")[index]
    )
    ctrl.core.get_property.side_effect = lambda *_args: state["value"]
    ctrl.core.get_property_type.return_value = "String"
    ctrl.core.has_property_limits.return_value = False
    ctrl.core.get_focus_device.return_value = "Z"
    ctrl.core.get_camera_device.return_value = "Camera"
    ctrl.core.get_xy_stage_device.return_value = "XY"
    ctrl.core.set_property.side_effect = lambda _d, _p, value: state.update(value=value)
    hook = UntrustedHookAdapter(object())
    monkeypatch.setattr(tools, "_resolve_hook", lambda *args: hook)
    monkeypatch.setattr(tools, "CONFIRM_FN", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        tools, "_build_acquisition_events",
        lambda **kwargs: [{"axes": {"time": 0}}, {"axes": {"time": 1}}],
    )

    class KeyStrippingAcquisition:
        _keys = {"axes", "stage_positions", "x", "y", "z", "exposure",
                 "config_group", "min_start_time", "timeout_ms", "camera",
                 "tags", "properties", "slm_pattern", "special"}
        def __init__(self, **kwargs):
            self.callbacks = kwargs
            self._dataset_disk_location = str(tmp_path / "dataset")
        def __enter__(self): return self
        def acquire(self, events): self.events = events
        def __exit__(self, *_exc):
            for event in self.events:
                self.callbacks["pre_hardware_hook_fn"](
                    {key: value for key, value in event.items() if key in self._keys}
                )

    monkeypatch.setattr(tools, "Acquisition", KeyStrippingAcquisition)
    guard = SafetyGuard(SafetyConstraints())
    monkeypatch.setattr(guard, "resolve_in_workspace", lambda path: path)
    result = tools.run_timelapse(
        ctrl, guard, 2, 1, str(tmp_path), hook_strategy="saved",
        property_envelope={"device": "Wheel", "property": "State",
                           "allowed_values": ["B"], "max_writes": 2,
                           "restore": "leave"},
        hook_action_plan=[
            {"hook_event_index": 0, "actions": [
                {"kind": "SetDeviceProperty", "value": "B"}]},
            {"hook_event_index": 1, "actions": []},
        ],
    )
    assert "error" not in result
    assert ctrl.core.set_property.call_args_list == [call("Wheel", "State", "B")]
    assert result["property_restoration"]["last_known_value"] == "B"


def test_config_ceiling_refuses_wrongly_wide_envelope(tmp_path):
    ctrl = MagicMock()
    ctrl.core.get_property.return_value = "1"
    with pytest.raises(ValueError, match="configured"):
        tools._configure_hook_capabilities(
            UntrustedHookAdapter(object()), ctrl, _guard(max_power=5),
            str(tmp_path), "run",
            {"device": "Laser", "property": "Power",
             "max_power_percent": 6, "max_writes": 1}, None,
        )


@pytest.mark.parametrize("smuggled", [
    {"illumination_envelope": {}}, {"device": "Laser"}, {"path": "/tmp/x"},
])
def test_hook_params_cannot_smuggle_capabilities(monkeypatch, smuggled):
    class Hook:
        def __init__(self, **kwargs): self.kwargs = kwargs
        def analyze_frame(self, image, metadata): return HookResult({})
    monkeypatch.setattr("microclaw.hook_manager.list_saved_hooks", lambda: ["h"])
    monkeypatch.setattr("microclaw.hook_manager.load_hook_class", lambda name: Hook)
    adapter = tools._resolve_hook(MagicMock(), _guard(), "h", smuggled, None)
    assert adapter.hook.kwargs == {}


def test_composition_does_not_launder_ctrl_guard_queue_or_hash_check(monkeypatch):
    received = {}

    class SavedHook:
        def __init__(self, **kwargs):
            received.update(kwargs)

        def analyze_frame(self, _image, _metadata):
            return HookResult({})

    monkeypatch.setattr("microclaw.hook_manager.list_saved_hooks", lambda: ["saved"])
    monkeypatch.setattr(
        "microclaw.hook_manager.load_hook_class", lambda name: SavedHook
    )
    ctrl, guard = object(), _guard()
    composed = tools._resolve_hooks(
        ctrl, guard,
        ["snr_observer", "saved"],
        [{}, {"ctrl": ctrl, "guard": guard, "event_queue": object()}],
        None,
    )

    assert received == {}
    assert isinstance(composed.named_hooks[1][1], UntrustedHookAdapter)
    # load_hook_class is the manifest/hash-verifying loader. Composition must
    # use it rather than importing or constructing saved source directly.
    assert composed.named_hooks[1][0] == "saved"

    def hash_refusal(_name):
        raise ValueError("Saved hook hash does not match its manifest")

    monkeypatch.setattr("microclaw.hook_manager.load_hook_class", hash_refusal)
    with pytest.raises(ValueError, match="hash does not match"):
        tools._resolve_hooks(
            ctrl, guard, ["snr_observer", "saved"], [{}, {}], None
        )


@pytest.mark.parametrize("name,class_name", [
    ("filament_position_filter", "FilamentPositionFilter"),
    ("mosaic_cell_counter", "MosaicCellCounter"),
    ("mosaic_stitcher", "MosaicStitcher"),
    ("mosaic_stitcher_rot", "MosaicStitcherRot"),
])
def test_original_m5_hooks_remain_refused_at_resolve(monkeypatch, name, class_name):
    cls = getattr(_load("m5_legacy", name), class_name)
    monkeypatch.setattr("microclaw.hook_manager.list_saved_hooks", lambda: [name])
    monkeypatch.setattr("microclaw.hook_manager.load_hook_class", lambda n: cls)
    with pytest.raises(ValueError, match="writes its own log"):
        tools._resolve_hook(MagicMock(), _guard(), name, {}, None)


def test_filament_migration_preserves_scores_and_discards():
    old = _load("m5_legacy", "filament_position_filter").FilamentPositionFilter()
    new = _load("m5_migrated", "filament_position_filter").FilamentPositionFilter()
    image = np.zeros((12, 12), dtype=np.uint16)
    assert old._score(image) == new._score(image)
    result = new.analyze_frame(image, {"Axes": {"position": "p0"}})
    assert result.measurements["filament_score"] == round(old._score(image)[0], 5)
    assert result.actions[0].kind == "DiscardFrame"


def test_cell_counter_migration_preserves_count_and_state():
    old_cls = _load("m5_legacy", "mosaic_cell_counter").MosaicCellCounter
    new_cls = _load("m5_migrated", "mosaic_cell_counter").MosaicCellCounter
    old, new = old_cls(min_area_um2=0, snr_min=0), new_cls(min_area_um2=0, snr_min=0)
    image = np.array([[0, 0, 0], [0, 10, 0], [0, 0, 0]], dtype=np.uint16)
    md = {"Axes": {"position": "p0"}, "XPosition_um_Intended": 0,
          "YPosition_um_Intended": 0}
    old.tiles["p0"] = (0, 0, image)
    result = new.analyze_frame(image, md)
    assert result.measurements["running_cell_count"] == old._count_mosaic()[0]
    assert len(new.tiles) == 1


def test_uv_activation_fixture_reaches_authorized_ceiling(tmp_path):
    hook = _load("m5_migrated", "uv_activation").UVActivation(
        start_percent=1, step_percent=2, ceiling_percent=5,
    )
    core = MagicMock()
    adapter = UntrustedHookAdapter(hook)
    adapter.configure_illumination(
        core=core, guard=_guard(max_power=5, factor=3), device="Laser",
        property="Power", max_power_percent=5, max_writes=3, initial_value=1,
    )
    for _ in range(3):
        adapter.image_process_fn(np.zeros((2, 2)), {}, object())
    assert [float(c.args[2]) for c in core.set_property.call_args_list] == [3, 5, 5]


@pytest.mark.parametrize("name,class_name,kwargs", [
    ("mosaic_stitcher", "MosaicStitcher", {}),
    ("mosaic_stitcher_rot", "MosaicStitcherRot", {"rot90_k": 1}),
])
def test_stitcher_migrations_preserve_canvas_and_parent_writes(name, class_name,
                                                               kwargs, tmp_path):
    old_cls = getattr(_load("m5_legacy", name), class_name)
    new_cls = getattr(_load("m5_migrated", name), class_name)
    old, new = old_cls(n_tiles=1, **kwargs), new_cls(n_tiles=1, **kwargs)
    image = np.arange(6, dtype=np.uint16).reshape(2, 3)
    old.tiles["p0"] = (0, 0, image)
    expected = old._assemble()
    md = {"Axes": {"position": "p0"}, "XPosition_um_Intended": 0,
          "YPosition_um_Intended": 0}
    adapter = UntrustedHookAdapter(new)
    adapter.configure_artifacts(target_dir=tmp_path, max_artifact_bytes=10000,
                                max_count=1, max_total_bytes=10000)
    adapter.image_process_fn(image, md, object())
    written = tifffile.imread(tmp_path / "mosaic.tiff")
    assert written.dtype == expected.dtype
    np.testing.assert_array_equal(written, expected)
    observation = next(r for r in adapter._log if "schema" in r)
    assert len(observation["artifact_sha256"]) == 64


def test_artifact_directory_tracks_the_acquisitions_renamed_dataset(
    monkeypatch, tmp_path
):
    class Hook:
        def analyze_frame(self, image, metadata):
            return HookResult({}, (EmitArtifact("result.bin", b"x"),))

    class RenamedAcquisition:
        def __init__(self, **kwargs):
            self._dataset_disk_location = str(tmp_path / "run_1")
            self.image_process_fn = kwargs["image_process_fn"]

        def __enter__(self): return self
        def __exit__(self, *_exc): return None

        def acquire(self, _events):
            self.image_process_fn(np.zeros((1, 1)), {}, object())

    adapter = UntrustedHookAdapter(Hook())
    adapter.configure_artifacts(
        target_dir=tmp_path / "run" / "artifacts",
        max_artifact_bytes=10, max_count=1, max_total_bytes=10,
    )
    monkeypatch.setattr(tools, "Acquisition", RenamedAcquisition)

    path = tools._acquire_with_hooks(_guard(), str(tmp_path), "run", [], hook=adapter)

    assert path == str(tmp_path / "run_1")
    assert (tmp_path / "run_1" / "artifacts" / "result.bin").read_bytes() == b"x"
    assert not (tmp_path / "run" / "artifacts").exists()


def test_successive_renamed_runs_do_not_cross_collide(monkeypatch, tmp_path):
    class Hook:
        def analyze_frame(self, image, metadata):
            return HookResult({}, (EmitArtifact("same.bin", b"x"),))

    class SuccessiveAcquisition:
        next_suffix = 1

        def __init__(self, **kwargs):
            suffix = type(self).next_suffix
            type(self).next_suffix += 1
            self._dataset_disk_location = str(tmp_path / f"run_{suffix}")
            self.image_process_fn = kwargs["image_process_fn"]

        def __enter__(self): return self
        def __exit__(self, *_exc): return None

        def acquire(self, _events):
            self.image_process_fn(np.zeros((1, 1)), {}, object())

    monkeypatch.setattr(tools, "Acquisition", SuccessiveAcquisition)
    for _ in range(2):
        adapter = UntrustedHookAdapter(Hook())
        adapter.configure_artifacts(
            target_dir=tmp_path / "run" / "artifacts",
            max_artifact_bytes=10, max_count=1, max_total_bytes=10,
        )
        tools._acquire_with_hooks(_guard(), str(tmp_path), "run", [], hook=adapter)
        assert adapter._log[-1]["decision"] == "accepted"

    assert (tmp_path / "run_1" / "artifacts" / "same.bin").exists()
    assert (tmp_path / "run_2" / "artifacts" / "same.bin").exists()


def test_artifact_bind_is_idempotent_and_preserves_budget_state(tmp_path):
    adapter = UntrustedHookAdapter(object())
    adapter.configure_artifacts(
        target_dir=tmp_path / "predicted",
        max_artifact_bytes=11, max_count=2, max_total_bytes=12,
    )
    state = adapter._artifact_context["state"]
    state.update(count=1, total_bytes=7)

    adapter.bind_artifact_directory(tmp_path / "actual")
    adapter.bind_artifact_directory(tmp_path / "actual")

    assert adapter._artifact_context == {
        "target_dir": tmp_path / "actual", "max_artifact_bytes": 11,
        "max_count": 2, "max_total_bytes": 12, "state": state,
    }
    assert state == {"count": 1, "total_bytes": 7}


def test_artifact_bind_without_budget_is_a_noop(tmp_path):
    adapter = UntrustedHookAdapter(object())
    adapter.bind_artifact_directory(tmp_path / "actual")
    assert adapter._artifact_context is None


def test_artifact_bind_uses_dataset_path_fallback(monkeypatch, tmp_path):
    class AcquisitionWithoutLocation:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *_exc): return None
        def acquire(self, _events): pass

    adapter = UntrustedHookAdapter(object())
    adapter.configure_artifacts(
        target_dir=tmp_path / "run" / "artifacts",
        max_artifact_bytes=10, max_count=1, max_total_bytes=10,
    )
    monkeypatch.setattr(tools, "Acquisition", AcquisitionWithoutLocation)

    assert tools._acquire_with_hooks(
        _guard(), str(tmp_path), "run", [], hook=adapter
    ) == str(tmp_path / "run")
    assert adapter._artifact_context["target_dir"] == tmp_path / "run" / "artifacts"


def test_wind_down_fixture_survives_an_exhausted_budget(tmp_path):
    """R5b as a unit test: the ramp spends the budget, the wind-down still lands.

    This is the operator ruling made mechanical — end-of-run illumination policy
    lives in the hook, which is only safe if the hook cannot be refused while
    lowering power.
    """
    hook = _load("m5_migrated", "uv_activation_wind_down").UVActivationWithWindDown(
        start_percent=5, step_percent=1, ceiling_percent=12, wind_down_after=3,
    )
    core = MagicMock()
    adapter = UntrustedHookAdapter(hook)
    adapter.configure_illumination(
        core=core, guard=_guard(max_power=20, factor=3), device="Laser",
        property="Power", max_power_percent=8, max_writes=2, initial_value=5,
    )
    for _ in range(5):
        adapter.image_process_fn(np.zeros((2, 2)), {}, object())

    written = [float(call.args[2]) for call in core.set_property.call_args_list]
    # 6 and 7 spend the budget; 8 is refused for exhaustion; frames 4 and 5 are
    # the wind-down and land anyway.
    assert written == [6.0, 7.0, 0.0, 0.0]
    assert adapter._illumination_context["remaining"] == 0
    reasons = [r.get("reason") for r in adapter._log if r.get("decision") == "refused"]
    assert reasons == ["authorized illumination write budget exhausted"]


def test_envelope_wider_than_configured_bounds_refuses_before_any_exposure(
    monkeypatch, tmp_path
):
    """M5, 2026-08-17: the dialog offered an interval the rig would refuse.

    The approved envelope read `18000-21100 um` while `named_stages` capped the
    axis at 20000, so the operator approved a reach the guard could not honour
    and the run died mid-sweep on the target that crossed the bound. Approval
    cannot widen a configured bound (design/52 §"Approval and bounds are
    different controls"), so the envelope's own endpoints are checked at
    validation, before the confirmation and before any exposure.
    """
    ctrl = MagicMock()
    ctrl.core.get_position.return_value = 18673
    hook = UntrustedHookAdapter(object())
    acquire = MagicMock()
    confirmed = []
    monkeypatch.setattr(tools, "_resolve_hook", lambda *args: hook)
    monkeypatch.setattr(tools, "_build_acquisition_events", lambda **kwargs: [{"axes": {}}])
    monkeypatch.setattr(tools, "CONFIRM_FN",
                        lambda *args, **kwargs: confirmed.append(args) or True)
    monkeypatch.setattr(tools, "_acquire_with_hooks", acquire)
    guard = SafetyGuard(SafetyConstraints(
        named_stages=[NamedStageLimits("fixture-stage", 0, 20000)]
    ))
    with pytest.raises(SafetyViolation, match="exceeds the maximum allowed"):
        tools.run_timelapse(
            ctrl, guard, 1, 0, str(tmp_path), hook_strategy="saved",
            named_stage_envelope={
                "device": "fixture-stage", "min_um": 18000, "max_um": 21100,
                "max_writes": 2, "restore": "leave",
            },
            hook_action_plan=[{
                "hook_event_index": 0,
                "actions": [{"kind": "MoveNamedStage", "position_um": 18500}],
            }],
        )
    assert confirmed == [], "the operator must not be asked to approve an unreachable interval"
    acquire.assert_not_called()
    ctrl.core.set_position.assert_not_called()


def test_adaptive_hardware_failure_reports_dataset_frames_and_last_state(
    monkeypatch, tmp_path
):
    """M5, 2026-08-17: three aborted survey runs reported only an error string.

    `_acquire_with_hooks` raises `_HookedAcquisitionFailure` carrying the
    dataset path, the frames exposed and the last known hardware state, and the
    two fixed runners translate it. The survey runner did not, so every adaptive
    abort lost all three and the operator had to read the axis by hand.
    """
    class Scoring:
        def analyze_frame(self, image, metadata):
            return None

    hook = UntrustedHookAdapter(Scoring())
    hook.log_path = str(tmp_path / "hook.json")
    monkeypatch.setattr(tools, "_resolve_hook", lambda *args: hook)
    monkeypatch.setattr(tools, "CONFIRM_FN", lambda *args, **kwargs: True)
    monkeypatch.setattr(tools, "_authorize_acquisition", lambda *args, **kwargs: None)

    def explode(*_args, **_kwargs):
        # The real failure path builds this payload in _acquire_with_hooks from
        # the live envelope context; what is under test here is whether the
        # survey runner passes it on rather than stringifying it away.
        raise tools._HookedAcquisitionFailure(
            RuntimeError("named-stage move failed: serial command failed"),
            str(tmp_path / "survey_1"), frames_exposed=3,
            last_hardware_state={"device": "Axis", "position_um": 42.0},
        )

    monkeypatch.setattr(tools, "_acquire_with_hooks", explode)
    ctrl = MagicMock()
    guard = SafetyGuard(SafetyConstraints(
        named_stages=[NamedStageLimits("Axis", 0, 100)]
    ))
    monkeypatch.setattr(guard, "resolve_in_workspace", lambda path: path)
    result = tools.run_adaptive_survey(
        ctrl, guard, protocol="timelapse", save_dir=str(tmp_path),
        hook_strategy="saved",
        positions=[{"name": "p0", "x_um": 0.0, "y_um": 0.0},
                   {"name": "p1", "x_um": 1.0, "y_um": 0.0}],
        protocol_params={"n_frames": 1, "interval_s": 0},
        named_stage_envelope={"device": "Axis", "min_um": 0, "max_um": 100,
                              "max_writes": 2, "restore": "leave"},
    )
    assert result["dataset_path"] == str(tmp_path / "survey_1")
    assert result["frames_exposed"] == 3
    assert result["last_hardware_state"] == {"device": "Axis", "position_um": 42.0}
    assert result["log_path"] == hook.log_path
    assert "do not treat the run as untouched" in result["hint"]
