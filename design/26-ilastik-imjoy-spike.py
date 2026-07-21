"""design/26 — spike: the two rungs nobody has measured — ilastik and ImJoy.

design/26 MEASURES its classical floor (sections B-E, design/26-roi-detection-spike.py)
and ARGUES everything else. Two of those arguments are load-bearing, both are on the
doc's own list of things it does not know, and neither has ever met a real file:

  F5 — "a user-trained ilastik project drops into the Descriptor seam: pooled statistics
  of the probability map (mean, high percentiles, thresholded area, connected-component
  count and spacing) are a descriptor vector like any other." The doc says plainly that
  this is designed, not measured. It also asserts a multi-second start-up (hence
  score-mode only, batched between passes) and asserts a .ilp is "untrusted bytes"
  whose hash gets pinned like a hook's.

  F6/F3 — the adjudicator needs a surface, and the doc picks ImJoy+Kaibu: crops out,
  labels in, example_boxes drawn on a browser canvas instead of MM's rectangle tool.
  Read at source, ImJoy's pycro-manager tutorial is a JUPYTER NOTEBOOK. microclaw is a
  CLI. Nobody has checked what the bridge needs, what comes back over it, or what it
  wants off the network.

This spike answers what a machine can answer and hands the rest to a human at the rig.
It DISCOVERS rather than assumes: every stage runs the real binary / reads the real
file / imports the real package and prints what it found. Where this file guesses at a
flag or an API, it says so and prints the ground truth beside the guess.

Stage letters continue the sequence: A is design/24-event-queue-spike.py (the event
queue), B-E are design/26-roi-detection-spike.py (the classical floor), F-G are here.

  F_0  find ilastik; report what THIS binary's --headless help actually offers
  F_1  write the training tiles and print the GUI steps — the human step F5 is about
  F_2  read the .ilp as HDF5 with NO ilastik: classes, remembered paths, and whether
       it stores pickles (F4 asks whether a .ilp is data or code; nobody looked)
  F_3  headless, batched: what it costs, and the map's REAL shape/dtype/axes/channels
  F_4  batched vs per-tile: the number behind "never inside a 100-500 ms dwell"
  F_5  pool the map into a descriptor; score B's concept with B's probe, per channel
  G_1  what the ImJoy stack needs here, and whether `api` exists outside a kernel
  G_2  what it wants off the network — opt-in, because a spike should not phone home
  G_3  the human round trip: a notebook, because G_1 is why

Run (nothing below touches the network or ~/.microclaw unless you ask it to):

    python design/26-ilastik-imjoy-spike.py                      # discovery; skips the rest
    python design/26-ilastik-imjoy-spike.py --tiles-dir C:\\tmp\\d26   # write the training set
    ... draw labels in the ilastik GUI, save the project ...
    python design/26-ilastik-imjoy-spike.py --tiles-dir C:\\tmp\\d26 --ilp C:\\tmp\\d26\\p.ilp
    python design/26-ilastik-imjoy-spike.py --tiles-dir ... --ilp ... --snap  # rig camera
    python design/26-ilastik-imjoy-spike.py --check-egress       # G_2 opens sockets

--snap is the rig stage: the tiles come off the camera through microclaw's own
snap_to_numpy, so what ilastik reads is what an acquisition would hand it — the
dtype, the bit depth and the geometry, not a synthetic stand-in for them.

Exits non-zero if a stage that RAN fails its verdict. Skips are not failures: this
file is meant to be run on the rig, in pieces, as the pieces become available.
"""
from __future__ import annotations

import argparse
import glob
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

SEP = "=" * 78
TILE = 256

#: The concept these tiles carry is section B's: bright condensed chromatin
#: ("mitotic") against diffuse nuclei ("interphase") and empty/debris fields.
#: Same classes, same generator, so F_5's ilastik descriptor is measured against
#: the SAME concept the classical floor already scores 1.000 on (B_1) — the only
#: way the comparison means anything.
POSITIVE_KIND = "mitotic"
NEGATIVE_KINDS = ("interphase", "empty")


# ---------------------------------------------------------------------------
# Reuse section B's tiles, features and probe rather than forking them
# ---------------------------------------------------------------------------
#
# The B spike's filename starts with a digit, so it cannot be imported by name.
# Loading it by path is not a trick to be clever with: forking _tile() would let
# this spike measure a DIFFERENT concept from the one the doc's numbers describe,
# and the two would drift exactly the way F7a says the descriptor and
# find_features would drift.

def _load_b_spike():
    path = Path(__file__).with_name("26-roi-detection-spike.py")
    spec = importlib.util.spec_from_file_location("d26_b_spike", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# F_0. Find ilastik, and ask THIS binary what it supports
# ---------------------------------------------------------------------------

def _candidate_ilastik_paths(explicit: str | None) -> list[str]:
    if explicit:
        return [explicit]
    out = []
    env = os.environ.get("MICROCLAW_ILASTIK")
    if env:
        out.append(env)
    for name in ("ilastik.exe", "ilastik", "run_ilastik.sh"):
        found = shutil.which(name)
        if found:
            out.append(found)
    out += sorted(glob.glob(r"C:\Program Files\ilastik-*\ilastik.exe"), reverse=True)
    out += sorted(glob.glob(r"C:\Program Files\ilastik-*\run_ilastik.bat"), reverse=True)
    out += sorted(glob.glob("/Applications/ilastik-*.app/Contents/MacOS/ilastik"),
                  reverse=True)
    out += sorted(glob.glob(str(Path.home() / "ilastik-*" / "run_ilastik.sh")), reverse=True)
    return out


def f0_find_ilastik(explicit: str | None):
    """Locate ilastik and report what its --headless help ACTUALLY offers.

    F_3's flags are quoted from ilastik's headless docs (read 2026-07-16; see
    _run_headless). This stage exists anyway, because a doc is a claim about a
    version and the rig runs whichever version the lab installed: the help text
    printed here is the ground truth for THIS binary, and it beats both our
    spelling and theirs.
    """
    print("\nF_0  ilastik on this machine, and what its help text admits to")
    for cand in _candidate_ilastik_paths(explicit):
        if not Path(cand).exists() and not shutil.which(cand):
            continue
        try:
            proc = subprocess.run([cand, "--headless", "--help"],
                                  capture_output=True, text=True, timeout=180)
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f"     {cand}\n       -> could not run: {exc!r}")
            continue
        help_text = (proc.stdout or "") + (proc.stderr or "")
        if not help_text.strip():
            print(f"     {cand}\n       -> ran, printed nothing (exit {proc.returncode})")
            continue
        print(f"     found: {cand}")
        print("     top-level --headless --help lists:")
        for flag in ("--project", "--headless", "--readonly", "--new_project",
                     "--workflow", "--export_source", "--output_format",
                     "--output_filename_format", "--raw_data"):
            print(f"       {flag:28s} {'listed' if flag in help_text else 'not listed'}")
        print("""     READ THAT TABLE CORRECTLY — 'not listed' does NOT mean 'not
     supported', and the first draft of this stage got that exactly backwards.
     Measured (1.4.2, 2026-07-17): --export_source / --output_format /
     --output_filename_format / --raw_data appear NOWHERE in this help text, and
     they work fine. ilastik parses in two stages: this parser handles the
     generic options, and the WORKFLOW named by --project adds the export/input
     options afterwards. No project, no workflow, no flags in --help.

     So the help text cannot answer the question, and F_0's next probe can:""")
        return cand, help_text
    print("     SKIPPED — no ilastik found."
          "\n     Looked at: $MICROCLAW_ILASTIK, PATH, C:\\Program Files\\ilastik-*,"
          "\n     /Applications/ilastik-*.app, ~/ilastik-*."
          "\n     Point at it with --ilastik PATH. F5's whole claim is that the LAB"
          "\n     already has one — if the rig does not, that is itself a finding.")
    return None, ""


