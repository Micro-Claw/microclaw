# The lock must refuse the image sweep

Closes register row **R88**. It settles off-rig; a Nikon confirms it but is not
needed to decide it.

## Problem

One behaviour, three interventions, three failures.

| # | intervention | shipped | outcome |
|---|---|---|---|
| 1 | prompt text, first edit | pre-design/56 | did not hold (design/56 §9e) |
| 2 | prompt text, second edit | pre-design/56 | did not hold (design/56 §9e) |
| 3 | `status_properties` + `probe_hint` in the payload | block 56a, 2026-08-23 | **failed on its first exposure to a live model**, 2026-09-03 |

Intervention 3 exists *because* 1 and 2 failed — design/56 §9e says so in as many
words: *"Two prompt edits had already tried to push the other way and neither
held, which is the signal that the fix is not more prompt text."* Its own
"Owed rig evidence" list then parks it as item 3, **"No live model has seen this
payload"**, and item 4, **"the lock offered without being asked — has never
passed cleanly"**. One live model has now seen it. R88 is the measurement.

**A fourth paragraph of prose is not the fix, and neither is a fifth field in the
payload.** That is the whole reason this notebook exists.

### The line that already knows

`run_autofocus` **already calls `get_focus_lock_state`** — unconditionally,
before every sweep, at `microclaw/tools.py:6276` — and reads exactly one key off
it:

```python
    lock = get_focus_lock_state(ctrl, guard)
    if lock.get("engaged"):
        return {"error": "…a Z sweep would fight the servo loop…", "focus_lock": lock}
    # everything else in `lock` is discarded here
    with _pause_live(ctrl, restore=False) as live_state:
        ...
```

On the recorded session that call returned the device (`TIPFSStatus`), its status
property, its current value (`Out of focus search range`) and the `probe_hint`.
`engaged` was `False`, so the tool dropped all of it, paused live view, and spent
ten exposures (`exposures_spent: 10`) on a curve that could not have had a peak —
the sample plane was ~2250 µm away and the lock was the only instrument in the
room that could find it.

So the rule at `microclaw/agent.py:340-349` — *"BEFORE proposing an image-based
sweep… propose run_autofocus with a property probe FIRST… Do not wait to be
asked"* — is not merely unenforced. It is unenforced **at a line that already
holds everything needed to enforce it**, four lines below a refusal of exactly
the same shape.

There are two causes here, and they should not be collapsed. The **behavioural
cause** is that advisory routing is unreliable: the model had already received
this same focus payload through `get_system_state` and ignored it. The **code
defect** is that the image-sweep path remained executable even after the tool
itself had recovered enough state to know that the cheaper hardware-lock probe
should come first. The payload explains what to do; the refusal enforces what
must not happen. This block fixes the second and measures whether that
containment also recovers the session after the first routing mistake. It does
not claim that the refusal can change a decision the model made before seeing
it.

## Decision

### D1 — the tool offers the lock first, at zero exposures

(Titled "the tool refuses" in the original draft. It refuses *this call*; the
caller reaches the image sweep on the next one — see D3 as corrected. Nothing
here makes image-based autofocus unavailable on a lock rig.)

Extend the branch that is already there, but do not use non-empty
`status_properties` as the hardware-lock discriminator. The recorded demo
device `Autofocus` / `DAutoFocus` is a software autofocus adapter and already
returns three read-only properties, so that condition alone would refuse a
legitimate image sweep on the one available non-PFS machine.

Measured, not inferred — `design/61-block61a-system-state.json`, 2026-08-28:

```json
"focus": {"device": "Autofocus", "engaged": false,
          "property": "continuous focus device Autofocus",
          "status_properties": {"Description": "Demo auto-focus adapter",
                                "HubID": "", "Name": "DAutoFocus"},
          "probe_hint": "…call run_autofocus with probe device 'Autofocus'…"}
```

`_lock_status_properties` keeps every **read-only** property, and `Name` and
`Description` are common read-only metadata properties — the Nikon's own
payload carries `Name` beside its real `Status`. So a
`bool(status_properties)` gate is close to always-true, not a class test.

