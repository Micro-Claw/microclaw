"""Live Phase-1 authorization-map construction and runtime path gates."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
import sys
from typing import Any, Iterable

from microclaw.safety import (
    ActuatorId,
    BUILTIN_TYPED_CAPABILITIES,
    ParsedSafetyConfig,
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


def _auto_classified_state_pairs(
    core: Any,
    parsed_config: ParsedSafetyConfig,
    loaded_devices: Iterable[str],
    illumination_pairs: set[tuple[str, str]],
) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    """StateDevice position pairs admitted without an explicit declaration.

    Returns (admitted, denylisted): `admitted` are auto-classified categorical;
    `denylisted` are StateDevice position pairs held back by
    forbidden_properties so the map can say so out loud.

    Fails closed everywhere: an unreadable device type, an unreadable property
    list, or an unreadable Core shutter leaves the device excluded exactly as
    it is today. Shutter-ness comes only from the MM device type, Core.Shutter,
    and the reviewed illumination config — never from the device's name.
    """
    profile = parsed_config.rig_profile
    try:
        core_shutter = str(core.get_shutter_device() or "")
    except Exception:
        # Without Core.Shutter the shutter carve-out cannot be applied, so no
        # device may be auto-classified.
        return set(), set()
    illumination_devices = {device for device, _ in illumination_pairs}
    forbidden = {
        (item.device, item.property)
        for item in parsed_config.constraints.forbidden_properties
    }
    admitted: set[tuple[str, str]] = set()
    denylisted: set[tuple[str, str]] = set()
    for device in sorted({str(d) for d in loaded_devices if d}):
        # Shutter carve-out: the Core shutter and anything the operator
        # reviewed as illumination stay on the illumination gate.
        if device == core_shutter or device in illumination_devices:
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
            if (
                pair in profile.categorical_properties
                or pair in profile.excluded_properties
                or pair in illumination_pairs
                or _known_continuous_raw_pair(core, pair)
            ):
                continue
            if pair in forbidden:
                denylisted.add(pair)
                continue
            admitted.add(pair)
    return admitted, denylisted


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

    for device, prop in sorted(profile.categorical_properties):
        if _known_continuous_raw_pair(core, (device, prop)):
            errors.append(
                f"Raw property {device}.{prop} names a known continuous actuator; "
                "Phase 1 cannot classify it as categorical."
            )
        entries.append(AuthorizationEntry(
            path="generic-property",
            classification="reviewed_categorical_property",
            device=device,
            property=prop,
            source="declared",
        ))

    # Additive to the declared pairs above: an MM StateDevice (filter wheel,
    # slider, turret) needs no declaration for its own discrete position.
    auto_pairs, auto_denylisted = _auto_classified_state_pairs(
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
    for device, prop in sorted(auto_denylisted):
        entries.append(AuthorizationEntry(
            path="generic-property",
            classification="excluded",
            device=device,
            property=prop,
            detail=(
                "MM StateDevice position held back by the forbidden_properties "
                "denylist"
            ),
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
    entries.append(AuthorizationEntry(
        path="acquisition",
        classification="built_in_typed_capability",
        device=camera_device or None,
        capability="exposure",
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
            detail="GUI-owned plan cannot be enumerated as immutable guarded effects",
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
        "reviewed_categorical_property", "built_in_typed_capability"
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
