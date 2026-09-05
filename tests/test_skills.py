import ast
import json
import os
from pathlib import Path
import shutil
import subprocess
import venv

import pytest

from microclaw import agent, skills, tools


def _write_skill(root: Path, directory: str, name: str, description: str = "One line."):
    skill_dir = root / directory
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# Body\n",
        encoding="utf-8",
    )


def test_packaged_skill_catalog_metadata_and_registry_boundary():
    assert skills.SKILL_CATALOG
    names = [item.name for item in skills.SKILL_CATALOG]
    assert len(names) == len(set(names))
    assert all(item.resource.parent.name == item.name for item in skills.SKILL_CATALOG)
    assert all("\n" not in item.description for item in skills.SKILL_CATALOG)
    assert set(names).isdisjoint(tools.TOOL_REGISTRY)
    assert tools.TOOL_REGISTRY["load_skill"] is tools.load_skill


def test_malformed_frontmatter_fails_catalog_startup(tmp_path):
    root = tmp_path / "skills"
    skill_dir = root / "broken"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("name: broken\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Malformed skill frontmatter"):
        skills._build_catalog(root)


def test_directory_name_mismatch_fails_catalog_startup(tmp_path):
    root = tmp_path / "skills"
    _write_skill(root, "directory", "different")
    with pytest.raises(RuntimeError, match="Skill name mismatch"):
        skills._build_catalog(root)


def test_duplicate_names_fail_catalog_startup(tmp_path):
    root = tmp_path / "skills"
    _write_skill(root, "first", "duplicate")
    _write_skill(root, "second", "duplicate")
    with pytest.raises(RuntimeError, match="Duplicate skill names: duplicate"):
        skills._build_catalog(root)


def test_empty_catalog_fails_catalog_startup(tmp_path):
    root = tmp_path / "skills"
    root.mkdir()
    with pytest.raises(RuntimeError, match="Skill catalog is empty"):
        skills._build_catalog(root)


def test_absent_skills_tree_reports_an_empty_catalog_not_a_raw_oserror(tmp_path):
    # The wheel-side failure mode: package data matched nothing, so microclaw/
    # ships no skills/ directory at all.  importlib hands back a path that
    # cannot be listed, and the operator must still be told what is wrong.
    with pytest.raises(RuntimeError, match="Skill catalog is empty"):
        skills._build_catalog(tmp_path / "skills")


def test_skill_directory_without_skill_file_fails_catalog_startup(tmp_path):
    root = tmp_path / "skills"
    _write_skill(root, "smlm", "smlm")
    (root / "htsmlm").mkdir(parents=True)
    with pytest.raises(
        RuntimeError, match="Skill directories missing readable SKILL.md: htsmlm"
    ):
        skills._build_catalog(root)


@pytest.mark.parametrize("name", ["missing", "../smlm", "smlm/SKILL.md", "/smlm"])
def test_load_skill_refuses_unknown_and_path_shaped_names(name):
    result = tools.load_skill(None, None, name)
    assert "error" in result
    assert "Available catalog names:" in result["error"]
    assert "smlm" in result["error"]


def test_loaded_skill_emits_nothing_in_a_standalone_session_script(tmp_path):
    assert tools.load_skill._microclaw_emits_nothing is True
    records = [{"role": "assistant", "content": [{
        "type": "tool_use", "id": "skill-1", "name": "load_skill",
        "input": {"name": "smlm"},
    }]}]

    class Guard:
        def resolve_in_workspace(self, path):
            return str(tmp_path / path)

    result = tools.export_session_script(None, Guard(), "skill-session.py", records)
    source = (tmp_path / "skill-session.py").read_text(encoding="utf-8")
    assert result["emitted_calls"] == 0
    assert "NOT EMITTED" not in source
    ast.parse(source)


