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
from microclaw import ilastik_adapter
from microclaw.ilastik_adapter import (
    IlastikCompletedDatasetAdapter, _validate_absolute_command_paths, choose_stride,
    decimate_field, pool_probability_map,
)
from microclaw.safety import SafetyConstraints, SafetyGuard


LABELS = [b"BG", b"apo_mito", b"healthy_mito"]


def write_ilastik_project(path, *, labels=LABELS, resolution_um=0.127):
    h5py = pytest.importorskip("h5py")
    with h5py.File(path, "w") as handle:
        pixel_classification = handle.create_group("PixelClassification")
        pixel_classification.create_dataset("LabelNames", data=np.array(labels))
        handle.create_dataset("ilastikVersion", data=np.bytes_("1.4.1"))
        label_set = pixel_classification.create_group("LabelSets/labels000")
        block = label_set.create_dataset(
            "block0000", data=np.zeros((2, 2, 1), dtype=np.uint8)
        )
        block.attrs["axistags"] = json.dumps({
            "axes": [
                {"key": "y", "resolution": resolution_um},
                {"key": "x", "resolution": resolution_um},
                {"key": "c", "resolution": 0},
            ]
        })
        forests = pixel_classification.create_group("ClassifierForests")
        forests.create_dataset("known_labels", data=np.arange(1, len(labels) + 1))
    return path


def test_project_labels_are_refused_during_construction_without_ilastik(tmp_path, monkeypatch):
    project = write_ilastik_project(tmp_path / "model.ilp")
    monkeypatch.setattr(
        ilastik_adapter, "discover_ilastik",
        lambda: (_ for _ in ()).throw(AssertionError("ilastik discovery must not run")),
    )

    with pytest.raises(ValueError) as refusal:
        IlastikCompletedDatasetAdapter(
            project, "__unknown__", "__numerator__", "__denominator__"
        )

    message = str(refusal.value)
    assert "available labels" in message
    assert all(label.decode("utf-8") in message for label in LABELS)


def test_project_digest_is_checked_before_hdf5_and_stored_on_adapter(tmp_path, monkeypatch):
    project = write_ilastik_project(tmp_path / "model.ilp")
    actual_digest = hashlib.sha256(project.read_bytes()).hexdigest()
    import h5py

    opened = []
    real_file = h5py.File
    monkeypatch.setattr(h5py, "File", lambda *args, **kwargs: opened.append(args) or real_file(*args, **kwargs))
    with pytest.raises(ValueError, match="sha256 mismatch"):
        IlastikCompletedDatasetAdapter(
            project, "BG", "apo_mito", "healthy_mito", project_sha256="0" * 64
        )
    assert opened == []

    verified = IlastikCompletedDatasetAdapter(
        project, "BG", "apo_mito", "healthy_mito", project_sha256=actual_digest
    )
    computed = IlastikCompletedDatasetAdapter(
        project, "BG", "apo_mito", "healthy_mito"
    )
    assert (verified.project_sha256, verified.project_sha256_source) == (
        actual_digest, "verified"
    )
    assert (computed.project_sha256, computed.project_sha256_source) == (
        actual_digest, "computed"
    )


def test_saved_dataset_label_probe_precedes_missing_dataset_and_output_creation(tmp_path):
    project = write_ilastik_project(tmp_path / "model.ilp")
    output_dir = tmp_path / "probe-output"
    guard = SafetyGuard(SafetyConstraints(workspace_dir=str(tmp_path)))

    with pytest.raises(ValueError, match="available labels.*apo_mito"):
        completed_dataset.run_analysis_on_saved_dataset(
            guard, str(tmp_path / "missing-dataset"),
            "ilastik_pixel_classification", {}, "frames", {
                "project_path": str(project),
                "background_label": "__unknown__",
                "numerator_label": "__numerator__",
                "denominator_label": "__denominator__",
            }, str(output_dir),
        )
    assert not output_dir.exists()


def test_saved_dataset_with_correct_labels_still_fails_on_missing_dataset(tmp_path):
    project = write_ilastik_project(tmp_path / "model.ilp")
    output_dir = tmp_path / "analysis-output"
    guard = SafetyGuard(SafetyConstraints(workspace_dir=str(tmp_path)))

    with pytest.raises(FileNotFoundError, match="missing-dataset"):
        completed_dataset.run_analysis_on_saved_dataset(
            guard, str(tmp_path / "missing-dataset"),
            "ilastik_pixel_classification", {}, "frames", {
                "project_path": str(project),
                "background_label": "BG",
                "numerator_label": "apo_mito",
                "denominator_label": "healthy_mito",
            }, str(output_dir),
        )


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
        if key == "PixelClassification/ClassifierForests/known_labels":
            raise KeyError(key)
        return FakeDataset(np.array(LABELS))


