# design/42 — open what we wrote, in the ImageJ that is already running

Session `20260806_123046_193612` (M5, artifacts in `Micro-Claw/stitch_test_cant_open/`).
The request was one sentence:

> …Put them in a stitched mosaic. **Then open the mosaic and show it to me**

The acquisition ran, the mosaic built, and the last clause failed:

> a candid limitation: **I don't have a tool that opens a saved TIFF into the
> Micro-Manager viewer.** … The fastest way to see it is to open
> `D:\SSD\stitch_test\stitch_test_mosaic.tiff` in FIJI or the MM GUI.

The refusal was honest — nothing was faked — but it is the whole gap. Microclaw
writes a file, hashes it, describes it, then tells the user to go use other
software. The artifact is fine: file SHA still matches what `inspect_artifacts`
recorded on the rig, and both digests in the sidecar recompute exactly
(`pixel_sha256` → `945df644…`, `manifest_payload_sha256` → `7af8cd62…`). Nothing
is corrupt. There is simply no read side.

## The mechanism: MM *is* an ImageJ instance, and we are already inside it

What the user does by hand is drag the TIFF onto the ImageJ toolbar that
Micro-Manager runs under; IJ1's opener handles it, delegating to Bio-Formats for
anything it doesn't read natively. We are already connected to that JVM.

**`ij.IJ` static calls work over the ZMQ bridge today.** This is not a
proposal — `MicroscopeController._probe_imagej_dir()` calls
`ij.IJ.getDirectory("imagej")` in production on every rig, to find the MM app
dir. The class resolves on any MM build with no #2401 dependency
(`design/ij-plugins-spike.py` check 3).

### Correcting design/10

design/10 concluded, from its 2026-07-02 spike, that **"static IJ1 methods are
not directly callable over ZMQ"** (check 4 FAIL) and steered all IJ1 use through
instance objects. That conclusion is obsolete, and the failure it rests on was
misattributed. Check 4's error was:

```
AttributeError: 'java_lang_Class' object has no attribute 'get_results_table'
```

That is precisely the pyjavaz static-class cache collision **design/12 diagnosed
ten days later**: every static `JavaClass` shadow is cached under the single key
`"java.lang.Class"`, so the first classpath wrapped in the process wins and every
later one silently inherits its methods. Check 4 wrapped `ij.IJ` (check 3) before
`ij.measure.ResultsTable`, so `ResultsTable` came back carrying `ij.IJ`'s surface.
The spike diagnosed a dead end that was really a cache bug.

The fix has shipped since: `controller._new_static_java_class(port, classpath)`
evicts the colliding key first. **design/10's §2 and its "Net conclusions" #2
must be amended** — the static IJ1 path is open, and `_probe_imagej_dir` is the
existence proof.

### Also correcting this document's own earlier draft

An earlier version of design/42 rejected pushing into the MM viewer, citing
design/18 and the jPypeMM repaint limitation. Both are about the **Preview
canvas** failing to repaint for live/snapped frames — not about opening a new
ImageJ image window. The contrary evidence was already on file: `set_position_list`
round-trips *and* repaints the MM GUI position list. GUI writes over this bridge
do land. The rejection over-generalised one canvas's behaviour into a rule.

## Decision

`open_artifact(path)` opens the file in the running ImageJ and reports the
provenance check. **It does not render a thumbnail unless the user asked
microclaw to analyze the image.**

"Show me the mosaic" and "tell me what's in the mosaic" are different requests
and only the second one needs microclaw to see the pixels. The default is the
first: hand the file to ImageJ, confirm the window opened, state the digests, and
stop. Beyond that there is nothing to do that `inspect_artifacts` doesn't already
do.

A thumbnail is not free. The 512 px PNG for this mosaic is **32,496 base64
characters** (10,388 at 256 px) — measured on the actual file, not estimated —
and an image block stays in the conversation for every subsequent turn of a
session, not just the turn that produced it. Spending that so microclaw can
describe a picture the user is already looking at is pure waste. So `analyze` is
off by default and the tool description says plainly when to turn it on.

### The call

`IJ.open(path)` is the primary, not `IJ.runMacro('open("…")')`:

- ~~It is the same entry point drag-and-drop uses (`ij.plugin.DragAndDrop` and
  `IJ.open` both land in `ij.io.Opener.open`), so Bio-Formats delegation via
  `HandleExtraFileTypes` comes along for free~~ — **disproved for directories,
  2026-08-07; see §"What the spike measured" F1. The instruction to confirm
  rather than trust this paragraph was the right one, and it was wrong.** For a
  single TIFF `IJ.open` is correct and measured. For a *directory* it is a
  silent no-op that holds the bridge for ~7 s, while dragging the same folder
  onto the toolbar opens it — so the two are not one entry point, and
  Bio-Formats delegation is separately unproven here.
