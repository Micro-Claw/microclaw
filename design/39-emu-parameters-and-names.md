# design/39 — EMU's `parameters` block is never read, so nothing has a name

Source: M5 session `20260805_115726_454934_microclaw_history.jsonl` (rig evidence
folder `38-composite-hooks-m5`), against `config.uicfg` from the same rig.

The agent asserted the 640 nm laser was at **slot 1**. It is at **slot 3**. The
user had to correct it mid-run, twice, and has been hand-entering laser and
filter identities into the knowledge base to work around it. This is the fix.

---

## What happened

Turn 9, after reading `get_emu_laser_map`:

> Slot **1** — which the EMU map identifies as the 640 nm laser (Laser 3 enable)
> — reads `enabled="0"` […] the telemetry says the 640 nm laser is **off**

Turn 12, the user:

> Slot 3, the one that is on now, is 640 nm

The user is right, and so is `config.uicfg` — `"Laser 3 - Name": "640"`. The map
did not say slot 1 was 640 nm. **The map said nothing at all**, so the agent
inferred a wavelength from the MM property string `Laser 3: 1. Enable` on slot 1
— the iChrome's own internal channel number, which on this rig runs opposite to
the EMU slot order:

| EMU slot | `Laser i - Name` | MM property string |
| --- | --- | --- |
| 0 | 405 | `iChrome-MLE-TCP-Laser 4: 3. Level %` |
| 1 | 488 | `iChrome-MLE-TCP-Laser 3: 3. Level %` |
| 2 | 561 | `iChrome-MLE-TCP-Laser 2: 3. Level %` |
| 3 | **640** | `iChrome-MLE-TCP-Laser 1: 3. Level %` |

This is precisely the failure `build_emu_map`'s docstring forbids — "Nothing may
infer a slot index" — and the docstring is not enough, because the agent had no
other source. Naming the slots removes the temptation and the need.

## Root cause

`read_emu_config` (`microclaw/emu_manager.py:399`) returns four keys:

```python
return {
    "config_name": ..., "plugin_name": ...,
    "properties": _parse_properties(properties, device_labels),
    "plugin_settings": settings,
}
```

`properties` and `settings` are read. **`parameters` is never read.** Every
human-facing name on the rig lives in `parameters`, and microclaw discards the
whole block. Concretely, from M5's config:

```json
"Laser 0 - Name": "405",           "Laser 3 - Name": "640",
"Controls - Two-state device 3 name": "BFP",
"Controls - Two-state device 1 name": "3D",
"Filters - Filter names":   "525/45, 600/60, 676/37,685/70, 452/45, None",
"Filters - Filter names 2": "525/50, 600/52, 676/37, 685/70, 457/15, None",
"Linear Stages - Name 1": "TIRF Stage",
"Acquisitions - BFP lens": "Two-state device 3"
```

Two smaller defects fall out of the same session and the same file:

- **`check_emu_installed` reported `htsmlm_installed: false` on an htSMLM rig.**
  `_find_jars` (`emu_manager.py:58`) searches only `mmplugins/` and `plugins/`;
  the htSMLM jar ships in `<MM>/EMU/htsmlm-2.1.0.jar`. The prefix `"htSMLM"` also
  will not match `htsmlm-*.jar` on a case-sensitive filesystem.
- **The second filter wheel is invisible.** `_FILTER_WHEEL_KEY` is the single
  literal `"Filter wheel position"` (`emu_manager.py:272`), so `Filter wheel 2
  position` falls into `other` unrecognised — on a rig whose panel title is
  literally `"Top: Transmitted Bottom: Reflected"`.

## The join rules

The EMU contract, confirmed against `jdeschamps/EMU-guide` `examples/simpleui`
rather than assumed from M5:

- UIProperty key = `<PanelLabel> + " " + <property>` (`LaserPanel.java`
  `getUIPropertyLabel`, `"power percentage"`, `"on/off"`).
- UIParameter key = `<PanelLabel> + " - " + <ParameterName>`
  (`PARAM_TITLE = "Name"`, `PARAM_NAMES = "Filter names"`).
