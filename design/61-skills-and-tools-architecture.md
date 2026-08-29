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
Nikon follow-up brings the net change to about −1,650 tokens. A specialized
workflow pays one conditional round trip when its skill is first needed. Adding
a skill becomes adding a file. Authorization, guards, bounds and confirmation
enforcement are unchanged: no authority is gained by reading a document. Nikon
operational-safety guidance becomes conditionally loaded only after its routing
invariant and positive behavioral test have been confirmed on the Ti; if they
do not reliably load the skill before action, the Nikon procedure stays in the
core prompt.
