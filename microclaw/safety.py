from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import os
import yaml


class SafetyViolation(Exception):
    """Raised when a tool call would violate a user-defined safety constraint."""


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
      max_power_step_factor     refuse a power increase of more than N× in one
                                write (1% → 25% must take deliberate steps).
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


@dataclass
class SafetyConstraints:
    stage: StageConstraints = field(default_factory=StageConstraints)
    camera: CameraConstraints = field(default_factory=CameraConstraints)
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

    @classmethod
    def from_yaml(cls, path: str) -> SafetyConstraints:
        with open(path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}

        stage_cfg = cfg.get("stage", {})
        camera_cfg = cfg.get("camera", {})
        channels_cfg = cfg.get("channels", {})
        plugins_cfg = cfg.get("plugins", {}) or {}
        ill_cfg = cfg.get("illumination", {}) or {}
        forbidden = [
            ForbiddenProperty(**p)
            for p in cfg.get("forbidden_properties", [])
        ]
        allowed_cfg = cfg.get("allowed_properties")
        allowed = (
            [ForbiddenProperty(**p) for p in allowed_cfg]
            if allowed_cfg is not None
            else None
        )
        return cls(
            stage=StageConstraints(**stage_cfg),
            camera=CameraConstraints(**camera_cfg),
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
                    IlluminationProperty(**s) for s in ill_cfg.get("shutters") or []
                ],
                power_properties=[
                    ForbiddenProperty(**p)
                    for p in ill_cfg.get("power_properties") or []
                ],
                max_power_percent=ill_cfg.get("max_power_percent"),
                max_power_step_factor=ill_cfg.get("max_power_step_factor"),
                require_confirm_on_enable=bool(
                    ill_cfg.get("require_confirm_on_enable", True)
                ),
            ),
            named_stages=[
                NamedStageLimits(**s) for s in cfg.get("named_stages") or []
            ],
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

    def check_xy(self, x: float, y: float) -> None:
        s = self._c.stage
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
        s = self._c.stage
        if s.z_min is not None and z < s.z_min:
            raise SafetyViolation(
                f"Z={z:.1f} µm is below the minimum allowed ({s.z_min:.1f} µm)."
            )
        if s.z_max is not None and z > s.z_max:
            raise SafetyViolation(
                f"Z={z:.1f} µm exceeds the maximum allowed ({s.z_max:.1f} µm)."
            )

    def check_exposure(self, ms: float) -> None:
        limit = self._c.camera.max_exposure_ms
        if limit is not None and ms > limit:
            raise SafetyViolation(
                f"Exposure {ms:.0f} ms exceeds the maximum allowed ({limit:.0f} ms)."
            )

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
                raise SafetyViolation(
                    f"Property '{device}.{prop}' is not in the allowed_properties list."
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
        "PositionZ", ASI/PI names), or relative-move/offset properties. The only
        hard gate for raw property writes is allowlist mode (allowed_properties).
        """
        self.check_property(device, prop)          # denylist/allowlist first
        p = prop.lower()
        try:
            num = float(value)
        except (TypeError, ValueError):
            return                                  # non-numeric; denylist only
        focus = core.get_focus_device()
        cam = core.get_camera_device()
        xy = core.get_xy_stage_device()
        if device == focus and p in self._MOTION_PROPS:
            self.check_z(num)
        elif device == cam and p in self._EXPOSURE_PROPS:
            self.check_exposure(num)
        elif device == xy and p in self._XY_PROPS:
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

    def check_illumination(
        self, core, device: str, prop: str, value: str, confirm_fn=None
    ) -> None:
        """Confirm-gate a shutter enable, and ratchet-gate a power increase.

        Enforced in code, not just the prompt — same reasoning as
        save_knowledge's blocking confirmation: a confused model or an injected
        instruction must not be able to lase without a human 'y'.
        """
        ill = self._c.illumination
        shutter = self.is_illumination_enable(device, prop)
        if shutter and value == shutter.on_value and ill.require_confirm_on_enable:
            if confirm_fn is None or not confirm_fn(
                f"ENABLE ILLUMINATION: {device}.{prop} = {value!r}\n"
                f"This will emit light at the sample."
            ):
                raise SafetyViolation(
                    f"User declined to enable illumination {device}.{prop}."
                )

        if not any(
            p.device == device and p.property == prop for p in ill.power_properties
        ):
            return
        try:
            new = float(value)
        except (TypeError, ValueError):
            return
        if ill.max_power_percent is not None and new > ill.max_power_percent:
            raise SafetyViolation(
                f"{new:.1f}% exceeds illumination.max_power_percent "
                f"({ill.max_power_percent:.1f}%)."
            )
        if ill.max_power_step_factor is not None:
            try:
                old = float(core.get_property(device, prop))
            except (TypeError, ValueError):
                old = 0.0
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
        lim = next(
            (l for l in self._c.named_stages if l.device == device), None
        )
        if lim is None:
            raise SafetyViolation(
                f"No limits configured for stage '{device}'. Add a named_stages "
                f"entry to the safety config before microclaw may move it."
            )
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
        workspace_dir is None the path is returned unchanged — behaviour is
        unchanged unless a lab opts in by configuring a root.
        """
        root = self._c.workspace_dir
        if root is None:
            return path
        root = os.path.realpath(root)
        target = path if os.path.isabs(path) else os.path.join(root, path)
        resolved = os.path.realpath(target)
        if resolved != root and not resolved.startswith(root + os.sep):
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
