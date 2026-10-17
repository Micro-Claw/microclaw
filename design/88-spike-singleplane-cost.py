"""88a spike: what does verifying a 'Separate image files' dataset cost on this machine?

Read-only. For each folder given, twice (first pass may be cold), times:
  list   - rglob('*.tif') over the dataset, as the product's check does
  stat   - os.stat on every file
  open   - tifffile.TiffFile(f) and len(pages) on every file, as the product's check does
and reports totals and per-file means in milliseconds. Explains, or rules out, the 36 s
writer_finalization the second 88a gate measured for 1000 files (r0-mda-files).
"""
import json
import os
import sys
import time
from pathlib import Path

import tifffile

report = {}
for folder in sys.argv[1:]:
    root = Path(folder)
    passes = []
    for attempt in range(2):
        t0 = time.perf_counter()
        files = list(root.rglob('*.tif'))
        t1 = time.perf_counter()
        for f in files:
            os.stat(f)
        t2 = time.perf_counter()
        pages = 0
        for f in files:
            with tifffile.TiffFile(f) as tif:
                pages += len(tif.pages)
        t3 = time.perf_counter()
        n = max(len(files), 1)
        passes.append(dict(files=len(files), pages=pages, list_s=round(t1 - t0, 3),
                           stat_s=round(t2 - t1, 3), open_s=round(t3 - t2, 3),
                           open_ms_per_file=round((t3 - t2) * 1000 / n, 3),
                           stat_ms_per_file=round((t2 - t1) * 1000 / n, 3)))
        print(folder, attempt, json.dumps(passes[-1]), flush=True)
    report[folder] = passes
print(json.dumps(report, indent=1))
