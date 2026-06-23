"""
spike.py — Verify the remaining jPypeMM + AcqEngJ spike steps (5-8) on Windows.

Context: this validates the assumptions in `port-to-jpype-acqj.md` §10. Steps
1-4 (start_mm launches, snap_core works, dir(core) spellings, AcqEngJ JClass
resolves) were already confirmed; this script re-checks them as prerequisites
and then exercises the parts that need a live JVM:

  5. Engine(core) + a 2-event acquisition writes a dataset via the Java sinks:
       (a) NDTiffAndRAMStorage (confirm constructor args)
       (b) a MM Datastore from studio.data().createMultipageTIFFDatastore(...)
           fed via convertTaggedImage + putImage
       also: probe for a built-in AcqEngJ -> Datastore sink (acqengjcompat).
  6. A Python @JImplements AcquisitionHook is accepted by addHook and its run()
     fires (proves the JPype Python->Java interface + callback threading).
  7. A Python @JImplements TaggedImageProcessor receives images on its own
     thread and forwards them.
  8. The AcquisitionEvent / TaggedImage getters used by the shims really exist
     (getAxisPositions, tag keys, .pix/.tags) — dumped from live objects.

Design: this is a SPIKE. Nothing here is assumed to be correct. Every probe is
wrapped so one failure does not abort the run, and when an "expected" name is
wrong the script dumps the live object's constructors / methods so you can read
off the real API. All important output is written (and flushed) to a .txt file
as it happens, so the log survives even if the JVM/GUI keeps the process alive
at the end.

Usage (in a Windows desktop session, with Micro-Manager 2.0 installed):

    python spike.py --config "C:\\Program Files\\Micro-Manager-2.0\\MMConfig_demo.cfg"

Options:
    --config PATH        MM hardware config to load (recommended: the demo cfg,
                         so a camera/Z-stage exist). Omit to use whatever MM
                         loads on startup.
    --out PATH           Output .txt (default: spike_output_<timestamp>.txt).
    --save-dir PATH      Where acquisitions write (default: ./spike_data).
    --jpypemm-path PATH  Path to a jPypeMM checkout (Strategy B). Falls back to
                         $JPYPEMM_PATH, then ./third_party/jPypeMM, then assumes
                         `import start_mm` already works.
"""

from __future__ import annotations

import argparse
import datetime
import os
import platform
import sys
import threading
import traceback


# ---------------------------------------------------------------------------
# Logging: tee to stdout + file, flushing every write so the .txt is complete
# even if the process is held open by the Swing GUI at the end.
# ---------------------------------------------------------------------------
class Tee:
    def __init__(self, path):
        self._f = open(path, "w", encoding="utf-8")
        self.path = path

    def __call__(self, *parts):
        line = " ".join(str(p) for p in parts)
        print(line)
        self._f.write(line + "\n")
        self._f.flush()

    def rule(self, title=""):
        self("\n" + "=" * 78)
        if title:
            self(title)
            self("=" * 78)

    def close(self):
        self._f.close()


# Result tracking: list of (step, status, detail)
RESULTS: list[tuple[str, str, str]] = []


def record(step, status, detail=""):
    RESULTS.append((step, status, detail))


def probe(log, step, desc, fn):
    """Run fn(), logging PASS/FAIL and the full traceback on failure.
    Returns (ok, value)."""
    log.rule(f"[{step}] {desc}")
    try:
        value = fn()
        log(f"--> PASS: {step}")
        record(step, "PASS", desc)
        return True, value
    except Exception as e:  # noqa: BLE001 - spike: capture everything
        log(f"--> FAIL: {step}: {type(e).__name__}: {e}")
        log(traceback.format_exc())
        record(step, "FAIL", f"{type(e).__name__}: {e}")
        return False, None


def skip(log, step, desc, reason):
    log.rule(f"[{step}] {desc}")
    log(f"--> SKIP: {step}: {reason}")
    record(step, "SKIP", reason)


def dump_dir(log, obj, label, only=None):
    """Dump non-dunder attributes of a JPype/Java object. `only` is an optional
    list of substrings to filter by (case-insensitive)."""
    try:
        names = [n for n in dir(obj) if not n.startswith("__")]
        if only:
            low = [s.lower() for s in only]
            names = [n for n in names if any(s in n.lower() for s in low)]
        log(f"  dir({label}) [{len(names)} names]:")
        # wrap into lines of a few names for readability
        for i in range(0, len(names), 6):
            log("    " + ", ".join(names[i:i + 6]))
    except Exception as e:  # noqa: BLE001
        log(f"  (could not dir({label}): {e})")