def f0b_do_the_flags_exist(ilastik: str) -> bool | None:
    """Ask the binary whether it accepts the export flags, without a project.

    The help text cannot say (F_0), so make the ARGUMENT PARSER answer instead.
    ilastik rejects options it does not know with "Some of the command-line
    options you provided are not supported in headless mode." — so point it at a
    project that does not exist and hand it the flags:

      * complains about the FILE  -> the flags parsed fine; they are real.
      * complains about the FLAGS -> this build does not have them, and F_3's
        command line needs rewriting against this version.

    Measured (1.4.2, arm64 macOS, 2026-07-17): "RuntimeError: Project file
    '/tmp/does_not_exist.ilp' does not exist." — i.e. the flags are accepted, and
    the doc's citation (ilastik.org/documentation/basics/headless) holds on the
    build in front of us. Costs one ilastik start-up, which F_4 measures anyway.
    """
    print("\nF_0b are the export flags real on THIS build? (the parser knows; --help does not)")
    print('     (This probe FAILS ON PURPOSE — it points ilastik at a project that does'
          '\n     not exist. The "Launch error" / "get in touch with the ilastik team"'
          '\n     noise below is ours and is expected; the whole point is WHICH error.)')
    missing = Path("/nonexistent-design26-probe.ilp")
    try:
        proc = subprocess.run(
            [ilastik, "--headless", f"--project={missing}",
             "--export_source=Probabilities", "--output_format=hdf5",
             "--output_filename_format=/tmp/{nickname}_probs", "/tmp/nothing.tif"],
            capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"     SKIPPED — could not run it: {exc!r}")
        return None
    out = (proc.stdout or "") + (proc.stderr or "")
    unsupported = "not supported in headless mode" in out
    saw_file = "does not exist" in out or "Project file" in out
    for line in out.splitlines():
        if "does not exist" in line or "not supported" in line or "error:" in line:
            print(f"       {line.strip()[:150]}")
    if unsupported:
        print("     -> THE FLAGS ARE REJECTED by this build. F_3 will fail. Rewrite its"
              "\n        command line against this version and tell design/26 which"
              "\n        version changed it — export_detector_hook has to emit this.")
        return False
    if saw_file:
        print("     -> it got as far as the missing FILE, so the flags parsed. They are"
              "\n        real on this build, and F_3's command line is sound.")
        return True
    print("     -> inconclusive; read the output above by hand.")
    return None


# ---------------------------------------------------------------------------
# F_1. The training set, and the human step
# ---------------------------------------------------------------------------

def _write_tif(path: Path, img: np.ndarray) -> None:
    from skimage.io import imsave
    imsave(str(path), img, check_contrast=False)


def _snap_tiles(n: int, port: int) -> list[np.ndarray]:
    """Tiles off the real camera, through microclaw's own snap path.

    This is the point of running on the rig: ilastik reads what an acquisition
    would hand it — real dtype, real bit depth, real geometry — not a synthetic
    stand-in. The stage does not move: a demo camera returns the same field
    every time, which is useless for training but exact for measuring the DATA
    PATH, and on a real sample the operator moves the stage between snaps.
    """
    from microclaw.controller import MicroscopeController
    from microclaw.image_analysis import snap_to_numpy
    ctrl = MicroscopeController(port=port)
    out = []
    for _ in range(n):
        out.append(snap_to_numpy(ctrl))
        time.sleep(0.05)
    return out


def f1_write_tiles(tiles_dir: Path, snap: bool, port: int) -> dict:
    """Write the tiles the human will label, plus a scored set with known truth.

    F5's escape hatch is "the user draws pixel annotations, in twenty minutes, on
    a laptop" — the one rung on the ladder needing no GPU and no fine-tune. That
    step cannot be spiked. What CAN be spiked is everything on either side of it,
    so this stage lays out the input and prints the instructions, and the rest of
    F waits for the .ilp to come back.

    train/  a handful of tiles to draw on. Mixed classes, deliberately unlabelled
            in the filename: the operator should see what the sample looks like,
            not what this file thinks it is.
    score/  a held-out set WITH truth in truth.json, which F_5 scores. Never open
            this one in the GUI — a descriptor measured on its own training tiles
            is the design/20 failure with an ilastik hat on.
    """
    print("\nF_1  the training set, and the step no spike can take for you")
    b = _load_b_spike()
    train_dir, score_dir = tiles_dir / "train", tiles_dir / "score"
    train_dir.mkdir(parents=True, exist_ok=True)
    score_dir.mkdir(parents=True, exist_ok=True)

    if snap:
        try:
            raw = _snap_tiles(6, port)
        except Exception as exc:
            print(f"     --snap failed ({exc!r}); falling back to synthetic tiles.")
            snap = False
        else:
            for i, img in enumerate(raw):
                _write_tif(train_dir / f"train_{i:02d}.tif", img)
            print(f"     train/: {len(raw)} tiles OFF THE CAMERA "
                  f"({raw[0].shape} {raw[0].dtype}) -> {train_dir}")

    if not snap:
        kinds = [POSITIVE_KIND] * 3 + [NEGATIVE_KINDS[0]] * 2 + [NEGATIVE_KINDS[1]]
        for i, kind in enumerate(kinds):
            _write_tif(train_dir / f"train_{i:02d}.tif", b._tile(kind))
        print(f"     train/: 6 synthetic tiles ({', '.join(kinds)}) -> {train_dir}")

    truth = {}
    n_each = 15
    kinds = ([POSITIVE_KIND] * n_each
             + [NEGATIVE_KINDS[0]] * n_each + [NEGATIVE_KINDS[1]] * n_each)
    for i, kind in enumerate(kinds):
        name = f"score_{i:03d}.tif"
        _write_tif(score_dir / name, b._tile(kind))
        truth[name] = int(kind == POSITIVE_KIND)
    (score_dir / "truth.json").write_text(json.dumps(truth, indent=2), encoding="utf-8")
    print(f"     score/: {len(kinds)} tiles + truth.json ({sum(truth.values())} positive)"
          f" -> {score_dir}")

    print("""
     THE HUMAN STEP (F5). In the ilastik GUI, on this machine:
       1. New Project -> Pixel Classification. Save it as <tiles-dir>/project.ilp.
       2. Add the train/ tiles as raw data (Add New -> Add separate Image(s)).
       3. Feature Selection: take the defaults, or fewer if it drags.
       4. Training: TWO labels. Draw label 1 on the thing you want, label 2 on
          everything else. Note which is which — F_2 reads the names back, and
          which channel is yours is a real question, not a formality.
       5. Save the project. Quit ilastik (headless will not share the file).
       6. Rerun this spike with --ilp <tiles-dir>/project.ilp.""")
    return {"train": train_dir, "score": score_dir, "truth": truth}


