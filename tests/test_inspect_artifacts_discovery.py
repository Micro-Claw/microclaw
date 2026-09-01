"""Filename discovery folded into inspect_artifacts (design/62, block 62d)."""

import json
import os
from pathlib import Path

import pytest

from microclaw import tools
from microclaw.tools_schema import TOOLS


def _inspect(mock_ctrl, guard, path, **kwargs):
    return tools.inspect_artifacts(mock_ctrl, guard, [str(path)], **kwargs)


def test_discovers_mixed_case_basename_without_reading_contents(
        mock_ctrl, unconstrained_guard, tmp_path, monkeypatch):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    project = downloads / "260825_mito_m5.ilp"
    project.write_bytes(b"project contents must stay unread")

    monkeypatch.setattr(tools.hashlib, "sha256", lambda: pytest.fail("digest computed"))
    original_open = Path.open

    def reject_read(path, *args, **kwargs):
        if path == project:
            pytest.fail("file contents read")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", reject_read)
    result = _inspect(
        mock_ctrl, unconstrained_guard, downloads,
        name_glob="*M5*.ilp", recursive=False, hash=False,
    )

    assert result["matches"] == [str(project.resolve())]
    assert result["hashes_computed"] is False
    assert result["truncated"] is False
    assert result["scope"]["recursive"] is False
    assert result["scope"]["name_glob"] == "*M5*.ilp"
    assert result["scope_complete"] is True


def test_glob_is_basename_only_and_results_have_case_insensitive_stable_order(
        mock_ctrl, unconstrained_guard, tmp_path):
    folder = tmp_path / "named"
    folder.mkdir()
    for name in ("Zebra.ilp", "apple.ilp", "Beta.ilp"):
        (folder / name).touch()
    nested = folder / "M5-folder"
    nested.mkdir()
    (nested / "plain.ilp").touch()

    result = _inspect(
        mock_ctrl, unconstrained_guard, folder,
        name_glob="*.ILP", recursive=True, hash=False,
    )

    assert [Path(path).name for path in result["matches"]] == [
        "apple.ilp", "Beta.ilp", "plain.ilp", "Zebra.ilp",
    ]
    assert "M5-folder" not in result["matches"]


@pytest.mark.parametrize("name_glob", ["sub/*.ilp", r"sub\*.ilp", "..", "*.ilp.."])
def test_name_glob_rejects_both_separators_and_dot_dot(
        mock_ctrl, unconstrained_guard, tmp_path, name_glob):
    result = _inspect(
        mock_ctrl, unconstrained_guard, tmp_path,
        name_glob=name_glob, hash=False,
    )
    assert "name_glob" in result["error"]


def test_non_recursive_scope_is_complete_without_entering_child(
        mock_ctrl, unconstrained_guard, tmp_path, monkeypatch):
    root = tmp_path / "root"
    child = root / "child"
    child.mkdir(parents=True)
    top = root / "top.ilp"
    top.touch()
    (child / "nested.ilp").touch()
    original_iterdir = Path.iterdir

    def reject_child(path):
        if path == child:
            pytest.fail("entered child directory")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", reject_child)
    result = _inspect(
        mock_ctrl, unconstrained_guard, root,
        recursive=False, hash=False,
    )

    assert result["matches"] == [str(top)]
    assert result["truncated"] is False
    assert result["scope_complete"] is True


@pytest.mark.parametrize("provenance", ["hash", "manifest"])
def test_depth_bound_returns_partial_discovery_but_refuses_provenance_without_manifest(
        mock_ctrl, unconstrained_guard, tmp_path, provenance):
    root = tmp_path / "root"
    child = root / "child"
    grandchild = child / "grandchild"
    grandchild.mkdir(parents=True)
    reached = child / "reached.ilp"
    reached.touch()
    (grandchild / "beyond.ilp").touch()
    discovery = _inspect(
        mock_ctrl, unconstrained_guard, root,
        hash=False, max_depth=1,
    )
    assert discovery["matches"] == [str(reached)]
    assert discovery["truncated"] is True
    assert discovery["examined_count"] == 1

    manifest = tmp_path / f"{provenance}.json"
    kwargs = {"hash": True} if provenance == "hash" else {
        "hash": False, "manifest_path": str(manifest),
    }
    result = _inspect(
        mock_ctrl, unconstrained_guard, root,
        max_depth=1, **kwargs,
    )
    assert "reached max_depth=1" in result["error"]
    assert not manifest.exists()


def test_match_beyond_file_bound_is_an_explicit_incomplete_negative(
        mock_ctrl, unconstrained_guard, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "01_before.txt").touch()
    (root / "02_before.txt").touch()
    (root / "99_wanted.ilp").touch()

    result = _inspect(
        mock_ctrl, unconstrained_guard, root,
        name_glob="*.ilp", hash=False, max_files=2,
    )

    assert result["matches"] == []
    assert result["truncated"] is True
    assert result["examined_count"] == 2


