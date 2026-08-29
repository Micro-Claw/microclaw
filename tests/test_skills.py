import ast
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
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
    skill_dir = tmp_path / "broken"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("name: broken\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Malformed skill frontmatter"):
        skills._build_catalog(tmp_path)


def test_directory_name_mismatch_fails_catalog_startup(tmp_path):
    _write_skill(tmp_path, "directory", "different")
    with pytest.raises(RuntimeError, match="Skill name mismatch"):
        skills._build_catalog(tmp_path)


def test_duplicate_names_fail_catalog_startup(tmp_path):
    _write_skill(tmp_path, "first", "duplicate")
    _write_skill(tmp_path, "second", "duplicate")
    with pytest.raises(RuntimeError, match="Duplicate skill names: duplicate"):
        skills._build_catalog(tmp_path)


def test_empty_catalog_fails_catalog_startup(tmp_path):
    with pytest.raises(RuntimeError, match="Skill catalog is empty"):
        skills._build_catalog(tmp_path)


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
    assert "Specialized workflow skills (generated" in agent.SYSTEM_PROMPT
    for item in skills.SKILL_CATALOG:
        assert f"- {item.name}: {item.description}" in agent.SYSTEM_PROMPT
    assert "# Single-Molecule Localization Microscopy" not in agent.SYSTEM_PROMPT
    assert "# htSMLM / EMU reference" not in agent.SYSTEM_PROMPT
    # Block 61a deliberately leaves the core Nikon paragraph for block 61c.
    assert "Do BOTH of these every time you engage the lock" in agent.SYSTEM_PROMPT


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
    subprocess.run(
        [sys.executable, "-m", "pip", "wheel", ".", "--no-deps",
         "--wheel-dir", str(wheelhouse)],
        cwd=build_source, check=True, capture_output=True, text=True,
    )
    wheel = next(wheelhouse.glob("microclaw-*.whl"))
    environment = tmp_path / "wheel-venv"
    venv.EnvBuilder(with_pip=True).create(environment)
    interpreter = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
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
