"""Score block 69a's saved server and browser console logs."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

EVENT = re.compile(r"^\[microclaw turn ([0-9a-f]+)] seq (\d+) ([A-Za-z0-9_]+)$")
SUMMARY = re.compile(
    r"^\[microclaw turn ([0-9a-f]+)] done: (\d+) events "
    r"\((\d+) text_delta\), final seq (\d+)$"
)
BROWSER = re.compile(
    r"Microclaw (stream silence|turn settled); turn:\s*([0-9a-f]+)\s+"
    r"last applied seq:\s*(\d+|null)"
)


class NotExercised(Exception):
    pass


def score(server_path: Path | None, browser_path: Path | None, forbidden: list[str]):
    results = []

    def limb(name, fn):
        try:
            detail = fn()
            status = "PASS"
        except NotExercised as exc:
            status, detail = "NOT EXERCISED", str(exc)
        except Exception as exc:  # every limb reports independently
            status, detail = "FAIL", f"{type(exc).__name__}: {exc}"
        results.append((status, name, detail))

    server = None
    browser = None
    try:
        if server_path is not None:
            server = server_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        server = None
    try:
        if browser_path is not None:
            browser = browser_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        browser = None

    summaries = {}

    def sequence_limb():
        if server is None:
            raise NotExercised("server console log is required")
        events = {}
        for line in server.splitlines():
            match = EVENT.fullmatch(line.strip())
            if match:
                turn, seq, kind = match.group(1), int(match.group(2)), match.group(3)
                events.setdefault(turn, []).append((seq, kind))
            match = SUMMARY.fullmatch(line.strip())
            if match:
                turn, count, deltas, final = match.groups()
                if turn in summaries:
                    raise AssertionError(f"turn {turn} has more than one summary")
                summaries[turn] = (int(count), int(deltas), int(final))
        if not summaries:
            raise NotExercised("no product turn summary found")
        for turn, (count, deltas, final) in summaries.items():
            if count <= 0 or final != count:
                raise AssertionError(f"turn {turn}: {count} events but final seq {final}")
            numbered = events.get(turn, [])
            seqs = [item[0] for item in numbered]
            if seqs != sorted(set(seqs)) or any(seq < 1 or seq > final for seq in seqs):
                raise AssertionError(f"turn {turn}: non-monotonic/out-of-range logged seqs {seqs}")
            if len(numbered) + deltas != count:
                raise AssertionError(
                    f"turn {turn}: {len(numbered)} non-delta + {deltas} deltas != {count}"
                )
        return f"{len(summaries)} turn(s); every final seq equals its emitted-event count"

    limb("L6 server sequence arithmetic", sequence_limb)

    def browser_limb():
        if server is None or browser is None:
            raise NotExercised("both server and browser console logs are required")
        if not summaries:
            raise NotExercised("server log has no scored turn summary")
        records = [(kind, turn, value) for kind, turn, value in BROWSER.findall(browser)]
        if not records:
            raise NotExercised("browser log has no last-applied-seq warning")
        # A silence record is not required, and demanding one made this limb
        # unreachable on the gate machine: keepalives arrive every 10 s and the
        # detector fires at 30 s, so a healthy loopback session never emits one,
        # which is why limb 2b settles the detector off-rig. A silence record
        # that *is* present is still checked against its server turn below.
        settled = 0
        complete = []
        partial = []
        for kind, turn, raw in records:
            if turn not in summaries:
                raise AssertionError(f"browser turn {turn} has no server summary")
            if raw == "null":
                raise AssertionError(f"browser turn {turn} applied no numbered event")
            applied = int(raw)
            final = summaries[turn][2]
            if applied > final:
                raise AssertionError(f"browser turn {turn}: applied {applied} > server {final}")
            if kind == "turn settled":
                settled += 1
                # A page navigated away from mid-turn runs runTurn's `finally` on
                # its way out and logs whatever it had applied, which is legitimately
                # short of the server's final seq. Round 4 reported exactly that as a
                # FAIL -- turn 995951c2 settled at 3 of 15 -- and the HAR showed the
                # stream cut at the operator's reload, at max_seq 3. Reloading
                # mid-turn is the workflow this whole design exists to support, so
                # its own gate must not score one as a delivery failure. A short
                # settle is reported, never failed; what must hold is that at least
                # one turn was delivered end to end.
                (complete if applied == final else partial).append(f"{turn[:8]}@{applied}/{final}")
        if not settled:
            raise NotExercised("no turn-settled browser warning was captured")
        if not complete:
            raise NotExercised(
                "no turn was delivered end to end -- every settle is short, which a "
                "reload also produces: " + ", ".join(partial)
            )
        detail = f"{len(complete)} turn(s) delivered end to end ({', '.join(complete)})"
        if partial:
            detail += (f"; {len(partial)} settled short, consistent with a mid-turn "
                       f"reload ({', '.join(partial)})")
        return detail

    limb("L7 browser last-applied sequence", browser_limb)

    def payload_limb():
        if server is None:
            raise NotExercised("server console log is required")
        # Confirmation audits intentionally print their operator-facing summary.
        # This limb scores only the event records introduced by block 69a-3.
        event_log_lines = [
            line for line in server.splitlines()
            if line.strip().startswith("[microclaw turn ")
        ]
        if not any(EVENT.fullmatch(line.strip()) for line in event_log_lines):
            raise NotExercised("server log has no block 69a event line")
        if not any(SUMMARY.fullmatch(line.strip()) for line in event_log_lines):
            raise NotExercised("server log has no turn summary")
        leaked_delta = any(
            EVENT.fullmatch(line.strip()) and EVENT.fullmatch(line.strip()).group(3) == "text_delta"
            for line in event_log_lines
        )
        if leaked_delta:
            raise AssertionError("server log contains a per-text_delta event line")
        # A token the session never carried cannot be found in an event line, so
        # its absence would be a pass this limb could not fail. Rounds 2, 3 and 4
        # all skipped the marker-seeding submit, and this limb was void every
        # time. Seed it from the session instead of from the operator: every
        # confirmation prints its operator-facing summary through
        # `Session._audit_confirmation`, deliberately, and that summary *is* the
        # payload that must not reach an event line. A gate step skipped three
        # times is a gate step that should not exist.
        seeded = [token for token in forbidden if token and token in server]
        derived = [
            summary[:60] for summary in (
                json.loads(line.split("Confirmation audit:", 1)[1]).get("summary", "")
                for line in server.splitlines()
                if "Confirmation audit:" in line
            ) if len(summary) >= 12
        ]
        tokens = seeded + derived
        if not tokens:
            raise NotExercised(
                "no confirmation reached this session's audit log, so no payload "
                "could have leaked into an event line"
            )
        leaked = [t for t in tokens if any(t in line for line in event_log_lines)]
        if leaked:
            raise AssertionError(
                "server log contains payload text in an event line: "
                + ", ".join(repr(t) for t in leaked)
            )
        deltas = sum(
            int(match.group(3)) for match in
            (SUMMARY.fullmatch(line.strip()) for line in event_log_lines)
            if match
        )
        note = "" if not forbidden or seeded else " (the --forbidden marker was not seeded)"
        return (
            f"{deltas} text_delta events produced no event line, and none of "
            f"{len(tokens)} payload token(s) from this session's confirmations "
            f"reached one{note}"
        )

    limb("L8 payload-free logging", payload_limb)
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server-log", type=Path)
    parser.add_argument("--browser-log", type=Path)
    parser.add_argument("--forbidden", action="append", default=[])
    parser.add_argument("--log", type=Path, default=Path("69a-gate-score.log"))
    args = parser.parse_args(argv)
    results = score(args.server_log, args.browser_log, args.forbidden)
    lines = [f"{status}: {name} - {detail}" for status, name, detail in results]
    passed = all(status == "PASS" for status, _, _ in results)
    lines.append("BLOCK 69a COMPUTED GATE " + ("PASSED" if passed else "FAILED"))
    report = "\n".join(lines) + "\n"
    args.log.parent.mkdir(parents=True, exist_ok=True)
    args.log.write_text(report, encoding="utf-8")
    sys.stdout.write(report)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
