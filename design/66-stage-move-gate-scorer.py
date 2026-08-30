"""Offline scorer for block 66 M2/demo evidence; every limb fails closed.

The history it reads is Microclaw's own transcript, whose shape is set by
``AuditLog.append`` (``microclaw/conversation.py``) and not by this file: each
row is an Anthropic *message*, the tool's name lives only in the assistant's
``tool_use`` block, and a result arrives as a JSON **string** inside a
``tool_result`` block -- or, for a refused call, as
``{"error": "StageMoveError: ...", "hint": ...}`` with no structured result at
all (``microclaw/errors.py``). Both shapes were read off real rig histories
before this parser was written; an earlier version invented a ``{"tool": ...,
"result": ...}`` row that nothing in the product has ever produced, and its
self-test passed because the same assumption wrote the fixture.
"""
from __future__ import annotations
import argparse, ast, json, math, re, sys
from pathlib import Path

PASS, FAIL, NE, NA = "PASS", "FAIL", "NOT EXERCISED", "NOT APPLICABLE"

NUMBER = r"[-+]?\d+(?:\.\d+)?"
# Block 66's refusal message. A record without "started ... from <source> policy"
# was written by pre-block-66 code and cannot substantiate this gate's criteria.
REFUSAL = re.compile(
    rf"started ({NUMBER}) um, requested ({NUMBER}) um, measured ({NUMBER}) um"
    rf" after ({NUMBER}) s using ({NUMBER}) um from (\w+) policy"
)


def load_nonempty(path: Path, label: str) -> str:
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f"{label} is absent or empty: {path}")
    return path.read_text(encoding="utf-8-sig")


def iter_messages(text: str):
    """Yield message dicts from a live JSONL transcript or a saved JSON array."""
    if text.lstrip().startswith("["):
        loaded = json.loads(text)
        if not isinstance(loaded, list):
            raise ValueError("saved history is not a list of messages")
        yield from (row for row in loaded if isinstance(row, dict))
        return
    parsed = False
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            parsed = True
            yield row
    if not parsed:
        raise ValueError("history contained no parseable message rows")


def block_text(block: dict) -> str:
    """A tool_result's content is a str 390 times and a list of blocks 36 times
    across the rig archive; both are real."""
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(part.get("text", "") for part in content
                       if isinstance(part, dict))
    return ""


class Call:
    def __init__(self, arguments: dict, payload, raw: str):
        self.arguments = arguments if isinstance(arguments, dict) else {}
        self.payload = payload if isinstance(payload, dict) else None
        self.raw = raw

    @property
    def refused(self) -> bool:
        return self.payload is None or "error" in self.payload

    @property
    def message(self) -> str:
        return (self.payload or {}).get("error", "") if self.payload else self.raw


def collect_calls(text: str, tool: str) -> list[Call]:
    """Pair every ``tool_result`` with the ``tool_use`` that named and drove it."""
    calls: dict[str, dict] = {}
    found: list[Call] = []
    for row in iter_messages(text):
        content = row.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                calls[block.get("id")] = block
            elif block.get("type") == "tool_result":
                origin = calls.get(block.get("tool_use_id"))
                if origin is None or origin.get("name") != tool:
                    continue
                raw = block_text(block)
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    payload = None
                found.append(Call(origin.get("input"), payload, raw))
    return found


def call_target(call: Call, tool: str):
    """The commanded coordinate, from the result where it exists and from the
    call's own arguments where the call was refused."""
    if call.payload and "requested_um" in call.payload:
        return float(call.payload["requested_um"])
    match = REFUSAL.search(call.message)
    if match:
        return float(match.group(2))
    key = "um" if tool == "move_named_stage" else "z_um"
    if call.arguments.get("absolute", True) and key in call.arguments:
        try:
            return float(call.arguments[key])
        except (TypeError, ValueError):
            return None
    return None


def call_device(call: Call, tool: str) -> str | None:
    if tool != "move_named_stage":
        return None
    if call.payload and isinstance(call.payload.get("device"), str):
        return call.payload["device"]
    device = call.arguments.get("device")
    return device if isinstance(device, str) else None


def select(calls: list[Call], tool: str, device: str, target: float) -> list[Call]:
    matched = []
    for call in calls:
        if tool == "move_named_stage" and call_device(call, tool) != device:
            continue
        value = call_target(call, tool)
        if value is None or not math.isclose(value, target, abs_tol=1e-6):
            continue
        matched.append(call)
    return matched