def test_provenance_refuses_all_bounds_and_never_writes_partial_manifest(
        mock_ctrl, unconstrained_guard, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.bin").write_bytes(b"abc")
    (root / "b.bin").write_bytes(b"def")

    for bound, value, use_hash, message in (
        ("max_files", 1, False, "max_files=1"),
        ("max_total_bytes", 2, True, "max_total_bytes=2"),
    ):
        manifest = tmp_path / f"{bound}.json"
        result = _inspect(
            mock_ctrl, unconstrained_guard, root,
            manifest_path=str(manifest), hash=use_hash, **{bound: value},
        )
        assert message in result["error"]
        assert not manifest.exists()


def test_missing_directory_is_named_without_hardware_access(
        mock_ctrl, unconstrained_guard, tmp_path):
    missing = tmp_path / "missing"
    result = _inspect(mock_ctrl, unconstrained_guard, missing, hash=False)
    assert result["error"] == f"Artifact path not found: {missing.resolve()}"
    assert mock_ctrl.mock_calls == []


@pytest.mark.skipif(os.name == "nt", reason="chmod writes no ACL on Windows")
def test_unreadable_directory_is_named_without_hardware_access(
        mock_ctrl, unconstrained_guard, tmp_path):
    unreadable = tmp_path / "unreadable"
    unreadable.mkdir()
    unreadable.chmod(0)
    try:
        result = _inspect(mock_ctrl, unconstrained_guard, unreadable, hash=False)
    finally:
        unreadable.chmod(0o700)
    assert "error" in result
    assert str(unreadable) in result["error"]
    assert mock_ctrl.mock_calls == []


@pytest.mark.parametrize(
    ("name_glob", "recursive"),
    [("*", False), ("*.tif", True)],
)
def test_written_manifest_records_its_exact_provenance_scope(
        mock_ctrl, unconstrained_guard, tmp_path, name_glob, recursive):
    root = tmp_path / "root"
    child = root / "child"
    child.mkdir(parents=True)
    (root / "a.tif").touch()
    (root / "b.txt").touch()
    (child / "c.tif").touch()
    manifest = tmp_path / "manifest.json"

    result = _inspect(
        mock_ctrl, unconstrained_guard, root,
        manifest_path=str(manifest), name_glob=name_glob,
        recursive=recursive, max_files=17, max_total_bytes=23, max_depth=5,
    )
    expected_scope = {
        "name_glob": name_glob,
        "recursive": recursive,
        "max_files": 17,
        "max_total_bytes": 23,
        "max_depth": 5,
    }
    assert result["scope"] == expected_scope
    assert json.loads(manifest.read_text(encoding="utf-8"))["scope"] == expected_scope


def test_scope_reports_one_shape_in_discovery_and_in_provenance(
        mock_ctrl, unconstrained_guard, tmp_path):
    """One key, one type.

    design/62's own F4 is `z_step_um` naming two different quantities on one
    tool; a result key that is a string in one mode and an object in the other
    is that same defect moved from the request into the response. A caller
    reading result["scope"] must not have to know which mode ran.
    """
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.tif").touch()

    common = dict(name_glob="*.tif", recursive=False, max_files=9,
                  max_total_bytes=11, max_depth=3)
    discovery = _inspect(mock_ctrl, unconstrained_guard, root,
                         hash=False, **common)
    provenance = _inspect(mock_ctrl, unconstrained_guard, root,
                          hash=True, **common)

    assert discovery["scope"] == provenance["scope"]
    assert discovery["scope"] == {
        "name_glob": "*.tif", "recursive": False,
        "max_files": 9, "max_total_bytes": 11, "max_depth": 3,
    }


def test_inspect_artifacts_schema_publishes_discovery_contract():
    schema = next(item for item in TOOLS if item["name"] == "inspect_artifacts")
    properties = schema["input_schema"]["properties"]
    assert properties["name_glob"]["default"] == "*"
    assert "Case-insensitive" in properties["name_glob"]["description"]
    assert properties["recursive"]["default"] is True
    assert "examined" in properties["max_files"]["description"]
    assert "not found within the bound" in properties["max_files"]["description"]
    assert "refuses" in properties["manifest_path"]["description"]
    assert "no partial manifest" in properties["manifest_path"]["description"]
    assert "Downloads" in schema["description"]
    open_schema = next(item for item in TOOLS if item["name"] == "open_artifact")
    assert "tool returned" not in open_schema["input_schema"]["properties"]["path"]["description"]
