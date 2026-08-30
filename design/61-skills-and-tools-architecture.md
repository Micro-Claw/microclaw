# Skills and tools are different interfaces

## Problem

Procedural knowledge and microscope capability are the same interface today.

1. Four references are Python string constants (`HOOK_REFERENCE`,
   `SMLM_REFERENCE`, `OPTICS_REFERENCE`, `HTSMLM_REFERENCE`); only DNA-PAINT
   keeps its maintained text in Markdown, read through `importlib.resources`.
2. Five `get_*_documentation` functions sit in `TOOLS` and `TOOL_REGISTRY` as
   peers of `move_stage_xy` and `run_timelapse`.
3. `SYSTEM_PROMPT` carries SMLM routing, EMU/htSMLM procedure and Nikon PFS
   procedure on every rig, including rigs where none of them can apply.

The costs are authoring and attention, and they are not the same size.

**Authoring is the real one.** Prose in a `"""..."""` constant is hard to read,
review, diff and render, and it is edited by whoever is editing the acquisition
code. `dna_paint_docs.py` already states the fix in its own docstring: the file
the user maintains *is* the file the tool returns, so the two cannot drift.

**Context is smaller than it looks — measure it before designing for it.**
The doc bodies are already lazy: each `get_*_documentation` imports its module
inside the function, so nothing enters context until the model calls the tool.
What every session actually pays is the routing prose plus five schemas:

| | chars | ~tokens | share of static context |
|---|---|---|---|
| SMLM + htSMLM + Nikon paragraphs in `SYSTEM_PROMPT` | 4,048 of 32,088 | ~1,000 | 3% |
| the five doc-tool schemas | 2,999 of 89,802 | ~750 | 2% |
| all 85 tool schemas | 89,802 | ~22,450 | 74% |

So the win available here is ~1,750 tokens gross — call it ~1,650 after the
catalog that replaces them — against a ~30,500-token cached system block. It is
worth taking, and it does not justify a subsystem. The 74% row is where a
context project would go, and this is not that project.

## Decision

Move maintained prose to `microclaw/skills/<name>/SKILL.md`. Replace the five
`get_*_documentation` tools with **one** `load_skill(name)` tool in the existing
schema list and registry. Ultimately delete the specialist paragraphs from
`SYSTEM_PROMPT` and leave a generated catalog of skill names and descriptions
in their place; the safety-relevant Nikon paragraph has the staged removal
described below.

The boundary being drawn is semantic, and a directory is enough to hold it:

| Kind | Purpose | Home |
|---|---|---|
| Core prompt | identity, authorization, dose, verify-before-asserting, the skill catalog | `agent.SYSTEM_PROMPT` |
| Tool | reads or changes microscope/runtime state | `tools.py`, `tools_schema.py` |
| Skill | how to combine tools for a task or a rig | `microclaw/skills/<name>/SKILL.md` |
| User knowledge | facts about this rig, sample, preference | `~/.microclaw/knowledge.yaml` |

Read-only is not the distinction: `get_system_state` stays a tool because it
observes the live machine. A skill changes reasoning and observes nothing.

## Layout

```text
microclaw/
  skills/
    hook-authoring/SKILL.md      (from hook_docs.py, 584 lines)
    smlm/SKILL.md                (from smlm_docs.py, 405 lines)
    dna-paint/SKILL.md           (dna_paint_protocol.md, moved)
    optical-paths/SKILL.md       (from optics_docs.py, 60 lines)
    htsmlm/SKILL.md              (from htsmlm_docs.py, 149 lines)
    nikon-pfs/SKILL.md           (from SYSTEM_PROMPT, 2,353 chars)
  skills.py                      (catalog + loader, ~60 lines)
```

Flat. Each skill is one `SKILL.md` with `name` and a discriminating
`description` in YAML frontmatter. **No `references/` subtree yet** — five of
the six bodies are under 500 lines, and splitting a 149-line htSMLM reference
into a skill plus a reference is two files doing one file's work. Add
`references/` to a skill when that skill's body is actually too big to load
whole, and not before.

Markdown is package data, read through `importlib.resources` exactly as
`dna_paint_docs.load_reference` does. `pyproject.toml` lists package-data files
individually today; a tree needs a pattern (`skills/*/*.md`), and setuptools'
recursive-glob handling is a footgun — hence the wheel test below.

## The tool

```python
@emits_nothing
def load_skill(ctrl, guard, name: str) -> dict:
    """Return a skill's SKILL.md. Reads no hardware; changes no state."""
```

`@emits_nothing` is not optional. An undecorated tool collects the default
refusal and plants a `raise RuntimeError` in every exported script of any
session that loaded a skill. That has shipped three times already — 43h
(`generate_and_save_hook`), 47 (`set_roi`/`clear_roi`), 52a
(`move_named_stage`) — and all five current doc tools are `@emits_nothing`
(`tools.py:8674–8749`).

The catalog is generated from frontmatter at import time and rendered into the
system prompt as name + description, one line each. `name` is validated against
the catalog; anything else, including a path, is refused. There is no second
schema list, no second registry, and no `SKILL_SCHEMAS`: the model receives
these through the tool protocol anyway, so a parallel plumbing layer buys a
label and costs a parity invariant.

These are repository-owned package resources, not optional user extensions.
Malformed frontmatter, a directory/name mismatch, or duplicate skill names are
therefore programming or packaging errors and fail startup loudly. Silently
omitting a broken skill would leave the catalog and the workflows the package
claims to support out of sync. Validate the source tree in tests, and validate
the installed wheel separately so a bad package-data pattern fails before a
release reaches a microscope.

**An empty catalog fails the same way**, and it is the likelier failure. Each
condition above needs a skill that is present and wrong; the package-data glob
above drops the tree entirely, and zero skills is malformed nothing, duplicates
nothing and mismatches nothing. Startup would succeed, the catalog would render
empty, and `load_skill` would refuse every name as unknown. Assert a nonempty
catalog where the others are checked. The wheel test separately compares its
catalog names with the names discovered from the source tree, so both total and
partial package-data omissions fail without maintaining a hard-coded count.

## What leaves the system prompt

The SMLM (895 chars) and EMU/htSMLM (800 chars) paragraphs are replaced in the
initial block by the catalog and one routing rule: load the relevant skill
before running its specialized workflow. The Nikon paragraph (2,353 chars) is
copied into `nikon-pfs` but remains redundantly in the core prompt until a Ti
confirmation proves the new route on real hardware; only then does a small
follow-up remove it. What otherwise stays is what must be true *before* the
model can pick a skill — identity and GUI context, authorization, bounds and
dose accounting, verify-state-before-asserting, and the universal tool
semantics that prevent unsafe misuse.

The implementation of `check_emu_installed` is unchanged; its schema description
is updated to name `load_skill` instead of the removed documentation tool, and
keeps carrying the conditional rule in prose, at zero cost: load `htsmlm` after
positive plugin/configuration detection, or when the operator identifies the
system or workflow as htSMLM or EMU — never merely because the catalog lists it.
Explicit operator identification stays a route, because it is the only one left
when the Micro-Manager application directory cannot be located.

**That rule lives in the tool's schema, not in the skill's frontmatter.** A
catalog `description` is one discriminating line and nothing more: it is in the
permanent system block, so policy written there is the EMU/htSMLM paragraph
moved rather than removed, and most of the win in the table above goes back.
The schema description is already paid for, and it is where the model is
standing at the moment the rule applies.

**`nikon-pfs` needs the same anchor, and today it has none.** htSMLM routes
through `check_emu_installed`; no tool plays that part for Nikon — checked
across all 85 schemas, **zero mention PFS, TIPFS or Nikon**. So route it
through `get_focus_lock_state`, `set_focus_lock` and `run_autofocus`, whose
descriptions gain one sentence: on a rig with a hardware focus lock of this
kind, load `nikon-pfs` before engaging or adjusting it. All three already
exist, are already paid for in every session, and are where the agent is
standing when the rule applies. `set_focus_lock` carries it too because it is
the tool that performs the hazardous act, and an agent that goes straight there
must still meet the rule.

