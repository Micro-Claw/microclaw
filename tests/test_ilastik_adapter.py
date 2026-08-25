import builtins
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from microclaw import completed_dataset
from microclaw.ilastik_adapter import (
    IlastikCompletedDatasetAdapter, _validate_absolute_command_paths,
    decimate_field, pool_probability_map,
)
from microclaw.safety import SafetyConstraints, SafetyGuard


LABELS = [b"BG", b"apo_mito", b"healthy_mito"]


def recorded_output():
    fixture = Path(__file__).parent / "fixtures" / "ilastik" / "recorded_probabilities.npz"
    data = np.load(fixture, allow_pickle=False)
    return data["probabilities"], str(data["axistags"]), list(data["label_names"])


def pool(probabilities, axistags, labels, **kwargs):
    return pool_probability_map(
        probabilities, axistags=axistags, label_names=labels,
        background_label="BG", numerator_label="apo_mito",
        denominator_label="healthy_mito", coverage_key="mito_coverage",
        ratio_key="apo_fraction", **kwargs,
    )


def test_real_recorded_output_reads_axes_and_labels_in_their_recorded_order():
    probabilities, axistags, labels = recorded_output()
    expected = pool(probabilities, axistags, labels)
    transposed = np.transpose(probabilities, (2, 0, 1))
    axes = json.loads(axistags)
    axes["axes"] = [axes["axes"][2], axes["axes"][0], axes["axes"][1]]
    reordered = transposed[[2, 0, 1], ...]
    actual = pool(reordered, axes, [labels[2], labels[0], labels[1]])
    assert actual["apo_fraction"] == pytest.approx(expected["apo_fraction"])
    assert actual["mito_coverage"] == pytest.approx(expected["mito_coverage"])


def test_coverage_gate_refuses_ratio_from_recorded_output():
    probabilities, axistags, labels = recorded_output()
    pooled = pool(probabilities, axistags, labels, coverage_floor=1.0)
    assert pooled["status"] == "unresolved"
    assert pooled["apo_fraction"] is None


def test_pooling_is_generic_and_named_labels_are_validated():
    probabilities, axistags, _ = recorded_output()
    generic = pool_probability_map(
        probabilities, axistags=axistags, label_names=["empty", "one", "two"],
        background_label="empty", numerator_label="one", denominator_label="two",
    )
    assert set(generic["pooled_channels"]) == {"empty", "one", "two"}
    assert generic["coverage"] == pytest.approx(1 - generic["pooled_channels"]["empty"]["mean"])
    assert "ratio" in generic
    with pytest.raises(ValueError, match="absent.*missing"):
        pool_probability_map(
            probabilities, axistags=axistags, label_names=["empty", "one", "two"],
            background_label="empty", numerator_label="missing", denominator_label="two",
        )


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
        if key == "ilastikVersion":
            return FakeDataset(np.array(b"9.8.7"))
        return FakeDataset(np.array(LABELS))


def adapter(tmp_path):
    executable = tmp_path / "python"
    executable.write_bytes(b"executable")
    project = tmp_path / "model.ilp"
    project.write_bytes(b"pinned project")
    return IlastikCompletedDatasetAdapter(
        executable, project, hashlib.sha256(project.read_bytes()).hexdigest(),
        "BG", "apo_mito", "healthy_mito", timeout_s=2,
        coverage_key="mito_coverage", ratio_key="apo_fraction",
    )


def test_batch_is_one_absolute_invocation_and_cleans_intermediates(tmp_path, monkeypatch):
    probabilities, axistags, _ = recorded_output()
    instance = adapter(tmp_path)
    work_seen = []

    def run(command, **kwargs):
        assert len(command) == 7
        assert "--readonly=true" in command
        assert "--output_format=hdf5" in command
        project_arg = next(item for item in command if item.startswith("--project="))
        output_arg = next(item for item in command if item.startswith("--output_filename_format="))
        assert Path(project_arg.split("=", 1)[1]).is_absolute()
        assert Path(output_arg.split("=", 1)[1]).is_absolute()
        assert Path(command[0]).is_absolute()
        assert all(Path(item).is_absolute() for item in command[-1:])
        work = Path(kwargs["cwd"])
        work_seen.append(work)
        output = work / "field_000000_probabilities.h5"
        # Measured real naming: field_000000.tiff -> field_000000_probabilities.h5.
        output.touch()
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setitem(sys.modules, "h5py", SimpleNamespace(
        File=lambda path, mode: FakeFile(path, mode, Path("never"), probabilities, axistags)
    ))
    result = instance.analyze_completed_dataset(FakeView(), {}, FakeContext())
    assert result[0]["result"]["coordinates"] == {"position": 0}
    assert result[0]["parameters"]["project_ilastik_version"] == "9.8.7"
    assert not work_seen[0].exists()
    assert decimate_field(FakeView().read_image()).shape == (256, 256)