def emitted_settle_calls(source: str) -> list[list]:
    """Every inlined ``settle_stage_move(...)`` and its literal arguments.

    Matched structurally rather than by substring: a recorded integer target
    emits ``200`` while ``str(200.0)`` is ``"200.0"``, and 52c's grep that
    matched nothing still reported a pass.
    """
    found = []
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "settle_stage_move"):
            literals = []
            for argument in node.args:
                try:
                    literals.append(ast.literal_eval(argument))
                except (ValueError, SyntaxError):
                    literals.append(None)
            found.append(literals)
    return found


def score_emitted(path: Path, tool: str, device: str, target: float,
                  policy: str | None) -> tuple[str, str, str]:
    try:
        source = load_nonempty(path, "emitted script")
        settles = emitted_settle_calls(source)
    except Exception as exc:
        return ("selected emitted move", NE, str(exc))
    if not settles:
        return ("selected emitted move", NE, "script emits no settle_stage_move call")
    matches = [
        args for args in settles
        if len(args) >= 6
        and isinstance(args[2], (int, float))
        and math.isclose(float(args[2]), target, abs_tol=1e-6)
        and (tool != "move_named_stage" or args[1] == device)
    ]
    if len(matches) != 1:
        return ("selected emitted move", NE,
                f"expected one emitted move for the selected call, found {len(matches)}")
    emitted_start, emitted_policy = matches[0][3], matches[0][4]
    if emitted_start is None:
        return ("selected emitted move", FAIL,
                "emitted move carries no recorded start position")
    if policy is not None and emitted_policy != policy:
        return ("selected emitted move", FAIL,
                f"emitted band_policy {emitted_policy!r} differs from the run's {policy!r}")
    return ("selected emitted move", PASS,
            f"emitted start={emitted_start}, band_policy={emitted_policy!r}")


def score_capture(path: Path) -> tuple[str, str, str]:
    try:
        capture = load_nonempty(path, "standalone capture")
    except Exception as exc:
        return ("standalone execution", NE, str(exc))
    codes = re.findall(r"(?m)^EXIT_CODE=(-?\d+)\s*$", capture)
    if len(codes) != 1:
        return ("standalone execution", NE,
                f"expected one EXIT_CODE line, found {len(codes)}")
    if int(codes[0]) == 0:
        return ("standalone execution", PASS, f"EXIT_CODE={codes[0]}")
    if "StageMoveError" in capture:
        return ("standalone execution", FAIL, "typed StageMoveError")
    return ("standalone execution", NE, "failed before a typed stage-move decision")


def score_control(call: Call) -> tuple[str, str, str]:
    """A refusal is recorded as an error *string*; there is no result dict."""
    if not call.refused:
        return ("genuine non-response", FAIL,
                "the control call succeeded; the axis responded")
    message = call.message
    # A link-down axis fails its pre-dispatch read, so there is no start
    # coordinate to compare -- the refusal says so, before any Java text that
    # first-line trimming might cut. This is the strongest control shape there
    # is: the axis could not even be read, and was never commanded.
    if "start position is unavailable" in message:
        return ("genuine non-response", PASS,
                "refused before dispatch: the axis could not be read or commanded")
    match = REFUSAL.search(message)
    if not match:
        return ("genuine non-response", NE,
                "refusal predates block 66 (no start/policy in the message)")
    start, _, measured = (float(match.group(index)) for index in (1, 2, 3))
    if "dispatch_error:" in message:
        return ("genuine non-response", PASS,
                f"dispatch refusal recorded; start={start}, measured={measured}")
    if math.isclose(start, measured, abs_tol=1e-6):
        return ("genuine non-response", PASS,
                f"stationary axis: start == measured == {measured}")
    return ("genuine non-response", FAIL,
            f"axis moved from {start} to {measured}; this is not a non-response")


