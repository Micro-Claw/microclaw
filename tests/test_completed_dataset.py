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
        path.write_text(code, encoding="utf-8")
        entry = {"path": str(path), "source": "user_provided",
                 "sha256": hashlib.sha256(code.encode()).hexdigest(),
                 "accepted_warnings": completed_dataset.lint_hook_code(code), **extra}
        data = json.loads(manifest.read_text()) if manifest.exists() else {}
        data[name] = entry
        manifest.write_text(json.dumps(data))

    return save, dataset, guard, tmp_path


def run(saved, name, **kwargs):
    save, dataset, guard, root = saved
    return completed_dataset.run_analysis_on_saved_dataset(
        guard, str(dataset), name, {"time": 0}, "frames", {},
        str(root / kwargs.pop("output", f"out-{name}")), **kwargs,
    )


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


def test_cancellation_before_adapter_invocation(offline_home):
    save, *_ = offline_home
    save("cancel", '''
class Cancel:
 def analyze_saved_frame(self, image, metadata, context): raise AssertionError("called")
''')
    event = threading.Event(); event.set()
    result = run(offline_home, "cancel", cancellation_event=event)
    assert result["status"] == "cancelled" and result["cancelled"]


def test_mosaic_requires_explicit_reproducible_calibration(offline_home):
    save, dataset, guard, root = offline_home
    save("x", "class X:\n def analyze_saved_frame(self, image, metadata, context): pass\n")
    base = (guard, str(dataset), "x", {"time": 0}, "stage_coordinate_mosaic", {}, str(root / "m"))
    with pytest.raises(ValueError, match="explicit calibration_ref"):
        completed_dataset.run_analysis_on_saved_dataset(*base)
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
        Path(manifest).write_text("{}")
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
    source = (Path(__file__).parent / "fixtures/hooks/m5_migrated/mosaic_cell_counter.py").read_text()
    save("counter", source, version="m5-migrated-fixture")
    def build(ctrl, passed_guard, dataset_path, output_path, selection,
              calibration_ref, output_pixel_size_um):
        tifffile.imwrite(output_path, np.zeros((8, 8), np.uint16))
        manifest = output_path + ".json"
        Path(manifest).write_text("{}")
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
