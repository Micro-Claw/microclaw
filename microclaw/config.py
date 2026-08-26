from __future__ import annotations

import json
import subprocess
import sys
from importlib import resources
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

import yaml

from microclaw.paths import default_safety_config
from microclaw.safety import ParsedSafetyConfig, SafetyConfigError


class UnreviewedSafetyConfig(Exception):
    """The safety config still carries the example's fictional limits."""


@dataclass(frozen=True)
class ConfigDiagnostic:
    """One machine-readable result from an offline config check."""

    # `guaranteed_mode` and `degraded_mode` went with schema 2's completeness
    # requirements: nothing emits them now, and a kind with no producer is a
    # label waiting to be reused for something it does not describe.
    kind: Literal[
        "schema", "review", "example_limits", "plugin_motion", "live_check",
    ]
    message: str
    blocking: bool


ConfigClassification = Literal["missing", "blocked", "ready"]


@dataclass(frozen=True)
class ConfigValidationResult:
    """Document findings without contacting Micro-Manager."""

    path: Path
    parsed: ParsedSafetyConfig | None
    reviewed: bool | None
    diagnostics: tuple[ConfigDiagnostic, ...]
    _missing: bool = field(default=False, repr=False)
    classification: ConfigClassification = field(init=False)

    def __post_init__(self) -> None:
        if self.parsed is None and self._missing:
            classification = "missing"
        elif self.can_start_live_validation:
            classification = "ready"
        else:
            classification = "blocked"
        object.__setattr__(self, "classification", classification)

    @property
    def can_start_live_validation(self) -> bool:
        return self.parsed is not None and self.reviewed is True and not any(
            item.blocking for item in self.diagnostics
        )


@dataclass(frozen=True)
class ClassificationComparison:
    proceed: bool
    reason: str | None = None


def validation_result_json(result: ConfigValidationResult) -> dict:
    """Serialize the existing offline validation snapshot for a slot caller."""
    return {
        "classification": result.classification,
        "path": str(result.path),
        "diagnostics": [asdict(item) for item in result.diagnostics],
    }


def compare_config_classifications(
    active: ConfigClassification, candidate: ConfigClassification,
) -> ClassificationComparison:
    """Decide whether an update preserves the active slot's config state."""
    valid = {"missing", "blocked", "ready"}
    if active not in valid or candidate not in valid:
        raise ValueError("config classification must be missing, blocked, or ready")
    if active == candidate:
        return ClassificationComparison(True)
    if candidate == "ready":
        return ClassificationComparison(
            False,
            "The update cannot promote this config into normal hardware control. "
            "Repair or re-review the file in setup until this version also "
            "classifies it `ready`, then the update proceeds.",
        )
    if active == "ready":
        return ClassificationComparison(
            False,
            f"The update would downgrade a reviewed config from `ready` to "
            f"`{candidate}`.",
        )
    return ClassificationComparison(
        False,
        f"The update must preserve the restricted `{active}` classification; "
        f"the candidate reported `{candidate}`.",
    )


def classify_config_with_slot(
    executable: str | Path, path: str | Path, *, timeout: float = 30,
) -> ConfigClassification:
    """Ask one installed slot's own CLI to classify the shared config."""
    command = [
        str(executable), "--safety-config", str(path), "check-config", "--json",
    ]
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"slot could not classify the safety config: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or f"exit code {completed.returncode}"
        raise RuntimeError(f"slot could not classify the safety config: {detail}")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("slot emitted invalid config-classification JSON") from exc
    if not isinstance(payload, dict) or payload.get("classification") not in {
        "missing", "blocked", "ready",
    }:
        raise RuntimeError("slot emitted an invalid config classification")
    return payload["classification"]


def compare_slot_configurations(
    active_executable: str | Path, candidate_executable: str | Path,
    path: str | Path, *, timeout: float = 30,
) -> ClassificationComparison:
    """Compare answers produced independently by the active and candidate slots."""
    active = classify_config_with_slot(active_executable, path, timeout=timeout)
    candidate = classify_config_with_slot(candidate_executable, path, timeout=timeout)
    return compare_config_classifications(active, candidate)