- No macro engine, no global `ResultsTable`, no EDT batch-mode juggling.
- **No string escaping.** A Windows path (`D:\SSD\stitch_test\…`) goes through as
  an argument. Embedding it in macro source would require escaping backslashes
  into a Java string inside a macro inside JSON — three layers, each a defect
  waiting to happen.

### Proving it actually opened

`IJ.open` returns void, and design/18's real lesson stands even though its
Preview specifics don't: **a bridge call returning does not prove a window
painted.** So check structurally via `ij.WindowManager` — count images before,
open, then confirm a new window ID exists whose title matches the file name and
whose dimensions match what we read Python-side. Dimensions are the load-bearing
part; a title match alone would be nearly self-confirming.

If the count does not change, report that the open failed and fall back to the
thumbnail. Never report a window the user cannot see.

### Constraints this must respect

- **The user owns the session.** Open a *new* window and leave it. Never reuse or
  close an existing one, never `WindowManager.setTempCurrentImage`, never close
  anything on exit — microclaw writes nothing to MM on any exit path.
- **Java-side path.** `IJ.open` resolves the path on the *Java* machine. Verify
  the file Python-side first and refuse if the bridge is not local; a remote
  bridge would open some other machine's file, or nothing, and the window check
  above would not distinguish the two.
- **Re-wrap statics per call.** `_new_static_java_class` evicts the shared cache
  key each time. Do not hold an `IJ` shadow across a `WindowManager` wrap — get
  each immediately before use. (Whether a already-returned shadow survives a
  later eviction is a spike question, not something to design around.)

### The thumbnail, when it is asked for

Empty canvas between tiles is normal and needs no handling. One narrow exception
applies only on the `analyze` path, measured on this mosaic: it is 65% uncovered
zeros, and `make_thumbnail`'s 2nd/99.8th percentile stretch over *all* pixels
maps the real signal into the bottom ~1% of the ramp, so the thumbnail comes back
near-black and microclaw would be interpreting a black rectangle. Stretch over
nonzero pixels instead and say so in the payload. In ImageJ the user has their own
contrast controls, so on the default path this is a non-issue.

## Stubs

### `microclaw/controller.py` — beside `_probe_imagej_dir`, which already proves the path

```python
def open_in_imagej(self, path: str) -> dict:
    """Open a file in the ImageJ instance Micro-Manager runs under.

    Same entry point as dragging the file onto the ImageJ toolbar, so IJ1's
    opener (and Bio-Formats via HandleExtraFileTypes) handles the format. Opens
    a NEW window and leaves it: microclaw never closes or re-uses the user's
    windows. Statics go through _new_static_java_class per call — see design/12.
    """
    if not self.is_connected():
        return {"opened": False, "reason": "No Micro-Manager bridge connection."}

    def window_ids() -> set[int]:
        wm = _new_static_java_class(self._port, "ij.WindowManager")
        ids = wm.get_id_list()
        return set() if ids is None else {int(i) for i in ids}

    before = window_ids()
    _new_static_java_class(self._port, "ij.IJ").open(path)
    new_ids = window_ids() - before
    if not new_ids:
        # IJ.open returning is not proof a window exists (design/18's lesson,
        # even though its Preview specifics don't apply here).
        return {"opened": False,
                "reason": "ImageJ accepted the path but no new image window appeared."}
    wm = _new_static_java_class(self._port, "ij.WindowManager")
    opened = [wm.get_image(i) for i in sorted(new_ids)]
    return {"opened": True,
            "windows": [{"id": int(i), "title": str(im.get_title()),
                         "width": int(im.get_width()), "height": int(im.get_height()),
                         "n_slices": int(im.get_stack_size())}
                        for i, im in zip(sorted(new_ids), opened)]}
```

### `microclaw/tools.py`

