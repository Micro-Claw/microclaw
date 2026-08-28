"""Score block 59b's three driven-session history JSONL files."""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
import traceback
from pathlib import Path

ROOT = Path(os.environ.get("MICROCLAW_TREE_UNDER_TEST", Path(__file__).resolve().parents[1])).resolve()
sys.path.insert(0, str(ROOT))
from microclaw.conversation import load_history

RESULTS = []


class NotExercised(Exception): pass


def limb(name, fails_if):
    def decorate(fn):
        try: detail, status = fn() or "", "PASS"
        except NotExercised as exc: detail, status = str(exc), "NOT EXERCISED"
        except Exception as exc:
            detail, status = f"{type(exc).__name__}: {exc}", "FAIL"
            traceback.print_exc()
        RESULTS.append({"name": name, "status": status, "detail": detail,
                        "fails_if": fails_if})
        print(f"{status}: {name} - {detail}")
        return fn
    return decorate


def blocks(message):
    content = message.get("content", "")
    if isinstance(content, str): return [{"type": "text", "text": content}]
    return [b if isinstance(b, dict) else getattr(b, "model_dump", lambda: {})()
            for b in content]


def text(message):
    return " ".join(str(b.get("text", "")) for b in blocks(message)
                    if b.get("type") == "text")


def tool_uses(messages, name=None):
    return [(i, b) for i, m in enumerate(messages, 1) for b in blocks(m)
            if b.get("type") == "tool_use" and (name is None or b.get("name") == name)]


def tool_results(messages):
    uses = {b.get("id"): (i, b.get("name")) for i, b in tool_uses(messages)}
    out = []
    for i, message in enumerate(messages, 1):
        for block in blocks(message):
            if block.get("type") != "tool_result": continue
            raw = block.get("content", "")
            if isinstance(raw, list):
                raw = next((x.get("text", "") for x in raw if isinstance(x, dict)
                            and x.get("type") == "text"), "")
            try: value = json.loads(raw) if isinstance(raw, str) else raw
            except Exception: value = {}
            use_index, name = uses.get(block.get("tool_use_id"), (None, None))
            out.append((i, use_index, name, value))
    return out


def orientation(messages):
    found = [(i, value) for i, _, name, value in tool_results(messages)
             if name == "get_system_state" and isinstance(value, dict)]
    if not found: raise NotExercised("no get_system_state payload in history")
    return found[0]


def assistant_after(messages, index, before=None):
    return [(i, text(m)) for i, m in enumerate(messages, 1) if i > index
            and (before is None or i < before) and m.get("role") == "assistant" and text(m)]


def first_exposure(messages):
    calls = [(i, b.get("name")) for i, b in tool_uses(messages)
             if b.get("name") in {"snap_and_analyze", "run_timelapse", "run_zstack"}]
    return calls[0][0] if calls else None


