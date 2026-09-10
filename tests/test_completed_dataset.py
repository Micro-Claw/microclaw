import hashlib
import json
import threading
from pathlib import Path

import numpy as np
import pytest

from microclaw import completed_dataset
from microclaw.safety import AnalysisConstraints, SafetyConstraints, SafetyGuard


class FakeDataset:
    axes = {"time": [0, 1], "position": ["p0", "p1"]}

    def has_image(self, **coords):
        return coords in [
            {"time": 0, "position": "p0"},
            {"time": 0, "position": "p1"},
            {"time": 1, "position": "p0"},
            {"time": 1, "position": "p1"},
        ]

    def read_image(self, **coords):
        return np.full((2, 2), coords["time"] + (coords["position"] == "p1"), np.uint16)

    def read_metadata(self, **coords):
        return {"Axes": coords, "immutable": True}


@pytest.fixture
def offline_home(tmp_path, monkeypatch):
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    manifest = hooks / "manifest.json"
    monkeypatch.setattr(completed_dataset, "MANIFEST", manifest)
    monkeypatch.setattr(completed_dataset, "Dataset", lambda path: FakeDataset())
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "NDTiff.index").write_bytes(b"saved pixels")
    guard = SafetyGuard(SafetyConstraints(workspace_dir=str(tmp_path)))

    def save(name, code, **extra):
        path = hooks / f"{name}.py"
        # Write bytes and pin those same bytes, exactly as save_hook does.
        # Writing text here let Windows translate the newlines while the pin
        # was taken from the untranslated string, so every hook this fixture
        # built looked legacy-pinned on Windows and nowhere else.
        source_bytes = code.encode("utf-8")
        path.write_bytes(source_bytes)
        entry = {"path": str(path), "source": "user_provided",
                 "sha256": hashlib.sha256(source_bytes).hexdigest(),
                 "accepted_warnings": completed_dataset.lint_hook_code(code), **extra}
        data = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else {}
        data[name] = entry
        manifest.write_text(json.dumps(data), encoding="utf-8")

    return save, dataset, guard, tmp_path


def run(saved, name, **kwargs):
    save, dataset, guard, root = saved
    return completed_dataset.run_analysis_on_saved_dataset(
        guard, str(dataset), name, {"time": 0}, "frames", {},
        str(root / kwargs.pop("output", f"out-{name}")), **kwargs,
    )


