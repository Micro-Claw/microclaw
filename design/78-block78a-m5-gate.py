"""Score block 78a's M5 gate from a Micro-Manager CoreLog at debug level.

design/78 asks for three otherwise-identical runs -- no write, write with the
per-frame GUI refresh, write without it -- and for the cadence and
write-latency distributions, the absence of the EMU read fan-out, and
**whether it reappears on another thread**. All of that is computation over one
CoreLog, so it is a program rather than copy-paste steps: it reports every limb
independently, never lets one refusal hide the others, and exits nonzero on any
FAIL or NOT EXERCISED.

The grammar below is read off M5's own CoreLog20260904T110001_pid18460.txt --
the log design/78 was scored from -- not from our caller's assumptions. Real
lines, verbatim:

    ...T14:13:24.944211 tid548 [dbg,Core:dev:Laser Trigger] Will set property "Duration0 (us)" to "0"
    ...T14:13:24.944250 tid548 [dbg,Core:dev:Laser Trigger] Did set property "Duration0 (us)" to "0"
    ...T14:13:24.944990 tid548 [dbg,Core] Waiting for device Laser Trigger...
    ...T14:13:24.944996 tid548 [dbg,Core] Finished waiting for device Laser Trigger
    ...T14:13:24.949810 tid548 [IFO,App] Updating GUI; config pad = true; from cache = true
    ...T14:13:25.017455 tid15000 [dbg,App] [EMU] -- Retrieved MMProperty [PIZStage-Position] value: [50.1182].
    ...T14:13:25.066254 tid548 [IFO,App] Finished updating GUI

Note tid15000 in that sample: some EMU retrieval ALREADY happens off the
calling thread in the with-refresh arm. That is exactly why the fan-out limb
counts per thread and does not assume a single tid.

Usage (PowerShell, on the rig):

    uv run python design\78-block78a-m5-gate.py --corelog <path> ^
        --arms block78a-evidence\arms.json --out block78a-evidence
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

# One line: timestamp, tid, [LEVEL,component] message
LINE = re.compile(
    r"^(?P<ts>\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d+)\s+"
    r"(?P<tid>tid\d+)\s+\[(?P<level>[A-Za-z]+),(?P<comp>[^\]]*)\]\s*(?P<msg>.*)$"
)
WILL_SET = re.compile(r'Will set property "(?P<prop>[^"]+)" to "(?P<value>[^"]*)"')
DID_SET = re.compile(r'Did set property "(?P<prop>[^"]+)" to "(?P<value>[^"]*)"')
EMU_READ = re.compile(r"\[EMU\] -- Retrieved MMProperty \[(?P<pair>[^\]]+)\]")
DEV_COMP = re.compile(r"^Core:dev:(?P<device>.+)$")


def parse_ts(text: str) -> datetime:
    return datetime.fromisoformat(text)


class Event:
    __slots__ = ("ts", "tid", "level", "comp", "msg", "lineno")

    def __init__(self, ts, tid, level, comp, msg, lineno):
        self.ts, self.tid, self.level = ts, tid, level
        self.comp, self.msg, self.lineno = comp, msg, lineno


def read_corelog(path: Path):
    """Stream the log; it is routinely 280k+ lines."""
    events = []
    with path.open(encoding="utf-8", errors="replace") as handle:
        for lineno, raw in enumerate(handle, 1):
            m = LINE.match(raw.rstrip("\n"))
            if m is None:
                continue          # continuation lines of a multi-line message
            events.append(Event(parse_ts(m["ts"]), m["tid"], m["level"],
                                m["comp"], m["msg"], lineno))
    return events


def window(events, start: datetime, end: datetime):
    return [e for e in events if start <= e.ts <= end]


# EXPOSURE MARKERS, read off M5's own log -- NOT guessed, and NOT taken from
# design/78's M2 note, which quotes "[Snap Image] called". That string does not
# occur even once in M5's CoreLog: this scorer's first draft used it and would
# have found zero exposures on the rig, reporting every cadence limb NOT
# EXERCISED after the dose had already been spent. The real M5 forms are below,
# with their exact occurrence counts in CoreLog20260904T110001_pid18460.txt.
SNAP_START = "Will snap image from current camera"            # 11 in that log
SNAP_END = "Did snap image from current camera"               # 11
SEQ_START = "Did start continuous sequence acquisition"       # 23
SEQ_STOP = "Will stop sequence acquisition from current camera"  # 23


def snaps(events):
    """Exposure starts, whichever way this run drove the camera.

    A snap-per-frame run marks each exposure; a sequence acquisition marks the
    burst, so a sequenced arm yields one 'exposure' per burst and its cadence
    limb reports n=1 rather than a per-frame distribution. That is a true
    statement about the run, not a parsing failure -- and the write-to-exposure
    span, which is what this block changes, is measured against the next
    exposure marker either way.
    """
    return [e for e in events
            if e.msg.startswith(SNAP_START) or e.msg.startswith(SEQ_START)]


def writes(events, device: str, prop: str):
    """(will, did) pairs for one device/property, in order."""
    out, pending = [], None
    for e in events:
        dev = DEV_COMP.match(e.comp)
        if dev is None or dev["device"] != device:
            continue
        w = WILL_SET.search(e.msg)
        if w is not None and w["prop"] == prop:
            pending = e
            continue
        d = DID_SET.search(e.msg)
        if d is not None and d["prop"] == prop and pending is not None:
            out.append((pending, e))
            pending = None
    return out


def gui_updates(events):
    """(start, end) pairs of MMStudio's own repaint."""
    out, pending = [], None
    for e in events:
        if e.msg.startswith("Updating GUI;"):
            pending = e
        elif e.msg.startswith("Finished updating GUI") and pending is not None:
            out.append((pending, e))
            pending = None
    return out


