import hashlib
import json
import threading
from pathlib import Path

import numpy as np
import pytest

from microclaw import completed_dataset
from microclaw.safety import SafetyConstraints, SafetyGuard


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
        completed_dataset._load_saved_adapter("connected_components")
    message = str(caught.value)
    assert "No adapter named 'connected_components'" in message
    assert "Available saved adapters: ['alpha', 'zebra']" in message


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
