from __future__ import annotations
import json
from pathlib import Path
from typing import TYPE_CHECKING

from pycromanager import Core, Studio

if TYPE_CHECKING:
    from microclaw.safety import SafetyGuard


# Micro-Manager plugin access requires the unified SharedPluginClassLoader from
# micro-manager PR #2401 to be handed to the ZMQ server. That is a Java-side MM
# build, not a pip dependency, so it can't be version-locked from Python — we
# probe for it at runtime (PluginAccess._assert_plugin_loader) instead.
#
# Verified against micro-manager PR #2401 ("Studio: load all Micro-Manager
# plugins on one shared classloader..."), merge commit
# ad936ced45f6bcc0b55dad7b2f3c1c2436884f6f, merged 2026-06-25. If these names
# drift, update here + design/09.
_MM_PLUGIN_LOADER_CLASS = (
    "org.micromanager.internal.pluginmanagement.SharedPluginClassLoader"
)
_MM_PLUGIN_LOADER_SINCE = "20260626"  # first MM nightly after the #2401 merge


def _drain_java_iterable(iterable) -> list[str]:
    """Return the string elements of a Java Iterable returned over the bridge.

    Java collections (List, Set, ...) come back as non-iterable Java proxies
    unless the bridge was built with iterate=True (it is not, by default), so we
    drive the Java iterator ourselves — exactly what pyjavaz does internally —
    which works regardless of that flag. An already-materialised Python
    list/tuple/set is passed through.
    """
    if isinstance(iterable, (list, tuple, set)):
        return [str(x) for x in iterable]
    iterator = iterable.iterator()
    has_next = iterator.has_next if hasattr(iterator, "has_next") else iterator.hasNext
    out: list[str] = []
    while has_next():
        out.append(str(iterator.next()))
    return out