- Filter names are comma-separated, with `"None"` as the per-slot default
  (`FilterWheelPanel.java`: *"Comma separated filter names, e.g.:
  name1,name2,name3…"*, defaults built as `"None,None,…"`).

So four rules, applied in order. Each either matches an existing UIProperty key
exactly or does nothing — no rule invents a target.

**A — panel name.** `<Panel> - Name` names every UIProperty starting with
`<Panel> `. Covers `Laser 0`…`Laser 3`, `Laser trigger 0`…`3`, and the
iBeamSmart panels (whose panel labels come from `settings`, e.g.
`"iBeamSmart #1 name": "Booster"`). *This rule alone fixes the reported bug.*

**B — explicit target.** `<Panel> - <X> name` names the UIProperty `<X>`, when
`<X>` is a UIProperty key verbatim. `Controls - Two-state device 3 name` → strip
panel → `Two-state device 3 name` → strip `" name"` → `Two-state device 3`,
which exists ⇒ that property is named **BFP**.

**C — slot list.** `<Panel> - <Y> names[ <k>]` is a comma-separated per-slot
list for a MultiState UIProperty carrying the same ordinal `k`. `Filters -
Filter names` → wheel 1 → `Filter wheel position`; `Filters - Filter names 2`
→ wheel 2 → `Filter wheel 2 position`. The filter properties carry no panel
prefix, so unlike A/B this is a shape match; it must therefore **fail loudly**:
if the resolved property does not exist, or its `states` table and the name list
differ in length, leave the wheel unnamed and record `name_mismatch`. Never
`zip()` — `zip` truncates silently and would mis-slot every filter after a gap.

**D — role alias.** A parameter whose *value* is verbatim a UIProperty key is an
alias for it: `Acquisitions - BFP lens` → `Two-state device 3`,
`Acquisitions - Focus stabilization` → `Z stage focus locking`. Self-validating.
Note `Acquisitions - Bright field` → `Two-state device 5`, which is
`Unallocated` — an alias must not make an unallocated property look available.

`"None"` is EMU's placeholder for an unset name (`Two-state device 4/5/6 name`,
filter slot 5). It is recorded as an *empty slot* but must never be registered
as a lookup name.

---

## Planned edits

All in `microclaw/emu_manager.py` unless noted. No new module, no new tool, no
new indirection — names ride on the records that already exist.

**1. Read the block.** `read_emu_config` gains `"parameters":
_parse_parameters(active.get("parameters", {}))`, splitting each key on the
first `" - "` into `(panel, name)`. Missing/non-mapping `parameters` is not an
error — plugins need not declare any.

**2. Name things in `build_emu_map(props, params)`.** `params` defaults to `{}`
so offline callers and the existing tests keep working.

```python
lasers[3] = {"name": "640", "enable": {...}, "power_pct": {...},
             "trigger_mode": {...}, "trigger_sequence": {...}}
other["Two-state device 3"] = {"name": "BFP", "device": "Thorlabs ELL6",
                               "property": "State", "on": "0", "off": "1"}
```

Absent a parameter, `name` is simply absent — never a guess, never an index.

**3. Replace `filter_wheel` with `filter_wheels`.** Keyed by wheel ordinal,
each carrying its named slots. Per *No legacy anchoring*, the singular key is
removed outright rather than dual-written. `authorization.py:119` and
`tools.py:4481` reach into the map but take `["lasers"]`/`["focus_lock"]`, so
they are unaffected. The readers of the `filter_wheel` **key** are exactly:

```
microclaw/emu_manager.py:317,318,335   producer
microclaw/tools_schema.py:1611         get_emu_configuration tool description
tests/test_emu_manager.py:233,234      test_filter_wheel_carries_the_state_table
```

`tools_schema.py:1611` is the one that matters — it is the description the agent
reads to decide what the tool returns, so a stale `'filter_wheel'` there would
advertise a key that no longer exists. Note `htsmlm_docs.py` does **not** read
this key; its filter lines (70–71) are wrong for the unrelated reason in item 8.

```python
"filter_wheels": {
  1: {"ui_property": "Filter wheel position",
      "device": "Thorlabs Filter Wheel", "property": "State",
      "slots": {0: {"name": "525/45", "value": "0"},
                ...,
                5: {"name": None, "value": "5", "empty": True}}},
  2: {...},
}
```

**4. Teach `resolve_emu_device` common names**, so `resolve_emu_device("BFP")`
and `resolve_emu_device("640")` work alongside the UIProperty name it takes
today. Order: exact UIProperty key → rule-A/B name → rule-D role alias → filter
slot name. A laser name resolves to the **whole slot record**, not one line, so
the enable/power/trigger lines stay paired. Names are free text and may collide
(M5 has `"405"` on both `Laser 0` and `Laser trigger 0` — same physical laser, so
one slot record answers both; `Powermeter - wavelengths` says `638` where the
laser says `640`). Matching is case- and whitespace-insensitive and otherwise
**exact**: no fuzzy wavelength arithmetic. A collision across unrelated families
raises listing the candidates rather than picking one.

