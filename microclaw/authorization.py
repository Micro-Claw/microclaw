"""Live Phase-1 authorization-map construction and runtime path gates."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
from numbers import Real
import sys
from typing import Any, Iterable

from microclaw.config import ConfigDiagnostic
from microclaw.safety import (
    ActuatorId,
    BUILTIN_TYPED_CAPABILITIES,
    ParsedSafetyConfig,
    TypedActuatorId,
    TypedActuatorPolicy,
)


_ACQUISITION_POLICY_FIELDS = (
    ("max_frames", "per-plan frame maximum"),
    ("max_duration_s", "per-plan estimated-duration maximum"),
    ("max_bytes", "per-plan estimated-byte maximum"),
    ("max_illuminated_ms", "per-plan illuminated-time maximum"),
    ("confirm_above_frames", "per-plan frame confirmation threshold"),
    ("confirm_above_duration_s", "per-plan estimated-duration confirmation threshold"),
    ("confirm_above_illuminated_ms", "per-plan illuminated-time confirmation threshold"),
)


def _acquisition_tool_names() -> list[str]:
    """Read the public tool registry; function metadata is the coverage source."""
    from microclaw.tools import TOOL_REGISTRY

    return sorted(
        name for name, fn in TOOL_REGISTRY.items()
        if getattr(fn, "_microclaw_acquisition_entry_point", False)
    )


class RigAuthorizationError(RuntimeError):
    """The connected rig cannot satisfy the declared authorization profile."""

    def __init__(
        self, message: str, diagnostics: Iterable[ConfigDiagnostic] = ()
    ) -> None:
        super().__init__(message)
        self.diagnostics = tuple(diagnostics)


def _live_emu_laser_enables(
    ctrl: Any, loaded_devices: list[str]
) -> tuple[list[tuple[int, str, str]], list[str]]:
    """Read semantic EMU laser enables from the running MM installation.

    This is startup discovery only: asking ImageJ for its application directory
    and reading EMU's config file do not write hardware.  In particular, do not
    treat a per-user cached/guessed MM path as authoritative here; a stale path
    is not evidence about the live installation being authorized.
    """
    from microclaw.emu_manager import (
        _emu_config_path,
        _has_emu_config,
        build_emu_map,
        read_emu_config,
        resolve_mm_app_dir,
    )

    if not callable(getattr(ctrl, "get_mm_app_dir", None)):
        # Read-only/offline controller implementations have no live JVM whose
        # installation can be authorized.
        return [], []
    try:
        # Startup validation is read-only, including with respect to the
        # per-user locator cache. Normal tool callers retain write-through.
        resolution = resolve_mm_app_dir(ctrl, cache_live=False)
    except Exception as exc:
        return [], [
            "Could not locate the live Micro-Manager installation for EMU semantic "
            f"laser-enable discovery: {_clean_exception_message(exc)}"
        ]
    mm_app_dir = resolution.path
    if mm_app_dir is None:
        return [], [
            "Could not establish the live Micro-Manager installation for EMU "
            f"semantic laser-enable discovery (live probe: {resolution.live_probe}; "
            "no validated fallback). Verify the running ImageJ/Micro-Manager "
            "application directory before restarting."
        ]

    config_path = _emu_config_path(mm_app_dir)
    if not config_path.exists():
        # Emu.jar is present in nearly every stock MM installation.  Only an
        # actual config says this rig uses EMU; a validated live path without
        # one is therefore an ordinary non-EMU rig.
        if resolution.source == "live" and not _has_emu_config(mm_app_dir):
            return [], []
        return [], [
            f"Could not establish EMU semantics for the live installation: "
            f"{mm_app_dir} was located via {resolution.source!r} "
            f"(live probe: {resolution.live_probe}) but has no readable "
            "EMU/config.uicfg. A fallback path cannot prove that the connected "
            "installation is non-EMU."
        ]

    problems: list[str] = []
    if resolution.source != "live":
        problems.append(
            f"EMU/config.uicfg was found through {resolution.source} fallback at "
            f"{mm_app_dir}, but the live Micro-Manager path was "
            f"{resolution.live_probe}. The semantic map is checked conservatively, "
            "but this fallback cannot prove it belongs to the connected JVM."
        )
    try:
        config = read_emu_config(mm_app_dir, loaded_devices)
        lasers = build_emu_map(config["properties"])["lasers"]
    except Exception as exc:
        return [], [
            f"Could not resolve EMU semantic laser enables from {config_path}: "
            f"{_clean_exception_message(exc)}"
        ]

    enables: list[tuple[int, str, str]] = []
    for slot, laser in sorted(lasers.items()):
        enable = laser.get("enable")
        if enable is None:
            continue
        device = enable.get("device")
        prop = enable.get("property")
        if not isinstance(device, str) or not device or not isinstance(prop, str) or not prop:
            semantic = f"Laser {slot} enable"
            problems.append(
                f"EMU semantic {semantic!r} is allocated but its exact Micro-Manager "
                "device/property could not be resolved from the live loaded-device "
                "inventory; refusing to claim that illumination declarations are complete."
            )
            continue
        enables.append((slot, device, prop))
    return enables, problems


# This is deliberately the one preset group microclaw executes. It is fixed,
# rather than read from Core.ChannelGroup, because that property is writable and
# preset-controlled; on M5 its measured allowed values were only ["", "System"].
# Revisiting the decision requires changing this constant and re-gating expansion,
# authorization, and apply semantics for every newly exposed group (especially
# System, whose M5 Startup preset arms four lasers with TTL = 1).
CHANNEL_CONFIG_GROUP = "Channel"


class ChannelPlanError(RuntimeError):
    """A captured channel plan could not be safely completed."""


class ChannelPlanPartialApplicationError(ChannelPlanError):
    """A plan failed after writes landed; the message records rollback."""


class ChannelPlanSafeStateError(ChannelPlanPartialApplicationError):
    """Rollback itself failed, so the executor cannot claim a clean state."""


@dataclass(frozen=True)
class AuthorizationEntry:
    path: str
    classification: str
    device: str | None = None
    property: str | None = None
    capability: str | None = None
    axis: str | None = None
    detail: str | None = None
    # Where a categorical decision came from: "declared" (an explicit
    # rig_profile.categorical_properties entry) or "auto:state-device" (this
    # module classified the device as an MM StateDevice at startup). None for
    # every other classification. The rig operator reads this field to verify
    # an auto-classification without diffing the config.
    source: str | None = None


@dataclass
class AuthorizationMap:
    mode: str
    verdict: str
    complete: bool | None
    entries: list[AuthorizationEntry] = field(default_factory=list)
    excluded_presets: dict[str, list[str]] = field(default_factory=dict)
    authorized_presets: frozenset[str] = frozenset()
    channel_expansion_hashes: dict[str, str] = field(default_factory=dict)
    diagnostics: tuple[ConfigDiagnostic, ...] = ()

    def to_dict(self) -> dict:
        out = asdict(self)
        out["authorized_presets"] = sorted(self.authorized_presets)
        return out


def _strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        return [str(value)]
    try:
        return [str(item) for item in value]
    except TypeError:
        size = getattr(value, "size", None)
        get = getattr(value, "get", None)
        if callable(size) and callable(get):
            return [str(get(i)) for i in range(int(size()))]
        return []


def _setting_value(setting: Any, names: Iterable[str]) -> str | None:
    if isinstance(setting, dict):
        for name in names:
            if name in setting:
                return str(setting[name])
        return None
    for name in names:
        value = getattr(setting, name, None)
        if callable(value):
            try:
                return str(value())
            except Exception:
                continue
        if value is not None:
            return str(value)
    return None


def _config_settings(config: Any) -> list[Any]:
    if config is None:
        return []
    if isinstance(config, dict):
        settings = config.get("settings", config)
        if isinstance(settings, dict):
            return [
                {"device": pair[0], "property": pair[1], "value": value}
                for pair, value in settings.items()
                if isinstance(pair, tuple) and len(pair) == 2
            ]
        return list(settings)
    if isinstance(config, (list, tuple)):
        return list(config)
    for size_name, get_name in (
        ("size", "get_setting"),
        ("size", "getSetting"),
        ("get_number_of_settings", "get_setting"),
    ):
        size = getattr(config, size_name, None)
        get = getattr(config, get_name, None)
        if callable(size) and callable(get):
            return [get(i) for i in range(int(size()))]
    try:
        return list(config)
    except TypeError:
        return []


def _expand_preset(core: Any, preset: str) -> list[tuple[str, str, str | None]]:
    config = core.get_config_data(CHANNEL_CONFIG_GROUP, preset)
    effects = []
    for setting in _config_settings(config):
        device = _setting_value(
            setting, ("device", "device_label", "get_device_label", "getDeviceLabel")
        )
        prop = _setting_value(
            setting, ("property", "property_name", "get_property_name", "getPropertyName")
        )
        value = _setting_value(
            setting, ("value", "property_value", "get_property_value", "getPropertyValue")
        )
        if not device or not prop:
            raise RigAuthorizationError(
                f"Channel preset {preset!r} contains an unreadable setting, so its "
                "effects cannot be authorized. No safety-config declaration can permit "
                "an effect whose device/property identity is unknown; repair or recreate "
                "the preset in Micro-Manager, then restart validation."
            )
        effects.append((device, prop, value))
    return effects


def _expansion_hash(effects: Iterable[tuple[str, str, str | None]]) -> str:
    encoded = json.dumps(list(effects), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _clean_exception_message(exc: Exception) -> str:
    """Return one useful line without Java/JNI class-name or stack noise."""
    message = next(
        (line.strip() for line in str(exc).splitlines() if line.strip()),
        type(exc).__name__,
    )
    for prefix in ("java.lang.", "mmcorej.", "org.micromanager."):
        message = message.replace(prefix, "")
    return message


def _known_continuous_raw_pair(core: Any, pair: tuple[str, str]) -> bool:
    device, prop = pair
    key = prop.lower().replace("_", "").replace(" ", "")
    focus = str(core.get_focus_device() or "")
    xy = str(core.get_xy_stage_device() or "")
    camera = str(core.get_camera_device() or "")
    return (
        (device == focus and key == "position")
        or (device == xy and key in {"x", "y", "xposition", "yposition"})
        or (device == camera and key == "exposure")
    )


def _property_type_name(core: Any, device: str, prop: str) -> str:
    raw = core.get_property_type(device, prop)
    if hasattr(raw, "to_string"):
        name = str(raw.to_string())
        if name in {"Float", "Integer", "String", "Undef"}:
            return name
    if hasattr(raw, "swig_value"):
        try:
            return {0: "Undef", 1: "String", 2: "Float", 3: "Integer"}.get(int(raw.swig_value()), "Unknown")
        except (TypeError, ValueError):
            pass
    return str(raw)


def _continuous_introspection(core: Any, device: str, prop: str) -> bool:
    """Conservative refusal signal only; never an authorization source."""
    kind = device_type_name(core, device)
    if kind not in {
        "StageDevice", "XYStageDevice", "CameraDevice", "GalvoDevice",
        "SignalIODevice", "GenericDevice",
    }:
        return False
    if bool(core.is_property_pre_init(device, prop)):
        return False
    if bool(core.is_property_read_only(device, prop)):
        return False
    if _strings(core.get_allowed_property_values(device, prop)):
        return False
    return _property_type_name(core, device, prop) in {"Float", "Integer"}


def _validate_typed_live(
    core: Any, identity: TypedActuatorId, policy: TypedActuatorPolicy,
    loaded_devices: Iterable[str],
) -> list[str]:
    pair = f"{identity.device}.{identity.property}"
    errors: list[str] = []
    try:
        devices = set(loaded_devices)
        if identity.device not in devices:
            return [f"Typed actuator {pair} names a device that is not connected."]
        properties = set(_strings(core.get_device_property_names(identity.device)))
        if identity.property not in properties:
            return [f"Typed actuator {pair} names a property that does not exist on the live device."]
        if bool(core.is_property_read_only(identity.device, identity.property)):
            errors.append(f"Typed actuator {pair} is read-only on the live device.")
        allowed = _strings(core.get_allowed_property_values(identity.device, identity.property))
        if allowed:
            errors.append(f"Typed actuator {pair} is enumerated ({allowed!r}), not continuous numeric.")
        reported = _property_type_name(core, identity.device, identity.property)
        if reported not in {"Float", "Integer"}:
            errors.append(f"Typed actuator {pair} is not numeric (driver reports {reported}).")
        if bool(core.has_property_limits(identity.device, identity.property)):
            lower = float(core.get_property_lower_limit(identity.device, identity.property))
            upper = float(core.get_property_upper_limit(identity.device, identity.property))
            raw_min, raw_max = policy.minimum, policy.maximum
            if policy.kind == "illumination-power" and policy.units == "native":
                assert policy.full_scale is not None
                raw_min = policy.minimum * policy.full_scale / 100.0
                raw_max = policy.maximum * policy.full_scale / 100.0
            if raw_min < lower or raw_max > upper:
                errors.append(
                    f"Typed actuator {pair} declares a safe bound mapping to raw {raw_min:g}..{raw_max:g}, "
                    f"outside the driver-reported technical range {lower:g}..{upper:g}; technical ranges are only an outer sanity check, never inferred safe limits."
                )
    except Exception as exc:
        errors.append(f"Could not validate typed actuator {pair} by live introspection: {_clean_exception_message(exc)}")
    return errors


def _stage_identity(source: str, device: str | None, axis: str | None) -> ActuatorId:
    return ActuatorId(source, device, "stage-position", axis)


# mmcorej.DeviceType ordinals (5/6 verified over the bridge in the design/14
# V3 spike; 2/3/4 are the same published enum). This is the single source of
# truth for the table — microclaw.tools._device_type_name delegates here.
#
# Classifying one device at a time deliberately avoids
# get_loaded_devices_of_type, which needs a DeviceType *static* enum shadow and
# therefore the JavaClass cache workaround (CLAUDE.md); per-device
# get_device_type needs no JavaClass at all.
DEVICE_TYPE_NAMES = {
    2: "CameraDevice",
    3: "ShutterDevice",
    4: "StateDevice",
    5: "StageDevice",
    6: "XYStageDevice",
    7: "SerialDevice",
    8: "GenericDevice",
    9: "AutoFocusDevice",
    10: "CoreDevice",
    11: "ImageProcessorDevice",
    12: "SignalIODevice",
    13: "MagnifierDevice",
    14: "SLMDevice",
    15: "HubDevice",
    16: "GalvoDevice",
}

# A StateDevice's discrete position is exposed as exactly these two MM
# properties. Auto-classification admits these and nothing else on the device.
STATE_DEVICE_POSITION_PROPERTIES = ("Label", "State")

AUTO_STATE_DEVICE_SOURCE = "auto:state-device"
_AUTO_STATE_DEVICE_DETAIL = (
    "auto-classified: MM StateDevice discrete position; no declaration required"
)


def device_type_name(core: Any, label: str) -> str:
    """Classify one device via core.get_device_type(label).

    Returns the mmcorej DeviceType name when it can be resolved, otherwise the
    raw string. Raises whatever the bridge raises; callers that must fail
    closed catch it.
    """
    raw = core.get_device_type(label)
    if hasattr(raw, "to_string"):
        name = str(raw.to_string())
        if name and "0x" not in name:
            return name
    if hasattr(raw, "swig_value"):
        try:
            ordinal = int(raw.swig_value())
        except (TypeError, ValueError):
            pass
        else:
            return DEVICE_TYPE_NAMES.get(ordinal, str(ordinal))
    try:
        return DEVICE_TYPE_NAMES.get(int(raw), str(raw))
    except (TypeError, ValueError):
        return str(raw)


def _position_ruled_devices(parsed_config: ParsedSafetyConfig) -> set[str]:
    """Devices whose discrete position the operator has already ruled on.

    M5 rig finding (2026-07-23): the config declared only
    `iChrome-MLE-TCP.Label` — with a comment saying the operator was unsure
    whether the driver's write property was Label or State — and
    auto-classification handed them `State` on a laser engine for free. That
    inverts the intent: declaring one position property is a narrowing, not an
    invitation.

    So a ruling on EITHER Label or State (categorical, excluded, or the
    forbidden_properties denylist) takes the whole discrete position off the
    table for that device. Auto-classification fills vacuums only. The rule is
    scoped to the position pair, not the device: excluding, say, `Wheel.Speed`
    must not silently kill the wheel's auto-classification.
    """
    profile = parsed_config.rig_profile
    ruled = (
        set(profile.categorical_properties)
        | set(profile.excluded_properties)
        | {
            (item.device, item.property)
            for item in parsed_config.constraints.forbidden_properties
        }
    )
    return {
        device
        for device, prop in ruled
        if prop in STATE_DEVICE_POSITION_PROPERTIES
    }


def _auto_classified_state_pairs(
    core: Any,
    parsed_config: ParsedSafetyConfig,
    loaded_devices: Iterable[str],
    illumination_pairs: set[tuple[str, str]],
) -> set[tuple[str, str]]:
    """StateDevice position pairs admitted without an explicit declaration.

    Fails closed everywhere: an unreadable device type, an unreadable property
    list, or an unreadable Core shutter leaves the device excluded exactly as
    it is today. Shutter-ness comes only from the MM device type, Core.Shutter,
    and the reviewed illumination config — never from the device's name.

    NOTE (M5, 2026-07-23): that rig has NO core shutter, so `Core.Shutter`
    protects nothing there and the whole shutter carve-out rests on the
    `illumination:` block being complete.
    """
    try:
        core_shutter = str(core.get_shutter_device() or "")
    except Exception:
        # Without Core.Shutter the shutter carve-out cannot be applied, so no
        # device may be auto-classified.
        return set()
    illumination_devices = {device for device, _ in illumination_pairs}
    ruled_devices = _position_ruled_devices(parsed_config)
    admitted: set[tuple[str, str]] = set()
    for device in sorted({str(d) for d in loaded_devices if d}):
        # Shutter carve-out: the Core shutter and anything the operator
        # reviewed as illumination stay on the illumination gate.
        if device == core_shutter or device in illumination_devices:
            continue
        if device in ruled_devices:     # operator already ruled on this position
            continue
        try:
            kind = device_type_name(core, device)
        except Exception:
            continue
        if kind != "StateDevice":      # ShutterDevice and everything else: no
            continue
        try:
            properties = set(_strings(core.get_device_property_names(device)))
        except Exception:
            continue
        for prop in STATE_DEVICE_POSITION_PROPERTIES:
            pair = (device, prop)
            if prop not in properties:
                continue
            if pair in illumination_pairs or _known_continuous_raw_pair(core, pair):
                continue
            admitted.add(pair)
    return admitted


def validate_live_rig(
    ctrl: Any, parsed_config: ParsedSafetyConfig, guard: Any = None
) -> AuthorizationMap:
    """Build and attach the effective map before any mutation surface is exposed.

    `guard` is the SafetyGuard that will police the session. When given, the
    pairs auto-classified here (StateDevice discrete positions) are handed to
    it, because a raw write passes through both the map and the guard's
    categorical allowlist. Omit it for read-only enumeration.
    """
    core = ctrl.core
    profile = parsed_config.rig_profile
    guaranteed = profile.mode == "guaranteed"
    if not guaranteed:
        print(
            "\n!! DEGRADED TRUSTED-PLUGIN MODE: the authorization-map "
            "completeness guarantee is suspended. !!\n",
            file=sys.stderr,
        )
    errors: list[str] = []
    demotions: list[ConfigDiagnostic] = []
    demoted_property_pairs: set[tuple[str, str]] = set()
    entries: list[AuthorizationEntry] = []

    if "stage-position" not in BUILTIN_TYPED_CAPABILITIES:
        errors.append(
            "Code registry is missing the built-in stage-position capability; no "
            "safety-config declaration can repair this installation error."
        )
    if "exposure" not in BUILTIN_TYPED_CAPABILITIES:
        errors.append(
            "Code registry is missing the built-in exposure capability; no "
            "safety-config declaration can repair this installation error."
        )
    if "illumination" not in BUILTIN_TYPED_CAPABILITIES:
        errors.append(
            "Code registry is missing the built-in illumination capability; no "
            "safety-config declaration can repair this installation error."
        )
    if "acquisition-dose" not in BUILTIN_TYPED_CAPABILITIES:
        errors.append(
            "Code registry is missing the built-in acquisition-dose capability; no "
            "safety-config declaration can repair this installation error."
        )

    xy_device = str(core.get_xy_stage_device() or "")
    focus_device = str(core.get_focus_device() or "")
    camera_device = str(core.get_camera_device() or "")
    named_devices = {
        identity.device
        for identity in parsed_config.ranges
        if identity.source == "named" and identity.device is not None
    }
    conflicts = sorted(named_devices & {d for d in (xy_device, focus_device) if d})
    if conflicts:
        errors.append(
            "Core/named actuator declaration conflict for live device(s): "
            + ", ".join(conflicts)
            + ". Remove each duplicate item from top-level `named_stages`; Core XY/focus "
            "devices are permitted by the matching `stage.x_*`, `stage.y_*`, or "
            "`stage.z_*` edges."
        )

    reachable_axes = []
    if xy_device:
        reachable_axes.extend([
            (_stage_identity("core_xy", None, "x"), xy_device),
            (_stage_identity("core_xy", None, "y"), xy_device),
        ])
    if focus_device:
        reachable_axes.append((_stage_identity("core_focus", None, "z"), focus_device))
    for device in sorted(named_devices):
        reachable_axes.append((_stage_identity("named", device, None), device))

    for identity, live_device in reachable_axes:
        policy = parsed_config.ranges.get(identity)
        if policy is None:
            if guaranteed:
                location = (
                    f"an item for device {live_device!r} under top-level `named_stages`"
                    if identity.source == "named"
                    else f"both `stage.{identity.axis}_min` and `stage.{identity.axis}_max`"
                )
                errors.append(
                    f"Reachable {identity.axis or 'z'} stage actuator {live_device!r} "
                    f"has no declared range policy. The declaration that permits it is {location}."
                )
            continue
        if guaranteed and (
            policy.minimum.bound is None or policy.maximum.bound is None
        ):
            location = (
                f"the `min_um` and `max_um` keys for device {live_device!r} "
                "under top-level `named_stages`"
                if identity.source == "named"
                else (
                    f"`stage.{identity.axis}_min` and `stage.{identity.axis}_max` "
                    "at the top level"
                )
            )
            errors.append(
                f"Reachable stage actuator {live_device!r} axis "
                f"{identity.axis or 'z'} has an open range edge in guaranteed mode. "
                f"Set finite reviewed bounds in {location}."
            )
        entries.append(AuthorizationEntry(
            path="dedicated-stage",
            classification="built_in_typed_capability",
            device=live_device,
            capability="stage-position",
            axis=identity.axis,
        ))

    if camera_device:
        if guaranteed and parsed_config.constraints.camera.max_exposure_ms is None:
            errors.append(
                f"Reachable camera {camera_device!r} has no finite exposure maximum. "
                "Set `camera.max_exposure_ms` in the top-level `camera` section to "
                "this rig's reviewed finite positive limit."
            )
        entries.append(AuthorizationEntry(
            path="dedicated-exposure",
            classification="built_in_typed_capability",
            device=camera_device,
            capability="exposure",
        ))

    illumination = parsed_config.constraints.illumination
    illumination_power_pairs = {
        (item.device, item.property) for item in illumination.power_properties
    }
    illumination_pairs = {
        (item.device, item.property) for item in illumination.shutters
    } | illumination_power_pairs
    illumination_shutter_pairs = {
        (item.device, item.property) for item in illumination.shutters
    }

    # Device inventory is read once here: auto-classification needs it before
    # the categorical entries are emitted. The enumeration error is still
    # reported at its original position below, so failure ordering is unchanged.
    try:
        loaded_devices = _strings(core.get_loaded_devices())
        loaded_devices_error = None
    except Exception as exc:
        loaded_devices = []
        loaded_devices_error = f"Could not enumerate connected devices: {exc}"

    emu_enables, emu_discovery_problems = _live_emu_laser_enables(
        ctrl, loaded_devices
    )
    emu_warnings = list(emu_discovery_problems)
    for slot, device, prop in emu_enables:
        if (device, prop) in illumination_shutter_pairs:
            continue
        emu_warnings.append(
            f"EMU laser slot {slot} semantically identifies exact enable "
            f"{device}.{prop}, but it is missing from "
            "constraints.illumination.shutters. Add the verified declaration using:\n"
            "  illumination:\n"
            "    shutters:\n"
            f"      - device: {device!r}\n"
            f"        property: {prop!r}\n"
            "Establish and declare on_value/off_value too if this hardware does not "
            "use the schema defaults; no values were inferred from the semantic map."
        )
    if guaranteed:
        errors.extend(emu_warnings)
    else:
        for warning in emu_warnings:
            print(
                "WARNING: " + warning + " Completeness guarantee is suspended in "
                "degraded_trusted_plugins mode; unresolved or undeclared EMU enables "
                "are not silently authorized as illumination.",
                file=sys.stderr,
            )

    typed_pairs = {(identity.device, identity.property) for identity in parsed_config.typed_actuators}
    denied_pairs = {
        (item.device, item.property)
        for item in parsed_config.constraints.forbidden_properties
    } | set(profile.excluded_properties)
    for identity, policy in sorted(
        parsed_config.typed_actuators.items(), key=lambda item: (item[0].device, item[0].property)
    ):
        errors.extend(_validate_typed_live(core, identity, policy, loaded_devices))
        pair = (identity.device, identity.property)
        if policy.kind == "bounded-numeric" and (
            pair in illumination_pairs or _known_continuous_raw_pair(core, pair)
        ):
            errors.append(
                f"Typed bounded-numeric {identity.device}.{identity.property} aliases a built-in "
                "stage, exposure, or illumination capability and cannot bypass its dedicated safety gate."
            )
        if pair in denied_pairs:
            errors.append(
                f"Raw property {identity.device}.{identity.property} cannot be both "
                "typed-continuous and explicitly excluded."
            )
        if policy.kind == "absolute-position":
            axis_policies: list[tuple[str, Any]] = []
            if identity.device == focus_device:
                axis_policies.append(("core focus z", parsed_config.ranges.get(
                    _stage_identity("core_focus", None, "z")
                )))
            if identity.device == xy_device:
                axis_policies.extend([
                    ("core XY x", parsed_config.ranges.get(_stage_identity("core_xy", None, "x"))),
                    ("core XY y", parsed_config.ranges.get(_stage_identity("core_xy", None, "y"))),
                ])
            if identity.device in named_devices:
                axis_policies.append((f"named stage {identity.device}", parsed_config.ranges.get(
                    _stage_identity("named", identity.device, None)
                )))
            for axis_name, axis_policy in axis_policies:
                if axis_policy is None:
                    continue
                lower, upper = axis_policy.minimum.bound, axis_policy.maximum.bound
                if ((lower is not None and policy.minimum < lower)
                        or (upper is not None and policy.maximum > upper)):
                    errors.append(
                        f"Typed absolute-position {identity.device}.{identity.property} bounds "
                        f"{policy.minimum:g}..{policy.maximum:g} um widen the declared {axis_name} "
                        f"bounds {lower!r}..{upper!r} um; a typed axis entry may only narrow them."
                    )
        if policy.kind == "illumination-power":
            declared_power = next((item for item in illumination.power_properties
                                   if (item.device, item.property) == (identity.device, identity.property)), None)
            if declared_power is None:
                errors.append(
                    f"Typed illumination-power {identity.device}.{identity.property} must also appear in "
                    "illumination.power_properties so the percent cap and per-write ratchet remain active."
                )
            elif ((getattr(declared_power, "units", None) or "percent") != policy.units
                  or getattr(declared_power, "full_scale", None) != policy.full_scale):
                errors.append(
                    f"Typed illumination-power {identity.device}.{identity.property} units/full_scale disagree with "
                    "illumination.power_properties; declare the same raw representation in both places."
                )
        canonical_unit = policy.units if policy.kind == "bounded-numeric" else (
            "um" if policy.kind == "absolute-position" else "percent"
        )
        conversion = (
            f"raw native/full_scale {policy.full_scale:g} -> percent"
            if policy.kind == "illumination-power" and policy.units == "native"
            else f"raw {policy.units} -> {canonical_unit} identity"
        )
        entries.append(AuthorizationEntry(
            path="generic-property",
            classification="typed_continuous_actuator",
            device=identity.device,
            property=identity.property,
            capability=policy.kind,
            detail=(f"units={policy.units}; {conversion}; effective canonical bound "
                    f"{policy.minimum:g}..{policy.maximum:g} {canonical_unit}"),
            source="declared",
        ))
    admitted_categorical_pairs: set[tuple[str, str]] = set()
    loaded_device_set = set(loaded_devices)
    for device, prop in sorted(profile.categorical_properties):
        if (device, prop) in typed_pairs:
            errors.append(f"Raw property {device}.{prop} cannot be both typed-continuous and categorical.")
        absent_reason = None
        if loaded_devices_error is None and device not in loaded_device_set:
            absent_reason = f"declared device {device!r} is not loaded"
        elif loaded_devices_error is None:
            try:
                if prop not in _strings(core.get_device_property_names(device)):
                    absent_reason = f"property {device}.{prop} is not present"
            except Exception as exc:
                if guaranteed:
                    errors.append(
                        f"Could not introspect declared categorical property "
                        f"{device}.{prop}: {_clean_exception_message(exc)}"
                    )
        if absent_reason is not None:
            demoted_property_pairs.add((device, prop))
            demotions.append(ConfigDiagnostic(
                "live_check",
                f"Categorical claim for {device}.{prop} was dropped because the "
                f"{absent_reason}; it is not authorized for writes. Load the exact "
                "device/property in Micro-Manager, or remove this exact entry from "
                "`rig_profile.categorical_properties`.",
                False,
            ))
            entries.append(AuthorizationEntry(
                path="generic-property", classification="excluded", device=device,
                property=prop, detail=absent_reason, source="declared",
            ))
            continue
        try:
            continuous = (_known_continuous_raw_pair(core, (device, prop))
                          or _continuous_introspection(core, device, prop))
        except Exception as exc:
            continuous = False
            # Degraded mode deliberately retains its existing best-effort
            # admission; only guaranteed mode promises a complete map.
            if guaranteed:
                errors.append(
                    f"Could not introspect declared categorical property "
                    f"{device}.{prop}: {_clean_exception_message(exc)}"
                )
        if continuous:
            demoted_property_pairs.add((device, prop))
            demotions.append(ConfigDiagnostic(
                "live_check",
                f"Raw property {device}.{prop} is a known continuous actuator (confirmed by the live rig) and cannot be classified as categorical; "
                "declare it in rig_profile.typed_actuators with exact semantics, units, and safe canonical bounds, or place it in "
                "rig_profile.excluded_properties if it is intentionally unavailable for writes. The categorical claim was dropped and this property is not authorized for writes.",
                False,
            ))
        entries.append(AuthorizationEntry(
            path="generic-property",
            classification=("excluded" if continuous else "reviewed_categorical_property"),
            device=device,
            property=prop,
            detail=("declared categorical claim demoted because the live rig proves this property is continuous" if continuous else None),
            source="declared",
        ))
        if not continuous:
            admitted_categorical_pairs.add((device, prop))

    # Additive to the declared pairs above: an MM StateDevice (filter wheel,
    # slider, turret) needs no declaration for its own discrete position —
    # unless the operator has already ruled on that device's position, in which
    # case their declaration stands alone (see _position_ruled_devices).
    auto_pairs = _auto_classified_state_pairs(
        core, parsed_config, loaded_devices, illumination_pairs
    )
    for device, prop in sorted(auto_pairs):
        entries.append(AuthorizationEntry(
            path="generic-property",
            classification="reviewed_categorical_property",
            device=device,
            property=prop,
            detail=_AUTO_STATE_DEVICE_DETAIL,
            source=AUTO_STATE_DEVICE_SOURCE,
        ))
    categorical_pairs = admitted_categorical_pairs | auto_pairs

    for device, prop in sorted(illumination_pairs):
        entries.append(AuthorizationEntry(
            path="dedicated-illumination",
            classification="built_in_typed_capability",
            device=device,
            property=prop,
            capability="illumination",
        ))
    if guaranteed and illumination_power_pairs:
        maximum = illumination.max_power_percent
        step_factor = illumination.max_power_step_factor
        if maximum is None:
            errors.append(
                "Reachable illumination power requires a finite "
                "illumination.max_power_percent in guaranteed mode."
            )
        elif not math.isfinite(maximum) or not 0 <= maximum <= 100:
            errors.append(
                "illumination.max_power_percent must be within 0..100 "
                "for reachable illumination power."
            )
        if step_factor is None:
            errors.append(
                "Reachable illumination power requires a finite "
                "illumination.max_power_step_factor in guaranteed mode."
            )
        elif not math.isfinite(step_factor) or step_factor < 1:
            errors.append(
                "illumination.max_power_step_factor must be at least 1."
            )
        for item in illumination.power_properties:
            if getattr(item, "units", None) in (None, "percent"):
                try:
                    has_limits = bool(core.has_property_limits(item.device, item.property))
                    lower = float(core.get_property_lower_limit(item.device, item.property)) if has_limits else None
                    upper = float(core.get_property_upper_limit(item.device, item.property)) if has_limits else None
                except Exception:
                    lower = upper = None
                if lower is not None and upper is not None and (lower, upper) != (0.0, 100.0):
                    errors.append(
                        f"Illumination power {item.device}.{item.property} declares "
                        f"{('no units' if getattr(item, 'units', None) is None else 'units: percent')} and its driver technical range is "
                        f"{lower!r}..{upper!r}, not 0..100 percent. The existing max_power_percent cap may be inoperative; "
                        "declare units: native and full_scale equal to the measured native full scale (M5 Power (mW): 75.0), or select a real percent property."
                    )
    for device, prop in sorted(profile.excluded_properties):
        if _known_continuous_raw_pair(core, (device, prop)):
            errors.append(
                f"Excluded property {device}.{prop} aliases a built-in motion/exposure "
                "path that Phase 1 cannot remove independently from every dedicated, "
                "autofocus, and acquisition path."
            )
        absent_reason = None
        if loaded_devices_error is None and device not in loaded_device_set:
            absent_reason = f"declared device {device!r} is not loaded"
        elif loaded_devices_error is None:
            try:
                if prop not in _strings(core.get_device_property_names(device)):
                    absent_reason = f"property {device}.{prop} is not present"
            except Exception:
                # The exclusion remains enforced without widening authority.
                pass
        if absent_reason is not None:
            demotions.append(ConfigDiagnostic(
                "live_check",
                f"Excluded-property claim for {device}.{prop} was not corroborated "
                f"because the {absent_reason}. The property remains unauthorized. "
                "Correct the exact device/property name, or remove this exact entry "
                "from `rig_profile.excluded_properties`.",
                False,
            ))
        entries.append(AuthorizationEntry(
            path="all-property-paths",
            classification="excluded",
            device=device,
            property=prop,
            detail=absent_reason,
        ))

    allowed_channels = parsed_config.constraints.allowed_channels
    try:
        available_presets = _strings(core.get_available_configs(CHANNEL_CONFIG_GROUP))
    except Exception as exc:
        available_presets = None
        errors.append(
            "Could not enumerate presets in the \"Channel\" group: "
            + _clean_exception_message(exc)
        )
    if allowed_channels is None:
        presets = available_presets or []
    elif available_presets is None:
        presets = []
    else:
        available = set(available_presets)
        missing = [preset for preset in allowed_channels if preset not in available]
        if missing:
            demotions.append(ConfigDiagnostic(
                "live_check",
                "channels.allowed lists preset(s) not present in the \"Channel\" "
                "group: " + ", ".join(repr(preset) for preset in missing)
                + ". The missing preset claim(s) were dropped and are not authorized. Add each preset to Micro-Manager's Channel group, or remove its exact name from top-level `channels.allowed`.",
                False,
            ))
        presets = [preset for preset in allowed_channels if preset in available]
    authorized_presets: set[str] = set()
    excluded_presets: dict[str, list[str]] = {
        preset: [
            "preset is absent from Micro-Manager's Channel group; add it there or remove its exact name from top-level `channels.allowed`"
        ]
        for preset in (missing if allowed_channels is not None and available_presets is not None else [])
    }
    channel_expansion_hashes: dict[str, str] = {}
    for preset in presets:
        reasons = []
        try:
            effects = _expand_preset(core, preset)
            channel_expansion_hashes[preset] = _expansion_hash(effects)
        except Exception as exc:
            effects = []
            reasons.append(_clean_exception_message(exc))
        for device, prop, value in effects:
            pair = (device, prop)
            if pair in profile.excluded_properties:
                classification = "excluded"
                reasons.append(f"{device}.{prop} is excluded")
            elif device == "Core":
                if prop == "Shutter" and (
                    guard.is_illumination_shutter_device(value)
                    if guard is not None else value in {
                        item.device for item in illumination.shutters
                    }
                ):
                    classification = "built_in_typed_capability"
                else:
                    classification = "excluded"
                    reasons.append(f"{device}.{prop} core-device retarget is excluded")
            elif pair in typed_pairs:
                classification = "typed_continuous_actuator"
            elif pair in illumination_pairs:
                classification = "built_in_typed_capability"
            elif _known_continuous_raw_pair(core, pair):
                classification = "built_in_typed_capability"
            elif pair in categorical_pairs:
                # Auto-classified StateDevice positions count here too: a
                # preset that only moves filter wheels must not need the
                # declaration the wheel itself no longer needs.
                classification = "reviewed_categorical_property"
            else:
                classification = "excluded"
                reasons.append(f"{device}.{prop} is unclassified")
            entries.append(AuthorizationEntry(
                path=f"channel-preset:{preset}",
                classification=classification,
                device=device,
                property=prop,
                detail=None if value is None else f"value={value!r}",
                source=(
                    AUTO_STATE_DEVICE_SOURCE
                    if pair in auto_pairs
                    and classification == "reviewed_categorical_property"
                    else "declared"
                    if classification == "reviewed_categorical_property"
                    else None
                ),
            ))
        if reasons:
            excluded_presets[preset] = reasons
            if guaranteed:
                errors.append(
                    f"Allowed channel preset {preset!r} is not fully classified: "
                    + "; ".join(reasons)
                )
        else:
            authorized_presets.add(preset)

    if focus_device:
        entries.append(AuthorizationEntry(
            path="autofocus-and-acquisition",
            classification="built_in_typed_capability",
            device=focus_device,
            capability="stage-position",
            axis="z",
        ))
    acquisition = parsed_config.constraints.acquisition
    acquisition_policy_complete = True
    for field_name, policy_detail in _ACQUISITION_POLICY_FIELDS:
        value = getattr(acquisition, field_name)
        valid = (
            not isinstance(value, bool)
            and isinstance(value, Real)
            and math.isfinite(value)
            and value > 0
        )
        acquisition_policy_complete = acquisition_policy_complete and valid
        if guaranteed and not valid:
            errors.append(
                f"Acquisition authorization requires finite positive "
                f"acquisition.{field_name}; got {value!r}. Set that key in the "
                "top-level `acquisition` section to this rig's reviewed budget."
            )
        entries.append(AuthorizationEntry(
            path=f"acquisition-policy:{field_name}",
            classification=(
                "built_in_typed_capability" if valid else "trusted_degraded"
            ),
            device=camera_device or None,
            capability="acquisition-dose",
            detail=f"{policy_detail}; configured={value!r}",
        ))

    for tool_name in _acquisition_tool_names():
        if tool_name == "run_mda":
            continue
        entries.append(AuthorizationEntry(
            path=f"acquisition-tool:{tool_name}",
            classification=(
                "built_in_typed_capability"
                if acquisition_policy_complete else "trusted_degraded"
            ),
            device=camera_device or None,
            capability="acquisition-dose",
            detail=(
                "plans and reserves before hardware effects"
                if acquisition_policy_complete
                else (
                    "planner and ledger remain active, but the complete typed dose "
                    "policy is unavailable; global completeness is suspended"
                )
            ),
        ))
    entries.extend([
        AuthorizationEntry(
            path="camera-roi",
            classification="excluded",
            device=camera_device or None,
            detail="no Phase-1 typed capability or reviewed property identity",
        ),
        AuthorizationEntry(
            path="mmstudio-mda",
            classification="excluded",
            detail=(
                "run_mda remains excluded even though its preview is dose-planned; "
                "GUI-owned effects cannot be enumerated as an immutable guarded plan"
            ),
        ),
    ])

    if parsed_config.constraints.plugins.allow_hardware_motion:
        entries.append(AuthorizationEntry(
            path="opaque-hardware-motion-plugin",
            classification="excluded" if guaranteed else "trusted_degraded",
            detail="effects cannot be enumerated or intercepted",
        ))
        if guaranteed:
            errors.append(
                "Opaque hardware-motion plugins are forbidden in guaranteed mode."
            )

    if loaded_devices_error is not None and guaranteed:
        errors.append(loaded_devices_error)
    devices_with_entries = {entry.device for entry in entries if entry.device}
    for device in sorted(set(loaded_devices) - devices_with_entries):
        entries.append(AuthorizationEntry(
            path="connected-device-inventory",
            classification="excluded",
            device=device,
            detail=(
                "no dedicated typed path; raw writes are allowlisted, presets were "
                "expanded, and named-stage motion fails closed"
                + (
                    "; opaque motion plugins are unavailable"
                    if guaranteed else "; completeness is suspended for trusted plugins"
                )
            ),
        ))

    if errors:
        # Severity taxonomy: every legacy validator error is a process refusal.
        # Each represents missing proof/completeness or an authority conflict;
        # ignoring it would widen authority. Narrowing claim mismatches are
        # created explicitly above as non-blocking live_check diagnostics.
        blocking_diagnostics = tuple(
            ConfigDiagnostic("live_check", message, True)
            for message in errors
        )
        raise RigAuthorizationError(
            "Live rig authorization failed:\n- " + "\n- ".join(errors),
            (*blocking_diagnostics, *demotions),
        )

    if demotions:
        print("\n!! AUTHORIZATION CLAIMS DEMOTED — STARTUP CONTINUES WITH LESS AUTHORITY !!", file=sys.stderr)
        for diagnostic in demotions:
            print(f"- {diagnostic.message}", file=sys.stderr)
        print("!! END DEMOTED AUTHORIZATION CLAIMS !!\n", file=sys.stderr)

    # A raw write passes two gates: this map and SafetyGuard.check_property's
    # categorical allowlist (built from the declared pairs at config-parse
    # time). Retract live-demoted declarations before handing over exactly the
    # auto-classified additions, so both gates independently refuse a demoted
    # pair and admit an accepted one.
    if guard is not None:
        guard.deny_demoted_properties(demoted_property_pairs)
    if guard is not None and auto_pairs:
        guard.admit_auto_classified(auto_pairs)
    if guard is not None:
        guard.admit_typed_actuators(parsed_config.typed_actuators)

    report = AuthorizationMap(
        mode=profile.mode,
        verdict=(
            "complete"
            if guaranteed
            else "DEGRADED: authorization-map completeness guarantee is suspended"
        ),
        complete=True if guaranteed else None,
        entries=entries,
        excluded_presets=excluded_presets,
        authorized_presets=frozenset(authorized_presets),
        channel_expansion_hashes=channel_expansion_hashes,
        diagnostics=tuple(demotions),
    )
    ctrl.authorization_map = report
    return report


def authorize_property_write(ctrl: Any, device: str, prop: str) -> None:
    """Enforce the attached reviewed/excluded decision on any raw write path."""
    report = getattr(ctrl, "authorization_map", None)
    if report is None:
        # Invariant: startup attaches this before exposing production mutation paths.
        return
    matches = [
        entry for entry in report.entries
        if entry.device == device and entry.property == prop
        # Preset entries authorize only the captured channel-plan route. A raw
        # write must have its own reachable map entry so the map and guard
        # remain independent gates.
        and entry.path in {
            "generic-property", "dedicated-illumination", "all-property-paths"
        }
    ]
    admitted = {
        "reviewed_categorical_property", "built_in_typed_capability",
        "typed_continuous_actuator",
    }
    if (
        not matches
        or any(entry.classification == "excluded" for entry in matches)
        or not any(entry.classification in admitted for entry in matches)
    ):
        raise RigAuthorizationError(
            f"Property write {device}.{prop} was refused because it is excluded from "
            "the authorization map. No legal declaration can be named from this runtime "
            "refusal alone: an explicitly excluded property must stay unavailable until "
            "its exclusion is deliberately removed, and an unclassified property needs "
            "its hardware semantics established first. Then declare this exact pair in "
            "the matching top-level list: `rig_profile.categorical_properties`, "
            "`rig_profile.typed_actuators`, `illumination.shutters`, or "
            "`illumination.power_properties`."
        )


def authorize_channel(ctrl: Any, preset: str) -> None:
    report = getattr(ctrl, "authorization_map", None)
    if report is not None and preset not in report.authorized_presets:
        reasons = report.excluded_presets.get(preset, ["preset was not authorized at startup"])
        raise RigAuthorizationError(
            f"Channel preset {preset!r} was refused: {'; '.join(reasons)}. The preset "
            "itself must appear under top-level `channels.allowed`, and every expanded "
            "effect must be permitted where its kind is declared: "
            "`rig_profile.categorical_properties`, `rig_profile.typed_actuators`, "
            "`illumination.shutters`, or `illumination.power_properties`. An explicitly "
            "excluded or unclassifiable effect has no legal declaration until that "
            "exclusion is removed or its hardware semantics are established."
        )


def _cancelled(cancel: Any) -> bool:
    if cancel is None:
        return False
    check = getattr(cancel, "is_set", None)
    if callable(check):
        return bool(check())
    if callable(cancel):
        return bool(cancel())
    return bool(cancel)


def _verify_property(core: Any, device: str, prop: str, expected: str) -> None:
    actual = str(core.get_property(device, prop))
    if _property_type_name(core, device, prop) == "Float":
        try:
            equal = math.isclose(float(actual), float(expected), rel_tol=1e-9, abs_tol=1e-9)
        except (TypeError, ValueError):
            equal = False
    else:
        equal = actual == str(expected)
    if not equal:
        raise ChannelPlanError(
            f"Read-back verification failed for {device}.{prop}: requested {expected!r}, got {actual!r}."
        )


def _wait_for_plan_device(core: Any, device: str) -> None:
    # Core is MM's pseudo-device, not an adapter that can become busy. The
    # measured replay surface has no Core wait; every real device is awaited.
    if device != "Core":
        core.wait_for_device(device)


def _authorize_channel_effect(
    ctrl: Any, guard: Any, device: str, prop: str, value: str, confirm_fn: Any
) -> None:
    """Route one captured effect through the same exact-pair policies as raw writes."""
    core = ctrl.core
    if device == "Core":
        if prop != "Shutter":
            raise RigAuthorizationError(
                f"Channel effect Core.{prop} was refused. Only Core.Shutter retargeting "
                "has a legal channel-effect declaration; no safety-config declaration "
                f"can permit Core.{prop}. Remove that effect from the Micro-Manager preset."
            )
        if not guard.is_illumination_shutter_device(value):
            raise RigAuthorizationError(
                f"Core.Shutter retarget to {value!r} was refused. Declare the target "
                "device's exact device/property/on_value/off_value mapping as an item "
                "under top-level `illumination.shutters`; Core.Shutter selects that "
                "declared device and is not itself the item to add."
            )
        if confirm_fn is None or not confirm_fn(
            f"SELECT ILLUMINATION SHUTTER: Core.Shutter = {value!r}\n"
            "This selects which declared light source AutoShutter may fire on the next exposure.",
            kind="illumination",
        ):
            raise RigAuthorizationError(
                f"Core.Shutter retarget to {value!r} was refused because the operator "
                "declined it. No safety-config declaration overrides an operator decline; "
                "request the action again only if the operator intends to approve it."
            )
        return

    pair = (device, prop)
    report = getattr(ctrl, "authorization_map", None)
    matches = [] if report is None else [
        entry for entry in report.entries
        if entry.device == device and entry.property == prop
    ]
    if report is not None and (
        not matches or any(entry.classification == "excluded" for entry in matches)
    ):
        raise RigAuthorizationError(
            f"Channel effect {device}.{prop} was refused because it is unclassified or "
            "excluded. A discrete non-illumination effect goes under top-level "
            "`rig_profile.categorical_properties`; a bounded continuous actuator goes "
            "under `rig_profile.typed_actuators`; illumination goes under "
            "`illumination.shutters` or `illumination.power_properties`. If the property "
            "is explicitly excluded or its actuator kind is not established, no legal "
            "declaration can permit it yet."
        )

    if guard.is_illumination_enable(device, prop) or guard.is_illumination_power(device, prop):
        guard.check_illumination(core, device, prop, value, confirm_fn=confirm_fn)
    elif guard.is_typed_actuator(device, prop):
        guard.check_device_property(core, device, prop, value)
    elif _known_continuous_raw_pair(core, pair):
        key = prop.lower().replace("_", "").replace(" ", "")
        if device == str(core.get_camera_device() or "") and key == "exposure":
            guard.check_exposure(float(value))
        elif device == str(core.get_focus_device() or ""):
            guard.check_z(float(value))
        elif device == str(core.get_xy_stage_device() or ""):
            number = float(value)
            guard.check_xy(
                number if key.startswith("x") else core.get_x_position(),
                number if key.startswith("y") else core.get_y_position(),
            )
        else:  # Defensive: _known_continuous_raw_pair currently has no other limb.
            raise RigAuthorizationError(
                f"Stage effect {device}.{prop} was refused because Microclaw could not "
                "associate the known continuous property with an axis guard. No "
                "safety-config declaration can repair this internal classification; "
                "keep the effect unavailable and report the device/property to Microclaw."
            )
    else:
        guard.check_property(device, prop)


def execute_channel_plan(
    ctrl: Any, guard: Any, preset: str, *, confirm_fn: Any = None, cancel: Any = None
) -> dict:
    """Capture, authorize, and replay exactly one immutable Channel expansion.

    Cancellation is polled only between writes. The pyjavaz bridge holds one lock
    across each round trip, so an in-flight set/wait/read cannot be interrupted.
    """
    authorize_channel(ctrl, preset)
    effects = tuple(
        (device, prop, "" if value is None else str(value))
        for device, prop, value in _expand_preset(ctrl.core, preset)
    )
    for effect in effects:
        _authorize_channel_effect(ctrl, guard, *effect, confirm_fn)

    report = getattr(ctrl, "authorization_map", None)
    startup_hash = None if report is None else report.channel_expansion_hashes.get(preset)
    fresh_hash = _expansion_hash(effects)
    drifted = startup_hash is not None and startup_hash != fresh_hash
    originals = [str(ctrl.core.get_property(device, prop)) for device, prop, _ in effects]
    attempted: list[tuple[str, str, str]] = []
    applied: list[tuple[str, str, str]] = []
    try:
        for device, prop, value in effects:
            if _cancelled(cancel):
                raise ChannelPlanError("Channel plan cancelled between writes.")
            attempted.append((device, prop, value))
            ctrl.core.set_property(device, prop, value)
            _wait_for_plan_device(ctrl.core, device)
            _verify_property(ctrl.core, device, prop, value)
            applied.append((device, prop, value))
    except Exception as exc:
        rolled_back: list[str] = []
        rollback_failures: list[str] = []
        for index in range(len(attempted) - 1, -1, -1):
            device, prop, _ = attempted[index]
            try:
                ctrl.core.set_property(device, prop, originals[index])
                _wait_for_plan_device(ctrl.core, device)
                _verify_property(ctrl.core, device, prop, originals[index])
                rolled_back.append(f"{device}.{prop}")
            except Exception as rollback_exc:
                rollback_failures.append(
                    f"{device}.{prop}: {_clean_exception_message(rollback_exc)}"
                )
        applied_names = [f"{d}.{p}" for d, p, _ in applied]
        attempted_names = [f"{d}.{p}" for d, p, _ in attempted]
        message = (
            f"Channel plan {preset!r} stopped after {len(applied)}/{len(effects)} writes: "
            f"{_clean_exception_message(exc)}; applied={applied_names}; "
            f"attempted={attempted_names}; rolled_back={rolled_back}"
        )
        if rollback_failures:
            raise ChannelPlanSafeStateError(
                message + f"; SAFE STATE NOT VERIFIED; rollback_failures={rollback_failures}"
            ) from exc
        raise ChannelPlanPartialApplicationError(message) from exc

    return {
        "status": f"Channel set to '{preset}'.",
        "writes": len(effects),
        "expansion_drift": drifted,
        "startup_expansion_sha256": startup_hash,
        "applied_expansion_sha256": fresh_hash,
    }


def authorize_path(ctrl: Any, path: str) -> None:
    """Refuse a code-level path classified as excluded in the attached map."""
    report = getattr(ctrl, "authorization_map", None)
    if report is None:
        return
    entries = [entry for entry in report.entries if entry.path == path]
    if not entries or any(entry.classification == "excluded" for entry in entries):
        raise RigAuthorizationError(
            f"The {path} write path was refused because it is excluded from the Phase-1 "
            "authorization map. No safety-config declaration permits an excluded code "
            "path; use a supported typed/categorical tool path instead."
        )
