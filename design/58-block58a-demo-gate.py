"""Block 58a demo gate — runs every limb, reports each, writes its own evidence.

Run through the PowerShell wrapper, not directly:

    powershell -NoProfile -ExecutionPolicy Bypass -File design\58-block58a-demo-gate.ps1

Every limb runs even when an earlier one fails, so one failure never hides the
rest — the old copy-paste runbook aborted mid-sequence and still printed PASSED.
The exit code is 0 only when every limb passed.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

from microclaw import updates

RESULTS: list[tuple[str, bool, str]] = []


def limb(name):
    """Run one limb, record PASS/FAIL, never let it abort the others."""
    def wrap(fn):
        try:
            detail = fn()
            RESULTS.append((name, True, detail or ""))
        except Exception as exc:
            RESULTS.append((name, False, f"{type(exc).__name__}: {exc}"))
            traceback.print_exc()
        return fn
    return wrap


def git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {(r.stderr or r.stdout).strip()}")
    return r.stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    repo, out = Path(args.repo), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    # --- Environment. This block is evidence in its own right: the round-1 gate
    # failed because of the remote URL, and nothing printed it.
    print("=" * 70)
    print("ENVIRONMENT")
    print("=" * 70)
    print(f"microclaw:   {updates.__file__}")
    print(f"python:      {sys.version.split()[0]}  ({sys.executable})")
    print(f"platform:    {sys.platform}")
    print(f"repo:        {repo}")
    print(f"branch:      {git(repo, 'branch', '--show-current')}")
    print(f"HEAD:        {git(repo, 'rev-parse', 'HEAD')}")
    print(f"origin/main: {git(repo, 'rev-parse', 'origin/main')}")
    print("remotes:")
    for line in git(repo, "remote", "-v").splitlines():
        print(f"  {line}")
    print(f"compiled-in: {updates.REPO}  id={updates.REPO_ID}  branch={updates.BRANCH}")
    print()

    expected = git(repo, "rev-parse", "origin/main")
    ancestor = git(repo, "rev-parse", "origin/main~1")
    head = git(repo, "rev-parse", "HEAD")
    clone_state = out / "clone-state.json"
    public_state = out / "public-state.json"
    stage = out / "stage"
    status_before = git(repo, "status", "--porcelain=v1")

    @limb("1. provenance records the clone without refusing a redirected name")
    def _():
        state = updates.clone_provenance(repo, ancestor, git_executable=shutil.which("git"))
        updates.write_state(state, clone_state)
        note = state.get("rename_note") or state.get("note") or "(none)"
        return (f"remote_identity={state.get('remote_identity')} "
                f"remote={state.get('remote')} tracked={state.get('tracked_branch')} note={note}")

    @limb("1b. requirement 3 — a pre-transfer remote name is accepted, not refused")
    def _():
        state = updates.load_state(clone_state)
        identity = str(state.get("remote_identity") or "")
        compiled = f"github:{updates.REPO.casefold()}"
        if not identity.startswith("github:"):
            return (f"SKIPPED — this clone's remote is {identity!r}, not a github.com URL, "
                    "so it cannot exercise the redirect case")
        if identity == compiled:
            return (f"NOT EXERCISED — this clone already points at {updates.REPO}. "
                    "A clone still on the pre-transfer name is what tests requirement 3; "
                    "do not repoint the demo machine's remote to create one.")
        # The interesting case: the clone names a repository GitHub redirects.
        # Provenance must have succeeded (we got here) and discovery must work.
        c = updates.check_for_update(state_file=clone_state, now=101, jitter=lambda a, b: 0)
        if c is None:
            raise AssertionError(
                f"clone on redirected name {identity!r} produced no candidate; "
                "requirement 3 says a transfer must not cut an install off")
        return (f"clone tracks {identity} and GitHub redirects it to {compiled}; "
                f"discovery still reached {c.sha}")

    @limb("2. check_for_update discovers origin/main from an ancestor install")
    def _():
        c = updates.check_for_update(state_file=clone_state, now=100, jitter=lambda a, b: 0)
        if c is None:
            raise AssertionError("no candidate; state="
                                 + json.dumps(updates.load_state(clone_state), sort_keys=True))
        if c.sha != expected:
            raise AssertionError(f"candidate {c.sha} != origin/main {expected}")
        cached = updates.load_state(clone_state)
        if cached["last_attempt"] != 100:
            raise AssertionError(f"last_attempt {cached['last_attempt']!r} != 100")
        if cached["last_success"]["candidate"]["sha"] != expected:
            raise AssertionError("cached candidate does not match")
        return f"candidate={c.sha} subject={c.subject!r} cached atomically"

    @limb("3. the block branch's own HEAD is refused specifically as diverged")
    def _():
        state = updates.load_state(clone_state)
        state["installed_commit"] = head
        c = updates.discover_clone(state)
        status = state.get("discovery", {}).get("status")
        if c is not None:
            raise AssertionError(f"block HEAD wrongly offered {c.sha}")
        if status != "diverged":
            raise AssertionError(f"status {status!r} != 'diverged'")
        return f"status={status} message={state['discovery']['message']!r}"

    @limb("4. discovery leaves a dirty checkout byte-identical")
    def _():
        proof = repo / ".58a-dirty-proof"
        proof.write_text("must survive byte-for-byte", encoding="utf-8")
        try:
            before = (git(repo, "status", "--porcelain=v1"), git(repo, "rev-parse", "HEAD"),
                      git(repo, "branch", "--show-current"), proof.read_bytes())
            state = updates.load_state(clone_state)
            state["installed_commit"] = ancestor
            updates.discover_clone(state)
            after = (git(repo, "status", "--porcelain=v1"), git(repo, "rev-parse", "HEAD"),
                     git(repo, "branch", "--show-current"), proof.read_bytes())
        finally:
            proof.unlink(missing_ok=True)
        if before != after:
            raise AssertionError(f"checkout changed:\n before={before}\n after ={after}")
        return f"branch/HEAD/status/bytes identical (HEAD={before[1]})"

    @limb("5. materialization stages exactly the discovered candidate")
    def _():
        state = updates.load_state(clone_state)
        raw = (state.get("last_success") or {}).get("candidate")
        if not raw:
            raise AssertionError("no cached candidate to materialize")
        c = updates.Candidate(**raw)
        updates.materialize_clone(state, c, stage)
        updates.verify_staged_source(stage, c.sha)
        marker = json.loads((stage / updates.STAGED_SOURCE_NAME).read_text(encoding="utf-8"))
        if marker["commit"] != expected:
            raise AssertionError(f"marker {marker['commit']} != origin/main {expected}")
        n = sum(1 for _ in stage.rglob("*"))
        return f"staged {n} entries; marker={marker['commit']} == origin/main"

    @limb("6. the private repo's public 404 is cached without clobbering success")
    def _():
        state = updates.public_provenance()
        state["last_success"] = {"checked_at": 1, "candidate": None}
        updates.write_state(state, public_state)
        updates.check_for_update(state_file=public_state, now=100)
        after = updates.load_state(public_state)
        if after["last_attempt"] != 100:
            raise AssertionError(f"last_attempt {after['last_attempt']!r} != 100")
        if "404" not in str(after.get("last_error")):
            raise AssertionError(f"last_error {after.get('last_error')!r} carries no 404")
        if after["last_success"]["checked_at"] != 1:
            raise AssertionError("prior success was overwritten")
        return f"last_error={after['last_error']!r}; prior success preserved"

    @limb("7. both opt-outs perform no check at all")
    def _():
        before = updates.load_state(public_state)["last_attempt"]
        updates.check_for_update(state_file=public_state, no_update_check=True, now=200)
        after_flag = updates.load_state(public_state)["last_attempt"]
        os.environ["MICROCLAW_UPDATE_CHECK"] = "0"
        try:
            updates.check_for_update(state_file=public_state, now=300)
        finally:
            os.environ.pop("MICROCLAW_UPDATE_CHECK", None)
        after_env = updates.load_state(public_state)["last_attempt"]
        if not (before == after_flag == after_env):
            raise AssertionError(f"attempt moved: {before} -> {after_flag} -> {after_env}")
        return f"last_attempt stayed {before} across --no-update-check and MICROCLAW_UPDATE_CHECK=0"

    @limb("8. the gate left the checkout exactly as it found it")
    def _():
        status_after = git(repo, "status", "--porcelain=v1")
        if status_before != status_after:
            raise AssertionError(f"status changed:\n before={status_before!r}\n after ={status_after!r}")
        return "git status --porcelain=v1 unchanged"

    print("=" * 70)
    print("RESULTS")
    print("=" * 70)
    failed = 0
    for name, ok, detail in RESULTS:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
        if detail:
            print(f"        {detail}")
        failed += 0 if ok else 1
    print()
    (out / "results.json").write_text(
        json.dumps([{"limb": n, "passed": o, "detail": d} for n, o, d in RESULTS], indent=2),
        encoding="utf-8",
    )
    if failed:
        print(f"BLOCK 58a DEMO GATE FAILED — {failed} of {len(RESULTS)} limbs failed")
    else:
        print(f"BLOCK 58a DEMO GATE PASSED — all {len(RESULTS)} limbs")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
