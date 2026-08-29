import inspect
import re

from microclaw import agent, authorization, tools
from microclaw.skills import load_skill_text


OPTICS_REFERENCE = load_skill_text("optical-paths")


def test_reference_frames_ordering_and_stand_caveat_together():
    text = OPTICS_REFERENCE
    section = text.split("## Usual path ordering", 1)[1].split("## What software", 1)[0]
    assert "how light paths usually work" in text.lower()
    for term in ("lamphouse", "field diaphragm", "condenser", "specimen", "objective",
                 "inverted", "epi-illumination", "TIRF", "spinning-disk", "multi-camera"):
        assert term in section


def test_reference_has_no_nikon_identifiers_or_rig_claims():
    text = OPTICS_REFERENCE
    for forbidden in ("PFS", "TIPFSStatus", "PFSOffset", "TINosePiece", "TILightPath"):
        assert forbidden not in text
    assert "manual prism first" in text
    assert "wrong reflecting surface" in text


def test_hint_names_skill_route_but_body_is_not_in_system_prompt():
    assert 'load_skill(name=\\"optical-paths\\")' in inspect.getsource(tools._optical_path_state)
    assert "## Usual path ordering" not in agent.SYSTEM_PROMPT
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
        if 'load_skill(name=\\"optical-paths\\")' in part
    )
    assert "not getting the signal" in sentence
    assert "when imaging is working" in hint


def test_scoped_modules_do_not_define_state_labels():
    scoped_modules = (
        ("microclaw/authorization.py", authorization),
        ("microclaw/tools.py", tools),
    )
    for module_path, module in scoped_modules:
        source = inspect.getsource(module)
        assert "define_state_label" not in source, module_path
        assert "defineStateLabel" not in source, module_path
    assert "define_state_label" not in OPTICS_REFERENCE
    assert "defineStateLabel" not in OPTICS_REFERENCE


def test_skill_loader_needs_no_bridge_writes():
    class WriteRejectingCore:
        def define_state_label(self, *args): raise AssertionError("must not write")
        def defineStateLabel(self, *args): raise AssertionError("must not write")
    result = tools.load_skill(type("Ctrl", (), {"core": WriteRejectingCore()})(), object(), "optical-paths")
    assert "how light paths usually work" in result["documentation"].lower()

def test_continuous_angle_adjuster_is_last_resort_and_never_asserts_a_normal_range():
    # A TIRF illuminator moves the incidence angle on a continuous axis, so it is
    # a light-path element with no discrete positions; orientation already shows
    # it as a bare named-stage number. Far enough off, it extinguishes the field
    # with every discrete device still correct -- but that is rare, and it is the
    # LAST thing to check (operator, 2026-08-28). The text must carry that
    # ordering itself, and must send the reader to the operator for what "normal"
    # is, because microclaw cannot know it and a guessed range would be the
    # sourced-but-wrong table design/20 and design/21 are about.
    text = OPTICS_REFERENCE
    section = " ".join(text.split("## Illumination angle", 1)[1].split())
    assert "last thing to check" in section
    assert "Ask the operator what the usual value is" in section
    assert "must not guess" in section
    # Priority is positional as well as stated: the manual prism is named as the
    # first thing to check and must precede this section.
    assert text.index("manual prism first") < text.index("## Illumination angle")
    # No numeric range may appear here -- naming one would invent the rig fact
    # the section exists to say microclaw does not have.
    assert not re.search(r"\d+\s*(?:degrees?|deg|mm|um|\u00b5m)", section)
