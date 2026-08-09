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
    assert result["via"] == "micro-manager dataset reader"


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


def _java_list(items):
    """A Java List over the bridge: size()/get(i), and NOT Python-iterable."""
    java = MagicMock()
    java.size.return_value = len(items)
    java.get.side_effect = lambda index: items[index]
    return java


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


def _dataset_store(save_path, *, n_images=6, width=512, height=512):
    image = MagicMock()
    image.get_width.return_value = width
    image.get_height.return_value = height
    store = MagicMock()
    store.get_num_images.return_value = n_images
    store.get_save_path.return_value = str(save_path)
    store.get_any_image.return_value = image
    return store


def _open_dataset(monkeypatch, path, *, store=_UNSET, created=_UNSET,
                  open_windows=(), open_viewers=()):
    """Run the real directory branch.

    Returns (result, studio, displays, static_wraps). static_wraps records every
    ImageJ static this path wrapped — it must stay empty.
    """
    static_wraps = []
    dataset = MagicMock()
    dataset.axes = {"time": [0]}
    monkeypatch.setattr("microclaw.controller.Dataset", lambda unused: dataset)
    monkeypatch.setattr(
        "microclaw.controller._new_static_java_class",
        lambda port, cp: static_wraps.append(cp),
    )
    if store is _UNSET:
        store = _dataset_store(path)
    if created is _UNSET:
        display = MagicMock()
        display.get_name.return_value = Path(path).name
        created = _java_list([display])

    displays = MagicMock()
    displays.get_all_image_windows.return_value = _java_list(list(open_windows))
    displays.get_all_data_viewers.return_value = _java_list(list(open_viewers))
    displays.load_displays.return_value = created
    studio = MagicMock()
    studio.displays.return_value = displays
    studio.data.return_value.load_data.return_value = store

    ctrl = _bare_controller()
    ctrl._studio = studio
    result = MicroscopeController.open_in_imagej(ctrl, str(path))
    return result, studio, displays, static_wraps


def test_non_integer_dataset_axis_refuses_without_touching_bridge(
    monkeypatch, tmp_path
):
    dataset_path = tmp_path / "acq_1"
    dataset_path.mkdir()
    dataset = MagicMock()
    dataset.axes = {"time": [0], "channel": ["640"]}
    monkeypatch.setattr("microclaw.controller.Dataset", lambda unused: dataset)
    ctrl = _bare_controller()
    ctrl.is_connected = MagicMock(side_effect=AssertionError("bridge was touched"))
    ctrl._studio = MagicMock()

    result = MicroscopeController.open_in_imagej(ctrl, str(dataset_path))

    assert result["opened"] is False
    assert "axis `channel` has value '640'" in result["reason"]
    ctrl.is_connected.assert_not_called()
    ctrl._studio.displays.assert_not_called()


@pytest.mark.parametrize("collection", ["open_windows", "open_viewers"])
def test_already_open_dataset_reports_it_without_load_data(
    monkeypatch, tmp_path, collection
):
    dataset_path = tmp_path / "acq_1"
    dataset_path.mkdir()
    provider = MagicMock()
    provider.get_save_path.return_value = str(dataset_path.resolve())
    viewer = MagicMock()
    viewer.get_data_provider.return_value = provider

    result, studio, _, _ = _open_dataset(
        monkeypatch, dataset_path, **{collection: (viewer,)}
    )

    assert result == {
        "opened": False,
        "already_open": True,
        "via": "micro-manager dataset reader",
        "reason": (
            "This dataset is already open in Micro-Manager; it is on your "
            "screen now."
        ),
    }
    studio.data.return_value.load_data.assert_not_called()


def test_stalling_load_data_returns_a_labelled_failure(monkeypatch, tmp_path):
    """A wedged bridge call is reported, and the reason names the call.

    The test bounds its own wait. Asserting only on the returned payload makes
    the watchdog itself untestable: with the watchdog removed this call blocks
    forever, so the test would hang the suite rather than fail it, and a check
    that can only hang cannot report a verdict.
    """
    dataset_path = tmp_path / "acq_1"
    dataset_path.mkdir()
    monkeypatch.setattr("microclaw.controller._DIRECTORY_BRIDGE_TIMEOUT_S", 0.01)
    blocker = threading.Event()

    dataset = MagicMock()
    dataset.axes = {"time": [0]}
    monkeypatch.setattr("microclaw.controller.Dataset", lambda unused: dataset)
    displays = MagicMock()
    displays.get_all_image_windows.return_value = _java_list([])
    displays.get_all_data_viewers.return_value = _java_list([])
    studio = MagicMock()
    studio.displays.return_value = displays
    studio.data.return_value.load_data.side_effect = lambda *unused: blocker.wait()
    ctrl = _bare_controller()
    ctrl._studio = studio

    returned: dict = {}

    def call() -> None:
        returned["value"] = MicroscopeController.open_in_imagej(
            ctrl, str(dataset_path)
        )

    try:
        caller = threading.Thread(target=call, daemon=True)
        caller.start()
        caller.join(30)
        assert not caller.is_alive(), (
            "open_in_imagej never returned: the directory branch blocked on a "
            "stalled bridge call instead of watchdogging it."
        )
    finally:
        # Release the stubbed call so its thread exits with the test rather than
        # living until the interpreter does.
        blocker.set()

    result = returned["value"]
    assert result["opened"] is False
    assert "loadData stalled" in result["reason"]
    assert "restart" in result["reason"].lower()


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