def test_crlf_hook_uses_one_byte_hash_for_save_load_describe_and_offline(
    tmp_path, monkeypatch,
):
    """Simulate Windows text translation so this regression runs on POSIX."""
    from microclaw import hook_manager

    hooks = tmp_path / "hooks"
    manifest = hooks / "manifest.json"
    monkeypatch.setattr(hook_manager, "HOOKS_DIR", hooks)
    monkeypatch.setattr(hook_manager, "MANIFEST", manifest)
    monkeypatch.setattr(completed_dataset, "MANIFEST", manifest)

    code = (
        "class CrLf:\r\n"
        " def analyze_frame(self, image, metadata): return None\r\n"
        " def analyze_saved_frame(self, image, metadata, context): return {'ok': True}\r\n"
    )
    original_write_text = Path.write_text

    def windows_write_text(path, data, *args, **kwargs):
        if path.suffix == ".py":
            data = data.replace("\n", "\r\n")
        return original_write_text(path, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", windows_write_text)
    hook_manager.save_hook("crlf", code, "CRLF fixture", source="user_provided")

    raw = (hooks / "crlf.py").read_bytes()
    assert raw == code.encode("utf-8")
    assert hook_manager.load_hook_class("crlf").__name__ == "CrLf"
    described = hook_manager.describe_saved_hook("crlf")
    assert described["provenance"]["matches_manifest"] is True
    assert completed_dataset._load_saved_adapter("crlf")[0].__name__ == "CrLf"


def test_fixture_pins_the_bytes_it_writes_under_windows_translation(
    offline_home, monkeypatch,
):
    """The offline_home fixture must pin the bytes it wrote, not the string.

    Simulated rather than supplied: on Windows it is text-mode *translation*
    that makes the file bytes differ from the in-memory string, so feeding CRLF
    in directly would pass on POSIX either way and guard nothing. This fixture
    kept writing text after the product code stopped, which is why the Windows
    gate still showed all 23 failures with the repair in place.
    """
    original_write_text = Path.write_text

    def windows_write_text(path, data, *args, **kwargs):
        if path.suffix == ".py":
            data = data.replace("\n", "\r\n")
        return original_write_text(path, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", windows_write_text)
    save, *_ = offline_home
    save("fixture_bytes", "class FixtureBytes:\n"
         " def analyze_saved_frame(self, image, metadata, context): return {'ok': True}\n")
    assert completed_dataset._load_saved_adapter("fixture_bytes")[0].__name__ == "FixtureBytes"


def test_unknown_adapter_names_the_refusal_and_lists_saved_choices(offline_home):
    save, *_ = offline_home
    save("zebra", "class Zebra:\n def analyze_saved_frame(self, image, metadata, context): pass\n")
    save("alpha", "class Alpha:\n def analyze_saved_frame(self, image, metadata, context): pass\n")
    with pytest.raises(KeyError) as caught:
        completed_dataset._load_saved_adapter("not_an_adapter")
    message = str(caught.value)
    assert "No adapter named 'not_an_adapter'" in message
    assert message.index("Available built-in adapters") < message.index("Available saved adapters")
    assert "['connected_components', 'frame_statistics', 'ilastik_pixel_classification']" in message
    assert "Available saved adapters: ['alpha', 'zebra']" in message


def test_builtins_need_no_manifest_and_saved_hooks_cannot_shadow_them(offline_home):
    save, _, _, _ = offline_home
    save("frame_statistics", '''
class Shadow:
 def analyze_saved_frame(self, image, metadata, context): raise AssertionError("shadow ran")
''')
    completed_dataset.MANIFEST.unlink()
    result = run(offline_home, "frame_statistics")
    assert result["status"] == "completed"
    assert {item["status"] for item in result["observations"]} == {"observed"}
    assert result["analyzer"]["source"] == "builtin"
    assert result["parameters"] == {
        "min_snr": completed_dataset.resolve_min_snr()[0],
        "min_snr_source": "package_default_uncalibrated",
    }
    assert result["observations"][0]["parameters"] == result["parameters"]
    assert {"signal_coverage", "structure_coverage", "signal_concentration"} <= set(
        result["observations"][0]["result"]
    )
    assert len(result["analyzer"]["source_sha256"]) == 64
    int(result["analyzer"]["source_sha256"], 16)
    json.dumps(result, allow_nan=False)


def test_builtin_threshold_prefers_explicit_then_records_rig_configuration(offline_home):
    _, dataset, _, root = offline_home
    configured_guard = SafetyGuard(SafetyConstraints(
        workspace_dir=str(root), analysis=AnalysisConstraints(min_snr=7.5),
    ))
    configured = completed_dataset.run_analysis_on_saved_dataset(
        configured_guard, str(dataset), "frame_statistics", {"time": 0}, "frames", {},
        str(root / "configured-threshold"),
    )
    assert configured["parameters"] == {
        "min_snr": 7.5, "min_snr_source": "rig_config",
    }
    explicit = completed_dataset.run_analysis_on_saved_dataset(
        configured_guard, str(dataset), "frame_statistics", {"time": 0}, "frames",
        {"min_snr": 4.25}, str(root / "explicit-threshold"),
    )
    assert explicit["parameters"] == {
        "min_snr": 4.25, "min_snr_source": "explicit",
    }


def test_builtin_status_refusal_names_its_actual_allowed_statuses(offline_home, monkeypatch):
    class BadStatus:
        def __init__(self, min_snr, min_snr_source):
            pass

        def analyze_saved_frame(self, image, metadata, context):
            return {"result": {}, "status": "typo"}

    monkeypatch.setitem(completed_dataset.BUILTIN_ADAPTERS, "bad_status", BadStatus)
    result = run(offline_home, "bad_status")
    assert result["status"] == "failed"
    assert result["failure"]["message"] == (
        "Offline analysis status must be one of "
        "['observed', 'provisional', 'unverified']; 'typo' is not allowed for this adapter."
    )


def test_saved_resolution_still_requires_manifest_hash_lint_and_capabilities(offline_home):
    save, _, _, _ = offline_home
    code = "class Saved:\n def analyze_saved_frame(self, image, metadata, context): return {}\n"
    save("saved", code)
    manifest = json.loads(completed_dataset.MANIFEST.read_text(encoding="utf-8"))

    manifest["saved"]["sha256"] = "0" * 64
    completed_dataset.MANIFEST.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(RuntimeError, match="changed on disk"):
        completed_dataset._load_saved_adapter("saved")

    path = Path(manifest["saved"]["path"])
    warned = "import os\n" + code
    path.write_bytes(warned.encode("utf-8"))
    manifest["saved"]["sha256"] = hashlib.sha256(warned.encode("utf-8")).hexdigest()
    manifest["saved"]["accepted_warnings"] = []
    completed_dataset.MANIFEST.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(RuntimeError, match="lint warnings"):
        completed_dataset._load_saved_adapter("saved")

    manifest["saved"]["accepted_warnings"] = completed_dataset.lint_hook_code(warned)
    manifest["saved"]["offline_capabilities"] = ["ctrl"]
    completed_dataset.MANIFEST.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="forbidden capabilities"):
        completed_dataset._load_saved_adapter("saved")


def test_per_frame_selection_read_only_and_normalized_replay(offline_home):
    save, *_ = offline_home
    save("frame", '''
class Frame:
    def analyze_saved_frame(self, image, metadata, context):
        assert not image.flags.writeable
        return {"result": {"sum": int(image.sum()), "position": metadata["Axes"]["position"]},
                "status": "provisional", "analyzer": "counter", "analyzer_version": "1"}
''')
    first = run(offline_home, "frame", output="one")
    second = run(offline_home, "frame", output="two")
    assert first["status"] == "completed"
    assert [x["result"]["sum"] for x in first["observations"]] == [0, 4]
    assert first["scientific_payload_sha256"] == second["scientific_payload_sha256"]
    assert first["run_id"] != second["run_id"]
    assert first["calibration_used"] is False
    assert "calibration_identity" not in first


def test_batch_view_refuses_selection_escape_and_is_bounded(offline_home):
    save, *_ = offline_home
    save("batch", '''
class Batch:
    def analyze_completed_dataset(self, view, selection, context):
        try:
            view.read_image(time=1, position="p0")
        except PermissionError:
            escaped = False
        else:
            escaped = True
        return [{"result": {"escaped": escaped, "shape": list(view.as_array().shape)},
                 "status": "provisional"}]
''')
    result = run(offline_home, "batch")
    assert result["observations"][0]["result"] == {"escaped": False, "shape": [2, 2, 2]}


@pytest.mark.parametrize("capability", ["ctrl", "guard", "credentials", "event_queue", "acquisition"])
def test_manifest_capability_requests_refused(offline_home, capability):
    save, *_ = offline_home
    save("evil", "class Evil:\n def analyze_saved_frame(self, image, metadata, context): pass\n",
         offline_capabilities=[capability])
    with pytest.raises(ValueError, match="forbidden capabilities"):
        run(offline_home, "evil")


def test_live_only_refused_at_resolve_time_and_dual_use_calls_offline(offline_home):
    save, *_ = offline_home
    save("live", "class Live:\n def analyze_frame(self, image, metadata): raise AssertionError\n")
    with pytest.raises(ValueError, match="analyze_saved_frame.*analyze_completed_dataset"):
        run(offline_home, "live")
    save("dual", '''
class Dual:
 def analyze_frame(self, image, metadata): raise AssertionError("live called")
 def analyze_saved_frame(self, image, metadata, context): return {"ok": True}
''')
    assert run(offline_home, "dual")["status"] == "completed"


def test_live_and_offline_loaders_share_alphabetical_module_defined_selection(
    offline_home, monkeypatch,
):
    from microclaw import hook_manager
    save, *_ = offline_home
    code = '''
from collections import Counter
class Zeta:
 def analyze_frame(self, image, metadata): return None
 def analyze_saved_frame(self, image, metadata, context): return {"chosen": "zeta"}
class Alpha:
 def analyze_frame(self, image, metadata): return None
 def analyze_saved_frame(self, image, metadata, context): return {"chosen": "alpha"}
'''
    save("choice", code)
    monkeypatch.setattr(hook_manager, "MANIFEST", completed_dataset.MANIFEST)
    assert hook_manager.load_hook_class("choice").__name__ == "Alpha"
    assert completed_dataset._load_saved_adapter("choice")[0].__name__ == "Alpha"


def test_plain_measurements_may_use_status_key(offline_home):
    save, *_ = offline_home
    save("status_measurement", '''
class Measurement:
 def analyze_saved_frame(self, image, metadata, context):
  return {"status": "ok", "cells": 3}
''')
    result = run(offline_home, "status_measurement")
    assert result["status"] == "completed"
    assert result["observations"][0]["result"] == {"status": "ok", "cells": 3}


def test_untrusted_cannot_self_assert_observed(offline_home):
    save, *_ = offline_home
    save("claim", '''
class Claim:
 def analyze_saved_frame(self, image, metadata, context):
  return {"result": 1, "status": "observed"}
''')
    result = run(offline_home, "claim")
    assert result["status"] == "failed"
    assert "not self-assertable" in result["failure"]["message"]


@pytest.mark.parametrize("filename", ["../escape.bin", "/tmp/escape.bin", "a/b.bin"])
def test_artifact_escape_is_recorded_as_failure(offline_home, filename):
    save, *_ = offline_home
    save("artifact", f'''
class Artifact:
 def analyze_saved_frame(self, image, metadata, context):
  context.artifacts.emit({filename!r}, b"abc")
''')
    assert run(offline_home, "artifact")["status"] == "failed"


def test_artifact_limits_and_hashes(offline_home):
    save, *_ = offline_home
    save("artifact", '''
class Artifact:
 def analyze_saved_frame(self, image, metadata, context):
  context.artifacts.emit("x.bin", b"abc")
''')
    result = run(offline_home, "artifact",
                 artifact_limits={"max_artifact_bytes": 2, "max_count": 1, "max_total_bytes": 2})
    assert result["status"] == "failed"
    assert "exceeds" in result["failure"]["message"]


def test_manifest_survives_artifact_record_assembly_failure(offline_home, monkeypatch):
    save, *_ = offline_home
    save("bad_record", '''
class Artifact:
 def analyze_saved_frame(self, image, metadata, context):
  context.artifacts.emit("x.bin", b"abc")
''')
    original = completed_dataset.write_hook_artifact
    def outside_path(*args, **kwargs):
        info = original(*args, **kwargs)
        return {**info, "path": "/outside/artifact.bin"}
    monkeypatch.setattr(completed_dataset, "write_hook_artifact", outside_path)
    result = run(offline_home, "bad_record")
    assert result["status"] == "failed"
    assert result["failure"]["type"] == "ValueError"
    assert Path(result["manifest_path"]).exists()


def test_dataset_and_content_hashes_are_distinct(offline_home):
    _, dataset, *_ = offline_home
    dataset_hash, content_hash, _ = completed_dataset._dataset_content(str(dataset))
    assert dataset_hash != content_hash


def test_model_project_config_values_that_are_not_paths_stay_values(
        offline_home, tmp_path, monkeypatch):
    """A tilde in free text must not be mistaken for a path (block 41d).

    `_optional_input_hashes` uses the read resolver as a *type probe*: "is this
    string a file?". Once a leading `~` started being expanded, a value like
    `"~500 cells"` — which expanduser leaves alone, so the resolver refuses it —
    raised through the manifest assembly and failed the whole record. The
    answer to "is this a file?" for an unresolvable string is no.
    """
    _, _, guard, root = offline_home
    home = tmp_path / "home"
    home.mkdir()
    (home / "model.cfg").write_bytes(b"weights")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    records = completed_dataset._optional_input_hashes(
        {"approx_cells": "~500 cells", "note": "~", "config": "~/model.cfg",
         "threshold": 0.5},
        guard,
    )
    # Free text and a bare tilde are values, not references.
    assert records["approx_cells"] == {
        "value": "~500 cells",
        "sha256": hashlib.sha256(b'"~500 cells"').hexdigest(),
    }
    assert "reference" not in records["note"] and records["note"]["value"] == "~"
    assert records["threshold"]["value"] == 0.5
    # A `~` path that really names a file is still expanded and hashed.
    assert records["config"]["reference"] == "~/model.cfg"
    assert records["config"]["sha256"] == hashlib.sha256(b"weights").hexdigest()


def test_a_tilde_in_model_project_config_does_not_fail_the_record(offline_home):
    """End to end: the manifest assembly is wrapped in `except Exception`, so
    the regression showed up as a whole run marked failed, not as a traceback."""
    save, *_ = offline_home
    save("noop", '''
class Noop:
 def analyze_saved_frame(self, image, metadata, context): return {"n": 1}
''')
    result = run(offline_home, "noop",
                 model_project_config={"approx_cells": "~500 cells"})
    assert result["status"] == "completed", result.get("failure")
    assert "manifest_assembly_failure" not in result
    assert result["model_project_config"]["approx_cells"]["value"] == "~500 cells"


def test_cancellation_before_adapter_invocation(offline_home):
    save, *_ = offline_home
    save("cancel", '''
class Cancel:
 def analyze_saved_frame(self, image, metadata, context): raise AssertionError("called")
''')
    event = threading.Event(); event.set()
    result = run(offline_home, "cancel", cancellation_event=event)
    assert result["status"] == "cancelled" and result["cancelled"]


def test_mosaic_uses_dataset_recorded_calibration_when_reference_is_omitted(
        offline_home, monkeypatch):
    import tifffile
    from microclaw import tools
    save, dataset, guard, root = offline_home
    save("x", '''
class X:
 def analyze_saved_frame(self, image, metadata, context):
  return {"shape": list(image.shape)}
''')
    def build(_ctrl, _guard, _dataset_path, output_path, _selection,
              calibration_ref, _output_pixel_size_um):
        assert calibration_ref is None
        tifffile.imwrite(output_path, np.ones((3, 4), np.uint16))
        manifest = output_path + ".json"
        Path(manifest).write_text("{}", encoding="utf-8")
        return {"calibration_identity": {"source_kind": "acquisition_recorded"},
                "shape": [3, 4], "manifest_path": manifest}
    monkeypatch.setattr(tools, "build_stage_coordinate_mosaic", build)
    base = (guard, str(dataset), "x", {"time": 0}, "stage_coordinate_mosaic", {}, str(root / "m"))
    result = completed_dataset.run_analysis_on_saved_dataset(*base)
    assert result["status"] == "completed"
    assert result["observations"][0]["result"] == {"shape": [3, 4]}
    assert result["calibration_identity"]["source_kind"] == "acquisition_recorded"
    with pytest.raises(ValueError, match="live microscope core"):
        completed_dataset.run_analysis_on_saved_dataset(
            *base[:-1], str(root / "m2"), calibration_ref={"kind": "confirmed_current"}
        )


def test_mosaic_invokes_geometry_primitive_and_records_calibration(offline_home, monkeypatch):
    import tifffile
    from microclaw import tools
    save, dataset, guard, root = offline_home
    save("mosaic", '''
class Mosaic:
 def analyze_saved_frame(self, image, metadata, context):
  return {"result": {"shape": list(image.shape), "kind": metadata["input_kind"]},
          "status": "provisional"}
''')
    calls = []
    def build(ctrl, passed_guard, dataset_path, output_path, selection,
              calibration_ref, output_pixel_size_um):
        calls.append((ctrl, passed_guard, calibration_ref))
        tifffile.imwrite(output_path, np.ones((3, 4), np.uint16))
        manifest = output_path + ".json"
        Path(manifest).write_text("{}", encoding="utf-8")
        return {"calibration_identity": {"source_kind": "artifact", "payload_sha256": "a" * 64},
                "manifest_path": manifest, "artifact": {"kind": "tiff", "path": output_path}}
    monkeypatch.setattr(tools, "build_stage_coordinate_mosaic", build)
    result = completed_dataset.run_analysis_on_saved_dataset(
        guard, str(dataset), "mosaic", {"time": 0}, "stage_coordinate_mosaic", {},
        str(root / "mosaic-output"), calibration_ref={"kind": "artifact", "path": "cal.json"},
    )
    assert calls == [(None, guard, {"kind": "artifact", "path": "cal.json"})]
    assert result["calibration_used"] is True
    assert result["observations"][0]["result"] == {"shape": [3, 4], "kind": "stage_coordinate_mosaic"}
    assert len(result["artifacts"]) == 2


def test_provisional_counting_fixture_runs_over_saved_mosaic(offline_home, monkeypatch):
    import tifffile
    from microclaw import tools
    save, dataset, guard, root = offline_home
    source = (Path(__file__).parent / "fixtures/hooks/m5_migrated/mosaic_cell_counter.py").read_text(encoding="utf-8")
    save("counter", source, version="m5-migrated-fixture")
    def build(ctrl, passed_guard, dataset_path, output_path, selection,
              calibration_ref, output_pixel_size_um):
        tifffile.imwrite(output_path, np.zeros((8, 8), np.uint16))
        manifest = output_path + ".json"
        Path(manifest).write_text("{}", encoding="utf-8")
        return {"calibration_identity": {"source_kind": "artifact", "payload_sha256": "a" * 64},
                "manifest_path": manifest, "artifact": {"kind": "tiff", "path": output_path}}
    monkeypatch.setattr(tools, "build_stage_coordinate_mosaic", build)
    result = completed_dataset.run_analysis_on_saved_dataset(
        guard, str(dataset), "counter", {"time": 0}, "stage_coordinate_mosaic", {},
        str(root / "counter-mosaic"), calibration_ref={"kind": "artifact", "path": "cal.json"},
    )
    assert result["status"] == "completed"
    assert [item["status"] for item in result["observations"]] == ["provisional"]
    assert result["observations"][0]["result"]["running_cell_count"] == 0


def test_builtin_connected_components_runs_real_mosaic_path_in_stage_coordinates(
        tmp_path, monkeypatch):
    from microclaw import tools
    from microclaw.calibration import (
        StageCameraAffine, affine_payload_hash, canonical_affine_payload,
    )

    image = np.fromfunction(lambda row, col: 10 + ((row + col) % 2), (12, 12)).astype(np.uint16)
    image[1:3, 1:3] = 100
    image[7:9, 8:11] = 120

    class MosaicDataset:
        axes = {"position": ["p0"], "time": [0]}

        def __init__(self, path):
            pass

        def has_image(self, **coords):
            return coords == {"time": 0, "position": "p0"}

        def read_image(self, **coords):
            return image

        def read_metadata(self, **coords):
            return {
                "Axes": coords, "XPosition_um_Intended": 10.0,
                "YPosition_um_Intended": 20.0, "Core-Camera": "Camera",
                "Camera-Camera": "model", "ROI": "0-0-12-12", "Binning": "1x1",
                "Height": 12, "Width": 12, "PixelType": "GRAY16",
            }

    monkeypatch.setattr(completed_dataset, "Dataset", MosaicDataset)
    monkeypatch.setattr(tools, "Dataset", MosaicDataset)
    monkeypatch.setattr(completed_dataset, "MANIFEST", tmp_path / "absent-manifest.json")
    dataset_path = tmp_path / "dataset"
    dataset_path.mkdir()
    (dataset_path / "NDTiff.index").write_bytes(b"real mosaic path fixture")
    transform = StageCameraAffine(1, 0, 0, 1, "obj", 1, 1)
    calibration_path = tmp_path / "calibration.json"
    calibration_path.write_text(json.dumps({
        "payload": canonical_affine_payload(transform),
        "payload_sha256": affine_payload_hash(transform),
        "camera_device": "Camera", "camera_model": "model", "roi": [0, 0, 12, 12],
    }), encoding="utf-8")
    guard = SafetyGuard(SafetyConstraints(workspace_dir=str(tmp_path)))
    result = completed_dataset.run_analysis_on_saved_dataset(
        guard, str(dataset_path), "connected_components", {"time": 0},
        "stage_coordinate_mosaic", {"min_area_um2": 3, "max_area_um2": 10},
        str(tmp_path / "analysis"),
        calibration_ref={"kind": "artifact", "path": str(calibration_path)},
    )

    assert result["status"] == "completed", result.get("failure")
    measured = result["observations"][0]["result"]
    assert measured["n_components"] == 2
    np.testing.assert_allclose([item["area_um2"] for item in measured["objects"]], [4, 6])
    np.testing.assert_allclose(
        [item["centroid_stage_um"] for item in measured["objects"]],
        [[6.0, 16.0], [13.5, 22.0]],
    )
    assert measured["objects"][0]["bounding_box_stage_um"] == {
        "x_min": 5.0, "y_min": 15.0, "x_max": 7.0, "y_max": 17.0,
    }
    assert result["observations"][0]["status"] == "observed"
    assert result["parameters"]["min_snr_source"] == "package_default_uncalibrated"
    assert len(result["analyzer"]["source_sha256"]) == 64
    json.dumps(result, allow_nan=False)


def test_public_wrapper_never_accesses_controller(monkeypatch):
    from microclaw import tools
    seen = {}
    monkeypatch.setattr(completed_dataset, "run_analysis_on_saved_dataset",
                        lambda guard, *args, **kwargs: seen.setdefault("guard", guard) or {})
    class ExplodingController:
        def __getattribute__(self, name):
            raise AssertionError(f"hardware capability accessed: {name}")
    guard = object()
    tools.run_analysis_on_saved_dataset(
        ExplodingController(), guard, "d", "a", {}, "frames", {}, "o"
    )
    assert seen["guard"] is guard


def test_shared_writer_closed_vocabulary():
    from microclaw.hooks import write_analysis_observation
    with pytest.raises(ValueError, match="Unknown analysis observation status"):
        write_analysis_observation([], analyzer="a", analyzer_version="1", result={}, status="final")


def test_hook_skill_offline_contract_matches_runner(offline_home, monkeypatch):
    from microclaw.skills import load_skill_text

    # Observe the defaults the imported runner actually passes to its writer;
    # they are local to that function, not exported constants.
    limits_seen = []
    writer = completed_dataset.write_hook_artifact

    def record_limits(target, filename, payload, *, state, **limits):
        limits_seen.append(limits)
        return writer(target, filename, payload, state=state, **limits)

    monkeypatch.setattr(completed_dataset, "write_hook_artifact", record_limits)
    save, *_ = offline_home
    save("contract_probe", '''
class ContractProbe:
 def analyze_completed_dataset(self, dataset_view, selection, context):
  context.artifacts.emit("probe.bin", b"probe")
''')
    assert run(offline_home, "contract_probe")["status"] == "completed"
    assert len(limits_seen) == 1
    text = load_skill_text("hook-authoring")
    missing = [verb for verb in completed_dataset.OFFLINE_VERBS if verb not in text]
    missing.extend(f'"{key}": {value}' for key, value in limits_seen[0].items()
                   if f'"{key}": {value}' not in text)
    assert not missing, f"Skill omits offline runner contract: {missing}"


@pytest.mark.parametrize("verb", completed_dataset.OFFLINE_VERBS)
def test_offline_adapter_saved_and_run_through_tools(tmp_path, monkeypatch, verb):
    from microclaw import hook_manager, tools

    hooks = tmp_path / "hooks"
    manifest = hooks / "manifest.json"
    monkeypatch.setattr(hook_manager, "HOOKS_DIR", hooks)
    monkeypatch.setattr(hook_manager, "MANIFEST", manifest)
    monkeypatch.setattr(completed_dataset, "MANIFEST", manifest)
    monkeypatch.setattr(completed_dataset, "Dataset", lambda path: FakeDataset())
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "NDTiff.index").write_bytes(b"saved pixels")
    guard = SafetyGuard(SafetyConstraints(workspace_dir=str(tmp_path)))
    whole = verb == completed_dataset.OFFLINE_VERBS[0]
    args = "dataset_view, selection" if whole else "image, metadata"
    result = "[{'count': 1}]" if whole else "{'count': 1}"
    code = f'''class OfflineAdapter:
 def {verb}(self, {args}, context):
  context.artifacts.emit("result.bin", b"verified")
  context.emit_observation({{"via_context": True}}, status="provisional")
  return {result}
'''
    assert not manifest.exists()
    saved = tools.generate_and_save_hook(
        None, guard, "offline", code, "Offline measurement", runner_contract="fixed",
    )
    assert "error" not in saved, saved
    assert manifest.exists()
    described = tools.describe_hook(None, guard, "offline")
    assert described["callback"] == verb
    assert described["provenance"]["matches_manifest"] is True
    assert not described["resolve_refusal"]["would_refuse"]
    # `resolvable` is true and the hook is still not attachable, so the route has
    # to say which runner takes it -- and the adaptive-survey note, which is
    # about hardware actions during an acquisition, must not be offered at all.
    assert described["route"].startswith("run_analysis_on_saved_dataset")
    assert "cannot be passed as hook_strategy" in described["route"]
    assert "adaptive_hardware_actions" not in described
    listed = tools.list_hooks(None, guard)["saved"]["offline"]
    assert listed["resolvable"] is True
    assert listed["route"] == described["route"]
    analyzed = tools.run_analysis_on_saved_dataset(
        None, guard, str(dataset), "offline", {"time": 0, "position": "p0"},
        "frames", {}, str(tmp_path / "analysis"),
    )
    assert analyzed["status"] == "completed", analyzed["failure"]
    assert [item["result"] for item in analyzed["observations"]] == [
        {"via_context": True}, {"count": 1},
    ]
    assert Path(analyzed["artifacts"][0]["path"]).read_bytes() == b"verified"
    adaptive = tools.generate_and_save_hook(
        None, guard, "adaptive", code, "Wrong route", runner_contract="adaptive",
    )
    assert adaptive["contract_errors"] == [
        "This runner requires analyze_frame; the hook does not define it."
    ]
    assert "adaptive" not in json.loads(manifest.read_text(encoding="utf-8"))
    # The acquisition route refuses it by name, as a ValueError -- which is what
    # every run_* tool converts into a returned error. A bare AttributeError here
    # would reach the operator as an unexplained traceback.
    with pytest.raises(ValueError, match="run_analysis_on_saved_dataset"):
        hook_manager.load_hook_class("offline")


