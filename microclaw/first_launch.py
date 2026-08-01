"""Restricted first-launch inventory interview and unreviewed profile writer."""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from difflib import get_close_matches
from pathlib import Path
from typing import Callable

import yaml

from microclaw import __version__
from microclaw.config import ConfigValidationResult, validate_safety_config
from microclaw.rig_inventory import validate_inventory_schema


class SetupRefusal(ValueError):
    """The first-launch interview cannot safely continue."""


Input = Callable[[str], str]
Output = Callable[[str], None]


INTRO = """\
FIRST-LAUNCH RESTRICTED SETUP

This mode has no agent and exposes no mutation tools or inferred authorization.
It only interviews you about the existing read-only rig inventory and writes a
profile with reviewed: false.

Enumeration is hardware contact. Connecting to the already-running
Micro-Manager Core and reading its loaded devices can trigger driver activity;
Micro-Manager may already have initialized devices when its configuration was
loaded. On a rig with an undeclared emission path, initialization may emit
before any safety config exists to gate it. The normal-startup cross-check is
downstream of this window and does not close it. The guarantee here is only:
no agent- or tool-directed hardware action before human review.

After writing the file, setup disconnects. Review every line manually, then set
reviewed: true and perform a normal restart. Configuration is loaded and the
live authorization map is built only at process startup, so edits require a
restart; setup never hot-loads its output.

max_session_illuminated_ms caps the illumination-dose ledger accumulated by one
Microclaw process. That ledger is not durable across restarts: restarting starts
a new process ledger, so this value is not a lifetime or cross-restart dose cap.

Heuristic candidates are questions, not proof. Discovery may miss physical
emission paths. Micro-Manager writability and value-domain metadata provide
classification proposals. Driver technical ranges are shown as review evidence
for bounded numeric properties, but do not silently invent a physical kind or
unit. Current values and observed focus positions are never copied as policy.
"""

CONTACT_ACKNOWLEDGEMENT = "I ACKNOWLEDGE HARDWARE CONTACT"

GLOSSARY = """\
PROPERTY CLASSIFICATION GLOSSARY

Categorical: Microclaw may write this property, but only using one of the
discrete values Micro-Manager reports (for example, a mode or binning choice).

Typed absolute position: Microclaw may write numeric positions only within the
shown lower and upper bounds. Review those bounds as safety policy.

Excluded: Microclaw will not be permitted to write this property.

Unresolved: Microclaw will not be permitted to write this property until a
human resolves its meaning and updates the profile after setup.
"""


