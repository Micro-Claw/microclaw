"""open_artifact and controller.open_in_imagej (design/42, block 42b).

The hard part of this tool is not opening the file; it is NOT rendering it. Most
of what is asserted here is an absence: no thumbnail on the default path, no
claim of a window that did not appear, no second place that builds a text+image
content block.
"""
import json
from pathlib import Path
import threading
from unittest.mock import MagicMock

import numpy as np
import pytest
import tifffile

from microclaw import image_analysis
from microclaw.controller import MicroscopeController
from microclaw.tools import TOOL_REGISTRY, open_artifact

FIXTURES = Path(__file__).parent / "fixtures" / "artifacts"


@pytest.fixture
def opening_ctrl():
    """A controller whose open_in_imagej reports a window matching the fixture."""
    ctrl = MagicMock(spec=MicroscopeController)
    ctrl.open_in_imagej.return_value = {
        "opened": True,
        "via": "ij.IJ.open",
        "windows": [{"id": 4294967292, "title": "mosaic.tiff",
                     "width": 16, "height": 12, "n_planes": 1}],
    }
    return ctrl


@pytest.fixture
def no_thumbnails(monkeypatch):
    """Any call into thumbnail rendering is a test failure, not a slow test."""
    def explode(*args, **kwargs):
        raise AssertionError(
            "make_thumbnail was called on a path that must not render pixels"
        )
    monkeypatch.setattr(image_analysis, "make_thumbnail", explode)


# --- The default path renders nothing --------------------------------------- #

def test_default_call_returns_a_dict_and_never_renders(
    opening_ctrl, default_guard, no_thumbnails
):
    result = open_artifact(opening_ctrl, default_guard, str(FIXTURES / "mosaic.tiff"))
    assert isinstance(result, dict), (
        "the default call must return a payload, not a content list — an image "
        "block stays in the conversation for every subsequent turn"
    )
    assert result["opened"] is True
    assert result["dimensions_match"] is True


def test_opened_is_a_top_level_boolean(default_guard, no_thumbnails):
    """Nesting this made the top-level `opened` a dict — truthy on failure.

    Both the schema and the agent prompt tell the model not to claim a window
    unless `opened` is true. A container at that key makes that instruction
    false exactly when it matters, which is the failure this whole tool exists
    to prevent.
    """
    ctrl = MagicMock(spec=MicroscopeController)
    ctrl.open_in_imagej.return_value = {
        "opened": False, "via": "ij.IJ.open", "reason": "nothing appeared."
    }
    result = open_artifact(ctrl, default_guard, str(FIXTURES / "mosaic.tiff"))
    assert result["opened"] is False
    assert not result["opened"], "a failed open must be falsy at the top level"
    assert result["reason"] == "nothing appeared."


def test_analyze_is_off_by_default_in_the_signature():
    import inspect
    assert inspect.signature(open_artifact).parameters["analyze"].default is False


def test_analyze_true_renders_one_text_and_one_image(opening_ctrl, default_guard):
    result = open_artifact(
        opening_ctrl, default_guard, str(FIXTURES / "mosaic.tiff"), analyze=True
    )
    assert isinstance(result, list) and len(result) == 2
    assert result[0]["type"] == "text"
    assert result[1]["type"] == "image"
    payload = json.loads(result[0]["text"])
    assert "nonzero pixels" in payload["thumbnail_stretch"], (
        "the payload must say the stretch was masked, or the model is reading a "
        "contrast it was not given"
    )


# --- Provenance ------------------------------------------------------------- #

def test_intact_artifact_reports_both_digests_matching(opening_ctrl, default_guard):
    result = open_artifact(opening_ctrl, default_guard, str(FIXTURES / "mosaic.tiff"))
    assert result["pixel_sha256_matches"] is True
    assert result["manifest_payload_sha256_matches"] is True
    assert result["kind"] == "stage_coordinate_mosaic"
    assert result["coverage_fraction"] == pytest.approx(0.25)


def test_tampered_tiff_reports_mismatch_and_still_opens(opening_ctrl, default_guard):
    result = open_artifact(
        opening_ctrl, default_guard, str(FIXTURES / "tampered_mosaic.tiff")
    )
    assert result["pixel_sha256_matches"] is False
    # The manifest itself is intact; only the pixels moved.
    assert result["manifest_payload_sha256_matches"] is True
    assert result["opened"] is True, (
        "a failed provenance check must not stop the operator looking at the "
        "file — that is often exactly the file they need to see"
    )
    opening_ctrl.open_in_imagej.assert_called_once()


