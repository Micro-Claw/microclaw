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
    # None = denylist mode (forbidden_properties). When set, ONLY these
    # (device, property) pairs may be written; everything else is refused. This
    # is the only mode in which raw property writes have a hard gate (see
    # SafetyGuard.check_property and safety_config.yaml).
    allowed_properties: Optional[list[ForbiddenProperty]] = None
    # Filesystem sandbox root for file-touching tools. None = unrestricted
    # (behaviour unchanged); set it to confine reads/writes to one directory.
    workspace_dir: Optional[str] = None
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
