"""Every third-party module the agent imports is a declared core dependency.

`microclaw.agent` is imported by every entry point (CLI, `serve`, the desktop
launcher), so a module it needs that is not in `[project] dependencies` makes a
plain `install.bat` install (`.[serve]`) fail to start at all. design/84 84a
imported `httpx` at the top of `agent.py` while `httpx` was declared only in the
`test` extra; it had arrived transitively through `anthropic`, which since moved
to `httpx2`, so a fresh non-editable `.[serve]` install raised
`ModuleNotFoundError: No module named 'httpx'` (measured 2026-10-06). A
transitive dependency is not a declaration.
"""
from __future__ import annotations

import ast
import importlib.metadata
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _core_dependency_names() -> set[str]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    return {_normalise(re.split(r"[<>=!~;\[ ]", spec, maxsplit=1)[0])
            for spec in project["dependencies"]}


def _top_level_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_agent_imports_only_declared_core_dependencies():
    third_party = {name for name in _top_level_imports(ROOT / "microclaw" / "agent.py")
                   if name != "microclaw" and name not in sys.stdlib_module_names
                   and name != "__future__"}
    providers = importlib.metadata.packages_distributions()
    declared = _core_dependency_names()
    undeclared = {
        module: sorted(providers.get(module, ["<not installed>"]))
        for module in third_party
        if not any(_normalise(dist) in declared for dist in providers.get(module, []))
    }
    assert not undeclared, (
        f"agent.py imports modules no core dependency provides: {undeclared}. "
        "Add the distribution to [project] dependencies in pyproject.toml; an extra "
        "or a transitive dependency does not reach an install.bat install."
    )