def test_missing_sidecar_opens_with_provenance_unverified(opening_ctrl, default_guard):
    result = open_artifact(
        opening_ctrl, default_guard, str(FIXTURES / "no_sidecar.tiff")
    )
    assert result["opened"] is True
    assert "unverified" in result["provenance"]
    assert "pixel_sha256_matches" not in result


def test_a_json_that_is_not_our_manifest_is_not_provenance(
    opening_ctrl, default_guard
):
    result = open_artifact(
        opening_ctrl, default_guard, str(FIXTURES / "foreign_sidecar.tiff")
    )
    assert "unverified" in result["provenance"], (
        "reading someone else's .json as provenance is worse than having none"
    )


def test_missing_artifact_is_an_error_not_an_open(opening_ctrl, default_guard, tmp_path):
    result = open_artifact(opening_ctrl, default_guard, str(tmp_path / "nope.tiff"))
    assert "error" in result
    opening_ctrl.open_in_imagej.assert_not_called()


# --- Never report a window the user cannot see ------------------------------ #

def test_a_failed_open_is_reported_as_such(default_guard, no_thumbnails):
    ctrl = MagicMock(spec=MicroscopeController)
    ctrl.open_in_imagej.return_value = {
        "opened": False, "via": "ij.IJ.open", "reason": "no new image window appeared."
    }
    result = open_artifact(ctrl, default_guard, str(FIXTURES / "mosaic.tiff"))
    assert result["opened"] is False
    assert "dimensions_match" not in result
    # Provenance is still reported: the file is real even if nothing painted.
    assert result["pixel_sha256_matches"] is True


def test_dimension_mismatch_is_reported(default_guard, no_thumbnails):
    ctrl = MagicMock(spec=MicroscopeController)
    ctrl.open_in_imagej.return_value = {
        "opened": True, "via": "ij.IJ.open",
        "windows": [{"id": 1, "title": "mosaic.tiff",
                     "width": 999, "height": 999, "n_planes": 1}],
    }
    result = open_artifact(ctrl, default_guard, str(FIXTURES / "mosaic.tiff"))
    assert result["dimensions_match"] is False, (
        "a title match alone is nearly self-confirming; the dimensions are the "
        "load-bearing part of the structural check"
    )
    assert result["measured"] == {"width": 16, "height": 12, "n_planes": 1}


# --- Ambiguous stacks refuse rather than render plane 0 --------------------- #

def test_ambiguous_stack_refuses_and_names_the_axes(
    opening_ctrl, default_guard, no_thumbnails
):
    result = open_artifact(
        opening_ctrl, default_guard, str(FIXTURES / "stack.tiff"), analyze=True
    )
    assert isinstance(result, dict)
    assert "ambiguous" in result["analysis_refused"]
    assert "'z'" in result["analysis_refused"] or "z" in result["analysis_refused"]
    assert result["opened"] is True, "the refusal is about analysis, not opening"


def test_stack_with_a_selection_renders_that_plane(opening_ctrl, default_guard):
    result = open_artifact(
        opening_ctrl, default_guard, str(FIXTURES / "stack.tiff"),
        analyze=True, axis_selection={"z": 1},
    )
    assert isinstance(result, list)
    assert json.loads(result[0]["text"])["selection"] == {"z": 1}


def test_out_of_range_selection_refuses(opening_ctrl, default_guard, no_thumbnails):
    result = open_artifact(
        opening_ctrl, default_guard, str(FIXTURES / "stack.tiff"),
        analyze=True, axis_selection={"z": 99},
    )
    assert "0..2" in result["analysis_refused"]


# --- Directories ------------------------------------------------------------ #