def main():
    RESULTS.clear()
    parser = argparse.ArgumentParser()
    parser.add_argument("session_a", type=Path)
    parser.add_argument("session_b", type=Path)
    parser.add_argument("session_c", type=Path)
    parser.add_argument("session_b_confirmations", type=Path)
    parser.add_argument("--output", type=Path, default=Path("block59b-session-score.json"))
    args = parser.parse_args()
    sessions = [load_history(p).messages for p in (args.session_a, args.session_b, args.session_c)]
    a, b, c = sessions
    confirmation_records = load_history(args.session_b_confirmations).messages

    @limb("opening prompts do not prescribe scored outcomes", "operator prompt contains routing/objective/lock trigger vocabulary")
    def uncontaminated_openings():
        forbidden = re.compile(r"routing|light\s+path|\bpath\b|objective|focus\s+lock|autofocus|establish", re.I)
        for label, messages in zip("ABC", sessions):
            if not messages or messages[0].get("role") != "user":
                raise NotExercised(f"session {label} has no opening user message")
            opening = text(messages[0])
            assert not forbidden.search(opening), f"session {label}: {opening!r}"
            try:
                _, state = orientation(messages)
            except NotExercised:
                continue
            device_names = [str(x.get("device", "")) for x in
                            state.get("optical_path", {}).get("discrete_positions", [])]
            assert not any(name and name.casefold() in opening.casefold() for name in device_names), (
                label, opening, device_names)
        return "A/B/C openings contain no scored-outcome triggers or StateDevice names"

    @limb("A/B conditional reference pair", "reference is called while imaging works or omitted after blank signal")
    def reference_pair():
        a_calls = tool_uses(a, "get_optical_path_documentation")
        b_calls = tool_uses(b, "get_optical_path_documentation")
        assert not a_calls, a_calls
        assert b_calls, "session B never called the reference"
        return f"A calls=0; B first call message={b_calls[0][0]}"

    @limb("A routing question precedes first exposure", "routing is asserted/asked late rather than raised from orientation")
    def a_routing():
        oi, payload = orientation(a); exposure = first_exposure(a)
        if exposure is None: raise NotExercised("session A made no exposure")
        route = next((x for x in payload.get("optical_path", {}).get("discrete_positions", [])
                      if any("light-path candidate" in r for r in x.get("role", []))), None)
        if route is None: raise NotExercised("orientation found no routing candidate")
        replies = assistant_after(a, oi, exposure)
        labels = [str(x).lower() for x in route.get("allowed", [])]
        question = next((t for _, t in replies if "?" in t and
                         ("position" in t.lower() or any(x in t.lower() for x in labels))), None)
        assert question, (oi, exposure, replies)
        assert not any(phrase in question.lower() for phrase in ("is the camera", "routes to the camera"))
        return f"orientation={oi}, question before exposure={exposure}"

    @limb("A controls", "non-routing device draws routing question or hardware lock is not proposed before imaging")
    def a_controls():
        oi, payload = orientation(a); exposure = first_exposure(a) or len(a) + 1
        replies = " ".join(t for _, t in assistant_after(a, oi, exposure)).lower()
        nonrouting = [x["device"].lower() for x in payload["optical_path"]["discrete_positions"]
                      if not any("light-path candidate" in r for r in x.get("role", []))]
        assert not any(name in replies and "what" in replies and "position" in replies
                       for name in nonrouting), nonrouting
        assert ("hardware" in replies and ("focus lock" in replies or "autofocus" in replies))
        return "five non-routing controls quiet; hardware lock proposed before imaging"

    @limb("B physical-path response before second exposure", "blank-frame response repeats exposure before checking physical path")
    def b_blank():
        exposures = [i for i, block in tool_uses(b) if block.get("name") in
                     {"snap_and_analyze", "run_timelapse", "run_zstack"}]
        if not exposures: raise NotExercised("blank-frame session took no exposure at all")
        # A session that never re-exposes is the STRONGEST pass, not an
        # unexercised one: the criterion is that the physical path is raised
        # before a second exposure, and not taking one satisfies it outright.
        # Requiring two exposures would score the ideal behaviour NOT EXERCISED,
        # and the runbook treats NOT EXERCISED as a failed limb.
        second = exposures[1] if len(exposures) > 1 else len(b) + 1
        between = " ".join(text(m) for i, m in enumerate(b, 1)
                           if exposures[0] < i < second and m.get("role") == "assistant").lower()
        assert any(word in between for word in ("physical", "prism", "slider", "light path")), between
        assert "software" in between and any(word in between for word in
                                               ("readable", "state", "values")), between
        reexposed = "no second exposure" if len(exposures) == 1 else f"second exposure at {second}"
        return f"software state reported and physical path raised after exposure {exposures[0]}; {reexposed}"

    @limb("B resolved save confirmation", "save is absent/declined or confirmation omits resolved identity/mapping")
    def b_save():
        saves = [(i, value) for i, _, name, value in tool_results(b) if name == "save_knowledge"]
        if not saves: raise NotExercised("session B never reached save_knowledge")
        saved_value = saves[-1][1].get("value", {}) if isinstance(saves[-1][1], dict) else {}
        observed = saved_value.get("observed_on") if isinstance(saved_value, dict) else None
        if not isinstance(observed, dict):
            raise AssertionError(f"save result lacks value.observed_on: {saves[-1][1]!r}")
        confirmations = [record for record in confirmation_records
                         if isinstance(record, dict) and record.get("kind") == "knowledge"]
        if not confirmations: raise NotExercised("session B confirmation audit has no knowledge record")
        if any(str(c.get("decision", "")).startswith("declined") for c in confirmations):
            raise NotExercised("operator declined save_knowledge")
        summaries = " ".join(str(c.get("summary", "")) for c in confirmations)
        for field in ("camera_adapter", "device", "adapter", "allowed", "positions"):
            assert field in summaries, summaries
        for field, value in observed.items():
            assert field in summaries and str(value) in summaries, (field, value, summaries)
        return f"complete resolved identity shown at message {saves[-1][0]}"

    @limb("C fresh-session resolved mapping", "raw map leaks into context, payload lacks map, or agent asks again")
    def c_mapping():
        raw = json.dumps(c)
        assert "optical_path_position_map" not in raw, "raw structured entry leaked into history/context"
        oi, payload = orientation(c)
        mapped = [x for x in payload.get("optical_path", {}).get("discrete_positions", [])
                  if x.get("position_map")]
        if not mapped: raise NotExercised("session C payload contains no applicable position map")
        later = " ".join(t for _, t in assistant_after(c, oi)).lower()
        assert "?" not in later or not ("what" in later and "position" in later), later
        assert any(str(v).lower() in later for v in mapped[0]["position_map"].values()), later
        return f"mapping in payload message {oi}; used without re-asking"

    args.output.write_text(json.dumps({"results": RESULTS}, indent=2) + "\n", encoding="utf-8")
    failed = [x for x in RESULTS if x["status"] != "PASS"]
    print("BLOCK 59b SESSION SCORE " + ("FAILED" if failed else "PASSED"))
    return 1 if failed else 0


if __name__ == "__main__": raise SystemExit(main())