**5. Surface the name where the agent reads first.** `_laser_state`
(`tools.py:749`) emits `{"3": {"name": "640", "enabled": "1", "power_pct":
"2.2400"}}`, so `get_system_state` — the first tool called in the failing session
— carries the identity. `get_emu_laser_map` inherits it from `build_emu_map`.

**6. Cache parameters.** `_EMU_SESSION_CACHE["parameters"]` alongside
`properties` (`tools.py:4372`), and `_cached_emu_properties` returns both.

**7. Fix htSMLM detection.** `_find_jars` searches `EMU/` in addition to
`mmplugins/`/`plugins/`, and matches case-insensitively. Add
`htsmlm_configured` to `check_emu_installed`, taken from the config's
`pluginName` (`"ht-SMLM"`, normalised) — that is what is actually *in use*,
which the jar's presence on disk does not establish.

**8. Correct `htsmlm_docs.py`.** It currently documents UIProperty names that do
not exist: `"Filters Filter wheel position"`, `"Focus Z stage position"`,
`"Powermeter Laser powermeter"`. The real keys carry no panel prefix — `"Filter
wheel position"`, `"Z stage position"`, `"Laser powermeter"`. Also: drop *"Ask
the user what each numbered device corresponds to"* (§"Additional two-state
controls"), which is now answered by the config, and document the `name` field
and name-based `resolve_emu_device`.

**9. `_candidate_mm_dirs` misses date-suffixed installs** — the user's path is
`C:\Program Files\Micro-Manager-2.0-20260713`. Glob `Micro-Manager-2.0*`. Minor
(the live Java probe resolved it in this session), but the offline fallback
cannot find this rig at all.

## Deliberate departure: parsing is not gated on htSMLM

The ask was to detect htSMLM and *then* read the filter parameters. I plan to fix
the detection (item 7 — it is genuinely broken) but **not** gate parsing on it.
`Filter names` as a comma-separated per-slot list is EMU's own convention, not
htSMLM's: it is in `examples/simpleui/FilterWheelPanel.java`, quoted above.
Gating would break a plain-EMU rig with a filter wheel for no gain, and it cuts
against *"never anchor on one microscope."* The rules key off parameter **key
shape**, which is shared, and every one of them no-ops when the shape is absent.

## Rejected

- **Laser/filter `Color`** (`"Laser 3 - Color": "red"`) — corroborates the name,
  but the name is the answer; it stays in raw `parameters` for anyone who wants
  it.
- **A `names` top-level index.** `resolve_emu_device` already is the lookup;
  a parallel index is a second way to do one thing.
- **Wavelength inference from device strings.** The cause of the bug. If no
  parameter names a slot, the map says nothing.

## Tests (`tests/test_emu_manager.py`)

The existing fixture is synthetic; add a fixture built from the real M5
`config.uicfg` (checked in as `tests/fixtures/m5-config.uicfg`) so the regression is
pinned to the file that produced it.

1. `lasers[3]["name"] == "640"` and `lasers[1]["name"] == "488"` — the reported
   bug, asserted against the rig's own config.
2. `other["Two-state device 3"]["name"] == "BFP"`; `Two-state device 4` gets no
   name (`"None"` placeholder).
3. `filter_wheels[1]["slots"][2]["name"] == "676/37"` — proves the whitespace in
   `"676/37,685/70"` is stripped; slot 5 is `empty`.
4. `filter_wheels[2]["slots"][0]["name"] == "525/50"` — the second wheel exists
   and is not the first wheel's list.
5. A name list shorter than the `states` table leaves the wheel unnamed with
   `name_mismatch` — no silent `zip` truncation.
6. `resolve_emu_device("BFP")` and `resolve_emu_device("640")` resolve; `"640"`
   returns the slot record with `trigger_mode.property == "Mode3"` (slot-pairing
   preserved through name lookup).
7. `Acquisitions - Bright field` → unallocated `Two-state device 5` is not
   presented as available.
8. A config with no `parameters` block parses, and every `name` is absent —
   the generic-rig case.
9. `_find_jars` finds `EMU/htsmlm-2.1.0.jar`, case-insensitively.

## Rig gate

M5 (htSMLM, two wheels, reversed iChrome order) and one non-EMU rig for the
no-`parameters` path. The gate that matters is the one this came from: ask for
the 640 nm laser and confirm the agent reaches **slot 3** with no correction, and
ask to change the **BFP** and confirm it reaches `Thorlabs ELL6-State` without
asking the user which numbered device that is.
