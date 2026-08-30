from __future__ import annotations
from dataclasses import dataclass
import hashlib
import logging
import math
from pathlib import Path
import threading
import time
from typing import TYPE_CHECKING

from pycromanager import Core, Studio


logger = logging.getLogger(__name__)


# Generic policy defaults. Rig profiles do not describe per-axis repeatability;
# their origins and rationale are frozen in design/35-block56-rig-gate.md.
STAGE_MOVE_RESPONSE_BAND_UM = 2.0
STAGE_MOVE_RESPONSE_FRACTION = 0.1
STAGE_MOVE_TIMEOUT_S = 10.0
STAGE_MOVE_POLL_S = 0.05
STAGE_MOVE_REQUIRED_SAMPLES = 3
STAGE_MOVE_STABILITY_WINDOW_S = 0.1


class StageMoveError(RuntimeError):
    """A stage failed to demonstrate that it reached and settled at its target."""

    def __init__(self, result: dict):
        self.result = result
        super().__init__(
            "Stage move did not demonstrate the requested response: "
            f"started {result['start_um']} um, requested {result['requested_um']} um, measured "
            f"{result['measured_um']} um after {result['elapsed_s']} s "
            f"using {result['tolerance_um']} um from {result['band_source']} policy "
            f"({result['last_device_status']}); "
            + ("the axis did not move." if result['start_um'] == result['measured_um']
               else "hardware error or incomplete motion remains possible.")
        )


def _stage_move_band(target_um: float, start_um: float | None,
                     band_policy: str, configured_band_um: float | None
                     ) -> tuple[float, str, str, bool]:
    if band_policy not in ("relative", "floor"):
        raise ValueError(f"unsupported stage-move band policy {band_policy!r}")
    if configured_band_um is not None:
        band = float(configured_band_um)
        source = "configured"
        kind = "configured_accuracy"
    elif band_policy == "relative" and start_um is not None:
        relative = STAGE_MOVE_RESPONSE_FRACTION * abs(target_um - start_um)
        band = max(STAGE_MOVE_RESPONSE_BAND_UM, relative)
        source = "relative" if relative >= STAGE_MOVE_RESPONSE_BAND_UM else "floor"
        kind = "response"
    else:
        band = STAGE_MOVE_RESPONSE_BAND_UM
        source = "floor"
        kind = "response"
    unverifiable = start_um is None or abs(target_um - start_um) <= band
    return band, source, kind, unverifiable


def stage_move_dispatch_failure(core, device: str, target_um: float,
                                start_um: float | None, band_policy: str,
                                configured_band_um: float | None,
                                exc: Exception) -> StageMoveError:
    """Translate a driver refusal into the same measured move-failure contract."""
    try:
        measured = round(float(core.get_position(device)), 4)
    except Exception:
        measured = None
    try:
        device_state = "busy" if bool(core.device_busy(device)) else "idle"
    except Exception:
        device_state = "unavailable"
    band, source, kind, unverifiable = _stage_move_band(
        target_um, start_um, band_policy, configured_band_um
    )
    residual = None if measured is None else round(abs(measured - target_um), 4)
    return StageMoveError({
        "start_um": None if start_um is None else round(start_um, 4),
        "requested_um": round(target_um, 4),
        "measured_um": measured,
        "arrival_residual_um": residual,
        "tolerance_um": band,
        "band_policy": band_policy,
        "band_source": source,
        "arrival_unverifiable": unverifiable,
        "verification_kind": kind,
        "within_tolerance": False,
        "elapsed_s": 0.0,
        "last_device_status": (
            f"dispatch_error: {type(exc).__name__}: {exc}; {device_state}"
        ),
    })


