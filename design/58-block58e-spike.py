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
import shutil
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(os.environ["LOCALAPPDATA"]) / "microclaw"
CONFIG = Path(os.environ["APPDATA"]) / "microclaw" / "safety_config.yaml"
TIMEOUT = 45


def timed(label: str, command: list[str], *, env=None, stdin_null=True, cwd=None) -> dict:
    full = dict(os.environ)
    full.update(env or {})
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=TIMEOUT,
            env=full, stdin=subprocess.DEVNULL if stdin_null else None, cwd=cwd,
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


#: The two edits that fix the hang, looked for in the code a slot actually runs.
FIX_MARKERS = {
    # Must be strings the PRE-fix code does not contain.  Spike round 3 used
    # `args.command == "check-config"`, which is also the old dispatch line, so
    # it reported the fix present in two slots that predate it.
    "config.py": "stdin=subprocess.DEVNULL",
    "__main__.py": 'getattr(args, "json", False)',
}


def slot_code_identity(slot: str) -> dict:
    """Which code is installed in this slot, and does it carry the fix?

    Spike round 2 could not distinguish "the fix does not work" from "the slot
    still holds the old build", because nothing recorded what was installed.
    One run should answer that without a second trip.
    """
    site = ROOT / f"env-{slot}" / "Lib" / "site-packages" / "microclaw"
    marker = ROOT / f"env-{slot}" / "microclaw-slot.json"
    identity = {"slot": slot, "commit": None, "has_fix": {}}
    try:
        identity["commit"] = json.loads(marker.read_text(encoding="utf-8")).get("commit")
    except (OSError, ValueError):
        pass
    for name, needle in FIX_MARKERS.items():
        try:
            identity["has_fix"][name] = needle in (site / name).read_text(
                encoding="utf-8", errors="replace")
        except OSError:
            identity["has_fix"][name] = None
    return identity


def probe_checkout(repo: Path) -> list[dict]:
    """Run the *checkout's* CLI in the configuration that hangs.

    This is the decisive probe: it exercises the fixed source directly, so it
    answers whether the fix works on this machine without reinstalling anything.
    """
    print("\n0. The checkout's own CLI (no install needed)\n")
    command = [sys.executable, "-m", "microclaw",
               "--safety-config", str(CONFIG), "check-config", "--json"]
    return [
        timed("checkout check-config --json  (MICROCLAW_FROM_SHORTCUT=1, stdin=NUL)",
              command, env={"MICROCLAW_FROM_SHORTCUT": "1"}, cwd=repo),
        timed("checkout check-config --json  (MICROCLAW_FROM_SHORTCUT=1, stdin inherited)",
              command, env={"MICROCLAW_FROM_SHORTCUT": "1"}, stdin_null=False, cwd=repo),
    ]


def probe_exit_pause(repo: Path) -> list[dict]:
    """Does MICROCLAW_UPDATE_RESTART=1 actually suppress the exit pause?

    `check-config` **without** `--json` still registers the pause, so this runs
    the real mechanism.  The second probe is the control: without the flag it
    MUST hang.  A suppression probe whose control also passes has measured
    nothing -- 58a shipped an opt-out limb that could not fail, and this design
    is not repeating it.
    """
    print("\n0b. Exit-pause suppression, with a control that must hang\n")
    command = [sys.executable, "-m", "microclaw",
               "--safety-config", str(CONFIG), "check-config"]
    shortcut = {"MICROCLAW_FROM_SHORTCUT": "1"}
    results = [
        timed("pause suppressed  (FROM_SHORTCUT=1 + UPDATE_RESTART=1, stdin inherited)",
              command, env={**shortcut, "MICROCLAW_UPDATE_RESTART": "1"},
              stdin_null=False, cwd=repo),
        timed("CONTROL: must hang (FROM_SHORTCUT=1 only, stdin inherited)",
              command, env=shortcut, stdin_null=False, cwd=repo),
    ]
    suppressed, control = results
    if control["returncode"] != "TIMED OUT":
        print("  INCONCLUSIVE: the control did NOT hang, so this probe never "
              "reached the exit pause and the result above proves nothing. "
              "Expected when stdin is not a real console.")
        suppressed["inconclusive"] = True
    elif suppressed["returncode"] != "TIMED OUT":
        # Not `== 0`: `check-config` without --json exits 1 whenever the config
        # is not `ready`, which is an ordinary state on a real machine. What is
        # being measured is whether the process *returned*, not how it exited.
        print(f"  Suppression works: the control hung and the suppressed run "
              f"returned in {suppressed['seconds']}s (exit {suppressed['returncode']}).")
    else:
        print("  SUPPRESSION FAILED: both runs hung. MICROCLAW_UPDATE_RESTART=1 "
              "did not skip the exit pause.")
    return results


