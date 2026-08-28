#!/usr/bin/env python3
"""design/58 offline limb: does a managed install launch and check with no network?

Stdlib only, and it imports nothing from ``microclaw`` -- it reads the managed
install's own files, so it scores the build under test rather than participating
in it. Run it with any Python on the machine.

    python design\\58-offline-gate.py arm        # online, with a candidate offered
    python design\\58-offline-gate.py offline    # after launching + checking offline
    python design\\58-offline-gate.py restored   # after reconnecting + checking

Each phase compares against the snapshot the previous one saved. Every limb is
reported independently -- one FAIL must not hide the limbs after it -- and the
process exits nonzero if any limb failed. A limb whose mechanism could not run
reports NOT EXERCISED, which is never a pass.

``--selftest`` builds fake trees and asserts the scoring discriminates; it is
run on a passing tree and a failing one, because a scorer that cannot fail is
not a scorer.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PASS, FAIL, NOT_EXERCISED = "PASS", "FAIL", "NOT EXERCISED"


def default_root() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "microclaw"


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None


def snapshot(root: Path) -> dict:
    """Everything a later phase needs, read once."""
    state_text = _read(root / "update-state.json")
    try:
        state = json.loads(state_text) if state_text else None
    except json.JSONDecodeError as exc:
        state = {"__unparseable__": str(exc)}
    log = _read(root / "launcher.log") or ""
    markers = {}
    for slot in ("a", "b"):
        text = _read(root / f"env-{slot}" / "microclaw-slot.json")
        try:
            markers[slot] = json.loads(text) if text else None
        except json.JSONDecodeError:
            markers[slot] = {"__unparseable__": True}
    return {
        "active": _read(root / "active-slot.txt"),
        "pending": _read(root / "pending-slot.txt"),
        "state": state,
        "markers": markers,
        "launcher_lines": [line for line in log.splitlines() if line.strip()],
    }


def _candidate(snap: dict) -> dict | None:
    state = snap.get("state") or {}
    success = state.get("last_success")
    candidate = success.get("candidate") if isinstance(success, dict) else None
    return candidate if isinstance(candidate, dict) else None


def _attempt(snap: dict):
    return (snap.get("state") or {}).get("last_attempt")


def score_offline(arm: dict, now: dict) -> list[tuple[str, str, str]]:
    limbs: list[tuple[str, str, str]] = []
    add = lambda *row: limbs.append(row)

    launched = len(now["launcher_lines"]) - len(arm["launcher_lines"])
    add("the launcher started a session while offline",
        PASS if launched > 0 else FAIL,
        f"{launched} new launcher.log line(s); last: {now['launcher_lines'][-1] if now['launcher_lines'] else '<none>'}")

    before, after = _attempt(arm), _attempt(now)
    if after is None or before is None:
        add("the offline check completed rather than hanging", NOT_EXERCISED,
            "no last_attempt recorded in one of the snapshots")
    elif after > before:
        add("the offline check completed rather than hanging", PASS,
            f"last_attempt advanced by {after - before:.1f}s")
    else:
        add("the offline check completed rather than hanging", NOT_EXERCISED,
            "last_attempt did not advance -- was 'Check for updates' clicked?")

    add("the active slot did not change while offline",
        PASS if now["active"] == arm["active"] else FAIL,
        f"{arm['active']} -> {now['active']}")

    armed_commit = (arm.get("state") or {}).get("installed_commit")
    now_commit = (now.get("state") or {}).get("installed_commit")
    add("installed_commit is unchanged",
        PASS if now_commit == armed_commit else FAIL, f"{armed_commit} -> {now_commit}")

    add("no slot was rolled back or left pending",
        PASS if not now["pending"] else FAIL, f"pending={now['pending'] or '<none>'}")

    # The interesting one. Offline, discover_clone's fetch fails but the stale
    # refs/remotes/origin/main still resolves, so it may legitimately still offer
    # a commit -- and when it does it MUST carry the warning, or the banner
    # presents possibly-superseded history as the newest thing there is.
    candidate = _candidate(now)
    if candidate is None:
        add("an offline offer carries its fetch warning", NOT_EXERCISED,
            "the offline check offered no candidate; nothing to warn about")
    else:
        warning = candidate.get("warning")
        add("an offline offer carries its fetch warning",
            PASS if warning else FAIL,
            f"candidate {str(candidate.get('sha'))[:7]}, warning={warning!r}")
    return limbs


def score_restored(offline: dict, now: dict) -> list[tuple[str, str, str]]:
    limbs: list[tuple[str, str, str]] = []
    before, after = _attempt(offline), _attempt(now)
    if before is None or after is None or after <= before:
        limbs.append(("the check recovered once the network returned", NOT_EXERCISED,
                      "last_attempt did not advance after reconnecting"))
    else:
        limbs.append(("the check recovered once the network returned", PASS,
                      f"last_attempt advanced by {after - before:.1f}s"))
    candidate = _candidate(now)
    if candidate is None:
        limbs.append(("a reconnected offer carries no stale warning", NOT_EXERCISED,
                      "no candidate after reconnecting (installed commit is current)"))
    else:
        warning = candidate.get("warning")
        limbs.append(("a reconnected offer carries no stale warning",
                      PASS if not warning else FAIL,
                      f"candidate {str(candidate.get('sha'))[:7]}, warning={warning!r}"))
    error = (now.get("state") or {}).get("last_error")
    limbs.append(("the reconnected check recorded no error",
                  PASS if not error else FAIL, f"last_error={error!r}"))
    return limbs


def run(phase: str, root: Path, work: Path) -> int:
    work.mkdir(parents=True, exist_ok=True)
    now = snapshot(root)
    (work / f"{phase}.json").write_text(json.dumps(now, indent=2, sort_keys=True), encoding="utf-8")

    lines = [f"=== design/58 offline gate -- phase {phase} ===",
             f"root={root}", f"active={now['active']} pending={now['pending'] or '<none>'}"]
    state = now.get("state") or {}
    lines.append(f"installed_commit={state.get('installed_commit')}")
    candidate = _candidate(now)
    lines.append("candidate=" + (f"{candidate.get('sha')} warning={candidate.get('warning')!r}"
                                 if candidate else "<none>"))
    lines.append(f"discovery={json.dumps(state.get('discovery'))}")

    failed = 0
    if phase == "arm":
        lines.append("")
        if candidate is None:
            lines.append(f"{NOT_EXERCISED}: no candidate is offered, so the offline phase "
                         "cannot test the warning. Click 'Check for updates' while online first.")
        else:
            lines.append(f"{PASS}: armed with candidate {str(candidate.get('sha'))[:7]}. "
                         "Now quit, disconnect the network, and relaunch.")
    else:
        previous_name = "arm" if phase == "offline" else "offline"
        previous_path = work / f"{previous_name}.json"
        if not previous_path.is_file():
            lines.append(f"\nCANNOT SCORE: {previous_path} is missing; run the {previous_name} phase first.")
            failed = 1
        else:
            previous = json.loads(previous_path.read_text(encoding="utf-8"))
            scorer = score_offline if phase == "offline" else score_restored
            lines.append("")
            for name, verdict, detail in scorer(previous, now):
                lines.append(f"  {verdict:<13} {name}\n                {detail}")
                failed += verdict == FAIL

    lines.append("")
    lines.append(f"RESULT: {'FAILED' if failed else 'no failed limbs'} "
                 f"({failed} FAIL) -- artifacts in {work}")
    report = "\n".join(lines)
    print(report)
    # The script owns its own log: PowerShell 5.1's Start-Transcript does not
    # capture a native child process's stdout, and came back empty twice on 58a.
    with (work / "gate.log").open("a", encoding="utf-8") as stream:
        stream.write(report + "\n\n")
    return 1 if failed else 0


def _fake(tmp: Path, *, active="a", pending=None, attempt=100.0, commit="a" * 40,
          candidate=None, lines=1, error=None) -> Path:
    root = tmp
    (root / "env-a").mkdir(parents=True, exist_ok=True)
    (root / "active-slot.txt").write_text(active + "\n", encoding="ascii")
    if pending:
        (root / "pending-slot.txt").write_text(pending + "\n", encoding="ascii")
    else:
        (root / "pending-slot.txt").unlink(missing_ok=True)
    state = {"installed_commit": commit, "last_attempt": attempt, "last_error": error,
             "last_success": {"candidate": candidate}}
    (root / "update-state.json").write_text(json.dumps(state), encoding="utf-8")
    (root / "launcher.log").write_text(
        "".join(f"2026-08-28T10:0{i}:00+02:00 slot={active} nonce=x\n" for i in range(lines)),
        encoding="utf-8")
    return root


def selftest() -> int:
    import tempfile
    problems = []
    with tempfile.TemporaryDirectory() as name:
        tmp = Path(name)
        offered = {"sha": "b" * 40, "subject": "s", "source": "clone", "warning": None}
        arm = snapshot(_fake(tmp / "arm", candidate=offered, lines=1))

        # Good offline tree: launched, checked, warned, nothing moved.
        warned = dict(offered, warning="Open GitHub Desktop, Fetch origin, then Check again.")
        good = snapshot(_fake(tmp / "good", candidate=warned, attempt=200.0, lines=2))
        verdicts = {name: verdict for name, verdict, _ in score_offline(arm, good)}
        if set(verdicts.values()) != {PASS}:
            problems.append(f"good tree did not pass cleanly: {verdicts}")

        # Bad tree: never launched, never checked, slot flipped, no warning.
        bad = snapshot(_fake(tmp / "bad", active="b", pending="a", candidate=offered,
                             attempt=100.0, commit="c" * 40, lines=1))
        verdicts = {name: verdict for name, verdict, _ in score_offline(arm, bad)}
        expected = {
            "the launcher started a session while offline": FAIL,
            "the offline check completed rather than hanging": NOT_EXERCISED,
            "the active slot did not change while offline": FAIL,
            "installed_commit is unchanged": FAIL,
            "no slot was rolled back or left pending": FAIL,
            "an offline offer carries its fetch warning": FAIL,
        }
        if verdicts != expected:
            problems.append(f"bad tree mis-scored:\n  got      {verdicts}\n  expected {expected}")

        # A missing candidate is NOT EXERCISED, never a pass.
        none = snapshot(_fake(tmp / "none", candidate=None, attempt=200.0, lines=2))
        verdicts = {name: verdict for name, verdict, _ in score_offline(arm, none)}
        if verdicts["an offline offer carries its fetch warning"] != NOT_EXERCISED:
            problems.append("a missing candidate must be NOT EXERCISED")

        # restored: a warning that survives reconnection is a FAIL.
        after = snapshot(_fake(tmp / "after", candidate=warned, attempt=300.0, lines=2))
        verdicts = {name: verdict for name, verdict, _ in score_restored(good, after)}
        if verdicts["a reconnected offer carries no stale warning"] != FAIL:
            problems.append("a stale warning after reconnecting must FAIL")
        clean = snapshot(_fake(tmp / "clean", candidate=offered, attempt=300.0, lines=2))
        verdicts = {name: verdict for name, verdict, _ in score_restored(good, clean)}
        if set(verdicts.values()) != {PASS}:
            problems.append(f"clean restored tree did not pass: {verdicts}")

    for problem in problems:
        print("SELFTEST FAILURE:", problem)
    print("selftest:", "FAILED" if problems else "ok")
    return 1 if problems else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("phase", nargs="?", choices=("arm", "offline", "restored"))
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--work", type=Path, default=None)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    if not args.phase:
        parser.error("a phase is required (arm, offline, restored) unless --selftest")
    root = args.root or default_root()
    if not (root / "update-state.json").is_file():
        print(f"No managed installation at {root} (no update-state.json).")
        return 1
    work = args.work or Path(os.environ.get("TEMP", "/tmp")) / "mc-offline-gate"
    return run(args.phase, root, work)


if __name__ == "__main__":
    sys.exit(main())
