from __future__ import annotations
from dataclasses import dataclass, field
import math
from numbers import Real
from typing import Iterable, Literal, Optional
import os
import yaml


class SafetyViolation(Exception):
    """Raised when a tool call would violate a user-defined safety constraint."""


class SafetyConfigError(ValueError):
    """Raised once with all recoverable safety-config validation errors."""


def _finite_number(value, name: str, error_type=SafetyViolation) -> float:
    """Return a finite real number, failing closed on bools and invalid values."""
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
        raise error_type(f"{name} must be a finite number (not a boolean); got {value!r}")
    return float(value)


def _finite_number_text(value, name: str) -> float:
    """Parse a numeric device-property value, then apply the shared validator."""
    if isinstance(value, bool):
        raise SafetyViolation(f"{name} must be a finite number; got {value!r}")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise SafetyViolation(f"{name} must be a finite number; got {value!r}")
    return _finite_number(number, name)


@dataclass
class StageConstraints:
    x_min: Optional[float] = None
    x_max: Optional[float] = None
    y_min: Optional[float] = None
    y_max: Optional[float] = None
    z_min: Optional[float] = None
    z_max: Optional[float] = None


@dataclass
class CameraConstraints:
    max_exposure_ms: Optional[float] = None


@dataclass
class AnalysisConstraints:
    """Rig-measured analysis settings; these do not authorize hardware actions."""
    min_snr: Optional[float] = None


@dataclass
class AcquisitionConstraints:
    max_frames: Optional[float] = None
    max_duration_s: Optional[float] = None
    max_bytes: Optional[float] = None
    max_illuminated_ms: Optional[float] = None
    max_session_illuminated_ms: Optional[float] = None
    confirm_above_frames: Optional[float] = None
    confirm_above_duration_s: Optional[float] = None
    confirm_above_bytes: Optional[float] = None
    confirm_above_illuminated_ms: Optional[float] = None


@dataclass
class ForbiddenProperty:
    device: str
    property: str


@dataclass
class IlluminationProperty:
    device: str
    property: str
    on_value: str = "On"
    off_value: str = "Off"


@dataclass
class IlluminationConstraints:
    """Gate for anything that emits light at the sample (design/14 §3).

    Illumination is the only irreversible thing microclaw controls: it bleaches
    sample and endangers eyes. Before this class existed a Class-3B laser was
    one unconfirmed set_device_property away.

      shutters                  properties that gate light; turning one to its
                                on_value requires a blocking human confirmation.
      power_properties          properties that set emission power (percent).
      max_power_percent         refuse writes above this value.
      max_power_step_factor     bound the ratio between consecutive parent writes,
                                limiting how fast power climbs rather than how high
                                it can reach. From zero it imposes no constraint;
                                only max_power_percent bounds the first increase.
                                This is a runaway backstop, not a gradual-ramp
                                mechanism; hooks implement ramps themselves.
      require_confirm_on_enable confirm-gate shutter enables (default on).
    """

    shutters: list[IlluminationProperty] = field(default_factory=list)
    power_properties: list[ForbiddenProperty] = field(default_factory=list)
    max_power_percent: Optional[float] = None
    max_power_step_factor: Optional[float] = None
    require_confirm_on_enable: bool = True


@dataclass
class PluginConstraints:
    """Gates for Micro-Manager plugin hooks (see design/09).

    Plugin code is arbitrary Java that bypasses SafetyGuard and the Python AST
    scanner. Two risk classes, two gates:

      blocked                 read-only analyzer plugins are allowed by default;
                              list only the fully-qualified classpaths to forbid.
      allow_hardware_motion   a single global opt-in for plugins that move
                              hardware (e.g. autofocus). Off by default.
    """

    blocked: list[str] = field(default_factory=list)
    allow_hardware_motion: bool = False


@dataclass
class NamedStageLimits:
    """Travel limits for a single-axis stage addressed by device label.

    A single global stage.z_min/z_max cannot express 'PIZStage: 0–200 µm,
    TIRF Stage: ±3000 µm' (design/14 §6)."""

    device: str
    min_um: Optional[float] = None
    max_um: Optional[float] = None


@dataclass(frozen=True)
class RangeEdge:
    """One finite range boundary, or an explicitly reviewed open boundary."""

    bound: float | None
    unbounded_reason: str | None


@dataclass(frozen=True)
class ActuatorId:
    """Collision-free identity for a core axis or a named stage."""

    source: Literal["core_xy", "core_focus", "named"]
    device: str | None
    capability: str
    axis: str | None = None


@dataclass(frozen=True)
class RangePolicy:
    minimum: RangeEdge
    maximum: RangeEdge