The core prompt keeps one vendor-neutral routing invariant, and it is an
**ordering** rule, not a second prohibition on the raw setter: before any
operation that engages or adjusts a hardware focus lock, call
`get_focus_lock_state` first. That makes the state tool the single discovery
point instead of duplicating a Nikon/PFS warning on the generic property
setter. The raw-setter half is already written —
`agent.py:118` says *"Never call set_device_property for core operations that
have dedicated tools (stage, channel, exposure)"*, and focus lock has a
dedicated tool (`set_focus_lock`) that is simply missing from that
parenthetical. Add the word there rather than writing a parallel rule.

**Identification is by the `device` value the tool returns**, and nothing else.
On the non-EMU path `get_focus_lock_state` reports it from
`core.get_auto_focus_device()` (`tools.py:9061`). Make the EMU result shape
consistent by also returning `"device": lock["device"]`; today that branch
returns `engaged`, `raw_value`, `property`, and `qpd` but omits the device
(`tools.py:9094-9099`). **Add the key; do not recover the device by splitting
`property` on the dot.** That branch does carry the identity, flattened into
`property` as `"<device>.<property>"`, so `property.split(".")[0]` is the
shortcut an implementer reaches for instead — and parsing an identity back out
of a display string is the same fragility this paragraph forbids one sentence
later. Match against the returned device value; do **not**
substring-scan properties for `"PFS"` — a status property may contain it as
readily as the lock device, which is exactly the kind of identity confusion
design/59 fixed in `_PORT_LABEL_WORDS`. And where no autofocus device is
configured the tool returns `{"engaged": None, "reason": ...}` with **no
`device` key at all** (`tools.py:9088`): the invariant is a silent no-op there.
Do not add an "unknown → ask the operator" branch, which would turn every
ordinary rig into a question.

Worth recording, because it is why the prose has to carry this: authorization
already registers the *EMU* focus-lock device/property as a typed capability
(`authorization.py:969-983`). The non-EMU Nikon path — the one this skill is
about — has no such code-level guard.

This matters more here than for htSMLM, because the Nikon paragraph is not
reference material — it is operational safety procedure, and it says so:
*"Do BOTH of these every time you engage the lock, as steps, not as caveats you
mention and skip."* Unconditionally in the prompt today, a PFS rig gets the
coverslip-tilt and jog checks without electing anything. Behind an unanchored
one-line catalog entry it would not, and the failure is a pushed-up coverslip,
not a worse answer. So the paragraph is **retained by design** until the route
is confirmed on a real Ti — it is the operational-safety exception the section
above already admits, and migration steps 3-5 keep it in the core prompt,
redundant with `nikon-pfs`, for the first merged release. Its removal is a
follow-up that a positive confirmation authorizes; nothing here treats
retention as a fallback.

**Nikon evidence arrives after merge, so Nikon prompt removal does too.** The Ti
is in daily use, but by a collaborator who receives code through the update
route — so anything on `main` can be exercised on it and anything on a block
branch effectively cannot. Step 5 of the block workflow therefore cannot cover
the Nikon limb. This does not hold the architecture block because its merged
version keeps the existing Nikon procedure in the core prompt while also
shipping the skill and anchors. The recorded-payload fixture is the pre-merge
evidence for routing; Ti confirmation is a carried-forward row collected once
the block is on `main`. Only a positive confirmation authorizes the small
follow-up that removes the duplicated Nikon paragraph. If confirmation is
negative, retain the core paragraph while fixing and re-testing the route — no
released version has first lost the guidance it is meant to preserve.

## What we deliberately do not build

**No eligibility predicates.** A guard whose "no" withholds a Markdown file
protects nothing: no dose, no motion, no irreversible state. Safety lives in
`safety.py` and `authorization.py` at the point of action, and a skill grants no
authority to reach them. Under CLAUDE.md's rule this is information, not a gate.
It also fails closed in the wrong direction — the current htSMLM description
carries a second route, *"OR if the user has explicitly mentioned htSMLM or
EMU"*, which a detection predicate drops. An operator who says "I'm running
htSMLM" on a machine where the MM app directory cannot be located would then be
refused the document on precisely the rig that needs it. If a session is later
measured loading the wrong skill or loading it too late, that measurement is
what buys a predicate, and it bolts onto this shape unchanged.

**No separate skill registry or schema list.** Two registries need a disjoint-
union parity test where one needs equality. `tests/test_schema_parity.py:18`
asserts `set(TOOL_REGISTRY) == set(_SCHEMA_BY_NAME)`; keep it as equality.

**No deprecation cycle for the five old names.** Nothing outside this repo
depends on them, so keep them working only if it is free — it is not. Aliases
in `TOOL_REGISTRY` that are absent from `TOOLS` are exactly what forces the
parity test to be loosened. Replace outright, in one commit.

**No `tools.py` split.** Unrelated, and 9,683 lines moving at the same time as
model context changes makes any regression unlocalizable.

## Migration — one block plus the Nikon follow-up

1. Move the four constants to `skills/<name>/SKILL.md` and relocate
   `dna_paint_protocol.md`. Add `nikon-pfs/SKILL.md` from the prompt text.
   Verify the parsed Markdown body against the old Python constant **once, in
   review**, after normalizing only the one leading/trailing newline introduced
   by the old triple-quoted literal. Whole-file byte equivalence is impossible
   because `SKILL.md` adds YAML frontmatter. This is a migration-time check, not
   a shipped test. DNA-PAINT receives frontmatter but its body remains identical.
2. Add `skills.py` and `load_skill`; delete the five tools, their five schemas
   and their five registry entries; generate the catalog into the prompt.
3. Delete the SMLM and EMU/htSMLM paragraphs from `SYSTEM_PROMPT`. Keep the
   Nikon paragraph unchanged and redundant with `nikon-pfs` for the first
   merged release.
4. Land the Nikon anchors, which are code changes and not wording: the one
   sentence added to `get_focus_lock_state`, `set_focus_lock` and
   `run_autofocus`; `focus lock` added to the dedicated-tool parenthetical at
   `agent.py:118`; the ordering invariant in the core prompt; and the `device`
   key on the EMU branch of `get_focus_lock_state`. These ship with the redundant
   Nikon paragraph so the real-rig confirmation observes the new route without
   withdrawing the old guidance.
5. After merge, record the Ti confirmation of
   `get_focus_lock_state` → `load_skill(name="nikon-pfs")` → the requested
   focus-lock action. On a positive result, remove the Nikon paragraph from
   `SYSTEM_PROMPT` in a small follow-up and close the carried-forward row. On a
   negative result, leave the paragraph in place, fix the route, and repeat the
   confirmation before removing anything.

### Call sites that must move with it

Found by grep; the second one is the trap.

- `tools.py:9573–9578` (registry) and the five schemas in `tools_schema.py`,
  which run 1960–2035 but are **interleaved** with `check_emu_installed`
  (2012) and `get_emu_configuration` (2039) — both of which stay.
- The `check_emu_installed` and `get_emu_configuration` schemas stay, but any
  description that names a removed documentation tool changes to the
  corresponding `load_skill(name=...)` route.
- Nikon routing changes three existing schemas — `get_focus_lock_state`,
  `set_focus_lock`, and `run_autofocus` — and the dedicated-tool sentence at
  `agent.py:118`, whose parenthetical gains `focus lock`. These are capability
  anchors, not incidental wording: omitting any of them reopens the direct-
  engagement path the design is meant to close. The EMU branch of
  `get_focus_lock_state` also gains the `device` field described above so both
  discovery paths expose the identity on which routing depends.
