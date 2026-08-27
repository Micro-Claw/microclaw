"""Block 58e diagnostic spike: why did a slot CLI take longer than 30 seconds?

Runs in about a minute, touches no production state, needs no Prepare, no
install.bat and no staging.  It answers four questions the gate could not:

1. Does each slot's `check-config --json` -- the exact call
   `compare_slot_configurations` makes during staging -- return, and how fast?
2. Does it hang only when stdin is inherited rather than /dev/null?  A slot CLI
   that reaches `pause_on_exit`'s `input()` blocks forever on a console stdin.
3. Does `MICROCLAW_FROM_SHORTCUT=1` change the answer?  The desktop launcher
   sets it, so every subprocess the *server* spawns inherits it -- including
   this classifier call.
4. How often does `write_state`'s `os.replace` lose to a concurrent reader?
   `[WinError 5] Access is denied` on exactly that replace is what killed the
   round-2 staging job.

Run:  uv run python design\58-block58e-spike.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(os.environ["LOCALAPPDATA"]) / "microclaw"
CONFIG = Path(os.environ["APPDATA"]) / "microclaw" / "safety_config.yaml"
TIMEOUT = 45


def timed(label: str, command: list[str], *, env=None, stdin_null=True) -> dict:
    full = dict(os.environ)
    full.update(env or {})
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=TIMEOUT,
            env=full, stdin=subprocess.DEVNULL if stdin_null else None,
        )
        elapsed = time.monotonic() - started
        result = {
            "label": label, "seconds": round(elapsed, 2),
            "returncode": completed.returncode,
            "stdout": completed.stdout.strip()[:300],
            "stderr": completed.stderr.strip()[:300],
        }
    except subprocess.TimeoutExpired:
        result = {"label": label, "seconds": TIMEOUT, "returncode": "TIMED OUT",
                  "stdout": "", "stderr": ""}
    except OSError as exc:
        result = {"label": label, "seconds": round(time.monotonic() - started, 2),
                  "returncode": f"OSError: {exc}", "stdout": "", "stderr": ""}
    print(f"  {result['seconds']:>6.2f}s  rc={result['returncode']}  {label}")
    if result["stdout"]:
        print(f"          stdout: {result['stdout']}")
    if result["stderr"]:
        print(f"          stderr: {result['stderr']}")
    return result


def probe_slots() -> list[dict]:
    print("\n1-3. Slot CLI probes (read-only; check-config writes nothing)\n")
    results = []
    for slot in ("a", "b"):
        exe = ROOT / f"env-{slot}" / "Scripts" / "microclaw.exe"
        python = ROOT / f"env-{slot}" / "Scripts" / "python.exe"
        if not exe.is_file():
            print(f"  env-{slot}: no microclaw.exe -- skipped")
            continue
        classify = [str(exe), "--safety-config", str(CONFIG), "check-config", "--json"]
        results += [
            timed(f"env-{slot} python -I -c 'import microclaw'",
                  [str(python), "-I", "-c", "import microclaw; print(microclaw.__file__)"]),
            timed(f"env-{slot} microclaw.exe --help", [str(exe), "--help"]),
            timed(f"env-{slot} check-config --json  (stdin=NUL)", classify),
            timed(f"env-{slot} check-config --json  (stdin inherited)",
                  classify, stdin_null=False),
            timed(f"env-{slot} check-config --json  (MICROCLAW_FROM_SHORTCUT=1, stdin=NUL)",
                  classify, env={"MICROCLAW_FROM_SHORTCUT": "1"}),
            timed(f"env-{slot} check-config --json  (MICROCLAW_FROM_SHORTCUT=1, stdin inherited)",
                  classify, env={"MICROCLAW_FROM_SHORTCUT": "1"}, stdin_null=False),
        ]
    return results


def probe_replace_contention() -> dict:
    """Reproduce the WinError 5 that killed round 2's staging, in a scratch dir."""
    print("\n4. os.replace under a concurrent reader (scratch directory only)\n")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from microclaw import updates

    work = Path(tempfile.mkdtemp(prefix="58e-spike-"))
    target = work / "update-state.json"
    updates.write_state({"provenance": "public-head"}, target)
    stop = threading.Event()
    reads = {"count": 0, "errors": 0}

    def reader():
        while not stop.is_set():
            try:
                updates.load_state(target)
                reads["count"] += 1
            except Exception:
                reads["errors"] += 1

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    failures, attempts, first = 0, 300, None
    for index in range(attempts):
        try:
            updates.write_state({"provenance": "public-head", "n": index}, target)
        except OSError as exc:
            failures += 1
            first = first or str(exc)
    stop.set()
    thread.join(timeout=5)
    print(f"  {failures} of {attempts} writes failed while a reader was polling")
    print(f"  ({reads['count']} reads, {reads['errors']} read errors)")
    if first:
        print(f"  first failure: {first}")
    return {"attempts": attempts, "failures": failures, "first_failure": first,
            "reads": reads["count"], "read_errors": reads["errors"]}


def main() -> int:
    print(f"managed root: {ROOT}")
    print(f"config:       {CONFIG}  (exists: {CONFIG.is_file()})")
    active = (ROOT / "active-slot.txt")
    print(f"active slot:  {active.read_text().strip() if active.is_file() else 'unknown'}")
    report = {"slots": probe_slots(), "replace": probe_replace_contention()}
    out = Path.home() / "Documents" / "58e-spike.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nWrote {out}. Send that file back.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