```python
@emits_nothing   # a display step has no place in a re-run script, as with read_hook_log
def open_artifact(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    path: str,
    analyze: bool = False,
    max_size: int = 512,
) -> dict | list:
    """Open a file microclaw wrote in Micro-Manager's ImageJ and report what it is.

    Zero exposure; moves nothing. Opens a new ImageJ window the user can close;
    microclaw never closes it, and never renders the pixels into the
    conversation unless `analyze` asks it to.
    """
    resolved = Path(guard.resolve_readable_path(path))
    if not resolved.exists():
        return {"error": f"Artifact not found: {resolved}"}

    payload = {"path": str(resolved), "imagej": ctrl.open_in_imagej(str(resolved))}
    manifest = _sidecar_manifest(resolved)          # <path>.json, if we wrote one
    if manifest is not None:
        payload |= _verify_against_manifest(manifest, resolved)
    else:
        payload["provenance"] = ("No microclaw manifest beside this file; opened, "
                                 "but its origin is unverified.")
    # The default path ends here: the file is on the user's screen and its
    # provenance is stated. Reading the pixels in is a separate, costlier act.
    if not analyze or resolved.is_dir():
        return payload
    plane, selection = _select_plane(resolved, ...)  # refuses on an ambiguous stack
    return image_content(payload | {"selection": selection}, plane, max_size=max_size,
                         mask=plane != 0)


def _verify_against_manifest(manifest: dict, resolved: Path) -> dict:
    """Recompute both digests the writer recorded. Measured to reproduce exactly
    on stitch_test_mosaic.tiff (945df644…, 7af8cd62…)."""
    inner = manifest["manifest_payload"]
    payload_bytes = json.dumps(inner, sort_keys=True, separators=(",", ":"),
                               allow_nan=False).encode("utf-8")
    pixels = tifffile.imread(resolved)
    pixel_digest = hashlib.sha256(
        pixels.astype(np.uint16, copy=False).tobytes(order="C")).hexdigest()
    return {
        "kind": inner.get("kind"),
        "pixel_sha256_matches": pixel_digest == inner.get("pixel_sha256"),
        "manifest_payload_sha256_matches":
            hashlib.sha256(payload_bytes).hexdigest() == manifest["manifest_payload_sha256"],
        # The numbers that make the picture readable, straight from the manifest.
        **{k: inner[k] for k in ("coverage_fraction", "origin_um", "extent_um",
                                 "output_basis_um", "overwrite_convention")
           if k in inner},
        **({"calibration_warning": inner["calibration_warning"]}
           if "calibration_warning" in inner else {}),
    }
```

### `microclaw/image_analysis.py` — fold the duplicated block, add the mask

`snap_and_analyze` and `run_autofocus` each hand-build the same text+image pair.
Factor it once and let `open_artifact` be the third caller:

```python
def image_content(payload: dict, image: np.ndarray, *, max_size: int = 512,
                  mask: np.ndarray | None = None) -> list[dict]:
    return [
        {"type": "text", "text": json.dumps(payload)},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                     "data": make_thumbnail(image, max_size=max_size,
                                                            mask=mask)}},
    ]

# make_thumbnail: one line changes — percentiles read `img if mask is None else img[mask]`.
```

### `microclaw/tools_schema.py`

The description is where the default actually holds — it is what stops the model
reaching for `analyze` out of helpfulness:

```python
{
    "name": "open_artifact",
    "description": (
        "Open a file microclaw wrote — a mosaic or exported TIFF, an NDTiff "
        "dataset directory, or a JSON manifest/hook log — in the ImageJ window "
        "Micro-Manager runs under, and verify it against the digests recorded "
        "when it was written. This is how you show the user a file. Zero "
        "exposure; touches no hardware."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "The artifact path a tool returned."},
            "analyze": {"type": "boolean", "description":
                "Also read the pixels into this conversation so you can measure "
                "or describe them. Default false. Set it ONLY when the user asked "
                "you to analyze, interpret, or check the contents of the image. "
                "If they just want to look at the file, leave it false — the file "
                "is already open on their screen, and rendering it also costs "
                "context for the rest of the session."},
            "axis_selection": {"type": "object", "description":
                "With analyze, fixes ambiguous axes for a multi-plane TIFF or "
                "dataset, e.g. {\"time\": 0, \"z\": 3}."},
            "max_size": {"type": "integer", "description":
                "With analyze, longest rendered edge in px. Default 512."},
        },
        "required": ["path"],
    },
}
```

### `microclaw/agent.py`

> When the user asks to see or open a file microclaw wrote, call `open_artifact`
> and stop there — the file is then on their screen. Do not tell them to open it
> in FIJI. Only pass `analyze=true` when they asked you to interpret the image
> rather than look at it, and never describe an image you have not opened.

## Spike first — **DONE 2026-08-07, results in §"What the spike measured"**

Per repo convention, `design/42-ij-open-spike.py` committed, outputs not. Run on
the rig with MM open. Checks, each isolated:

1. `ij.IJ` and `ij.WindowManager` both resolve through `_new_static_java_class`,
   wrapped in both orders — the design/12 collision must not resurface.