The same artifact rules out the obvious discriminator: `engaged` is a clean
`False` there, so **`is_continuous_focus_enabled()` answers for a software
autofocus adapter too** and cannot separate the classes. If MMCore exposes a
continuous-focus-drive predicate through the bridge, verify it against both
machines before relying on it — `DAutoFocus` must answer false and `TIPFSStatus`
true. Treat that as a candidate to check, not a fact: the bridge resolves method
names at runtime, so its presence cannot be established from this checkout.

74a must first establish a positive `probeable_hardware_lock` discriminator from
configuration or adapter capability. It may reuse state already collected by
`get_focus_lock_state`; it must not infer the class merely from property names or
from the existence of readable properties. If Micro-Manager exposes no reliable
generic discriminator, scope the refusal to device/configuration identities
established as hardware surface locks and leave unknown autofocus devices
unchanged. Do not ship the broader refusal on the promise that a future rig will
clarify the ambiguity.

```python
    lock = get_focus_lock_state(ctrl, guard)
    if lock.get("engaged"):
        return {...}                                    # unchanged
    if (probe is None and probeable_hardware_lock(lock)
            and lock.get("status_properties") and not focus_lock_probe_failure):
        return {"error": ..., "focus_lock": lock}       # new; before _pause_live
```

Placement is load-bearing: it must stay above the `with _pause_live(...)` block,
so the refusal costs no exposures and moves no Z. `guard.check_z` has already
run, so a refusal here still validates its window.

### D2 — the refusal carries the vocabulary, not a lecture

The same shape as the probe's existing missed-window refusal, which lists every
value the sweep observed. It names the device, prints `status_properties` with
their current values, and gives a directly reusable `probe=` argument:

> This rig has a hardware focus lock, `TIPFSStatus`, and it is not engaged. An
> image metric maximises sharpness, which on a coverslip is often not the sample
> plane. Probe the lock first — property reads spend no exposures. Current
> readings: `{"Name": "TIPFSStatus", "Status": "Out of focus search range"}`.
> Re-call with `method="sweep"` and
> `probe={"device": "TIPFSStatus", "property": "Status", "in_focus_values": ["<your best guess>"]}`;
> if no plane matches, the refusal lists every value the sweep actually saw.
> `Out of focus search range` says the coverslip is not in the band **at this
> Z** — it is not a statement that the lock is unavailable.