def dump_java_signatures(log, jclass, label):
    """Dump constructor + method signatures of a Java class via reflection.
    `jclass` is a JPype class object (e.g. JClass('...'))."""
    try:
        kls = jclass.class_  # java.lang.Class
        log(f"  constructors of {label}:")
        for ctor in kls.getConstructors():
            log(f"    {ctor.toString()}")
        log(f"  methods of {label} (declared):")
        for m in kls.getDeclaredMethods():
            log(f"    {m.toString()}")
    except Exception as e:  # noqa: BLE001
        log(f"  (could not reflect {label}: {e})")


# ---------------------------------------------------------------------------
# jPypeMM bootstrap (Strategy B): put a jPypeMM checkout on sys.path.
# ---------------------------------------------------------------------------
def bootstrap_jpypemm(log, explicit_path):
    candidates = []
    if explicit_path:
        candidates.append(explicit_path)
    if os.environ.get("JPYPEMM_PATH"):
        candidates.append(os.environ["JPYPEMM_PATH"])
    here = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(here, "third_party", "jPypeMM"))
    for c in candidates:
        if c and os.path.isdir(c):
            abs_c = os.path.abspath(c)
            if abs_c not in sys.path:
                sys.path.insert(0, abs_c)
            log(f"jPypeMM path added to sys.path: {abs_c}")
            return
    log("No jPypeMM checkout found on the candidate paths; relying on an "
        "existing `import start_mm` (Strategy A).")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="jPypeMM + AcqEngJ spike (steps 5-8).")
    parser.add_argument("--config", default=None, help="MM hardware config to load.")
    parser.add_argument("--out", default=None, help="Output .txt path.")
    parser.add_argument("--save-dir", default=None, help="Acquisition output dir.")
    parser.add_argument("--jpypemm-path", default=None, help="Path to a jPypeMM checkout.")
    args = parser.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = args.out or os.path.join(here, f"spike_output_{stamp}.txt")
    # Per-run dir (timestamped) — the MM Datastore factories refuse to write into
    # a path that already holds data, so each run needs a fresh directory.
    save_dir = args.save_dir or os.path.join(here, f"spike_data_{stamp}")
    os.makedirs(save_dir, exist_ok=True)

    log = Tee(out_path)
    log(f"jPypeMM + AcqEngJ spike — {datetime.datetime.now().isoformat()}")
    log(f"output file : {out_path}")
    log(f"save dir    : {save_dir}")
    log(f"python      : {sys.version.split()[0]}  ({platform.platform()})")

    # ---- imports ----------------------------------------------------------
    bootstrap_jpypemm(log, args.jpypemm_path)
    try:
        import numpy as np
        import jpype
        from jpype import JClass, JImplements, JOverride  # noqa: F401
        import start_mm
        log(f"jpype       : {jpype.__version__}")
    except Exception as e:  # noqa: BLE001
        log(f"FATAL: could not import dependencies: {type(e).__name__}: {e}")
        log(traceback.format_exc())
        log.close()
        sys.exit(1)

    ACQENG = "org.micromanager.acqj"

    # ---- prerequisite steps 1, 2, 4 --------------------------------------
    ok, sc = probe(log, "1", "start_mm.main(skip_intro=True) launches MM",
                   lambda: start_mm.main(skip_intro=True))
    if not ok:
        log("Cannot continue without studio/core. Aborting.")
        finish(log)
        return
    studio, core = sc
    log(f"  MMCore version: {core.getVersionInfo()}")

    if args.config:
        probe(log, "1b", f"loadSystemConfiguration({args.config})",
              lambda: core.loadSystemConfiguration(args.config))
    cam = str(core.getCameraDevice())
    have_camera = bool(cam)
    log(f"  camera device : {cam!r}")
    log(f"  focus device  : {core.getFocusDevice()!r}")
    if not have_camera:
        log("")
        log("  " + "!" * 70)
        log("  WARNING: no camera is loaded — MM started with no hardware config.")
        log("  The image/acquisition steps (2, 5a, 5b, 6, 7) CANNOT run and will")
        log("  be skipped. Re-run with, e.g.:")
        log('    python spike.py --config "C:\\Program Files\\Micro-Manager-2.0\\MMConfig_demo.cfg"')
        log("  " + "!" * 70)
        log("")
        record("camera", "WARN", "no camera loaded — pass --config")
    else:
        try:
            core.setExposure(10.0)
            log("  exposure set to 10 ms")
        except Exception as e:  # noqa: BLE001
            log(f"  (could not set exposure: {e})")

    def _snap():
        img = np.asarray(start_mm.snap_core(core))
        log(f"  snapped array: shape={img.shape} dtype={img.dtype}")
        return img
    if have_camera:
        probe(log, "2", "start_mm.snap_core(core) -> numpy", _snap)
    else:
        skip(log, "2", "start_mm.snap_core(core) -> numpy", "no camera loaded")

    # Resolve the AcqEngJ classes (step 4 + the rest depend on these).
    classes = {}

    def _resolve():
        names = {
            "Engine": f"{ACQENG}.internal.Engine",
            "Acquisition": f"{ACQENG}.main.Acquisition",
            "AcquisitionEvent": f"{ACQENG}.main.AcquisitionEvent",
            "AcquisitionAPI": f"{ACQENG}.api.AcquisitionAPI",
            "AcquisitionHook": f"{ACQENG}.api.AcquisitionHook",
            "TaggedImageProcessor": f"{ACQENG}.api.TaggedImageProcessor",
            "AcqEngJDataSink": f"{ACQENG}.api.AcqEngJDataSink",
        }
        for short, fq in names.items():
            classes[short] = JClass(fq)
            log(f"  resolved {short} -> {fq}")
        return classes
    ok, _ = probe(log, "4", "JClass(...) resolves the AcqEngJ classes", _resolve)
    if not ok:
        log("AcqEngJ classes did not resolve — steps 5-8 cannot run. See §1.5 "
            "(drop the jars into MM plugins/).")
        finish(log)
        return

    # Dump the hook-point constants and key signatures (informational).
    log.rule("[info] AcquisitionAPI hook constants")
    for const in ("BEFORE_HARDWARE_HOOK", "BEFORE_Z_DRIVE_HOOK",
                  "AFTER_HARDWARE_HOOK", "AFTER_CAMERA_HOOK", "AFTER_EXPOSURE_HOOK"):
        try:
            log(f"  {const} = {getattr(classes['AcquisitionAPI'], const)}")
        except Exception as e:  # noqa: BLE001
            log(f"  {const}: (not found: {e})")
    AFTER_HARDWARE = _const(classes["AcquisitionAPI"], "AFTER_HARDWARE_HOOK", 3)

    log.rule("[info] reflected signatures for the classes the port wires")
    dump_java_signatures(log, classes["Acquisition"], "Acquisition")
    dump_java_signatures(log, classes["AcquisitionEvent"], "AcquisitionEvent")
    dump_java_signatures(log, classes["AcqEngJDataSink"], "AcqEngJDataSink")
    dump_java_signatures(log, classes["AcquisitionHook"], "AcquisitionHook")
    dump_java_signatures(log, classes["TaggedImageProcessor"], "TaggedImageProcessor")
    try:
        ndt = JClass("org.micromanager.ndtiffstorage.NDTiffAndRAMStorage")
        classes["NDTiffAndRAMStorage"] = ndt
        dump_java_signatures(log, ndt, "NDTiffAndRAMStorage")
    except Exception as e:  # noqa: BLE001
        log(f"  NDTiffAndRAMStorage did not resolve: {e}")
    # MM's built-in AcqEngJ -> Datastore sink (found in a prior run). Reflect its
    # constructor/methods so we can wire it instead of the hand-rolled sink.
    try:
        mda = JClass("org.micromanager.acquisition.internal.acqengjcompat.AcqEngJMDADataSink")
        classes["AcqEngJMDADataSink"] = mda
        dump_java_signatures(log, mda, "AcqEngJMDADataSink")
    except Exception as e:  # noqa: BLE001
        log(f"  AcqEngJMDADataSink did not resolve: {e}")

    # DataManager + Image API — needed to get OME-TIFF saving right (convertTaggedImage
    # overloads, coordsBuilder/summaryMetadataBuilder/metadataBuilder, Image.copyAtCoords).
    log.rule("[info] DataManager / Image methods")
    try:
        dm = studio.data()
        for m in dm.getClass().getMethods():
            s = str(m)
            if any(k in s for k in ("convertTaggedImage", "Builder", "createMultipage",
                                    "createNDTIFF", "coords", "metadata")):
                log(f"    {s}")
    except Exception as e:  # noqa: BLE001
        log(f"  (could not reflect DataManager: {e})")
    if have_camera:
        try:
            core.snapImage()
            ti = core.getTaggedImage()
            img = studio.data().convertTaggedImage(ti)
            log("  Image methods (copy*/coords/metadata):")
            for m in img.getClass().getMethods():
                s = str(m)
                if any(k in s for k in ("copy", "Coords", "Metadata")):
                    log(f"    {s}")
        except Exception as e:  # noqa: BLE001
            log(f"  (could not reflect Image: {e})")

    # ---- step 5 engine singleton -----------------------------------------
    def _engine():
        Engine = classes["Engine"]
        if Engine.getInstance() is None:
            Engine(core)
        inst = Engine.getInstance()
        assert inst is not None, "Engine.getInstance() is None after construction"
        return inst
    probe(log, "5-engine", "Engine(core) singleton created", _engine)

    # ---- build the JPype proxy classes (post-JVM, post-resolve) ----------
    # These must be defined after the JVM is up and the interfaces resolve,
    # because @JImplements binds the interface at class-creation time. Wrapped in
    # probe() so a decoration failure lands in the .txt (not just the console).
    ok, proxies = probe(log, "proxies",
                        "@JImplements proxy classes build (Hook/Processor/Sink/Iterator)",
                        lambda: build_proxies(jpype, np, classes, AFTER_HARDWARE, log))
    if not ok:
        log("Proxy classes failed to build — cannot wire AcqEngJ. Aborting runs.")
        finish(log)
        return

    # ---- step 8 (event introspection): dump the live AcquisitionEvent API --
    # Build a throwaway Acquisition just to construct an event for inspection,
    # then dump its attributes. (Live getter *values* are also dumped from a
    # real engine-created event inside run1's hook.run, below.)
    def _event_api():
        tmp_acq = classes["Acquisition"](proxies["RecordingSink"](log))
        ev = make_event(jpype, classes, tmp_acq, {"axes": {"time": 0}})
        dump_dir(log, ev, "AcquisitionEvent", only=["axis", "index", "get", "z",
                                                     "config", "exposure", "acquire"])
        for getter in ("getAxisPositions", "getZIndex", "getTIndex",
                       "getConfigPreset", "getConfigGroup", "getExposure"):
            try:
                log(f"    {getter}() -> {getattr(ev, getter)()!r}")
            except Exception as e:  # noqa: BLE001
                log(f"    {getter}: (n/a: {e})")
        try:
            tmp_acq.abort()  # discard the throwaway acquisition
        except Exception:  # noqa: BLE001
            pass
        return ev
    probe(log, "8-event", "AcquisitionEvent getters exist / inspect", _event_api)

    if not have_camera:
        skip(log, "6+7", "hook + processor + sink acquisition", "no camera loaded")
        skip(log, "5a", "NDTiffAndRAMStorage sink", "no camera loaded")
        skip(log, "5b", "MM Datastore OME-TIFF sink", "no camera loaded")
        finish(log)
        return

    # ---- run 1: custom Python sink + hook + processor (steps 6, 7, 8) -----
    probe(log, "6+7", "Acquisition with AcquisitionHook + TaggedImageProcessor "
                      "+ Python AcqEngJDataSink fires hook, processor, putImage",
          lambda: run_acquisition(
              jpype, np, classes, proxies, core, AFTER_HARDWARE, log,
              label="run1-python-sink",
              sink=proxies["RecordingSink"](log),
              with_hook=True, with_processor=True))

    # ---- run 2: NDTiff via MM Datastore (step 5a) ------------------------
    # NDTiffAndRAMStorage is absent on this build, so NDTiff goes through the MM
    # DataManager (createNDTIFFDatastore). Keep the proven "simple" put mode.
    nd_dir = os.path.join(save_dir, "ndtiff")
    probe(log, "5a", "MM Datastore (createNDTIFFDatastore) writes NDTiff",
          lambda: run_acquisition(
              jpype, np, classes, proxies, core, AFTER_HARDWARE, log,
              label="run2-ndtiff",
              sink=proxies["DatastoreSink"](studio, nd_dir, "spike_nd", log,
                                            fmt="ndtiff", put_mode="simple",
                                            set_summary=False),
              with_hook=False, with_processor=False))
    log(f"  inspect on disk: {nd_dir}")

    # ---- step 5b: OME-TIFF EXPERIMENTS -----------------------------------
    # The minimal sink NPE'd writing multipage TIFF (null Metadata) and then hit a
    # rewrite collision. Try several combinations and report which one writes both
    # images. Each variant gets a fresh dataset dir (the TIFF factory refuses an
    # existing path). Variants:
    #   i   : copyAtCoords put + summary metadata + combined metadata file
    #   ii  : copyAtCoords put + summary metadata + separate metadata file
    #   iii : simple put (convertTaggedImage(t, coords, None)) + summary metadata
    ome_variants = [
        ("5b-i",   dict(put_mode="copy_coords", set_summary=True,  separate_metadata=False)),
        ("5b-ii",  dict(put_mode="copy_coords", set_summary=True,  separate_metadata=True)),
        ("5b-iii", dict(put_mode="simple",      set_summary=True,  separate_metadata=False)),
    ]
    for vid, opts in ome_variants:
        vdir = os.path.join(save_dir, f"ometiff_{vid}")
        probe(log, vid, f"OME-TIFF write — {opts}",
              lambda vdir=vdir, opts=opts: run_acquisition(
                  jpype, np, classes, proxies, core, AFTER_HARDWARE, log,
                  label=f"run3-{vid}",
                  sink=proxies["DatastoreSink"](studio, vdir, "spike_ome", log,
                                                fmt="ometiff", **opts),
                  with_hook=False, with_processor=False))
        log(f"  inspect on disk: {vdir}")

    # ---- step 5 (built-in sink probe): does MM ship an AcqEngJ->Datastore? -
    log.rule("[5-builtin] probe for a built-in AcqEngJ -> Datastore sink")
    for cand in (
        "org.micromanager.acquisition.internal.acqengjcompat.AcqEngJDataSinkAdapter",
        "org.micromanager.acquisition.internal.acqengjcompat.AcqEngJMDADataSink",
        "org.micromanager.acquisition.internal.DefaultAcqEngJDataSink",
        "org.micromanager.remote.RemoteStorage",
    ):
        try:
            JClass(cand)
            log(f"  FOUND: {cand}")
        except Exception:  # noqa: BLE001
            log(f"  (absent: {cand})")

    finish(log)