@pytest.mark.parametrize("verb", completed_dataset.OFFLINE_VERBS)
def test_offline_save_rejects_missing_context(tmp_path, monkeypatch, verb):
    from microclaw import hook_manager, tools

    monkeypatch.setattr(hook_manager, "HOOKS_DIR", tmp_path / "hooks")
    monkeypatch.setattr(hook_manager, "MANIFEST", tmp_path / "manifest.json")
    result = tools.generate_and_save_hook(
        None, None, "short", f"class Short:\n def {verb}(self, first, second): pass\n",
        "Missing offline context",
    )
    assert any(f"Short.{verb} must accept" in error
               for error in result["contract_errors"]), result
    assert not hook_manager.MANIFEST.exists()


@pytest.fixture
def component_frames(tmp_path, monkeypatch):
    from microclaw import tools
    from microclaw.calibration import StageCameraAffine, canonical_affine_payload, affine_payload_hash
    images = [np.zeros((32, 32), np.uint16) for _ in range(3)]
    # One shared signal: frame 1 is displaced by four columns under the
    # incident's rotated affine. Both images must retain this component.
    images[0][16, 20] = images[1][16, 16] = 100
    # Two touching objects form one connected region, not two identifications.
    images[0][24:26, 24:26] = 80
    images[0][24:26, 26:28] = 80
    metadata = [{
        'XPosition_um_Intended': 10., 'YPosition_um_Intended': 20. - index * .508,
        'Core-Camera': 'Camera', 'Camera-Camera': 'model',
        'ROI': '0-0-32-32', 'Binning': '1x1', 'Height': 32, 'Width': 32,
        'PixelType': 'GRAY16', 'PositionName': f'field-{index}',
    } for index in range(3)]

    class SourceDataset:
        axes = {'position': [0, 1, 2], 'time': [0, 1], 'channel': ['A', 'B'], 'z': [0, 1]}

        def has_image(self, **coords):
            return all(coords.get(k) in values for k, values in self.axes.items())

        read_coordinates = []

        def read_image(self, **coords):
            self.read_coordinates.append(dict(coords))
            return images[self.axes['position'].index(coords['position']) if 'position' in coords else 0]

        def read_metadata(self, **coords):
            return metadata[self.axes['position'].index(coords['position']) if 'position' in coords else 0]

    dataset = SourceDataset()
    monkeypatch.setattr(completed_dataset, 'Dataset', lambda path: dataset)
    monkeypatch.setattr(tools, 'Dataset', lambda path: dataset)
    path = tmp_path / 'dataset'
    path.mkdir()
    (path / 'NDTiff.index').write_bytes(b'component frames')
    transform = StageCameraAffine(-0., .127, -.127, 0., 'obj', 1, .127)
    calibration = tmp_path / 'calibration.json'
    calibration.write_text(json.dumps({
        'payload': canonical_affine_payload(transform),
        'payload_sha256': affine_payload_hash(transform),
        'camera_device': 'Camera', 'camera_model': 'model', 'roi': [0, 0, 32, 32],
    }), encoding='utf-8')
    guard = SafetyGuard(SafetyConstraints(workspace_dir=str(tmp_path)))
    def analyze(name='out', parameters=None, **kwargs):
        options = {'calibration_ref': {'kind': 'artifact', 'path': str(calibration)}}
        options.update(kwargs)
        return completed_dataset.run_analysis_on_saved_dataset(
            guard, str(path), 'connected_components',
            options.pop('axis_selection', {'time': 1, 'channel': 'B', 'z': 1}),
            options.pop('input_kind', 'frames'), parameters or {}, str(tmp_path / name), **options,
        )
    return analyze, images, metadata, dataset, calibration


