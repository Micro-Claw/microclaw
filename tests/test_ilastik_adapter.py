import inspect
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from microclaw.hook_decisions import HookResult
from microclaw.hook_manager import lint_hook_code, validate_hook_contract
from microclaw import completed_dataset
from microclaw.ilastik_adapter import (
    IlastikCompletedDatasetAdapter, IlastikPooledObservationAdapter,
    decimate_field, pool_probability_map,
)
from microclaw.safety import SafetyConstraints, SafetyGuard


LABELS = [b"BG", b"apo_mito", b"healthy_mito"]


def recorded_output():
    fixture = Path(__file__).parent / "fixtures" / "ilastik" / "recorded_probabilities.npz"
    data = np.load(fixture, allow_pickle=False)
    return data["probabilities"], str(data["axistags"]), list(data["label_names"])


def test_real_recorded_output_reads_axes_and_labels_in_their_recorded_order():
    probabilities, axistags, labels = recorded_output()
    expected = pool_probability_map(probabilities, axistags=axistags, label_names=labels)
    transposed = np.transpose(probabilities, (2, 0, 1))
    axes = json.loads(axistags)
    axes["axes"] = [axes["axes"][2], axes["axes"][0], axes["axes"][1]]
    reordered = transposed[[2, 0, 1], ...]
    actual = pool_probability_map(
        reordered, axistags=axes,
        label_names=[labels[2], labels[0], labels[1]],
    )
    assert actual["apo_fraction"] == pytest.approx(expected["apo_fraction"])
    assert actual["mito_coverage"] == pytest.approx(expected["mito_coverage"])


def test_coverage_gate_refuses_ratio_from_recorded_output():
    probabilities, axistags, labels = recorded_output()
    pooled = pool_probability_map(
        probabilities, axistags=axistags, label_names=labels, coverage_floor=1.0,
    )
    assert pooled["status"] == "unresolved"
    assert pooled["apo_fraction"] is None


def test_plain_adapter_contract_and_lint():
    source = inspect.getsource(IlastikPooledObservationAdapter)
    assert IlastikPooledObservationAdapter.__bases__ == (object,)
    assert "log_path" not in inspect.signature(IlastikPooledObservationAdapter).parameters
    assert lint_hook_code(source) == []
    module_source = "from microclaw.hook_decisions import HookResult\n" + source
    assert validate_hook_contract(module_source, required_callback="analyze_frame") == []
    probabilities, axistags, labels = recorded_output()
    result = IlastikPooledObservationAdapter().analyze_frame(
        probabilities, {"axistags": axistags, "LabelNames": labels}
    )
    assert isinstance(result, HookResult)


class FakeView:
    coordinates = ({"position": 0},)

    def read_image(self, **coordinates):
        return np.arange(2048 * 2048, dtype=np.uint16).reshape(2048, 2048)


class FakeContext:
    def raise_if_cancelled(self):
        pass


class FakeDataset:
    def __init__(self, values, attrs=None):
        self.values = values
        self.attrs = attrs or {}

    def __getitem__(self, key):
        if key == ():
            return self.values
        return self


class FakeFile:
    def __init__(self, path, mode, probability_path, probabilities, axistags):
        self.path = Path(path)
        self.probability_path = probability_path
        self.probabilities = probabilities
        self.axistags = axistags

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def __getitem__(self, key):
        if self.path.suffix == ".h5":
            return FakeDataset(self.probabilities, {"axistags": self.axistags})
        return FakeDataset(np.array(LABELS))


def adapter(tmp_path):
    executable = tmp_path / "python"
    executable.write_bytes(b"executable")
    project = tmp_path / "model.ilp"
    project.write_bytes(b"pinned project")
    import hashlib
    return IlastikCompletedDatasetAdapter(
        executable, project, hashlib.sha256(project.read_bytes()).hexdigest(), timeout_s=2,
    )