def validate_safety_config(path: str | Path | None = None) -> ConfigValidationResult:
    """Check a config document entirely offline.

    Live inventory remains necessary to prove that every reachable actuator has
    policy and to apply requirements conditional on reachable hardware.
    """
    p = Path(path) if path else default_safety_config()
    diagnostics: list[ConfigDiagnostic] = []
    if not p.exists():
        return ConfigValidationResult(
            p, None, None,
            (ConfigDiagnostic("schema", f"No safety config at {p}.", True),),
            _missing=True,
        )

    try:
        loaded = yaml.safe_load(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        return ConfigValidationResult(
            p, None, None,
            (ConfigDiagnostic("schema", f"Could not parse {p}: {exc}", True),),
        )

    reviewed = (
        loaded["reviewed"]
        if isinstance(loaded, dict) and type(loaded.get("reviewed")) is bool
        else None
    )
    if reviewed is False:
        diagnostics.append(ConfigDiagnostic(
            "review",
            "This config is intentionally unreviewed. Review every limit for this "
            "microscope, then set `reviewed: true` in the top level of the file.",
            True,
        ))

    try:
        parsed = ParsedSafetyConfig.from_yaml(str(p))
    except (OSError, UnicodeError, yaml.YAMLError, SafetyConfigError) as exc:
        diagnostics.append(ConfigDiagnostic("schema", str(exc), True))
        return ConfigValidationResult(p, None, reviewed, tuple(diagnostics))

    # Defence in depth for configs deliberately hand-authored from the packaged
    # example. Compare the source documents rather than repeating any fictional
    # number here, so this diagnostic cannot drift away from the example.
    example_path = resources.files("microclaw").joinpath("safety_config.example.yaml")
    example = yaml.safe_load(example_path.read_text(encoding="utf-8"))

    def matching_leaves(actual, reference, prefix):
        matches = []
        if isinstance(actual, dict) and isinstance(reference, dict):
            for key in actual.keys() & reference.keys():
                matches.extend(matching_leaves(
                    actual[key], reference[key], prefix + (str(key),)
                ))
        elif isinstance(actual, list) and isinstance(reference, list):
            for index, (left, right) in enumerate(zip(actual, reference)):
                matches.extend(matching_leaves(left, right, prefix + (str(index),)))
        elif type(actual) in (int, float) and type(reference) in (int, float):
            if actual == reference:
                matches.append(".".join(prefix))
        return matches

    example_matches = []
    for section in ("stage", "named_stages", "camera", "illumination", "acquisition"):
        if section in loaded and section in example:
            example_matches.extend(matching_leaves(
                loaded[section], example[section], (section,)
            ))
    if example_matches:
        diagnostics.append(ConfigDiagnostic(
            "example_limits",
            "These limit values still equal the packaged fictional hand-authoring "
            "example: " + ", ".join(sorted(example_matches)) + ". A matching value "
            "can be legitimate, so this is a review warning, not a refusal; verify "
            "each named value against this rig.",
            False,
        ))

    live_message = (
        "Offline validation cannot enumerate the rig. Live startup must still verify "
        "that every reachable stage has a closed declared range and that any optional "
        "camera, illumination, property, channel, or plugin policy matches hardware."
    )

    diagnostics.append(ConfigDiagnostic("live_check", live_message, False))

    return ConfigValidationResult(p, parsed, reviewed, tuple(diagnostics))


def load_safety_config(path: str | Path | None = None) -> ParsedSafetyConfig:
    """Load THIS RIG's limits, refusing anything a human has not signed off on.

    `path` of None means the per-user default (normally generated through
    in-app setup through `microclaw serve`). That
    is what a double-clicked desktop shortcut loads, sight unseen. Schema 3 is
    protected by reviewed stage bounds authored through in-app setup; offline
    validation also emits a non-blocking warning naming any limit that still
    equals the packaged fictional example.

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
    cfg = yaml.safe_load(p.read_text(encoding="utf-8"))
    parsed = ParsedSafetyConfig.from_yaml(str(p))
    if cfg.get("reviewed") is not True:
        raise UnreviewedSafetyConfig(str(p))

    return parsed


def load_safety_config_or_exit(path: str | Path | None = None) -> ParsedSafetyConfig:
    """`load_safety_config`, but turn its refusals into readable exits.

    Both entry points (`run_session`, `serve`) want the same three messages, and
    a novice reads them in a console window that a shortcut is about to close.
    """
    try:
        return load_safety_config(path)
    except FileNotFoundError as e:
        sys.exit(
            f"No safety config at {e}.\n"
            "Launch `microclaw serve` to open in-app setup, author this rig's "
            "security bounds, then restart Microclaw."
        )
    except UnreviewedSafetyConfig as e:
        sys.exit(
            f"The safety config at {e} has not been reviewed.\n\n"
            "Its limits are the example's — they match no real hardware, and they "
            "are what stands between the AI and your microscope.\n"
            "Rename this file rather than deleting it (it is the only written record "
            "of this rig's reviewed bounds), then launch `microclaw serve` to "
            "re-author the config through in-app setup."
        )
    except yaml.YAMLError as e:
        sys.exit(f"Could not parse the safety config: {e}")
    except SafetyConfigError as e:
        sys.exit(str(e))
