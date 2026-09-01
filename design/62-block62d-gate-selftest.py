r"""Self-test for design/62 block 62d's demo gate. Runs on macOS/Linux.

The gate's own limbs all short-circuit on `os.name != "nt"`, so running it here
proves only that it imports -- and a gate whose bodies nobody executed is the
defect this workflow keeps paying for (design/59 block 59a: every orientation
limb reached the demo machine and died on a `list()` over a Core collection).

So force the gate's own `IS_WINDOWS` flag and drive every limb body
against a real POSIX fixture, with `mklink /J` stubbed to a symlink -- the
nearest object POSIX has to a junction, and the same `is_symlink()` guard.

What this can prove: every limb executes, parses its own result, reaches its
verdict, and cleans up. What it cannot prove, and does not claim to: Windows
path serialisation, and whether `Path.is_symlink()` is true for a real
junction. Those are why limbs A, B and D need the machine at all.

    python design/62-block62d-gate-selftest.py
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO))

spec = importlib.util.spec_from_file_location(
    "block62d_gate", HERE / "62-block62d-demo-gate.py"
)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if condition else 'FAIL'} {label}{(' — ' + detail) if detail else ''}")
    if not condition:
        FAILURES.append(label)


def verdicts() -> dict[str, tuple[str, str]]:
    return {limb: (verdict, detail) for limb, verdict, detail in gate.RESULTS}


def fake_windows_subprocess(argv, **kwargs):
    """Stand in for `cmd /c mklink /J` and `cmd /c rmdir` on POSIX."""
    class Done:
        returncode = 0
        stdout = ""
        stderr = ""
    if "mklink" in argv:
        link, target = Path(argv[-2]), Path(argv[-1])
        os.symlink(target, link)
    elif "rmdir" in argv:
        target = Path(argv[-1])
        if target.is_symlink():
            target.unlink()
    return Done()


def main() -> int:
    fixture = Path(tempfile.mkdtemp(prefix="selftest62d_"))
    for name in ("01.tif", "02.tif", "03.txt", "04.txt", "05.ilp"):
        (fixture / name).write_bytes(b"x")
    nested = fixture / "sub"
    nested.mkdir()
    (nested / "06.tif").write_bytes(b"xx")

    print("=== forcing gate.IS_WINDOWS=True and driving every limb ===")
    gate.IS_WINDOWS = True
    gate.subprocess.run = fake_windows_subprocess
    gate.RESULTS.clear()
    gate.LOG.clear()

    print("\nlimb A (POSIX paths are not drive-rooted, so FAIL is the honest verdict)")
    result_a = gate.limb_a(fixture)
    got = verdicts()
    check("A executed its body rather than short-circuiting",
          got["A"][0] != "NOT EXERCISED", f"verdict={got['A'][0]}")
    check("A reached the drive-shape assertion",
          "drive-rooted" in got["A"][1], got["A"][1][:90])
    check("A parsed the tool result without raising", result_a is not None)

    print("\nlimb B (fed limb A's own returned candidate)")
    gate.limb_b(result_a)
    got = verdicts()
    check("B executed its body", got["B"][0] != "NOT EXERCISED", f"verdict={got['B'][0]}")
    check("B round-tripped the candidate and hashed it",
          got["B"][0] == "PASS", got["B"][1][:110])

    print("\nlimb C (fully platform-independent — must PASS here)")
    gate.limb_c(fixture)
    got = verdicts()
    check("C executed its body", got["C"][0] != "NOT EXERCISED", f"verdict={got['C'][0]}")
    check("C passed: a bound is distinguishable from an absence",
          got["C"][0] == "PASS", got["C"][1][:150])

    print("\nlimb D (mklink stubbed to a symlink — same is_symlink() guard)")
    gate.limb_d()
    got = verdicts()
    check("D executed its body", got["D"][0] != "NOT EXERCISED", f"verdict={got['D'][0]}")
    check("D passed: control found, link not followed",
          got["D"][0] == "PASS", got["D"][1][:150])
    check("D's control is load-bearing (its detail names the control file)",
          "control_reached.ilp" in got["D"][1] or "control" in got["D"][1].lower())

    print("\n=== the control that must fire: break the guard and D must FAIL ===")
    original = gate.tools.inspect_artifacts

    def follows_links(ctrl, guard, paths, **kwargs):
        """A traversal that really follows links -- the defect limb D exists to
        catch. `rglob` was the first attempt and does NOT cross a symlinked
        directory, so the control silently did not fire: a fake that encodes
        your assumption is not a test of it, and that applies to a control too.
        `os.walk(followlinks=True)` actually crosses it."""
        import fnmatch as _fn
        pattern = kwargs.get("name_glob", "*")
        matches = []
        for base, _dirs, names in os.walk(Path(paths[0]), followlinks=True):
            for name in names:
                if _fn.fnmatchcase(name.lower(), pattern.lower()):
                    matches.append(str(Path(base) / name))
        matches.sort()
        return {"matches": matches, "examined_count": len(matches),
                "truncated": False, "scope_complete": True,
                "scope": {"name_glob": kwargs.get("name_glob", "*"), "recursive": True,
                          "max_files": 1000, "max_total_bytes": 1, "max_depth": 16},
                "artifact_count": len(matches), "total_bytes": 0,
                "hashes_computed": False, "artifacts": [], "survey": []}

    gate.tools.inspect_artifacts = follows_links
    gate.RESULTS.clear()
    gate.limb_d()
    gate.tools.inspect_artifacts = original
    got = verdicts()
    check("D FAILS against a link-following traversal",
          got["D"][0] == "FAIL", got["D"][1][:130])
    check("and says the design's claim is false on this machine",
          "already true" in got["D"][1] or "followed the junction" in got["D"][1])

    print("\n=== main() end to end: build_fixture, the try/finally, and limb E ===")
    real = fixture / "real_extra"
    real.mkdir()
    for n in ("r1.txt", "r2.txt", "r3.txt", "r4.txt"):
        (real / n).write_bytes(b"z")
    log = fixture / "gate.log"
    argv = sys.argv[:]
    sys.argv = ["gate", "--real-folder", str(real), "--log", str(log)]
    gate.RESULTS.clear()
    gate.LOG.clear()
    try:
        rc = gate.main()
    finally:
        sys.argv = argv
    got = verdicts()
    check("main() ran every limb plus E", set(got) == {"A", "B", "C", "D", "E"},
          f"limbs reported: {sorted(got)}")
    # A can only FAIL here, and for one specific reason: POSIX paths are not
    # drive-rooted. What matters is that it got far enough to *say* that -- i.e.
    # it traversed the gate's own fixture and needed no operator folder.
    check("A reached its drive-shape check against the gate's own fixture",
          got["A"][0] == "FAIL" and "drive-rooted" in got["A"][1], got["A"][1][:110])
    check("C passed against the gate's own fixture, with no operator folder",
          got["C"][0] == "PASS", got["C"][1][:110])
    check("E passed against the supplied real folder", got["E"][0] == "PASS",
          got["E"][1][:110])
    failures = [limb for limb, (verdict, _) in got.items() if verdict == "FAIL"]
    check("main() exits nonzero, and only because of A",
          rc == 1 and failures == ["A"], f"rc={rc}, failures={failures}")
    check("main() wrote its own log", log.exists() and log.stat().st_size > 0)
    subject_lines = [l for l in gate.LOG if l.startswith("subject removed:")]
    check("main() removed the fixture it built",
          bool(subject_lines) and "exists=False" in subject_lines[0],
          subject_lines[0] if subject_lines else "no removal line")

    print("\n=== an absent --real-folder must be NOT EXERCISED, never FAIL ===")
    sys.argv = ["gate", "--real-folder", str(fixture / "does_not_exist"),
                "--log", str(fixture / "gate2.log")]
    gate.RESULTS.clear()
    gate.LOG.clear()
    try:
        rc = gate.main()
    finally:
        sys.argv = argv
    got = verdicts()
    check("E reports NOT EXERCISED for a missing folder",
          got["E"][0] == "NOT EXERCISED", got["E"][1][:110])
    check("and a missing folder contributes no FAIL of its own",
          [l for l, (v, _) in got.items() if v == "FAIL"] == ["A"],
          f"failures={[l for l, (v, _) in got.items() if v == 'FAIL']}")

    import shutil
    shutil.rmtree(fixture, ignore_errors=True)

    print()
    if FAILURES:
        print(f"SELFTEST FAILED: {len(FAILURES)} check(s): {FAILURES}")
        return 1
    print("SELFTEST PASSED — every limb body executes, and limb D's control fires.")
    print("Limbs A, B and D still need Windows for the claims they exist to make.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