def test_original_frames_count_geometry_and_where(component_frames, monkeypatch):
    analyze, images, metadata, dataset, _ = component_frames
    originals = [image.copy() for image in images]
    measured_pixels = []
    original = completed_dataset.connected_components
    def measure(image, **kwargs):
        assert not image.flags.writeable
        measured_pixels.append((image, image.copy()))
        return original(image, **kwargs)
    monkeypatch.setattr(completed_dataset, 'connected_components', measure)
    result = analyze()
    assert result['status'] == 'completed', result['failure']
    assert result['calibration_used'] is True
    assert result['calibration_identity']['camera_model'] == 'model'
    observations = result['observations']
    assert dataset.read_coordinates == [
        {'position': p, 'time': 1, 'channel': 'B', 'z': 1} for p in range(3)
    ]
    assert [o['result']['n_components'] for o in observations] == [2, 1, 0]
    for index, observation in enumerate(observations):
        assert {k: observation[k] for k in ('position', 'time', 'channel', 'z', 'PositionName')} == {
            'position': index, 'time': 1, 'channel': 'B', 'z': 1, 'PositionName': f'field-{index}',
        }
        measured = observation['result']
        assert measured['threshold'] == measured['background_level'] == measured['noise_mad_sigma'] == 0
        assert measured['min_area_um2'] == 0 and measured['max_area_um2'] is None
        assert 'not a unique object total' in result['count_semantics']
        assert measured['count_semantics_ref'] == 'count_semantics'
        assert 'count_semantics' not in measured
        assert 'not object identification' in result['count_semantics']
        assert 'frame_statistics' in measured
        # Measurement is read-only: the saved pixels are handed back untouched.
        np.testing.assert_array_equal(images[index], originals[index])
    for pixels, before in measured_pixels:
        assert not pixels.flags.writeable
        np.testing.assert_array_equal(pixels, before)
    shared = [o['result']['objects'][0] for o in observations[:2]]
    for component in shared:
        np.testing.assert_allclose(component['centroid_stage_um'], [10.0635, 19.4285])
        assert component['area_um2'] == .016129
        assert 'bounding_box_stage_um' not in component
    assert observations[0]['result']['objects'][1]['n_pixels'] == 8
    assert observations[0]['result']['objects'][1]['area_um2'] == .129032
    # A frames run writes no artifacts at all, and nothing claims one.
    assert result['artifacts'] == []
    assert all('artifact_sha256' not in o or o['artifact_sha256'] is None
               for o in observations)


