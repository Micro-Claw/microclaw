"""Live Phase-1 authorization-map construction and runtime path gates."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from numbers import Real
import sys
from typing import Any, Iterable

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
    (
        "max_session_illuminated_ms",
        "in-memory controller-session illuminated-time maximum; resets on process restart",
    ),
    ("confirm_above_frames", "per-plan frame confirmation threshold"),
    ("confirm_above_duration_s", "per-plan estimated-duration confirmation threshold"),
    ("confirm_above_bytes", "per-plan estimated-byte confirmation threshold"),
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
    config = core.get_config_data("Channel", preset)
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
                f"Channel preset {preset!r} contains an unreadable setting; refusing it."
            )
        effects.append((device, prop, value))
    return effects


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
    try:
        kind = device_type_name(core, device)
        if kind not in {
            "StageDevice", "XYStageDevice", "CameraDevice", "GalvoDevice",
            "SignalIODevice",
        }:
            return False
        if bool(core.is_property_read_only(device, prop)):
            return False
        if _strings(core.get_allowed_property_values(device, prop)):
            return False
        return _property_type_name(core, device, prop) in {"Float", "Integer"}
    except Exception:
        return False


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
    entries: list[AuthorizationEntry] = []

    if "stage-position" not in BUILTIN_TYPED_CAPABILITIES:
        errors.append("Code registry is missing the built-in stage-position capability.")
    if "exposure" not in BUILTIN_TYPED_CAPABILITIES:
        errors.append("Code registry is missing the built-in exposure capability.")
    if "illumination" not in BUILTIN_TYPED_CAPABILITIES:
        errors.append("Code registry is missing the built-in illumination capability.")
    if "acquisition-dose" not in BUILTIN_TYPED_CAPABILITIES:
        errors.append("Code registry is missing the built-in acquisition-dose capability.")

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
                errors.append(
                    f"Reachable {identity.axis or 'z'} stage actuator {live_device!r} "
                    "has no declared range policy."
                )
            continue
        if guaranteed and (
            policy.minimum.bound is None or policy.maximum.bound is None
        ):
            errors.append(
                f"Reachable stage actuator {live_device!r} axis "
                f"{identity.axis or 'z'} has an open range edge in guaranteed mode."
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
                f"Reachable camera {camera_device!r} has no finite exposure maximum."
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

    # Device inventory is read once here: auto-classification needs it before
    # the categorical entries are emitted. The enumeration error is still
    # reported at its original position below, so failure ordering is unchanged.
    try:
        loaded_devices = _strings(core.get_loaded_devices())
        loaded_devices_error = None
    except Exception as exc:
        loaded_devices = []
        loaded_devices_error = f"Could not enumerate connected devices: {exc}"

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
        canonical_unit = "um" if policy.kind == "absolute-position" else "percent"
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
    for device, prop in sorted(profile.categorical_properties):
        if (device, prop) in typed_pairs:
            errors.append(f"Raw property {device}.{prop} cannot be both typed-continuous and categorical.")
        if (_known_continuous_raw_pair(core, (device, prop))
                or _continuous_introspection(core, device, prop)):
            errors.append(
                f"Raw property {device}.{prop} is a known continuous actuator (confirmed by the live rig) and cannot be classified as categorical; "
                "declare it in rig_profile.typed_actuators with exact semantics, units, and safe canonical bounds."
            )
        entries.append(AuthorizationEntry(
            path="generic-property",
            classification="reviewed_categorical_property",
            device=device,
            property=prop,
            source="declared",
        ))

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
    categorical_pairs = set(profile.categorical_properties) | auto_pairs

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
        entries.append(AuthorizationEntry(
            path="all-property-paths",
            classification="excluded",
            device=device,
            property=prop,
        ))

    allowed_channels = parsed_config.constraints.allowed_channels
    try:
        available_presets = _strings(core.get_available_configs("Channel"))
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
            errors.append(
                "channels.allowed lists preset(s) not present in the \"Channel\" "
                "group: " + ", ".join(repr(preset) for preset in missing) + "."
            )
        presets = [preset for preset in allowed_channels if preset in available]
    authorized_presets: set[str] = set()
    excluded_presets: dict[str, list[str]] = {}
    for preset in presets:
        reasons = []
        try:
            effects = _expand_preset(core, preset)
        except Exception as exc:
            effects = []
            reasons.append(_clean_exception_message(exc))
        for device, prop, value in effects:
            pair = (device, prop)
            if pair in profile.excluded_properties:
                classification = "excluded"
                reasons.append(f"{device}.{prop} is excluded")
            elif pair in typed_pairs:
                classification = "excluded"
                reasons.append(
                    f"{device}.{prop} is typed continuous but presets cannot invoke the typed guard without the deferred channel-plan executor"
                )
            elif pair in illumination_pairs:
                classification = "excluded"
                reasons.append(
                    f"{device}.{prop} is typed illumination but presets cannot "
                    "invoke check_illumination without the deferred channel-plan executor"
                )
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
                f"acquisition.{field_name}; got {value!r}."
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
        raise RigAuthorizationError(
            "Live rig authorization failed:\n- " + "\n- ".join(errors)
        )

    # A raw write passes two gates: this map and SafetyGuard.check_property's
    # categorical allowlist (built from the declared pairs at config-parse
    # time). Hand the guard exactly the pairs auto-classified above — nothing
    # else — so an auto-classified write is admitted by both.
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
            f"Property write {device}.{prop} is excluded from the authorization map."
        )


def authorize_channel(ctrl: Any, preset: str) -> None:
    report = getattr(ctrl, "authorization_map", None)
    if report is not None and preset not in report.authorized_presets:
        reasons = report.excluded_presets.get(preset, ["preset was not authorized at startup"])
        raise RigAuthorizationError(
            f"Channel preset {preset!r} is excluded: {'; '.join(reasons)}"
        )


def authorize_path(ctrl: Any, path: str) -> None:
    """Refuse a code-level path classified as excluded in the attached map."""
    report = getattr(ctrl, "authorization_map", None)
    if report is None:
        return
    entries = [entry for entry in report.entries if entry.path == path]
    if not entries or any(entry.classification == "excluded" for entry in entries):
        raise RigAuthorizationError(
            f"The {path} write path is excluded from the Phase-1 authorization map."
        )
