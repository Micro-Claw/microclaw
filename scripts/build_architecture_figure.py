"""Rebuild the two-panel architecture figure used as a manuscript supplementary figure.

The figure is NOT committed. A rendered diagram goes stale silently and then
lies, so only this generator and `docs/architecture-figure.md` live in the repo;
the SVG/PNG land in the gitignored `build/` and are regenerated on demand.

What makes the figure hard is not layout, it is type size. Mermaid's label font
is a fixed number of viewBox units, so the printed point size is decided by how
many units wide the diagram ends up:

    pt = font_units x (text_width_mm / viewbox_width_units) x 72 / 25.4

The first version of this diagram carried every module with a two-line
description and rendered at 5112 units wide, which is ~1.5 pt at 170 mm --
illegible, and far under the ~5-7 pt most journals require. The fixes, in order
of effect: name-only node labels with the prose moved to the caption, plumbing
modules pruned, two panels instead of one, and `flowchart TB` so the panels are
tall rather than wide. This script fails if that budget regresses.

    python scripts/build_architecture_figure.py            # render into build/
    python scripts/build_architecture_figure.py --check    # drift check, no render

`--check` is the anti-staleness guard: every module under `microclaw/` must be
either drawn in a panel or listed in OMITTED with a reason. A new module makes
the check fail rather than quietly going missing from the figure.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "microclaw"
OUT_DIR = ROOT / "build" / "architecture-figure"

# Journal geometry. 170 mm is a typical full-text-width figure; 220 mm is a
# conservative usable page height once a caption is allowed for.
TEXT_WIDTH_MM = 170.0
PAGE_HEIGHT_MM = 220.0
PT_FLOOR = 7.0

# Raising fontSize is what buys point size: node spacing is a fixed unit count
# independent of the font, so a larger font shrinks spacing's share of the total
# width. subGraphTitleMargin is required at this font -- without it the subgraph
# titles render underneath the node boxes.
MMDC_CONFIG = {
    "themeVariables": {"fontSize": "32px"},
    "flowchart": {
        "nodeSpacing": 30,
        "rankSpacing": 45,
        "subGraphTitleMargin": {"top": 6, "bottom": 20},
    },
}

PANEL_A = """flowchart TB
    BROWSER(["browser"])
    API(["Anthropic API"])
    MM(["Micro-Manager<br/>MMCore"])
    HW(["microscope<br/>hardware"])

    subgraph S1["session"]
        WEB["webserve.py"]
        AGENT["agent.py"]
        CONV["conversation.py"]
    end

    subgraph S2["tool surface"]
        TOOLS["tools.py<br/>81 tools"]
    end

    subgraph S3["policy"]
        SAFETY["safety.py"]
        AUTH["authorization.py"]
    end

    subgraph S4["hardware access"]
        CTRL["controller.py"]
        INV["rig_inventory.py"]
    end

    BROWSER <--> WEB
    WEB --> AGENT
    AGENT <--> API
    AGENT --> CONV
    AGENT --> TOOLS
    TOOLS --> SAFETY
    SAFETY --> AUTH
    AUTH -.gates.-> TOOLS
    TOOLS --> CTRL
    CTRL --> AUTH
    CTRL --> MM
    INV --> MM
    MM --> HW

    classDef ext fill:#fff,stroke:#666,stroke-dasharray:5 3
    class BROWSER,API,MM,HW ext
"""

PANEL_B = """flowchart TB
    TOOLS["tools.py"]
    ACQE(["pycro-manager<br/>AcqEngJ"])
    MM(["Micro-Manager<br/>MMCore"])
    SCRIPT(["standalone<br/>script"])

    subgraph S1["acquisition"]
        PLAN["acquisition.py"]
        HMGR["hook_manager.py"]
        HDEC["hook_decisions.py"]
        HOOKS["hooks.py"]
    end

    subgraph S2["analysis"]
        IMG["image_analysis.py"]
        AF["autofocus.py"]
        DONE["completed_dataset.py"]
        ILP["ilastik_adapter.py"]
        CAL["calibration.py"]
    end

    subgraph S3["export"]
        EXPORT["export_session_script"]
    end

    TOOLS --> PLAN
    TOOLS --> HMGR
    TOOLS --> DONE
    TOOLS --> CAL
    TOOLS -.records.-> EXPORT
    HMGR --> HDEC
    HDEC --> HOOKS
    PLAN --> ACQE
    ACQE --> HOOKS
    ACQE --> MM
    HOOKS --> AF
    HOOKS --> IMG
    DONE --> IMG
    DONE --> ILP
    CAL --> IMG
    EXPORT --> SCRIPT
    SCRIPT -.-> ACQE

    classDef ext fill:#fff,stroke:#666,stroke-dasharray:5 3
    class ACQE,MM,SCRIPT ext