def test_a_directory_that_is_not_a_dataset_still_measures_what_it_opened(
    default_guard, tmp_path, no_thumbnails
):
    """The round-3 gate case: `D:\\stitch_test`, a TIFF beside its datasets.

    Measuring the directory as an NDTiff dataset fails there, and the payload
    used to claim `opened: true` with no `dimensions_match` at all — a window
    reported without the structural check that is the load-bearing part of it.
    """
    folder = tmp_path / "stitch_test"
    (folder / "dataset_1").mkdir(parents=True)
    tifffile.imwrite(folder / "mosaic.tiff",
                     np.zeros((12, 16), dtype=np.uint16))

    ctrl = MagicMock(spec=MicroscopeController)
    ctrl.open_in_imagej.return_value = {
        "opened": True, "via": "ij.IJ.open (dataset stack files)",
        "windows": [{"id": 1, "title": "mosaic.tiff",
                     "width": 16, "height": 12, "n_planes": 1}],
        "n_stack_files": 1,
    }
    result = open_artifact(ctrl, default_guard, str(folder))

    assert result["opened"] is True
    assert result["dimensions_match"] is True, (
        "a window was claimed, so it must have been checked against the file "
        "that produced it"
    )
    assert result["measured"]["files"] == [
        {"file": "mosaic.tiff", "width": 16, "height": 12, "n_planes": 1}
    ]
    assert "not_a_dataset" in result["measured"], (
        "and the payload must still say this was not read as a dataset"
    )


def test_an_unchecked_extra_window_is_not_reported_as_matching(
    default_guard, tmp_path, no_thumbnails
):
    """`any` would pass here: one window matches and the other is never checked."""
    folder = tmp_path / "two"
    folder.mkdir()
    tifffile.imwrite(folder / "a.tif", np.zeros((12, 16), dtype=np.uint16))
    tifffile.imwrite(folder / "b.tif", np.zeros((12, 16), dtype=np.uint16))

    ctrl = MagicMock(spec=MicroscopeController)
    ctrl.open_in_imagej.return_value = {
        "opened": True, "via": "ij.IJ.open (dataset stack files)",
        "windows": [{"id": 1, "title": "a.tif", "width": 16, "height": 12,
                     "n_planes": 1},
                    {"id": 2, "title": "b.tif", "width": 999, "height": 999,
                     "n_planes": 1}],
        "n_stack_files": 2,
    }
    result = open_artifact(ctrl, default_guard, str(folder))
    assert result["dimensions_match"] is False



def test_directory_via_reaches_the_tool_payload(
    default_guard, tmp_path, no_thumbnails
):
    dataset = tmp_path / "acq_1"
    dataset.mkdir()
    ctrl = MagicMock(spec=MicroscopeController)
    ctrl.open_in_imagej.return_value = {
        "opened": True, "via": "ij.IJ.open (dataset stack files)",
        "windows": [{"title": "acq_1", "width": 512, "height": 512, "n_planes": 6}],
    }
    result = open_artifact(ctrl, default_guard, str(dataset))
    assert result["via"] == "ij.IJ.open (dataset stack files)"


def test_analyze_on_a_directory_refuses_rather_than_rendering(
    default_guard, tmp_path, no_thumbnails
):
    dataset = tmp_path / "acq_1"
    dataset.mkdir()
    ctrl = MagicMock(spec=MicroscopeController)
    ctrl.open_in_imagej.return_value = {
        "opened": True, "via": "ij.IJ.open (dataset stack files)",
        "windows": [{"title": "acq_1", "width": 512, "height": 512, "n_planes": 6}],
    }
    result = open_artifact(ctrl, default_guard, str(dataset), analyze=True)
    assert isinstance(result, dict)
    assert "analysis_refused" in result


# --- controller.open_in_imagej ---------------------------------------------- #

def test_open_in_imagej_with_no_bridge_returns_opened_false():
    ctrl = MicroscopeController.__new__(MicroscopeController)
    ctrl.is_connected = lambda: False
    result = MicroscopeController.open_in_imagej(ctrl, "/anywhere/at/all.tiff")
    assert result == {"opened": False, "reason": "No Micro-Manager bridge connection."}


def test_open_in_imagej_reports_a_java_failure_rather_than_raising(monkeypatch):
    ctrl = MicroscopeController.__new__(MicroscopeController)
    ctrl.is_connected = lambda: True
    ctrl._port = 4827

    def boom(port, classpath):
        raise RuntimeError("Class not found on any classloaders")
    monkeypatch.setattr("microclaw.controller._new_static_java_class", boom)

    result = MicroscopeController.open_in_imagej(ctrl, str(FIXTURES / "mosaic.tiff"))
    assert result["opened"] is False
    assert "Class not found" in result["reason"]


