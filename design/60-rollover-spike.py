"""Off-rig probe of NDTiff rollover handling. No Micro-Manager, no Java.

Scope, stated first because the obvious spike is redundant. **Java writes the
files.** pycro-manager's notification thread receives an index-entry payload and
calls `NDTiffDataset.add_index_entry` (java_backend_acquisitions.py:203-204),
which at a rollover hits the one branch a normal frame never does
(ndtiff_dataset.py:260): a filename it has not seen, so it opens a
`SingleNDTiffReader` on the file Java has only just created.

**That branch is already known to work on the demo machine.** `add_index_entry`
runs *before* `_image_notification_queue.put(...)`, so an image-saved callback
proves it returned. Block 60b's gate recorded progress `first 1, last 8256 of
8256` with the roll at frame 8,114 -- on two independent runs. Frames 8,115
onward all called back, so the rollover entry parsed and the new reader opened.
Re-running that on the demo machine would measure nothing new.

What is left is the Python-side index code, which is cheap to probe here and is
*not* covered by those runs. Run:

    python design/60-rollover-spike.py            # seconds
    python design/60-rollover-spike.py --full     # also writes a real 4 GiB

Findings are printed and the exit status is nonzero if a probe fails.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

import numpy as np
from ndstorage import ndtiff_file
from ndstorage.ndtiff_dataset import NDTiffDataset
from ndstorage.ndtiff_index import NDTiffIndexEntry, read_ndtiff_index

RESULTS = []


def probe(name):
    def decorate(fn):
        try:
            detail, status = fn() or "", "OK"
        except Exception as exc:                    # noqa: BLE001 - reported
            detail, status = f"{type(exc).__name__}: {exc}", "FINDING"
            traceback.print_exc()
        RESULTS.append((status, name, detail))
        print(f"{status}: {name} - {detail}")
        return fn
    return decorate


def payload_for(axes: dict, entry) -> bytes:
    """Serialise an index entry the way the Java side puts it on the wire.

    Deliberately not `entry.as_byte_buffer()`: that method is broken upstream
    for any entry produced by parsing (see probe 2), and a probe that leans on
    a broken helper measures the helper.
    """
    import json
    import struct
    from io import BytesIO
    axes_bytes = json.dumps(axes).encode("utf-8")
    name_bytes = entry.filename.encode("utf-8")
    buf = BytesIO()
    buf.write(struct.pack("I", len(axes_bytes)))
    buf.write(axes_bytes)
    buf.write(struct.pack("I", len(name_bytes)))
    buf.write(name_bytes)
    buf.write(struct.pack("IIIIIIII", entry.pix_offset, entry.image_width,
                          entry.image_height, entry.pixel_type,
                          entry.pixel_compression, entry.metadata_offset,
                          entry.metadata_length, entry.metadata_compression))
    return buf.getvalue()


def write_dataset(path, frames, width, height, name="spike"):
    """Drive the real writer over a rollover and return the dataset."""
    ds = NDTiffDataset(str(path), name=name, summary_metadata={}, writable=True)
    frame = np.zeros((height, width), dtype=np.uint16)
    for i in range(frames):
        # Metadata size matters: it is part of the admission test, and it is the
        # quantity that differed 3.4x between M2 and the demo rig.
        ds.put_image({"time": i}, frame, {"Frame": i, "pad": "x" * 512})
    ds.finish()
    actual = Path(ds.path)
    ds.close()
    # NDTiffDataset._create_unique_acq_dir puts the data in <path>/<name>_1, the
    # same AcqEngJ-style rename design/21 F6 records. Return where it went.
    return actual


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true",
                        help="also cross a real 4 GiB (needs ~5 GB and minutes)")
    args = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="ndtiff-rollover-spike-"))
    print(f"scratch: {root}\nndstorage MAX_FILE_SIZE: {ndtiff_file.MAX_FILE_SIZE}\n")

    # ---- 1. Does the writer roll at all, and is every frame indexed? --------
    # Shrunk cap: same code path, seconds instead of minutes. This cannot test
    # 32-bit offset arithmetic near 2**32 -- that is what --full is for.
    real_max = ndtiff_file.MAX_FILE_SIZE
    small = 8 * 1024 * 1024
    rolled = {}

    @probe("the writer rolls to a second file at the cap, losing no frames")
    def rolls():
        ndtiff_file.MAX_FILE_SIZE = small
        try:
            path = write_dataset(root / "shrunk", frames=48, width=512, height=512)
        finally:
            ndtiff_file.MAX_FILE_SIZE = real_max
        stacks = sorted(p.name for p in path.glob("*NDTiffStack*.tif"))
        index = read_ndtiff_index((path / "NDTiff.index").read_bytes(), verbose=False)
        names = {e.filename for e in index.values()}
        rolled["path"], rolled["index"] = path, index
        rollover = [(k, e) for k, e in index.items() if e.filename.endswith("_1.tif")]
        assert rollover, "no entry names the second file"
        rolled["rollover"] = min(rollover, key=lambda kv: kv[1].pix_offset)
        assert len(stacks) >= 2, f"no rollover at an {small} B cap: {stacks}"
        assert len(index) == 48, f"{len(index)} entries for 48 frames"
        assert names == set(stacks), f"index names {names}, disk has {set(stacks)}"
        return f"{len(stacks)} stacks, {len(index)} entries, names match disk"

    # ---- 2. Does a rollover entry survive the wire format? ------------------
    @probe("a parsed index entry can be re-serialised (as_byte_buffer)")
    def roundtrip():
        index = rolled.get("index")
        if index is None:
            raise AssertionError("probe 1 produced no index")
        key, entry = rolled["rollover"]
        # Round-trip through the payload parser using our own serialiser first,
        # to show the WIRE FORMAT is fine and isolate the defect to the helper.
        _, axes, parsed = NDTiffIndexEntry.unpack_single_index_entry(
            payload_for(dict(key), entry))
        assert parsed.filename == entry.filename, (parsed.filename, entry.filename)
        assert parsed.pix_offset == entry.pix_offset
        # Now the library's own re-serialiser on the same parsed entry.
        entry.as_byte_buffer()
        return "wire format round-trips and as_byte_buffer works"

    # ---- 3. The window the notification thread runs into --------------------
    @probe("add_index_entry on a rollover entry whose file is not yet readable")
    def missing_file():
        index = rolled.get("index")
        if index is None:
            raise AssertionError("probe 1 produced no index")
        key, entry = rolled["rollover"]
        empty = root / "reader-window"
        empty.mkdir()
        # A reader-side dataset, as pycro-manager holds during an acquisition.
        ds = NDTiffDataset(str(empty), summary_metadata={})
        try:
            ds.add_index_entry(payload_for(dict(key), entry))
        except Exception as exc:                    # noqa: BLE001 - the point
            # Must fail BECAUSE the file is missing, not for any other reason:
            # a probe that accepts the wrong exception is not a probe.
            assert entry.filename in str(exc) or isinstance(exc, (OSError, FileNotFoundError)), \
                f"failed for an unrelated reason: {type(exc).__name__}: {exc}"
            return (f"raises {type(exc).__name__} when {entry.filename} is absent "
                    f"-- pycro-manager catches this, calls acquisition.abort(e) "
                    f"and continues (java_backend_acquisitions.py:227-229), so a "
                    f"writer that notifies before its new file is readable aborts "
                    f"the run rather than crashing the thread")
        raise AssertionError(
            "add_index_entry accepted an entry naming a file that does not exist; "
            "there is no window here, which would be good news")

    # ---- 4. A live bug in ndstorage's own index reader ----------------------
    @probe("NDTiffIndexEntry.read_index_map works")
    def read_index_map_works():
        path = rolled.get("path")
        if path is None:
            raise AssertionError("probe 1 produced no dataset")
        NDTiffIndexEntry.read_index_map(str(path / "NDTiff.index"))
        return "reads without error"

    # ---- 5. Optional: a real 2**32 crossing ---------------------------------
    if args.full:
        @probe("a real 4 GiB crossing with the true cap")
        def full_crossing():
            free = shutil.disk_usage(str(root)).free
            need = int(real_max * 1.3)
            if free < need:
                raise AssertionError(f"needs ~{need/1e9:.1f} GB, {free/1e9:.1f} GB free")
            # 2048x2048x2 = 8 MB/frame -> ~540 frames to cross 4 GiB.
            frames = int(real_max // (2048 * 2048 * 2) + 32)
            path = write_dataset(root / "full", frames=frames, width=2048, height=2048)
            stacks = sorted(p.name for p in path.glob("*NDTiffStack*.tif"))
            sizes = {p.name: p.stat().st_size for p in path.glob("*NDTiffStack*.tif")}
            index = read_ndtiff_index((path / "NDTiff.index").read_bytes(), verbose=False)
            assert len(stacks) >= 2, f"no rollover at the real cap: {stacks}"
            assert len(index) == frames, f"{len(index)} entries for {frames} frames"
            assert max(sizes.values()) <= real_max, sizes
            return (f"{len(stacks)} stacks, largest {max(sizes.values()):,} B "
                    f"<= {real_max:,}; {len(index)} entries, none lost")
    else:
        print("SKIPPED: real 4 GiB crossing (pass --full)")

    print()
    findings = [r for r in RESULTS if r[0] != "OK"]
    for status, name, detail in findings:
        print(f"  {status}: {name}")
    print(f"\n{len(RESULTS)} probes, {len(findings)} findings")
    shutil.rmtree(root, ignore_errors=True)
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
