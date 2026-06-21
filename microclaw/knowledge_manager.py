from __future__ import annotations
from pathlib import Path
import yaml

KNOWLEDGE_PATH = Path.home() / ".microclaw" / "knowledge.yaml"
CATEGORIES = ("samples", "devices", "strategies")


def load_knowledge() -> dict:
    if not KNOWLEDGE_PATH.exists():
        return {}
    return yaml.safe_load(KNOWLEDGE_PATH.read_text()) or {}


def save_entry(category: str, key: str, value: dict) -> None:
    if category not in CATEGORIES:
        raise ValueError(f"Unknown category '{category}'. Must be one of: {', '.join(CATEGORIES)}")
    data = load_knowledge()
    data.setdefault(category, {})[key] = value
    KNOWLEDGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    KNOWLEDGE_PATH.write_text(yaml.dump(data, default_flow_style=False, allow_unicode=True))


def delete_entry(category: str, key: str) -> bool:
    data = load_knowledge()
    if category not in data or key not in data[category]:
        return False
    del data[category][key]
    if not data[category]:
        del data[category]
    KNOWLEDGE_PATH.write_text(yaml.dump(data, default_flow_style=False, allow_unicode=True))
    return True


def format_for_prompt(knowledge: dict) -> str | None:
    populated = {c: knowledge[c] for c in CATEGORIES if knowledge.get(c)}
    if not populated:
        return None
    content = yaml.dump(populated, default_flow_style=False, allow_unicode=True)
    return (
        "## User knowledge base\n\n"
        "The following information was saved from previous sessions:\n\n"
        f"```yaml\n{content}```"
    )