_UNSET = object()


def _bare_controller():
    """A controller with a live bridge and nothing else — no MagicMock spec.

    open_in_imagej is called unbound so the real method runs against exactly the
    two attributes it uses.
    """
    ctrl = MicroscopeController.__new__(MicroscopeController)
    ctrl.is_connected = lambda: True
    ctrl._port = 4827
    return ctrl


def _open_file(monkeypatch, path, *, window_ids=([-7], [-7, -8])):
    """Run the real file branch. Returns (result, the ij static shadow)."""
    image = MagicMock()
    image.get_title.return_value = Path(path).name
    image.get_width.return_value = 16
    image.get_height.return_value = 12
    image.get_stack_size.return_value = 1

    shadow = MagicMock()
    shadow.get_id_list.side_effect = list(window_ids)
    shadow.get_image.return_value = image
    monkeypatch.setattr(
        "microclaw.controller._new_static_java_class", lambda port, cp: shadow
    )
    return MicroscopeController.open_in_imagej(_bare_controller(), str(path)), shadow


def _open_dataset_dir(monkeypatch, directory, *, window_ids=None, n_files=1):
    """Run the real directory branch against a directory of TIFF stack files.

    An NDTiff dataset is ordinary TIFF files plus an NDTiff.index sidecar, so the
    directory branch resolves those files and hands each to the same IJ.open the
    file branch uses. Returns (result, the ij static shadow).
    """
    if window_ids is None:
        # One extra window id per file, so each open looks like it produced one.
        window_ids = []
        for i in range(n_files):
            window_ids.append(list(range(-7, -7 - i, -1)) or [])
            window_ids.append(list(range(-7, -7 - i - 1, -1)))

    image = MagicMock()
    image.get_title.return_value = "stack.tif"
    image.get_width.return_value = 16
    image.get_height.return_value = 12
    image.get_stack_size.return_value = 1

    shadow = MagicMock()
    shadow.get_id_list.side_effect = list(window_ids)
    shadow.get_image.return_value = image
    monkeypatch.setattr(
        "microclaw.controller._new_static_java_class", lambda port, cp: shadow
    )
    result = MicroscopeController.open_in_imagej(
        _bare_controller(), str(directory)
    )
    return result, shadow


def _dataset_dir(tmp_path, name="acq_1", *, n_files=1, extras=("NDTiff.index",)):
    """A directory shaped like a saved NDTiff dataset."""
    directory = tmp_path / name
    directory.mkdir()
    for i in range(n_files):
        suffix = "" if i == 0 else f"_{i}"
        (directory / f"{name}_NDTiffStack{suffix}.tif").write_bytes(b"II*\x00")
    for extra in extras:
        (directory / extra).write_bytes(b"x")
    return directory


def test_open_in_imagej_never_claims_a_window_that_did_not_appear(monkeypatch):
    """IJ.open returns void; a returning bridge call is not a painted window."""
    path = FIXTURES / "mosaic.tiff"
    result, shadow = _open_file(monkeypatch, path, window_ids=(None, None))
    assert result["opened"] is False
    assert "no new image window appeared" in result["reason"]
    shadow.open.assert_called_once_with(str(path))


def test_open_in_imagej_reports_the_new_window_only(monkeypatch):
    result, shadow = _open_file(monkeypatch, FIXTURES / "mosaic.tiff")
    assert result["opened"] is True
    assert [w["id"] for w in result["windows"]] == [-8], (
        "only the window this call created may be reported; the user's existing "
        "windows are theirs and are neither claimed nor touched"
    )
    assert result["windows"][0]["width"] == 16
    # Never close, never re-use, never setTempCurrentImage.
    for forbidden in ("close", "set_temp_current_image", "setTempCurrentImage"):
        assert not getattr(shadow, forbidden).called


def test_open_in_imagej_arms_redirect_error_messages(monkeypatch):
    """A modal IJ1 error dialog would hold the single pyjavaz lock."""
    _, shadow = _open_file(monkeypatch, FIXTURES / "mosaic.tiff",
                           window_ids=(None, None))
    shadow.redirect_error_messages.assert_called_once_with()