AuthorizationMode = Literal["guaranteed", "degraded_trusted_plugins"]
BUILTIN_TYPED_CAPABILITIES = frozenset(
    {"stage-position", "exposure", "illumination", "acquisition-dose"}
)


@dataclass(frozen=True)
class RigProfile:
    """Reviewed per-rig property decisions; ranges declare stage actuators."""

    mode: AuthorizationMode
    categorical_properties: frozenset[tuple[str, str]]
    excluded_properties: frozenset[tuple[str, str]]


@dataclass
class SafetyConstraints:
    stage: StageConstraints = field(default_factory=StageConstraints)
    camera: CameraConstraints = field(default_factory=CameraConstraints)
    analysis: AnalysisConstraints = field(default_factory=AnalysisConstraints)
    acquisition: AcquisitionConstraints = field(default_factory=AcquisitionConstraints)
    allowed_channels: Optional[list[str]] = None  # None means all allowed
    forbidden_properties: list[ForbiddenProperty] = field(default_factory=list)
    # None = denylist mode (forbidden_properties). When set, ONLY these
    # (device, property) pairs may be written; everything else is refused. This
    # is the only mode in which raw property writes have a hard gate (see
    # SafetyGuard.check_property and safety_config.yaml).
    allowed_properties: Optional[list[ForbiddenProperty]] = None
    # Filesystem sandbox root for file-touching tools. None = unrestricted
    # (behaviour unchanged); set it to confine reads/writes to one directory.
    workspace_dir: Optional[str] = None
    plugins: PluginConstraints = field(default_factory=PluginConstraints)
    illumination: IlluminationConstraints = field(
        default_factory=IlluminationConstraints
    )
    # Per-device limits for stages addressed by label (move_named_stage). The
    # global stage.z_min/z_max applies only to the core focus device; named
    # stages fail closed — no entry here means the stage may not be moved.
    named_stages: list[NamedStageLimits] = field(default_factory=list)


def _range_edge(raw, label: str, problem) -> RangeEdge | None:
    """Parse exactly one finite bound or reviewed-unbounded declaration."""
    if isinstance(raw, dict):
        for key in raw.keys() - {"unbounded", "reason"}:
            problem(f"{label}.{key}", "unknown key")
        if raw.get("unbounded") is not True:
            problem(label, "object edge must set `unbounded: true`")
            return None
        reason = raw.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            problem(label, "reviewed-unbounded edge requires a non-empty reason")
            return None
        return RangeEdge(None, reason)
    try:
        return RangeEdge(_finite_number(raw, label, ValueError), None)
    except ValueError as exc:
        problem(label, str(exc))
        return None


def _stage_ranges(
    stage_cfg: dict, named_cfg: list[dict], problem
) -> dict[ActuatorId, RangePolicy]:
    """Build the single authoritative range map for core and named stages."""
    policies: dict[ActuatorId, RangePolicy] = {}

    def add(identity: ActuatorId, raw: dict, low: str, high: str, location: str) -> None:
        present = low in raw or high in raw
        if not present:
            return
        missing = [key for key in (low, high) if key not in raw]
        for key in missing:
            problem(
                location,
                f"declared range is missing {key!r}; give it a finite bound or mark it explicitly unbounded",
            )
        if missing:
            return
        prefix = "stage" if location.startswith("stage.") else location
        minimum = _range_edge(raw[low], f"{prefix}.{low}", problem)
        maximum = _range_edge(raw[high], f"{prefix}.{high}", problem)
        if minimum is None or maximum is None:
            return
        if (
            minimum.bound is not None
            and maximum.bound is not None
            and minimum.bound >= maximum.bound
        ):
            problem(location, "minimum must be less than maximum")
            return
        policies[identity] = RangePolicy(minimum, maximum)

    for axis in ("x", "y", "z"):
        source = "core_focus" if axis == "z" else "core_xy"
        add(
            ActuatorId(source, None, "stage-position", axis),
            stage_cfg,
            f"{axis}_min",
            f"{axis}_max",
            f"stage.{axis}",
        )
    seen_devices: set[str] = set()
    for index, item in enumerate(named_cfg):
        location = f"named_stages[{index}]"
        device = item.get("device")
        if not isinstance(device, str) or not device.strip():
            problem(f"{location}.device", "required non-empty string")
            continue
        if device in seen_devices:
            problem(f"{location}.device", f"duplicate named stage {device!r}")
            continue
        seen_devices.add(device)
        if "min_um" not in item and "max_um" not in item:
            problem(location, "declared named stage requires both 'min_um' and 'max_um'")
            continue
        add(
            ActuatorId("named", device, "stage-position"),
            item,
            "min_um",
            "max_um",
            location,
        )
    return policies


