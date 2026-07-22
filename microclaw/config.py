from __future__ import annotations

import sys
from pathlib import Path

import yaml

from microclaw.paths import default_safety_config
from microclaw.safety import SafetyConfigError, SafetyConstraints


class UnreviewedSafetyConfig(Exception):
    """The safety config still carries the example's fictional limits."""


def load_safety_config(path: str | Path | None = None) -> SafetyConstraints:
    """Load THIS RIG's limits, refusing anything a human has not signed off on.

    `path` of None means the per-user default (`microclaw init` writes it). That
    is what a double-clicked desktop shortcut loads, sight unseen — so the
    `reviewed: true` line is the only thing between a novice and a stage driven
    under the example's fictional bounds (design/14 §6, design/17 v2).

    The gate applies to an explicit --safety-config path too. "The file I typed"
    and "the file the icon loaded" being governed by different rules is the kind
    of asymmetry that gets forgotten; the cost is a one-line edit to configs that
    predate this.

    Fails closed: a missing file, a missing key, an unparseable file, and
    `reviewed: false` all refuse.
    """
    p = Path(path) if path else default_safety_config()
    if not p.exists():
        raise FileNotFoundError(p)

    # utf-8 explicit: the file is hand-edited and may hold µm; the Windows
    # default is cp1252 (design/14 knowledge-base bug).
    cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(cfg, dict) or cfg.get("reviewed") is not True:
        raise UnreviewedSafetyConfig(str(p))

    return SafetyConstraints.from_yaml(str(p))


def load_safety_config_or_exit(path: str | Path | None = None) -> SafetyConstraints:
    """`load_safety_config`, but turn its refusals into readable exits.

    Both entry points (`run_session`, `serve`) want the same three messages, and
    a novice reads them in a console window that a shortcut is about to close.
    """
    try:
        return load_safety_config(path)
    except FileNotFoundError as e:
        sys.exit(
            f"No safety config at {e}.\n"
            "Run `microclaw init` to create one, then edit it for this microscope."
        )
    except UnreviewedSafetyConfig as e:
        sys.exit(
            f"The safety config at {e} has not been reviewed.\n\n"
            "Its limits are the example's — they match no real hardware, and they "
            "are what stands between the AI and your microscope.\n"
            "Open the file, set every limit for THIS instrument, then change the "
            "line `reviewed: false` to `reviewed: true`."
        )
    except yaml.YAMLError as e:
        sys.exit(f"Could not parse the safety config: {e}")
    except SafetyConfigError as e:
        sys.exit(str(e))
