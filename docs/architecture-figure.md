# The architecture figure

A two-panel diagram of Microclaw's architecture, built for use as a manuscript
supplementary figure.

    python scripts/build_architecture_figure.py            # render into build/
    python scripts/build_architecture_figure.py --check    # drift check, no render

## The rendered figure is not committed

Only the generator and this file live in the repo. A committed diagram is a
second spelling of `CLAUDE.md`'s architecture story, and it would be the one
nobody watches — it goes stale silently and then lies to whoever opens it. The
SVG and PNG land in the gitignored `build/architecture-figure/`.

`--check` is what keeps the *generator* honest: every module under `microclaw/`
must be either drawn in a panel or listed in `OMITTED` with a reason. Adding a
module fails the check instead of quietly going missing from the figure. It
caught a stale entry on its first run.

If a snapshot ever has to be committed, put it in a **dated design doc**, where
`design/` is already read as history — never in a file that claims to be current.

## The constraint is type size, not layout

Mermaid's label font is a fixed number of viewBox units, so printed point size
falls out of how many units wide the diagram is:

    pt = font_units x (text_width_mm / viewbox_width_units) x 72 / 25.4

The first version carried all 33 modules with two-line descriptions, rendered
5112 units wide, and printed at **1.5 pt** at 170 mm. Most journals want 5–7 pt.
Rotating the page does not help: it buys a ~1.30:1 frame and never touches the
font-to-width ratio. Only reducing units per node does.

Fixes, in order of effect:

1. **Name-only labels.** The descriptive sub-text set each node's width. The
   prose belongs in the caption — that is what captions are for.
2. **Prune plumbing.** Paths, credentials, updater, assets, setup mode. Listed
   in `OMITTED` with reasons.
3. **Two panels.** (a) control plane, (b) acquisition and analysis. They join at
   `tools.py`, which is a real hub, so the split follows structure.
4. **`flowchart TB`.** The panels come out tall rather than wide, which uses the
   page's spare vertical room. Measured: TB 6.5 pt vs LR 5.5 pt.

Current: 1215 and 2023 units wide, **7.6 pt at 170 mm**, 185 mm tall stacked.
The script fails if either the 7 pt floor or the 220 mm page height regresses.

## Settings that are load-bearing

- `themeVariables.fontSize: 32px` — node spacing is a fixed unit count
  independent of the font, so a larger font shrinks spacing's share of the
  width. This is the main lever; 20px gives 6.5 pt, 32px gives 7.6 pt.
- `flowchart.subGraphTitleMargin` — **required at this font.** Without it the
  subgraph titles render underneath the node boxes, struck through.
- Config goes in a **JSON file passed with `-c`**, not an inline `%%{init}%%`
  directive. The inline directive silently fails to parse a nested object.
- External systems are dashed stadium nodes, not a `subgraph`. Boxing them makes
  the layout engine stretch that box down one whole edge and drag every edge
  across the figure.

## Output

Ship the **SVG**; journals want vector for line art. The PNG is for preview.

## A trap when measuring

Arrowhead markers carry their own `viewBox="0 0 10 10"`. A loose regex matches
one of those instead of the root `<svg>` and reports a 10x10 figure. Anchor the
pattern to `<svg`.