def probe_staging_dry_run(repo: Path) -> dict:
    """Run the real `stage_inactive_slot` against a scratch root.

    Nothing here writes to the live install.  The scratch root gets a junction
    to the real `env-a` so the comparison's *active* side is the operator's own
    CLI, exactly as production has it, while the candidate is built fresh and
    `pending-slot.txt` is published inside the scratch directory where nothing
    will ever read it.  This is the one mechanism the block depends on that has
    never completed on any machine: round 1 died in `uv venv`, round 2 in the
    classifier it invokes.
    """
    print("\n5. Full staging dry run into a scratch root (live install untouched)\n")
    sys.path.insert(0, str(repo))
    from microclaw import config as mc_config
    from microclaw import updates

    real_state = ROOT / "update-state.json"
    if not real_state.is_file():
        print("  no managed install -- skipped")
        return {"skipped": "no managed install"}
    state = json.loads(real_state.read_text(encoding="utf-8-sig"))
    success = (state.get("last_success") or {}).get("candidate")
    if not isinstance(success, dict):
        print("  no cached candidate; run Microclaw once so a check populates it")
        return {"skipped": "no cached candidate"}
    candidate = updates.Candidate(**success)

    scratch = Path(tempfile.mkdtemp(prefix="58e-dryrun-"))
    report = {"scratch": str(scratch), "candidate": candidate.sha}
    started = time.monotonic()
    try:
        (scratch / "active-slot.txt").write_text("a\n", encoding="ascii")
        for name in ("launcher-protocol.txt", "update-state.json"):
            shutil.copy2(ROOT / name, scratch / name)
        # A junction on Windows, a symlink elsewhere -- so this whole path can be
        # exercised off-rig before it is ever pointed at a real installation.
        if os.name == "nt":
            link = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(scratch / "env-a"), str(ROOT / "env-a")],
                capture_output=True, text=True, check=False,
            )
            report["junction"] = link.stdout.strip() or link.stderr.strip()
        else:
            os.symlink(ROOT / "env-a", scratch / "env-a", target_is_directory=True)
            report["junction"] = "symlink (non-Windows)"
        if not (scratch / "env-a" / "Scripts" / "microclaw.exe").is_file():
            print(f"  could not junction env-a: {report['junction']}")
            return {**report, "skipped": "junction failed"}

        source = scratch / "source"
        updates.materialize_clone(state, candidate, source)
        report["materialize_seconds"] = round(time.monotonic() - started, 2)
        print(f"  materialized {candidate.sha[:7]} in {report['materialize_seconds']}s")

        uv = shutil.which("uv")
        if not uv:
            print("  uv is not on PATH -- cannot build; skipped")
            return {**report, "skipped": "uv not found"}
        build_started = time.monotonic()
        target = updates.stage_inactive_slot(
            scratch, source, candidate, uv_executable=uv, config_path=CONFIG,
        )
        report["build_seconds"] = round(time.monotonic() - build_started, 2)
        report["pending"] = (scratch / "pending-slot.txt").read_text(encoding="ascii").strip()
        report["built_marker"] = json.loads(
            (Path(target) / "microclaw-slot.json").read_text(encoding="utf-8"))
        print(f"  BUILT and staged in {report['build_seconds']}s -> pending={report['pending']}")

        fresh = Path(target) / "Scripts" / "microclaw.exe"
        report["fresh_cli"] = timed(
            "freshly built slot check-config --json (FROM_SHORTCUT=1, stdin inherited)",
            [str(fresh), "--safety-config", str(CONFIG), "check-config", "--json"],
            env={"MICROCLAW_FROM_SHORTCUT": "1"}, stdin_null=False,
        )
        active_exe = scratch / "env-a" / "Scripts" / "microclaw.exe"
        # ClassificationComparison carries only `proceed` and `reason`, so the
        # two classifications are gathered separately -- and they are the more
        # useful evidence anyway.
        comparison = mc_config.compare_slot_configurations(active_exe, fresh, CONFIG)
        report["comparison"] = {
            "proceed": comparison.proceed,
            "reason": comparison.reason,
            "active": mc_config.classify_config_with_slot(active_exe, CONFIG),
            "candidate": mc_config.classify_config_with_slot(fresh, CONFIG),
        }
        print(f"  comparison: {report['comparison']}")
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        print(f"  FAILED: {report['error']}")
    finally:
        # The junction MUST be gone before anything recursive touches `scratch`.
        # `env-a` points at the operator's live installation; a recursive delete
        # that followed it would destroy the running install on a machine this
        # work can already brick.  If the junction survives, leave the whole
        # scratch tree alone and say so -- a stale temp directory is free, and
        # the alternative is not.
        junction = scratch / "env-a"
        if os.name == "nt":
            subprocess.run(["cmd", "/c", "rmdir", str(junction)],
                           capture_output=True, check=False)
        elif junction.is_symlink():
            junction.unlink()
        if junction.exists():
            report["cleanup"] = f"junction survived; left {scratch} in place ON PURPOSE"
            print(f"  NOT deleting {scratch}: {junction} is still a link to your "
                  f"live env-a. Remove it by hand with:  rmdir \"{junction}\"")
        else:
            shutil.rmtree(scratch, ignore_errors=True)
            report["cleanup"] = "scratch removed"
        # Independent proof that the live installation is untouched either way.
        live = ROOT / "env-a" / "Scripts" / "microclaw.exe"
        report["live_env_a_intact"] = live.is_file()
        if not report["live_env_a_intact"]:
            print(f"  WARNING: {live} is missing. Restore from the safety backup.")
    report["total_seconds"] = round(time.monotonic() - started, 2)
    return report