def test_directory_opens_the_tiffs_inside_it(monkeypatch, tmp_path):
    """An NDTiff dataset is TIFF files; ImageJ reads TIFF natively.

    Micro-Manager's dataset reader is not used and is not present: it cannot
    open what microclaw writes (design/42-block42b-gate-findings.md F1 and F4).
    """
    directory = _dataset_dir(tmp_path)
    result, shadow = _open_dataset_dir(monkeypatch, directory)

    assert result["opened"] is True
    assert result["via"] == "ij.IJ.open (dataset stack files)"
    assert result["n_stack_files"] == 1
    assert len(result["windows"]) == 1
    opened = [c.args[0] for c in shadow.open.call_args_list]
    assert opened == [str(directory / "acq_1_NDTiffStack.tif")], (
        "the directory itself must never be handed to IJ.open: 42a measured "
        "that as a silent no-op that held the bridge for 6.94 s"
    )


def test_split_dataset_opens_every_stack_file(monkeypatch, tmp_path):
    """A dataset split across files opens as several windows, by design."""
    directory = _dataset_dir(tmp_path, n_files=3)
    result, shadow = _open_dataset_dir(monkeypatch, directory, n_files=3)

    assert result["opened"] is True
    assert result["n_stack_files"] == 3
    opened = [Path(c.args[0]).name for c in shadow.open.call_args_list]
    assert opened == sorted(opened), "stack files open in their own plane order"
    assert len(opened) == 3


def test_directory_with_no_tiffs_refuses_and_says_so(monkeypatch, tmp_path):
    directory = _dataset_dir(tmp_path, n_files=0)
    result, shadow = _open_dataset_dir(monkeypatch, directory)

    assert result["opened"] is False
    assert "No TIFF files" in result["reason"]
    shadow.open.assert_not_called()


def test_a_stack_file_that_fails_does_not_hide_the_ones_that_opened(
    monkeypatch, tmp_path
):
    """Partial success is success for the windows that appeared, and names the rest."""
    directory = _dataset_dir(tmp_path, n_files=2)
    # First open produces a window, second produces none.
    result, _ = _open_dataset_dir(
        monkeypatch, directory, window_ids=([-7], [-7, -8], [-7, -8], [-7, -8])
    )

    assert result["opened"] is True
    assert len(result["windows"]) == 1
    assert len(result["unopened"]) == 1
    assert "NDTiffStack_1.tif" in result["unopened"][0]


def test_stalling_ij_open_returns_a_labelled_failure(monkeypatch, tmp_path):
    """A wedged bridge call is reported, and the reason names the call.

    The test bounds its own wait. Asserting only on the returned payload makes
    the watchdog itself untestable: with the watchdog removed this call blocks
    forever, so the test would hang the suite rather than fail it, and a check
    that can only hang cannot report a verdict.
    """
    monkeypatch.setattr("microclaw.controller._OPEN_BRIDGE_TIMEOUT_S", 0.01)
    blocker = threading.Event()

    shadow = MagicMock()
    shadow.get_id_list.return_value = [-7]
    shadow.open.side_effect = lambda *unused: blocker.wait()
    monkeypatch.setattr(
        "microclaw.controller._new_static_java_class", lambda port, cp: shadow
    )
    ctrl = _bare_controller()
    returned: dict = {}

    def call() -> None:
        returned["value"] = MicroscopeController.open_in_imagej(
            ctrl, str(FIXTURES / "mosaic.tiff")
        )

    try:
        caller = threading.Thread(target=call, daemon=True)
        caller.start()
        caller.join(30)
        assert not caller.is_alive(), (
            "open_in_imagej never returned: it blocked on a stalled bridge call "
            "instead of watchdogging it."
        )
    finally:
        blocker.set()

    result = returned["value"]
    assert result["opened"] is False
    assert "IJ.open(mosaic.tiff) stalled" in result["reason"]
    assert "restart" in result["reason"].lower()


