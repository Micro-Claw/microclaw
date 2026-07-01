from __future__ import annotations
import json
from pathlib import Path

from pycromanager import Core, Studio


# Micro-Manager plugin access requires the unified SharedPluginClassLoader from
# micro-manager PR #2401 to be handed to the ZMQ server. That is a Java-side MM
# build, not a pip dependency, so it can't be version-locked from Python — we
# probe for it at runtime (PluginAccess._assert_plugin_loader) instead.
#
# MANUAL VERIFICATION REQUIRED before relying on plugin hooks: open the merged
# #2401 commit, confirm the class/method names below, and fill in the SHA + the
# first MM nightly date that shipped it. Update design/09 if names drift.
_MM_PLUGIN_LOADER_CLASS = (
    "org.micromanager.internal.pluginmgmt.SharedPluginClassLoader"
)
_MM_PLUGIN_LOADER_SINCE = "20260624"  # TODO: MM nightly that first shipped #2401


def _java_map_keys(java_map) -> list[str]:
    """Return the string keys of a Java Map returned over the pycro-manager bridge.

    A returned java.util.HashMap comes back as a non-iterable Java proxy, and its
    keySet() is likewise a Java Set proxy that Python cannot iterate directly
    unless the bridge was built with iterate=True (it is not, by default). So we
    drive the Java iterator ourselves — exactly what pyjavaz does internally —
    which works regardless of that flag. Both representations are handled: a
    Python list/dict (already materialised) or a raw Set proxy.
    """
    if isinstance(java_map, dict):
        return [str(k) for k in java_map]
    key_set = java_map.key_set()
    if isinstance(key_set, (list, tuple, set)):
        return [str(k) for k in key_set]
    iterator = key_set.iterator()
    has_next = iterator.has_next if hasattr(iterator, "has_next") else iterator.hasNext
    keys: list[str] = []
    while has_next():
        keys.append(str(iterator.next()))
    return keys


class PluginAccess:
    """Resolve and call Micro-Manager plugins over the active backend.

    On the pycro-manager backend this is JavaObject/JavaClass over ZMQ; the
    classes are only resolvable once micro-manager#2401 lands (unified
    SharedPluginClassLoader handed to the ZMQ server). On a future jPype backend
    this becomes a direct in-process JVM lookup with the same surface, so hooks
    depend on this seam and never import pycromanager directly.
    """

    def __init__(self, studio, port: int = 4827):
        self._studio = studio
        self._port = port
        self._loader_checked = False

    def _assert_plugin_loader(self) -> None:
        """Fail fast with a clear message if the MM build predates #2401.

        Without this, a pre-#2401 MM raises a cryptic Java ClassNotFoundException
        partway through an acquisition instead of a message the user can act on.
        """
        if self._loader_checked:
            return
        from pycromanager import JavaClass
        try:
            JavaClass(_MM_PLUGIN_LOADER_CLASS, port=self._port)
        except Exception as e:  # only resolves post-#2401
            raise RuntimeError(
                "MM plugin access requires a Micro-Manager build with the unified "
                "plugin classloader (PR #2401, nightly >= "
                f"{_MM_PLUGIN_LOADER_SINCE}). Update Micro-Manager, or don't use "
                "plugin hooks."
            ) from e
        self._loader_checked = True

    def list_plugins(self) -> dict[str, list[str]]:
        """Return installed plugins grouped by role (autofocus/processor/menu).

        Reads studio.plugins() (PluginManager) so the list_mm_plugins tool can
        surface classpaths for a human to review/gate.
        """
        pm = self._studio.plugins()
        out: dict[str, list[str]] = {}
        for role, getter in (
            ("autofocus", pm.get_autofocus_plugins),
            ("processor", pm.get_processor_plugins),
            ("menu", pm.get_menu_plugins),
        ):
            # Deliberately not swallowing errors here: a genuine failure must
            # surface as an error (via the list_mm_plugins tool) rather than
            # masquerade as an empty plugin list.
            out[role] = sorted(_java_map_keys(getter()))
        return out

    def get_object(self, classpath: str, args: list | None = None):
        """Construct an arbitrary plugin object by fully-qualified class name.

        Only reachable post-#2401. Kept here so hooks never import pycromanager.
        """
        self._assert_plugin_loader()
        from pycromanager import JavaObject
        return JavaObject(classpath, args=args or [], port=self._port)

    def get_autofocus_method(self, plugin_name: str | None = None):
        """Return the active (or named) MM autofocus plugin."""
        afm = self._studio.get_autofocus_manager()
        if plugin_name:
            afm.set_autofocus_method_by_name(plugin_name)
        return afm.get_autofocus_method()


