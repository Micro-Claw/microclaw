from __future__ import annotations

import sys
from importlib import resources
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
        "schema", "review", "example_limits", "guaranteed_mode",
        "degraded_mode", "live_check",
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
        if parsed.constraints.plugins.allow_hardware_motion:
            # Two settings, one decision, and until this diagnostic existed you
            # only found that out by restarting into a live refusal — with the
            # rig connected and a sample under the objective. Flipping one flag
            # looks sufficient because the runtime gate it opens IS the only
            # gate; the mode line is a separate, earlier refusal.
            diagnostics.append(ConfigDiagnostic(
                "guaranteed_mode",
                "`plugins.allow_hardware_motion: true` cannot start in guaranteed "
                "mode, and this flag alone is NOT enough to run a hardware-motion "
                "plugin: guaranteed mode promises that every hardware effect is "
                "either typed and guarded or explicitly excluded, and a plugin is "
                "arbitrary Java whose effects microclaw can neither enumerate nor "
                "intercept. To allow one, ALSO set `property_authorization.mode: "
                "degraded_trusted_plugins`, which keeps every limit in this file "
                "enforced but drops the completeness claim (startup then reports "
                "the authorization map as DEGRADED). If you did not mean to run a "
                "motion plugin, set allow_hardware_motion back to false and stay "
                "in guaranteed mode.",
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

    `path` of None means the per-user default (normally generated through
    `microclaw init` and first-launch setup). That
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
            "Run `microclaw init` for restricted first-launch setup, review the "
            "generated profile, then restart Microclaw."
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