def _const(jclass, name, default):
    try:
        return int(getattr(jclass, name))
    except Exception:  # noqa: BLE001
        return default


# ---------------------------------------------------------------------------
# Event construction (mirrors acquisition.py _dict_to_event in the plan).
# Defensive: only calls setters that exist on this AcqEngJ version.
# ---------------------------------------------------------------------------
def make_event(jpype, classes, acq, d):
    # AcquisitionEvent(AcquisitionAPI acq) — acq must be a real Acquisition.
    Event = classes["AcquisitionEvent"]
    ev = Event(acq)
    Integer = jpype.java.lang.Integer
    Double = jpype.java.lang.Double
    Long = jpype.java.lang.Long

    def _try(call_desc, fn):
        try:
            fn()
        except Exception:  # noqa: BLE001
            pass  # logged elsewhere via dump; keep event construction resilient

    if d.get("z_um") is not None and hasattr(ev, "setZ"):
        _try("setZ", lambda: ev.setZ(Integer(int(d.get("z_index", 0))),
                                     Double(float(d["z_um"]))))
    if "channel" in d:
        if hasattr(ev, "setConfigGroup"):
            _try("setConfigGroup", lambda: ev.setConfigGroup(d["channel"]["group"]))
        if hasattr(ev, "setConfigPreset"):
            _try("setConfigPreset", lambda: ev.setConfigPreset(d["channel"]["config"]))
    if "exposure" in d and hasattr(ev, "setExposure"):
        _try("setExposure", lambda: ev.setExposure(float(d["exposure"])))
    if "min_start_time_ms" in d and hasattr(ev, "setMinimumStartTime"):
        _try("setMinimumStartTime",
             lambda: ev.setMinimumStartTime(Long(int(d["min_start_time_ms"]))))
    for axis, idx in d.get("axes", {}).items():
        if axis == "time" and hasattr(ev, "setTimeIndex"):
            # AcqEngJ has a dedicated setTimeIndex(int) (confirmed in reflection).
            _try("setTimeIndex", lambda i=idx: ev.setTimeIndex(int(i)))
        elif axis not in ("z", "channel") and hasattr(ev, "setAxisPosition"):
            _try("setAxisPosition",
                 lambda a=axis, i=idx: ev.setAxisPosition(a, Integer(int(i))))
    # NOTE: AcqEngJ has no setAcquireImage — `shouldAcquireImage()` is read-only
    # and the engine decides based on the event. A plain event acquires one image.
    return ev