def settle_stage_move(core, device: str, target_um: float,
                      start_um: float | None, band_policy: str,
                      configured_band_um: float | None) -> dict:
    """Read until a single-axis stage is both near its target and stable."""
    band, source, kind, unverifiable = _stage_move_band(
        target_um, start_um, band_policy, configured_band_um
    )
    started = time.monotonic()
    in_tolerance: list[tuple[float, float]] = []
    measured: float | None = None
    status = "unknown"
    while True:
        now = time.monotonic()
        try:
            status = "busy" if bool(core.device_busy(device)) else "idle"
        except Exception as exc:  # status is evidence, never the success gate
            status = f"unavailable: {type(exc).__name__}"
        try:
            measured = float(core.get_position(device))
            # A non-finite read is a failed read, not a position. Reached via
            # the same handler so there is one policy: NaN never survives into
            # a result dict, where json.dumps would write it as bare `NaN` and
            # any strict reader of the history would reject the line.
            if not math.isfinite(measured):
                raise ValueError(f"non-finite position {measured!r}")
        except Exception as exc:
            in_tolerance.clear()
            status = f"{status}; position_read_error: {type(exc).__name__}: {exc}"
            if now - started >= STAGE_MOVE_TIMEOUT_S:
                raise StageMoveError({
                    "start_um": None if start_um is None else round(start_um, 4),
                    "requested_um": round(target_um, 4),
                    "measured_um": None,
                    "arrival_residual_um": None,
                    "tolerance_um": band,
                    "band_policy": band_policy, "band_source": source,
                    "arrival_unverifiable": unverifiable,
                    "verification_kind": kind,
                    "within_tolerance": False,
                    "elapsed_s": round(now - started, 3),
                    "last_device_status": status,
                }) from exc
            time.sleep(STAGE_MOVE_POLL_S)
            continue
        if abs(measured - target_um) <= band:
            in_tolerance.append((now, measured))
            if len(in_tolerance) > STAGE_MOVE_REQUIRED_SAMPLES:
                in_tolerance.pop(0)
            if (len(in_tolerance) == STAGE_MOVE_REQUIRED_SAMPLES and
                    now - in_tolerance[0][0] >= STAGE_MOVE_STABILITY_WINDOW_S):
                return {
                    "start_um": None if start_um is None else round(start_um, 4),
                    "requested_um": round(target_um, 4),
                    "measured_um": round(measured, 4),
                    "arrival_residual_um": round(abs(measured - target_um), 4),
                    "tolerance_um": band,
                    "band_policy": band_policy, "band_source": source,
                    "arrival_unverifiable": unverifiable,
                    "verification_kind": kind,
                    "within_tolerance": True,
                    "elapsed_s": round(now - started, 3),
                    "last_device_status": status,
                }
        else:
            in_tolerance.clear()
        if now - started >= STAGE_MOVE_TIMEOUT_S:
            result = {
                "start_um": None if start_um is None else round(start_um, 4),
                "requested_um": round(target_um, 4),
                "measured_um": round(measured, 4),
                "arrival_residual_um": round(abs(measured - target_um), 4),
                "tolerance_um": band,
                "band_policy": band_policy, "band_source": source,
                "arrival_unverifiable": unverifiable,
                "verification_kind": kind,
                "within_tolerance": False,
                "elapsed_s": round(now - started, 3),
                "last_device_status": status,
            }
            raise StageMoveError(result)
        time.sleep(STAGE_MOVE_POLL_S)

if TYPE_CHECKING:
    from microclaw.safety import SafetyGuard


@dataclass(frozen=True)
class PositionProjection:
    """Lossless-enough inspection plus the safe microclaw navigation subset."""

    positions: list[dict]
    native_entries: list[dict]
    issues: list[dict]


@dataclass(frozen=True)
class PreparedPositionList:
    """A native list parsed but not yet published to Micro-Manager's GUI."""

    candidate: object
    projection: PositionProjection
    path: str
    content_hash: str


class PositionListConflict(ValueError):
    """A native position list could not be safely prepared or committed."""

    def __init__(self, path: str, issues: list[dict], content_hash: str | None = None):
        super().__init__(f"Position list conflict in {path}.")
        self.path = path
        self.issues = issues
        self.content_hash = content_hash


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