def test_both_paths_answer_with_one_shape(monkeypatch, tmp_path):
    """A caller must not have to branch on `via` to understand the answer.

    Both results come from the real open_in_imagej, so this fails if the file
    path and the directory path ever drift apart.
    """
    directory = _dataset_dir(tmp_path)
    file_result, _ = _open_file(monkeypatch, FIXTURES / "mosaic.tiff")
    dir_result, _ = _open_dataset_dir(monkeypatch, directory)

    assert file_result["via"] != dir_result["via"], "two paths really ran"
    for result in (file_result, dir_result):
        assert result["opened"] is True
        assert {"opened", "via", "windows"} <= set(result)
        for window in result["windows"]:
            assert {"id", "title", "width", "height", "n_planes"} <= set(window), (
                f"{result['via']} reports a window as {sorted(window)}, which "
                "does not carry the shared contract"
            )


def test_both_paths_refuse_with_one_shape(monkeypatch, tmp_path):
    """And a refusal is one shape too: opened false, via, reason, no windows."""
    directory = _dataset_dir(tmp_path, n_files=0)
    file_result, _ = _open_file(monkeypatch, FIXTURES / "mosaic.tiff",
                                window_ids=(None, None))
    dir_result, _ = _open_dataset_dir(monkeypatch, directory)
    for result in (file_result, dir_result):
        assert result["opened"] is False
        assert set(result) == {"opened", "via", "reason"}


# --- One place builds a text+image block ------------------------------------ #

def test_image_content_is_the_only_place_the_pair_is_built():
    package = Path(image_analysis.__file__).parent
    builders = {
        source_file.name: source_file.read_text(encoding="utf-8").count('"type": "image"')
        for source_file in sorted(package.glob("*.py"))
    }
    assert {name: n for name, n in builders.items() if n} == {"image_analysis.py": 1}, (
        f"the text+image content block is built in more than one place: "
        f"{ {name: n for name, n in builders.items() if n} }. image_content is "
        "the one definition; a fourth hand-built copy is the defect this "
        "consolidation removed."
    )


def test_image_content_shape():
    payload = {"a": 1}
    blocks = image_analysis.image_content(
        payload, np.zeros((8, 8), dtype=np.uint16), max_size=32
    )
    assert [b["type"] for b in blocks] == ["text", "image"]
    assert json.loads(blocks[0]["text"]) == payload
    assert blocks[1]["source"]["media_type"] == "image/png"


# --- The mask ---------------------------------------------------------------- #

def test_mask_changes_the_stretch_on_a_mostly_empty_canvas():
    """The fixture mosaic is 75% uncovered zeros — the case design/42 measured."""
    image = tifffile.imread(FIXTURES / "mosaic.tiff")
    assert np.count_nonzero(image) / image.size == pytest.approx(0.25)
    unmasked = image_analysis.make_thumbnail(image, max_size=32)
    masked = image_analysis.make_thumbnail(image, max_size=32, mask=image != 0)
    assert unmasked != masked, (
        "stretching across the uncovered zeros drags the black point to 0, so "
        "the real signal is compressed into the top of the ramp and the "
        "analyze path is reading a contrast it was not given"
    )


def test_mask_that_selects_nothing_falls_back_to_the_whole_image():
    image = np.zeros((16, 16), dtype=np.uint16)
    assert image_analysis.make_thumbnail(image, max_size=8, mask=image != 0)


def test_unmasked_behaviour_is_unchanged():
    rng = np.random.default_rng(0)
    image = rng.integers(0, 4096, size=(32, 32), dtype=np.uint16)
    assert (image_analysis.make_thumbnail(image, max_size=16)
            == image_analysis.make_thumbnail(image, max_size=16, mask=None))


# --- Registration ------------------------------------------------------------ #

def test_open_artifact_is_registered_and_emits_nothing():
    assert TOOL_REGISTRY["open_artifact"] is open_artifact
    assert open_artifact._microclaw_emits_nothing is True, (
        "a display step has no place in a re-run script"
    )


def test_schema_description_holds_the_default_off():
    from microclaw.tools_schema import TOOLS
    schema = next(t for t in TOOLS if t["name"] == "open_artifact")
    analyze = schema["input_schema"]["properties"]["analyze"]["description"]
    assert "ONLY when" in analyze
    assert "show me" in analyze.lower()
    assert "FIJI" in schema["description"], (
        "the description is what stops the model offering FIJI instead"
    )


def test_agent_prompt_tells_it_to_call_and_stop():
    from microclaw.agent import SYSTEM_PROMPT
    assert "open_artifact and stop there" in SYSTEM_PROMPT
    assert "Never tell them to open it in FIJI" in SYSTEM_PROMPT
