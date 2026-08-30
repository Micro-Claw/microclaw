"""Reproduce (and then score the fix for) the knowledge base's lost-update race.

design/35's open register: ``knowledge_manager.save_entry`` loads the whole
document, edits it and rewrites it.  design/64 made the *write* atomic
(``_write_knowledge`` replaces the file), which closed the torn-read window and
left the read-modify-write untouched.  Two launches -- and microclaw is required
to open more than once (CLAUDE.md) -- can therefore each load the same document,
each add a different key, and each write their own version back, so whichever
writes second silently deletes the other's unrelated edit.

This probe is state-free and needs no rig.  It runs two real processes, each
writing its own disjoint set of keys into the same file, and counts how many of
those keys survive.  Every key is written by exactly one process and no key is
written twice, so the correct answer is *all of them*; anything less is an edit
that was dropped.

    python design/64-kb-lost-update-probe.py

Exits 0 when nothing was lost, 1 otherwise.  Run it on the pre-fix tree to watch
it fail: that is the evidence the fix is a fix (CLAUDE.md, step 3).
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

WORKERS = 2
WRITES_PER_WORKER = 40
# The race window is the load+dump of the *whole* document, so a document with
# some bulk in it widens the window the way a real session's knowledge base
# does.  This is not a trick to manufacture the defect: it is the same code path
# with a realistic amount of data in it.
PRIMED_ENTRIES = 200


def _worker(kb_path: Path, tag: str) -> int:
    import microclaw.knowledge_manager as km

    km.KNOWLEDGE_PATH = kb_path
    for index in range(WRITES_PER_WORKER):
        km.save_entry("samples", f"{tag}-{index}", {"worker": tag, "index": index})
    return 0


def main() -> int:
    if len(sys.argv) == 3:  # re-entry as a worker
        return _worker(Path(sys.argv[1]), sys.argv[2])

    with tempfile.TemporaryDirectory() as scratch:
        kb_path = Path(scratch) / "knowledge.yaml"

        import microclaw.knowledge_manager as km

        km.KNOWLEDGE_PATH = kb_path
        for index in range(PRIMED_ENTRIES):
            km.save_entry("strategies", f"primed-{index}", {"index": index})

        env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parent.parent)}
        children = [
            subprocess.Popen(
                [sys.executable, __file__, str(kb_path), f"w{worker}"],
                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            for worker in range(WORKERS)
        ]
        failed = False
        for child in children:
            _out, err = child.communicate()
            if child.returncode != 0:
                failed = True
                print(f"worker exited {child.returncode}:\n{err[-2000:]}")

        data = km.load_knowledge()
        samples = data.get("samples") or {}
        primed = data.get("strategies") or {}

        expected = WORKERS * WRITES_PER_WORKER
        missing = [
            f"w{worker}-{index}"
            for worker in range(WORKERS)
            for index in range(WRITES_PER_WORKER)
            if f"w{worker}-{index}" not in samples
        ]
        print(f"primed entries surviving : {len(primed)}/{PRIMED_ENTRIES}")
        print(f"worker entries surviving : {len(samples)}/{expected}")
        print(f"lost updates             : {len(missing)}")
        if missing:
            print(f"  first few missing      : {missing[:8]}")
        if failed:
            print("RESULT: a worker crashed -- inconclusive")
            return 1
        if missing or len(primed) != PRIMED_ENTRIES:
            print("RESULT: LOST UPDATES -- the read-modify-write is not serialized")
            return 1
        print("RESULT: no lost updates")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
