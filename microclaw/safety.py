from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
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
class SafetyConstraints:
    stage: StageConstraints = field(default_factory=StageConstraints)
    camera: CameraConstraints = field(default_factory=CameraConstraints)
    allowed_channels: Optional[list[str]] = None  # None means all allowed
    forbidden_properties: list[ForbiddenProperty] = field(default_factory=list)
    plugins: PluginConstraints = field(default_factory=PluginConstraints)

    @classmethod
    def from_yaml(cls, path: str) -> SafetyConstraints:
        with open(path) as f:
            cfg = yaml.safe_load(f) or {}

        stage_cfg = cfg.get("stage", {})
        camera_cfg = cfg.get("camera", {})
        channels_cfg = cfg.get("channels", {})
        plugins_cfg = cfg.get("plugins", {}) or {}
        forbidden = [
            ForbiddenProperty(**p)
            for p in cfg.get("forbidden_properties", [])
        ]
        return cls(
            stage=StageConstraints(**stage_cfg),
            camera=CameraConstraints(**camera_cfg),
            allowed_channels=channels_cfg.get("allowed"),
            forbidden_properties=forbidden,
            plugins=PluginConstraints(
                blocked=plugins_cfg.get("blocked") or [],
                allow_hardware_motion=bool(plugins_cfg.get("allow_hardware_motion", False)),
            ),
        )


class SafetyGuard:
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
        for fp in self._c.forbidden_properties:
            if fp.device == device and fp.property == prop:
                raise SafetyViolation(
                    f"Property '{device}.{prop}' is forbidden by safety config."
                )

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