@pytest.mark.parametrize("relative", ["python", "model.ilp", "out/{nickname}.h5", "field.tiff"])
def test_every_command_path_is_required_to_be_absolute(relative):
    command = [
        "/app/python", "/app/ilastik", "--headless", "--readonly=true",
        "--project=/model.ilp", "--output_format=hdf5",
        "--output_filename_format=/out/{nickname}.h5", "/in/field.tiff",
    ]
    inputs = ["/in/field.tiff"]
    if relative == "python":
        command[0] = relative
    elif relative == "model.ilp":
        command[4] = f"--project={relative}"
    elif relative.startswith("out"):
        command[6] = f"--output_filename_format={relative}"
    else:
        command[-1] = relative
        inputs = [relative]
    with pytest.raises(ValueError, match="must be absolute"):
        _validate_absolute_command_paths(command, Path("/app/ilastik"), inputs)


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
            "background_label": "BG",
            "numerator_label": "apo_mito",
            "denominator_label": "healthy_mito",
            "coverage_key": "mito_coverage",
            "ratio_key": "apo_fraction",
            "timeout_s": 2,
        }, str(tmp_path / "result"),
        model_project_config={"ilastik_project": str(instance.project_path)},
    )
    assert result["status"] == "completed"
    assert len(result["observations"]) == 1
    assert result["observations"][0]["result"]["ranking_unit"] == "whole_field"
    assert result["model_project_config"]["ilastik_project"]["sha256"] == instance.project_sha256


@pytest.mark.skipif(importlib.util.find_spec("h5py") is None,
                    reason="requires optional microclaw[ilastik] dependency")
def test_real_h5py_reads_all_project_and_output_keys(tmp_path, monkeypatch):
    import h5py

    synthetic_labels = np.array([b"empty", b"first", b"second"])
    executable = tmp_path / "python"
    executable.write_bytes(b"executable")
    project = tmp_path / "synthetic.ilp"
    with h5py.File(project, "w") as handle:
        group = handle.create_group("PixelClassification")
        group.create_dataset("LabelNames", data=synthetic_labels)
        handle.create_dataset("ilastikVersion", data=np.bytes_("4.3.2"))

    prepared_output = tmp_path / "prepared.h5"
    probabilities, axistags, _ = recorded_output()
    with h5py.File(prepared_output, "w") as handle:
        dataset = handle.create_dataset("exported_data", data=probabilities)
        dataset.attrs["axistags"] = axistags

    instance = IlastikCompletedDatasetAdapter(
        executable, project, hashlib.sha256(project.read_bytes()).hexdigest(),
        "empty", "first", "second", timeout_s=2,
    )

    def run(command, **kwargs):
        expected = Path(kwargs["cwd"]) / "field_000000_probabilities.h5"
        expected.write_bytes(prepared_output.read_bytes())
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", run)
    result = instance.analyze_completed_dataset(FakeView(), {}, FakeContext())[0]
    assert result["parameters"]["project_ilastik_version"] == "4.3.2"
    assert result["parameters"]["executable_path"] == str(executable.resolve())
    assert result["parameters"]["launcher_script_path"] is None
    assert set(result["result"]["pooled_channels"]) == {
        "empty", "first", "second",
    }
    assert result["result"]["ratio"] is not None


def test_missing_h5py_names_optional_extra(tmp_path, monkeypatch):
    instance = adapter(tmp_path)
    original_import = builtins.__import__

    def missing(name, *args, **kwargs):
        if name == "h5py":
            raise ImportError("synthetic missing optional dependency")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing)
    with pytest.raises(RuntimeError, match=r"microclaw\[ilastik\]"):
        instance.analyze_completed_dataset(FakeView(), {}, FakeContext())


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


class StageView(FakeView):
    """A view that stamps intended stage coordinates, as MM does per position."""

    def read_metadata(self, **coordinates):
        return {"XPosition_um_Intended": "6005.7",
                "YPosition_um_Intended": -2457.6,
                "PositionName": "r0_c0"}


def _run_with_view(tmp_path, monkeypatch, view):
    probabilities, axistags, _ = recorded_output()
    instance = adapter(tmp_path)

    def run(command, **kwargs):
        (Path(kwargs["cwd"]) / "field_000000_probabilities.h5").touch()
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setitem(sys.modules, "h5py", SimpleNamespace(
        File=lambda path, mode: FakeFile(path, mode, Path("never"), probabilities, axistags)
    ))
    return instance.analyze_completed_dataset(view, {}, FakeContext())[0]["result"]


def test_scored_field_carries_the_stage_position_it_can_be_revisited_at(tmp_path, monkeypatch):
    # Ranking a survey is only useful if the winner can be driven to, and an
    # axis coordinate does not locate a stage. Strings are coerced: MM stamps
    # these as text on some adapters.
    result = _run_with_view(tmp_path, monkeypatch, StageView())
    assert result["stage_x_um"] == pytest.approx(6005.7)
    assert result["stage_y_um"] == pytest.approx(-2457.6)


def test_single_position_dataset_reports_no_stage_position_rather_than_failing(
        tmp_path, monkeypatch):
    # MM omits the intended-XY keys on single-position acquisitions, so absence
    # is ordinary and must not raise.
    class NoKeys(FakeView):
        def read_metadata(self, **coordinates):
            return {"PositionName": "only"}

    result = _run_with_view(tmp_path, monkeypatch, NoKeys())
    assert result["stage_x_um"] is None and result["stage_y_um"] is None
