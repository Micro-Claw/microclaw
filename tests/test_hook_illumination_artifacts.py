import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest
import tifffile

from microclaw import tools
from microclaw.hook_decisions import EmitArtifact, HookResult, UntrustedHookAdapter
from microclaw.safety import (
    ForbiddenProperty, IlluminationConstraints, SafetyConstraints, SafetyGuard,
    SafetyViolation,
)


FIXTURES = Path(__file__).parent / "fixtures" / "hooks"


def _load(group, name):
    path = FIXTURES / group / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"{group}_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    tools.run_adaptive_timelapse(
        ctrl, _guard(), 1, 0, str(tmp_path), "generated",
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
        tools.run_adaptive_timelapse(
            ctrl, _guard(), 1, 0, str(tmp_path), "generated",
            illumination_envelope={"device": "Laser", "property": "Power",
                                   "max_power_percent": 10, "max_writes": 2},
        )
    reserve.assert_not_called()


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
