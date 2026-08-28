import inspect

from microclaw import agent, authorization, optics_docs, tools


def test_reference_frames_ordering_and_stand_caveat_together():
    text = optics_docs.OPTICS_REFERENCE
    section = text.split("## Usual path ordering", 1)[1].split("## What software", 1)[0]
    assert "how light paths usually work" in text.lower()
    for term in ("lamphouse", "field diaphragm", "condenser", "specimen", "objective",
                 "inverted", "epi-illumination", "TIRF", "spinning-disk", "multi-camera"):
        assert term in section


def test_reference_has_no_nikon_identifiers_or_rig_claims():
    text = optics_docs.OPTICS_REFERENCE
    for forbidden in ("PFS", "TIPFSStatus", "PFSOffset", "TINosePiece", "TILightPath"):
        assert forbidden not in text
    assert "manual prism first" in text
    assert "wrong reflecting surface" in text


def test_hint_names_documentation_tool_but_system_prompt_does_not():
    assert "get_optical_path_documentation" in inspect.getsource(tools._optical_path_state)
    assert "get_optical_path_documentation" not in agent.SYSTEM_PROMPT
    assert "optical_path and any declared_illumination_properties" in agent.SYSTEM_PROMPT
    assert agent.SYSTEM_PROMPT.count("before considering another exposure") == 1


def test_hint_asks_for_the_reference_only_when_signal_is_missing():
    # The reference is a whole document; an unconditional "call this before
    # interpreting these" pulls it into context on every session that ever reads
    # orientation. It earns its tokens only when the camera is not getting
    # signal (operator, 2026-08-28), so the hint must condition the call and
    # must say so in the same sentence that names the tool.
    hint = inspect.getsource(tools._optical_path_state)
    sentence = next(
        part for part in hint.replace("\n", " ").split(".")
        if "get_optical_path_documentation" in part
    )
    assert "not getting the signal" in sentence
    assert "when imaging is working" in hint


def test_scoped_modules_do_not_define_state_labels():
    scoped_modules = (
        ("microclaw/authorization.py", authorization),
        ("microclaw/tools.py", tools),
        ("microclaw/optics_docs.py", optics_docs),
    )
    for module_path, module in scoped_modules:
        source = inspect.getsource(module)
        assert "define_state_label" not in source, module_path
        assert "defineStateLabel" not in source, module_path


def test_documentation_tool_needs_no_bridge_writes():
    class WriteRejectingCore:
        def define_state_label(self, *args): raise AssertionError("must not write")
        def defineStateLabel(self, *args): raise AssertionError("must not write")
    result = tools.get_optical_path_documentation(
        type("Ctrl", (), {"core": WriteRejectingCore()})(), object()
    )
    assert "how light paths usually work" in result["documentation"].lower()