# ---------------------------------------------------------------------------
# F_2. Read the .ilp without ilastik: is it data, or is it code?
# ---------------------------------------------------------------------------

#: Pickle protocol headers, and the opcodes that IMPORT A MODULE — which is the
#: part that matters. If these appear inside an HDF5 dataset, that dataset is not
#: data: it is a program that runs on load, which is F4's argument about a .pt
#: arriving by a shorter path.
#:
#: MEASURED (2026-07-17, ilastik 1.4.2, arm64 macOS): a Pixel Classification .ilp
#: created HEADLESS, with no labels and no training, already carries
#:   PixelClassification/ClassifierFactory =
#:     b'ccopy_reg\n_reconstructor\np0\n(clazyflow.classifiers.parallelVigraRf...'
#: — a protocol-0 pickle whose GLOBAL opcode imports from lazyflow. So a .ilp is
#: code from the moment it exists, not merely once somebody trains it.
#:
#: `c` at position 0 is protocol-0 GLOBAL; the \x80 pair is a binary protocol
#: header. Deliberately NOT here: bare b"\x8c" (SHORT_BINUNICODE — one byte, hits
#: anything) and b"GLOBAL" (an English word). A marker that fires on everything
#: proves nothing, which is the failure mode this stage exists to avoid.
_PICKLE_MARKERS = (b"\x80\x02", b"\x80\x03", b"\x80\x04", b"\x80\x05",
                   b"ccopy_reg", b"c__builtin__", b"cnumpy", b"csklearn",
                   b"clazyflow", b"cvigra", b"csklearn.ensemble")


