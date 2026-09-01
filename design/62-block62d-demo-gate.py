r"""design/62 block 62d — demo-machine gate for bounded file discovery.

Run this on the demo machine (Windows). It is a program, not a checklist:
every limb is a computation, so per CLAUDE.md it ships as one script that runs
them all, reports each **independently**, owns its own log, and exits nonzero
if any limb fails.

    py -3 design\62-block62d-demo-gate.py --downloads "C:\Users\<you>\Downloads"

Limbs, and why each one needs this machine. Two claims the implementer listed
were dropped deliberately: case-insensitive *matching* and case-insensitive
*ordering* are pure Python over `entry.name` and are asserted directly in
tests/test_inspect_artifacts_discovery.py, so a Windows run of them could not
fail. A limb that cannot fail is not a criterion.

  A  A real Windows folder resolves and traverses: drive-letter root, backslash
     separators, a real subtree. macOS cannot instantiate any of that.
  B  The returned candidate is directly usable: the exact string this tool
     returns is fed straight back in as `paths`, which is what the design
     promises a caller may do. POSIX paths cannot establish Windows path
     serialisation.
  C  A bound is not an absence, in its motivating setting. With max_files below
     the real folder's file count, `matches == []` must arrive with
     `truncated=True` and `examined_count == max_files`. This is the limb that
     stops a bound being reported as a fact about the folder.
  D  Junctions are not followed. design/62 says "do not follow directory
     symlinks/junctions during recursion (already true)" -- an *untested* claim
     about a Windows-only object, and design/58 was already bitten once by a
     junction in this repository. The guard is `not entry.is_symlink()`, whose
     behaviour for a junction only Windows can settle. Carries a control: a
     plain subdirectory's file must be found in the same run, so "marker absent"
     cannot pass because recursion was off.

Nothing here writes to the product's configuration, requires `workspace_dir`
(the product does not require it, so the gate must not), or leaves state behind.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from microclaw import tools  # noqa: E402

# One flag, consulted by every limb. The selftest overrides *this*, never
# os.name -- mutating os.name makes pathlib construct WindowsPath, and the gate
# then dies on its first resolve(). Found by writing the selftest, which is the
# point of writing the selftest.
IS_WINDOWS = os.name == "nt"

LOG: list[str] = []
RESULTS: list[tuple[str, str, str]] = []   # (limb, verdict, detail)


def say(line: str = "") -> None:
    print(line)
    LOG.append(line)


def record(limb: str, verdict: str, detail: str) -> None:
    """One limb, one verdict, independent of every other limb."""
    RESULTS.append((limb, verdict, detail))
    say(f"  [{verdict}] {limb}: {detail}")


def guard_for(*, workspace: str | None = None) -> MagicMock:
    """A guard that resolves paths and nothing else.

    Deliberately not a real SafetyGuard: this gate must not require a safety
    config, because the product does not require one for a read-only listing.
    """
    guard = MagicMock()
    guard.resolve_readable_path.side_effect = lambda raw: str(Path(raw).resolve())
    guard.resolve_in_workspace.side_effect = lambda raw: str(Path(raw))
    return guard


def limb_a(downloads: Path) -> dict | None:
    say("A. a real Windows folder resolves and traverses")
    if not IS_WINDOWS:
        record("A", "NOT EXERCISED", "not Windows; drive-letter roots do not exist here")
        return None
    if not downloads.is_dir():
        record("A", "FAIL", f"{downloads} is not a directory")
        return None
    started = time.monotonic()
    result = tools.inspect_artifacts(
        MagicMock(), guard_for(), [str(downloads)],
        name_glob="*", recursive=False, hash=False,
    )
    elapsed = time.monotonic() - started
    if "error" in result:
        record("A", "FAIL", f"returned an error: {result['error']}")
        return None
    detail = (f"{len(result['matches'])} top-level matches, "
              f"examined {result['examined_count']}, "
              f"truncated={result['truncated']}, {elapsed:.2f}s")
    drive_shaped = [m for m in result["matches"] if len(m) > 2 and m[1] == ":"]
    if result["matches"] and not drive_shaped:
        record("A", "FAIL", f"no returned path looks drive-rooted: {result['matches'][:2]}")
        return result
    if not result["matches"]:
        record("A", "NOT EXERCISED",
               f"{downloads} has no top-level files, so path shape proved nothing")
        return result
    if "\\" not in drive_shaped[0]:
        record("A", "FAIL", f"returned path has no backslash separator: {drive_shaped[0]}")
        return result
    record("A", "PASS", f"{detail}; first={drive_shaped[0]}")
    return result


def limb_b(previous: dict | None) -> None:
    say("B. the returned candidate is directly usable as an input path")
    if not IS_WINDOWS:
        record("B", "NOT EXERCISED", "not Windows; nothing to serialise")
        return
    if not previous or not previous.get("matches"):
        record("B", "NOT EXERCISED", "limb A produced no candidate to feed back")
        return
    candidate = previous["matches"][0]
    result = tools.inspect_artifacts(
        MagicMock(), guard_for(), [candidate], hash=True,
    )
    if "error" in result:
        record("B", "FAIL", f"the tool's own returned string was rejected: {result['error']}")
        return
    if result["artifact_count"] != 1:
        record("B", "FAIL", f"expected 1 artifact from a single file, got {result['artifact_count']}")
        return
    digest = result["artifacts"][0].get("sha256")
    if not digest:
        record("B", "FAIL", "hash=true produced no sha256")
        return
    if result["scope"]["recursive"] is not True:
        record("B", "FAIL", f"scope did not record the request: {result['scope']}")
        return
    record("B", "PASS", f"round-tripped {candidate} -> sha256 {digest[:12]}…, "
                        f"scope recorded {result['scope']}")


def limb_c(downloads: Path) -> None:
    say("C. a bound is not an absence")
    if not IS_WINDOWS:
        record("C", "NOT EXERCISED", "needs the real folder this decision was written for")
        return
    if not downloads.is_dir():
        record("C", "FAIL", f"{downloads} is not a directory")
        return
    census = tools.inspect_artifacts(
        MagicMock(), guard_for(), [str(downloads)],
        recursive=True, hash=False, max_files=100000,
    )
    if "error" in census:
        record("C", "FAIL", f"census failed: {census['error']}")
        return
    total = census["examined_count"]
    if total < 3:
        record("C", "NOT EXERCISED",
               f"{downloads} holds only {total} files; cannot place a match beyond a bound")
        return
    bound = max(1, total - 1)
    # Ask for something that certainly does not exist under a bound that
    # certainly trips, then for something under a bound that does not.
    tripped = tools.inspect_artifacts(
        MagicMock(), guard_for(), [str(downloads)],
        name_glob="*.__microclaw_no_such_extension__", recursive=True,
        hash=False, max_files=bound,
    )
    if "error" in tripped:
        record("C", "FAIL", f"discovery refused instead of truncating: {tripped['error']}")
        return
    complete = tools.inspect_artifacts(
        MagicMock(), guard_for(), [str(downloads)],
        name_glob="*.__microclaw_no_such_extension__", recursive=True,
        hash=False, max_files=100000,
    )
    problems = []
    if tripped["matches"] != []:
        problems.append(f"bounded matches not empty: {tripped['matches'][:2]}")
    if tripped["truncated"] is not True:
        problems.append("bounded call did not report truncated=True")
    if tripped["examined_count"] != bound:
        problems.append(f"examined_count {tripped['examined_count']} != max_files {bound}")
    # The control: the same absent pattern, unbounded, must be a REAL negative.
    if complete.get("truncated") is not False:
        problems.append("the unbounded control was itself truncated, so the "
                        "two cases are indistinguishable")
    if problems:
        record("C", "FAIL", "; ".join(problems))
        return
    record("C", "PASS",
           f"{total} files examined unbounded (truncated=False, a real negative); "
           f"at max_files={bound} the same absent pattern gives matches=[] with "
           f"truncated=True and examined_count={bound} — distinguishable")


def limb_d() -> None:
    say("D. junctions are not followed during recursion")
    if not IS_WINDOWS:
        record("D", "NOT EXERCISED", "junctions are a Windows object; nothing to create here")
        return
    root = Path(tempfile.mkdtemp(prefix="microclaw62d_"))
    try:
        inside = root / "tree"
        plain = inside / "plain_subdir"
        plain.mkdir(parents=True)
        (plain / "control_reached.ilp").write_bytes(b"control")
        outside = root / "elsewhere"
        outside.mkdir()
        (outside / "must_not_be_reached.ilp").write_bytes(b"beyond the junction")
        link = inside / "junction_to_elsewhere"
        made = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
            capture_output=True, text=True, stdin=subprocess.DEVNULL,
        )
        if made.returncode != 0 or not link.exists():
            record("D", "NOT EXERCISED",
                   f"mklink /J failed, so the guard was never reached: "
                   f"{(made.stderr or made.stdout).strip()[:160]}")
            return
        say(f"     junction created: {link} -> {outside}")
        say(f"     Path.is_symlink() on the junction: {link.is_symlink()}")
        result = tools.inspect_artifacts(
            MagicMock(), guard_for(), [str(inside)],
            name_glob="*.ilp", recursive=True, hash=False,
        )
        if "error" in result:
            record("D", "FAIL", f"traversal errored: {result['error']}")
            return
        names = sorted(Path(m).name for m in result["matches"])
        followed = "must_not_be_reached.ilp" in names
        control = "control_reached.ilp" in names
        if not control:
            # Without this the limb could "pass" simply by not recursing at all.
            record("D", "FAIL",
                   f"the control file in a plain subdirectory was not found either, "
                   f"so recursion itself is broken; matches={names}")
            return
        if followed:
            record("D", "FAIL",
                   f"recursion followed the junction: {names}. design/62's "
                   f"'already true' is false on this machine — is_symlink() "
                   f"returned {link.is_symlink()} for a junction")
            return
        record("D", "PASS",
               f"control found and junction not followed; matches={names}; "
               f"is_symlink()={link.is_symlink()}")
    finally:
        # Break the junction before deleting, so rmtree cannot reach outside.
        try:
            subprocess.run(["cmd", "/c", "rmdir", str(root / "tree" / "junction_to_elsewhere")],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL)
        except OSError:
            pass
        shutil.rmtree(root, ignore_errors=True)
        say(f"     fixture removed: {root} exists={root.exists()}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--downloads", required=True,
                        help=r'the real folder to search, e.g. "C:\Users\you\Downloads"')
    parser.add_argument("--log", default="block62d-demo-gate.log")
    args = parser.parse_args()

    downloads = Path(args.downloads)
    say("=== design/62 block 62d demo gate ===")
    say(f"platform={sys.platform} os.name={os.name} python={sys.version.split()[0]}")
    say(f"microclaw={tools.__file__}")
    say(f"downloads={downloads}")
    say("")

    previous = limb_a(downloads)
    say("")
    limb_b(previous)
    say("")
    limb_c(downloads)
    say("")
    limb_d()
    say("")

    failed = [r for r in RESULTS if r[1] == "FAIL"]
    unexercised = [r for r in RESULTS if r[1] == "NOT EXERCISED"]
    passed = [r for r in RESULTS if r[1] == "PASS"]
    say("=== summary ===")
    for limb, verdict, detail in RESULTS:
        say(f"{limb}: {verdict}")
    say(f"{len(passed)} passed, {len(failed)} failed, {len(unexercised)} not exercised")
    say("NOT EXERCISED is never a pass. A limb that could not run its mechanism")
    say("produced no evidence, and the row it belongs to stays open.")

    Path(args.log).write_text("\n".join(LOG) + "\n", encoding="utf-8")
    print(f"\nlog written to {Path(args.log).resolve()}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