class MicroscopeController:
    """Thin wrapper around pycro-manager Core and Studio.
    Holds the ZMQ connection; all tool functions go through here."""

    def __init__(self, port: int = 4827):
        self._port = port
        self._core = Core(port=port)
        self._studio = Studio(port=port)
        self._plugins: PluginAccess | None = None
        # Python-native position store. Use import_from_mm_position_list() to
        # pull in positions the user has marked in MM's GUI.
        self._positions: list[dict] = []

    @property
    def core(self) -> Core:
        return self._core

    @property
    def studio(self) -> Studio:
        return self._studio

    @property
    def plugins(self) -> PluginAccess:
        """Backend seam for Micro-Manager plugin access (see design/09)."""
        if self._plugins is None:
            self._plugins = PluginAccess(self._studio, self._port)
        return self._plugins

    def is_connected(self) -> bool:
        try:
            self._core.get_version_info()
            return True
        except Exception:
            return False

    # --- Position list management ---

    def _read_mm_position_list(self) -> list[dict]:
        """Read positions from MM's GUI position list (read-only Java access)."""
        pl = self._studio.positions().get_position_list()
        out = []
        for i in range(pl.get_number_of_positions()):
            msp = pl.get_position(i)
            entry: dict = {"name": str(msp.get_label())}
            for j in range(msp.size()):
                sp = msp.get(j)
                if sp.num_axes == 2:
                    entry["x_um"] = round(float(sp.x), 3)
                    entry["y_um"] = round(float(sp.y), 3)
                elif sp.num_axes == 1:
                    entry["z_um"] = round(float(sp.x), 3)
            out.append(entry)
        return out

    def import_from_mm_position_list(self) -> list[str]:
        """Copy positions from MM's GUI position list into the internal store.

        Existing entries with the same name are replaced. Returns the list of
        imported position names.
        """
        imported = []
        for pos in self._read_mm_position_list():
            self._positions = [p for p in self._positions if p["name"] != pos["name"]]
            self._positions.append(pos)
            imported.append(pos["name"])
        return imported

    def add_position(self, label: str, x: float, y: float, z: float | None = None) -> None:
        """Add or replace a named position in the internal store."""
        entry: dict = {"name": label, "x_um": round(x, 3), "y_um": round(y, 3)}
        if z is not None:
            entry["z_um"] = round(z, 3)
        self._positions = [p for p in self._positions if p["name"] != label]
        self._positions.append(entry)

    def get_positions(self) -> list[dict]:
        """Return all stored positions."""
        return list(self._positions)

    def go_to_position(self, label: str) -> None:
        """Move stage to a named position."""
        for pos in self._positions:
            if pos["name"] == label:
                self._core.set_xy_position(pos["x_um"], pos["y_um"])
                self._core.wait_for_device(self._core.get_xy_stage_device())
                if "z_um" in pos:
                    self._core.set_position(pos["z_um"])
                    self._core.wait_for_device(self._core.get_focus_device())
                return
        raise KeyError(f"Position '{label}' not found.")

    def remove_position(self, label: str) -> None:
        """Remove a named position."""
        before = len(self._positions)
        self._positions = [p for p in self._positions if p["name"] != label]
        if len(self._positions) == before:
            raise KeyError(f"Position '{label}' not found.")

    def clear_positions(self) -> None:
        """Clear all stored positions."""
        self._positions = []

    def save_position_list(self, path: str) -> None:
        """Persist the position list to a JSON file."""
        Path(path).write_text(json.dumps(self._positions, indent=2))

    def load_position_list(self, path: str) -> None:
        """Load positions from a JSON file written by save_position_list."""
        self._positions = json.loads(Path(path).read_text())
