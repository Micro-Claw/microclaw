from __future__ import annotations

import sys
from dataclasses import dataclass
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

    kind: Literal[
        "schema", "review", "deprecation", "guaranteed_mode", "degraded_mode", "live_check",
    ]
    message: str
    blocking: bool


@dataclass(frozen=True)
class ConfigValidationResult:
    """Document findings without contacting Micro-Manager."""

    path: Path
    parsed: ParsedSafetyConfig | None
    reviewed: bool | None
    diagnostics: tuple[ConfigDiagnostic, ...]

    @property
    def can_start_live_validation(self) -> bool:
        return self.parsed is not None and self.reviewed is True and not any(
            item.blocking for item in self.diagnostics
        )


def validate_safety_config(path: str | Path | None = None) -> ConfigValidationResult:
    """Check a config document and guaranteed-mode prerequisites, entirely offline.

    Live inventory remains necessary to prove that every reachable actuator has
    policy and to apply requirements conditional on reachable hardware.
    """
    p = Path(path) if path else default_safety_config()
    diagnostics: list[ConfigDiagnostic] = []
    if not p.exists():
        return ConfigValidationResult(
            p, None, None,
            (ConfigDiagnostic("schema", f"No safety config at {p}.", True),),
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
    if isinstance(loaded, dict) and "rig_profile" in loaded:
        diagnostics.append(ConfigDiagnostic(
            "deprecation",
            "`rig_profile` is deprecated; rename it to `property_authorization`, "
            "then rename `categorical_properties` to `allowed_categorical`, "
            "`typed_actuators` to `allowed_numeric`, and `excluded_properties` "
            "to `denied` (`mode` keeps its name).",
            False,
        ))
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

    acquisition = parsed.constraints.acquisition
    acquisition_fields = (
        "max_frames", "max_duration_s", "max_bytes", "max_illuminated_ms",
        "confirm_above_frames", "confirm_above_duration_s",
        "confirm_above_illuminated_ms",
    )
    if parsed.property_authorization.mode == "guaranteed":
        for name in acquisition_fields:
            if getattr(acquisition, name) is None:
                diagnostics.append(ConfigDiagnostic(
                    "guaranteed_mode",
                    f"Guaranteed mode requires a finite positive `acquisition.{name}` "
                    "at live startup; replace null with this rig's reviewed budget.",
                    True,
                ))
        if parsed.constraints.camera.max_exposure_ms is None:
            diagnostics.append(ConfigDiagnostic(
                "guaranteed_mode",
                "`camera.max_exposure_ms` is null. If a camera is reachable, "
                "guaranteed-mode live startup will refuse it; set this rig's reviewed "
                "finite positive maximum.",
                True,
            ))
        live_message = (
            "Offline validation cannot enumerate the rig. Live startup must still verify "
            "that every reachable stage has a closed declared range, every reachable "
            "actuator has policy, and reachable camera and illumination declarations "
            "match hardware."
        )
    else:
        unenforced = [
            f"acquisition.{name}"
            for name in acquisition_fields
            if getattr(acquisition, name) is None
        ]
        if parsed.constraints.camera.max_exposure_ms is None:
            unenforced.append("camera.max_exposure_ms")
        if unenforced:
            diagnostics.append(ConfigDiagnostic(
                "degraded_mode",
                "Degraded mode permits these null caps at live startup, and runtime "
                "checks do not enforce a limit when it is null: " + ", ".join(unenforced)
                + ". Set reviewed finite positive limits to enforce them; leaving them "
                "null deliberately suspends these protections.",
                False,
            ))
        live_message = (
            "Offline validation cannot enumerate the rig. Degraded-mode live startup "
            "must still enumerate connected devices, validate declared properties and "
            "channel presets, and build the authorization map; its completeness claim "
            "remains explicitly suspended for trusted plugin and unclassified paths."
        )

    diagnostics.append(ConfigDiagnostic("live_check", live_message, False))

    return ConfigValidationResult(p, parsed, reviewed, tuple(diagnostics))


def load_safety_config(path: str | Path | None = None) -> ParsedSafetyConfig:
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