def emu_reads(events):
    return [(e, EMU_READ.search(e.msg)["pair"]) for e in events
            if EMU_READ.search(e.msg) is not None]


def reads_of(events, device: str, prop: str):
    """Read-backs of one pair, counted from EMU's own retrieval line.

    A bare COM read cannot be attributed to a property; EMU names the pair.
    """
    target = f"{device}-{prop}"
    return [e for e, pair in emu_reads(events) if pair == target]


def stats(values):
    if not values:
        return None
    ordered = sorted(values)
    def pct(f):
        return ordered[min(len(ordered) - 1, int(f * (len(ordered) - 1) + 0.5))]
    return {
        "n": len(ordered),
        "min_s": round(ordered[0], 6),
        "median_s": round(pct(0.5), 6),
        "p95_s": round(pct(0.95), 6),
        "max_s": round(ordered[-1], 6),
        "mean_s": round(sum(ordered) / len(ordered), 6),
    }


class Report:
    """Every limb reports independently; one refusal never hides the rest."""

    def __init__(self):
        self.limbs = []

    def add(self, name, status, detail, would_fail=None, data=None):
        assert status in ("PASS", "FAIL", "NOT EXERCISED")
        self.limbs.append({"limb": name, "status": status, "detail": detail,
                           "would_have_failed_if": would_fail, "data": data})
        print(f"{status:<14} {name}\n               {detail}")

    def finish(self, out_dir: Path):
        passes = sum(1 for l in self.limbs if l["status"] == "PASS")
        total = len(self.limbs)
        print(f"\n{passes}/{total} PASS")
        for l in self.limbs:
            if l["status"] != "PASS":
                print(f"  {l['status']}: {l['limb']}")
        (out_dir / "score.json").write_text(
            json.dumps({"limbs": self.limbs, "passed": passes, "total": total},
                       indent=2), encoding="utf-8")
        # NOT EXERCISED is never a pass.
        return 0 if passes == total else 1