2. Does a shadow returned before another static wrap still work after it? Decides
   whether "re-wrap per call" is a rule or just hygiene.
3. `IJ.open()` on `stitch_test_mosaic.tiff` — does a window appear, and do
   `WindowManager` dimensions match the 1004×1024 we read Python-side?
4. `IJ.open()` on a format IJ1 does not read natively (confirm Bio-Formats
   delegation is real on this install, and how it fails when it is absent).
5. `IJ.open()` on the NDTiff **directory** `stitch_test_1` — expected to fail;
   record how, so `open_artifact` can refuse it with a reason instead of hanging.
6. Does the call block until the window paints, or return early? Bridge calls
   serialise under one lock, so a modal Bio-Formats import dialog could stall
   every subsequent core call. **If it can hang, that is a blocker** and the tool
   needs a non-blocking path before it ships.

## What the spike measured — M5, 2026-08-07 (block 42a)

`design/42-ij-open-spike.py`, MMCore 12.5.0 / ImageJ 1.53c / Windows 11.
Evidence `out42a.txt` under `Micro-Claw/` (UTF-16LE). **6 PASS / 3 INFO / 1 SKIP,
no FAIL.** Neither stop condition fired, so the design below stands — with one
mechanism replaced and one assumption withdrawn.

**Confirmed.**

- **The design/10 correction is now measured, not argued.** `ij.IJ` and
  `ij.WindowManager` each wrapped cleanly through `_new_static_java_class` in
  both orders behind a decoy, exposing their own 138 / 38 methods and none of the
  other's. The **control** — the same wrap with the eviction bypassed —
  reproduced design/12 exactly: `ij.WindowManager` came back carrying
  `java.lang.System`'s 40 methods and missing `getIDList`/`getImageCount`/
  `getImage`. design/10 §2 and Net conclusions #2 are amended accordingly.
- **`IJ.open` on the mosaic works.** Window id `4294967292`, title
  `stitch_test_mosaic.tiff`, **1004×1024 == tifffile's (1024, 1004)**. The
  operator confirmed by eye that it painted. This is the last clause of the
  failing session, executed.
- **No stall, and no wait needed.** `open_s` 0.018 s, a trivial core call
  straight after 0.000 s, and the window visible to `WindowManager` with **no
  sleep at all**. So the structural check reads once; it does not poll.
- **Re-wrapping statics per call is hygiene, not a rule.** A held `ij.IJ` shadow
  answered identically after `ij.WindowManager` evicted the shared key, and the
  mirror case held. §"Constraints this must respect" said this was a spike
  question rather than something to design around; it is now answered, and the
  advice stands for consistency rather than for correctness.

**F1 — `IJ.open` is not drag-and-drop, for directories. This replaces a
mechanism.** `IJ.open` on the NDTiff directory raised nothing, opened nothing,
and **held the bridge 6.94 s** doing it. The operator then dragged the same
folder onto the ImageJ toolbar and **it opened**. So `ij.plugin.DragAndDrop` and
`IJ.open` do not both land in `ij.io.Opener.open` for a directory, and the
paragraph above that claimed they did is struck.

This matters more than a corner case: NDTiff datasets are the commonest thing
microclaw writes, and design/43 F7 turns on *"NDTiff opens directly in Fiji —
never export just to look"*. Block 42b must find the entry point the drag
actually dispatches to, or refuse directories by name. **Calling `IJ.open` on a
directory and reporting the result is the one thing it must not do** — a silent
seven-second no-op on the commonest artifact is worse than an honest refusal.

**F2 — Bio-Formats delegation is unproven, and the probe was inconclusive rather
than negative.** `loci.formats.ImageReader` resolved; `loci.plugins.BF`,
`loci.plugins.LociImporter` and `HandleExtraFileTypes` all returned "Class not
found on any classloaders". That is weak evidence: `HandleExtraFileTypes` is in
the default package and is loaded by IJ's own `PluginClassLoader`, which
pyjavaz's `ZMQUtil.loadClass` may not search. Check 4 **SKIPped** — no non-native
file was supplied — so nothing was opened and nothing is settled. Two
consequences: 42b must not lean on delegation, and **check 6's "does not stall"
is scoped to a file that opens natively.** The modal-importer case, which is the
one that could hold the lock, was never exercised.

**F3 — `IJ.redirectErrorMessages` is present** (as `redirect_error_messages`).
That is the lever for turning an IJ1 open failure into a Log entry rather than a
modal dialog, and given F2 it is worth using.

## Acceptance