def f2_read_ilp(ilp: Path) -> bool | None:
    """Walk the .ilp as plain HDF5 and report what it holds. No ilastik involved.

    Three questions design/26 asserts answers to and never checked:

      1. WHICH CHANNEL IS THE USER'S CLASS? F_5 pools a probability map and F5's
         prose says "a descriptor vector like any other" as though the map were
         one number per pixel. It is one number per pixel PER CLASS, and the
         channel order is the label order in a GUI we did not watch. If the names
         are in the file, the tool can read them instead of asking.
      2. WHAT PATHS DOES IT REMEMBER? An ilastik project stores where its raw
         data was. If those are absolute paths into the operator's Desktop, then
         "the user hands back the .ilp" is not a portable artifact and the manifest
         pins a file that only resolves on one machine.
      3. IS IT UNTRUSTED BYTES, AND IN WHICH SENSE? F4 says a .ilp gets a hash pin
         "by F4's argument and a shorter path". F4's argument is about UNPICKLING.
         HDF5 is data, not code — so if nothing in here is pickled, the .ilp is
         untrusted the way a CSV is untrusted, and the doc is over-claiming. If
         the classifier IS a pickle blob (ilastik has stored one), the claim is
         exact and the pin is mandatory. Either way the doc should say which.

    This stage does not index known paths — it VISITS every node, because the
    layout differs across ilastik versions and a spike that hardcodes a path
    measures its own assumption.
    """
    print(f"\nF_2  the .ilp as HDF5, with no ilastik: {ilp}")
    try:
        import h5py
    except ImportError:
        print("     SKIPPED — h5py not importable. `pip install h5py` (it is not a"
              "\n     microclaw dependency, and this stage is the argument for whether"
              "\n     it should become one: reading class names beats asking the user).")
        return None
    if not ilp.exists():
        print(f"     SKIPPED — no such file: {ilp}")
        return None

    datasets: list[tuple[str, tuple, str]] = []
    with h5py.File(str(ilp), "r") as f:
        def visit(name, obj):
            if isinstance(obj, h5py.Dataset):
                datasets.append((name, obj.shape, str(obj.dtype)))
        f.visititems(visit)

        print(f"     {len(datasets)} datasets. Workflow: "
              f"{_read_scalar(f, 'workflowName') or '(not found at /workflowName)'}")

        # ilastik's docs (read 2026-07-16) say the map is "a multichannel image,
        # where each channel corresponds to a class you defined during training",
        # so LABEL ORDER IS CHANNEL ORDER and the names are the only thing that
        # tells us which channel the user meant.
        names = [n for n, _, _ in datasets if "labelname" in n.lower()]
        trained = False
        print("     class names (label order == channel order in the map):")
        if not names:
            print("       NOT FOUND by name match — the map's channels are then"
                  "\n       positional only, and F_5's channel choice is a guess.")
        for n in names:
            val = f[n][()]
            decoded = _decode(val)
            # An untrained project stores a float64 PLACEHOLDER here (measured:
            # array([0.]) on a headless-created 1.4.2 project), not strings. A
            # spike that printed "[0.0]" as if it were a class list would be
            # reporting a placeholder as a finding.
            if getattr(val, "dtype", None) is not None and val.dtype.kind == "f":
                print(f"       {n}: placeholder {decoded} — NO LABELS DRAWN YET")
            else:
                trained = True
                print(f"       {n}: {decoded}")
                print("         ^ channel order. Channel i is whatever you drew i-th,"
                      "\n           which is a fact about a GUI session, not about this"
                      "\n           file. THIS is how the tool knows, instead of asking.")

        # The project REMEMBERS its export settings, and they are defaults from a
        # GUI session we did not watch (measured, 1.4.2: OutputFormat "compressed
        # hdf5", OutputFilenameFormat "{dataset_dir}/{nickname}_{result_type}",
        # OutputInternalPath "exported_data"). So an ilastik-backed detector that
        # omits --output_format inherits whatever the operator last clicked. Pass
        # it explicitly, always: an artifact whose output encoding depends on an
        # unobserved GUI setting is not reproducible, which is the whole bar.
        print("     export settings this project remembers (we must OVERRIDE these):")
        for n, _, _ in datasets:
            if n.startswith("Prediction Export/") or "ilastikversion" in n.lower():
                print(f"       {n}: {_decode(f[n][()])}")

        # Only real path fields. The first draft also matched "location", which
        # holds the enum "FileSystem" — and then solemnly reported that FileSystem
        # does not resolve on this machine.
        paths = [n for n, _, _ in datasets
                 if any(k in n.lower() for k in ("filepath", "datasetdirectory"))]
        print("     remembered raw-data paths (ABSOLUTE would mean: not portable):")
        n_rel = n_abs = 0
        for n in paths[:8]:
            val = _decode(f[n][()])
            for p in (val if isinstance(val, list) else [val]):
                if not isinstance(p, str) or not p:
                    continue
                # Resolve RELATIVE TO THE PROJECT FILE, not to the cwd. The first
                # draft checked Path(p.split("/")[0]) against the working
                # directory and declared a perfectly good relative path broken.
                target = Path(p) if Path(p).is_absolute() else (ilp.parent / p)
                if Path(p).is_absolute():
                    n_abs += 1
                    kind = "ABSOLUTE"
                else:
                    n_rel += 1
                    kind = "relative to the .ilp"
                ok = "resolves" if target.exists() else "MISSING"
                print(f"       {n}:\n         {p}  [{kind}; {ok}]")
        if not paths:
            print("       none found by name match")
        elif n_abs:
            print(f"     -> {n_abs} ABSOLUTE path(s). The project only opens on a machine"
                  "\n        with that exact layout, so 'the user hands back the .ilp' is"
                  "\n        not a portable artifact and the manifest pins a file that"
                  "\n        resolves nowhere else (F5).")
        else:
            print(f"     -> all {n_rel} stored RELATIVE to the project file. The .ilp is"
                  "\n        portable if it travels with its training tiles — and for"
                  "\n        PREDICTION it does not need them at all, since headless takes"
                  "\n        its inputs on the command line. Good news for F5: the artifact"
                  "\n        the user hands back is self-contained.")

        # DO NOT filter by dtype. The first draft of this stage skipped anything
        # whose dtype was not int8/opaque/S — and h5py hands back variable-length
        # bytes as dtype OBJECT, which is exactly what ilastik stores the pickled
        # ClassifierFactory in. It therefore reported "no pickles" about a file
        # that opens with `ccopy_reg`, and told the doc to go and weaken F4 on the
        # strength of it. Read every dataset; let the MARKERS decide.
        pickled = []
        for name, shape, dtype in datasets:
            try:
                raw = f[name][()]
            except Exception:
                continue
            blob = _as_bytes(raw)
            if blob is None:
                continue
            head = blob[:8192]
            hit = [m for m in _PICKLE_MARKERS if m in head]
            if hit:
                pickled.append((name, len(blob), _imported_modules(head)))

    print("     datasets that are PICKLES (F4: is a .ilp data, or code?):")
    if pickled:
        for name, size, mods in pickled[:8]:
            print(f"       {name}  ({size} bytes)")
            for m in mods[:4]:
                print(f"          imports on load: {m}")
        print("       -> a .ilp IS executable code: unpickling runs those imports."
              "\n          F4's 'untrusted by the same argument, and by a shorter path'"
              "\n          is EXACT, not a stretch, and the hash pin is mandatory rather"
              "\n          than tidy. Note WHEN: if this project was created headless and"
              "\n          never trained, the pickle predates any label — a .ilp is code"
              "\n          from birth, not once somebody teaches it something.")
    else:
        print("       none detected — and treat that as SUSPICIOUS, not clean."
              "\n       Measured on ilastik 1.4.2 (2026-07-17), even an untrained project"
              "\n       carries a pickled ClassifierFactory. A .ilp with none is either a"
              "\n       version that stores the forest natively, or this scan missing it"
              "\n       the way its first draft did (an h5py dtype filter). Look at the"
              "\n       dataset list above by hand before concluding anything.")

    if not trained:
        print("     NOTE: this project has NO LABELS — it is untrained. Everything above"
              "\n     about structure holds; nothing about a CLASSIFIER can be read from"
              "\n     it, because there is not one yet. Do the GUI step for that half.")
    return True


def _as_bytes(raw) -> bytes | None:
    """Whatever h5py just handed back, as bytes, or None if it is not bytes-ish."""
    if isinstance(raw, bytes):
        return raw
    if isinstance(raw, np.ndarray):
        if raw.dtype == object:
            parts = [p for p in raw.ravel().tolist() if isinstance(p, bytes)]
            return b"".join(parts) if parts else None
        if raw.dtype.kind in ("S", "V", "u", "i"):
            return raw.tobytes()
    return None


def _imported_modules(blob: bytes) -> list[str]:
    """The modules a pickle imports on load — the part that makes it code.

    Protocol 0 spells GLOBAL as `c<module>\\n<name>\\n`; the binary protocols use
    STACK_GLOBAL with the module as a short string. This is a readable
    approximation for a spike, not a pickle parser: it is here to print WHAT gets
    imported, because "it is a pickle" is abstract and
    "it imports lazyflow.classifiers.parallelVigraRfLazyflowClassifier on load"
    is not.
    """
    import re
    mods = re.findall(rb"c([a-zA-Z_][\w.]{2,60})\n([a-zA-Z_][\w.]{0,60})\n", blob[:8192])
    return [f"{m.decode()}.{n.decode()}" for m, n in mods[:6]]


def _decode(val):
    if isinstance(val, bytes):
        return val.decode("utf-8", "replace")
    if isinstance(val, np.ndarray):
        return [_decode(v) for v in val.tolist()]
    return val


def _read_scalar(f, key):
    try:
        return _decode(f[key][()])
    except Exception:
        return None


# ---------------------------------------------------------------------------
# F_3 / F_4. Headless: what comes back, and what it costs
# ---------------------------------------------------------------------------