def test_directory_uses_the_mm_reader_and_never_ij_open(monkeypatch, tmp_path):
    """The one thing 42b must not do is call IJ.open on a directory."""
    dataset = tmp_path / "acq_1"
    dataset.mkdir()
    result, studio, _, static_wraps = _open_dataset(monkeypatch, dataset)

    assert result["opened"] is True
    assert result["via"] == "micro-manager dataset reader"
    assert result["windows"] == [
        {"n_planes": 6, "title": "acq_1", "width": 512, "height": 512}
    ]
    assert static_wraps == [], "no ImageJ static was wrapped for a directory"
    # virtual=True: loadData's only modal sits inside `if (!isVirtual)`, and a
    # modal here holds the single pyjavaz lock until a human answers it.
    studio.data.return_value.load_data.assert_called_once_with(str(dataset), True)


def test_a_display_that_cannot_be_described_is_still_reported_as_open(
    monkeypatch, tmp_path
):
    """Failing to describe a window must not invert into 'nothing opened'."""
    dataset = tmp_path / "acq_1"
    dataset.mkdir()
    store = MagicMock()
    store.get_num_images.side_effect = AttributeError("no getNumImages")
    store.get_save_path.side_effect = AttributeError("no getSavePath")
    store.get_any_image.side_effect = AttributeError("no getAnyImage")
    created = MagicMock()
    created.size.return_value = 1
    created.get.side_effect = AttributeError("no get on this shadow")

    result, _, _, _ = _open_dataset(monkeypatch, dataset, store=store, created=created)
    assert result["opened"] is True, (
        "a display was created; failing to read its name does not un-create it"
    )
    assert len(result["windows"][0]["unread"]) == 3, (
        "and what could not be read must be said, not silently dropped"
    )


def test_directory_with_no_display_created_reports_failure(monkeypatch, tmp_path):
    dataset = tmp_path / "acq_1"
    dataset.mkdir()
    result, _, _, _ = _open_dataset(monkeypatch, dataset, created=_java_list([]))
    assert result["opened"] is False
    assert "opened no display window" in result["reason"]


def test_directory_that_mm_cannot_read_refuses(monkeypatch, tmp_path):
    dataset = tmp_path / "not_a_dataset"
    dataset.mkdir()
    result, _, displays, _ = _open_dataset(monkeypatch, dataset, store=None)
    assert result["opened"] is False
    assert "could not read" in result["reason"]
    displays.load_displays.assert_not_called()


def test_both_branches_answer_with_one_shape(monkeypatch, tmp_path):
    """A caller must not have to branch on `via` to understand the answer.

    Both results are produced by the real open_in_imagej, so this fails if the
    two branches ever drift apart. Comparing hand-written literals here would
    have asserted nothing about the code.
    """
    dataset = tmp_path / "acq_1"
    dataset.mkdir()
    file_result, _ = _open_file(monkeypatch, FIXTURES / "mosaic.tiff")
    dir_result, _, _, _ = _open_dataset(monkeypatch, dataset)

    assert file_result["via"] != dir_result["via"], "two branches really ran"
    for result in (file_result, dir_result):
        assert result["opened"] is True
        assert {"opened", "via", "windows"} <= set(result)
        for window in result["windows"]:
            assert {"title", "width", "height", "n_planes"} <= set(window), (
                f"{result['via']} reports a window as {sorted(window)}, which "
                "does not carry the shared contract"
            )
    # The only permitted difference is the ImageJ window id, which an MM display
    # has no equivalent of and which nothing reads.
    file_keys = set(file_result["windows"][0])
    dir_keys = set(dir_result["windows"][0])
    assert file_keys - dir_keys == {"id"}
    assert dir_keys - file_keys == set()


def test_both_branches_refuse_with_one_shape(monkeypatch, tmp_path):
    """And a refusal is one shape too: opened false, via, reason, no windows."""
    dataset = tmp_path / "acq_1"
    dataset.mkdir()
    file_result, _ = _open_file(monkeypatch, FIXTURES / "mosaic.tiff",
                                window_ids=(None, None))
    dir_result, _, _, _ = _open_dataset(monkeypatch, dataset, store=None)
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