That last sentence is the one this session most needed. The model read
`Out of focus search range` as a verdict about the hardware and wrote *"I'll
disengage worry about PFS (it's already out of range / disengaged)"*.

`in_focus_values` is the one part a caller must fill in, and it cannot be
omitted: `run_autofocus` reads a probe with no `in_focus_values` as a *numeric*
probe, a different mode. So the refusal carries a placeholder, and this repo has
a scar about placeholders that look copy-pasteable (52c shipped
`Select-String -Pattern "<t2>", "<t3>"`, it was run verbatim, matched nothing,
and "passed"). Write it so an unedited re-send fails loudly rather than
silently — and lean on the follow-up refusal, which lists every value the sweep
observed, as the real discovery path.

**This is a refusal, not a confirmation.** It blocks nothing the caller cannot
retry with a property probe, it waits for no human, and it reaches no
`CONFIRM_FN` (`CLAUDE.md` §"A confirmation is for something Microclaw is about
to do on the user's behalf").

### D3 — one explicit, auditable opt-out

**Corrected 2026-09-04, operator, before 74a merged.** The paragraph below
originally specified `focus_lock_probe_failure: str | None = None` on the
premise that *"the only real case left"* for an image sweep on a lock rig is a
probe that found no band. **That premise is wrong**, and it made the shipped
build a block rather than an offer: a caller who simply wants the image metric
has no failed probe to describe, so the only way past the refusal was to assert
something untrue. The operator's words: *"I don't want to completely block the
user from being able to run an image-based autofocus on a Nikon… Sometimes it's
helpful to use the image-based approach. Most of the time, the PFS approach is
better. **The PFS approach must be offered first**, before the image-based
autofocus."*

So the refusal stays — enforcement in the tool is wanted — but the opt-out
states **why the image metric is the right instrument here**, not a claim about
the rig. `image_metric_reason: str | None = None`, non-empty, admitting the two
legitimate answers:

- the operator asked for an image-based focus, and
- a property probe reported no band at this XY.

One argument, one extra round trip, zero exposures spent reaching it. That is
"offered first" rather than "blocked". The dissent recorded below — that a bare
`bool` would do — is *partly* upheld: its objection was to a parameter needing
two paragraphs before it can be called, and a reason string keeps the audit
trail D3's second obligation asks for while fitting on one line. What the
correction actually overturns is the *justification* for the string, not the
string.

The engaged-lock branch above is untouched and still fires first, so this
refusal is unreachable while the lock is engaged.

The tool is stateless and cannot prove that an earlier call occurred, so this is
an auditable assertion rather than cryptographic enforcement. The live-model gate
must score premature use of it. If models routinely invent the assertion, remove
the opt-out rather than adding more prose.

Two obligations come with it, whichever spelling wins:

- **`run_autofocus` is `@emits(_emit_autofocus)`** (`microclaw/tools.py:6171`),
  and `CLAUDE.md` requires every argument a tool accepts to reach the emitted
  script. This one legitimately renders nothing — a standalone script has no
  Microclaw refusal to suppress — but that must be a **decision recorded in the
  emitter**, not a silent drop, which is the shape 43j's gate caught.
- **A freeform justification lands in the session record**, where a later reader
  sees a sentence about the rig that no instrument measured. That is R79 /
  design/40 D6's live complaint about measurement-versus-inference. Keep it
  labelled as a caller assertion wherever it is stored or replayed.

*Considered and not taken:* a bare `bool`. The coordinator's preference, on
`CLAUDE.md` §"Microclaw is easy to use" — a parameter that needs two paragraphs
before it can be called is the smell that rule names, and a model willing to flip
a boolean will write one clause just as readily, so the added friction is thin
against a permanent surface cost. Recorded as a dissent, not a blocker: D3's gate
condition (score premature use; delete the opt-out if it is invented) settles it
with a number either way, which a preference cannot.

### D4 — the hint names the skill

`get_focus_lock_state`'s `probe_hint` (`microclaw/tools.py:10241`) does not name
`nikon-pfs`. The skill-routing sentences live only in tool schema prose
(`microclaw/tools_schema.py:994`, `:2118`, `:2135`) and every one of them is
conditioned on *"when **get_focus_lock_state** reports a PFS"* — a call this
session never made, because `get_system_state` had already answered it
(`microclaw/tools.py:4043` embeds the identical payload). The antecedent was
literally unmet.

The payload-names-a-skill pattern already exists one field over: `optical_path`'s
hint says `call load_skill(name="optical-paths")` at `microclaw/tools.py:3864`.
Do the same here, gated case-insensitively on `"PFS"` appearing in the **device**
label — which is the discrimination the schemas already ask the model to make
(*"identify the lock by this device value, never by a property name that happens
to contain 'PFS'"*), moved into code. Both known rigs match (`TIPFSStatus`,
`PFS`); a Zeiss Definite Focus correctly does not, and there is no skill for it.

**This substring is not the name-matching D1 forbids**, and the difference is
the consequence of being wrong. D4 decides *which skill to name in a hint*: a
miss names no skill and the session proceeds exactly as it does today, and a
false hit costs a wasted `load_skill`. D1 decides *whether to block an action*:
a false hit refuses legitimate work on a machine microclaw misread. Same
technique, two different standards of evidence — D4 may key on the device label;
D1 may not.

This is not a new discrimination: **block 61b already shipped it and gated it**
(`design/61` §"Run ledger"). Its three anchors first shipped as *"on a rig with
this kind of hardware lock"*, which read plainly told an agent on **any**
focus-lock rig — the demo machine included — to load the Nikon skill; the fix was
to identify the PFS by the returned device value, and the demo gate then recorded
the agent saying so in its own words: *"The configured device is `Autofocus` …
not a Nikon PFS, so no special skill is needed."* D4 moves that same
discriminator from the schema prose into the payload.

### D5 — do not touch `microclaw/agent.py` in the same block

Trimming the ten-line prompt paragraph at `agent.py:340-349` is tempting once
code enforces it. Don't, in this block: changing the prompt and adding the
refusal together makes 74b's measurement uninterpretable, because a pass would
not say which one produced it. Revisit after 74b has a number.

## What this notebook does not decide

- **Whether `probe_hint` itself should be gated the same way.** Register row
  **R06** is this identical question one field over: `probe_hint` is emitted for
  any non-empty `status_properties`, so the demo machine is invited to probe
  `Description`, `HubID` and `Name`. R06 has been parked for want of *"a rig with
  a real lock to settle"* it — but the thing it actually needs is a positive
  classifier, which is exactly what D1 must now build. If 74a produces a working
  `probeable_hardware_lock`, gating the hint on the same predicate is a one-line
  follow-on and R06 closes with it; if 74a falls back to an identity list, R06
  stays open and says so. Either way, **do not let the two drift into two
  different answers to one question.**

  (An earlier draft of this notebook claimed the demo device exposes no readable
  properties and that D1 was therefore safe there. It does expose three, and the
  claim was wrong — it came from paraphrasing R06's summary line instead of
  reading the artifact. Recorded because a `bool(status_properties)` gate would
  have shipped on that reasoning.)
- **Locks the discriminator cannot classify.** On the non-EMU route,
  `get_auto_focus_device()` does not by itself distinguish a hardware surface
  lock from a software autofocus adapter. The demo artifact proves the problem:
  `Autofocus` / `DAutoFocus` is software autofocus and its recorded
  `status_properties` are non-empty (`Description`, `HubID`, and `Name`). D1
  would false-positive there if readable properties were its only gate.

  D1 therefore applies only when the new positive discriminator establishes a
  probeable hardware lock. An unknown device is left unchanged. Expanding the
  classifier to additional adapters is separate evidence-driven work; neither a
  device-name substring nor `status_properties` alone closes it.
- **Image-metric autofocus on `TIPFSOffset`.** Explicitly out of scope
  (operator, 2026-09-04): *"we don't want to adjust any of the behaviour
  surrounding the TIPFSOffset in this block."* Recorded here because it was
  found while scoring this one and it is the *other half* of the Nikon focus
  workflow. `run_autofocus` sweeps only the core focus device — there is no
  named-stage axis — and its engaged-lock branch refuses outright, so the
  post-engage fine-tune has no tool and is done by hand. Measured in three
  sessions, always with the PFS engaged, one model round trip and one snap per
  plane:

  | session | route | planes |
  |---|---|---|
  | `pfs_fix` 2026-08-05 | raw `set_device_property TIPFSOffset.Position` | 163.325 → 169.325, 2 µm steps |
  | `pfs-nikon` 2026-08-22 | `move_named_stage` | 17 moves, 5 µm steps, `snap_and_analyze` between every one |
  | `nikon-no-pfs-again` 2026-09-03 (R88) | `move_named_stage` | 140 → 120 → 130 → 150 → 140 |

  The last two **backtrack and re-visit** — 165.4 appears three times in the
  2026-08-22 run, and the R88 run ends at the value it started from. That is
  hunting, not a sweep, and it is design/56's own finding one axis over: *"a
  hand-driven loop hides that behind its own latency … collapsing that loop into
  one tool call took it away."* Goes to `design/70` as a register row.

- **R15**, engaging after the band is found. It is a *different* row and this
  session gave it a **positive** observation: once `nikon-pfs` was loaded, the
  model engaged the lock the moment the band appeared and ran both of the skill's
  mandatory post-engage checks — an image check, then a +10/+10 µm XY jog with
  the lock re-read, which came back `engaged: true`. The defect here is entirely
  upstream of that.

## Evidence

**The three previous interventions all shipped without a measurement.** That is
the thing to fix about the process, not only the code.

1. **A fixture built from the rig's own payload, not from our assumptions.** The
   recorded `focus` block in R88's JSONL is the fixture. Make it
   **bridge-shaped** (`CLAUDE.md`: a `MagicMock` hands back Python-friendly
   objects and hides the defect): `_lock_status_properties` reads
   `get_device_property_names` through `_str_vector`, so the fake should return a
   `size()`/`get(i)` vector whose `__iter__` raises, which is what proves that
   path is actually taken rather than bypassed by a Python list.
2. **Assert the cost, not just the refusal.** Count snaps on the fake camera and
   assert **zero**, and assert Z did not move. A refusal that fires after
   `_pause_live` would pass a text-only assertion.
   Add the converse counterexample from the demo machine rather than inventing
   one: replay the recorded `Autofocus` / `DAutoFocus` payload with its three
   non-empty `status_properties` — it is on disk, in
   `design/61-block61a-system-state.json` — and assert that an image sweep
   is **not** refused. This test must fail if the implementation collapses the hardware
   discriminator back to `bool(status_properties)`.
   Add positive fixtures for both known Nikon device labels so the discriminator
   cannot become a demo-only exclusion list that admits nothing useful.
3. **Watch it fail.** `git checkout <before> -- microclaw/`, run the new test,
   confirm it fails because the sweep *ran and spent exposures*, restore.
4. **The measurement that decides it** — block 74b below. Everything above proves
   the refusal exists; only 74b proves it prevents the costly outcome and gives
   the model a usable recovery path.
5. **Nikon, to confirm.** The register's Nikon bucket says the operator has no
   reachable Ti and parks four rows on it. **The session behind R88 is a Nikon Ti
   dated 2026-09-03**, so ask the operator before those rows stay parked.

## Blocks

**74a — the refusal.** D1–D4, their tests, and step 3 above. `microclaw/tools.py`
(`run_autofocus` at `:6276`, `get_focus_lock_state`'s hint at `:10241`) and
`microclaw/tools_schema.py`. Settles LOCAL. Leaves `microclaw/agent.py` alone
(D5).

**74b — the live-model measurement.** Three arms (A: pre-74a; B: post-74a on the
frozen payload, scoring D1; C: post-74a on the regenerated payload, scoring D4).
Replay a `get_system_state` payload to a live model — the recorded pre-74a one
for arms A and B, the regenerated one for arm C — cold, with this session's two
opening messages verbatim (*"ok, I have a sample on the microscope. Use the BF to
find a couple of dividing cells."*, then *"no take the 60x and find the focus
first"*). Arms A and B score one thing: **does an image-based sweep execute
before a property probe?** Record, separately:

1. image exposures spent before the first property probe;
2. Z motion caused by an image sweep before the first property probe;
3. whether a non-probe call is refused and the model then retries with a probe;
4. whether the model supplies `focus_lock_probe_failure` before any probe has
   actually reported no band.

The primary pass condition for arm B is zero image-sweep exposures and
zero image-sweep Z motion before the probe. A first non-probe *call* may still
occur: D1 cannot alter a decision made before its refusal is returned. Successful
recovery is a subsequent probe call. Premature use of the opt-out is a failure.

Do not score “probe call before non-probe call” as evidence for D1. With the
recorded payload replayed verbatim, the arms are intentionally indistinguishable
until the first non-probe call returns, so that ordering cannot improve because
of the refusal.

**That freeze leaves D4 with no arm, and D4 must not ship unmeasured** — it is
the direct successor to interventions 1, 2 and 3, and this notebook opens by
naming the fact that all three shipped without a measurement. Add **arm C**: the
post-74a tree replaying the payload **as 74a now generates it**, carrying the
skill-naming hint, scored on initial routing — probe call before non-probe call,
the criterion arms A and B are barred from using. Same harness, same fixtures,
one more arm; do not attribute arm C's result to D1 or arms A/B's to D4.

- Run **every arm** — pre-74a, post-74a on the frozen payload, and post-74a on
  the regenerated payload — so each number discriminates. A single arm measures
  nothing, the same way design/59's gate selftest had to run on both trees.
- **Put enough n in every stochastic arm.** Arm A is a baseline live-model
  routing measurement, not a deterministic control: a completion can choose a
  property probe before any image sweep even though the recorded session did
  not. Arms A, B and C therefore each need **n ≥ 8** — two runs of one wording
  once gave 5/8 then 15/16 (`CLAUDE.md` §step 6), so a smaller sample cannot see
  a difference. Report the per-arm rates and counts; do not treat one historical
  failure as the probability of the pre-74a behavior.
- Price it before running it. This is a handful of short completions against a
  recorded payload, not full sessions — but say the number first
  (`no-credit-overage`).
- If arm B does not prevent execution of the image sweep, or models evade it
  through the opt-out before probing, **the refusal is the wrong containment**,
  and that is a result worth having before a fourth intervention ships
  unmeasured. Initial-call ordering is not part of that verdict; it is arm C's,
  and arm C failing is a verdict on D4, not on D1.

### Coordinator decisions, 2026-09-04

**There is no Nikon gate, and there will not be one.** The operator cannot test
on the Ti before this reaches `main`; the Nikon user pulls afterwards and can
only run live on a real sample. So everything 74a ships must be decidable from
recorded artifacts, and the Nikon run is a formality, not evidence we are
waiting on.

Three consequences, all measured rather than assumed:

- **`core.get_device_library` / `get_device_name` are already proven on a real
  Nikon Ti over the bridge.** `microclaw/rig_inventory.py:393-394` calls both,
  and `nikon-lausanne-rig/inventory.json` is that rig's own output: the lock is
  library `NikonTI`, adapter name `TIPFSStatus`, device type `AutoFocusDevice`.
  The demo machine's is `DemoCamera` / `DAutoFocus` (`Device,Autofocus,DemoCamera,
  DAutoFocus` in `9a-gate-demo/MMConfig_demo_aux_z.cfg`). D1's discriminator is
  therefore the **adapter identity allowlist** the Decision section names as the
  fallback — built on two calls whose return values on both machines are on
  disk, not on a capability nobody can verify.
- **The Ti2-E / Dragonfly is a recorded gap, and stays one.** Its lock's device
  *label* is `PFS` (design/56, and the 2026-08-23 session), but its adapter
  library and name appear nowhere in the archive. It is left unclassified — no
  refusal, behaviour unchanged — and becomes a register row asking for those two
  reads next time that rig is reachable. A guessed `NikonTi2` entry is not
  admissible in a refusal path.
- **No M5 gate is possible for D1.** M5 takes the EMU branch of
  `get_focus_lock_state`, which never returns `status_properties`, so D1's
  condition is unreachable there; M2 has no autofocus device configured at all.
  The demo machine is the only rig that can exercise this, and only as the
  negative control.

**74b is held until 74a merges** (operator decision). Arm A runs against a
worktree pinned at the pre-74a commit, so nothing is lost by deciding its
budget later. Priced for that decision, against Opus 4.8 at **$5 / $25 per
Mtok** (checked against the model table, not recalled): ~31k tokens of static
context per call, ~6 turns per sample, so ~$1.15 per sample uncached and ~$0.50
with prompt caching on the tools+system prefix — 3 arms x 12 samples is about
**$20**.

## Run ledger

Baseline before the block: `main` `4a4faba`, coordinator-run suite
**2804 passed / 99 skipped / 2 warnings** in 156.5 s (2026-09-04,
`.venv/bin/python -m pytest -q`).

| block | branch | start | implementation | gate | merge |
|---|---|---|---|---|---|
| 74a | `design74/lock-refuses-image-sweep` | `4a4faba` (2026-09-04), worktree `../microclaw-74a` | | | |
| 74b | | | | | |