def test_source_missing_calibration_uses_resolver_refusal(component_frames):
    analyze, *_ = component_frames
    result = analyze(calibration_ref=None)
    assert result['status'] == 'failed'
    assert result['failure']['type'] == 'CalibrationResolutionError'
    assert result['failure']['message'].startswith('Dataset does not record a usable calibration (')
    assert result['observations'] == []


def test_source_confirmed_current_refused_offline(component_frames):
    analyze, *_ = component_frames
    with pytest.raises(ValueError, match='confirmed_current calibration requires a live microscope core'):
        analyze(calibration_ref={'kind': 'confirmed_current'})


def test_source_missing_intended_xy_refuses_stage_geometry(component_frames):
    analyze, _, metadata, *_ = component_frames
    del metadata[0]['XPosition_um_Intended']
    result = analyze()
    assert result['status'] == 'completed', result['failure']
    measured = result['observations'][0]['result']
    assert measured['stage_geometry_refusal'] == 'Stage coordinates refused: missing XPosition_um_Intended'
    assert measured['n_components'] == 2
    assert measured['objects'][0]['centroid_px'] == [20., 16.]
    assert 'centroid_stage_um' not in measured['objects'][0]
    assert 'bounding_box_stage_hull_um' not in measured['objects'][0]


def test_named_position_axis_counts_per_field_and_omits_absent_names(component_frames):
    analyze, images, metadata, dataset, _ = component_frames
    for i in range(3):
        images[i] = images[i].astype(np.uint8)  # GRAY8 frames count the same way
        metadata[i]['PixelType'] = 'GRAY8'
    dataset.axes['position'] = ['left', 'right', 'empty']
    del metadata[0]['PositionName']
    result = analyze()
    assert result['status'] == 'completed', result['failure']
    by_position = {o['position']: o for o in result['observations']}
    # `where` carries the saved name when there is one, and invents none when
    # there is not: a position with no PositionName simply has no key.
    assert 'PositionName' not in by_position['left']
    assert by_position['right']['PositionName'] == 'field-1'
    assert {p: o['result']['n_components'] for p, o in by_position.items()} == {
        'left': 2, 'right': 1, 'empty': 0,
    }
    assert not images[2].any()  # the empty field stayed empty, and its zero is a result


