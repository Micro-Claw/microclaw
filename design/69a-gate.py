"""Score block 69a's saved server and browser console logs."""
from __future__ import annotations

import argparse
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
        if not any(kind == "stream silence" for kind, _, _ in records):
            raise NotExercised("the disconnect/silence warning was not captured")
        settled = 0
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
                if applied != final:
                    raise AssertionError(
                        f"browser turn {turn}: settled at {applied}, server finished at {final}"
                    )
        if not settled:
            raise NotExercised("no turn-settled browser warning was captured")
        return f"{len(records)} browser checkpoint(s) agree with server turn summaries"

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
        leaked_delta = any(
            EVENT.fullmatch(line.strip()) and EVENT.fullmatch(line.strip()).group(3) == "text_delta"
            for line in event_log_lines
        )
        if leaked_delta:
            raise AssertionError("server log contains a per-text_delta event line")
        leaked = [
            token for token in forbidden
            if token and any(token in line for line in event_log_lines)
        ]
        if leaked:
            raise AssertionError("server log contains forbidden payload marker(s): " + ", ".join(leaked))
        if not forbidden:
            raise NotExercised("supply --forbidden with the recognizable confirmation marker")
        return "event log has no text_delta line and no recognizable payload marker"

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