def score_success(call: Call, target: float, mode: str) -> list[tuple[str, str, str]]:
    items = []
    if call.refused:
        items.append(("complete move record", FAIL,
                      f"the move was refused: {call.message[:200]}"))
        return items
    result = call.payload
    required = ("start_um", "measured_um", "arrival_residual_um", "tolerance_um",
                "band_policy", "band_source", "elapsed_s", "verification_kind")
    missing = [key for key in required if key not in result]
    items.append(("complete move record", FAIL if missing else PASS,
                  f"missing {missing}" if missing else "all required fields present"))
    if missing:
        return items
    if mode == "demo":
        exact = (result.get("within_tolerance") is True
                 and math.isclose(float(result["measured_um"]), target, abs_tol=1e-9))
        items.append(("demo instant arrival", PASS if exact else FAIL,
                      "exact verified arrival"
                      if exact else "arrival was not exact and verified"))
        return items
    residual = result.get("arrival_residual_um")
    if residual is None or float(residual) <= .5:
        items.append(("discriminating residual", NE,
                      f"residual must be greater than 0.5 um; got {residual}"))
    else:
        items.append(("discriminating residual", PASS, f"residual={residual}"))
    start = result.get("start_um")
    if start is None:
        items.append(("relative policy", NE, "record carries no start_um"))
    else:
        expected = max(2.0, .1 * abs(target - float(start)))
        if result.get("band_source") != "relative":
            items.append(("relative policy", NE,
                          f"band_source={result.get('band_source')!r}; the starting "
                          "coordinate precondition was not met"))
        elif result.get("band_policy") != "relative" or not math.isclose(
                float(result.get("tolerance_um", math.inf)), expected, abs_tol=1e-9):
            items.append(("relative policy", FAIL,
                          f"policy={result.get('band_policy')!r}, band="
                          f"{result.get('tolerance_um')} against recomputed {expected}"))
        else:
            items.append(("relative policy", PASS,
                          f"recomputed band={expected} from start={start}"))
    elapsed = float(result.get("elapsed_s", math.inf))
    items.append(("fast settlement", PASS if elapsed < 1.0 else FAIL,
                  f"elapsed_s={elapsed}; required <1.0"))
    items.append(("target unchanged", PASS if math.isclose(
        float(result["requested_um"]), target, abs_tol=1e-6) else FAIL,
        f"requested_um={result['requested_um']}"))
    return items


def report(items, log: Path) -> int:
    body = "\n".join(f"{name}: {state} — {reason}" for name, state, reason in items) + "\n"
    print(body, end="")
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(body, encoding="utf-8")
    scored = [state for _, state, _ in items if state != NA]
    return 0 if scored and all(state == PASS for state in scored) else 1


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--history", type=Path, required=True)
    parser.add_argument("--emitted", type=Path)
    parser.add_argument("--capture", type=Path)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--device", default="")
    parser.add_argument("--target", required=True, type=float)
    parser.add_argument("--tool", default="move_named_stage",
                        choices=("move_named_stage", "move_stage_z"))
    parser.add_argument("--mode", required=True,
                        choices=("m2-success", "m2-control", "demo"))
    a = parser.parse_args(argv)
    if a.tool == "move_named_stage" and not a.device:
        parser.error("--device is required for move_named_stage")
    if a.mode == "m2-control":
        if a.emitted or a.capture:
            parser.error("the control limb scores a refused call, which emits no "
                         "runnable move; do not pass --emitted/--capture, and do "
                         "not reuse another run's artifacts")
    elif not (a.emitted and a.capture):
        parser.error("--emitted and --capture are required for this mode")

    items = []
    try:
        calls = collect_calls(load_nonempty(a.history, "history"), a.tool)
    except Exception as exc:
        calls = []
        items.append(("history artifact", NE, str(exc)))
    matched = select(calls, a.tool, a.device, a.target)
    if len(matched) != 1:
        items.append(("matching move", NE,
                      f"expected exactly one {a.tool} call at {a.device or 'the focus axis'}"
                      f"/{a.target}, found {len(matched)} among {len(calls)} recorded"))
        call = None
    else:
        items.append(("matching move", PASS,
                      f"exactly one {a.tool} call matched device and target"))
        call = matched[0]

    if a.mode == "m2-control":
        items.append(score_control(call) if call is not None
                     else ("genuine non-response", NE, "no unique control result"))
        return report(items, a.log)

    policy = None
    if call is not None:
        items.extend(score_success(call, a.target, a.mode))
        if not call.refused and isinstance(call.payload, dict):
            policy = call.payload.get("band_policy")
    items.append(score_emitted(a.emitted, a.tool, a.device, a.target, policy))
    items.append(score_capture(a.capture))
    return report(items, a.log)


if __name__ == "__main__":
    sys.exit(main())