@pytest.mark.parametrize('key,value', [('Core-Camera', 'other'), ('Camera-Camera', 'other'), ('Binning', '2x2')])
def test_source_calibration_identity_refuses_mismatch(component_frames, key, value):
    analyze, _, metadata, *_ = component_frames
    for item in metadata:
        item[key] = value
        if key == 'Core-Camera':
            item['other-Camera'] = 'model'
    result = analyze()
    assert result['status'] == 'failed'
    assert 'Calibration identity contradicts dataset metadata' in result['failure']['message']


def test_source_calibration_records_roi_difference_and_refuses_inconsistency(component_frames):
    analyze, _, metadata, *_ = component_frames
    for item in metadata:
        item['ROI'] = '1-2-32-32'
    result = analyze()
    assert result['status'] == 'completed'
    assert result['calibration_roi_difference']['dataset'] == [1, 2, 32, 32]
    metadata[0]['Binning'] = '2x2'
    refused = analyze('inconsistent')
    assert refused['status'] == 'failed'
    assert 'changes within the selected plane' in refused['failure']['message']


def test_old_mosaic_manifest_still_drives_original_adapter(tmp_path):
    import tifffile
    from microclaw.tools import _verify_against_manifest
    # Generated by build_stage_coordinate_mosaic with microclaw/ checked out at
    # fb0a3ec. Keep its original payload and hash, including its historical paths.
    manifest = json.loads((Path(__file__).parent / 'fixtures/pre81b-mosaic.json').read_text(encoding='utf-8'))
    old = manifest['manifest_payload']
    image = np.ones((8, 8), np.uint16)
    image[2:4, 2:4] = 100
    path = tmp_path / 'old.tiff'
    tifffile.imwrite(path, image)
    verification = _verify_against_manifest(manifest, path)
    assert verification['manifest_payload_sha256_matches'] is True
    assert verification['pixel_sha256_matches'] is True
    adapter = completed_dataset.ConnectedComponents(min_snr=3, min_snr_source='explicit')
    result = adapter.analyze_saved_frame(image, {'input_kind': 'stage_coordinate_mosaic', 'mosaic_manifest': old}, None)
    assert result['result'] == {
        'threshold': 1., 'background_level': 1., 'noise_mad_sigma': 0., 'n_components': 1,
        'objects': [{'label': 1, 'area_um2': 4., 'centroid_stage_um': [12.5, 22.5],
                     'bounding_box_stage_um': {'x_min': 11.5, 'y_min': 21.5, 'x_max': 13.5, 'y_max': 23.5},
                     'bounding_box_px': [2, 2, 4, 4]}],
    }
    assert 'tile_placements' not in old