def test_batch_is_one_absolute_invocation_and_cleans_intermediates(tmp_path, monkeypatch):
    probabilities, axistags, _ = recorded_output()
    instance = adapter(tmp_path)
    work_seen = []

    def run(command, **kwargs):
        assert len(command) == 7
        assert "--readonly=true" in command
        assert "--output_format=hdf5" in command
        assert all(Path(item).is_absolute() for item in command[-1:])
        work = Path(kwargs["cwd"])
        work_seen.append(work)
        output = work / "field_000000_probabilities.h5"
        output.touch()
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setitem(sys.modules, "h5py", SimpleNamespace(
        File=lambda path, mode: FakeFile(path, mode, Path("never"), probabilities, axistags)
    ))
    result = instance.analyze_completed_dataset(FakeView(), {}, FakeContext())
    assert result[0]["result"]["coordinates"] == {"position": 0}
    assert not work_seen[0].exists()
    assert decimate_field(FakeView().read_image()).shape == (256, 256)


def test_completed_dataset_runner_executes_ilastik_path_end_to_end(tmp_path, monkeypatch):
    probabilities, axistags, _ = recorded_output()
    instance = adapter(tmp_path)
    dataset_path = tmp_path / "dataset"
    dataset_path.mkdir()
    (dataset_path / "NDTiff.index").write_bytes(b"recorded dataset fixture")

    class OneFieldDataset(FakeView):
        axes = {"position": [0]}

        def has_image(self, **coordinates):
            return coordinates == {"position": 0}

        def read_metadata(self, **coordinates):
            return {"Axes": coordinates}

    monkeypatch.setattr(completed_dataset, "Dataset", lambda path: OneFieldDataset())

    original_run = subprocess.run

    def run(command, **kwargs):
        if "cwd" not in kwargs:
            return original_run(command, **kwargs)
        output = Path(kwargs["cwd"]) / "field_000000_probabilities.h5"
        output.touch()
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setitem(sys.modules, "h5py", SimpleNamespace(
        File=lambda path, mode: FakeFile(path, mode, Path("never"), probabilities, axistags)
    ))
    guard = SafetyGuard(SafetyConstraints(workspace_dir=str(tmp_path)))
    result = completed_dataset.run_analysis_on_saved_dataset(
        guard, str(dataset_path), "ilastik_pixel_classification", {"position": 0},
        "frames", {
            "executable_path": str(instance.executable_path),
            "project_path": str(instance.project_path),
            "project_sha256": instance.project_sha256,
            "timeout_s": 2,
        }, str(tmp_path / "result"),
        model_project_config={"ilastik_project": str(instance.project_path)},
    )
    assert result["status"] == "completed"
    assert len(result["observations"]) == 1
    assert result["observations"][0]["result"]["ranking_unit"] == "whole_field"
    assert result["model_project_config"]["ilastik_project"]["sha256"] == instance.project_sha256


def test_timeout_is_killed_and_intermediates_are_cleaned(tmp_path, monkeypatch):
    probabilities, axistags, _ = recorded_output()
    instance = adapter(tmp_path)
    work_seen = []
    monkeypatch.setitem(sys.modules, "h5py", SimpleNamespace(
        File=lambda path, mode: FakeFile(path, mode, Path("never"), probabilities, axistags)
    ))

    def timeout(command, **kwargs):
        work_seen.append(Path(kwargs["cwd"]))
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(TimeoutError, match="was killed"):
        instance.analyze_completed_dataset(FakeView(), {}, FakeContext())
    assert not work_seen[0].exists()


@pytest.mark.parametrize("case", ["missing", "mismatch"])
def test_project_must_exist_and_match_hash(tmp_path, case):
    instance = adapter(tmp_path)
    if case == "missing":
        instance.project_path.unlink()
        expected = FileNotFoundError
    else:
        instance.project_path.write_bytes(b"changed")
        expected = ValueError
    with pytest.raises(expected):
        instance.analyze_completed_dataset(FakeView(), {}, FakeContext())