# ---------------------------------------------------------------------------
# Build the JPype @JImplements proxy classes. Defined here (called after the
# JVM is up) so the interfaces resolve at decoration time.
# ---------------------------------------------------------------------------
def build_proxies(jpype, np, classes, after_hardware, log):
    from jpype import JImplements, JOverride
    ACQENG = "org.micromanager.acqj"

    def tagged_to_numpy(tagged):
        tags = tagged.tags
        w = int(tags.getInt("Width"))
        h = int(tags.getInt("Height"))
        arr = np.asarray(tagged.pix[:])
        # Java has no unsigned types: a 16-bit camera buffer arrives as short[]
        # (-> int16); reinterpret the same bits as uint16. byte[] -> uint8.
        if arr.dtype == np.int16:
            arr = arr.view(np.uint16)
        elif arr.dtype == np.int8:
            arr = arr.view(np.uint8)
        return arr.reshape(h, w)

    @JImplements(f"{ACQENG}.api.AcquisitionHook")
    class _Hook:
        def __init__(self, log):
            self._log = log
            self.calls = 0
            self._dumped = False

        @JOverride
        def run(self, event):
            self.calls += 1
            if not self._dumped:
                self._log(f"  [hook.run] fired; event type={type(event)}")
                try:
                    self._log(f"  [hook.run] getAxisPositions() -> "
                              f"{event.getAxisPositions()!r}")
                except Exception as e:  # noqa: BLE001
                    self._log(f"  [hook.run] getAxisPositions n/a: {e}")
                self._dumped = True
            return event

        @JOverride
        def close(self):
            self._log(f"  [hook.close] total run() calls = {self.calls}")

    @JImplements(f"{ACQENG}.api.TaggedImageProcessor")
    class _Processor:
        def __init__(self, log):
            self._log = log
            self.received = 0
            self._acq = self._source = self._sink = None

        def _start(self, acq, source, sink):
            self._acq, self._source, self._sink = acq, source, sink
            threading.Thread(target=self._loop, name="spike-imgproc",
                             daemon=True).start()

        @JOverride
        def setAcqAndQueues(self, acq, source, sink):
            self._start(acq, source, sink)

        @JOverride
        def setAcqAndDequeues(self, acq, source, sink):
            # Deprecated variant; JPype requires every abstract method to be
            # overridden. LinkedBlockingDeque is a BlockingQueue, so _loop works.
            self._start(acq, source, sink)

        def _loop(self):
            if not jpype.isThreadAttachedToJVM():
                jpype.attachThreadToJVM()
            while True:
                try:
                    tagged = self._source.take()
                except Exception as e:  # noqa: BLE001
                    self._log(f"  [processor] source.take() error: {e}")
                    return
                if tagged is None or tagged.tags is None:
                    self._sink.put(tagged)  # forward poison/end sentinel
                    self._log(f"  [processor] end-of-stream; received={self.received}")
                    return
                self.received += 1
                if self.received == 1:
                    try:
                        img = tagged_to_numpy(tagged)
                        self._log(f"  [processor] first image: shape={img.shape} "
                                  f"dtype={img.dtype}")
                        # mmcorej JSONObject has no keySet(); toString() dumps all
                        # keys+values so we can finalize _tags_to_dict.
                        self._log(f"  [processor] tags JSON: {tagged.tags.toString()}")
                    except Exception as e:  # noqa: BLE001
                        self._log(f"  [processor] image/tag inspect failed: {e}")
                self._sink.put(tagged)

    @JImplements(f"{ACQENG}.api.AcqEngJDataSink")
    class _RecordingSink:
        """Pure-Python sink that just records putImage calls — proves Java->Python
        image delivery and lets us inspect TaggedImage (step 8)."""
        def __init__(self, log):
            self._log = log
            self.count = 0
            self._finished = False

        @JOverride
        def initialize(self, acq, summary_metadata):
            self._log("  [sink.initialize] called")

        @JOverride
        def putImage(self, tagged):
            self.count += 1
            if self.count == 1:
                try:
                    tags = tagged.tags
                    self._log(f"  [sink.putImage] first image; "
                              f"Width={tags.getInt('Width')} Height={tags.getInt('Height')}")
                    self._log(f"  [sink.putImage] pix type={type(tagged.pix)}")
                except Exception as e:  # noqa: BLE001
                    self._log(f"  [sink.putImage] inspect failed: {e}")
            return None

        @JOverride
        def anythingAcquired(self):
            return self.count > 0

        @JOverride
        def isFinished(self):
            return self._finished

        @JOverride
        def finish(self):
            self._finished = True
            self._log(f"  [sink.finish] total images = {self.count}")

    @JImplements(f"{ACQENG}.api.AcqEngJDataSink")
    class _DatastoreSink:
        """Forwards images into a MM Datastore (written Java-side). `fmt` selects
        the DataManager factory: 'ometiff' -> createMultipageTIFFDatastore,
        'ndtiff' -> createNDTIFFDatastore."""
        def __init__(self, studio, directory, name, log, fmt="ometiff",
                     separate_metadata=False, put_mode="copy_coords",
                     set_summary=True):
            self._studio = studio
            self._log = log
            self._finished = False
            self.count = 0
            self._fmt = fmt
            self._separate_metadata = separate_metadata  # multipage-TIFF flag
            self._put_mode = put_mode                    # "copy_coords" | "simple"
            self._set_summary = set_summary
            os.makedirs(directory, exist_ok=True)
            self._path = os.path.join(directory, name)
            self._ds = None
            # NOTE: build the Datastore in initialize() (called once), not here —
            # JPype may instantiate the proxy twice and the TIFF factory refuses
            # an existing path.

        @JOverride
        def initialize(self, acq, summary_metadata):
            dm = self._studio.data()
            if self._fmt == "ndtiff":
                self._ds = dm.createNDTIFFDatastore(self._path)
            else:
                self._ds = dm.createMultipageTIFFDatastore(
                    self._path, self._separate_metadata, False)
            self._log(f"  [datastore:{self._fmt}] created at {self._path} "
                      f"(separate_metadata={self._separate_metadata}, "
                      f"put_mode={self._put_mode}, set_summary={self._set_summary})")
            # EXPERIMENT: the multipage-TIFF storage may need a non-null
            # SummaryMetadata before putImage. Datastore.setSummaryMetadata wants a
            # SummaryMetadata object (NOT the AcqEngJ JSONObject), so build a
            # minimal one via the DataManager builder.
            if self._set_summary:
                try:
                    sm = dm.summaryMetadataBuilder().build()
                    self._ds.setSummaryMetadata(sm)
                    self._log("  [datastore] summary metadata set OK")
                except Exception as e:  # noqa: BLE001
                    self._log(f"  [datastore] setSummaryMetadata FAILED: "
                              f"{type(e).__name__}: {e}")

        @JOverride
        def putImage(self, tagged):
            dm = self._studio.data()
            coords = dm.coordsBuilder().t(self.count).build()
            try:
                if self._put_mode == "copy_coords":
                    # 1-arg convert builds an Image WITH metadata (tag-derived),
                    # then relocate to a distinct Coords (preserves pixels+metadata).
                    # This fixes the multipage-TIFF NPE (null Metadata) AND the
                    # all-(0,0,0,0)-coords rewrite collision in one shot.
                    img = dm.convertTaggedImage(tagged)
                    img = img.copyAtCoords(coords)
                else:  # "simple" — proven to work for NDTiff
                    img = dm.convertTaggedImage(tagged, coords, None)
                self._ds.putImage(img)
                self.count += 1
            except Exception as e:  # noqa: BLE001
                self._log(f"  [datastore.putImage:{self._put_mode}] FAILED: "
                          f"{type(e).__name__}: {e}")
            return None

        @JOverride
        def anythingAcquired(self):
            return self.count > 0

        @JOverride
        def isFinished(self):
            return self._finished

        @JOverride
        def finish(self):
            try:
                self._ds.freeze()
            except Exception as e:  # noqa: BLE001
                self._log(f"  [datastore.finish] freeze n/a: {e}")
            self._finished = True
            self._log(f"  [datastore.finish] images written = {self.count}")

    @JImplements("java.util.Iterator")
    class _EventIterator:
        def __init__(self, acq, events):
            self._acq = acq
            self._it = iter(events)
            self._next = self._advance()

        def _advance(self):
            try:
                return next(self._it)
            except StopIteration:
                return None

        @JOverride
        def hasNext(self):
            return self._next is not None

        @JOverride
        def next(self):
            d, self._next = self._next, self._advance()
            return make_event(jpype, classes, self._acq, d)

    return {
        "Hook": _Hook,
        "Processor": _Processor,
        "RecordingSink": _RecordingSink,
        "DatastoreSink": _DatastoreSink,
        "EventIterator": _EventIterator,
    }


