"""Block 58b demo gate: offline classification and the nine-cell guard."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from microclaw.config import compare_config_classifications

RESULTS: list[tuple[str, str, str]] = []


class NotExercised(Exception):
    pass


class Tee:
    def __init__(self, stream, path: Path):
        self.stream = stream
        self.file = path.open("w", encoding="utf-8")

    def write(self, data):
        self.stream.write(data)
        self.file.write(data)
        return len(data)

    def flush(self):
        self.stream.flush()
        self.file.flush()


def limb(name):
    def decorate(fn):
        try:
            RESULTS.append((name, "PASS", fn() or ""))
        except NotExercised as exc:
            RESULTS.append((name, "NOT EXERCISED", str(exc)))
        except Exception as exc:
            RESULTS.append((name, "FAIL", f"{type(exc).__name__}: {exc}"))
            traceback.print_exc()
        return fn
    return decorate


def invoke(executable: Path, config_path: Path, *, json_mode: bool):
    command = [str(executable), "--safety-config", str(config_path), "check-config"]
    if json_mode:
        command.append("--json")
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NotExercised(f"could not invoke {executable}: {exc}") from exc


def parse_one_object(stdout: str) -> dict:
    decoder = json.JSONDecoder()
    value, end = decoder.raw_decode(stdout)
    if stdout[end:].strip() or not isinstance(value, dict):
        raise AssertionError(f"stdout was not exactly one JSON object: {stdout!r}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--active-exe", default=None)
    parser.add_argument("--candidate-exe", default=None)
    args = parser.parse_args()
    repo, out = Path(args.repo), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    sys.stdout = sys.stderr = Tee(sys.__stdout__, out / "gate.txt")

    discovered = shutil.which("microclaw")
    managed = (
        Path(os.environ["LOCALAPPDATA"]) / "microclaw" / "env" /
        "Scripts" / "microclaw.exe"
        if os.environ.get("LOCALAPPDATA") else None
    )
    active = Path(args.active_exe) if args.active_exe else managed
    candidate = Path(args.candidate_exe or discovered) if (args.candidate_exe or discovered) else None
    print("=" * 70)
    print("ENVIRONMENT")
    print("=" * 70)
    print(f"repo: {repo}")
    print(f"python: {sys.executable}")
    print(f"active CLI: {active or '(not found)'}")
    print(f"candidate CLI: {candidate or '(not found)'}")

    documents = {
        "missing": out / "missing.yaml",
        "blocked": out / "blocked.yaml",
        "ready": out / "ready.yaml",
    }
    base = (
        "schema_version: 3\n"
        "reviewed: {reviewed}\n"
        "property_authorization: {{mode: guaranteed, allowed_categorical: [], denied: []}}\n"
        "stage: {{x_min: -101.0, x_max: 101.0}}\n"
        "acquisition:\n"
        "  max_frames: 10001\n  max_duration_s: 3601\n"
        "  max_bytes: 50000000001\n  max_illuminated_ms: 600001\n"
        "  max_session_illuminated_ms: 1800001\n"
        "  confirm_above_frames: 501\n  confirm_above_duration_s: 301\n"
        "  confirm_above_bytes: 5000000001\n"
        "  confirm_above_illuminated_ms: 60001\n"
    )
    documents["blocked"].write_text(base.format(reviewed="false"), encoding="utf-8")
    documents["ready"].write_text(base.format(reviewed="true"), encoding="utf-8")
    documents["missing"].unlink(missing_ok=True)

    for classification, path in documents.items():
        @limb(f"JSON classification: {classification}")
        def _(classification=classification, path=path):
            if active is None or not active.is_file():
                raise NotExercised("the installed active-slot CLI was not found")
            completed = invoke(active, path, json_mode=True)
            if completed.returncode != 0:
                raise AssertionError(f"exit {completed.returncode}: {completed.stderr}")
            payload = parse_one_object(completed.stdout)
            if payload.get("classification") != classification:
                raise AssertionError(f"reported {payload.get('classification')!r}")
            return f"exit=0 path={payload.get('path')} diagnostics={len(payload.get('diagnostics', []))}"

    @limb("human mode keeps unreviewed=1 and ready=0")
    def _():
        if active is None or not active.is_file():
            raise NotExercised("the installed active-slot CLI was not found")
        blocked = invoke(active, documents["blocked"], json_mode=False)
        ready = invoke(active, documents["ready"], json_mode=False)
        if (blocked.returncode, ready.returncode) != (1, 0):
            raise AssertionError(
                f"expected (blocked, ready)=(1, 0), got "
                f"({blocked.returncode}, {ready.returncode})"
            )
        return "blocked exit=1; ready exit=0"

    for active_class, active_path in documents.items():
        for candidate_class, candidate_path in documents.items():
            @limb(f"guard {active_class}->{candidate_class}")
            def _(
                active_class=active_class, active_path=active_path,
                candidate_class=candidate_class, candidate_path=candidate_path,
            ):
                if active is None or not active.is_file():
                    raise NotExercised("the installed active-slot CLI was not found")
                if candidate is None or not candidate.is_file():
                    raise NotExercised("the candidate-slot CLI was not found")
                if active.resolve() == candidate.resolve():
                    raise NotExercised(
                        "active and candidate resolve to the same CLI; two slot validators were not exercised"
                    )
                left = invoke(active, active_path, json_mode=True)
                right = invoke(candidate, candidate_path, json_mode=True)
                if left.returncode or right.returncode:
                    raise AssertionError(
                        f"slot CLI exits were active={left.returncode}, candidate={right.returncode}"
                    )
                left_class = parse_one_object(left.stdout).get("classification")
                right_class = parse_one_object(right.stdout).get("classification")
                if (left_class, right_class) != (active_class, candidate_class):
                    raise AssertionError(
                        f"slot answers {(left_class, right_class)!r} did not match fixtures"
                    )
                comparison = compare_config_classifications(left_class, right_class)
                expected = active_class == candidate_class
                if comparison.proceed is not expected:
                    raise AssertionError(f"proceed={comparison.proceed}, expected={expected}")
                if right_class == "ready" and left_class != "ready":
                    if "Repair or re-review" not in (comparison.reason or ""):
                        raise AssertionError("promotion refusal omitted the human-review escape")
                return f"active CLI={active}; candidate CLI={candidate}; proceed={expected}"

    print("=" * 70)
    print("RESULTS")
    print("=" * 70)
    for name, status, detail in RESULTS:
        print(f"[{status:>13}] {name}")
        if detail:
            print(f"                {detail}")
    (out / "results.json").write_text(
        json.dumps(
            [{"limb": name, "status": status, "detail": detail}
             for name, status, detail in RESULTS],
            indent=2,
        ),
        encoding="utf-8",
    )
    failed = sum(status == "FAIL" for _, status, _ in RESULTS)
    skipped = sum(status == "NOT EXERCISED" for _, status, _ in RESULTS)
    passed = sum(status == "PASS" for _, status, _ in RESULTS)
    print(f"{passed} passed, {failed} failed, {skipped} not exercised, of {len(RESULTS)} limbs")
    if failed:
        print(f"BLOCK 58b DEMO GATE FAILED — {failed} limb(s) failed")
    elif skipped:
        print(f"BLOCK 58b DEMO GATE INCOMPLETE — {skipped} limb(s) tested nothing")
    else:
        print(f"BLOCK 58b DEMO GATE PASSED — all {len(RESULTS)} limbs")
    return 1 if failed or skipped else 0


if __name__ == "__main__":
    raise SystemExit(main())
