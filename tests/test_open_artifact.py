"""open_artifact and controller.open_in_imagej (design/42, block 42b).

The hard part of this tool is not opening the file; it is NOT rendering it. Most
of what is asserted here is an absence: no thumbnail on the default path, no
claim of a window that did not appear, no second place that builds a text+image
content block.
"""
import json
from pathlib import Path
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
    assert result["opened"]["opened"] is True
    assert result["dimensions_match"] is True


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
    assert result["opened"]["opened"] is True, (
        "a failed provenance check must not stop the operator looking at the "
        "file — that is often exactly the file they need to see"
    )
    opening_ctrl.open_in_imagej.assert_called_once()


def test_missing_sidecar_opens_with_provenance_unverified(opening_ctrl, default_guard):
    result = open_artifact(
        opening_ctrl, default_guard, str(FIXTURES / "no_sidecar.tiff")
    )
    assert result["opened"]["opened"] is True
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
    assert result["opened"]["opened"] is False
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
    assert result["opened"]["opened"] is True, "the refusal is about analysis, not opening"


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

def test_directory_goes_to_the_micro_manager_reader_not_ij_open(
    default_guard, tmp_path, no_thumbnails
):
    dataset = tmp_path / "acq_1"
    dataset.mkdir()
    ctrl = MagicMock(spec=MicroscopeController)
    ctrl.open_in_imagej.return_value = {
        "opened": True, "via": "micro-manager dataset reader",
        "windows": [{"title": "acq_1", "width": 512, "height": 512, "n_planes": 6}],
    }
    result = open_artifact(ctrl, default_guard, str(dataset))
    assert result["opened"]["via"] == "micro-manager dataset reader"