def _stage_constraints(
    ranges: dict[ActuatorId, RangePolicy],
) -> tuple[StageConstraints, list[NamedStageLimits]]:
    """Compile all runtime stage limits in one pass over authoritative policies."""
    core: dict[str, float | None] = {}
    named: list[NamedStageLimits] = []
    for identity, policy in ranges.items():
        if identity.source == "named":
            assert identity.device is not None
            named.append(
                NamedStageLimits(
                    identity.device, policy.minimum.bound, policy.maximum.bound
                )
            )
        else:
            assert identity.axis is not None
            core[f"{identity.axis}_min"] = policy.minimum.bound
            core[f"{identity.axis}_max"] = policy.maximum.bound
    return StageConstraints(**core), named


@dataclass(frozen=True)
class ParsedSafetyConfig:
    """A schema-validated file and its retained authoritative range policies."""

    constraints: SafetyConstraints
    ranges: dict[ActuatorId, RangePolicy]
    rig_profile: RigProfile = field(
        default_factory=lambda: RigProfile("guaranteed", frozenset(), frozenset())
    )

    @classmethod
    def from_yaml(cls, path: str) -> ParsedSafetyConfig:
        with open(path, encoding="utf-8") as f:
            loaded = yaml.safe_load(f)

        errors: list[str] = []

        def problem(location: str, message: str) -> None:
            errors.append(f"{path}: {location}: {message}")

        if loaded is None:
            cfg = {}
        elif not isinstance(loaded, dict):
            raise SafetyConfigError(
                f"{path}: document root: expected a mapping, got {type(loaded).__name__}"
            )
        else:
            cfg = loaded

        top_keys = {
            "schema_version", "reviewed", "stage", "camera", "analysis", "channels", "plugins",
            "illumination", "forbidden_properties",
            "workspace_dir", "named_stages", "rig_profile", "acquisition",
        }
        section_keys = {
            "stage": {"x_min", "x_max", "y_min", "y_max", "z_min", "z_max"},
            "camera": {"max_exposure_ms"},
            "analysis": {"min_snr"},
            "acquisition": {
                "max_frames", "max_duration_s", "max_bytes",
                "max_illuminated_ms", "max_session_illuminated_ms",
                "confirm_above_frames", "confirm_above_duration_s",
                "confirm_above_bytes", "confirm_above_illuminated_ms",
            },
            "channels": {"allowed"},
            "plugins": {"blocked", "allow_hardware_motion"},
            "illumination": {
                "shutters", "power_properties", "max_power_percent",
                "max_power_step_factor", "require_confirm_on_enable",
            },
            "rig_profile": {"mode", "categorical_properties", "excluded_properties"},
        }
        for key in cfg.keys() - top_keys:
            problem(str(key), "unknown top-level key")

        if "schema_version" not in cfg:
            problem(
                "schema_version",
                "missing required schema version; add `schema_version: 2`, a rig_profile, and give every declared range both edges",
            )
        elif type(cfg["schema_version"]) is not int or cfg["schema_version"] != 2:
            problem(
                "schema_version",
                f"unsupported schema version {cfg['schema_version']!r}; expected 2 (schema 1 configs must add rig_profile and migrate to property allowlist mode)",
            )
        if "reviewed" not in cfg:
            problem("reviewed", "missing required key")
        if "rig_profile" not in cfg:
            problem("rig_profile", "missing required declared rig profile")
        if "acquisition" not in cfg:
            fields = ", ".join(sorted(section_keys["acquisition"]))
            problem(
                "acquisition",
                "missing required acquisition budget section; add all nine fields: "
                + fields,
            )

        def mapping(name: str) -> dict:
            value = cfg.get(name, {})
            if value is None:
                value = {}
            if not isinstance(value, dict):
                problem(name, f"expected a mapping, got {type(value).__name__}")
                return {}
            for key in value.keys() - section_keys[name]:
                problem(f"{name}.{key}", "unknown key")
            return value

        stage_cfg = mapping("stage")
        camera_cfg = mapping("camera")
        analysis_cfg = mapping("analysis")
        acquisition_cfg = mapping("acquisition")
        channels_cfg = mapping("channels")
        plugins_cfg = mapping("plugins")
        ill_cfg = mapping("illumination")
        rig_cfg = mapping("rig_profile")

        # `analysis.min_snr` is deliberately optional (omitting it retains the
        # uncalibrated package fallback — see the shipped example), so it is NOT
        # required-when-present here.
        for section_name, section, required_keys in (
            ("camera", camera_cfg, {"max_exposure_ms"}),
            ("channels", channels_cfg, {"allowed"}),
        ):
            if section_name in cfg:
                for key in required_keys - section.keys():
                    problem(f"{section_name}.{key}", "missing required key")

        def typed(location: str, value, expected_type: type) -> bool:
            if not isinstance(value, expected_type):
                problem(
                    location,
                    f"expected {expected_type.__name__}, got {type(value).__name__}",
                )
                return False
            return True

        if "reviewed" in cfg:
            typed("reviewed", cfg["reviewed"], bool)
        if cfg.get("workspace_dir") is not None:
            typed("workspace_dir", cfg["workspace_dir"], str)
        for section, key, location in (
            (plugins_cfg, "allow_hardware_motion", "plugins.allow_hardware_motion"),
            (ill_cfg, "require_confirm_on_enable", "illumination.require_confirm_on_enable"),
        ):
            if key in section:
                typed(location, section[key], bool)
        for section, key, location in (
            (channels_cfg, "allowed", "channels.allowed"),
            (plugins_cfg, "blocked", "plugins.blocked"),
        ):
            value = section.get(key)
            if value is not None and typed(location, value, list):
                for index, item in enumerate(value):
                    typed(f"{location}[{index}]", item, str)

        numeric_fields = [
            (camera_cfg, "max_exposure_ms", "camera.max_exposure_ms"),
            (analysis_cfg, "min_snr", "analysis.min_snr"),
            (ill_cfg, "max_power_percent", "illumination.max_power_percent"),
            (ill_cfg, "max_power_step_factor", "illumination.max_power_step_factor"),
            *[
                (acquisition_cfg, key, f"acquisition.{key}")
                for key in section_keys["acquisition"]
            ],
        ]
        for section, key, location in numeric_fields:
            if section.get(key) is not None:
                try:
                    section[key] = _finite_number(section[key], location, ValueError)
                except ValueError as e:
                    problem(location, str(e))

        exposure = camera_cfg.get("max_exposure_ms")
        if isinstance(exposure, float) and exposure <= 0:
            problem("camera.max_exposure_ms", "must be greater than zero")
        if "acquisition" in cfg:
            for key in section_keys["acquisition"] - acquisition_cfg.keys():
                problem(f"acquisition.{key}", "missing required key")
        for key, value in acquisition_cfg.items():
            if isinstance(value, float) and value <= 0:
                problem(f"acquisition.{key}", "must be greater than zero")

        def object_list(name: str, value, allowed: set[str]) -> list[dict]:
            if value is None:
                return []
            if not isinstance(value, list):
                problem(name, f"expected a list, got {type(value).__name__}")
                return []
            result = []
            for index, item in enumerate(value):
                location = f"{name}[{index}]"
                if not isinstance(item, dict):
                    problem(location, f"expected a mapping, got {type(item).__name__}")
                    continue
                for key in item.keys() - allowed:
                    problem(f"{location}.{key}", "unknown key")
                for key, item_value in item.items():
                    if key in allowed and key not in {"min_um", "max_um"}:
                        typed(f"{location}.{key}", item_value, str)
                result.append(item)
            return result

        forbidden_cfg = object_list(
            "forbidden_properties", cfg.get("forbidden_properties"), {"device", "property"}
        )
        categorical_value = rig_cfg.get("categorical_properties")
        categorical_cfg = object_list(
            "rig_profile.categorical_properties", categorical_value,
            {"device", "property"},
        )
        excluded_cfg = object_list(
            "rig_profile.excluded_properties", rig_cfg.get("excluded_properties"),
            {"device", "property"},
        )
        shutters_cfg = object_list(
            "illumination.shutters", ill_cfg.get("shutters"),
            {"device", "property", "on_value", "off_value"},
        )
        power_cfg = object_list(
            "illumination.power_properties", ill_cfg.get("power_properties"),
            {"device", "property"},
        )
        named_cfg = object_list(
            "named_stages", cfg.get("named_stages"), {"device", "min_um", "max_um"}
        )
        for name, items in (
            ("forbidden_properties", forbidden_cfg),
            ("rig_profile.categorical_properties", categorical_cfg),
            ("rig_profile.excluded_properties", excluded_cfg),
            ("illumination.shutters", shutters_cfg),
            ("illumination.power_properties", power_cfg),
        ):
            seen_pairs: set[tuple[str, str]] = set()
            for index, item in enumerate(items):
                for key in ("device", "property"):
                    if key not in item:
                        problem(f"{name}[{index}].{key}", "missing required key")
                pair = (item.get("device"), item.get("property"))
                if all(isinstance(value, str) for value in pair):
                    if pair in seen_pairs:
                        problem(f"{name}[{index}]", f"duplicate device/property pair {pair!r}")
                    seen_pairs.add(pair)
        ranges = _stage_ranges(stage_cfg, named_cfg, problem)

        mode = rig_cfg.get("mode", "guaranteed")
        if mode not in ("guaranteed", "degraded_trusted_plugins"):
            problem(
                "rig_profile.mode",
                "expected 'guaranteed' or 'degraded_trusted_plugins'",
            )
        if "rig_profile" in cfg:
            for key in {"excluded_properties"} - rig_cfg.keys():
                problem(f"rig_profile.{key}", "missing required key")
        if mode == "guaranteed" and categorical_value is None:
            problem(
                "rig_profile.categorical_properties",
                "required in guaranteed mode, even when empty; denylist-only configs must migrate",
            )
        categorical_pairs = {
            (item["device"], item["property"])
            for item in categorical_cfg
            if isinstance(item.get("device"), str)
            and isinstance(item.get("property"), str)
        }
        excluded_pairs = {
            (item["device"], item["property"])
            for item in excluded_cfg
            if isinstance(item.get("device"), str)
            and isinstance(item.get("property"), str)
        }
        for pair in sorted(categorical_pairs & excluded_pairs):
            problem(
                "rig_profile",
                f"property {pair!r} cannot be both categorically authorized and excluded",
            )

        if errors:
            raise SafetyConfigError("Invalid safety config:\n" + "\n".join(errors))

        forbidden = [
            ForbiddenProperty(**p)
            for p in forbidden_cfg
        ]
        allowed = (
            [ForbiddenProperty(**p) for p in categorical_cfg]
            if categorical_value is not None
            else None
        )
        stage, named_stages = _stage_constraints(ranges)
        constraints = SafetyConstraints(
            stage=stage,
            camera=CameraConstraints(**camera_cfg),
            analysis=AnalysisConstraints(**analysis_cfg),
            acquisition=AcquisitionConstraints(**acquisition_cfg),
            allowed_channels=channels_cfg.get("allowed"),
            forbidden_properties=forbidden,
            allowed_properties=allowed,
            workspace_dir=cfg.get("workspace_dir"),
            plugins=PluginConstraints(
                blocked=plugins_cfg.get("blocked") or [],
                allow_hardware_motion=bool(plugins_cfg.get("allow_hardware_motion", False)),
            ),
            illumination=IlluminationConstraints(
                shutters=[
                    IlluminationProperty(**s) for s in shutters_cfg
                ],
                power_properties=[
                    ForbiddenProperty(**p)
                    for p in power_cfg
                ],
                max_power_percent=ill_cfg.get("max_power_percent"),
                max_power_step_factor=ill_cfg.get("max_power_step_factor"),
                require_confirm_on_enable=bool(
                    ill_cfg.get("require_confirm_on_enable", True)
                ),
            ),
            named_stages=named_stages,
        )
        return cls(
            constraints=constraints,
            ranges=ranges,
            rig_profile=RigProfile(
                mode, frozenset(categorical_pairs), frozenset(excluded_pairs)
            ),
        )


