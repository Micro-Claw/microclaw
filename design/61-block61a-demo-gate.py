"""Block 61a demo-machine gate: the installed build carries its skills.

Every limb here is a computation, so it ships as a program rather than pasted
PowerShell blocks: each limb reports independently, a refusal in one cannot hide
the rest, and the exit status is nonzero if any limb did not pass (58a). The
half that needs a human -- an agent *choosing* to load a skill, and a real
session exporting -- is in the runbook beside this file, deliberately, because
that is the half a program cannot judge.

What needs the demo machine here is narrow: the suite already proves the
catalog, the refusals and the wheel's package data on the source tree. What it
cannot prove is that the artifact `install.bat` actually produces on Windows
carries `microclaw/skills/*/SKILL.md`. `pyproject.toml` lists package data
individually today and this block replaces one entry with a pattern; setuptools'
glob handling is the footgun the design names. So this program deliberately
imports the INSTALLED microclaw, not a checkout -- see `_import_installed`.

It reads no hardware, opens no bridge, and needs no safety config or
workspace_dir: `load_skill` observes nothing. A gate must not require
configuration the product does not require (60b), so there is nothing to
prepare and nothing to put back.
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

RESULTS = []


class NotExercised(Exception):
    """This machine could not exercise the limb; never a pass."""


class Tee:
    def __init__(self, stream, path):
        self.stream = stream
        self.file = path.open("w", encoding="utf-8")

    def write(self, data):
        # The gate owns its log: PowerShell 5.1's Start-Transcript does not
        # capture a native child process's stdout and came back empty twice
        # (58a). Keep the file UTF-8 and the console ASCII-safe.
        self.stream.write(data.encode("ascii", "backslashreplace").decode("ascii"))
        self.file.write(data)
        return len(data)

    def flush(self):
        self.stream.flush()
        self.file.flush()


def limb(name, fails_if):
    def decorate(fn):
        try:
            detail, status = fn() or "", "PASS"
        except NotExercised as exc:
            detail, status = str(exc), "NOT EXERCISED"
        except Exception as exc:                    # noqa: BLE001 - reported
            detail, status = f"{type(exc).__name__}: {exc}", "FAIL"
            traceback.print_exc()
        RESULTS.append({"name": name, "status": status, "detail": detail,
                        "fails_if": fails_if})
        print(f"{status}: {name} - {detail}")
        return fn
    return decorate


def _import_installed():
    """Import microclaw as the demo machine runs it, never as a checkout.

    This program is copied next to the evidence folder and run with the
    installed interpreter, so the repository must not be on sys.path.  A
    checkout ahead of the install would make every limb below report on source
    that the operator is not running -- the same failure the design's wheel test
    exists to prevent, one level out.  Refuse rather than guess.
    """
    here = Path(__file__).resolve()
    import microclaw
    origin = Path(microclaw.__file__).resolve()
    if origin.parent.parent == here.parent.parent:
        raise SystemExit(
            f"Refusing to score the checkout at {origin.parent}. Run this file "
            f"with the installed interpreter from outside the repository; see "
            f"the runbook's Step 1."
        )
    return origin


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("block61a-demo-evidence"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    sys.stdout = sys.stderr = Tee(sys.__stdout__, args.output / "gate.txt")

    # The limbs are nested here, and that is load-bearing: @limb runs the
    # function it decorates immediately, so a limb defined at module level would
    # execute at import -- before the log exists and before the checkout guard
    # below has had a chance to refuse. Same shape as 60b's gate.
    origin = _import_installed()
    print(f"installed microclaw: {origin}")
    print(f"interpreter: {sys.executable}")

    @limb("G1 installed build carries every skill body",
          fails_if="the package-data pattern did not ship microclaw/skills/*/SKILL.md")
    def _g1():
        from microclaw.skills import SKILL_CATALOG
        from microclaw.tools import load_skill
        if not SKILL_CATALOG:
            raise AssertionError("installed catalog is empty")
        lengths = {}
        for skill in SKILL_CATALOG:
            result = load_skill(None, None, skill.name)
            body = result.get("documentation", "")
            if "error" in result or len(body) < 500:
                raise AssertionError(
                    f"{skill.name} returned {len(body)} chars: "
                    f"{result.get('error', 'short body')}"
                )
            lengths[skill.name] = len(body)
        return f"{len(lengths)} skills, chars {json.dumps(lengths, sort_keys=True)}"

    @limb("G2 an unknown and a path-shaped name are both refused",
          fails_if="load_skill accepted a name that is not in the catalog")
    def _g2():
        from microclaw.tools import load_skill
        refused = {}
        for name in ("no-such-skill", "../smlm", "smlm/SKILL.md", "/smlm"):
            result = load_skill(None, None, name)
            if "error" not in result:
                raise AssertionError(f"{name!r} was not refused: {sorted(result)}")
            if "Available catalog names" not in result["error"]:
                raise AssertionError(f"{name!r} refused without naming the catalog")
            refused[name] = result["error"][:60]
        return f"{len(refused)} names refused, each naming the catalog"

    @limb("G3 the installed prompt carries the catalog and no skill body",
          fails_if="a skill body is in the permanent block, or the catalog is not")
    def _g3():
        from microclaw.agent import SYSTEM_PROMPT
        from microclaw.skills import SKILL_CATALOG
        missing = [s.name for s in SKILL_CATALOG
                   if f"- {s.name}: {s.description}" not in SYSTEM_PROMPT]
        if missing:
            raise AssertionError(f"catalog lines absent from the prompt: {missing}")
        for marker in ("# Single-Molecule Localization Microscopy",
                       "# htSMLM / EMU reference",
                       "# pycro-manager hook API reference",
                       "# DNA-PAINT Experiment Protocol",
                       "## Usual path ordering"):
            if marker in SYSTEM_PROMPT:
                raise AssertionError(f"skill body {marker!r} is in the permanent block")
        # Block 61a deliberately keeps the Nikon procedure in the core prompt;
        # its removal is 61c and is authorized only by a positive Ti
        # confirmation. A gate that let it vanish early would hide exactly that.
        if "Do BOTH of these every time you engage the lock" not in SYSTEM_PROMPT:
            raise AssertionError("the Nikon paragraph 61a must retain is gone")
        return (f"{len(SKILL_CATALOG)} catalog lines present, no body inlined, "
                f"Nikon paragraph retained, prompt {len(SYSTEM_PROMPT)} chars")

    (args.output / "results.json").write_text(
        json.dumps({"results": RESULTS, "installed": str(origin),
                    "interpreter": sys.executable}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    failed = [item for item in RESULTS if item["status"] != "PASS"]
    print("BLOCK 61a DEMO GATE " + ("FAILED" if failed else "PASSED"))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