def _new_static_java_class(port: int, classpath: str):
    """Create a JavaClass for static-method access, around a pyjavaz cache bug.

    pyjavaz caches each generated shadow class by the *serialized* Java class
    name (`bridge._class_factory.classes`). For every static JavaClass wrapper
    that name is "java.lang.Class" — pyjavaz marks a call static via
    `_java_class == "java.lang.Class"` (see pyjavaz/bridge.py). So ALL
    static-class shadows collide under one cache key: the first classpath
    wrapped in the process wins, and every later JavaClass(...) returns that
    first class's static methods.

    Symptom (design/12 lab run): on a long-lived bridge JavaClass("ij.IJ") and
    JavaClass("java.lang.System") came back with no static methods at all,
    because another class (StagePosition) had been wrapped first. Isolated, the
    same call worked — ij.IJ was then the first wrapped.

    Fix: evict the colliding key before creating the shadow, forcing pyjavaz to
    regenerate it from THIS class's own serialized methods. The get-class
    round-trip happens on every JavaClass call regardless, so the only added
    cost is regenerating the Python shadow class. EVERY static JavaClass in
    microclaw must go through here — evicting for one call would otherwise leave
    that class cached and break the next site's call.
    """
    try:
        from pyjavaz.bridge import Bridge
        ref = Bridge._cached_bridges_by_port.get(port)
        bridge = ref() if ref is not None else None
        if bridge is not None:
            bridge._class_factory.classes.pop("java.lang.Class", None)
    except Exception:
        # pyjavaz internals moved; fall back to a plain JavaClass. Callers
        # tolerate a missing/wrong method via getattr / try-except.
        pass
    from pycromanager import JavaClass
    return JavaClass(classpath, port=port)


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


def dataset_stack_files(directory) -> list[Path]:
    """The TIFF files a dataset directory would open, in plane order.

    One definition, because the measurement has to describe the files that were
    actually opened. When these two disagreed, `open_artifact` claimed
    `opened: true` for a window whose dimensions it had never checked: the
    directory was measured as an NDTiff dataset while a stray TIFF beside it was
    what got opened (round-3 gate, `D:\\stitch_test`).

    Sorted so a split dataset opens in its own plane order rather than whatever
    order the filesystem happens to return.
    """
    return sorted(p for p in Path(directory).iterdir()
                  if p.is_file() and p.suffix.lower() in (".tif", ".tiff"))


_OPEN_BRIDGE_TIMEOUT_S = 30.0


class _BridgeCallStalled(RuntimeError):
    """An open-path bridge interaction exceeded its watchdog."""