class SafetyGuard:
    # Raw property-name aliases that map onto the numeric guards. These are
    # defence-in-depth only: they narrow the hole for a raw set_property that
    # targets a guarded axis, they do NOT close it (see check_device_property).
    _MOTION_PROPS = {"position"}          # focus-device raw position aliases
    _EXPOSURE_PROPS = {"exposure"}        # camera raw exposure aliases
    _XY_PROPS = {"x", "y", "xposition", "yposition"}

    def __init__(self, constraints: SafetyConstraints):
        self._c = constraints
        # (device, property) pairs the live authorization map auto-classified
        # as categorical because Micro-Manager types the device a StateDevice
        # (see authorization.validate_live_rig). Empty until startup fills it,
        # so nothing widens without a live rig saying so.
        self._auto_classified: frozenset[tuple[str, str]] = frozenset()

    def admit_auto_classified(self, pairs: Iterable[tuple[str, str]]) -> None:
        """Admit exactly the pairs the live authorization map auto-classified.

        Additive to allowed_properties, never a replacement: this is the only
        way a pair enters the guard without a config declaration, and the
        forbidden_properties denylist still refuses these pairs.
        """
        self._auto_classified = frozenset(
            (str(device), str(prop)) for device, prop in pairs
        )

    @property
    def analysis_min_snr(self) -> float | None:
        """Configured analysis gate for internal hook injection, not a tool payload."""
        return self._c.analysis.min_snr

    def check_xy(self, x: float, y: float) -> None:
        x = _finite_number(x, "X position")
        y = _finite_number(y, "Y position")
        s = self._c.stage
        for name in ("x_min", "x_max", "y_min", "y_max"):
            value = getattr(s, name)
            if value is not None:
                _finite_number(value, f"Configured stage.{name}")
        if s.x_min is not None and x < s.x_min:
            raise SafetyViolation(
                f"X={x:.1f} µm is below the minimum allowed ({s.x_min:.1f} µm)."
            )
        if s.x_max is not None and x > s.x_max:
            raise SafetyViolation(
                f"X={x:.1f} µm exceeds the maximum allowed ({s.x_max:.1f} µm)."
            )
        if s.y_min is not None and y < s.y_min:
            raise SafetyViolation(
                f"Y={y:.1f} µm is below the minimum allowed ({s.y_min:.1f} µm)."
            )
        if s.y_max is not None and y > s.y_max:
            raise SafetyViolation(
                f"Y={y:.1f} µm exceeds the maximum allowed ({s.y_max:.1f} µm)."
            )

    def check_z(self, z: float) -> None:
        z = _finite_number(z, "Z position")
        s = self._c.stage
        if s.z_min is not None:
            _finite_number(s.z_min, "Configured stage.z_min")
        if s.z_max is not None:
            _finite_number(s.z_max, "Configured stage.z_max")
        if s.z_min is not None and z < s.z_min:
            raise SafetyViolation(
                f"Z={z:.1f} µm is below the minimum allowed ({s.z_min:.1f} µm)."
            )
        if s.z_max is not None and z > s.z_max:
            raise SafetyViolation(
                f"Z={z:.1f} µm exceeds the maximum allowed ({s.z_max:.1f} µm)."
            )

    def check_exposure(self, ms: float) -> None:
        ms = _finite_number(ms, "Exposure")
        limit = self._c.camera.max_exposure_ms
        if limit is not None:
            _finite_number(limit, "Configured camera.max_exposure_ms")
        if limit is not None and ms > limit:
            raise SafetyViolation(
                f"Exposure {ms:.0f} ms exceeds the maximum allowed ({limit:.0f} ms)."
            )

    def check_acquisition(
        self, *, frames, duration_s, bytes_, illuminated_ms,
        session_illuminated_ms,
    ) -> None:
        values = {
            "frames": _finite_number(frames, "Acquisition frames"),
            "duration_s": _finite_number(duration_s, "Acquisition duration"),
            "bytes": _finite_number(bytes_, "Acquisition bytes"),
            "illuminated_ms": _finite_number(illuminated_ms, "Acquisition illuminated time"),
            "session_illuminated_ms": _finite_number(
                session_illuminated_ms, "Session illuminated time"
            ) + _finite_number(illuminated_ms, "Acquisition illuminated time"),
        }
        limits = self._c.acquisition
        for value_name, limit_name in (
            ("frames", "max_frames"), ("duration_s", "max_duration_s"),
            ("bytes", "max_bytes"), ("illuminated_ms", "max_illuminated_ms"),
            ("session_illuminated_ms", "max_session_illuminated_ms"),
        ):
            limit = getattr(limits, limit_name)
            if limit is not None and values[value_name] > _finite_number(
                limit, f"Configured acquisition.{limit_name}"
            ):
                raise SafetyViolation(
                    f"Acquisition {value_name}={values[value_name]:g} exceeds "
                    f"acquisition.{limit_name}={limit:g}."
                )

    @property
    def acquisition_confirmation_thresholds(self) -> AcquisitionConstraints:
        return self._c.acquisition

    def check_channel(self, preset: str) -> None:
        allowed = self._c.allowed_channels
        if allowed is not None and preset not in allowed:
            raise SafetyViolation(
                f"Channel '{preset}' is not in the allowed list: {allowed}."
            )

    def check_property(self, device: str, prop: str) -> None:
        allow = self._c.allowed_properties
        if allow is not None:
            # Allowlist mode: the only hard gate for raw property writes.
            if not any(a.device == device and a.property == prop for a in allow):
                if (device, prop) not in self._auto_classified:
                    raise SafetyViolation(
                        f"Property '{device}.{prop}' is not in the allowed_properties list."
                    )
                # Auto-classified StateDevice position: admitted by the live
                # authorization map, but the denylist still wins over it.
                for fp in self._c.forbidden_properties:
                    if fp.device == device and fp.property == prop:
                        raise SafetyViolation(
                            f"Property '{device}.{prop}' is forbidden by safety config."
                        )
            return
        for fp in self._c.forbidden_properties:
            if fp.device == device and fp.property == prop:
                raise SafetyViolation(
                    f"Property '{device}.{prop}' is forbidden by safety config."
                )

    def check_device_property(self, core, device: str, prop: str, value: str) -> None:
        """Guard a raw `set_property` write on a guarded axis, then apply the
        denylist/allowlist from check_property.

        HEURISTIC, NOT A GATE. It re-applies the numeric guards only when the
        target is the *current* focus/camera/XY device and the property name is
        one of the small alias sets above. It does NOT protect a second Z drive,
        a driver whose position property is named differently ("Position (um)",
        "PositionZ", ASI/PI names), or relative-move/offset properties. Ordinary
        raw writes require allowlist mode; configured illumination pairs instead
        use their exact code-owned typed capability and check_illumination.
        """
        illumination_pair = self.is_illumination_enable(device, prop) or any(
            item.device == device and item.property == prop
            for item in self._c.illumination.power_properties
        )
        # Illumination pairs are code-owned typed capabilities. Startup's live
        # map authorizes the exact pair; check_illumination below owns its
        # confirmation/cap/ratchet rather than the categorical allowlist.
        if not illumination_pair:
            self.check_property(device, prop)      # denylist/allowlist first
        p = prop.lower()
        focus = core.get_focus_device()
        cam = core.get_camera_device()
        xy = core.get_xy_stage_device()
        if device == focus and p in self._MOTION_PROPS:
            num = _finite_number_text(value, f"Position property {device}.{prop}")
            self.check_z(num)
        elif device == cam and p in self._EXPOSURE_PROPS:
            num = _finite_number_text(value, f"Exposure property {device}.{prop}")
            self.check_exposure(num)
        elif device == xy and p in self._XY_PROPS:
            num = _finite_number_text(value, f"Position property {device}.{prop}")
            # Only one axis is known here; read the other from the core so the
            # known axis is guarded against its own bound.
            x = num if p.startswith("x") else core.get_x_position()
            y = num if p.startswith("y") else core.get_y_position()
            self.check_xy(x, y)

    def is_illumination_enable(
        self, device: str, prop: str
    ) -> Optional[IlluminationProperty]:
        return next(
            (
                s
                for s in self._c.illumination.shutters
                if s.device == device and s.property == prop
            ),
            None,
        )

    def is_illumination_power(
        self, device: str, prop: str
    ) -> Optional[ForbiddenProperty]:
        """Return the declared power property for an exact device/property pair."""
        return next(
            (
                item
                for item in self._c.illumination.power_properties
                if item.device == device and item.property == prop
            ),
            None,
        )

    @property
    def max_illumination_power_percent(self) -> Optional[float]:
        return self._c.illumination.max_power_percent

    @property
    def max_illumination_power_step_factor(self) -> Optional[float]:
        return self._c.illumination.max_power_step_factor

    def check_illumination(
        self, core, device: str, prop: str, value: str, confirm_fn=None,
        previous_percent: float | None = None,
    ) -> None:
        """Confirm-gate a shutter enable, and ratchet-gate a power increase.

        ``previous_percent`` lets a trusted parent enforce the ratchet against
        its own last successful write without re-reading mutable device state.

        Enforced in code, not just the prompt — same reasoning as
        save_knowledge's blocking confirmation: a confused model or an injected
        instruction must not be able to lase without a human 'y'.
        """
        ill = self._c.illumination
        shutter = self.is_illumination_enable(device, prop)
        if shutter and value == shutter.on_value and ill.require_confirm_on_enable:
            # kind as an argument, not a prose prefix the frontend would have to
            # string-match: safety.py stays free to reword the summary.
            if confirm_fn is None or not confirm_fn(
                f"ENABLE ILLUMINATION: {device}.{prop} = {value!r}\n"
                f"This will emit light at the sample.",
                kind="illumination",
            ):
                raise SafetyViolation(
                    f"User declined to enable illumination {device}.{prop}."
                )

        if not self.is_illumination_power(device, prop):
            return
        new = _finite_number_text(value, f"Illumination power {device}.{prop}")
        if ill.max_power_percent is not None:
            _finite_number(
                ill.max_power_percent, "Configured illumination.max_power_percent"
            )
        if ill.max_power_step_factor is not None:
            _finite_number(
                ill.max_power_step_factor,
                "Configured illumination.max_power_step_factor",
            )
        if ill.max_power_percent is not None and new > ill.max_power_percent:
            raise SafetyViolation(
                f"{new:.1f}% exceeds illumination.max_power_percent "
                f"({ill.max_power_percent:.1f}%)."
            )
        if ill.max_power_step_factor is not None:
            old = (
                _finite_number(previous_percent, "Previous illumination power")
                if previous_percent is not None
                else _finite_number_text(
                    core.get_property(device, prop),
                    f"Current illumination power {device}.{prop}",
                )
            )
            if old > 0 and new / old > ill.max_power_step_factor:
                raise SafetyViolation(
                    f"Power increase {old:.1f}% → {new:.1f}% exceeds the "
                    f"{ill.max_power_step_factor}× per-write ratchet. "
                    f"Step up gradually."
                )

    def shutter_all(self, core) -> list[str]:
        """Best-effort: drive every known illumination shutter to its off value.

        Called on session teardown so no exit path leaves a laser on. Must not
        raise — a failed shutter on one device should not stop the others."""
        done = []
        for s in self._c.illumination.shutters:
            try:
                core.set_property(s.device, s.property, s.off_value)
                done.append(f"{s.device}.{s.property}")
            except Exception:
                pass
        return done

    def check_named_stage(self, device: str, pos: float) -> None:
        """Guard a stage addressed by label against its per-device travel limits.

        Fails closed: a stage with no named_stages entry may not be moved at
        all. The global stage.z_min/z_max cannot stand in — it describes only
        the core focus device, and applying it to (say) a ±3 mm TIRF steering
        axis would be wrong in both directions.
        """
        pos = _finite_number(pos, f"Position for named stage {device}")
        lim = next(
            (l for l in self._c.named_stages if l.device == device), None
        )
        if lim is None:
            raise SafetyViolation(
                f"No limits configured for stage '{device}'. Add a named_stages "
                f"entry to the safety config before microclaw may move it."
            )
        if lim.min_um is not None:
            _finite_number(lim.min_um, f"Configured named_stages[{device}].min_um")
        if lim.max_um is not None:
            _finite_number(lim.max_um, f"Configured named_stages[{device}].max_um")
        if lim.min_um is not None and pos < lim.min_um:
            raise SafetyViolation(
                f"{device}={pos:.2f} µm is below the minimum allowed ({lim.min_um:.2f} µm)."
            )
        if lim.max_um is not None and pos > lim.max_um:
            raise SafetyViolation(
                f"{device}={pos:.2f} µm exceeds the maximum allowed ({lim.max_um:.2f} µm)."
            )

    def resolve_in_workspace(self, path: str) -> str:
        """Resolve a file path, confining it to workspace_dir if one is set.

        realpath-resolves (so `..` and symlinks can't escape) and raises
        SafetyViolation if the result leaves the configured root. When
        workspace_dir is None the path is absolutised — confinement is
        unchanged unless a lab opts in by configuring a root.
        """
        root = self._c.workspace_dir
        if root is None:
            # abspath, not normpath. normpath is lexical: it respells separators
            # and leaves '/tmp/x' as the drive-relative '\tmp\x'. dataset_path
            # does not come through here at all — it comes back from
            # pycro-manager's Java side already anchored ('C:\tmp\...', with
            # AcqEngJ's _1 rename); matching a filesystem-resolved string takes
            # filesystem resolution, not respelling (design/21 F6).
            return os.path.abspath(path)
        root = os.path.realpath(root)
        target = path if os.path.isabs(path) else os.path.join(root, path)
        resolved = os.path.realpath(target)
        # rstrip: realpath of a drive or filesystem root ("D:\", "/") already
        # ends in a separator, so `root + os.sep` would be a doubled separator
        # that nothing starts with — and the sandbox would reject every path.
        if resolved != root and not resolved.startswith(root.rstrip(os.sep) + os.sep):
            raise SafetyViolation(
                f"Path '{path}' escapes the configured workspace directory ({root})."
            )
        return resolved

    def check_plugin(self, classpath: str) -> None:
        """Gate a read-only analyzer plugin: allow by default, deny if blocklisted.

        Enforced at *runtime* (when the plugin object is constructed), because the
        classpath is just a Python string the AST scanner can't interpret.
        """
        if classpath in self._c.plugins.blocked:
            raise SafetyViolation(
                f"Plugin '{classpath}' is in safety_config.yaml plugins.blocked."
            )

    def check_plugin_motion(self, classpath: str) -> None:
        """Gate a hardware-motion plugin behind the global opt-in flag.

        A blocklist can't protect against a *legitimate* plugin parking a stage
        somewhere unsafe, so hardware-motion plugins are gated by one global flag
        rather than a per-plugin allowlist. The blocklist still applies.
        """
        self.check_plugin(classpath)  # blocklist still applies
        if not self._c.plugins.allow_hardware_motion:
            raise SafetyViolation(
                f"Plugin '{classpath}' moves hardware; set plugins.allow_hardware_motion: "
                "true in safety_config.yaml to permit hardware-motion plugin hooks. "
                "microclaw guards the *result* (see check_z) but does not re-drive the "
                "axis the plugin controls."
            )