- **41 test assertions across four files** (`test_survey_runner.py`,
  `test_optics_docs.py`, `test_hook_manager.py`, `test_tools.py`) import these
  constants and assert on their *content*. They are not incidental: they pin
  design/24, /26 and /27 into the hook reference — "NEVER return None",
  "STILL FIRES THE CAMERA", "SILENT NO-OP". They must keep passing against the
  Markdown, through the loader. Preserve substantive content assertions. Tests
  that specifically assert an old documentation-tool name must change with the
  interface; that rename is not a protocol-content regression.
- Prose that names the old tools: `smlm_docs.py` (7 mentions, one asserted at
  `test_tools.py:5223` as `count(...) >= 2`), `dna_paint_protocol.md:3`,
  `agent.py:357,454,519`, `tools.py:3352`. Rewrite those references to the
  appropriate `load_skill(name=...)` call. In particular, the assertion at
  `test_tools.py:5223` changes because preserving the removed function name
  would teach the model to call a tool that no longer exists.

## Required tests

- every skill has valid frontmatter and its directory name matches `name`;
- skill identifiers are unique and do not collide with names in
  `TOOL_REGISTRY` (`load_skill` itself remains an ordinary registry entry);
- malformed packaged skill metadata fails startup rather than silently dropping
  the skill from the catalog, **and so does an empty catalog**;
- every catalog `description` is a single line;
- `load_skill` refuses an unknown name and any path-shaped argument;
- `load_skill` is decorated, and an exported session that loaded a skill
  compiles and contains no `NOT EMITTED`;
- schema/registry parity stays an **equality**;
- build and install the wheel, then assert that its catalog names equal the
  nonempty catalog names discovered from the source tree — this catches total
  and partial package-data omissions without a hard-coded count or a vacuous
  loop over whatever the wheel happened to contain. **The two sides must be
  discovered by different mechanisms**: a plain filesystem glob over the repo's
  `microclaw/skills/*/SKILL.md` for the tree, `importlib.resources` against the
  installed package for the wheel. Every resource test in the suite today reads
  through `resources.files("microclaw")` (`test_history_viewer.py:60`,
  `test_transcript_js.py:24`, `test_safety.py:241`), so that is the idiom an
  implementer will reach for on both sides — and under an installed wheel both
  queries then hit the same object and the assertion passes having compared
  nothing. **Install the built wheel into a throwaway virtualenv and inspect it
  with that venv's interpreter**, from a working directory outside the
  repository. A subprocess alone is not isolation: this repo's standard dev
  setup is `pip install -e .`, which leaves an `__editable__.microclaw-*.pth`
  and a finder module in site-packages that map `microclaw` back to the
  checkout from any cwd and any `PYTHONPATH` — so the wheel would never be
  read and the test would compare the tree to itself, which is the failure the
  paragraph above exists to prevent. Never mutate the ambient environment, per
  the standing no-`-e`-while-another-tree-is-live rule. Say in the test which
  side is which and why;
- a session with no SMLM or htSMLM trigger has neither skill body in its system
  blocks, and the catalog is present. Before the Ti follow-up, the deliberately
  redundant Nikon core paragraph remains; after that follow-up, a session with
  no Nikon trigger has no Nikon skill body either;
- **and the converse, which is the one that matters**: a session whose opening
  does trigger a skill loads it before acting. The negative above passes on an
  agent that never loads anything, and skipping a skill raises no error and
  produces no artifact — just a worse session, which is design/59's failure
  mode exactly. Drive it from a recorded payload rather than asserting the
  routing in prose. For `nikon-pfs`, start with an operator request to engage
  PFS and replay a `get_focus_lock_state` result whose `device` is
  `TIPFSStatus`, and another whose `device` is `PFS`; assert the order
  `get_focus_lock_state` → `load_skill(name="nikon-pfs")` →
  `set_focus_lock(enabled=true)`. This deliberately exercises the hazardous
  direct-engagement shortcut rather than an autofocus request that was already
  likely to visit the state tool.
  Both device values are measured on different Nikon rigs. A Ti reported
  *"`Core.Focus` reported `TIZDrive` and `Core.AutoFocus` reported
  `TIPFSStatus`"* (`design/34-nikon-pfs-tizdrive-findings.md:48-49`). A Nikon
  Ti2-E / Andor Dragonfly reported lock device `PFS`, property `PFS in Range`,
  and in-range value `In Range`
  (`design/56-the-focus-metric-need-not-be-an-image.md:785-797`). Cite both in
  the fixtures: together they preserve cross-rig capability and prevent routing
  from collapsing onto one literal device name;
- the Nikon routing fixture also needs two negative limbs, and **the first one
  must name its device string** or it is the outcome-shaped step the row above
  avoids. Use a real focus lock that is not PFS — `CRISP` (design/06 maps an
  ASI `CRISP`/`CRISP State` focus device) — and replay the now-consistent EMU
  result containing `"device": "CRISP"`, so the limb discriminates *PFS from
  other focus locks* rather than merely *focus lock from no focus lock*, which
  a non-lock device would pass for the wrong reason. It must not load
  `nikon-pfs`. Second, the no-device `{"engaged": null, "reason": ...}` result
  must neither load it nor ask the operator an unnecessary identification
  question;
- the existing substantive content assertions pass unchanged against the
  Markdown; assertions coupled to removed tool names are updated to the new
  `load_skill` interface.

Assert routing and content invariants, not whole-prompt wording.

## Rejected alternatives

**Move the strings to Markdown and keep five tools.** Gets the authoring win,
keeps knowledge loaders as peers of hardware actions, and still adds a Python
schema per future skill.

**Put the documentation in the system prompt.** Pays 30k tokens on every rig to
avoid a conditional round trip. Specifically wrong for optional htSMLM and Nikon
behavior.

**Make every instruction a skill.** Authorization and dose rules must be present
before skill selection and cannot depend on the model choosing to load them.
Tool-local contracts — the meaning of `focus_metric_valid` in
`snap_and_analyze`, say — belong in the parameter description, next to the call.

**A full catalog/registry/predicate subsystem (the first draft of this doc).**
Rejected on measurement: ~1,650 tokens of win against a second parallel tool
system, a trusted-predicate layer guarding documents, and a six-stage migration
with a deprecation cycle. CLAUDE.md's rule applies directly — no extra
registries or guard passes unless the existing architecture cannot express the
behavior. It can.

## Consequences

Microscopy guidance is Markdown in an obvious place; code that touches the
microscope is in another. Ordinary rigs stop carrying Nikon and htSMLM
procedure after the staged confirmation. The initial merged block saves about
1,050 tokens of cached static context while retaining the Nikon paragraph; the
Nikon follow-up was predicted to bring the net change to about −1,650 tokens.
**Measured:** 61a came
in at −3,300 chars / ~−825 tokens against that ~1,050 prediction, and 61b spent
**+115 chars** of prompt (the ordering invariant and one word at `agent.py:118`;
the anchors are schema text, already paid for), taking `SYSTEM_PROMPT` from 32,088
chars before 61a to 31,326 after 61b. So the shipped net is about **−760 chars of
prompt plus −2,600 chars of schema, ~−840 tokens** — with the Nikon paragraph
(2,353 chars) still carried, exactly as designed. 61c then removed 2,355 prompt
chars including the paragraph's bounding newlines (31,326 → 28,971), about −589
tokens by the design's chars/4 convention. Across all three blocks the measured
net is **−3,115 prompt chars plus −2,600 schema chars, about −1,429 tokens**,
221 tokens short of the ~−1,650 estimate. A specialized
workflow pays one conditional round trip when its skill is first needed. Adding
a skill becomes adding a file. Authorization, guards, bounds and confirmation
enforcement are unchanged: no authority is gained by reading a document. Nikon
operational-safety guidance becomes conditionally loaded only after its routing
invariant and positive behavioral test have been confirmed on the Ti; if they
do not reliably load the skill before action, the Nikon procedure stays in the
core prompt.