def test_real_sheared_calibration_counts_both_overlapping_fields(component_frames):
    from microclaw.calibration import StageCameraAffine, canonical_affine_payload, affine_payload_hash
    analyze, _, metadata, _, calibration = component_frames
    a, b, c, d = (.004927971153294251, -.10452770834076626,
                  -.10638852331845677, -.00512650316309355)
    transform = StageCameraAffine(a, b, c, d, 'obj', 1, np.sqrt(abs(a*d-b*c)))
    record = json.loads(calibration.read_text(encoding='utf-8'))
    record.update(payload=canonical_affine_payload(transform), payload_sha256=affine_payload_hash(transform))
    calibration.write_text(json.dumps(record), encoding='utf-8')
    # The shared signal moves four source columns between fields; move the
    # intended centre by those same four columns through the recorded affine.
    metadata[1]['XPosition_um_Intended'] = 10 + 4*a
    metadata[1]['YPosition_um_Intended'] = 20 + 4*c
    result = analyze()
    assert result['status'] == 'completed', result['failure']
    observations = result['observations']
    assert [o['result']['n_components'] for o in observations] == [2, 1, 0]
    assert sum(o['result']['n_components'] for o in observations) == 3  # no deduplication
    assert 'not a unique object total' in result['count_semantics']
    assert 'not object identification' in result['count_semantics']
    for observation in observations[:2]:
        shared = observation['result']['objects'][0]
        np.testing.assert_allclose(shared['centroid_stage_um'],
                                   [10 + 4.5*a + .5*b, 20 + 4.5*c + .5*d], rtol=0, atol=1e-12)
        assert shared['area_um2'] == abs(a*d-b*c)
    touching = observations[0]['result']['objects'][1]
    assert touching['n_pixels'] == 8  # two adjacent 2x2 supports, one component
    assert touching['area_um2'] == 8 * abs(a*d-b*c)


def test_omitted_axes_enumerate_frames_without_pooling(component_frames):
    analyze, _, _, dataset, _ = component_frames
    result = analyze(axis_selection={})
    assert result['status'] == 'completed', result['failure']
    observations = result['observations']
    assert len(observations) == 3 * 2 * 2 * 2
    assert len(dataset.read_coordinates) == len(observations)
    assert {(o['time'], o['channel'], o['z']) for o in observations} == {
        (t, c, z) for t in (0, 1) for c in ('A', 'B') for z in (0, 1)
    }
    assert all(o['result']['n_components'] == {0: 2, 1: 1, 2: 0}[o['position']] for o in observations)


def test_no_position_axis_still_counts_every_selected_frame(component_frames):
    analyze, _, _, dataset, _ = component_frames
    del dataset.axes['position']
    result = analyze()
    assert result['status'] == 'completed', result['failure']
    assert [o['result']['n_components'] for o in result['observations']] == [2]
    assert 'position' not in result['observations'][0]
    assert result['artifacts'] == []