def _run_headless(ilastik: str, ilp: Path, tiles: list[Path], out_dir: Path,
                  fmt: str) -> tuple[float, subprocess.CompletedProcess]:
    """One invocation, N tiles. Returns wall clock and the completed process.

    Every flag below is from ilastik's headless docs, read 2026-07-16 at
    ilastik.org/documentation/basics/headless — not recalled, which is the house
    rule (design/21). What that page settles, verbatim:

      * inputs are POSITIONAL, last: "the list of input files to process must come
        after all other items in the command" — and yes, many files in one
        invocation, which is the batching F_4 measures;
      * --export_source for Pixel Classification: "Probabilities", "Simple
        Segmentation", Uncertainty, Features, Labels;
      * --output_format accepts hdf5, compressed hdf5, numpy, tif, png, ... (use
        --output-format numpy on a machine with no h5py; F_2 needs h5py anyway);
      * --output_filename_format placeholders include {nickname} ("raw input file
        basename") and {dataset_dir}; the extension comes from --output_format;
      * --readonly: "necessary for using a single project from multiple processes"
        — which is why it is here rather than as tidiness: F_4 invokes the same
        .ilp N times in a row.

    F_0 still prints the installed binary's help text, because a doc is a claim
    about a version and the rig has whichever version the lab installed. If this
    fails on an unrecognised argument, that IS the finding — export_detector_hook
    would have to emit this exact command line for an ilastik-backed detector.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [ilastik, "--headless",
           f"--project={ilp}",
           "--export_source=Probabilities",
           f"--output_format={fmt}",
           f"--output_filename_format={out_dir}/{{nickname}}_probs",
           # =true is LOAD-BEARING, measured 2026-07-17: the flag is declared
           # `--readonly [READONLY]` (an OPTIONAL value), so a bare --readonly
           # sitting before the positional inputs makes argparse swallow the
           # first tile as its value — "invalid value 'score_000.tif' for
           # --readonly". The scan silently loses an input to an argparse detail.
           # Whatever emits this command line has to know that.
           "--readonly=true"]
    cmd += [str(t) for t in tiles]
    t0 = time.perf_counter()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    return time.perf_counter() - t0, proc


def _read_map(path: Path):
    """Read one exported probability map, whatever ilastik just wrote."""
    if path.suffix in (".h5", ".hdf5"):
        import h5py
        with h5py.File(str(path), "r") as f:
            keys = []
            f.visititems(lambda n, o: keys.append(n) if hasattr(o, "shape") else None)
            ds = f[keys[0]]
            # ilastik stamps the axis order on the dataset. This is the single
            # most useful thing in the file and the doc never mentions it.
            tags = ds.attrs.get("axistags")
            return np.asarray(ds[()]), (_decode(tags) if tags is not None else None), keys[0]
    if path.suffix == ".npy":
        return np.load(str(path)), None, path.name
    from skimage.io import imread
    return imread(str(path)), None, path.name


def f3_headless_once(ilastik: str, ilp: Path, tiles: list[Path], out_dir: Path,
                     fmt: str) -> dict | None:
    """Run the batch, then report the map's REAL shape, dtype, axes and channels.

    "ilastik emits a per-pixel probability map, and pooled statistics of that map
    are a descriptor vector like any other" (F5). Every word of that is a design
    decision waiting on four facts this stage measures: the array's shape, its
    axis ORDER (is it cyx, yxc, tzyxc?), its dtype (float32 in 0-1, or uint8 in
    0-255, which changes what a 0.5 threshold means), and how many channels it has.
    """
    print(f"\nF_3  headless, {len(tiles)} tiles in ONE invocation, --output_format={fmt}")
    elapsed, proc = _run_headless(ilastik, ilp, tiles, out_dir, fmt)
    if proc.returncode != 0:
        out = (proc.stderr or "") + (proc.stdout or "")
        for line in out.strip().splitlines()[-12:]:
            print(f"       {line}")
        print(f"     FAILED (exit {proc.returncode}) after {elapsed:.1f} s")
        # Tell the two failures apart, because they mean opposite things.
        if "SlotNotReadyError" in out or "isn't ready" in out:
            print("""     -> THIS IS F5, NOT A BUG. The flags parsed and the workflow ran; it
        died at the export slot because the project HAS NO TRAINED CLASSIFIER.
        Measured 2026-07-17: an ilastik created headless via --new_project
        --workflow="Pixel Classification" produces a valid .ilp that predicts
        NOTHING. Headless can make a project; only a human in the GUI can make
        it mean something. That is F5's entire thesis, arriving as a stack
        trace: this program cannot proceed without you.
        Do the F_1 GUI step and rerun with that .ilp.""")
        elif "not supported in headless mode" in out or "invalid value" in out:
            print("     -> an ARGUMENT problem, not a workflow one: F_0b's probe and the"
                  "\n        flag notes in _run_headless are where to look. (Watch for"
                  "\n        --readonly eating a positional input.)")
        else:
            print("     -> unrecognised failure; F_0's help text and F_0b's probe are the"
                  "\n        first two places to look.")
        if "Launch error" in out:
            print("""     (IGNORE "Launch error" / "Please get in touch with the ilastik
        team". Measured 2026-07-17 on the arm64 .app: it appears ONLY when the
        run has already failed — zero occurrences on a successful headless run
        (exit 0) and zero on an argparse rejection (exit 2), but two on any
        unhandled exception (exit 255). It is the macOS bundle stub saying the
        app exited abnormally, which it did, for the real reason printed above
        it. It is not a broken install and not a clue; do not chase it.)""")
        return None
    written = sorted(p for p in out_dir.iterdir() if p.is_file())
    print(f"     {elapsed:.1f} s wall, {len(written)} file(s) written")
    if not written:
        print("     ...and nothing came out. --output_filename_format is the suspect.")
        return None
    arr, axistags, key = _read_map(written[0])
    print(f"     first map: {written[0].name}  (dataset {key})")
    print(f"       shape {arr.shape}   dtype {arr.dtype}   "
          f"range [{float(np.min(arr)):.3f}, {float(np.max(arr)):.3f}]")
    print(f"       axistags: {str(axistags)[:300] if axistags else 'ABSENT — order is a guess'}")
    n_ch = arr.shape[-1] if arr.ndim >= 3 else 1
    print(f"       channels: {n_ch}  (one per label you drew; F_2 printed their names)")
    print(f"     per-tile amortised: {elapsed / len(tiles) * 1000:.0f} ms — but see F_4,"
          "\n     because the interesting number is what is start-up and what is per tile.")
    return {"elapsed": elapsed, "written": written, "shape": arr.shape,
            "dtype": str(arr.dtype), "channels": n_ch, "axistags": axistags}


def f4_batched_vs_per_tile(ilastik: str, ilp: Path, tiles: list[Path], out_dir: Path,
                           fmt: str, batched_elapsed: float) -> bool | None:
    """Split the batch's cost into start-up and per-tile. This is the F5 claim.

    F5 makes a structural ruling from an unmeasured number: "ilastik headless is a
    subprocess with a multi-second start-up. You cannot pay that inside a 100-500 ms
    tile dwell, so it can never be an online scanner... it runs once, batched, over
    the whole survey directory between passes." If start-up is ~5 s and per-tile is
    ~50 ms, the ruling is right and the batching is the whole game. If start-up is
    300 ms, mode="acquire" with an ilastik backend is at least arguable and the doc
    is asserting more than it knows.
    """
    n = min(3, len(tiles))
    print(f"\nF_4  the same {n} tiles, ONE AT A TIME — where does the time actually go?")
    per = []
    for i, tile in enumerate(tiles[:n]):
        elapsed, proc = _run_headless(ilastik, ilp, [tile], out_dir / f"single_{i}", fmt)
        if proc.returncode != 0:
            print(f"     FAILED on {tile.name} (exit {proc.returncode})")
            return None
        per.append(elapsed)
        print(f"       {tile.name}: {elapsed:.2f} s")
    solo = float(np.mean(per))
    marginal = ((batched_elapsed - solo) / max(len(tiles) - 1, 1)) if len(tiles) > 1 else 0.0
    print(f"     one tile alone      : {solo:.2f} s  (start-up + one tile)")
    print(f"     marginal per tile   : {marginal * 1000:.0f} ms  "
          f"(from the {len(tiles)}-tile batch)")
    print(f"     start-up, implied   : {max(solo - marginal, 0.0):.2f} s")
    verdict = solo > 0.5
    print(f"     -> F5's ruling ('never inside a 100-500 ms dwell'): "
          f"{'HOLDS' if verdict else 'DOES NOT HOLD — the doc is asserting too much'}")
    print(f"     -> batching {len(tiles)} tiles saved "
          f"{max(solo * len(tiles) - batched_elapsed, 0.0):.1f} s")
    return verdict


# ---------------------------------------------------------------------------
# F_5. Is the pooled map a descriptor?
# ---------------------------------------------------------------------------

def _pool(prob_map: np.ndarray, channel: int) -> np.ndarray:
    """F5's proposed pooling, verbatim: mean, high percentiles, thresholded area,
    connected-component count and spacing — over ONE class channel of the map.

    Nothing here is clever, and that is deliberate: the doc proposes exactly this
    and calls it "the obvious pooling and it is an argument". F_5 turns the
    argument into a number so the design can stop hedging.
    """
    from skimage.measure import label, regionprops
    m = prob_map[..., channel] if prob_map.ndim >= 3 else prob_map
    m = m.astype(np.float32)
    if m.max() > 1.5:                    # uint8 export: put it back on 0-1
        m = m / 255.0
    mask = m > 0.5
    lab = label(mask)
    props = regionprops(lab)
    areas = np.array([p.area for p in props], dtype=float) if props else np.zeros(1)
    cents = np.array([p.centroid for p in props], dtype=float) if props else np.zeros((1, 2))
    if len(cents) > 1:
        d = np.linalg.norm(cents[:, None, :] - cents[None, :, :], axis=-1)
        np.fill_diagonal(d, np.inf)
        spacing = float(np.mean(np.min(d, axis=1)))
    else:
        spacing = 0.0
    return np.array([
        float(m.mean()), float(np.percentile(m, 90)), float(np.percentile(m, 99)),
        float(mask.mean()), float(len(props)), float(areas.mean()), spacing,
    ], dtype=float)


def f5_pooled_descriptor(maps: list[tuple[str, Path]], truth: dict, n_channels: int) -> bool:
    """Score the pooled map with section B's own probe, per channel.

    This is the measurement F5 has been writing cheques against. It reports EVERY
    channel, not the one we assume is the user's: which channel is "the thing" is
    F_2's question, and if the answer has to come from an AUC then the tool has to
    ask the user, which is a design consequence.

    The comparison is against the classical floor on the SAME tiles, with the SAME
    probe — which on this by-construction concept already scores 1.000 (B_1). So a
    tie here means "ilastik cleared a bar that was already on the floor", NOT "the
    backends are equivalent". The transferable question — does a hand-drawn ilastik
    project span a concept the generic descriptor cannot — needs a real sample and
    a real .ilp, and F9's oriented/configural concepts are where to point it.
    """
    print("\nF_5  is the pooled probability map a descriptor? (F5's unmeasured claim)")
    b = _load_b_spike()
    names = [n for n, _ in maps]
    y = np.array([truth[n] for n in names], dtype=int)
    if y.sum() < 6 or (1 - y).sum() < 6:
        print(f"     SKIPPED — need >=6 of each class among the scored tiles; got "
              f"{int(y.sum())} positive, {int((1 - y).sum())} negative.")
        return False

    print(f"     {len(names)} tiles, {int(y.sum())} positive, {n_channels} channel(s)")
    best, worst = -1.0, 2.0
    for ch in range(n_channels):
        X = np.array([_pool(_read_map(p)[0], ch) for _, p in maps])
        auc = _fit_and_auc(b, X, y)
        flag = "  <- your class, if this is the label you drew first" if ch == 0 else ""
        print(f"       channel {ch}: pooled-map AUC {auc:.3f}{flag}")
        best, worst = max(best, auc), min(worst, auc)

    from skimage.io import imread
    Xc = np.array([b.features(imread(str(_tile_for(p)))) for _, p in maps])
    classical = _fit_and_auc(b, Xc, y)
    print(f"     classical floor, same tiles, same probe: AUC {classical:.3f}")
    print(f"     -> best ilastik channel {best:.3f} vs classical {classical:.3f}")
    if n_channels == 2 and worst > 0.99:
        print("""     -> BOTH channels scored the same, and that is not luck: with two
        labels the channels are complements (p and 1-p), and a FITTED probe
        learns its own polarity from the verdicts. So "which channel is the
        user's class" does NOT matter to FewShotProbe — it matters to a human
        reading the map, to anything thresholding a raw probability, and to a
        project with 3+ labels, where the channels stop being complements.
        F_2's class names stay worth reading; they are just not load-bearing
        for the probe.""")
    if classical > 0.99:
        print("     -> and read that pair the way F2's caveat says to: this concept was"
              "\n        built to separate photometrically, so the floor is already"
              "\n        perfect and there is NOTHING HERE FOR ILASTIK TO WIN. This stage"
              "\n        proves the PLUMBING (map -> pooled vector -> probe -> ranking),"
              "\n        not the backend. Point it at F9's oriented/configural concepts,"
              "\n        or better, at a real sample, before believing any ordering.")
    return best > 0.5


def _tile_for(map_path: Path) -> Path:
    """The source tile a probability map came from — see the caller's note."""
    stem = map_path.stem.replace("_probs", "")
    for parent in (map_path.parent.parent / "score", map_path.parent.parent):
        cand = parent / f"{stem}.tif"
        if cand.exists():
            return cand
    raise FileNotFoundError(f"no source tile for {map_path}")