---

## Blocks

design/61 owns its own blocks, checklist and run ledger, as design/58, design/59
and design/60 do. It is not a design/35 row.

The "Migration" section above sizes this as **one block plus the Nikon
follow-up**. The coordinator is splitting the first of those in two. This is a
sizing judgement, not a disagreement with the design: step 1–3 is already five
document migrations, a new module, a five-for-one tool swap, a package-data
pattern with a wheel test that must isolate itself from an editable install, a
system-prompt edit and ~57 test references across four files. Step 4's Nikon
anchors are a separate, separately-gateable mechanism whose own test — the
recorded-payload routing fixture with four limbs — is the subtlest thing in the
document. Reviewing them in one diff makes any regression unlocalizable, which
is the same reason this design refuses the `tools.py` split.

Splitting costs nothing the design cares about: it requires only that **both**
blocks be on `main` before the Ti confirmation is requested, because the
confirmation observes the anchors and the skill together. Step 5 is unchanged.

### 61a — skills are files, and one `load_skill` replaces five tools

Migration steps 1–3. The catalog, the loader, the tool, the packaging, the
deletion of the five doc tools and the SMLM/EMU paragraphs. The Nikon paragraph
stays untouched and is not this block's business.

### 61b — the Nikon anchors, and the route they create

Migration step 4, plus the recorded-payload routing fixture. Code changes, not
wording: three schema descriptions, one word at `agent.py:118`, the ordering
invariant in the core prompt, and the `device` key on the EMU branch of
`get_focus_lock_state`. Ships with the Nikon paragraph still redundantly in the
core prompt, exactly as the design requires.

### 61c — the Nikon paragraph leaves the core prompt

Migration step 5's follow-up. **Conditional and unscheduled**: it does not start
until the carried-forward Ti row closes *positive*. A negative confirmation
sends the work back to 61b's route and this block does not run.

## Implementation checklist

### Block 61a — skills are files, and one `load_skill` replaces five tools

Items:

1. **Move the four constants to `microclaw/skills/<name>/SKILL.md`** and
   relocate `dna_paint_protocol.md` into `skills/dna-paint/SKILL.md`. Names and
   sources are the design's Layout table: `hook-authoring` (`hook_docs.py`),
   `smlm` (`smlm_docs.py`), `dna-paint` (the existing Markdown), `optical-paths`
   (`optics_docs.py`), `htsmlm` (`htsmlm_docs.py`). `nikon-pfs/SKILL.md` is
   authored from the `SYSTEM_PROMPT` Nikon paragraph (2,353 chars stripped of
   its bounding newline, 2,355 as a raw prompt segment; measured 2026-08-29,
   and the design's 2,353 is the stripped figure).
   Delete `hook_docs.py`, `smlm_docs.py`, `optics_docs.py`,
   `htsmlm_docs.py` and `dna_paint_docs.py` outright — no shim, no re-export.
2. **Body equivalence is checked once, in review, and is not a shipped test.**
   Normalize only the single leading/trailing newline the triple-quoted literal
   introduced; frontmatter is expected to differ. The implementer reports the
   diff for each of the four; the coordinator reproduces it. DNA-PAINT's body is
   byte-identical apart from added frontmatter.
3. **`microclaw/skills.py`** — catalog plus loader, read through
   `importlib.resources` exactly as `dna_paint_docs.load_reference` did (the
   docstring explaining why is worth carrying over into the new module). The
   catalog is built from YAML frontmatter at import time.
4. **`load_skill(ctrl, guard, name)` decorated `@emits_nothing`**, one schema in
   `TOOLS`, one entry in `TOOL_REGISTRY`. No `SKILL_SCHEMAS`, no second
   registry. Delete the five `get_*_documentation` functions, their five schemas
   and their five registry entries in the same commit. Watch the schema
   interleaving: `tools_schema.py` runs the five doc schemas across ~1960–2035
   with `check_emu_installed` (2012) and `get_emu_configuration` (2039) **in the
   middle of them**, and both of those stay.
5. **Startup fails loudly** on malformed frontmatter, a directory/name mismatch,
   duplicate names, **and an empty catalog**. The empty case is the likely one
   and is the only one a bad package-data glob produces, so it does not get to
   share a code path with "no skills were broken".
6. **The catalog renders into `SYSTEM_PROMPT`** as name + one-line description,
   generated — not hand-copied. Delete the SMLM (897 chars) and EMU/htSMLM (802
   chars) paragraphs and replace them with the catalog plus one routing rule:
   load the relevant skill before running its specialized workflow. **Leave the
   Nikon paragraph (2,353 chars stripped) exactly as it is.**
7. **`check_emu_installed`'s schema description** carries the conditional
   htSMLM rule — load `htsmlm` after positive plugin/configuration detection, or
   when the operator identifies the system or workflow as htSMLM or EMU, never
   merely because the catalog lists it. It goes in the *schema*, not in the
   skill's frontmatter: a catalog `description` is one discriminating line, and
   policy written there is the paragraph moved rather than removed. Same for
   `get_emu_configuration`'s description where it names a removed tool.
8. **Rewrite every prose reference to a removed tool name** to the corresponding
   `load_skill(name=...)` call: `smlm_docs` content (7 mentions), the migrated
   `dna-paint` body's line 3, `agent.py:357,454,519`, `tools.py:3352`.
9. **`pyproject.toml` package data gains `skills/*/*.md`.** setuptools'
   recursive-glob handling is the footgun the wheel test below exists for; do
   not assume the pattern works because the source tree does.

Tests:

- every skill has valid frontmatter, its directory name matches `name`, and
  every `description` is a single line;
- skill identifiers are unique and collide with no name in `TOOL_REGISTRY`
  (`load_skill` itself stays an ordinary registry entry);
- malformed metadata, a name mismatch, duplicates **and a nonempty-catalog
  assertion** each fail startup. The empty-catalog assertion sits with the
  others;
- `load_skill` refuses an unknown name and any path-shaped argument, and the
  refusal names the catalog;
- `load_skill` is decorated: an exported session that loaded a skill compiles
  and contains no `NOT EMITTED`;
- `test_schema_parity.py:18` stays an **equality** — `set(TOOL_REGISTRY) ==
  set(_SCHEMA_BY_NAME)`. If a change makes it want to be a subset, the change is
  wrong;
- **the wheel test.** Build the wheel, install it into a throwaway virtualenv,
  and inspect it **with that venv's interpreter from a working directory outside
  the repository**. Assert its catalog names equal the nonempty catalog names
  discovered from the source tree by a **plain filesystem glob over
  `microclaw/skills/*/SKILL.md`** — deliberately a different mechanism from the
  `importlib.resources` query used on the wheel side. Both sides using
  `resources.files("microclaw")` is the failure this test exists to prevent:
  this repo's dev setup is `pip install -e .`, whose `__editable__.microclaw-*.pth`
  maps `microclaw` back to the checkout from any cwd and any `PYTHONPATH`, so a
  subprocess alone is not isolation and the test would compare the tree to
  itself. Say in the test which side is which and why. Never mutate the ambient
  environment;
- **the ~57 existing content assertions keep passing through the loader.** They
  pin design/24, /26 and /27 into the hook reference — "NEVER return None",
  "STILL FIRES THE CAMERA", "SILENT NO-OP" — and are not incidental. Measured
  2026-08-29: `test_survey_runner.py` 28, `test_tools.py` 7 (+12 tool-name
  references), `test_optics_docs.py` 3 (+4), `test_hook_manager.py` 2. Preserve
  every substantive content assertion. Assertions coupled to a **removed tool
  name** change with the interface — including `test_tools.py:5223`'s
  `count(...) >= 2`, which must not keep teaching the model a tool that no
  longer exists;
- **the negative context assertion**: a session with no SMLM or htSMLM trigger
  has neither skill body in its system blocks, and the catalog is present. The
  Nikon core paragraph is still there — assert its presence, deliberately, so
  61c has something to flip;
- **and the converse, which is the one that matters**: a session whose opening
  *does* trigger a skill loads it before acting. The negative above passes on an
  agent that never loads anything. Drive it from a recorded payload, not from
  prose. 61a's positive limb is SMLM: an operator-worded dSTORM request must
  reach `load_skill(name="smlm")` before it proposes acquisition parameters.
  (The Nikon limbs are 61b's.)

Coordinator notes for review:

- Watch for a second registry appearing under another name — a `SKILLS` dict
  that the schema is generated from is the parallel plumbing layer the design
  rejects, whatever it is called.
- Watch for `references/` subdirectories. Five of six bodies are under 500
  lines; the design forbids the split until a body is actually too big.
- **Verify the body equivalence yourself.** The design makes it a review-time
  check precisely because nothing in the suite will do it afterwards.

> **Three things 61a's gate cost, which 61b's gate should not repeat.**
>
> 1. **Derive paths, never assume them.** 61a's runbook hardcoded
>    `$repo = "$HOME\Code\microclaw"`; the demo machine's checkout is on `D:`,
>    so the operator reasonably skipped that line and the *next* step expanded
>    an unset `$repo` into `C:\design\...`, failing with an error that named
>    nothing relevant. Use `git rev-parse --show-toplevel`, echo every derived
>    path before use, guard each step's inputs, and put `Set-StrictMode -Version
>    Latest` at the top so an unset variable is a named error rather than an
>    empty expansion.
> 2. **A gate's own environment is a fake, and it encodes assumptions too.**
>    61a's selftest installed each tree with `--no-deps` into an isolated venv,
>    so every limb died on a missing numpy and *both* trees "failed" — which
>    would have reported a discrimination the gate had never demonstrated. It
>    now borrows ambient dependencies, installs only microclaw, asserts the gate
>    imported the tree it installed, and requires `main` to fail **for the right
>    reason** rather than merely to fail. Check the reason, not the exit code.
> 3. **A human limb needs a NOT EXERCISED branch.** 61a's gate *program* had
>    one; its prose limbs offered only PASS/FAIL, so an operator following the
>    runbook literally would have recorded FAIL for a limb that measured
>    nothing. A rubric that cannot say "this did not run" reports nulls as
>    results.

### Block 61b — the Nikon anchors, and the route they create

Items:

1. **One sentence into three existing schema descriptions** —
   `get_focus_lock_state`, `set_focus_lock`, `run_autofocus`: on a rig with a
   hardware focus lock of this kind, load `nikon-pfs` before engaging or
   adjusting it. `set_focus_lock` carries it because it performs the hazardous
   act and an agent that goes straight there must still meet the rule. These are
   capability anchors, not incidental wording — checked across all 85 schemas,
   **zero mention PFS, TIPFS or Nikon** today, so `nikon-pfs` has no anchor at
   all until this lands.
2. **`agent.py:118` gains one word.** The parenthetical reads "(stage, channel,
   exposure)"; it becomes "(stage, channel, exposure, focus lock)". Focus lock
   already has a dedicated tool. Add the word rather than writing a parallel
   rule.
3. **One vendor-neutral ordering invariant in the core prompt**: before any
   operation that engages or adjusts a hardware focus lock, call
   `get_focus_lock_state` first. It is an **ordering** rule, not a second
   prohibition on the raw setter, and it makes the state tool the single
   discovery point.
4. **The `device` key on the EMU branch of `get_focus_lock_state`**
   (`tools.py:9094–9099`, which returns `engaged`, `raw_value`, `property`,
   `qpd` and omits the device). Return `"device": lock["device"]`. **Add the
   key; do not recover the device by splitting `property` on the dot** — the
   branch flattens the identity into `"<device>.<property>"`, so
   `property.split(".")[0]` is the shortcut an implementer reaches for, and
   parsing an identity out of a display string is the fragility this design
   forbids one sentence later.
5. **Identification is by the returned `device` value and nothing else.** Do not
   substring-scan properties for `"PFS"`: a status property may contain it as
   readily as the lock device, which is the identity confusion design/59 fixed
   in `_PORT_LABEL_WORDS`. Where no autofocus device is configured
   (`tools.py:9088` returns `{"engaged": None, "reason": ...}` with **no
   `device` key at all**) the invariant is a silent no-op. **Do not add an
   "unknown → ask the operator" branch**, which would turn every ordinary rig
   into a question.
6. **Coordinator-added: decorate `get_focus_lock_state` `@emits_nothing`.** It
   is undecorated today and it sits on the exact route this block creates —
   `get_focus_lock_state` → `load_skill(name="nikon-pfs")` →
   `set_focus_lock(enabled=true)` — so the first PFS session that exports gets
   `raise RuntimeError` planted three lines in. That is the 43h / 47 / 52a shape
   for the fourth time, and 52a's version was likewise a tool sitting on its own
   block's gate path. The tool only reads (`get_auto_focus_device`,
   `is_continuous_focus_enabled`, `get_property`, `_read_qpd`), so
   `@emits_nothing` is the correct decoration, not `@emits`. **`run_autofocus`
   and `set_focus_lock` are already decorated** — checked, not assumed.

Tests — the recorded-payload routing fixture, four limbs. Drive them from
replayed payloads; **do not assert the routing in prose**. Skipping a skill
raises no error and produces no artifact — just a worse session, which is
design/59's failure mode exactly.

**Coordinator, before the block is assigned: what a suite test of this can and
cannot be.** 61a's routing test was vacuous, and one mutation proved it — its
"recorded payload" was a scripted mock whose first response *was*
`load_skill(name="smlm")`, so the assertion held with the catalog and the
routing rule deleted from `SYSTEM_PROMPT` outright. **A scripted mock cannot
measure a routing choice, because it supplies the choice.** Writing the four
limbs that way again produces four tests that cannot fail, and they would be the
half of this block that matters.

So the four limbs are split by what each half can actually observe, and each
half is named for what it is:

- **In the suite, at $0: the discriminator.** Each limb replays a rig-shaped
  payload through the *real* `get_focus_lock_state` and asserts the identity the
  routing depends on — `device` equal to `TIPFSStatus`, to `PFS`, to `CRISP`, and
  absent for the no-device rig. These fail when item 4 or item 5 is wrong, which
  is the product content of this block. **Bridge-shaped fakes** (59a): Core
  collections are `size()`/`get(i)` vectors whose `__iter__` raises. The `PFS`
  limb carries the Dragonfly's `PFS Status` *and* `PFS in Range` properties
  beside a device that is not named `PFS`-only, and the `CRISP` limb carries a
  status property containing `PFS`, so a substring scan over properties —
  the shortcut item 5 forbids — turns both red.
- **Separately, as presence regressions:** the three amended schema
  descriptions name `nikon-pfs`, `agent.py:118`'s parenthetical carries
  `focus lock`, and the ordering invariant is in the core prompt. These are weak
  by construction — deleting the sentence turns them red and that is all they
  claim. Do not label them routing tests.
- **The routing choice itself is a live-model observation and is not bought
  here.** Its negative direction is gate limb H2 on the demo machine, in a
  session the operator was going to drive anyway; its positive direction is the
  Ti row R1, post-merge. `design/61-skill-routing-spike.py` stays unrun. No
  paid measurement is proposed for this block.

- **Positive, `TIPFSStatus`.** Operator request to engage PFS; replay a
  `get_focus_lock_state` result whose `device` is `TIPFSStatus`. Assert the
  order `get_focus_lock_state` → `load_skill(name="nikon-pfs")` →
  `set_focus_lock(enabled=true)`. Start from the **hazardous direct-engagement
  shortcut**, not an autofocus request that was already likely to visit the
  state tool.
- **Positive, `PFS`.** Same, with `device` of `PFS`. Both device strings are
  measured on different Nikon rigs — a Ti reported `Core.AutoFocus` =
  `TIPFSStatus` (`design/34-nikon-pfs-tizdrive-findings.md:48-49`); a Ti2-E /
  Andor Dragonfly reported lock device `PFS`, property `PFS in Range`, in-range
  value `In Range` (`design/56-the-focus-metric-need-not-be-an-image.md:785-797`).
  Cite both in the fixtures. Together they prevent routing from collapsing onto
  one literal device name.
- **Negative, and it must name its device string.** Use a real focus lock that
  is not PFS — `CRISP` (design/06 maps an ASI `CRISP`/`CRISP State` focus
  device) — replayed through the now-consistent EMU result carrying
  `"device": "CRISP"`. This discriminates *PFS from other focus locks*; a
  non-lock device would pass for the wrong reason and is the outcome-shaped step
  this workflow keeps paying for. It must not load `nikon-pfs`.
- **Negative, no device.** The `{"engaged": null, "reason": ...}` result must
  neither load `nikon-pfs` **nor ask the operator an unnecessary identification
  question**. Assert both halves; the second is the one that regresses silently.
- Regression: `test_schema_parity.py` still equality; the three amended schemas
  still match their signatures.
- Regression: an exported session that called `get_focus_lock_state` compiles
  and contains no `NOT EMITTED` (item 6).

Coordinator notes for review:

- The four limbs are the block. An implementation that lands the anchors and
  hand-writes a prose assertion instead of replaying payloads has delivered the
  half that cannot fail.
- Check that the negative limbs *can* fail: mutate the routing rule and confirm
  the CRISP limb goes red. A limb that cannot fail is not a criterion — 58a's
  opt-out limb passed three rounds for that reason.

### Block 61c — the Nikon paragraph leaves the core prompt (conditional)

**Do not start this block until the Ti row in the carried-forward register
closes positive.** On a negative confirmation, retain the paragraph, fix 61b's
route, and repeat the confirmation. No released version first loses the guidance
it is meant to preserve.

Items:

1. Delete the Nikon paragraph (2,353 chars stripped) from `SYSTEM_PROMPT`. `nikon-pfs`
   already carries it verbatim from 61a.
2. Flip 61a's deliberate presence assertion: a session with no Nikon trigger has
   no Nikon skill body **and no Nikon paragraph**; the catalog and the anchors
   remain.
3. Record the measured net token change against the design's ~1,650 estimate.

## Gates

### Block 61a — demo machine

Two artifacts, because the limbs are of two kinds and CLAUDE.md's rule is that
computational limbs ship as a program:

- **`design/61-block61a-demo-gate.py`** — every limb that only computes. Reports
  each limb **independently** (one failure must not hide the rest behind a
  cascade), writes **its own log** (PowerShell 5.1's `Start-Transcript` does not
  capture a native child's stdout), exits nonzero, and reports **NOT EXERCISED**
  for a limb whose mechanism could not run. NOT EXERCISED is never a pass.
  **It must not require a `workspace_dir` or any configuration the product does
  not require** — 60b's gate reported six limbs NOT EXERCISED for exactly that
  and made the operator edit a production safety config.
- **`design/61-block61a-demo-gate.md`** — only the steps a human performs and
  judges: driving a session and reading what the agent did. Every step is a
  literal command or a verbatim prompt. No placeholders in a literal command;
  52c's export grep shipped as `Select-String -Pattern "<t2>", "<t3>"`, was run
  verbatim, matched nothing, and "passed".
- **`design/61-block61a-gate-selftest.py`** — bridge-shaped, run on **both**
  trees so its failure discriminates. Collections must be `size()`/`get(i)`
  vectors whose `__iter__` raises; a `MagicMock` hands back Python-friendly
  objects and would have missed 59a's `TypeError: 'mmcorej_StrVector' object is
  not iterable`. **Write the fake from the dependency's source, not from the
  caller** — 60b's gate globbed `NDTiffStack*.tif` and its fake obligingly wrote
  that name, and a perfect 4 GiB crossing came back FAIL.

Limbs:

- **G1 (program) — the real install route ships the skills.** Install the branch
  the way the demo machine actually installs, then `load_skill(name="hook-authoring")`
  returns a nonempty body. This is the limb the wheel test cannot replace: the
  wheel test proves the pattern, this proves the installer.
- **G2 (program) — refusals.** An unknown name and a path-shaped name are both
  refused, and the refusal names the catalog.
- **G3 (program) — the catalog is in the prompt and the bodies are not.** SMLM
  and htSMLM bodies absent, catalog present, **Nikon paragraph still present**.
- **G4 (runbook, reach) — do not name the tool.** An operator-worded dSTORM
  request. Does the session call `load_skill(name="smlm")` before it proposes
  acquisition parameters? Ask unprompted first; if it does not get there, record
  that as a finding and **then** ask directly, so one round yields both answers
  instead of neither.
- **G5 (runbook, mechanism) — the export.** The G4 session runs a 2-frame
  timelapse and then `export_session_script`. The script compiles and contains
  no `NOT EMITTED`. **A fresh session emits a 13-line stub**, so the run must
  come before the export.

### Block 61b — replay first, demo machine second

The Ti is unreachable pre-merge: it is in daily use by a collaborator who
receives code through the update route, so anything on `main` can be exercised
on it and anything on a block branch effectively cannot. Step 5 of the block
workflow cannot cover the Nikon limb, and that is why 61b ships with the Nikon
paragraph intact.

- **H1 (off-rig) — dry-run the runbook's prompts against the fixture payloads.**
  Cheap here, and worth it: the payloads already exist as 61b's required tests,
  so the harness is not new work. 59b lost three demo rounds to prompt defects
  and zero to product defects — but the same block then built a replay harness
  that cost more than the session it replaced, so this is a judgement call each
  time, and it goes the other way here only because the fixtures are free.
- **H2 (program, demo machine) — the ordinary rig is unharmed.** On a demo
  config with no autofocus device, a driven session that engages focus must
  **not** load `nikon-pfs` and must **not** ask an identification question. If
  the demo config *does* configure an autofocus device, report the `device`
  value and mark the no-device limb NOT EXERCISED rather than passing it.
- **H3 (program) — the `device` key.** Where an EMU focus lock is present (M5),
  `get_focus_lock_state` returns `device` and it equals the EMU map's. NOT
  EXERCISED on a rig without one.
- **H4 (program) — the export.** A session that called `get_focus_lock_state`
  exports a script with no `NOT EMITTED` (item 6's regression, on real
  hardware).

### Post-merge — the Ti confirmation (carried forward, not a gate step)

Once **both** 61a and 61b are on `main`: a Ti session in which the operator asks
to engage PFS, and the record shows `get_focus_lock_state` →
`load_skill(name="nikon-pfs")` → the requested focus-lock action. Ask for the
rig's normal work, not for a script — `design/40-pfs-five-sessions.md` cost five
sessions to learn that. A positive result authorizes 61c. A negative result does
not.

**This observation is asymmetric, and the reason is that the block it confirms
has not shipped yet.** `nikon-pfs/SKILL.md` is byte-identical to the paragraph
still in `SYSTEM_PROMPT` — 2,353 chars both sides, measured 2026-08-29 — so on
today's released build the call returns text the agent already has verbatim.
There is no information gain from making it. Therefore:

- **Positive is strong**, and stronger than a bare pass: an agent that loads the
  skill when the load is *redundant* will certainly load it when the skill is
  the only source. That survives the difference between the two conditions and
  is why one Ti session is enough to authorize 61c.
- **Negative is uninterpretable, and is not a defect report.** Declining to
  fetch a document you are already carrying is correct behaviour on this build.
  Record it as *unmeasured* and leave 61c unscheduled; do **not** go looking for
  a broken route, and do not "fix and repeat" as an earlier draft of this
  section said. That instruction was the 61a G4 shape — a failure condition
  satisfiable by the better answer.

The structural point, worth stating because it is easy to miss: R1 measures the
route under condition A (paragraph present) in order to license condition B
(paragraph absent), and A and B differ in exactly the variable that matters —
whether the skill is the only source of the guidance. The asymmetry above is
what makes a positive still worth collecting; nothing makes a negative worth
acting on.

**If the Ti does come back negative and 61c still looks worth having**, the
question 61c actually turns on is answerable off-rig: replay a Ti
`get_focus_lock_state` payload (`TIPFSStatus`) against a build with the
paragraph *removed*, and measure whether the model reaches
`load_skill(name="nikon-pfs")` before acting.
`design/61-skill-routing-spike.py` is already shaped for it and stays unrun
until then. Note the burden of proof is the opposite of 61a's declined A/B: that
one would have licensed *restoring* wording that had worked for months, which
needs no measurement, whereas this one licenses *removing* operational safety
guidance, which is a new mechanism and does. Price it against published rates at
the time, not from memory — 61a's estimate was wrong by 3x — and validate the
scoring on one sample before buying a batch.

### Post-merge design gate (step 10, all blocks)

- Reconcile the design's estimates to what was measured: the "~1,050 tokens
  saved at 61a, ~1,650 after the Nikon follow-up" line in Consequences becomes a
  measured number.
- Update `CLAUDE.md`'s undecorated-tool register — see the row below.
- Tick the carried-forward rows this design closes.

## What the demo machine measured for 61a, 2026-08-29

Round 1. **G1, G2, G3, G5 PASS. G4 NOT EXERCISED** — and its limb is a gate
defect, not a product one.

The three programmatic limbs passed against the *installed* build, and their
numbers agree byte-for-byte with the coordinator's macOS selftest run: six
skills at `dna-paint` 19,829 / `hook-authoring` 34,025 / `htsmlm` 7,149 /
`nikon-pfs` 2,470 / `optical-paths` 3,572 / `smlm` 23,056 chars, prompt 31,070
chars. `install.bat`'s artifact carries `skills/*/SKILL.md`, so the package-data
pattern works through the route the machine actually uses, which is what the
gate existed to prove.

**G5 passed on real artifacts.** The session ran a 2-frame timelapse and
exported; the emitted script compiles, imports nothing from `microclaw`, and
renders `load_skill` as `# No hardware-routine effect.` with no `NOT EMITTED`
anywhere. A session that loaded a skill exports a working standalone script.

**G4 measured nothing about routing, and the prompt is why.** Asked the
unprompted opening — *"I need to run dSTORM on this sample. What acquisition
parameters should I use?"* — the agent called `get_system_state`, read `DCam`
and the rest of the demo configuration, and spent its whole turn telling the
operator that this rig cannot do dSTORM at all. It never loaded `smlm`. It also
never proposed a parameter. So neither the PASS condition nor the FAIL condition
in the runbook was met: the mechanism under test — does a triggered session load
the skill *before acting* — was never put to the agent, because the agent never
reached the point of planning an acquisition.

That is the design/59 lesson again, and this time the coordinator wrote the rule
into the checklist and then reasoned past it: *dry-run a prompt when the gate is
long, repeated, or the operator is not standing at the rig; otherwise ask for the
session.* The judgement went the wrong way here, and cheaply, because
`design/61-skill-routing-spike.py` had already been authored and replays exactly
this opening off-rig. **A gate prompt that asks for dSTORM on a rig whose camera
emits synthetic bands is answerable without the thing under test** — and the
better answer, the one a good microscopy agent gives, skips it.

**The rubric was also wrong, in the same shape.** The runbook offered the human
limbs only PASS or FAIL. The gate *program* has a `NotExercised` state; the
prose limbs did not, so an operator following the runbook literally would have
had to record FAIL for a limb that measured nothing. A rubric that cannot say
"this did not run" reports nulls as results.

**The direct fallback worked.** Asked *"Load the smlm skill, then tell me what
acquisition parameters to use for dSTORM"*, the agent called
`load_skill(name="smlm")` immediately and answered from the returned reference.
The mechanism is fine; what is unmeasured is the reach.

### A hypothesis this raises, which one session cannot settle

The paragraph 61a deleted said: *"When the user asks to do SMLM,
super-resolution, dSTORM, PALM, PAINT, DNA-PAINT, or single-molecule
localization, call get_smlm_documentation **first**."* That is trigger-worded and
enumerates the exact words an operator uses. What replaced it is *"Load the
relevant skill before running its specialized workflow"*, which fires on
**running** a workflow, not on **being asked about** one. Under the new rule the
observed session is arguably compliant: it ran nothing.

So the migration may have narrowed the routing trigger, and that would be a real
cost of the change rather than a bug in it. **It is a hypothesis, not a finding.**
n=1, on the one rig where the confound is strongest, and design/59 measured two
runs of the *same* wording at 5/8 then 15/16 — a single session cannot separate
a wording effect from noise. `design/61-skill-routing-spike.py` exists to size
it; the design's own stance is that a measurement, not an intuition, is what buys
a change here. **No product change on this evidence.**

### What was done about it, and what was deliberately not

The routing rule got its ordering half back, in 141 characters: *"...When the
user asks about a task a skill covers, load that skill FIRST, before answering
— a caveat about the rig is not a reason to skip it."* The catalog's own
descriptions already carry the trigger vocabulary — `smlm` names dSTORM, PALM,
PAINT and DNA-PAINT — so only the framing had to return, not the word list.
Net context change for 61a is **−3,300 chars, about −825 tokens**, against the
design's predicted ~1,050.

**No measurement was bought for that change, on purpose.** A two-arm A/B was
designed, priced and declined. The reasoning is worth recording because the
coordinator got it wrong first:

- The coordinator proposed 2 arms × 16 samples and priced it at **$35**. That
  number was **wrong by 3×** — it used $15/$75 per Mtok from memory where Opus
  4.8 is **$5/$25**. Real cost ~$11. *Never price a model from memory.*
- The operator's objection was the sharper one: this is a **rearrangement of
  code that had worked for months**, and a normal spike costs a few dollars.
- Stating the experiment plainly is what settled it. Both arms hold the
  operator prompt and the rig payload fixed and vary **one sentence of our own
  system prompt** — arm B being approximately the wording 61a had deleted. So
  its best case was *"put back what you took out"*, which is the action either
  way. **The burden of proof was backwards**: the migration removed working
  wording, and restoring one reversible sentence needs no licence. A
  measurement buys a *new* mechanism, not the restoration of an old one.

Evidence actually held: **three observations of the shipped wording — the demo
session and two smoke samples — none of which loaded the skill.** One opening,
n=3. That is a direction, not a rate, and it is not reported as one.

`design/61-skill-routing-spike.py` stays in the tree **unrun**, with the
recorded payload, as the instrument if this recurs. Its scoring was rewritten
before it was shelved, and that rewrite is the durable lesson: the original
verdict ran a regex over the assistant's prose, and a **one-sample validation
run** showed it scoring `PROPOSED_FIRST` on a message that was *refusing* to
give numbers — it had matched a stray "TIRF" and "20 ms" further down. Sixteen
samples of that would have looked like data. The metric is now binary — was
`load_skill(smlm)` called — and needs no text classification at all. **Validate
an instrument on one sample before buying sixteen.**

## Carried-forward register

| # | row | owner | state |
| --- | --- | --- | --- |
| R1 | **Ti confirmation of the `nikon-pfs` route.** Closed positive from the 2026-08-30 normal-work session `20260830_160718_176677_microclaw_history.jsonl` (sha256 `557235fcfe0adcfa0221c5395d255d15634fd7eec1b59c0e810d70dad1248596`): after the operator asked "hey, focus for me", `get_system_state` returned focus device `TIPFSStatus`; the agent then called `load_skill(name="nikon-pfs")` before its first focus action (`run_autofocus`). The state came through `get_system_state.focus`, not the narrower `get_focus_lock_state` call named in the planned observation, but the positive-informative question is settled: the model fetched the byte-identical skill while the paragraph was still present. This authorizes 61c. The same history separately shows an ordering defect after the failed sweeps — `set_focus_lock(enabled=true)` preceded a later explicit `get_focus_lock_state` — which is not evidence against the skill route and is not silently folded into 61c. | operator, post-merge | closed positive |
| R2 | **`CLAUDE.md` says "Eleven tools are still undecorated" (measured 2026-08-17). It is twelve, measured 2026-08-29** over `TOOL_REGISTRY`: `calibrate_snr_threshold`, `calibrate_stage_to_camera`, `center_feature`, `export_dataset_as_tiff`, `find_features`, `get_focus_lock_state`, `run_mda`, `run_multiposition_with_autofocus`, `set_emu_laser_power_percentage`, `shutter_declared_illumination`, `snap_to_album`, `verify_emu_laser_power_calibration`. 61b closed `get_focus_lock_state`, leaving **eleven**, measured 2026-08-29 over `TOOL_REGISTRY` (81 tools): `calibrate_snr_threshold`, `calibrate_stage_to_camera`, `center_feature`, `export_dataset_as_tiff`, `find_features`, `run_mda`, `run_multiposition_with_autofocus`, `set_emu_laser_power_percentage`, `shutter_declared_illumination`, `snap_to_album`, `verify_emu_laser_power_calibration`. That is the same *number* `CLAUDE.md` has carried since 2026-08-17 but not the same *membership*, so the date must move with it. Note for whoever measures next: the three attributes are `_microclaw_emitter`, `_microclaw_emits_nothing` and `_microclaw_refusal_reason` — a probe that guesses `_microclaw_refuses` counts `build_stage_coordinate_mosaic` as undecorated and reports twelve. | coordinator | closed |
| R3 | **`load_skill` is one more tool in an 85-schema list that is 74% of static context.** The design says plainly that the 74% row is where a context project would go and that this is not that project. Recorded so the measurement is not lost, not scheduled. | — | recorded |

## Run ledger

| block | branch | start | implementation | gate | merge |
| --- | --- | --- | --- | --- | --- |
| 61a | `design61/skills-are-files` | `64cb63f` (2026-08-29), worktree `../microclaw-61a` | `26cae61` (1 Codex start + 1 revision turn **killed mid-flight by a Codex account usage limit** with its edits landed — preserved as `40aaa8a` and committed unreviewed per the workflow, then verified by the coordinator: R2/R3 watched red on the pre-fix tree, R1 mutation-checked. 4 findings returned; R1 was a **vacuous routing test** proved by deleting the catalog from the prompt and watching it still pass. Coordinator fix `26cae61` for the total-package-data-omission path. Coordinator suite 2483/99/0) | round 1 demo 2026-08-29 after `85d63ec`: **G1/G2/G3/G5 PASS, G4 NOT EXERCISED**. Installed-build limbs agree byte-for-byte with the coordinator's selftest (6 skills, prompt 31,070 chars); emitted script compiles with `load_skill` as `# No hardware-routine effect.` and no `NOT EMITTED`. G4's prompt asked for dSTORM on the demo camera, so the agent correctly answered *this rig cannot do dSTORM* and never reached a routing decision — **gate defect, no product defect**. A runbook path bug (`$repo` hardcoded to the wrong drive) cost the operator one round before this. G4's cause fixed at `daa0053` by restoring the routing rule's ordering half (141 chars); the two-arm A/B was priced (~$11, after a 3x mispricing) and **declined as backwards burden of proof** — see the section above. Net context −3,300 chars / ~−825 tokens. Suite 2484/99/0. | `c18b15a` merged 2026-08-29, branch deleted locally and on `origin`; worktree removed. Design gate at step 10 below. |
| 61b | `design61/nikon-anchors` | `fe82b05` (2026-08-29), worktree `../microclaw-61b` | `b5535c7` (1 Codex start turn, no revision turn needed). Runner's report was honest about the limbs that pass pre-change; it committed that report into the repo root, which the coordinator dropped (preserved in scratch). Coordinator reproduced both pre-change failures independently — `KeyError: 'device'` on the CRISP limb, `NOT EMITTED: get_focus_lock_state` on the export. **One finding, and the suite structurally could not catch it**: the three anchors shipped as "on a rig with this kind of hardware lock", whose antecedent was a *generic* hardware focus lock — so read plainly they told an agent on any focus-lock rig, this demo machine included, to load the Nikon skill. The discriminator fixture asserts what the tool returns, not how a model reads a description, and the presence test only asked for the string `nikon-pfs`, which the vague form contains. Fixed at `b5535c7`: each anchor names the Nikon PFS and identifies it by the returned device value; the presence test now checks the discriminator and all three parameters go red on the previous wording. Coordinator suite 2495/99/0 (main was 2484). | Gate pushed at `276dd5a`. **The demo config has an autofocus device** — `Autofocus` / `DAutoFocus`, from 61a's own system-state artifact — so this machine is a *non-PFS focus lock*, the discriminating negative, not "a rig with no lock"; the no-device limb is NOT EXERCISED by design. Routing is scored from the session history via microclaw's own `load_history` / `_recorded_tool_calls`. Selftest run on both trees before pushing: main fails with `run_autofocus carries no nikon-pfs anchor`, the missing ordering invariant and `NOT EMITTED: get_focus_lock_state`; H5 proved able to fail on a synthetic misrouted session; NOT EXERCISED never exits zero. **Demo round 1, 2026-08-29 after `a9bdd91`: all 7 limbs PASS plus the human limb.** The gate program failed to launch three times first, and every cause was the runbook's: bare `python` is not on PATH there (Store alias, exit 9009), `uv run` from outside the repo cannot import microclaw, and `uv run` from inside it hit the checkout guard — 61a had already solved this via `active-slot.txt` and the file was written fresh instead of reusing it. Session limbs scored off-rig from the operator's artifacts (H4 `Autofocus`, H5 no `nikon-pfs` with state read before set, H5c `smlm` loaded, H6 export standalone); H1/H2/H3 then read directly off the installed build at `env-a`, agreeing with the coordinator's run byte-for-byte (prompt 31,326 chars, 16-line export). **H5's evidence is better than a bare pass**: the agent said *"The configured device is `Autofocus` … not a Nikon PFS, so no special skill is needed"* — identification by the returned device value, in its own words. H3 was settled twice: the operator's own exported script renders `# No hardware-routine effect.` with zero `NOT EMITTED`, where `main`'s exporter on the *same history* plants `raise RuntimeError`. | `a7b894c` merged 2026-08-29, branch deleted locally and on `origin`; worktree removed. Design gate run at step 10: R2 closed, Consequences reconciled to measured numbers, `CLAUDE.md`'s undecorated register re-measured. |
| 61c | `design61/nikon-paragraph-removal` | `536e202` (2026-08-30), worktree `../microclaw-61c` | `de1db13` (1 Codex start, no revision findings). Coordinator reviewed the diff, watched the flipped context test fail on the pre-fix product for the intended reason, then ran 279 focused tests and the complete suite: 2554 passed / 99 skipped / 0 failed. `SYSTEM_PROMPT` 31,326 → 28,971 chars (−2,355 / ~−589 tokens). | No additional rig gate: R1's positive Ti normal-work history is this conditional block's precondition and the block only removes its now-redundant prompt copy. | `9b052fa` merged 2026-08-30; post-merge design gate reconciled the measured context total and closed R1. |