def adapter(tmp_path):
    executable = tmp_path / "python"
    executable.write_bytes(b"executable")
    project = write_ilastik_project(tmp_path / "model.ilp")
    return IlastikCompletedDatasetAdapter(
        project, "BG", "apo_mito", "healthy_mito", executable_path=executable,
        project_sha256=hashlib.sha256(project.read_bytes()).hexdigest(), timeout_s=2,
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
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setitem(sys.modules, "h5py", SimpleNamespace(
        File=lambda path, mode: FakeFile(path, mode, Path("never"), probabilities, axistags)
    ))
    result = instance.analyze_completed_dataset(FakeView(), {}, FakeContext())
    assert result[0]["result"]["coordinates"] == {"position": 0}
    assert result[0]["parameters"]["project_ilastik_version"] == "1.4.1"
    assert not work_seen[0].exists()
    assert decimate_field(FakeView().read_image())[0].shape == (256, 256)


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
        return SimpleNamespace(returncode=0, stdout="")

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
        project, "empty", "first", "second", executable_path=executable,
        project_sha256=hashlib.sha256(project.read_bytes()).hexdigest(), timeout_s=2,
    )

    def run(command, **kwargs):
        expected = Path(kwargs["cwd"]) / "field_000000_probabilities.h5"
        expected.write_bytes(prepared_output.read_bytes())
        return SimpleNamespace(returncode=0, stdout="")

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
    with pytest.raises(RuntimeError, match=r"Extensions panel"):
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
    if case == "missing":
        project = tmp_path / "missing.ilp"
        digest = None
        expected = FileNotFoundError
    else:
        project = write_ilastik_project(tmp_path / "model.ilp")
        digest = "0" * 64
        expected = ValueError
    with pytest.raises(expected):
        IlastikCompletedDatasetAdapter(
            project, "BG", "apo_mito", "healthy_mito", project_sha256=digest,
        )


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
        return SimpleNamespace(returncode=0, stdout="")

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


def test_a_mistyped_label_is_refused_before_ilastik_is_launched(tmp_path, monkeypatch):
    # The refusal used to live in pool_probability_map, which runs only after
    # the batch: on the demo machine a mistyped label was refused 116 s in,
    # after ilastik had scored every field. The message was right and the cost
    # was the whole point, so this asserts the subprocess never happens.
    probabilities, axistags, _ = recorded_output()
    base = adapter(tmp_path)
    launched = []

    def run(command, **kwargs):
        # Write the output the adapter expects, so that on the OLD ordering this
        # test reaches the late refusal and fails on `launched`, rather than
        # dying earlier on a missing file for an unrelated reason.
        launched.append(command)
        (Path(kwargs["cwd"]) / "field_000000_probabilities.h5").touch()
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setitem(sys.modules, "h5py", SimpleNamespace(
        File=lambda path, mode: FakeFile(path, mode, Path("never"), probabilities, axistags)
    ))
    with pytest.raises(ValueError, match="absent.*mitochondria"):
        IlastikCompletedDatasetAdapter(
            base.project_path, "BG", "mitochondria", "healthy_mito",
            executable_path=base.executable_path,
            project_sha256=base.project_sha256, timeout_s=2,
        )
    assert launched == []


def test_an_argument_error_leaves_no_output_directory_behind(tmp_path, monkeypatch):
    # The directory was created before the adapter was constructed, so a wrong
    # parameter left an empty directory that then blocked the name the caller
    # retried with -- one argument error becoming two unrelated ones. Measured
    # on the demo machine: three runs, five directories.
    dataset_path = tmp_path / "dataset"
    dataset_path.mkdir()
    (dataset_path / "NDTiff.index").write_bytes(b"recorded dataset fixture")
    out = tmp_path / "never_created"
    guard = SafetyGuard(SafetyConstraints(workspace_dir=str(tmp_path)))
    with pytest.raises(TypeError):
        completed_dataset.run_analysis_on_saved_dataset(
            guard, str(dataset_path), "ilastik_pixel_classification",
            {"position": 0}, "frames", {}, str(out),
        )
    assert not out.exists()


