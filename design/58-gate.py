#!/usr/bin/env python3
"""design/58 rig gate: the limbs that need a person at the machine.

Stdlib only, and it imports nothing from ``microclaw`` -- it reads the managed
install's own files, so it scores the build under test rather than participating
in it. Run it with any Python on the machine.

    python design\\58-gate.py arm        # online, with a candidate offered
    python design\\58-gate.py offline    # after launching + checking offline
    python design\\58-gate.py restored   # after reconnecting + checking

    python design\\58-gate.py staged     # after pressing "Restart later"
    python design\\58-gate.py activated  # after the next ordinary desktop launch

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
PHASE_PREDECESSOR = {"offline": "arm", "restored": "offline", "activated": "staged"}
PHASES = ("arm", "offline", "restored", "staged", "activated")


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
        # The child writes this after it starts, carrying the nonce the launcher
        # generated for *this* start. launcher.log is written ten lines before
        # Start-Process, so a new log line proves the launcher ran, not that the
        # application did -- design/58's own rule, and the first version of this
        # script broke it.
        "health": _read(root / "launch-health.txt"),
        "state": state,
        "markers": markers,
        "launcher_lines": [line for line in log.splitlines() if line.strip()],
    }


def _started(snap: dict, previous: dict) -> tuple[str, str]:
    """Did the application itself start since `previous`?

    Never scored on a new launcher.log line: updater-launcher.ps1 writes that at
    line 46 and spawns the child at line 56, so the line proves the launcher
    ran.  launch-health.txt carries the nonce the launcher minted for *this*
    start and is written by the child once it is up.
    """
    if len(snap["launcher_lines"]) - len(previous["launcher_lines"]) <= 0:
        return FAIL, "no new launcher.log line: the launcher itself never ran"
    nonce, health = _launch_nonce(snap), snap.get("health")
    if nonce is None:
        return NOT_EXERCISED, "the last launcher.log line carries no nonce to match"
    return (PASS if health == nonce else FAIL,
            f"launch-health.txt={health!r} vs this start's nonce={nonce!r}")


def _launch_nonce(snap: dict) -> str | None:
    """The nonce on the most recent launcher.log line."""
    for line in reversed(snap.get("launcher_lines") or []):
        for field in line.split():
            if field.startswith("nonce="):
                return field[len("nonce="):]
    return None


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

    add("the application started while offline", *_started(now, arm))

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


def score_activated(staged: dict, now: dict) -> list[tuple[str, str, str]]:
    """`Restart later`: an ordinary desktop launch must consume the selector.

    The mechanism is evidenced many times over by launcher-driven restarts. What
    this scores is the button path -- the operator declining the restart and the
    *next manual launch* activating what was staged.
    """
    limbs: list[tuple[str, str, str]] = []
    pending = staged.get("pending")
    if not pending or pending == staged.get("active"):
        limbs.append(("Restart later left a distinct pending slot", NOT_EXERCISED,
                      f"at the staged phase: active={staged.get('active')} "
                      f"pending={pending or '<none>'} -- nothing was staged to activate"))
        return limbs
    limbs.append(("Restart later left a distinct pending slot", PASS,
                  f"active={staged['active']} pending={pending}"))
    limbs.append(("the application started on the next ordinary launch", *_started(now, staged)))
    limbs.append(("that launch activated the staged slot",
                  PASS if now.get("active") == pending else FAIL,
                  f"active {staged['active']} -> {now.get('active')}, expected {pending}"))
    limbs.append(("the pending selector was consumed",
                  PASS if not now.get("pending") else FAIL,
                  f"pending={now.get('pending') or '<none>'}"))
    marker = (staged["markers"].get(pending) or {}).get("commit")
    installed = (now.get("state") or {}).get("installed_commit")
    if not marker:
        limbs.append(("installed_commit was reconciled from the activated slot",
                      NOT_EXERCISED, f"env-{pending} had no readable slot marker when staged"))
    else:
        limbs.append(("installed_commit was reconciled from the activated slot",
                      PASS if installed == marker else FAIL,
                      f"env-{pending} marker={marker[:7]}, installed_commit={str(installed)[:7]}"))
    previous = staged.get("active")
    kept = (now["markers"].get(previous) or {}).get("commit")
    limbs.append(("the previous slot is preserved as a rollback target",
                  PASS if kept else FAIL,
                  f"env-{previous} marker={str(kept)[:7] if kept else '<missing>'}"))
    return limbs


def run(phase: str, root: Path, work: Path) -> int:
    work.mkdir(parents=True, exist_ok=True)
    now = snapshot(root)
    (work / f"{phase}.json").write_text(json.dumps(now, indent=2, sort_keys=True), encoding="utf-8")

    lines = [f"=== design/58 gate -- phase {phase} ===",
             f"root={root}", f"active={now['active']} pending={now['pending'] or '<none>'}"]
    state = now.get("state") or {}
    lines.append(f"installed_commit={state.get('installed_commit')}")
    candidate = _candidate(now)
    lines.append("candidate=" + (f"{candidate.get('sha')} warning={candidate.get('warning')!r}"
                                 if candidate else "<none>"))
    lines.append(f"discovery={json.dumps(state.get('discovery'))}")

    failed = 0
    if phase == "staged":
        lines.append("")
        if now["pending"] and now["pending"] != now["active"]:
            lines.append(f"{PASS}: staged into slot {now['pending']}. Now quit Microclaw and "
                         "launch it from the desktop icon. Do NOT run install.bat.")
        else:
            lines.append(f"{NOT_EXERCISED}: no distinct pending slot. Press Update, wait for "
                         "'ready to restart', then press Restart later before running this.")
    elif phase == "arm":
        lines.append("")
        if candidate is None:
            lines.append(f"{NOT_EXERCISED}: no candidate is offered, so the offline phase "
                         "cannot test the warning. Click 'Check for updates' while online first.")
        else:
            lines.append(f"{PASS}: armed with candidate {str(candidate.get('sha'))[:7]}. "
                         "Now quit, disconnect the network, and relaunch.")
    else:
        previous_name = PHASE_PREDECESSOR[phase]
        previous_path = work / f"{previous_name}.json"
        if not previous_path.is_file():
            lines.append(f"\nCANNOT SCORE: {previous_path} is missing; run the {previous_name} phase first.")
            failed = 1
        else:
            previous = json.loads(previous_path.read_text(encoding="utf-8"))
            scorer = {"offline": score_offline, "restored": score_restored,
                      "activated": score_activated}[phase]
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
          candidate=None, lines=1, error=None, healthy=True, markers=None) -> Path:
    root = tmp
    (root / "env-a").mkdir(parents=True, exist_ok=True)
    for slot, slot_commit in (markers or {}).items():
        (root / f"env-{slot}").mkdir(parents=True, exist_ok=True)
        (root / f"env-{slot}" / "microclaw-slot.json").write_text(
            json.dumps({"commit": slot_commit, "required_launcher_protocol": 1}), encoding="utf-8")
    (root / "active-slot.txt").write_text(active + "\n", encoding="ascii")
    if pending:
        (root / "pending-slot.txt").write_text(pending + "\n", encoding="ascii")
    else:
        (root / "pending-slot.txt").unlink(missing_ok=True)
    state = {"installed_commit": commit, "last_attempt": attempt, "last_error": error,
             "last_success": {"candidate": candidate}}
    (root / "update-state.json").write_text(json.dumps(state), encoding="utf-8")
    (root / "launcher.log").write_text(
        "".join(f"2026-08-28T10:0{i}:00+02:00 slot={active} nonce=n{i}\n" for i in range(lines)),
        encoding="utf-8")
    # The child's marker matches this start's nonce only when it actually ran.
    (root / "launch-health.txt").write_text(
        (f"n{lines - 1}" if healthy else "n-stale") + "\n", encoding="ascii")
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

        # A launcher that logged its intent and spawned nothing must FAIL: the
        # defect this limb originally had, now the thing it discriminates.
        spawned_nothing = snapshot(_fake(tmp / "nospawn", candidate=warned, attempt=200.0,
                                         lines=2, healthy=False))
        verdicts = {n: v for n, v, _ in score_offline(arm, spawned_nothing)}
        if verdicts["the application started while offline"] != FAIL:
            problems.append("a launcher that spawned nothing must FAIL, not pass on its own log line")

        # Bad tree: never launched, never checked, slot flipped, no warning.
        bad = snapshot(_fake(tmp / "bad", active="b", pending="a", candidate=offered,
                             attempt=100.0, commit="c" * 40, lines=1))
        verdicts = {name: verdict for name, verdict, _ in score_offline(arm, bad)}
        expected = {
            "the application started while offline": FAIL,
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

        # --- Restart later ---
        old, new = "a" * 40, "b" * 40
        both = {"a": old, "b": new}
        staged = snapshot(_fake(tmp / "staged", active="a", pending="b", commit=old,
                                lines=1, markers=both))
        activated = snapshot(_fake(tmp / "activated", active="b", pending=None, commit=new,
                                   lines=2, markers=both))
        verdicts = {n: v for n, v, _ in score_activated(staged, activated)}
        if set(verdicts.values()) != {PASS}:
            problems.append(f"a clean Restart later did not pass: {verdicts}")

        # The launch happened but the selector was not consumed: the limb's point.
        not_activated = snapshot(_fake(tmp / "notactivated", active="a", pending="b",
                                       commit=old, lines=2, markers=both))
        verdicts = {n: v for n, v, _ in score_activated(staged, not_activated)}
        if verdicts["that launch activated the staged slot"] != FAIL:
            problems.append("a launch that did not activate the staged slot must FAIL")
        if verdicts["the pending selector was consumed"] != FAIL:
            problems.append("an unconsumed pending selector must FAIL")

        # Nothing staged at all is NOT EXERCISED, and must not report five more limbs.
        nothing = snapshot(_fake(tmp / "nothingstaged", active="a", pending=None, markers=both))
        rows = score_activated(nothing, activated)
        if len(rows) != 1 or rows[0][1] != NOT_EXERCISED:
            problems.append(f"an unstaged tree must report one NOT EXERCISED row, got {rows}")

    for problem in problems:
        print("SELFTEST FAILURE:", problem)
    print("selftest:", "FAILED" if problems else "ok")
    return 1 if problems else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("phase", nargs="?", choices=PHASES)
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--work", type=Path, default=None)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    if not args.phase:
        parser.error(f"a phase is required ({', '.join(PHASES)}) unless --selftest")
    root = args.root or default_root()
    if not (root / "update-state.json").is_file():
        print(f"No managed installation at {root} (no update-state.json).")
        return 1
    work = args.work or Path(os.environ.get("TEMP", "/tmp")) / "mc-offline-gate"
    return run(args.phase, root, work)


if __name__ == "__main__":
    sys.exit(main())