def test_untriggered_system_context_has_catalog_not_specialist_bodies():
    assert "Specialized workflow skills:" in agent.SYSTEM_PROMPT
    for item in skills.SKILL_CATALOG:
        assert f"- {item.name}: {item.description}" in agent.SYSTEM_PROMPT
    assert "# Single-Molecule Localization Microscopy" not in agent.SYSTEM_PROMPT
    assert "# htSMLM / EMU reference" not in agent.SYSTEM_PROMPT
    nikon_skill_body = skills.load_skill_text("nikon-pfs").split("\n---\n", 1)[1].strip()
    assert nikon_skill_body not in agent.SYSTEM_PROMPT
    assert "Do BOTH of these every time you engage the lock" not in agent.SYSTEM_PROMPT


def test_catalog_routing_rule_fires_on_being_asked_not_only_on_running():
    """The catalog replaced a trigger-worded paragraph; keep its ordering half.

    What 61a deleted said "when the user asks to do SMLM ... call it FIRST".
    The first replacement said only "load the relevant skill before RUNNING its
    specialized workflow", which fires on running a workflow rather than on
    being asked about one -- and block 61a's demo session was arguably
    compliant with it while never loading anything: asked for dSTORM
    parameters, the agent explained the rig instead and routed nowhere.

    The catalog's own descriptions already carry the trigger vocabulary
    (`smlm` names dSTORM, PALM, PAINT, DNA-PAINT), so only the ordering
    framing had to come back. This pins it.
    """
    rule = agent.SYSTEM_PROMPT.split("Specialized workflow skills:", 1)[1]
    rule = rule.split("\n\n", 1)[0]
    assert "before running its specialized workflow" in rule
    assert "asks about a task a skill covers" in rule
    assert "FIRST" in rule
    assert "before answering" in rule
    # The observed failure was a rig caveat displacing the lookup, not the
    # agent forgetting the rule existed.
    assert "not a reason to skip it" in rule


def test_htsmlm_skill_preserves_specialized_mapping_and_dose_rules():
    body = skills.load_skill_text("htsmlm")
    flat = " ".join(body.split())
    assert "get_emu_laser_map" in body
    assert "resolve_emu_device" in body
    assert "instead of trial-and-error property probing" in flat
    assert "Never calculate an EMU percentage conversion in prose" in flat
    assert "verify_emu_laser_power_calibration" in body
    assert "set_emu_laser_power_percentage" in body
    assert "get_emu_laser_power_percentage" in body
    assert "keep illumination disabled and report the disagreement" in flat


def test_smlm_skill_distinguishes_fixed_and_adaptive_timelapse_routes():
    body = skills.load_skill_text("smlm")
    density_section = body.split("## Density monitoring", 1)[1].split("\n---", 1)[0]
    density_flat = " ".join(density_section.split())
    interval_section = body.split("### Frame interval", 1)[1].split("### Number of frames", 1)[0]
    dstorm_section = body.split("### dSTORM", 1)[1].split("### PALM", 1)[0]
    frame_count_section = body.split("### Number of frames", 1)[1].split("### Channel selection", 1)[0]

    assert "adaptive 405 nm control" not in body
    assert "image_process_fn" not in density_section
    assert "signals end-of-acquisition" not in density_section
    assert "offer to write a hook" not in density_section
    assert "conditional stop is likewise refused on that fixed route" in density_flat
    assert "run_adaptive_survey" in density_section
    assert "planned position list" in density_flat
    assert "single-field adaptive time series" in density_flat
    assert 'hook_strategy="density_stop_hook"' in density_section
    assert "n_frames=None" in density_section
    assert "max_frames=80_000" in density_section
    assert "exactly one `ContinueAcquisition` or `StopAcquisition`" in density_flat
    assert "authorized dose cap" in density_flat
    assert "cannot widen" in density_flat
    assert "Do not combine it with" in density_flat
    assert "spans more than one frame" in interval_section
    assert "slows acquisition without reducing background" in interval_section
    assert "the operator may pulse" in dstorm_section
    assert "The operator gradually increases" in dstorm_section
    assert "Microclaw does not automate this feedback loop" in dstorm_section
    assert "the operator may stop" in frame_count_section
    assert "reviewed built-in example of observation-only" in density_section
    assert "not the callback shape to copy" in density_section
    assert "fixed `run_timelapse`" in density_flat
    assert "image-driven `SetIlluminationPower`" in density_flat
    assert "explicitly authorizes" in density_flat
    assert "no prompt occurs from the callback thread" in density_flat
    assert "budget of increasing writes" in density_flat
    assert "no automatic final restoration" in density_flat
    assert "device stays at the last accepted value" in density_flat
    assert "not shutter control" in density_flat
    assert "writes land asynchronously" in density_flat
    assert "Image-driven `SetDeviceProperty` and `MoveNamedStage` are refused" in density_flat


