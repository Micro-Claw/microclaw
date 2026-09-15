r"""Probe the public-head update path end to end, touching no managed install.

Written for the day the repository went public (2026-09-15), because every test
of `discover_public` / `materialize_public` in the suite drives a fake opener
and could not run live while the repository was private -- CLAUDE.md
Sec."a fake that encodes your assumption is not a test of it".

It builds a throwaway root under the temp directory and stages into that, so
%LOCALAPPDATA%\microclaw is never read or written.  Each limb reports
independently and a limb that could not run its mechanism reports NOT
EXERCISED, which is never a pass.

    <slot python> design\58-public-path-probe.py

Exits nonzero if any limb failed.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from microclaw import updates

results: list[tuple[str, str, str]] = []


def record(limb: str, status: str, detail: str = "") -> None:
    results.append((limb, status, detail))
    print(f"  {status:<13} {limb}" + (f" -- {detail}" if detail else ""), flush=True)


def find_uv() -> str | None:
    found = shutil.which("uv")
    if found:
        return found
    fallback = Path.home() / ".local" / "bin" / ("uv.exe" if sys.platform == "win32" else "uv")
    return str(fallback) if fallback.is_file() else None


def main() -> int:
    print(f"microclaw public-path probe -- {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  interpreter: {sys.executable}")
    print(f"  package    : {Path(updates.__file__).resolve()}")
    print(f"  compiled-in: repo={updates.REPO} id={updates.REPO_ID} branch={updates.BRANCH}\n")

    root = Path(tempfile.mkdtemp(prefix="microclaw-public-probe-"))
    print(f"  sandbox    : {root}\n")
    candidate = None
    source = None

    # 1. Discovery against the live API.
    try:
        state = updates.public_provenance()
        started = time.time()
        candidate = updates.discover_public(state)
        if candidate is None:
            record("discover_public", "FAIL", "returned None for installed_commit 'unknown'")
        else:
            record("discover_public", "PASS",
                   f"{candidate.sha[:12]} in {time.time() - started:.2f}s -- {candidate.subject[:60]}")
    except Exception as exc:
        record("discover_public", "FAIL", f"{type(exc).__name__}: {exc}")

    # 2. Download and extract the archive for that exact SHA.
    if candidate is None:
        record("materialize_public", "NOT EXERCISED", "no candidate to materialize")
    else:
        try:
            started = time.time()
            source = updates.materialize_public(state, candidate, root / "source")
            files = [p for p in source.rglob("*") if p.is_file()]
            total = sum(p.stat().st_size for p in files)
            record("materialize_public", "PASS",
                   f"{len(files)} files, {total / 1e6:.1f} MB in {time.time() - started:.2f}s")
        except Exception as exc:
            record("materialize_public", "FAIL", f"{type(exc).__name__}: {exc}")

    # 3. The launcher files install.bat copies must be in the archive, or a ZIP
    #    install cannot bootstrap at all.  Checked separately from extraction:
    #    an archive that extracts is not an archive that carries what we need.
    if source is None:
        record("archive carries launcher files", "NOT EXERCISED", "nothing extracted")
    else:
        required = ["pyproject.toml", "install.bat", "scripts/Microclaw.cmd",
                    "scripts/updater-launcher.ps1", "scripts/launcher-protocol.txt"]
        missing = [name for name in required if not (source / name).is_file()]
        if missing:
            record("archive carries launcher files", "FAIL", f"missing {missing}")
        else:
            crlf = (source / "install.bat").read_bytes()
            lone = crlf.count(b"\n") - crlf.count(b"\r\n")
            if lone:
                record("archive carries launcher files", "FAIL",
                       f"install.bat has {lone} LF-only lines; cmd.exe may mis-parse it")
            else:
                record("archive carries launcher files", "PASS", "install.bat CRLF intact")

    # 4. Build a slot from that source: uv venv, [serve] install, smoke check.
    uv = find_uv()
    if sys.platform != "win32":
        record("stage_inactive_slot", "NOT EXERCISED",
               f"staging resolves Scripts\\python.exe; this is {sys.platform}")
    elif source is None:
        record("stage_inactive_slot", "NOT EXERCISED", "nothing extracted")
    elif uv is None:
        record("stage_inactive_slot", "NOT EXERCISED", "uv not found on PATH or in ~/.local/bin")
    else:
        try:
            updates.write_state(updates.public_provenance(), root / updates.STATE_NAME)
            (root / updates.ACTIVE_SLOT_NAME).write_text("a\n", encoding="ascii")
            shutil.copyfile(source / "scripts" / "launcher-protocol.txt",
                            root / "launcher-protocol.txt")
            started = time.time()
            staged = updates.stage_inactive_slot(
                root, source, candidate, uv_executable=uv,
            )
            marker = json.loads((staged / updates.SLOT_NAME).read_text(encoding="utf-8"))
            if marker.get("commit") != candidate.sha:
                record("stage_inactive_slot", "FAIL",
                       f"slot marker records {marker.get('commit')}, expected {candidate.sha}")
            else:
                record("stage_inactive_slot", "PASS",
                       f"built {staged.name} at {marker['commit'][:12]} in {time.time() - started:.0f}s")
        except Exception as exc:
            detail = ""
            try:
                built = updates.load_state(root / updates.STATE_NAME) or {}
                detail = str(built.get("build_error_detail") or "")[-400:]
            except Exception:
                pass
            record("stage_inactive_slot", "FAIL", f"{type(exc).__name__}: {exc} {detail}")

    # 5. The staged slot must actually run `serve`'s imports.  stage_inactive_slot
    #    smoke-checks this, but prove it from outside: an updater that bricks the
    #    thing it updates is the worst failure this design can have (58e, round 5).
    staged_python = root / "env-b" / "Scripts" / "python.exe"
    if not staged_python.is_file():
        record("staged slot starts", "NOT EXERCISED", "no staged interpreter to run")
    else:
        completed = subprocess.run(
            [str(staged_python), "-I", "-c",
             "import microclaw, microclaw.webserve, uvicorn; print(microclaw.__file__)"],
            capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL,
        )
        if completed.returncode:
            record("staged slot starts", "FAIL",
                   (completed.stderr or completed.stdout).strip()[-400:])
        else:
            record("staged slot starts", "PASS", completed.stdout.strip())

    print(f"\n  sandbox left at {root}")
    print("  delete it with:  Remove-Item -Recurse -Force '%s'" % root)

    failed = [limb for limb, status, _ in results if status == "FAIL"]
    skipped = [limb for limb, status, _ in results if status == "NOT EXERCISED"]
    print(f"\n  {len(results) - len(failed) - len(skipped)} passed, "
          f"{len(failed)} failed, {len(skipped)} not exercised")
    if failed:
        print("  FAILED: " + ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