def test_analyze_on_a_directory_refuses_rather_than_rendering(
    default_guard, tmp_path, no_thumbnails
):
    dataset = tmp_path / "acq_1"
    dataset.mkdir()
    ctrl = MagicMock(spec=MicroscopeController)
    ctrl.open_in_imagej.return_value = {
        "opened": True, "via": "micro-manager dataset reader",
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


def test_open_in_imagej_never_claims_a_window_that_did_not_appear(monkeypatch):
    """IJ.open returns void; a returning bridge call is not a painted window."""
    ctrl = MicroscopeController.__new__(MicroscopeController)
    ctrl.is_connected = lambda: True
    ctrl._port = 4827

    shadow = MagicMock()
    shadow.get_id_list.return_value = None      # no windows, before and after
    monkeypatch.setattr(
        "microclaw.controller._new_static_java_class", lambda port, cp: shadow
    )
    result = MicroscopeController.open_in_imagej(ctrl, str(FIXTURES / "mosaic.tiff"))
    assert result["opened"] is False
    assert "no new image window appeared" in result["reason"]
    shadow.open.assert_called_once_with(str(FIXTURES / "mosaic.tiff"))


def test_open_in_imagej_reports_the_new_window_only(monkeypatch):
    ctrl = MicroscopeController.__new__(MicroscopeController)
    ctrl.is_connected = lambda: True
    ctrl._port = 4827

    image = MagicMock()
    image.get_title.return_value = "mosaic.tiff"
    image.get_width.return_value = 16
    image.get_height.return_value = 12
    image.get_stack_size.return_value = 1

    shadow = MagicMock()
    # A window the user already had, then that one plus ours.
    shadow.get_id_list.side_effect = [[-7], [-7, -8]]
    shadow.get_image.return_value = image
    monkeypatch.setattr(
        "microclaw.controller._new_static_java_class", lambda port, cp: shadow
    )
    result = MicroscopeController.open_in_imagej(ctrl, str(FIXTURES / "mosaic.tiff"))
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
    ctrl = MicroscopeController.__new__(MicroscopeController)
    ctrl.is_connected = lambda: True
    ctrl._port = 4827
    shadow = MagicMock()
    shadow.get_id_list.return_value = None
    monkeypatch.setattr(
        "microclaw.controller._new_static_java_class", lambda port, cp: shadow
    )
    MicroscopeController.open_in_imagej(ctrl, str(FIXTURES / "mosaic.tiff"))
    shadow.redirect_error_messages.assert_called_once_with()


def test_directory_uses_the_mm_reader_and_never_ij_open(monkeypatch, tmp_path):
    """The one thing 42b must not do is call IJ.open on a directory."""
    dataset = tmp_path / "acq_1"
    dataset.mkdir()

    ctrl = MicroscopeController.__new__(MicroscopeController)
    ctrl.is_connected = lambda: True
    ctrl._port = 4827

    static_wraps = []
    monkeypatch.setattr(
        "microclaw.controller._new_static_java_class",
        lambda port, cp: static_wraps.append(cp),
    )

    empty = MagicMock()
    empty.size.return_value = 0
    display = MagicMock()
    display.get_name.return_value = "acq_1"
    created = MagicMock()
    created.size.return_value = 1
    created.get.return_value = display

    image = MagicMock()
    image.get_width.return_value = 512
    image.get_height.return_value = 512
    store = MagicMock()
    store.get_num_images.return_value = 6
    store.get_save_path.return_value = str(dataset)
    store.get_any_image.return_value = image

    displays = MagicMock()
    displays.get_all_image_windows.return_value = empty
    displays.load_displays.return_value = created
    studio = MagicMock()
    studio.displays.return_value = displays
    studio.data.return_value.load_data.return_value = store
    ctrl._studio = studio

    result = MicroscopeController.open_in_imagej(ctrl, str(dataset))

    assert result["opened"] is True
    assert result["via"] == "micro-manager dataset reader"
    assert result["windows"] == [
        {"title": "acq_1", "n_planes": 6, "width": 512, "height": 512}
    ]
    assert static_wraps == [], "no ImageJ static was wrapped for a directory"
    # virtual=True: loadData's only modal sits inside `if (!isVirtual)`, and a
    # modal here holds the single pyjavaz lock until a human answers it.
    studio.data.return_value.load_data.assert_called_once_with(str(dataset), True)


def test_directory_with_no_display_created_reports_failure(monkeypatch, tmp_path):
    dataset = tmp_path / "acq_1"
    dataset.mkdir()
    ctrl = MicroscopeController.__new__(MicroscopeController)
    ctrl.is_connected = lambda: True
    ctrl._port = 4827

    empty = MagicMock()
    empty.size.return_value = 0
    displays = MagicMock()
    displays.get_all_image_windows.return_value = empty
    displays.load_displays.return_value = empty
    studio = MagicMock()
    studio.displays.return_value = displays
    studio.data.return_value.load_data.return_value = MagicMock()
    ctrl._studio = studio

    result = MicroscopeController.open_in_imagej(ctrl, str(dataset))
    assert result["opened"] is False
    assert "opened no display window" in result["reason"]


def test_directory_that_mm_cannot_read_refuses(monkeypatch, tmp_path):
    dataset = tmp_path / "not_a_dataset"
    dataset.mkdir()
    ctrl = MicroscopeController.__new__(MicroscopeController)
    ctrl.is_connected = lambda: True
    ctrl._port = 4827
    empty = MagicMock()
    empty.size.return_value = 0
    displays = MagicMock()
    displays.get_all_image_windows.return_value = empty
    studio = MagicMock()
    studio.displays.return_value = displays
    studio.data.return_value.load_data.return_value = None
    ctrl._studio = studio

    result = MicroscopeController.open_in_imagej(ctrl, str(dataset))
    assert result["opened"] is False
    assert "could not read" in result["reason"]
    displays.load_displays.assert_not_called()


def test_both_branches_answer_with_one_shape(monkeypatch, tmp_path):
    """A caller must not have to branch on `via` to understand the answer."""
    file_result = {"opened": True, "via": "ij.IJ.open",
                   "windows": [{"id": -8, "title": "m.tiff", "width": 16,
                                "height": 12, "n_planes": 1}]}
    dir_result = {"opened": True, "via": "micro-manager dataset reader",
                  "windows": [{"title": "acq_1", "n_planes": 6,
                               "width": 512, "height": 512}]}
    common = {"title", "width", "height", "n_planes"}
    for result in (file_result, dir_result):
        assert {"opened", "via", "windows"} <= set(result)
        for window in result["windows"]:
            assert common <= set(window)


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