def _bridge_call(label: str, fn, timeout: float | None = None):
    """Run one bridge interaction on a daemon thread with a labelled timeout.

    This makes an otherwise invisible bridge wedge reportable. It cannot cancel
    the Java call: if Java remains stuck, pyjavaz's single lock remains held.

    So a stall is terminal for the session, not a retryable error, and the
    message says so. Once the lock is held every later bridge call — including
    a core call — waits out its own watchdog and fails, which looks like
    microclaw answering while the microscope is unreachable. The operator needs
    to know to restart rather than keep asking.

    Kept even though the Micro-Manager dataset reader that first wedged the
    bridge is gone, because 42a finding 2 scoped its "IJ.open does not stall"
    measurement to the formats it actually tried: a format whose importer raises
    a modal dialog would still hold the lock, and `redirectErrorMessages` only
    covers IJ1's own error path. This is what turns that into a report.
    """
    if timeout is None:
        timeout = _OPEN_BRIDGE_TIMEOUT_S
    result: dict = {}

    def run() -> None:
        try:
            result["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - re-raised on the caller
            result["error"] = exc

    worker = threading.Thread(target=run, name=f"bridge:{label}", daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        raise _BridgeCallStalled(
            f"{label} stalled after the {timeout:g} s watchdog limit. "
            "Micro-Manager is still inside that call and pyjavaz holds one lock "
            "across every round trip, so the bridge is now unusable for this "
            "session: restart microclaw. Nothing was written to Micro-Manager "
            "and no window was closed."
        )
    if "error" in result:
        raise result["error"]
    return result.get("value")


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
        try:
            _new_static_java_class(self._port, _MM_PLUGIN_LOADER_CLASS)
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
        # Populated once by authorization.validate_live_rig.  This is tied to
        # this Core connection, unlike the process-wide EMU discovery cache.
        self._state_device_inventory: dict | None = None
        self._pixel_size_config_inventory: list[dict] | None = None
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

    def refresh_gui(self) -> None:
        """Repaint Micro-Manager's GUI from its current property cache.

        MMCore's cache is already current after a write through the core, while
        a full hardware refresh adds reads that can time out on a flaky serial
        link. Repainting is best-effort and never raises: a GUI failure must not
        turn a successful hardware write into a failed tool call. The failure is
        logged rather than silently discarded so operators and tests can see it;
        logging is itself guarded to keep the no-raise contract unconditional.
        """
        try:
            self.studio.app().refresh_gui_from_cache()
        except Exception:
            try:
                logger.warning("Micro-Manager GUI refresh from cache failed", exc_info=True)
            except Exception:
                pass

    def get_mm_app_dir(self) -> str | None:
        """Return MM's install root by asking the running ImageJ JVM, or None.

        Micro-Manager is ImageJ1 + plugins under one root; ImageJ knows that
        root. Primary probe is ij.IJ.getDirectory("imagej"), which resolves on
        ANY MM build (no #2401 needed — see design/ij-plugins-spike.py check 3).
        Falls back to the JVM working dir (System user.dir) — MM launches from
        its install root, so that is usually the same path.

        Both probes go through _new_static_java_class() to dodge the pyjavaz
        static-class cache collision that otherwise makes them return another
        class's methods (see that helper). Returns None if not connected or both
        probes fail, so callers can fall back to cache / path guessing. The raw
        answer is not validated here; find_mm_app_dir() sanity-checks it with
        _looks_like_mm_dir(), which also guards the weaker user.dir fallback.
        """
        if not self.is_connected():
            return None
        raw = self._probe_imagej_dir() or self._probe_user_dir()
        if not raw:
            return None
        # ImageJ returns a trailing-slash path string; normalise for Path use.
        return str(Path(raw))

    # --- Opening what we wrote, in the windows Micro-Manager already runs ---

    def open_in_imagej(self, path: str) -> dict:
        """Open a file or a saved dataset so the user can see it. Zero exposure.

        **One entry point: `ij.IJ.open` on a file.** A directory is resolved to
        the TIFF files inside it and each is opened the same way, because an
        NDTiff dataset *is* ordinary TIFF stack files plus an `NDTiff.index`
        sidecar. ImageJ reads TIFF natively — no Bio-Formats, and nothing that
        has to understand the dataset format.

        `IJ.open` is deliberately not called on the directory itself: 42a
        measured that as a silent no-op that held the bridge for 6.94 s. IJ1's
        own drag path is not called either — `DragAndDrop.openDirectory` opens a
        modal GenericDialog that would hold the single pyjavaz lock until a human
        answered it, and `FolderOpener.open` stalled the bridge for 120 s on two
        separate gate runs.

        Micro-Manager's dataset reader was the previous directory branch and is
        **removed**, not merely unused. It cannot open what microclaw writes, for
        two independent reasons measured on the rig (see
        design/42-block42b-gate-findings.md):

        * `NDTiffAdapter.hashMapToCoords` casts every axis value to `Integer`, so
          a string-valued axis throws `ClassCastException` during `loadData`;
        * `NDTiffAdapter` indexes coordinates with the **channel** axis stripped
          (`"channel"` is the only string constant in the class), so a dataset
          with no channel axis makes `getImagesIgnoringAxes` return an empty list
          and `.get(0)` throw `IndexOutOfBoundsException` inside
          `DisplayController.create`, under `loadDisplays`.

        The second one hits every single-channel dataset microclaw writes, and
        the crash landed part-way through display construction on the EDT, which
        wedged the bridge for the rest of the session. Reading the TIFFs avoids
        both because it never asks Micro-Manager to interpret the dataset.

        **Known limitation, deliberately accepted:** a dataset whose planes span
        several `*_NDTiffStack*.tif` files opens as several ImageJ windows, and
        the axis structure (channel/z/time names) is not reconstructed — ImageJ
        sees each file's planes as a plain stack. For the single-channel data
        this is used on that is indistinguishable from the ideal. See
        design/42-open-what-we-wrote.md §"Multi-channel datasets" for what a
        proper multi-channel answer would take.

        The path resolves **Java-side**. Microclaw's bridge is localhost-only by
        construction — `Core(port=…)` and `Studio(port=…)` take no host — so
        there is no remote case to detect and the caller's Python-side existence
        check is the whole check.

        Opens a NEW window and leaves it: microclaw never closes or re-uses the
        user's windows, and writes nothing to MM on any exit path. `IJ.open`
        returns void, so it is not proof anything painted (design/18's lesson,
        even though its Preview specifics do not apply) — the window is confirmed
        structurally against `WindowManager` and reports `opened: False` rather
        than claim a window the user cannot see.

        Statics go through `_new_static_java_class` per call (design/12); 42a
        check 2 measured that as hygiene rather than a correctness rule.

        ONE result shape:

            {"opened": bool,
             "via":    str,                  # which mechanism, for the record
             "windows": [{"title", "width", "height", "n_planes"}],  # if opened
             "reason": str}                  # if not

        `n_planes` is ImageJ's stack size for the window.
        """
        try:
            if not self.is_connected():
                return {"opened": False, "reason": "No Micro-Manager bridge connection."}
            if Path(path).is_dir():
                return self._open_dataset_files_in_imagej(path)
            return self._open_file_in_imagej(path)
        except Exception as exc:
            return {"opened": False,
                    "reason": f"{type(exc).__name__}: {exc}"}

    _DATASET_READER = "ij.IJ.open (dataset stack files)"

    def _open_dataset_files_in_imagej(self, path: str) -> dict:
        """Open the TIFFs inside a saved dataset directory."""
        stacks = dataset_stack_files(path)
        if not stacks:
            return {"opened": False, "via": self._DATASET_READER,
                    "reason": (f"No TIFF files in {path}. If this is a folder of "
                               "datasets rather than a dataset, open one of the "
                               "datasets inside it.")}
        windows: list[dict] = []
        failures: list[str] = []
        for stack in stacks:
            result = self._open_file_in_imagej(str(stack))
            if result.get("opened"):
                windows.extend(result.get("windows") or [])
            else:
                failures.append(f"{stack.name}: {result.get('reason', 'unknown')}")
        if not windows:
            return {"opened": False, "via": self._DATASET_READER,
                    "reason": "; ".join(failures) or "No window appeared."}
        # Partial success is still success for the windows that appeared, and the
        # ones that did not are named rather than dropped.
        payload = {"opened": True, "via": self._DATASET_READER,
                   "windows": windows, "n_stack_files": len(stacks)}
        if failures:
            payload["unopened"] = failures
        return payload

    def _imagej_window_ids(self) -> set[int]:
        """The IDs of every open ImageJ image window, read once.

        42a measured the window as visible to WindowManager with no sleep after
        IJ.open returned, so this reads once; there is no polling loop.
        """
        wm = _new_static_java_class(self._port, "ij.WindowManager")
        get_ids = getattr(wm, "get_id_list", None) or getattr(wm, "getIDList", None)
        ids = get_ids() if get_ids is not None else None
        return set() if ids is None else {int(i) for i in ids}

    def drawn_region(self) -> list[int]:
        """Read the current Preview selection's bounding rectangle."""
        image = None
        try:
            display = self._studio.live().get_display()
            if display is not None:
                image = display.get_image_plus()
        except Exception:
            image = None
        if image is None:
            try:
                wm = _new_static_java_class(self._port, "ij.WindowManager")
                get_current = getattr(wm, "get_current_image", None) or getattr(
                    wm, "getCurrentImage", None
                )
                image = get_current() if get_current is not None else None
            except Exception:
                image = None
        if image is None:
            raise ValueError(
                "Region 'drawn' cannot be read because no Preview display is "
                "reachable. Open Preview, draw a selection, and try again."
            )
        roi = image.get_roi()
        if roi is None:
            raise ValueError(
                "Region 'drawn' has no selection. Draw a selection on the "
                "Preview window and try again."
            )
        bounds = roi.get_bounds()
        return [
            int(bounds.x), int(bounds.y), int(bounds.width), int(bounds.height)
        ]

    def _describe_imagej_windows(self, ids: set[int]) -> list[dict]:
        wm = _new_static_java_class(self._port, "ij.WindowManager")
        get_image = getattr(wm, "get_image", None) or getattr(wm, "getImage", None)
        described = []
        for window_id in sorted(ids):
            image = get_image(window_id) if get_image is not None else None
            if image is None:
                described.append({"id": window_id,
                                  "error": "WindowManager has no image for this id"})
                continue
            described.append({
                "id": window_id,
                "title": str(image.get_title()),
                "width": int(image.get_width()),
                "height": int(image.get_height()),
                "n_planes": int(image.get_stack_size()),
            })
        return described

    def _open_file_in_imagej(self, path: str) -> dict:
        ij = _new_static_java_class(self._port, "ij.IJ")
        # One-shot: IJ1's no-argument redirectErrorMessages() applies to the very
        # next error only, so an IJ1 failure during THIS open lands in the Log
        # window instead of a modal dialog holding the single pyjavaz lock —
        # without leaving a global flag flipped in the user's session.
        redirect = getattr(ij, "redirect_error_messages", None) or getattr(
            ij, "redirectErrorMessages", None
        )
        if redirect is not None:
            try:
                redirect()
            except Exception:
                pass
        before = self._imagej_window_ids()
        _bridge_call(
            f"IJ.open({Path(path).name})",
            lambda: _new_static_java_class(self._port, "ij.IJ").open(path),
        )
        new_ids = self._imagej_window_ids() - before
        if not new_ids:
            return {
                "opened": False,
                "via": "ij.IJ.open",
                "reason": (
                    "ImageJ accepted the path but no new image window appeared. "
                    "Check ImageJ's Log window: a format ImageJ cannot read "
                    "natively fails here without raising."
                ),
            }
        return {"opened": True, "via": "ij.IJ.open",
                "windows": self._describe_imagej_windows(new_ids)}

    def _probe_imagej_dir(self) -> str | None:
        """ij.IJ.getDirectory("imagej") — MM's ImageJ install root."""
        try:
            ij = _new_static_java_class(self._port, "ij.IJ")
            getdir = getattr(ij, "get_directory", None) or getattr(
                ij, "getDirectory", None
            )
            if getdir is None:
                return None
            return getdir("imagej") or None
        except Exception:
            return None

    def _probe_user_dir(self) -> str | None:
        """Secondary probe: the JVM working directory (System user.dir).

        MM launches from its install root, so user.dir is usually the MM app
        dir. A weaker guarantee than the ImageJ call, so it is only used when
        that fails; the _looks_like_mm_dir() check in find_mm_app_dir() rejects
        it if it is not actually an MM root.
        """
        try:
            system = _new_static_java_class(self._port, "java.lang.System")
            getprop = getattr(system, "get_property", None) or getattr(
                system, "getProperty", None
            )
            if getprop is None:
                return None
            return getprop("user.dir") or None
        except Exception:
            return None

    # --- Position list management ---

    @staticmethod
    def _position_hash(path: str) -> str:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    @staticmethod
    def _stage_name(sp) -> str:
        # Live-verified on MMCore 12.5.0 (design/31 spike). Keep fallbacks for
        # bridge variants, but prefer the raw Java field that is known to work.
        for attr in ("stageName", "stage_name"):
            try:
                return str(getattr(sp, attr))
            except Exception:
                pass
        for method in ("get_stage_name", "getStageName"):
            try:
                return str(getattr(sp, method)())
            except Exception:
                pass
        return ""

    @staticmethod
    def _msp_default(msp, axis: str) -> str:
        names = (
            ("get_default_xy_stage", "getDefaultXYStage")
            if axis == "xy"
            else ("get_default_z_stage", "getDefaultZStage")
        )
        for name in names:
            try:
                return str(getattr(msp, name)())
            except Exception:
                pass
        return ""

    def _project_mm_position_list(self, plist) -> PositionProjection:
        """Project a native Java PositionList without mutating or publishing it."""
        xy_device = str(self._core.get_xy_stage_device())
        z_device = str(self._core.get_focus_device())
        positions: list[dict] = []
        native_entries: list[dict] = []
        issues: list[dict] = []
        labels: dict[str, list[int]] = {}

        for i in range(int(plist.get_number_of_positions())):
            msp = plist.get_position(i)
            label = str(msp.get_label())
            native: dict = {"index": i, "name": label, "device_positions": []}
            projected: dict = {"name": label}
            default_xy = self._msp_default(msp, "xy")
            default_z = self._msp_default(msp, "z")
            matched = False

            if not label.strip():
                issues.append({
                    "code": "empty_label", "index": i, "label": label,
                    "details": "Position label is empty.",
                    "allowed_resolutions": ["cancel", "remove_entry"],
                })
            labels.setdefault(label, []).append(i)

            for j in range(int(msp.size())):
                sp = msp.get(j)
                n_axes = int(sp.numAxes)
                stage_name = self._stage_name(sp)
                device_entry: dict = {"device": stage_name, "num_axes": n_axes}
                if n_axes == 2:
                    x, y = float(sp.x), float(sp.y)
                    device_entry["position_um"] = [x, y]
                    # Retain canonical coordinates for offline inspection even
                    # when this rig calls the device something else.
                    native.setdefault("x_um", x)
                    native.setdefault("y_um", y)
                    effective = stage_name or default_xy
                    if effective == xy_device:
                        projected["x_um"], projected["y_um"] = x, y
                        matched = True
                elif n_axes == 1:
                    z = float(sp.x)
                    device_entry["position_um"] = [z]
                    native.setdefault("z_um", z)
                    effective = stage_name or default_z
                    if effective == z_device:
                        projected["z_um"] = z
                        matched = True
                else:
                    issues.append({
                        "code": "unsupported_axis_count", "index": i,
                        "label": label, "details": f"Stage {stage_name!r} has {n_axes} axes.",
                        "allowed_resolutions": ["cancel", "remove_entry"],
                    })
                native["device_positions"].append(device_entry)

            native_entries.append(native)
            values = [projected[k] for k in ("x_um", "y_um", "z_um") if k in projected]
            if any(not math.isfinite(v) for v in values):
                issues.append({
                    "code": "non_finite_coordinate", "index": i, "label": label,
                    "details": "Position contains a non-finite coordinate.",
                    "allowed_resolutions": ["cancel", "remove_entry"],
                })
            elif matched:
                positions.append(projected)
            else:
                issues.append({
                    "code": "unsupported_only", "index": i, "label": label,
                    "details": "No stage in this entry matches the configured XY or focus device.",
                    "allowed_resolutions": ["cancel", "preserve_and_omit", "remove_entry"],
                })

        for label, indexes in labels.items():
            if label and len(indexes) > 1:
                issues.append({
                    "code": "duplicate_label", "indexes": indexes, "label": label,
                    "details": f"Position label {label!r} occurs {len(indexes)} times.",
                    "allowed_resolutions": ["cancel", "remove_entry"],
                })
        return PositionProjection(positions, native_entries, issues)

    def _read_mm_position_list(self) -> list[dict]:
        """Read the navigable projection of MM's current GUI position list."""
        pl = self._studio.positions().get_position_list()
        return self._project_mm_position_list(pl).positions

    def project_position_list(self, plist) -> PositionProjection:
        """Public controller seam for projecting an uncommitted native list."""
        return self._project_mm_position_list(plist)

    def prepare_position_list(self, path: str) -> PreparedPositionList:
        """Parse a native file into a temporary Java list without publishing it."""
        from pycromanager import JavaObject

        before = self._position_hash(path)
        candidate = JavaObject("org.micromanager.PositionList", port=self._port)
        try:
            candidate.load(str(path))
        except Exception as e:
            raise ValueError(
                f"{path} is not a native Micro-Manager position list."
            ) from e
        after = self._position_hash(path)
        if before != after:
            raise PositionListConflict(path, [{
                "code": "file_changed_during_load",
                "details": "The position-list file changed while Micro-Manager parsed it.",
                "allowed_resolutions": ["retry", "cancel"],
            }], content_hash=after)
        projection = self._project_mm_position_list(candidate)
        return PreparedPositionList(candidate, projection, str(path), after)

    def project_position_list_file(self, path: str) -> PositionProjection:
        """Parse and project a native file without publishing or caching it."""
        return self.prepare_position_list(path).projection

    def commit_position_list(self, prepared: PreparedPositionList) -> None:
        """Publish an already parsed and validated candidate to MM and Python."""
        self._studio.positions().set_position_list(prepared.candidate)
        self._positions = list(prepared.projection.positions)

    def inspect_current_position_list(self) -> PositionProjection:
        """Project the current native list without changing the Python cache."""
        return self._project_mm_position_list(
            self._studio.positions().get_position_list()
        )

    def set_position_projection(self, projection: PositionProjection) -> None:
        """Publish a projection to the cache after caller-owned validation."""
        if projection.issues:
            raise ValueError("Cannot cache an inconsistent position projection.")
        self._positions = list(projection.positions)

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
        entry: dict = {"name": label, "x_um": float(x), "y_um": float(y)}
        if z is not None:
            entry["z_um"] = float(z)
        self._positions = [p for p in self._positions if p["name"] != label]
        self._positions.append(entry)
        self._write_position_to_mm(entry)          # mirror into the GUI list

    def _write_position_to_mm(self, entry: dict) -> None:
        """Mirror a stored position into MM's PositionList so it shows in the GUI.

        StagePosition is a top-level MM2 class built via the static factories the
        bridge names create2_d / create1_d (Spike A). Re-marking a label replaces
        the matching MSP rather than duplicating it, mirroring the internal store.
        """
        from pycromanager import JavaObject
        pm = self._studio.positions()
        plist = pm.get_position_list()
        self._drop_label_from_plist(plist, entry["name"])   # de-dup before re-adding
        msp = JavaObject("org.micromanager.MultiStagePosition", port=self._port)
        msp.set_label(entry["name"])
        # Static-class access must go through _new_static_java_class (pyjavaz
        # cache collision — see that helper).
        sp_cls = _new_static_java_class(self._port, "org.micromanager.StagePosition")
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

    def set_z(self, z_um: float) -> dict:
        """Guarded focus write — the single seam every Z move should use."""
        if self._guard is not None:
            self._guard.check_z(z_um)
        device = self._core.get_focus_device()
        start_um = float(self._core.get_position(device))
        configured = (
            self._guard.stage_move_tolerance(device, core_focus=True)
            if self._guard is not None else None
        )
        try:
            self._core.set_position(z_um)
        except Exception as exc:
            raise stage_move_dispatch_failure(
                self._core, device, z_um, start_um, "relative", configured, exc
            ) from exc
        return settle_stage_move(
            self._core, device, z_um, start_um, "relative", configured
        )

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

    def remove_native_position(self, label: str) -> None:
        """Remove a conflicted label directly from MM after explicit confirmation."""
        pm = self._studio.positions()
        plist = pm.get_position_list()
        if not self._drop_label_from_plist(plist, label):
            raise KeyError(f"Position '{label}' not found.")
        pm.set_position_list(plist)
        self._positions = [p for p in self._positions if p.get("name") != label]

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

    def save_position_list(self, path: str) -> PositionProjection:
        """Persist Micro-Manager's current native PositionList."""
        plist = self._studio.positions().get_position_list()
        projection = self._project_mm_position_list(plist)
        plist.save(str(path))
        return projection

    def load_position_list(self, path: str) -> None:
        """Load and publish a structurally valid native Micro-Manager list.

        The tool layer uses prepare/commit directly so it can add safety-limit
        validation before publication. This convenience method rejects every
        projection issue and is retained for non-tool callers.
        """
        prepared = self.prepare_position_list(path)
        if prepared.projection.issues:
            raise PositionListConflict(
                prepared.path, prepared.projection.issues, prepared.content_hash
            )
        self.commit_position_list(prepared)