def probe_slots() -> list[dict]:
    print("\n1-3. Slot CLI probes (read-only; check-config writes nothing)\n")
    results = []
    for slot in ("a", "b"):
        identity = slot_code_identity(slot)
        print(f"  env-{slot}: commit={identity['commit']} fix present={identity['has_fix']}")
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
    repo = Path(__file__).resolve().parents[1]
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=False)
    print(f"checkout:     {repo} @ {head.stdout.strip()[:12] or 'unknown'}")
    report = {
        "checkout": {"path": str(repo), "head": head.stdout.strip(),
                     "probes": probe_checkout(repo)},
        "slot_identity": [slot_code_identity(slot) for slot in ("a", "b")],
        "exit_pause": probe_exit_pause(repo),
        "slots": probe_slots(),
        "replace": probe_replace_contention(),
        "staging_dry_run": probe_staging_dry_run(repo),
    }
    out = Path.home() / "Documents" / "58e-spike.json"
    verdict = report["checkout"]["probes"][-1]
    print("\nVERDICT: the fix " + (
        "WORKS on this machine (the checkout answered in "
        f"{verdict['seconds']}s in the configuration that used to hang)."
        if verdict["returncode"] == 0 else
        "DID NOT WORK: the checkout still hangs in that configuration."))
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nWrote {out}. Send that file back.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