def test_mosaic_manifest_records_a_placement_per_saved_tile(component_frames):
    import tifffile
    analyze, _, metadata, _, _ = component_frames
    del metadata[2]['PositionName']
    result = analyze(input_kind='stage_coordinate_mosaic')
    assert result['status'] == 'completed', result['failure']
    # The mosaic and its manifest are the only artifacts a mosaic run writes.
    assert [Path(a['path']).name for a in result['artifacts']] == [
        'stage_coordinate_mosaic.tiff', 'stage_coordinate_mosaic.tiff.json']
    source = tifffile.imread(result['mosaic']['artifact']['path'])
    assert hashlib.sha256(source.tobytes()).hexdigest() == result['mosaic']['pixel_sha256']
    placements = result['mosaic']['tile_placements']
    assert len(placements) == 3
    for index, placement in enumerate(placements):
        assert placement['coordinate'] == {'position': index, 'time': 1, 'channel': 'B', 'z': 1}
        assert placement['position_name'] == metadata[index].get('PositionName')
        assert placement['intended_xy_um'] == [metadata[index]['XPosition_um_Intended'], metadata[index]['YPosition_um_Intended']]
        assert 'source_basis_um' not in placement and 'bounds_convention' not in placement


def test_saved_adapter_keeps_one_context_and_only_paired_results_get_where(offline_home):
    save, *_ = offline_home
    save('context_identity', '''
class ContextIdentity:
 def analyze_saved_frame(self, image, metadata, context):
  assert "input_kind" not in metadata
  if hasattr(self, "context"):
   assert context is self.context
  self.context = context
  context.emit_observation({"own": True})
  if metadata["Axes"]["position"] == "p0": return None
  return {"paired": True}
''')
    result = run(offline_home, 'context_identity')
    assert result['status'] == 'completed', result['failure']
    own = [o for o in result['observations'] if o['result'].get('own')]
    assert len(own) == 2 and all('position' not in o and 'time' not in o for o in own)
    paired = [o for o in result['observations'] if o['result'].get('paired')]
    assert len(paired) == 1
    assert paired[0]['position'] == 'p1' and paired[0]['time'] == 0


def test_confirmed_current_refused_for_statistics_too(offline_home):
    with pytest.raises(ValueError, match='confirmed_current calibration requires a live microscope core'):
        run(offline_home, 'frame_statistics', calibration_ref={'kind': 'confirmed_current'})


def test_mosaic_placement_identity_length_mismatch_refuses(component_frames, monkeypatch):
    from microclaw import tools
    analyze, *_ = component_frames
    assembler = tools.assemble_stage_coordinate_mosaic
    def missing_placement(*args, **kwargs):
        result = assembler(*args, **kwargs)
        result['tile_placements'].pop()
        return result
    monkeypatch.setattr(tools, 'assemble_stage_coordinate_mosaic', missing_placement)
    result = analyze(input_kind='stage_coordinate_mosaic')
    assert result['status'] == 'failed'
    assert result['failure']['message'] == 'Mosaic tile placements and saved metadata must have equal lengths'


def test_singleton_dominance_is_disclosed_per_field_and_stays_observed(component_frames):
    analyze, images, *_ = component_frames
    # field 0: three real 2x2 objects, no minimum-size detections.
    images[0][:] = 0
    for row, col in ((4, 4), (4, 20), (20, 4)):
        images[0][row:row + 2, col:col + 2] = 500
    # field 1: five single-pixel excursions around one real object — the
    # incident's shape, where the count answers the noise floor.
    images[1][:] = 0
    images[1][10:12, 10:12] = 500
    for row, col in ((2, 2), (2, 28), (28, 2), (28, 28), (16, 25)):
        images[1][row, col] = 400
    images[2][:] = 0  # field 2: empty. A valid zero is a result.

    result = analyze()
    assert result['status'] == 'completed', result['failure']
    measured = [o['result'] for o in result['observations']]
    assert [o['status'] for o in result['observations']] == ['observed'] * 3
    assert [m['n_components'] for m in measured] == [3, 6, 0]

    assert measured[0]['component_size_distribution'] == {
        'n_components': 3, 'single_pixel_components': 0, 'single_pixel_fraction': 0.0,
        'n_pixels': {'min': 4, 'median': 4.0, 'max': 4}, 'pixel_area_um2': .016129,
    }
    assert measured[0]['review_notes'] == []

    assert measured[1]['component_size_distribution'] == {
        'n_components': 6, 'single_pixel_components': 5, 'single_pixel_fraction': .8333,
        'n_pixels': {'min': 1, 'median': 1.0, 'max': 4}, 'pixel_area_um2': .016129,
    }
    note, = measured[1]['review_notes']
    assert note.startswith('5 of 6 counted components (83%) are one pixel')
    assert 'min_area_um2 is the smallest component area counted; it is 0 µm² here' in note

    # An empty field reports the zero and says nothing that reads as a failure.
    assert measured[2]['component_size_distribution'] == {
        'n_components': 0, 'single_pixel_components': 0, 'single_pixel_fraction': 0.0,
        'n_pixels': {'min': None, 'median': None, 'max': None}, 'pixel_area_um2': .016129,
    }
    assert measured[2]['review_notes'] == []

    # Above the single-pixel area the singletons and the note both go, and the
    # count changes to the objects that are left.
    filtered = analyze('filtered', parameters={'min_area_um2': .05})
    assert filtered['status'] == 'completed', filtered['failure']
    assert [o['status'] for o in filtered['observations']] == ['observed'] * 3
    assert [o['result']['n_components'] for o in filtered['observations']] == [3, 1, 0]
    for observation in filtered['observations']:
        assert observation['result']['review_notes'] == []
        assert observation['result']['component_size_distribution']['single_pixel_components'] == 0


def test_mosaic_path_gains_no_size_disclosure(component_frames):
    analyze, *_ = component_frames
    result = analyze(input_kind='stage_coordinate_mosaic')
    assert result['status'] == 'completed', result['failure']
    measured = result['observations'][0]['result']
    # A subset because a mosaic with no covered pixels reports the short form.
    assert set(measured) <= {'threshold', 'background_level', 'noise_mad_sigma',
                             'n_components', 'objects'}
    assert 'component_size_distribution' not in measured and 'review_notes' not in measured