**Rig gate.** Re-run the failing session verbatim on M5 — acquire the six
positions, build the mosaic, *"open the mosaic and show it to me."* Success is a
new ImageJ window the user can see, reported with matching dimensions and both
digests confirmed, and no instruction to open anything in FIJI. **The transcript
must contain no thumbnail** — that phrasing is a show-me, not an analyze-me, and
a rendered image there is a failed gate even though the window opened. Then ask
*"how many cells are in it?"* and confirm `analyze=true` appears only on that
second call. Finally: close the window by hand, confirm microclaw neither reopens
nor complains; run a second acquisition to confirm the bridge still works after an
ImageJ window is open.

Suite, on committed fixtures: the default call returns a dict, never a content
list, and calls no thumbnail code (assert via a patched `make_thumbnail` that
fails if reached); a tampered TIFF reports `pixel_sha256_matches: false` and still
opens; a file with no sidecar opens with provenance stated as unverified;
`open_in_imagej` with no bridge returns `opened: false` rather than raising; and
`image_content` is the only place the text+image pair is built.

## The directory case: open the TIFFs, not the dataset

**Decision, after two rig rounds:** a directory is resolved to the TIFF stack
files inside it, and each goes to the same `IJ.open` a file does. Micro-Manager's
dataset reader is not used.

An NDTiff dataset is ordinary TIFF stack files plus an `NDTiff.index` sidecar, so
ImageJ reads them natively — `tifffile` confirms a `pos_*_NDTiffStack.tif` off the
demo rig is a plain 512x512 uint16 TIFF. This is also what the operator already
does by hand, which is the strongest argument for it.

The route tried first — `loadData` + `manage` + `loadDisplays`, imitating
`DragDropUtil` — is a dead end for **our own data**, for two independent reasons
measured on the rig and recorded in `42-block42b-gate-findings.md`:

- **F1**, at load: `NDTiffAdapter.hashMapToCoords` casts every axis value to
  `Integer`, so a string-valued axis throws `ClassCastException`.
- **F4**, at display: `NDTiffAdapter` indexes coordinates with the **channel**
  axis stripped, so a dataset with no channel axis makes `getImagesIgnoringAxes`
  return an empty list and `.get(0)` throw inside `DisplayController.create`.
  Every single-channel dataset microclaw writes hits this, and the crash wedged
  the bridge for the rest of the session.

Reading the TIFFs avoids both, because it never asks Micro-Manager to interpret
the dataset.

### Multi-channel datasets — known limitation, deliberately tabled

Opening the stack files gives ImageJ windows of planes. It does **not**
reconstruct the axis structure: channel/z/time names, per-channel LUTs and
contrast, and the composite view all come from the dataset index, which ImageJ
never reads. Concretely:

- a dataset split across several `*_NDTiffStack*.tif` files opens as **several
  windows** rather than one;
- a multi-channel dataset's channels are **planes in a stack**, not named
  channels, so they are not separable by name or shown as a composite.

For single-channel data — what this is used on — the result is indistinguishable
from the ideal, so **this is good enough and is not being solved**. Revisit only
if a user explicitly asks for multi-channel display.

**If it is ever picked up**, the routes worth costing, best first:

1. **Build the hyperstack Python-side.** `ndstorage` already reads the index and
   every plane correctly (it is what `_measured_shape` uses), so the axis map is
   in hand without Micro-Manager. Write one properly-dimensioned OME-TIFF beside
   the dataset with `tifffile.imwrite(..., metadata={"axes": ...})` and open that
   through the existing file path. Costs a derived file; needs no new bridge
   surface and no Java.
2. **`NDViewer`.** It already displays exactly these datasets during acquisition
   (`show_display=True`), so it demonstrably handles the format MM's own viewer
   cannot. It needs an `NDViewerDataSource` built over the bridge, and
   `org.micromanager.ndviewer.main.NDViewer` implements only `NDViewerAPI` — not
   `DisplayWindow`, not `DataViewer` — so nothing in MM's display manager can be
   reused to construct or find one.
3. **Fix `NDTiffAdapter` upstream.** The channel assumption is a real MM bug and
   worth reporting regardless. Not a path microclaw can depend on.

Do **not** revisit "make acquisitions always write a channel axis": that contorts
how data is written to suit a viewer, and would need its own acquisition re-gate.

## Out of scope

Registration or stitching (still absent, still refused by name). Pushing pixels
into MM's *Preview* canvas — that is the thing design/18 and jPypeMM actually
rule out. Reading results back out of ImageJ (`ResultsTable`, ROI Manager): the
static path being open makes it newly plausible, but it is a separate design and
`run_ij_analysis` in design/10 already owns that question.
