# Plan: User Knowledge Base for Microclaw

## Goal

Persist non-standard, session-learned information — sample profiles, imaging strategy
preferences, and hardware quirks — to `~/.microclaw/knowledge.yaml` so future sessions
start with that context already loaded into the model's working memory.

---

## What gets stored

Three categories, stored under top-level YAML keys:

**`samples`** — per-sample profiles keyed by a short name the user or agent assigns.
Each entry holds labels/dyes, typical laser lines, preferred exposures, and free-text notes.

**`devices`** — non-standard device mappings and roles that Micro-Manager's device
list does not communicate (e.g. `Thorlabs-ELL-9` → cylindrical lens for 3D astigmatism
SMLM; `FilterWheel-2` → ND filter wheel, not emission filter).

**`strategies`** — named imaging recipes the user wants to reuse across sessions
(objective, z-range, autofocus policy, hook strategy, channel order, etc.).

Example `~/.microclaw/knowledge.yaml`:

```yaml
samples:
  U2OS_dSTORM_actin:
    description: U2OS cells, Alexa647-phalloidin fixed actin
    labels:
      - dye: Alexa647
        target: actin
        laser_line: 647nm
    typical_exposure_ms: 50
    preferred_channel: STORM647
    notes: Requires oxygen scavenger buffer; add 50 mM MEA

devices:
  Thorlabs-ELL-9:
    role: cylindrical_lens
    description: 3D astigmatism element for SMLM; insert for Z-localization
    notes: Adds ~200 nm astigmatism; must be inserted before DNA-PAINT acquisition

strategies:
  fixed_cell_survey_20x:
    description: Standard multiposition survey, 20× objective
    z_range_um: 20
    z_step_um: 0.5
    hook_strategy: autofocus_per_position
    notes: Use run_multiposition_with_autofocus
```

---

## Storage and loading

**File:** `~/.microclaw/knowledge.yaml` (human-editable, same directory as existing
`emu.json` and `hooks/`).

**New module:** `microclaw/knowledge_manager.py`

```python
KNOWLEDGE_PATH = Path.home() / ".microclaw" / "knowledge.yaml"

def load_knowledge() -> dict          # returns {} if file missing
def save_entry(category, key, value)  # upserts one entry, writes file
def delete_entry(category, key) -> bool
def format_for_prompt(knowledge: dict) -> str | None  # None if empty
```

**Injection into context:** `agent.py::run_agent()` loads the knowledge file at the
top of each call and, when non-empty, appends a second text block to the `system`
list. Placing `cache_control` on this second block lets the Anthropic API cache it
separately from the main system prompt — so an unchanged knowledge base hits the cache
even if the system prompt block was already cached.

```python
system_blocks = [
    {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}},
]
kb = load_knowledge()
kb_text = format_for_prompt(kb)
if kb_text:
    system_blocks.append(
        {"type": "text", "text": kb_text, "cache_control": {"type": "ephemeral"}}
    )
```

No changes to `__main__.py` are needed. An optional `--knowledge-file` CLI flag could
point to a non-default path (useful for multi-lab or multi-microscope setups); defer
this unless requested.

---

## New tools

Three tools added to `tools_schema.py` (schemas) and `tools.py` (implementations):

### `save_knowledge`

```
category: "samples" | "devices" | "strategies"
key: str          # short identifier, e.g. "U2OS_dSTORM_actin"
value: dict | str # structured data or plain note
```

Upserts the entry in `knowledge.yaml`. Returns confirmation with the stored value so
the agent can show the user what was saved.

### `get_knowledge`

```
category: "samples" | "devices" | "strategies" | null
```

Returns the stored knowledge for a category (or all categories if null). Useful when
the agent wants to recall a sample profile mid-session or the user asks what's been saved.

### `delete_knowledge`

```
category: str
key: str
```

Removes one entry. Returns whether the key was found.

---

## Agent instructions (additions to `SYSTEM_PROMPT`)

Add a new section to the system prompt after the existing sections:

```
User knowledge base:
- At the start of sessions involving a named sample or unfamiliar device, call
  get_knowledge to retrieve stored profiles and device notes before issuing tool calls.
- When you learn a non-obvious fact during a session — a device's physical role, a
  sample's imaging requirements, a preferred parameter set — offer to save it:
  "Want me to save that to your knowledge base for future sessions?"
  Only call save_knowledge after the user confirms.
- Never infer device roles or sample properties from the knowledge base alone; confirm
  with the current hardware state (list_device_properties, get_system_state) because
  the microscope configuration may have changed.
```

The "confirm before saving" policy is consistent with the existing hook system.

---

## Implementation steps

1. **`microclaw/knowledge_manager.py`** — implement `load_knowledge`, `save_entry`,
   `delete_entry`, `format_for_prompt`. Format output as a clear YAML-fenced block
   with a heading so the model knows what it's reading.

2. **`tools_schema.py`** — add schemas for `save_knowledge`, `get_knowledge`,
   `delete_knowledge`. Insert them before the final (cached) tool entry so the cache
   watermark stays at the end of the tool list.

3. **`tools.py`** — implement the three tool functions and add them to `TOOL_REGISTRY`.

4. **`agent.py`** — load knowledge in `run_agent()`, build multi-block `system` list,
   add the knowledge-base instructions paragraph to `SYSTEM_PROMPT`.

5. **Tests** — unit tests for `knowledge_manager.py` (tmp_path fixture); integration
   test verifying the second system block is populated when the knowledge file is
   non-empty and absent when it is empty.

---

## What this does NOT do

- Does not auto-save without user confirmation (consistent with hooks policy).
- Does not version or diff the knowledge file (YAML history is left to git if the user
  checks it in).
- Does not store position lists (those are already persisted via `save_position_list`).
- Does not replace the safety config — safety limits remain in `safety_config.yaml`.