def test_missing_adapter_arguments_say_where_arguments_go():
    # "Re-read the tool schema" was the old hint, and it could not help: the
    # schema names the tool's parameters, not the adapter's. A demo session
    # spent four calls putting them in model_project_config, which sits right
    # beside `parameters` and looks like where a project path belongs.
    from microclaw.errors import hint_for_error
    try:
        IlastikCompletedDatasetAdapter()
    except TypeError as exc:
        hint = hint_for_error(exc)
    assert "`parameters`" in hint
    assert "model_project_config" in hint


def test_decimation_keeps_the_aspect_ratio_of_a_non_square_field():
    # One stride for both axes. The classical descriptor strides each axis
    # independently, which is fine for photometry and wrong here: a trained
    # classifier is being asked about shape, and a 220x512 field strided
    # (1, 2) leaves every mitochondrion half as wide as the ones it learned.
    field = np.zeros((220, 512), dtype=np.uint16)
    decimated, stride = decimate_field(field, target_size=256)
    assert stride == 2
    assert decimated.shape == (110, 256)
    assert decimated.shape[1] / decimated.shape[0] == pytest.approx(512 / 220)


def test_a_square_field_decimates_as_it_always_did():
    field = np.zeros((2048, 2048), dtype=np.uint16)
    decimated, stride = decimate_field(field, target_size=256)
    assert (stride, decimated.shape) == (8, (256, 256))


@pytest.mark.parametrize("native,training,expected,mode", [
    # M5: 105 nm against a project drawn at 127 nm -> 1.21x, which rounds to
    # stride 1 and leaves the scale inside the classifier's own tolerance.
    (0.105, 0.127, 1, "scale_matched"),
    # A finer rig needs real decimation, and matching the scale is what asks
    # for it -- the same stride the fixed 256 target was reaching for.
    (0.0159, 0.127, 8, "scale_matched"),
    # Coarser than the training data: cannot upsample, so stride 1 and the
    # mismatch is recorded rather than interpolated away.
    (0.25, 0.127, 1, "scale_matched"),
    # Nothing to match against falls back to the old fixed target.
    (None, 0.127, 1, "unknown_scale_no_decimation"),
    (0.105, None, 1, "unknown_scale_no_decimation"),
])
def test_stride_matches_the_scale_the_project_was_drawn_at(native, training, expected, mode):
    stride, chosen = choose_stride((2048, 2048), native_pixel_size_um=native,
                                   training_resolution_um=training)
    assert (stride, chosen) == (expected, mode)


def test_an_explicit_target_size_still_wins():
    stride, mode = choose_stride((2048, 2048), native_pixel_size_um=0.105,
                                 training_resolution_um=0.127, target_size=512)
    assert (stride, mode) == (4, "explicit_target_size")


def test_the_project_hash_is_recorded_when_the_caller_supplies_none(tmp_path, monkeypatch):
    # design/26 F4 asks for the project to be hash-pinned IN THE MANIFEST.
    # That is provenance, and on first use there is nothing for the caller to
    # have pinned against -- so requiring a digest before they can run their
    # own classifier bought nothing and cost a step.
    probabilities, axistags, _ = recorded_output()
    executable = tmp_path / "python"
    executable.write_bytes(b"executable")
    project = write_ilastik_project(tmp_path / "model.ilp")
    instance = IlastikCompletedDatasetAdapter(
        project, "BG", "apo_mito", "healthy_mito", executable_path=executable,
        timeout_s=2,
    )

    def run(command, **kwargs):
        (Path(kwargs["cwd"]) / "field_000000_probabilities.h5").touch()
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setitem(sys.modules, "h5py", SimpleNamespace(
        File=lambda path, mode: FakeFile(path, mode, Path("never"), probabilities, axistags)
    ))
    recorded = instance.analyze_completed_dataset(FakeView(), {}, FakeContext())[0]["parameters"]
    assert recorded["project_sha256"] == hashlib.sha256(project.read_bytes()).hexdigest()
    assert recorded["project_sha256_source"] == "computed"


def test_a_supplied_hash_is_still_verified_and_still_refuses(tmp_path):
    project = write_ilastik_project(tmp_path / "model.ilp")
    with pytest.raises(ValueError, match="sha256 mismatch"):
        IlastikCompletedDatasetAdapter(
            project, "BG", "apo_mito", "healthy_mito", project_sha256="0" * 64,
        )