def run_acquisition(jpype, np, classes, proxies, core, after_hardware, log,
                    *, label, sink, with_hook, with_processor, timeout_s=60):
    """Wire Engine -> Acquisition(sink) [+ hook +processor], submit 2 events,
    finish and wait. Returns a short summary string. waitForCompletion is run on
    a watchdog thread so a stalled engine aborts instead of freezing the run."""
    log(f"  --- acquisition: {label} ---")
    Acquisition = classes["Acquisition"]
    acq = Acquisition(sink)

    hook = proc = None
    if with_hook:
        hook = proxies["Hook"](log)
        acq.addHook(hook, after_hardware)
        log("  hook added at AFTER_HARDWARE_HOOK")
    if with_processor:
        proc = proxies["Processor"](log)
        acq.addImageProcessor(proc)
        log("  image processor added")

    # Two trivial time-point events.
    events = [{"axes": {"time": 0}}, {"axes": {"time": 1}}]
    it = proxies["EventIterator"](acq, events)

    # Some AcqEngJ versions need start() before submitting; harmless to attempt.
    try:
        acq.start()
        log("  acq.start() called")
    except Exception as e:  # noqa: BLE001
        log(f"  acq.start() skipped/failed (often fine): {e}")

    acq.submitEventIterator(it)
    log(f"  submitEventIterator returned; finish + waiting (timeout={timeout_s}s)...")
    acq.finish()

    # Watchdog: waitForCompletion on a side thread so a stalled engine can't
    # freeze the whole spike. Time out -> abort() -> raise (probe() logs it).
    done = threading.Event()
    err = {}

    def _wait():
        if not jpype.isThreadAttachedToJVM():
            jpype.attachThreadToJVM()
        try:
            acq.waitForCompletion()
        except Exception as e:  # noqa: BLE001
            err["e"] = e
        finally:
            done.set()

    threading.Thread(target=_wait, name="spike-wait", daemon=True).start()
    if not done.wait(timeout_s):
        log(f"  TIMEOUT after {timeout_s}s — aborting acquisition")
        try:
            acq.abort()
        except Exception as e:  # noqa: BLE001
            log(f"  abort() also failed: {e}")
        raise TimeoutError(f"{label}: waitForCompletion did not return in {timeout_s}s")
    if "e" in err:
        raise err["e"]
    log("  waitForCompletion returned")

    summary = []
    if hook is not None:
        summary.append(f"hook.run x{hook.calls}")
    if proc is not None:
        summary.append(f"processor received {proc.received}")
    for attr in ("count",):
        if hasattr(sink, attr):
            summary.append(f"sink.{attr}={getattr(sink, attr)}")
    result = "; ".join(summary) if summary else "completed"
    log(f"  result: {result}")
    # Assert the core thing each run is meant to prove:
    if with_hook:
        assert hook.calls > 0, "AcquisitionHook.run() never fired"
    if with_processor:
        assert proc.received > 0, "TaggedImageProcessor received no images"
    if hasattr(sink, "count"):
        assert sink.count > 0, "sink received no images"
    return result


def finish(log):
    log.rule("SUMMARY")
    width = max((len(s) for s, _, _ in RESULTS), default=4)
    for step, status, detail in RESULTS:
        log(f"  {step.ljust(width)}  {status:4}  {detail}")
    n_pass = sum(1 for _, s, _ in RESULTS if s == "PASS")
    n_fail = sum(1 for _, s, _ in RESULTS if s == "FAIL")
    n_skip = sum(1 for _, s, _ in RESULTS if s == "SKIP")
    n_warn = sum(1 for _, s, _ in RESULTS if s == "WARN")
    log(f"\n  {n_pass} passed, {n_fail} failed, {n_skip} skipped, {n_warn} warnings.")
    log(f"\nFull log written to: {log.path}")
    log("NOTE: the Micro-Manager GUI may keep this process alive — the .txt is "
        "already complete (flushed per line). Close MM or Ctrl-C to exit.")
    log.close()


if __name__ == "__main__":
    main()