"""

PANELS = {"fig_a": PANEL_A, "fig_b": PANEL_B}

# Modules deliberately absent from the figure. Detail was traded for legibility
# (see the module docstring); each entry says why, so a reviewer can disagree.
OMITTED = {
    "__init__": "empty package marker",
    "__main__": "CLI argument parsing; the entry point drawn is webserve.py",
    "assets": "browser asset inlining, not architecture",
    "bridge_check": "readiness probe, off the control path",
    "config": "per-user file layout",
    "credentials": "API key resolution",
    "dataset_mosaic": "pure geometry helper, folded into calibration.py in panel b",
    "emu_manager": "one vendor's device manager; a controller.py detail",
    "errors": "error taxonomy, no runtime role",
    "image_analysis": "drawn in panel b",
    "knowledge_manager": "per-user knowledge file",
    "paths": "platform file conventions",
    "setup_tools": "restricted setup-mode tool subset",
    "shortcut": "Windows desktop shortcut installer",
    "skills": "packaged workflow markdown",
    "tools_schema": "the JSON schemas for tools.py, drawn as one node",
    "updates": "managed-install updater; deliberately has no tool surface",
}


def shown_modules() -> set[str]:
    """Module stems that appear as a node label in some panel."""
    found: set[str] = set()
    for body in PANELS.values():
        found.update(re.findall(r"(\w+)\.py", body))
    return found


def check_drift() -> list[str]:
    """Report modules that are neither drawn nor explicitly omitted, and vice versa."""
    actual = {p.stem for p in PACKAGE.glob("*.py")}
    drawn = shown_modules()
    problems = []
    for stem in sorted(actual - drawn - set(OMITTED)):
        problems.append(f"microclaw/{stem}.py exists but is neither drawn nor in OMITTED")
    for stem in sorted(OMITTED.keys() - actual):
        problems.append(f"OMITTED lists {stem!r}, which no longer exists")
    for stem in sorted(drawn - actual):
        problems.append(f"a panel draws {stem}.py, which no longer exists")
    return problems


def root_viewbox(svg: str) -> tuple[float, float]:
    """Width and height of the root <svg> viewBox.

    Deliberately anchored to `<svg`: arrowhead markers carry their own
    `viewBox="0 0 10 10"`, and a looser pattern matches one of those instead.
    """
    m = re.search(r'<svg\b[^>]*?viewBox="[\d.\-]+ [\d.\-]+ ([\d.]+) ([\d.]+)"', svg)
    if not m:
        raise SystemExit("no root viewBox in rendered SVG")
    return float(m.group(1)), float(m.group(2))


def label_font_units(svg: str) -> float:
    m = re.search(r"#my-svg\{[^}]*font-size:(\d+(?:\.\d+)?)px", svg)
    if not m:
        raise SystemExit("no root font-size in rendered SVG")
    return float(m.group(1))


def render(out_dir: Path) -> dict[str, Path]:
    if shutil.which("npx") is None:
        raise SystemExit("npx not found; mermaid-cli is fetched with npx at build time")
    out_dir.mkdir(parents=True, exist_ok=True)
    written = {}
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "mmdc-config.json"
        cfg.write_text(json.dumps(MMDC_CONFIG))
        for name, body in PANELS.items():
            src = out_dir / f"{name}.mmd"
            src.write_text(body)
            for ext, extra in (("svg", []), ("png", ["-s", "2"])):
                target = out_dir / f"{name}.{ext}"
                subprocess.run(
                    ["npx", "-y", "-p", "@mermaid-js/mermaid-cli", "mmdc",
                     "-c", str(cfg), "-i", str(src), "-o", str(target),
                     "-b", "white", *extra],
                    check=True, stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                )
            written[name] = out_dir / f"{name}.svg"
    return written


def report(svgs: dict[str, Path]) -> int:
    dims = {n: root_viewbox(p.read_text()) for n, p in svgs.items()}
    font = label_font_units(next(iter(svgs.values())).read_text())
    width = max(w for w, _ in dims.values())
    height = sum(h for _, h in dims.values())
    for name, (w, h) in dims.items():
        print(f"  {name}: {w:.0f} x {h:.0f} units ({w / h:.2f}:1)")
    pt = font * (TEXT_WIDTH_MM / width) * 72 / 25.4
    tall = TEXT_WIDTH_MM * height / width
    print(f"  stacked at {TEXT_WIDTH_MM:.0f} mm wide: {tall:.0f} mm tall, labels {pt:.1f} pt")
    failures = []
    if pt < PT_FLOOR:
        failures.append(f"labels are {pt:.1f} pt, under the {PT_FLOOR:.0f} pt floor")
    if tall > PAGE_HEIGHT_MM:
        failures.append(f"stacked height {tall:.0f} mm exceeds {PAGE_HEIGHT_MM:.0f} mm")
    for f in failures:
        print(f"  FAIL: {f}", file=sys.stderr)
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="Check the figure against the package layout; render nothing.")
    ap.add_argument("--out", type=Path, default=OUT_DIR,
                    help=f"Output directory (default: {OUT_DIR.relative_to(ROOT)}).")
    args = ap.parse_args()

    problems = check_drift()
    for p in problems:
        print(f"  DRIFT: {p}", file=sys.stderr)
    if args.check:
        print("figure matches the package layout" if not problems else "figure has drifted")
        return 1 if problems else 0

    svgs = render(args.out)
    print(f"wrote {len(svgs) * 2} files to {args.out}")
    return report(svgs) or (1 if problems else 0)


if __name__ == "__main__":
    raise SystemExit(main())
