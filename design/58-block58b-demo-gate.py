"""Block 58b demo gate: checkout CLI classification and honest slot evidence."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import microclaw
from microclaw import config

RESULTS: list[tuple[str, str, str]] = []


class NotExercised(Exception):
    """The named mechanism could not run. This is never a pass."""


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


def limb(name: str, fails_if: str):
    """Run every limb independently and record its explicit failure control."""
    def decorate(fn):
        try:
            detail = fn() or ""
            RESULTS.append((name, "PASS", f"{detail}; FAILS IF: {fails_if}"))
        except NotExercised as exc:
            RESULTS.append((
                name, "NOT EXERCISED", f"{exc}; WOULD FAIL IF RUN: {fails_if}",
            ))
        except Exception as exc:
            RESULTS.append((
                name, "FAIL", f"{type(exc).__name__}: {exc}; FAILS IF: {fails_if}",
            ))
            traceback.print_exc()
        return fn
    return decorate


def run_cli(executable: Path, config_path: Path, *, json_mode: bool):
    command = [str(executable), "--safety-config", str(config_path), "check-config"]
    if json_mode:
        command.append("--json")
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NotExercised(f"could not invoke checkout CLI {executable}: {exc}") from exc


def parse_one_object(stdout: str) -> dict:
    decoder = json.JSONDecoder()
    value, end = decoder.raw_decode(stdout)
    if stdout[end:].strip() or not isinstance(value, dict):
        raise AssertionError(f"stdout was not exactly one JSON object: {stdout!r}")
    return value


def write_stub(
    path: Path, *, classification: str = "blocked", exit_code: int = 0,
    raw_stdout: str | None = None,
) -> None:
    """Write the gate-owned candidate executable, never a pretend second slot."""
    stdout = raw_stdout or json.dumps({
        "classification": classification,
        "path": "gate-stub-does-not-validate",
        "diagnostics": [],
    })
    if os.name == "nt":
        path.write_text(
            f"@echo off\r\necho {stdout.replace('%', '%%')}\r\n"
            f"exit /b {exit_code}\r\n",
            encoding="utf-8",
        )
    else:
        path.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' {json.dumps(stdout)}\n"
            f"exit {exit_code}\n",
            encoding="utf-8",
        )
        path.chmod(0o755)


def write_checkout_cli(path: Path, python: Path) -> None:
    """Turn uv's resolved Python into this checkout's real ``-m microclaw`` CLI."""
    if os.name == "nt":
        path.write_text(
            f'@echo off\r\n"{python}" -m microclaw %*\r\nexit /b %ERRORLEVEL%\r\n',
            encoding="utf-8",
        )
    else:
        path.write_text(
            f'#!/bin/sh\nexec "{python}" -m microclaw "$@"\n',
            encoding="utf-8",
        )
        path.chmod(0o755)


def module_path(python: Path) -> str:
    result = subprocess.run(
        [str(python), "-c", "import microclaw; print(microclaw.__file__)"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        return f"IDENTITY FAILED: {result.stderr.strip()}"
    return result.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    repo, out = Path(args.repo).resolve(), Path(args.out).resolve()
    os.chdir(repo)
    out.mkdir(parents=True, exist_ok=True)
    sys.stdout = sys.stderr = Tee(sys.__stdout__, out / "gate.txt")

    checkout_python = Path(sys.executable).resolve()
    checkout_cli = out / (
        "checkout-microclaw.cmd" if os.name == "nt" else "checkout-microclaw"
    )
    stub = out / (
        "candidate-gate-stub.cmd" if os.name == "nt" else "candidate-gate-stub"
    )
    write_checkout_cli(checkout_cli, checkout_python)

    print("=" * 70)
    print("ENVIRONMENT")
    print("=" * 70)
    print(f"repo:                      {repo}")
    print(f"checkout Python:           {checkout_python}")
    print(f"checkout CLI launcher:     {checkout_cli}")
    print("checkout CLI mechanism:    uv-resolved Python -m microclaw")
    print(f"CLI environment module:    {module_path(checkout_python)}")
    print(f"gate-imported microclaw:    {Path(microclaw.__file__).resolve()}")
    print(f"gate-imported comparison:   {Path(config.__file__).resolve()}")
    print(f"candidate gate stub:        {stub}")

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
        @limb(
            f"checkout CLI JSON classification: {classification}",
            "the checkout CLI is missing/wrong, exits nonzero, emits extra or invalid "
            "stdout, or reports the wrong classification",
        )
        def _(classification=classification, path=path):
            completed = run_cli(checkout_cli, path, json_mode=True)
            if completed.returncode != 0:
                raise AssertionError(f"exit {completed.returncode}: {completed.stderr}")
            payload = parse_one_object(completed.stdout)
            if payload.get("classification") != classification:
                raise AssertionError(f"reported {payload.get('classification')!r}")
            return f"checkout CLI exit=0; exactly one object; path={payload.get('path')}"

    @limb(
        "checkout CLI human mode keeps unreviewed=1 and ready=0",
        "either exit code differs from install.bat's existing (1, 0) contract",
    )
    def _():
        blocked = run_cli(checkout_cli, documents["blocked"], json_mode=False)
        ready = run_cli(checkout_cli, documents["ready"], json_mode=False)
        if (blocked.returncode, ready.returncode) != (1, 0):
            raise AssertionError(
                f"expected (blocked, ready)=(1, 0), got "
                f"({blocked.returncode}, {ready.returncode})"
            )
        return "checkout CLI: blocked exit=1; ready exit=0"

    for active_class, path in documents.items():
        for candidate_class in documents:
            name = (
                f"guard {active_class}->{candidate_class} "
                "(candidate = gate stub, not a second slot)"
            )

            @limb(
                name,
                "the real checkout/stub subprocess calls, argparse order, JSON pipe, "
                "matrix result, or promotion escape differs from the contract",
            )
            def _(active_class=active_class, path=path, candidate_class=candidate_class):
                write_stub(stub, classification=candidate_class)
                comparison = config.compare_slot_configurations(checkout_cli, stub, path)
                expected = active_class == candidate_class
                if comparison.proceed is not expected:
                    raise AssertionError(f"proceed={comparison.proceed}, expected={expected}")
                if candidate_class == "ready" and active_class != "ready":
                    if "Repair or re-review" not in (comparison.reason or ""):
                        raise AssertionError("promotion refusal omitted the review escape")
                return (
                    f"active=checkout CLI ({active_class}); candidate={stub} gate "
                    f"stub ({candidate_class}), not a second slot; proceed={expected}"
                )

    failure_cases = [
        ("candidate gate stub exits nonzero", {"exit_code": 7}, "could not classify"),
        ("candidate gate stub emits non-JSON", {"raw_stdout": "not-json"},
         "invalid config-classification JSON"),
        ("candidate gate stub emits unknown classification",
         {"classification": "surprise"}, "invalid config classification"),
    ]
    for name, stub_args, expected_message in failure_cases:
        @limb(
            name,
            "classify_config_with_slot accepts the real stub process's invalid result "
            "or returns a different refusal",
        )
        def _(name=name, stub_args=stub_args, expected_message=expected_message):
            write_stub(stub, **stub_args)
            try:
                config.classify_config_with_slot(stub, documents["ready"])
            except RuntimeError as exc:
                if expected_message not in str(exc):
                    raise AssertionError(f"wrong refusal: {exc}") from exc
            else:
                raise AssertionError(f"{name} was accepted")
            return f"real gate-stub subprocess refused with {expected_message!r}"

    @limb(
        "two real slot validators classify one shared file (requires Block 58c)",
        "once 58c supplies two slots, either real validator/process/JSON result or "
        "their comparison violates the contract",
    )
    def _():
        raise NotExercised(
            "Block 58c has not created two real slot environments; this gate did not "
            "fabricate that evidence from an unrelated installed version"
        )

    print("=" * 70)
    print("RESULTS")
    print("=" * 70)
    for name, status, detail in RESULTS:
        print(f"[{status:>13}] {name}")
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
        print(f"BLOCK 58b DEMO GATE INCOMPLETE — {skipped} limb(s) not exercised")
    else:
        print(f"BLOCK 58b DEMO GATE PASSED — all {len(RESULTS)} limbs")
    return 1 if failed or skipped else 0


if __name__ == "__main__":
    raise SystemExit(main())