def test_built_wheel_contains_the_source_tree_skill_catalog(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    build_source = tmp_path / "source"
    shutil.copytree(
        repo,
        build_source,
        ignore=shutil.ignore_patterns(".git", "build", "dist", "*.egg-info", "__pycache__"),
    )
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    # Build with the ISOLATED venv's pip, not the running interpreter's. `uv
    # venv` installs no pip at all and uv is this project's supported toolchain
    # (README, and install.bat builds every rig with it), so a `sys.executable
    # -m pip` here cannot run in the documented dev environment -- it failed
    # with "No module named pip" the first time anyone ran the suite under uv.
    # EnvBuilder bootstraps pip via ensurepip, so this needs nothing of the
    # outer environment, and the isolation the comment below relies on is the
    # same either way. Skipping when pip is absent would be worse than failing:
    # it would retire a packaging invariant silently.
    environment = tmp_path / "wheel-venv"
    venv.EnvBuilder(with_pip=True).create(environment)
    interpreter = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    subprocess.run(
        [str(interpreter), "-m", "pip", "wheel", ".", "--no-deps",
         "--wheel-dir", str(wheelhouse)],
        cwd=build_source, check=True, capture_output=True, text=True,
    )
    wheel = next(wheelhouse.glob("microclaw-*.whl"))
    subprocess.run(
        [str(interpreter), "-m", "pip", "install", "--no-deps", str(wheel)],
        cwd=tmp_path, check=True, capture_output=True, text=True,
    )

    # Tree side: a plain source-filesystem glob. Wheel side: importlib.resources
    # in an isolated venv, outside the repo, so an ambient editable install
    # cannot make this compare the checkout with itself.
    tree_names = {path.parent.name for path in (repo / "microclaw" / "skills").glob("*/SKILL.md")}
    assert tree_names
    probe = (
        "import json\n"
        "from importlib import resources\n"
        "root = resources.files('microclaw').joinpath('skills')\n"
        "print(json.dumps(sorted(p.name for p in root.iterdir() "
        "if p.is_dir() and p.joinpath('SKILL.md').is_file())))\n"
    )
    completed = subprocess.run(
        [str(interpreter), "-I", "-c", probe], cwd=tmp_path, check=True,
        capture_output=True, text=True,
    )
    assert set(json.loads(completed.stdout)) == tree_names


def test_model_visible_guidance_has_no_development_references():
    import re

    from microclaw.hooks import PRECODED_HOOK_REGISTRY
    from microclaw.tools_schema import TOOLS

    surface = {"SYSTEM_PROMPT": agent.SYSTEM_PROMPT}
    assert skills.SKILL_CATALOG
    for item in skills.SKILL_CATALOG:
        surface[f"skill:{item.name}"] = tools.load_skill(None, None, item.name)["documentation"]
    # Include parameter descriptions as well as each tool's main description.
    assert TOOLS
    surface["TOOLS"] = json.dumps(TOOLS)
    assert PRECODED_HOOK_REGISTRY
    for name in PRECODED_HOOK_REGISTRY:
        surface[f"hook:{name}"] = tools.describe_hook(None, None, name)["class_docstring"] or ""
    leaks = {
        name: re.findall(r"[^\n]*\bdesign/\d+[^\n]*", text)
        for name, text in surface.items() if re.search(r"\bdesign/\d+", text)
    }
    assert not leaks, f"Model-visible development references: {leaks}"