def score_arm(events, arm, report: Report, out_dir: Path):
    """Per-arm measurements. `arm` carries the operator-recorded window."""
    name = arm["name"]
    device, prop = arm["device"], arm["property"]
    win = window(events, parse_ts(arm["start"]), parse_ts(arm["end"]))
    if not win:
        report.add(f"{name}: window covers CoreLog lines", "NOT EXERCISED",
                   f"no CoreLog lines between {arm['start']} and {arm['end']}; "
                   "the arm's recorded window and this log disagree",
                   "an empty window")
        return None

    exposures = snaps(win)
    pairs = writes(win, device, prop)
    cadence = [(b.ts - a.ts).total_seconds()
               for a, b in zip(exposures, exposures[1:])]
    latency = [(did.ts - will.ts).total_seconds() for will, did in pairs]

    # Per write: everything between the write and the NEXT exposure. That is
    # the span design/78 attributed, and the one the block set out to shrink.
    per_write = []
    for will, did in pairs:
        nxt = next((e for e in exposures if e.ts > did.ts), None)
        if nxt is None:
            continue
        between = [e for e in win if did.ts < e.ts <= nxt.ts]
        fan = emu_reads(between)
        by_tid = {}
        for e, pair in fan:
            by_tid.setdefault(e.tid, []).append(pair)
        per_write.append({
            "write_line": will.lineno,
            "write_tid": did.tid,
            "emu_reads_on_write_thread": len(by_tid.get(did.tid, [])),
            "write_to_exposure_s": round((nxt.ts - did.ts).total_seconds(), 6),
            "gui_updates": len(gui_updates(between)),
            "emu_reads": len(fan),
            "emu_reads_by_tid": {k: len(v) for k, v in by_tid.items()},
            "read_backs_of_target": len(reads_of(between, device, prop)),
        })

    measured = {
        "arm": name,
        "commit": arm.get("commit"),
        "refresh_in_source": arm.get("refresh_in_source"),
        "device": device, "property": prop,
        "exposures": len(exposures),
        "writes": len(pairs),
        "frame_cadence_s": stats(cadence),
        "write_latency_s": stats(latency),
        "write_to_exposure_s": stats([w["write_to_exposure_s"] for w in per_write]),
        "per_write": per_write,
    }
    (out_dir / f"arm-{name}.json").write_text(
        json.dumps(measured, indent=2), encoding="utf-8")
    report.add(f"{name}: measured", "PASS",
               f"{len(exposures)} exposures, {len(pairs)} writes; cadence "
               f"{measured['frame_cadence_s']}; write latency "
               f"{measured['write_latency_s']}",
               "no exposures or no parseable window", measured)
    return measured


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--corelog", required=True, type=Path)
    ap.add_argument("--arms", required=True, type=Path,
                    help="JSON list of {name, start, end, device, property, "
                         "commit, refresh_in_source}")
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)

    report = Report()
    events = read_corelog(args.corelog)
    debug_lines = sum(1 for e in events if e.level.lower() == "dbg")
    if debug_lines == 0:
        report.add("CoreLog is at debug level", "NOT EXERCISED",
                   f"{len(events)} parsed lines and none at [dbg,...]. This log "
                   "cannot show device reads, so no fan-out limb below means "
                   "anything. Enable debug logging and repeat the runs.",
                   "a log with no dbg lines")
        return report.finish(args.out)
    report.add("CoreLog is at debug level", "PASS",
               f"{debug_lines} of {len(events)} parsed lines are [dbg,...]",
               "a log with no dbg lines")

    arms = {a["name"]: a for a in json.loads(args.arms.read_text())}
    measured = {}
    for name in ("no-write", "write-with-refresh", "write-without-refresh"):
        if name not in arms:
            report.add(f"{name}: present", "NOT EXERCISED",
                       f"no arm named {name!r} in {args.arms}", "a missing arm")
            continue
        got = score_arm(events, arms[name], report, args.out)
        if got is not None:
            measured[name] = got

    on = measured.get("write-with-refresh")
    off = measured.get("write-without-refresh")
    base = measured.get("no-write")

    # The arms must actually be the trees they claim. A gate that scored the
    # same build twice would report a clean improvement of zero and look fine.
    if on and off:
        if on.get("refresh_in_source") is True and off.get("refresh_in_source") is False:
            report.add("the two write arms ran different trees", "PASS",
                       f"with-refresh at {on.get('commit')} has refresh_gui in "
                       f"_apply_property; without-refresh at {off.get('commit')} "
                       "does not",
                       "both arms reporting the same refresh_in_source")
        else:
            report.add("the two write arms ran different trees", "FAIL",
                       f"refresh_in_source was {on.get('refresh_in_source')!r} and "
                       f"{off.get('refresh_in_source')!r}; at least one arm ran "
                       "the wrong checkout, so every comparison below is void",
                       "both arms reporting the same refresh_in_source")

    # CONTROL. If the with-refresh arm shows no fan-out, this gate measured
    # nothing at all and its other limbs are vacuous -- design/59a's lesson.
    if on:
        fan_on = sum(w["emu_reads"] for w in on["per_write"])
        gui_on = sum(w["gui_updates"] for w in on["per_write"])
        if fan_on > 0 and gui_on > 0:
            report.add("control: the with-refresh arm reproduces the fan-out",
                       "PASS",
                       f"{gui_on} GUI updates and {fan_on} EMU retrievals between "
                       f"write and exposure across {len(on['per_write'])} writes",
                       "zero EMU reads or zero GUI updates in the with-refresh arm")
        else:
            report.add("control: the with-refresh arm reproduces the fan-out",
                       "NOT EXERCISED",
                       f"{gui_on} GUI updates and {fan_on} EMU retrievals; this "
                       "rig did not reproduce design/78's condition, so the "
                       "without-refresh arm proves nothing here",
                       "zero EMU reads or zero GUI updates in the with-refresh arm")

    if off:
        gui_off = sum(w["gui_updates"] for w in off["per_write"])
        report.add("no GUI update between a write and its exposure", 
                   "PASS" if gui_off == 0 else "FAIL",
                   f"{gui_off} 'Updating GUI' windows across "
                   f"{len(off['per_write'])} writes",
                   "any GUI update in that span")

        # design/78 asks whether the work reappears on another thread. Asking
        # that as "zero EMU reads anywhere" is WRONG, and M2 proved it: EMU
        # polls continuously on its own thread at a rate that has nothing to do
        # with us -- measured at ~4.2/s on M2, including 502 reads during a
        # two-minute idle gap with no acquisition running at all. A short
        # write->exposure window catches a couple of those by coincidence, and
        # round 1 of this gate failed on exactly two such reads.
        #
        # The causal question is whether the fan-out still blocks OUR write
        # path, and that is crisp: zero EMU reads on the very thread that
        # performed the write, between the write and its exposure. On M2 that
        # went from 49 per write to 0 while the background thread carried on
        # unchanged.
        on_thread = sum(w["emu_reads_on_write_thread"] for w in off["per_write"])
        other = sum(w["emu_reads"] - w["emu_reads_on_write_thread"]
                    for w in off["per_write"])
        before = (sum(w["emu_reads_on_write_thread"] for w in on["per_write"])
                  if on else None)
        background = None
        if base is not None and "no-write" in arms:
            span = (parse_ts(arms["no-write"]["end"])
                    - parse_ts(arms["no-write"]["start"])).total_seconds()
            base_win = window(events, parse_ts(arms["no-write"]["start"]),
                              parse_ts(arms["no-write"]["end"]))
            background = round(len(emu_reads(base_win)) / span, 2) if span else None
        report.add("the read fan-out no longer blocks the write path",
                   "PASS" if on_thread == 0 else "FAIL",
                   f"{on_thread} EMU retrievals on the writing thread between "
                   f"write and exposure across {len(off['per_write'])} writes "
                   f"(with-refresh arm: {before}). {other} on other threads, "
                   f"against a no-write background of {background} EMU reads/s "
                   "measured in this same log -- background polling is not our "
                   "cost, so it is reported and not failed.",
                   "any EMU retrieval on the writing thread in that span")

        # This counter reads EMU's own "[EMU] -- Retrieved MMProperty [dev-prop]"
        # line, which comes from the htSMLM/EMU plugin rather than from MMCore.
        # MMCore's strings are identical across rigs (same build), EMU's are not
        # guaranteed to be. If the with-refresh arm shows no read-backs at all,
        # the counter cannot see them on this machine and "exactly one" would
        # pass vacuously -- the same shape as block 78b's gate defect, which
        # passed a control that never reached the engine. Say NOT EXERCISED.
        seen_before = sum(w["read_backs_of_target"] for w in on["per_write"]) if on else 0
        extra = [w for w in off["per_write"] if w["read_backs_of_target"] > 1]
        if seen_before == 0:
            report.add("EMU no longer re-reads the target after each write",
                       "NOT EXERCISED",
                       "the with-refresh arm recorded zero read-backs of "
                       f"{off['device']}.{off['property']}, so this rig's EMU "
                       "does not emit the "
                       "line this counter reads and 'exactly one' would pass "
                       "without measuring anything",
                       "a counter that cannot see a read-back it knows is there")
        else:
            report.add("EMU no longer re-reads the target after each write",
                       "PASS" if not extra else "FAIL",
                       f"{len(extra)} of {len(off['per_write'])} writes read the "
                       f"target more than once; the with-refresh arm recorded "
                       f"{seen_before} read-backs across {len(on['per_write'])} "
                       "writes, so the counter demonstrably works on this rig",
                       "any write reading its own property twice")

    # Report the residual as measured. design/78 is explicit that the ~0.3 s
    # figure is a hypothesis to compare against, never a threshold to pass.
    if base and on and off:
        summary = {k: (v["frame_cadence_s"] or {}).get("median_s")
                   for k, v in (("no-write", base), ("with-refresh", on),
                                ("without-refresh", off))}
        report.add("cadence reported for all three arms", "PASS",
                   f"median frame cadence: {summary}. design/78's ~0.3 s/frame "
                   "extrapolation is a hypothesis for comparison, NOT a "
                   "pass/fail threshold; the residual above the no-write arm is "
                   "reported, not subtracted away.",
                   "a missing arm")
    return report.finish(args.out)


if __name__ == "__main__":
    sys.exit(main())
