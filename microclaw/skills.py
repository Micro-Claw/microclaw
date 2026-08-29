"""Repository-owned workflow skills, read from packaged Markdown resources.

The maintained file is the text returned to the model, so prose cannot drift
from a hand-copied Python string.  Resources are resolved with
``importlib.resources`` rather than ``__file__`` so installed wheels work just
like the source tree.
"""
from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    # Annotations only, and never imported at runtime: `importlib.abc` is
    # deprecated from 3.12 and gone in 3.14, while `importlib.resources.abc`
    # does not exist on 3.10, which pyproject still supports. `from __future__
    # import annotations` above means nothing here is evaluated at run time, so
    # one guarded import covers 3.10 through 3.14 without a compatibility shim.
    from importlib.resources.abc import Traversable


@dataclass(frozen=True)
class SkillMetadata:
    name: str
    description: str
    resource: Traversable


def _parse_skill(resource: Traversable) -> SkillMetadata:
    text = resource.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise RuntimeError(f"Malformed skill frontmatter in {resource}: missing opening '---'")
    try:
        raw_frontmatter, _body = text[4:].split("\n---\n", 1)
    except ValueError as exc:
        raise RuntimeError(
            f"Malformed skill frontmatter in {resource}: missing closing '---'"
        ) from exc
    try:
        metadata = yaml.safe_load(raw_frontmatter)
    except yaml.YAMLError as exc:
        raise RuntimeError(f"Malformed skill frontmatter in {resource}: {exc}") from exc
    if not isinstance(metadata, dict):
        raise RuntimeError(f"Malformed skill frontmatter in {resource}: expected a mapping")
    if set(metadata) != {"name", "description"}:
        raise RuntimeError(
            f"Malformed skill frontmatter in {resource}: exactly name and description are required"
        )
    name = metadata["name"]
    description = metadata["description"]
    if not isinstance(name, str) or not name:
        raise RuntimeError(f"Malformed skill frontmatter in {resource}: name must be nonempty text")
    if not isinstance(description, str) or not description or "\n" in description:
        raise RuntimeError(
            f"Malformed skill frontmatter in {resource}: description must be one nonempty line"
        )
    return SkillMetadata(name=name, description=description, resource=resource)


def _build_catalog(root: Traversable) -> tuple[SkillMetadata, ...]:
    # A total package-data omission ships no files under skills/, so the
    # directory itself is absent and iterdir() raises rather than returning
    # nothing.  That is the failure this catalog is likeliest to meet in the
    # wild, so it reports the same thing as an empty tree instead of a bare
    # FileNotFoundError from inside importlib.
    try:
        children = list(root.iterdir())
    except (FileNotFoundError, NotADirectoryError):
        children = []
    skill_directories = sorted(
        (child for child in children if child.is_dir()),
        key=lambda item: item.name,
    )
    if not skill_directories:
        raise RuntimeError("Skill catalog is empty; packaged microclaw/skills/*/SKILL.md resources are missing")
    missing = [child.name for child in skill_directories
               if not child.joinpath("SKILL.md").is_file()]
    if missing:
        raise RuntimeError(
            "Skill directories missing readable SKILL.md: " + ", ".join(missing)
        )
    skill_files = [child.joinpath("SKILL.md") for child in skill_directories]
    catalog = tuple(_parse_skill(resource) for resource in skill_files)
    names = [skill.name for skill in catalog]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise RuntimeError(f"Duplicate skill names: {', '.join(duplicates)}")
    for skill in catalog:
        directory_name = skill.resource.parent.name
        if skill.name != directory_name:
            raise RuntimeError(
                f"Skill name mismatch in {skill.resource}: directory is "
                f"{directory_name!r}, name is {skill.name!r}"
            )
    return catalog


SKILL_CATALOG = _build_catalog(resources.files("microclaw").joinpath("skills"))


def catalog_text() -> str:
    """Render the generated permanent prompt catalog."""
    return "\n".join(f"- {skill.name}: {skill.description}" for skill in SKILL_CATALOG)


def load_skill_text(name: str) -> str:
    """Return one complete SKILL.md after validating an exact catalog name."""
    for skill in SKILL_CATALOG:
        if skill.name == name:
            return skill.resource.read_text(encoding="utf-8")
    available = ", ".join(skill.name for skill in SKILL_CATALOG)
    raise ValueError(f"Unknown skill {name!r}. Available catalog names: {available}")
