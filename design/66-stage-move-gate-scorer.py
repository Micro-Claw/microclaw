"""Offline scorer for block 66 M2/demo evidence; every limb fails closed."""
from __future__ import annotations
import argparse, ast, json, math, re, sys
from pathlib import Path

PASS, FAIL, NE = "PASS", "FAIL", "NOT EXERCISED"


def load_nonempty(path: Path, label: str, binary=False):
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f"{label} is absent or empty: {path}")
    return path.read_bytes() if binary else path.read_text(encoding="utf-8-sig")


def results_from_history(text: str, device: str, target: float) -> list[dict]:
    matches = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("tool") != "move_named_stage" and row.get("name") != "move_named_stage":
            continue
        result = row.get("result") or row.get("output") or {}
        if isinstance(result, str):
            try: result = json.loads(result)
            except json.JSONDecodeError: continue
        if result.get("device") == device and math.isclose(
                float(result.get("requested_um", math.inf)), target, abs_tol=1e-6):
            matches.append(result)
    return matches


def report(items, log: Path) -> int:
    lines = [f"{name}: {state} — {reason}" for name, state, reason in items]
    body = "\n".join(lines) + "\n"
    print(body, end="")
    log.write_text(body, encoding="utf-8")
    return 0 if items and all(state == PASS for _, state, _ in items) else 1


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--history", type=Path, required=True)
    p.add_argument("--emitted", type=Path, required=True)
    p.add_argument("--capture", type=Path, required=True)
    p.add_argument("--log", type=Path, required=True)
    p.add_argument("--device", required=True)
    p.add_argument("--target", required=True, type=float)
    p.add_argument("--mode", required=True, choices=("m2-success", "m2-control", "demo"))
    a = p.parse_args(argv)
    items = []
    try:
        history = load_nonempty(a.history, "history")
    except Exception as exc:
        history = ""
        items.append(("history artifact", NE, str(exc)))
    matches = results_from_history(history, a.device, a.target)
    if len(matches) != 1:
        items.append(("matching move", NE, f"expected exactly one matching call, found {len(matches)}"))
        result = None
    else:
        items.append(("matching move", PASS, "exactly one call matched device and target"))
        result = matches[0]

    if a.mode == "m2-control":
        if result is None:
            items.append(("genuine non-response", NE, "no unique control result"))
        elif result.get("within_tolerance") is False and (
                str(result.get("last_device_status", "")).startswith("dispatch_error:") or
                result.get("start_um") == result.get("measured_um")):
            items.append(("genuine non-response", PASS, "dispatch refusal or stationary axis recorded"))
        else:
            items.append(("genuine non-response", FAIL, "control did not demonstrate non-response"))
    elif result is not None:
        required = ("start_um", "measured_um", "arrival_residual_um", "tolerance_um",
                    "band_policy", "band_source", "elapsed_s", "verification_kind")
        missing = [key for key in required if key not in result]
        items.append(("complete move record", FAIL if missing else PASS,
                      f"missing {missing}" if missing else "all required fields present"))
        if a.mode == "m2-success":
            residual = result.get("arrival_residual_um")
            if residual is None or residual <= .5:
                items.append(("discriminating residual", NE, "residual must be greater than 0.5 um"))
            else:
                items.append(("discriminating residual", PASS, f"residual={residual}"))
            expected = max(2.0, .1 * abs(a.target - float(result.get("start_um", a.target))))
            source = result.get("band_source")
            if source != "relative":
                items.append(("relative policy", NE, f"band_source={source!r}"))
            elif result.get("band_policy") != "relative" or not math.isclose(
                    float(result.get("tolerance_um", math.inf)), expected, abs_tol=1e-9):
                items.append(("relative policy", FAIL, "policy or recomputed band differs"))
            else:
                items.append(("relative policy", PASS, f"recomputed band={expected}"))
            elapsed = float(result.get("elapsed_s", math.inf))
            items.append(("fast settlement", PASS if elapsed < 1.0 else FAIL,
                          f"elapsed_s={elapsed}; required <1.0"))
        else:
            exact = result.get("within_tolerance") is True and result.get("measured_um") == a.target
            items.append(("demo instant arrival", PASS if exact else FAIL,
                          "exact verified arrival" if exact else "arrival was not exact and verified"))

    try:
        emitted = load_nonempty(a.emitted, "emitted script")
        ast.parse(emitted)
        if str(a.target) not in emitted or a.device not in emitted:
            items.append(("selected emitted move", NE, "script has no matching device/target"))
        else:
            items.append(("selected emitted move", PASS, "script parses and contains matching move"))
    except Exception as exc:
        items.append(("selected emitted move", NE, str(exc)))
    try:
        capture = load_nonempty(a.capture, "standalone capture")
        codes = re.findall(r"(?m)^EXIT_CODE=(-?\d+)\s*$", capture)
        if len(codes) != 1:
            items.append(("standalone execution", NE, f"expected one EXIT_CODE line, found {len(codes)}"))
        elif int(codes[0]) == 0:
            items.append(("standalone execution", PASS, f"EXIT_CODE={codes[0]}"))
        elif "StageMoveError" in capture:
            items.append(("standalone execution", FAIL, "typed StageMoveError"))
        else:
            items.append(("standalone execution", NE, "failed before a typed stage-move decision"))
    except Exception as exc:
        items.append(("standalone execution", NE, str(exc)))
    return report(items, a.log)


if __name__ == "__main__":
    sys.exit(main())