def _commit_identity() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parent,
            capture_output=True, text=True, check=True, timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def inventory_sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class InterviewTranscript:
    """Flush every setup exchange to durable evidence as it happens."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("x", encoding="utf-8", newline="\n")
        self._write("MICROCLAW FIRST-LAUNCH INTERVIEW TRANSCRIPT")
        self._write(f"UTC timestamp: {datetime.now(timezone.utc).isoformat()}")
        self._write(f"Microclaw version: {__version__}")
        self._write(f"Microclaw commit: {_commit_identity()}")

    def _write(self, text: str) -> None:
        self._handle.write(text + ("" if text.endswith("\n") else "\n"))
        self._handle.flush()

    def identify_inventory(self, path: str | Path) -> None:
        source = Path(path).resolve()
        self._write(f"Inventory path: {source}")
        self._write(f"Inventory sha256: {inventory_sha256(source)}")
        self._write("")

    def say(self, text: str, *, stream=None) -> None:
        print(text, file=stream, flush=True)
        self._write(text)

    def ask(self, prompt: str) -> str:
        self._handle.write(prompt)
        self._handle.flush()
        answer = input(prompt)
        self._write(answer)
        return answer

    def outcome(self, text: str) -> None:
        self._write(text)

    def close(self) -> None:
        self._handle.close()


def new_interview_transcript(directory: str | Path) -> InterviewTranscript:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    return InterviewTranscript(Path(directory) / f"first-launch-transcript-{stamp}.txt")


def _choice(
    prompt: str, choices: dict[str, str], ask: Input, say: Output,
    *, default: str | None = None,
) -> str:
    rendered = ", ".join(f"{key}={value}" for key, value in choices.items())
    while True:
        suffix = f"default {default}; press Enter to accept" if default else "there is no default"
        answer = ask(f"{prompt}\nChoose one ({rendered}); {suffix}: ").strip().lower()
        if not answer and default is not None:
            return default
        if answer in choices:
            return answer
        say("SETUP REFUSAL: An explicit listed choice is required; blank or unrecognised input is never accepted.")


def _text(prompt: str, ask: Input, say: Output) -> str:
    while True:
        answer = ask(f"{prompt} (required; no default): ").strip()
        if answer:
            return answer
        say("SETUP REFUSAL: An explicit operator value is required; inventory observations are not substituted.")


def _positive(prompt: str, ask: Input, say: Output) -> float:
    while True:
        raw = _text(prompt, ask, say)
        try:
            value = float(raw)
        except ValueError:
            value = math.nan
        if math.isfinite(value) and value > 0:
            return value
        say("SETUP REFUSAL: Enter a finite number greater than zero. No driver-reported or example limit will be inferred.")


def _finite(prompt: str, ask: Input, say: Output) -> float:
    while True:
        raw = _text(prompt, ask, say)
        try:
            value = float(raw)
        except ValueError:
            value = math.nan
        if math.isfinite(value):
            return value
        say("SETUP REFUSAL: Enter a finite number. No driver-reported or example limit will be inferred.")


def _bounds(
    label: str, ask: Input, say: Output,
    default: tuple[float, float] | None = None,
) -> tuple[float, float]:
    while True:
        if default is None:
            low = _finite(f"Human-reviewed minimum for {label}", ask, say)
            high = _finite(f"Human-reviewed maximum for {label}", ask, say)
        else:
            values = []
            for edge, proposed in zip(("minimum", "maximum"), default):
                while True:
                    raw = ask(
                        f"Human-reviewed {edge} for {label} "
                        f"[MM driver technical range: {proposed}; press Enter to accept]: "
                    ).strip()
                    if not raw:
                        values.append(proposed)
                        break
                    try:
                        value = float(raw)
                    except ValueError:
                        value = math.nan
                    if math.isfinite(value):
                        values.append(value)
                        break
                    say("SETUP REFUSAL: Enter a finite number or press Enter to accept the shown MM driver bound.")
            low, high = values
        if low <= high:
            return low, high
        say("SETUP REFUSAL: The minimum exceeds the maximum; enter both bounds again.")


def load_inventory(path: str | Path) -> dict:
    """Load and structurally validate the producer-owned inventory contract."""
    source = Path(path)
    try:
        inventory = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SetupRefusal(f"SETUP REFUSAL: Could not read inventory {source}: {exc}") from exc
    if not isinstance(inventory, dict):
        raise SetupRefusal("SETUP REFUSAL: Inventory root must be an object.")
    try:
        validate_inventory_schema(inventory.get("schema"))
    except ValueError as exc:
        raise SetupRefusal(f"SETUP REFUSAL: {exc}") from exc
    missing = [name for name in ("facts", "heuristic_candidates", "human_decisions") if not isinstance(inventory.get(name), dict)]
    if missing:
        raise SetupRefusal(
            "SETUP REFUSAL: Inventory does not preserve required region(s): "
            + ", ".join(missing)
            + ". Run `microclaw inspect-rig` with this Microclaw version."
        )
    return inventory


def _property_index(inventory: dict) -> dict[str, dict]:
    return {
        f'{device["label"]}.{prop["name"]}': {
            "device": device["label"], "property": prop["name"],
            "device_type": device.get("device_type"), "record": prop,
        }
        for device in inventory["facts"].get("devices", [])
        for prop in device.get("properties", [])
    }


def _metadata_default(item: dict) -> tuple[str, str, tuple[float, float] | None]:
    record = item["record"]
    if record.get("read_only") is True or record.get("pre_init") is True:
        reasons = []
        if record.get("read_only") is True:
            reasons.append("read-only")
        if record.get("pre_init") is True:
            reasons.append("pre-init-only")
        return "x", "MM reports " + " and ".join(reasons), None
    allowed = record.get("allowed_values") or []
    if allowed:
        return "c", "MM reports allowed values " + ", ".join(map(str, allowed)), None
    numeric = str(record.get("reported_type") or "").casefold() in {
        "float", "double", "integer", "int", "long", "short",
    }
    span = record.get("technical_range") or {}
    if record.get("has_limits") is True and numeric:
        low, high = span.get("lower"), span.get("upper")
        if isinstance(low, (int, float)) and isinstance(high, (int, float)):
            return (
                "n",
                f"MM reports numeric technical range {low} to {high}; excluded until physical semantics are supplied",
                (float(low), float(high)),
            )
    return "x", "MM reports no discrete value domain or numeric limits", None


def _builtin_policy(item: dict, assignments: dict) -> str | None:
    """Return the dedicated policy owning a built-in raw-property alias."""
    device, prop = item["device"], item["property"]
    key = prop.casefold().replace("_", "").replace(" ", "")
    if device == assignments.get("camera") and key == "exposure":
        return "camera.max_exposure_ms"
    if device == assignments.get("focus") and key == "position":
        return "stage z travel bounds"
    if device == assignments.get("xy_stage") and key in {"x", "y", "xposition", "yposition"}:
        return "stage XY travel bounds"
    return None


def _revisit_entries(ask: Input, say: Output, valid: set[str]) -> set[str]:
    while True:
        raw = ask(
            "Exact property or preset names to revisit, separated by commas "
            "[press Enter for none]: "
        ).strip()
        requested = {name.strip() for name in raw.split(",") if name.strip()}
        unknown = sorted(requested - valid)
        if not unknown:
            return requested
        details = []
        for name in unknown:
            matches = get_close_matches(name, sorted(valid), n=3, cutoff=0.45)
            details.append(
                repr(name) + (f" (near: {', '.join(matches)})" if matches else "")
            )
        say(
            "SETUP REFUSAL: Revisit name(s) did not exactly match a displayed "
            "property or preset: " + "; ".join(details) + ". Re-enter the list."
        )


def _continuous_focus(device: str, prop: str, assignments: dict) -> bool:
    combined = f"{device} {prop}".casefold()
    autofocus = str(assignments.get("autofocus") or "").casefold()
    return (
        (autofocus and device.casefold() == autofocus and any(x in prop.casefold() for x in ("state", "enable", "on/off")))
        or (any(x in combined for x in ("pfs", "perfect focus", "autofocus", "focus lock"))
            and any(x in prop.casefold() for x in ("state", "enable", "on/off")))
    )


def _classification_failure(failure: dict) -> bool:
    """Whether a failed inventory query removes input an interview decision needs.

    The rules follow the inventory coordinates consumed by candidate generation
    and this interview, rather than treating every producer query as equally
    safety-classifying.
    """
    scope = str(failure.get("scope") or "")
    field = str(failure.get("field") or "")
    if scope == "core" and field == "loaded_devices":
        return True
    if scope.startswith("device:"):
        return field in {"device_type", "property_names"}
    if scope.startswith("property:"):
        # These are precisely the inputs used to establish writability,
        # categorical/enable shape, and typed/power shape. Driver lower/upper
        # limits are deliberately absent: technical ranges never become policy.
        return field in {
            "read_only", "pre_init", "allowed_values", "has_limits",
            "reported_type",
        }
    if scope.startswith("config_group:") and field == "presets":
        return True
    if scope.startswith("preset:"):
        # Every failure inside a preset scope can hide an effect coordinate.
        return True
    return False


def _nonclassifying_failure_note(failure: dict) -> str:
    """Explain why an observed failure is reviewable rather than terminal."""
    scope = str(failure.get("scope") or "")
    field = str(failure.get("field") or "")
    coordinate = f"{scope} / {field}"
    if scope.startswith("property:") and field == "current_value":
        reason = "observed current values are never copied or used as interview defaults"
    elif scope.startswith("property:") and field in {"lower_limit", "upper_limit"}:
        reason = "driver technical-range edges are never converted into safety bounds"
    elif scope.startswith("device:") and field in {
        "adapter_library", "adapter_name", "description", "state_labels",
    }:
        reason = "adapter metadata and observed state labels do not authorize a property"
    elif scope == "core_assignments":
        reason = "the missing observed core assignment is not inferred; the generated profile authorizes no absent built-in axis"
    else:
        reason = "this observational coordinate is not consumed by a safety classification or copied into policy"
    return f"ENUMERATION REVIEW NOTE: {coordinate} failed; setup continued because {reason}."


def interview(inventory: dict, *, ask: Input = input, say: Output = print) -> tuple[dict, list[str]]:
    """Walk every unresolved inventory decision and return config plus notes."""
    validate_inventory_schema(inventory.get("schema"))
    for region in ("facts", "heuristic_candidates", "human_decisions"):
        if not isinstance(inventory.get(region), dict):
            raise SetupRefusal(f"SETUP REFUSAL: Required inventory region {region!r} is missing.")
    facts, candidates = inventory["facts"], inventory["heuristic_candidates"]
    assignments = facts.get("core_device_assignments", {})
    properties = _property_index(inventory)
    writable = list(candidates.get("unclassified_writable_properties", []))
    unknown = list(candidates.get("properties_with_unknown_writability", []))
    enables = {x["path"] for x in candidates.get("illumination_enable_properties", [])}
    powers = {
        x["path"] for x in candidates.get("suspected_continuous_actuators", [])
        if not x.get("rejected_non_emitting")
    }
    duplicate_sets = [
        {row["path"] for row in group.get("representations", [])}
        for group in candidates.get("possible_duplicate_power_representations", [])
    ]
    notes: list[str] = []
    excluded: list[dict] = []
    categorical: list[dict] = []
    shutters: list[dict] = []
    power_properties: list[dict] = []
    typed: list[dict] = []
    decided: set[str] = set()

    failures = facts.get("enumeration_failures", [])
    blocking_failures = [item for item in failures if _classification_failure(item)]
    if blocking_failures or unknown:
        coordinates = [
            f"{item.get('scope')} / {item.get('field')}" for item in blocking_failures
        ] + [f"{path} / writability" for path in unknown]
        raise SetupRefusal(
            "SETUP REFUSAL: Enumeration left effects or writability unenumerable: "
            + "; ".join(coordinates)
            + ". No profile was generated; inspect the inventory evidence and resolve the read failure before setup."
        )
    notes.extend(
        _nonclassifying_failure_note(item)
        for item in failures
        if not _classification_failure(item)
    )

    all_candidates = sorted(set(writable) | enables | powers)
    defaults: dict[str, tuple[str, str, tuple[float, float] | None]] = {}
    say(GLOSSARY)
    say("DERIVED PROPERTY PROPOSAL (from Micro-Manager metadata)")
    labels = {
        "c": "categorical", "a": "typed absolute position", "x": "excluded",
        "u": "unresolved", "n": "bounded numeric, excluded pending typed semantics",
    }
    dedicated: set[str] = set()
    for path in all_candidates:
        item = properties.get(path)
        if item is None:
            raise SetupRefusal(f"SETUP REFUSAL: Candidate {path} has no matching fact record.")
        owner = _builtin_policy(item, assignments)
        if owner is not None:
            say(f"{path} [dedicated policy: {owner}; not duplicated in rig_profile]")
            dedicated.add(path)
            continue
        default = _metadata_default(item)
        defaults[path] = default
        if path in enables or path in powers:
            say(f"{path} [illumination candidate: explicit hazard classification required; {default[1]}]")
        else:
            say(f"{path} [{labels[default[0]]}: {default[1]}]")

    preset_proposals = []
    for group in facts.get("configuration_groups", []):
        for preset in group.get("presets", []):
            name = f"{group.get('name')}.{preset.get('name')}"
            effects = [
                f'{effect.get("device")}.{effect.get("property")}'
                for effect in preset.get("effects", [])
            ]
            preset_proposals.append((name, preset, effects))
            say(
                f"{name} [preset allowed: MM configuration reports structural paths "
                + (", ".join(effects) or "none") + "]"
            )

    bulk = _choice(
        "Accept all MM-derived defaults for ordinary properties and non-colliding presets? You can revisit entries by exact name next.",
        {"y": "accept proposal", "n": "review every ordinary property"}, ask, say,
        default="y",
    ) == "y"
    ordinary = set(defaults) - enables - powers
    preset_names_for_revisit = {name for name, _, _ in preset_proposals}
    revisit = _revisit_entries(ask, say, ordinary | preset_names_for_revisit) if bulk else set()

    # A duplicate representation is one decision, not two independent approvals.
    for group in duplicate_sets:
        present = sorted(group & (powers | set(writable)))
        if len(present) < 2:
            continue
        say("Possible duplicate percent/native representations: " + ", ".join(present))
        chosen = _text("Type the exact one representation to consider, or type exclude-all", ask, say)
        if chosen == "exclude-all":
            for path in present:
                item = properties[path]
                excluded.append({"device": item["device"], "property": item["property"]})
                decided.add(path)
            notes.append("OPERATOR EXCLUSION: duplicate representations " + ", ".join(present))
        elif chosen in present:
            for path in present:
                if path != chosen:
                    item = properties[path]
                    excluded.append({"device": item["device"], "property": item["property"]})
                    decided.add(path)
            notes.append(f"OPERATOR REPRESENTATION CHOICE: retained {chosen}; excluded alternatives. Technical ranges were not copied.")
        else:
            raise SetupRefusal(
                "SETUP REFUSAL: Duplicate representation choice did not exactly name one candidate; neither representation was declared."
            )

    for path in all_candidates:
        if path in decided or path in dedicated:
            continue
        item = properties.get(path)
        if item is None:
            raise SetupRefusal(f"SETUP REFUSAL: Candidate {path} has no matching fact record.")
        device, prop, kind = item["device"], item["property"], item["device_type"]
        lower_name = prop.casefold()
        if _continuous_focus(device, prop, assignments):
            say(
                f"SETUP DEFERRAL: {path} appears to enable continuous focus/autofocus. "
                "It cannot be classified as categorical before typed lock/failure/timeout and Z-movement policy lands."
            )
            excluded.append({"device": device, "property": prop})
            notes.append(
                f"UNSUPPORTED CONTINUOUS FOCUS: {path} excluded. Review the core-focus/autofocus/offset relationship as one policy question; PFS-offset workflows remain unsupported."
            )
            continue
        unsupported = (
            (kind == "CameraDevice" and "roi" in lower_name)
            or ("pulse" in lower_name and any(x in lower_name for x in ("duration", "width", "time")))
        )
        if unsupported:
            say(f"SETUP DEFERRAL: {path} is not expressible by the current safety schema; no bounds or geometry will be invented.")
            excluded.append({"device": device, "property": prop})
            notes.append(f"UNSUPPORTED ACTUATOR KIND: {path} excluded pending a typed capability.")
            continue
        if kind == "XYStageDevice" and (
            "position" in lower_name or lower_name.strip(" _-()[]") in {"x", "y"}
        ):
            say(f"SETUP REFUSAL: {path} is an ambiguous XY typed-actuator entry. The proposed axis schema is deferred; no axis is guessed.")
            excluded.append({"device": device, "property": prop})
            notes.append(f"AMBIGUOUS XY: {path} excluded pending an axis-aware schema.")
            continue
        if kind not in {
            "CameraDevice", "ShutterDevice", "StageDevice", "XYStageDevice",
            "StateDevice", "GenericDevice", "CoreDevice", "AutoFocusDevice",
        }:
            say(f"SETUP DEFERRAL: {path} has unrecognised device type {kind!r}; no actuator kind is inferred.")
            excluded.append({"device": device, "property": prop})
            notes.append(f"UNRECOGNISED DEVICE TYPE: {path} ({kind!r}) excluded pending review.")
            continue

        is_illumination = path in enables or path in powers
        if is_illumination:
            role = _choice(
                f"Illumination candidate {path}. Discovery is not exhaustive; classify this surfaced candidate explicitly.",
                {"e": "emission/enable", "p": "power", "x": "exclude", "u": "unresolved"}, ask, say,
            )
            if role == "e":
                shutters.append({
                    "device": device, "property": prop,
                    "on_value": _text(f"Exact operator-confirmed ON value for {path}", ask, say),
                    "off_value": _text(f"Exact operator-confirmed OFF value for {path}", ask, say),
                })
            elif role == "p":
                units = _choice(f"Representation units for {path}", {"p": "percent", "n": "native"}, ask, say)
                row = {"device": device, "property": prop, "units": "percent" if units == "p" else "native"}
                if units == "n":
                    row["full_scale"] = _positive(f"Operator-confirmed native full scale for {path}", ask, say)
                power_properties.append(row)
            else:
                excluded.append({"device": device, "property": prop})
                wording = "OPERATOR EXCLUSION" if role == "x" else "UNRESOLVED ILLUMINATION"
                notes.append(f"{wording}: {path}; no illumination authorization was inferred.")
            continue

        default_role, evidence, default_bounds = defaults[path]
        if default_role == "x" and bulk and path not in revisit:
            excluded.append({"device": device, "property": prop})
            notes.append(f"MM METADATA EXCLUSION: {path}; {evidence}.")
            continue
        recommendation = " (known TTL.State0 false-positive shape; exclusion is recommended but requires your confirmation)" if path.casefold().endswith("ttl.state0") else ""
        needs_question = not bulk or path in revisit or bool(recommendation)
        role = "x" if default_role == "n" else default_role
        if needs_question:
            role = _choice(
                f"Writable property {path} [{labels[default_role]}: {evidence}]{recommendation}",
                {"c": "categorical", "a": "typed absolute position", "x": "exclude", "u": "unresolved"}, ask, say,
                default="x" if default_role == "n" else default_role,
            )
        if role == "c":
            categorical.append({"device": device, "property": prop})
        elif role == "a":
            unit = _text(
                f"Physical unit for {path}; the current typed schema supports only um",
                ask, say,
            ).casefold()
            if unit not in {"um", "µm"}:
                say(
                    f"SETUP DEFERRAL: {path} uses operator-supplied unit {unit!r}, "
                    "which the current typed schema cannot express; it remains excluded."
                )
                excluded.append({"device": device, "property": prop})
                notes.append(
                    f"UNSUPPORTED TYPED UNIT: {path} excluded; operator supplied {unit!r}."
                )
                continue
            if default_bounds is not None:
                low, high = _bounds(path + " in um", ask, say, default_bounds)
            else:
                low, high = _bounds(path + " in um", ask, say)
            typed.append({
                "device": device, "property": prop, "kind": "absolute-position",
                "units": "um", "minimum": low, "maximum": high,
            })
        else:
            excluded.append({"device": device, "property": prop})
            wording = "OPERATOR EXCLUSION" if role == "x" else "UNRESOLVED PROPERTY"
            notes.append(f"{wording}: {path}; no authorization was inferred.")

    # Structural core assignments select which human-entered limits are needed.
    stage: dict[str, float] = {}
    if assignments.get("xy_stage"):
        stage["x_min"], stage["x_max"] = _bounds("core XY x travel (um)", ask, say)
        stage["y_min"], stage["y_max"] = _bounds("core XY y travel (um)", ask, say)
    if assignments.get("focus"):
        stage["z_min"], stage["z_max"] = _bounds("core focus z travel (um)", ask, say)
    # The shared offline guaranteed-mode validator requires this finite cap even
    # when it cannot prove a camera is reachable. Never emit a null that passes
    # schema parsing only to be refused at normal live startup.
    camera = {
        "max_exposure_ms": _positive(
            "Human-reviewed maximum camera exposure in ms (required by guaranteed-mode offline validation)",
            ask, say,
        )
    }

    acquisition = {}
    labels = {
        "max_frames": "hard maximum frames", "max_duration_s": "hard maximum acquisition duration (s)",
        "max_bytes": "hard maximum raw payload bytes", "max_illuminated_ms": "hard maximum illuminated time per acquisition (ms)",
        "max_session_illuminated_ms": "hard maximum illuminated time in this process ledger (ms; not durable across restarts)",
        "confirm_above_frames": "human-confirmation threshold frames", "confirm_above_duration_s": "human-confirmation threshold duration (s)",
        "confirm_above_bytes": "human-confirmation threshold raw bytes", "confirm_above_illuminated_ms": "human-confirmation threshold illuminated time (ms)",
    }
    for key, label in labels.items():
        acquisition[key] = _positive(label, ask, say)

    illumination: dict = {
        "require_confirm_on_enable": True,
        "shutters": shutters,
        "power_properties": power_properties,
    }
    if power_properties:
        illumination["max_power_percent"] = _positive("Human-reviewed maximum illumination power (canonical percent)", ask, say)
        illumination["max_power_step_factor"] = _positive("Human-reviewed maximum consecutive power step factor", ask, say)

    preset_names = []
    typed_paths = {f'{x["device"]}.{x["property"]}' for x in typed}
    for name, preset, effects in preset_proposals:
        collisions = sorted(set(effects) & typed_paths)
        needs_question = not bulk or name in revisit or bool(collisions)
        decision = "y"
        if needs_question:
            decision = _choice(
                f"Preset {name} affects structural paths: "
                + (", ".join(effects) or "none observed")
                + (f"; TYPED-PROPERTY COLLISION: {', '.join(collisions)}" if collisions else ""),
                {"y": "allow", "n": "exclude"}, ask, say, default="y",
            )
        if decision == "y":
            preset_names.append(preset["name"])

    prior = inventory["human_decisions"]
    if prior.get("source") or prior.get("comparison"):
        say(
            "Existing reviewed-config comparison is preserved as evidence only; "
            "it does not answer this interview: " + json.dumps(prior, sort_keys=True)
        )
        notes.append(
            "PRIOR HUMAN-DECISION REGION: an existing reviewed-config comparison was "
            "present in inventory.json and remains evidence only; no value or approval was copied."
        )

    autofocus, focus = assignments.get("autofocus"), assignments.get("focus")
    offset_devices = sorted({x["device"] for x in typed if "offset" in x["device"].casefold() or "offset" in x["property"].casefold()})
    if autofocus or offset_devices:
        notes.append(
            "CONTINUOUS-FOCUS REVIEW QUESTION: jointly review core focus stage "
            f"{focus!r}, autofocus device {autofocus!r}, and possible offset stage(s) {offset_devices!r}. "
            "No movement policy or engagement position was inferred; PFS-offset workflows are unsupported until settling/read-back work lands."
        )

    config = {
        "schema_version": 2,
        "reviewed": False,
        "rig_profile": {
            "mode": "guaranteed",
            "categorical_properties": sorted(categorical, key=lambda x: (x["device"], x["property"])),
            "typed_actuators": sorted(typed, key=lambda x: (x["device"], x["property"])),
            "excluded_properties": sorted(excluded, key=lambda x: (x["device"], x["property"])),
        },
        "stage": stage,
        "acquisition": acquisition,
        "channels": {"allowed": sorted(set(preset_names))},
        "illumination": illumination,
        "named_stages": [],
        "plugins": {"blocked": [], "allow_hardware_motion": False},
    }
    config["camera"] = camera
    return config, notes


def write_profile(config: dict, notes: list[str], path: str | Path) -> ConfigValidationResult:
    """Validate an adjacent temporary draft, then atomically publish it."""
    config = dict(config)
    config["reviewed"] = False
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    header = [
        "# GENERATED BY RESTRICTED FIRST-LAUNCH SETUP — MANUAL REVIEW REQUIRED",
        "# Inventory observations were not copied as values or limits.",
        "# Disconnect -> review every declaration/limit -> set reviewed: true -> normal restart.",
    ]
    header.extend("# " + line for line in notes)
    text = "\n".join(header) + "\n" + yaml.safe_dump(config, sort_keys=False)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=target.parent,
            prefix=f".{target.name}.", suffix=".tmp", delete=False,
        ) as handle:
            handle.write(text)
            temporary = Path(handle.name)
        result = validate_safety_config(temporary)
        blockers = [item for item in result.diagnostics if item.blocking]
        if result.parsed is None or any(item.kind != "review" for item in blockers):
            details = "\n".join(f"- {item.message}" for item in blockers)
            # --force authorizes replacement of a pre-existing output. If the
            # replacement is invalid, do not leave either that stale path or
            # the rejected temporary looking like the product of this run.
            target.unlink(missing_ok=True)
            raise SetupRefusal(
                "SETUP REFUSAL: The shared safety-config validator rejected the "
                f"generated profile; no file was written at {target}.\n{details}"
            )
        os.replace(temporary, target)
        temporary = None
        return ConfigValidationResult(
            target, result.parsed, result.reviewed, result.diagnostics
        )
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def disconnect_core(core: object, port: int) -> None:
    """Release the Core shadow and close the setup process's ZMQ bridge."""
    close = getattr(core, "_close", None)
    if callable(close):
        close()
    # pycro-manager has no public Core.disconnect(). Its public Core factory
    # obtains a cached pyjavaz Bridge; closing that bridge is the actual socket
    # disconnect. The setup CLI is a dedicated process and owns this bridge.
    try:
        from pyjavaz.bridge import Bridge
        ref = Bridge._cached_bridges_by_port.get(port)
        bridge = ref() if ref is not None else None
        if bridge is not None:
            bridge.close()
    except (ImportError, AttributeError):
        # Process exit is still a hard connection boundary on backends without
        # pyjavaz. Do not turn cleanup API drift into a hardware operation.
        pass