def test_it_finds_ilastik_rather_than_asking_where_it_lives(tmp_path, monkeypatch):
    # Nobody should have to tell Microclaw where ilastik is installed to score
    # their own project with it.
    probabilities, axistags, _ = recorded_output()
    project = write_ilastik_project(tmp_path / "model.ilp")
    found = tmp_path / "discovered_ilastik"
    found.write_bytes(b"executable")
    monkeypatch.setattr(ilastik_adapter, "discover_ilastik", lambda: (found, None))

    def run(command, **kwargs):
        assert command[0] == str(found)
        (Path(kwargs["cwd"]) / "field_000000_probabilities.h5").touch()
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setitem(sys.modules, "h5py", SimpleNamespace(
        File=lambda path, mode: FakeFile(path, mode, Path("never"), probabilities, axistags)
    ))
    instance = IlastikCompletedDatasetAdapter(project, "BG", "apo_mito", "healthy_mito",
                                              timeout_s=2)
    result = instance.analyze_completed_dataset(FakeView(), {}, FakeContext())[0]
    assert result["parameters"]["executable_path"] == str(found)


def test_when_it_cannot_find_ilastik_it_asks_for_the_path(tmp_path, monkeypatch):
    project = write_ilastik_project(tmp_path / "model.ilp")
    monkeypatch.setattr(ilastik_adapter, "discover_ilastik", lambda: None)
    instance = IlastikCompletedDatasetAdapter(project, "BG", "apo_mito", "healthy_mito",
                                              timeout_s=2)
    with pytest.raises(FileNotFoundError, match="Pass executable_path"):
        instance.analyze_completed_dataset(FakeView(), {}, FakeContext())


def test_ilastiks_default_resolution_is_not_read_as_a_pixel_size(tmp_path):
    # ilastik writes resolution 1 when nobody set a pixel size. Reading it as a
    # real micron decimated a 324x312 M5 field to 36x35 -- below the project's
    # own feature scales -- and ilastik produced no output at all.
    import h5py
    project = tmp_path / "unset.ilp"
    with h5py.File(project, "w") as handle:
        group = handle.create_group("PixelClassification/LabelSets/labels000")
        block = group.create_dataset("block0000", data=np.zeros((4, 4, 1), dtype=np.uint8))
        block.attrs["axistags"] = json.dumps(
            {"axes": [{"key": "y", "resolution": 1}, {"key": "x", "resolution": 1},
                      {"key": "c", "resolution": 0}]})
    with h5py.File(project, "r") as handle:
        assert ilastik_adapter._training_resolution_um(handle) is None
    stride, mode = choose_stride((324, 312), native_pixel_size_um=0.1056,
                                 training_resolution_um=None)
    assert (stride, mode) == (1, "unknown_scale_no_decimation")


def test_a_class_nobody_trained_cannot_be_a_ratio_denominator(tmp_path, monkeypatch):
    # ilastik exports one channel per NAMED label, so an untrained class comes
    # back identically zero and a ratio against it is pinned at 1.000 -- which
    # reads as a confident result. known_labels says which were really trained.
    probabilities, axistags, _ = recorded_output()
    project = tmp_path / "partly.ilp"
    project.write_bytes(b"pinned project")
    executable = tmp_path / "python"
    executable.write_bytes(b"executable")

    class PartlyTrained(FakeFile):
        def __getitem__(self, key):
            if key == "PixelClassification/ClassifierForests/known_labels":
                return FakeDataset(np.array([1, 2]))
            return super().__getitem__(key)

    monkeypatch.setitem(sys.modules, "h5py", SimpleNamespace(
        File=lambda path, mode: PartlyTrained(path, mode, Path("never"),
                                              probabilities, axistags)))
    with pytest.raises(ValueError, match="never trained on them"):
        IlastikCompletedDatasetAdapter(
            project, "BG", "apo_mito", "healthy_mito", executable_path=executable,
            timeout_s=2,
        )


def test_a_failed_batch_reports_what_ilastik_said(tmp_path, monkeypatch):
    # "did not create expected output" cost three retries on M5 while the real
    # reason -- FeatureSelectionConstraintError -- sat in captured output we
    # were discarding.
    probabilities, axistags, _ = recorded_output()
    instance = adapter(tmp_path)

    def run(command, **kwargs):
        return SimpleNamespace(returncode=0, stdout="INFO starting\nERROR FeatureSelectionConstraintError\n")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setitem(sys.modules, "h5py", SimpleNamespace(
        File=lambda path, mode: FakeFile(path, mode, Path("never"), probabilities, axistags)
    ))
    with pytest.raises(FileNotFoundError, match="FeatureSelectionConstraintError"):
        instance.analyze_completed_dataset(FakeView(), {}, FakeContext())