def _java_map_keys(java_map) -> list[str]:
    """Return the string keys of a Java Map returned over the pycro-manager bridge."""
    if isinstance(java_map, dict):
        return [str(k) for k in java_map]
    return _drain_java_iterable(java_map.key_set())


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
        out: dict[str, list[str]] = {}
        # Autofocus is selected via the AutofocusManager by *method name*
        # (AutofocusPlugin.getName()), not by the PluginManager's class-name key,
        # so list the names setAutofocusMethodByName accepts — i.e. exactly what
        # MMAutofocusPluginHook / get_autofocus_method consume.
        afm = self._studio.get_autofocus_manager()
        out["autofocus"] = sorted(_drain_java_iterable(afm.get_all_autofocus_methods()))
        # Processor/menu plugins are consumed generically by class name via
        # get_object(classpath), so their class-name keys are the right thing.
        pm = self._studio.plugins()
        for role, getter in (
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
        """Return the active (or named) MM autofocus plugin.

        `plugin_name`, when given, must be an autofocus *method name* as reported
        by list_plugins()['autofocus'] (i.e. AutofocusManager.getAllAutofocusMethods),
        not a plugin class name — setAutofocusMethodByName rejects class names with
        a cryptic Java IllegalArgumentException, so validate first.
        """
        afm = self._studio.get_autofocus_manager()
        if plugin_name:
            valid = _drain_java_iterable(afm.get_all_autofocus_methods())
            if plugin_name not in valid:
                raise ValueError(
                    f"Unknown autofocus method '{plugin_name}'. Valid names: {valid}. "
                    "Use a name from list_mm_plugins()['plugins']['autofocus']."
                )
            afm.set_autofocus_method_by_name(plugin_name)
        return afm.get_autofocus_method()


class MicroscopeController:
    """Thin wrapper around pycro-manager Core and Studio.
    Holds the ZMQ connection; all tool functions go through here."""

    def __init__(self, port: int = 4827, guard: "SafetyGuard | None" = None):
        self._port = port
        self._core = Core(port=port)
        self._studio = Studio(port=port)
        # Optional guard so every stage write funnels through one guarded seam
        # (set_z / set_xy). When present, no caller can skip the numeric guards;
        # when None the controller behaves as before (tool layer still guards).
        self._guard = guard
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

    def get_mm_app_dir(self) -> str | None:
        """Return MM's install root by asking the running ImageJ JVM, or None.

        Micro-Manager is ImageJ1 + plugins under one root; ImageJ knows that
        root. Primary probe is ij.IJ.getDirectory("imagej"), which resolves on
        ANY MM build (no #2401 needed — see design/ij-plugins-spike.py check 3).
        Falls back to the JVM working dir (System user.dir) — MM launches from
        its install root, so that is usually the same path — because the ij.IJ
        probe is not always reliable over the bridge (see _probe_imagej_dir).

        Returns None if not connected or both probes fail, so callers can fall
        back to cache / path guessing. The raw answer is not validated here;
        find_mm_app_dir() sanity-checks it with _looks_like_mm_dir() before
        trusting it, which also guards the weaker user.dir fallback.
        """
        if not self.is_connected():
            return None
        raw = self._probe_imagej_dir() or self._probe_user_dir()
        if not raw:
            return None
        # ImageJ returns a trailing-slash path string; normalise for Path use.
        return str(Path(raw))

    def _probe_imagej_dir(self) -> str | None:
        """ij.IJ.getDirectory("imagej"), tolerant of a flaky JavaClass proxy.

        Over a long-lived, busy bridge JavaClass("ij.IJ") intermittently comes
        back with an incomplete static-method table — the call raises
        AttributeError('...has no attribute get_directory') even though it
        resolves fine on a fresh bridge (observed in the design/12 lab run). So
        we accept either the snake_case or camelCase method name and retry with
        a freshly constructed proxy a few times before giving up.
        """
        from pycromanager import JavaClass
        for _ in range(3):
            try:
                ij = JavaClass("ij.IJ", port=self._port)
                getdir = getattr(ij, "get_directory", None) or getattr(
                    ij, "getDirectory", None
                )
                if getdir is None:
                    continue  # method table not populated this attempt; recreate
                raw = getdir("imagej")
                if raw:
                    return raw
            except Exception:
                continue
        return None

    def _probe_user_dir(self) -> str | None:
        """Secondary probe: the JVM working directory (System user.dir).

        MM launches from its install root, so user.dir is usually the MM app
        dir. A weaker guarantee than the ImageJ call, so it is only used when
        that fails; the _looks_like_mm_dir() check in find_mm_app_dir() rejects
        it if it is not actually an MM root.
        """
        from pycromanager import JavaClass
        try:
            system = JavaClass("java.lang.System", port=self._port)
            getprop = getattr(system, "get_property", None) or getattr(
                system, "getProperty", None
            )
            if getprop is None:
                return None
            return getprop("user.dir") or None
        except Exception:
            return None

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
                # The bridge exposes the axis-count as the raw Java field name
                # `numAxes`; `sp.num_axes` does NOT resolve over pycro-manager and
                # silently drops XY/Z (found via design/11b Spike A).
                n_axes = int(sp.numAxes)
                if n_axes == 2:
                    entry["x_um"] = round(float(sp.x), 3)
                    entry["y_um"] = round(float(sp.y), 3)
                elif n_axes == 1:
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
        """Add or replace a named position in the internal store AND MM's GUI list.

        design/11b Spike A confirmed set_position_list round-trips over the ZMQ
        bridge and repaints MM's Position List Manager immediately, so a marked
        position is visible in the GUI (unlike the jPypeMM Preview canvas).
        """
        entry: dict = {"name": label, "x_um": round(x, 3), "y_um": round(y, 3)}
        if z is not None:
            entry["z_um"] = round(z, 3)
        self._positions = [p for p in self._positions if p["name"] != label]
        self._positions.append(entry)
        self._write_position_to_mm(entry)          # mirror into the GUI list

    def _write_position_to_mm(self, entry: dict) -> None:
        """Mirror a stored position into MM's PositionList so it shows in the GUI.

        StagePosition is a top-level MM2 class built via the static factories the
        bridge names create2_d / create1_d (Spike A). Re-marking a label replaces
        the matching MSP rather than duplicating it, mirroring the internal store.
        """
        from pycromanager import JavaClass, JavaObject
        pm = self._studio.positions()
        plist = pm.get_position_list()
        self._drop_label_from_plist(plist, entry["name"])   # de-dup before re-adding
        msp = JavaObject("org.micromanager.MultiStagePosition", port=self._port)
        msp.set_label(entry["name"])
        sp_cls = JavaClass("org.micromanager.StagePosition", port=self._port)
        if "x_um" in entry and "y_um" in entry:
            msp.add(sp_cls.create2_d(
                self._core.get_xy_stage_device(), entry["x_um"], entry["y_um"]))
        if "z_um" in entry:
            msp.add(sp_cls.create1_d(self._core.get_focus_device(), entry["z_um"]))
        plist.add_position(msp)
        pm.set_position_list(plist)                 # round-trips AND repaints (Spike A)

    @staticmethod
    def _drop_label_from_plist(plist, label: str) -> bool:
        """Remove every MSP with the given label from a Java PositionList in place.

        Returns True if anything was removed. Caller is responsible for the
        set_position_list write-back (so a batch can do a single round trip)."""
        removed = False
        for i in reversed(range(int(plist.get_number_of_positions()))):
            if str(plist.get_position(i).get_label()) == label:
                plist.remove_position(i)
                removed = True
        return removed

    def get_positions(self) -> list[dict]:
        """Return all stored positions."""
        return list(self._positions)

    def set_xy(self, x_um: float, y_um: float) -> None:
        """Guarded XY stage write — the single seam every XY move should use."""
        if self._guard is not None:
            self._guard.check_xy(x_um, y_um)
        self._core.set_xy_position(x_um, y_um)
        self._core.wait_for_device(self._core.get_xy_stage_device())

    def set_z(self, z_um: float) -> None:
        """Guarded focus write — the single seam every Z move should use."""
        if self._guard is not None:
            self._guard.check_z(z_um)
        self._core.set_position(z_um)
        self._core.wait_for_device(self._core.get_focus_device())

    def go_to_position(self, label: str) -> None:
        """Move stage to a named position.

        XY is optional: Z-only entries (from 1-axis MultiStagePositions in MM)
        set only the focus device and never touch XY.
        """
        for pos in self._positions:
            if pos["name"] == label:
                if "x_um" in pos and "y_um" in pos:
                    self.set_xy(pos["x_um"], pos["y_um"])
                if "z_um" in pos:
                    self.set_z(pos["z_um"])
                return
        raise KeyError(f"Position '{label}' not found.")

    def remove_position(self, label: str) -> None:
        """Remove a named position from the internal store AND MM's GUI list."""
        before = len(self._positions)
        self._positions = [p for p in self._positions if p["name"] != label]
        if len(self._positions) == before:
            raise KeyError(f"Position '{label}' not found.")
        self._remove_position_from_mm(label)

    def _remove_position_from_mm(self, label: str) -> None:
        pm = self._studio.positions()
        plist = pm.get_position_list()
        if self._drop_label_from_plist(plist, label):
            pm.set_position_list(plist)             # repaints the GUI list

    def clear_positions(self) -> None:
        """Clear all stored positions from the internal store AND MM's GUI list."""
        self._positions = []
        self._clear_mm_position_list()

    def _clear_mm_position_list(self) -> None:
        pm = self._studio.positions()
        plist = pm.get_position_list()
        n = int(plist.get_number_of_positions())
        if n > 0:
            for i in reversed(range(n)):
                plist.remove_position(i)
            pm.set_position_list(plist)             # repaints the GUI list

    def save_position_list(self, path: str) -> None:
        """Persist the position list to a JSON file."""
        Path(path).write_text(json.dumps(self._positions, indent=2))

    def load_position_list(self, path: str) -> None:
        """Load positions from a JSON file written by save_position_list.

        Rejects a malformed file (hand-edited, wrong schema) up front with a
        ValueError rather than letting a missing key surface as a KeyError deep
        in go_to_position. Z-only entries ({"name", "z_um"}) are valid — they
        come from 1-axis MultiStagePositions in MM.
        """
        data = json.loads(Path(path).read_text())

        def _valid(p) -> bool:
            return isinstance(p, dict) and "name" in p and (
                {"x_um", "y_um"} <= p.keys() or "z_um" in p
            )

        if not isinstance(data, list) or not all(_valid(p) for p in data):
            raise ValueError(f"{path} is not a valid microclaw position list.")
        self._positions = data