def _fit_and_auc(b, X: np.ndarray, y: np.ndarray) -> float:
    """B's LogisticProbe, five positives, twenty negatives, held-out AUC."""
    rng = np.random.default_rng(0)
    pos_idx = np.flatnonzero(y == 1)
    neg_idx = np.flatnonzero(y == 0)
    fit_pos = rng.choice(pos_idx, size=min(5, len(pos_idx)), replace=False)
    fit_neg = rng.choice(neg_idx, size=min(20, len(neg_idx)), replace=False)
    held = np.array([i for i in range(len(y)) if i not in set(fit_pos) | set(fit_neg)])
    probe = b.LogisticProbe().fit(X[fit_pos], X[fit_neg])
    scores = np.array([probe.score(x) for x in X[held]])
    # B's _auc BOOLEAN-INDEXES truth (ranks[truth]); an int array would index
    # positions instead of masking and return a plausible wrong number.
    return b._auc(scores, y[held].astype(bool))


# ---------------------------------------------------------------------------
# G_1. What does the ImJoy surface actually need?
# ---------------------------------------------------------------------------

def g1_imjoy_environment() -> bool:
    """Report the ImJoy stack here, and answer the question design/26 never asks.

    The doc adopts ImJoy as the review surface on the strength of pycro-manager's
    own tutorial — which is a NOTEBOOK: `from imjoy import api`, a plugin class,
    `api.export(MyMicroscope())`. `api` is injected by the ImJoy runtime into a
    plugin's environment. microclaw is a CLI agent driving a rig over ZMQ, and
    review_roi_candidates(surface="imjoy") is specified as an ordinary tool call.

    So: does `api` exist outside a Jupyter kernel with imjoy-jupyter-extension
    loaded? If it does not — and this stage is how we find out rather than assume
    — then "surface='imjoy'" is not a rendering choice, it is a REQUIREMENT THAT
    THE OPERATOR BE IN A NOTEBOOK, and that belongs in the doc as a constraint on
    the F6 fallback rather than as a parameter default.
    """
    print("\nG_1  the ImJoy stack on this machine, and whether `api` exists off-notebook")
    for pkg in ("imjoy", "imjoy_rpc", "imjoy_jupyter_extension", "hypha_rpc",
                "jupyter", "notebook", "ipykernel", "jupyterlab"):
        try:
            mod = __import__(pkg)
        except Exception as exc:
            print(f"       {pkg:24s} NOT IMPORTABLE ({type(exc).__name__})")
        else:
            print(f"       {pkg:24s} {getattr(mod, '__version__', 'imported, no __version__')}")

    in_kernel = False
    try:
        from IPython import get_ipython
        in_kernel = get_ipython() is not None
    except Exception:
        pass
    print(f"     running inside an IPython kernel: {in_kernel}  "
          f"(python {platform.python_version()} on {platform.system()})")

    try:
        from imjoy import api
    except ModuleNotFoundError:
        print("     `from imjoy import api` -> not installed here."
              "\n     SKIPPED — install the review extra on the RIG and rerun:"
              "\n       pip install imjoy imjoy-jupyter-extension"
              "\n     Whether that install is even clean alongside pycro-manager on the"
              "\n     rig's Python is the first thing G_1 is for; it is a dependency the"
              "\n     doc adds on the strength of a tutorial nobody ran here.")
        return None
    except Exception as exc:
        print(f"     `from imjoy import api` -> {type(exc).__name__}: {exc}")
        print("     -> installed but not importable is the finding: whatever"
              "\n        surface='imjoy' is, it is not 'a tool call opens a window'. See G_3.")
        return False
    # MEASURED (2026-07-17, imjoy 0.11.20, CPython 3.11, no kernel): `hasattr`
    # RAISES here. imjoy_rpc's `api` is a werkzeug LocalProxy whose __getattr__
    # does `_rpc_context.api[attr]` and lets the KeyError out; hasattr only
    # swallows AttributeError, so it propagates and killed the whole spike.
    #
    # The crash IS the measurement, so catch it and report it rather than dying:
    # `from imjoy import api` SUCCEEDS outside a plugin runtime and the object it
    # gives you is a proxy with no context behind it. Importable is not usable.
    try:
        has_window = hasattr(api, "createWindow")
        print(f"     `from imjoy import api` -> ok; createWindow present: {has_window}")
        print(f"     api members: {sorted(a for a in dir(api) if not a.startswith('_'))[:12]}")
        print("     -> the proxy resolved. Surprising off-kernel; write down what you"
              "\n        got, because it contradicts the 2026-07-17 measurement below.")
        return True
    except Exception as exc:
        print(f"     `from imjoy import api` -> imports fine, but touching it raises:"
              f"\n       {type(exc).__name__}: {exc}")
        print("""     -> THIS IS THE G_1 RESULT, not an error in the spike. The import
        succeeds; the object is a proxy that resolves through an RPC context
        which only exists inside an ImJoy plugin runtime (a Jupyter kernel with
        imjoy-jupyter-extension, or the ImJoy web app). There is no such context
        in a CLI process, so `api` cannot be touched, let alone open a window.

        For design/26 this is a CONSTRAINT, not a detail: review_roi_candidates
        (surface="imjoy") cannot be an ordinary tool call from the microclaw CLI.
        The human adjudicator of F6 needs a notebook running beside the agent.
        Either the doc says that plainly, or surface="imjoy" does not ship.""")
        return False


def g2_egress(enabled: bool) -> bool | None:
    """What does the review surface want off the network? Opt-in, for a reason.

    F4-one-layer-up: "ImJoy's plugin sources come off the network... microclaw
    ships and self-hosts the review plugin it uses; it does not createWindow a
    gist at runtime." That is a rule the doc wrote for itself. What it did not
    check is whether the rule is even OPTIONAL: if a lab rig has no egress (the
    doc's own argument for the compile bar: "plenty of rigs are on isolated
    networks"), then a Kaibu loaded from kaibu.org does not open at all, and
    self-hosting is not hygiene — it is the only thing that works.

    This stage OPENS SOCKETS, so it is off by default: a spike that phones home
    from a lab machine without being asked is exactly the kind of surprise this
    project keeps refusing to ship. It connects and closes; it fetches nothing.
    """
    print("\nG_2  what the review surface wants off the network")
    if not enabled:
        print("     SKIPPED — pass --check-egress to run it. It opens TCP connections"
              "\n     to kaibu.org / imjoy.io from this machine and closes them again.")
        return None
    import socket
    hosts = [("kaibu.org", 443), ("imjoy.io", 443), ("ai.imjoy.io", 443),
             ("oeway.github.io", 443)]
    reachable = {}
    for host, port in hosts:
        try:
            socket.create_connection((host, port), timeout=4.0).close()
            reachable[host] = True
        except OSError as exc:
            reachable[host] = False
            print(f"       {host:24s} UNREACHABLE ({type(exc).__name__})")
        else:
            print(f"       {host:24s} reachable")
    if not any(reachable.values()):
        print("     -> this rig has no egress to the ImJoy CDN. A review window loaded"
              "\n        by URL will never open here, and self-hosting stops being a"
              "\n        security preference and becomes the only implementation.")
    else:
        print("     -> reachable, so the tutorial's createWindow(src=...) would work —"
              "\n        which is precisely why the doc bans it. Self-host anyway; the"
              "\n        point is that the rig should not need this column at all.")
    return True


def g3_the_round_trip(tiles_dir: Path) -> None:
    """The half a .py cannot measure: a human drawing a box, and what comes back.

    The question the design actually needs answered — and the one the user asked —
    is what the DATA looks like at the seam: an image goes out to a browser canvas,
    a person drags a rectangle, and something comes back into Python. design/26
    specifies that something as "plain (x, y, w, h) in image pixels" and has never
    seen it. If it comes back as normalised floats, as a polygon of vertices, as
    display coordinates against a scaled canvas, or with the y-axis flipped, then
    train_roi_detector's example_boxes contract is written against a fiction.

    That needs a browser and a hand, so it is a notebook, not a stage here.
    """
    print("\nG_3  the round trip — a human, a browser, and what lands back in Python")
    nb = Path(__file__).with_name("26-imjoy-review-spike.ipynb")
    print(f"     Not runnable from a script (G_1 is why). Run the notebook:\n"
          f"       jupyter notebook {nb}\n"
          f"     It snaps or loads a tile, hands it to Kaibu, waits for you to draw two"
          f"\n     rectangles, and PRINTS what comes back — type, shape, coordinate space."
          f"\n     Paste that output into design/26 F5/F6. If the window never opens, that"
          f"\n     is the finding, and G_1/G_2 above are the diagnosis.")
    if tiles_dir.exists():
        print(f"     (The notebook will read tiles from {tiles_dir / 'score'} if present.)")


# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ilastik", help="path to ilastik.exe / run_ilastik.sh")
    ap.add_argument("--ilp", help="a project.ilp you trained in the GUI (F_2-F_5)")
    ap.add_argument("--tiles-dir", default=None,
                    help="where to write train/ and score/ tiles "
                         "(default: a d26_tiles/ under the system temp dir — NOT the "
                         "repo, which is where the first draft put 6 MB of them)")
    ap.add_argument("--out-dir", default=None, help="where headless writes maps")
    ap.add_argument("--output-format", default="hdf5",
                    help="ilastik --output_format; F_0 prints what this build accepts")
    ap.add_argument("--snap", action="store_true",
                    help="take the training tiles off the rig camera via microclaw")
    ap.add_argument("--port", type=int, default=4827, help="Micro-Manager ZMQ port")
    ap.add_argument("--check-egress", action="store_true",
                    help="G_2 only: open sockets to the ImJoy CDN from this machine")
    args = ap.parse_args()

    print(SEP)
    print("design/26 — the two rungs nobody has measured: ilastik (F5) and ImJoy (F6)")
    print(SEP)

    # Default OUT of the working tree: run from the repo and the old default
    # dropped 6 MB of tiles into it. Spike source is committed; spike output is
    # never committed, so it should not land somewhere git can see it by accident.
    tiles_dir = Path(args.tiles_dir or (Path(tempfile.gettempdir()) / "d26_tiles")).resolve()
    out_dir = Path(args.out_dir or (tiles_dir / "maps")).resolve()
    results: dict[str, bool | None] = {}

    ilastik, _help = f0_find_ilastik(args.ilastik)
    # No ilastik here is a SKIP, not a failure — this file is meant to be run in
    # pieces, on whatever machine has the piece (design/24's A_9 convention).
    results["F_0"] = True if ilastik else None
    if ilastik:
        results["F_0b"] = f0b_do_the_flags_exist(ilastik)

    layout = f1_write_tiles(tiles_dir, args.snap, args.port)

    if args.ilp:
        ilp = Path(args.ilp).resolve()
        results["F_2"] = f2_read_ilp(ilp)
        if ilastik:
            score_tiles = sorted(layout["score"].glob("score_*.tif"))
            f3 = f3_headless_once(ilastik, ilp, score_tiles, out_dir, args.output_format)
            results["F_3"] = f3 is not None
            if f3:
                results["F_4"] = f4_batched_vs_per_tile(
                    ilastik, ilp, score_tiles, out_dir / "singles",
                    args.output_format, f3["elapsed"])
                maps = []
                for p in f3["written"]:
                    try:
                        maps.append((_tile_for(p).name, p))
                    except FileNotFoundError:
                        continue
                if maps:
                    results["F_5"] = f5_pooled_descriptor(
                        maps, layout["truth"], f3["channels"])
                else:
                    print("\nF_5  SKIPPED — could not match maps back to source tiles;"
                          "\n     --output_filename_format is what decides the names.")
        else:
            print("\nF_3  SKIPPED — no ilastik binary (see F_0).")
    else:
        print("\nF_2  SKIPPED — no --ilp yet. Do the GUI step F_1 printed, then rerun."
              "\nF_3  SKIPPED — same.\nF_4  SKIPPED — same.\nF_5  SKIPPED — same.")

    results["G_1"] = g1_imjoy_environment()
    results["G_2"] = g2_egress(args.check_egress)
    g3_the_round_trip(tiles_dir)

    print("\n" + SEP)
    ran = {k: v for k, v in results.items() if v is not None}
    failed = [k for k, v in ran.items() if not v]
    print(f"  RAN: {', '.join(sorted(ran)) or 'nothing but discovery'}")
    if failed:
        print(f"  FAILED STAGES: {', '.join(sorted(failed))}")
    print("  Findings belong in design/26-ml-roi-detection.md — F5 (ilastik), F6/F3 (the")
    print("  review surface). What a GREEN F does NOT prove: that ilastik BUYS anything.")
    print("  It ties a classical floor that was already perfect on this synthetic concept.")
    print("  The backend question needs F9's oriented/configural tiles, or a real sample.")
    print(SEP)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
