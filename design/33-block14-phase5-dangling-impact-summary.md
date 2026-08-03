# Block 14 Phase 5 — dangling-work impact summary

Date reviewed: 2026-07-30

Source: `design/26-29-32-33-implementation-checklist.md`, cross-checked against
the relevant first-launch and follow-up sections in
`design/33-authorization-map.md`, with the later Nikon continuous-focus findings
in `design/34-nikon-pfs-tizdrive-findings.md` assessed as an additional input.

Line citations: a bare `:NNN` is `design/33-authorization-map.md`; the checklist
and design/34 are named explicitly. The original two sources were read at
`db2e039`. Citations naming a source file
(`microclaw/rig_inventory.py`, `tests/test_rig_inventory.py`) were read at the
same commit and are the authority wherever they disagree with the original
design sources.

## Conclusion

Most dangling work in the implementation checklist will not materially affect
`safety_config.yaml` or Block 14 Phase 5 (`design33/first-launch-setup`). **No
item is a documented blocker** — both original sources state Phase 5 is
unblocked. The later Nikon findings do not block a generic, fail-closed setup
workflow, but they do block treating a generated profile as sufficient for PFS
initialization or PFS-coordinated Z movement. Five things should nonetheless
become explicit Phase 5 inputs or acceptance criteria:

1. Treat Block 9b's inventory as versioned rather than frozen, and validate the
   payload's `schema` with the producer-owned
   `microclaw.rig_inventory.validate_inventory_schema` contract rather than
   duplicating an inline producer string. Both source
   documents say Phase 5 is unblocked — the checklist states Phase 5 "depends
   on Block 9b's inventory, which shipped, so it is unblocked whenever wanted"
   (checklist `:1338`), and design/33 says the format is not frozen but "the
   schema is versioned so a finding can bump it" (`:439`). Phase 5 should declare
   the exact supported version set and refuse an unrecognized version, not wait
   for the cross-rig gate — which needs a second live rig and could otherwise defer
   Phase 5 indefinitely. The former producer/design version mismatch is closed:
   design/33 now points to the producer-owned validator and supported set, and
   the producer emits from that same contract. The owed live
   credential-redaction check remains a Block 9b inventory-producer gate, not a
   Phase 5 safety input: the inventory already lands on disk, and Phase 5 should
   structurally prohibit copying observed current or allowed values into the
   safety profile regardless of whether they were redaction-checked. Do not,
   however, read the off-rig coverage as retiring that check. Redaction is a
   property-*name* regex (`_SECRET_NAME`, `microclaw/rig_inventory.py:39`), so
   `test_deterministic_order_hash_and_credential_redaction` proves the
   substitution fires — it cannot prove the vocabulary covers real driver naming
   (`Passphrase`, `Community String`, `Login`). That residual gap shares the
   broader limitation of synthetic coverage recorded at `:381`–`:388`, where an
   invented `get_device_adapter_name` kept the suite green against an API CMMCore
   does not have, but it is not the same failure class.
2. Resolve the undeclared-light-source vulnerability **and** make explicit
   classification of every illumination candidate surfaced by discovery a
   Phase 5 acceptance criterion. This does not prove that discovery found every
   physical emission path. These are complements, not alternatives: the
   proposed fix is a startup cross-check of EMU laser enables against
   `illumination.shutters` (`:973`), which must refuse in guaranteed mode rather
   than merely warn. No Phase 5 profile can substitute for it — a hand-edited
   config, a stale one, or any rig never run through setup stays silently
   ungated.
3. Treat Phase 5's own enumeration as hardware contact, and state the ordering
   limit rather than implying setup precedes emission. design/33 `:363`–`:369`
   already refuses that promise: "even enumeration is hardware contact, and
   loading a Micro-Manager configuration may initialize devices," so setup mode
   promises only that no *agent- or tool-directed* action is possible before
   review, and owes the least-active connection path Micro-Manager supports plus
   documentation of any unavoidable device initialization. **This intersects
   item 2 and is the one place a dangling item collides with Phase 5
   physically.** Item 2 is a config-*completeness* problem, and its startup
   cross-check (`:973`) runs at startup — after Phase 5's enumeration. On a rig
   whose emission path is undeclared, Phase 5 may itself initialize and emit with
   no config yet in existence to gate it. No profile Phase 5 later writes closes
   that window; only the least-active connection path narrows it.
4. Make Phase 5 require a human decision for the unresolved M5
   `iChrome-MLE-TCP.Label`/`State` classification and for any equivalent
   ambiguous device property. It must not infer an approval. The declaration
   surface for that decision is `categorical_properties`, which is required in
   guaranteed mode and may be empty (`:476`) — so Phase 5 must emit the key even
   when every candidate is excluded or left unresolved.
5. Treat continuous focus as an unsupported typed capability until its runtime
   policy and rig probes exist. In particular, Phase 5 must not approve
   `TIPFSStatus.State` (or an equivalent autofocus enable) as an ordinary
   categorical `On`/`Off` property. The Nikon findings require explicit enable,
   lock, failure, timeout, and Z-movement coordination semantics
   (design/34 `:139`–`:178`), while the current schema expresses none of them.
   Phase 5 should preserve the observed relationship among the core focus stage,
   autofocus device, and offset stage as a question for review, then exclude or
   leave unresolved the continuous-focus mutation surface. It must also not copy
   the observed 2440–2450 um engagement position into a profile: safe approach
   bounds may vary with objective and sample holder (design/34 `:223`–`:234`).

The remaining items can be deferred without destabilizing a generic Phase 5,
provided Phase 5 fails closed on unsupported or ambiguous cases. On a rig with
continuous-focus hardware, Phase 5 must leave continuous-focus control and
coordinated Z movement unresolved or excluded until a typed capability and a
rig-verified policy exist. Producing a profile does not by itself make PFS
initialization or offset movement safe.

## Dangling-work impact table

| Dangling item or note | Likely `safety_config.yaml` change? | Effect on Block 14 Phase 5 | Recommendation |
|---|---|---|---|
| **Block 9b cross-rig inventory gate — credential redaction limb:** not live-tested because M2 has no credential-like properties (`:437`) | **No, if Phase 5 observes its boundary** | **Low.** The inventory already lands on disk, and Phase 5 has no reason to transfer observed current or allowed values into safety policy. Live-driver validation remains genuinely owed: redaction keys off a property-*name* regex (`rig_inventory.py:39`), so off-rig tests prove the substitution fires, not that the vocabulary matches real driver naming. | Keep the live check in Block 9b. Structurally prohibit Phase 5 from copying observed current or allowed property values into `safety_config.yaml`; copy only structural identifiers and explicit operator decisions. |
| **Block 9b cross-rig inventory gate — remaining three limbs:** real enumeration failures, live groups/state labels, bridge-typed returns | **Inventory-schema change, not safety-schema** | **Medium.** Could bump the inventory schema or change the questions Phase 5 presents. But both documents say Phase 5 is unblocked (checklist `:1338`), and the schema is versioned precisely so a finding can bump it (`:439`). | **Do not block Phase 5.** Validate the payload's `schema` against a shared supported-version contract, and refuse an unrecognized version. Do not hand-copy a version literal from prose: `:392` still says `v1` where the producer emits `v2` (`rig_inventory.py:380`). Requiring the full gate needs a second live rig and could defer Phase 5 indefinitely. |
| **Undeclared light-source vulnerability:** authorization can permit a laser enable when `illumination.shutters` omits it | **Likely profile-content change; possibly validation/schema change** | **High.** Phase 5 must ensure every illumination candidate surfaced by discovery is explicitly classified; otherwise it could generate an unsafe but apparently complete profile. This is not proof that every physical emission path was discovered. | Do **both**: land the startup EMU-enable/`illumination.shutters` cross-check (`:973`) and fail closed in Phase 5 until the operator classifies every surfaced candidate. The startup cross-check must refuse in guaranteed mode; warning alone is acceptable only in an explicitly degraded mode. A profile cannot substitute for the runtime gate — hand-edited, stale, or non-Phase-5 configs stay ungated. Note the cross-check runs at *startup*, so it does not cover Phase 5's own enumeration; see the next row. |
| **Enumeration is itself hardware contact and may initialize devices** (`:363`–`:369`) | No | **High for setup-mode ordering, none for profile content.** Phase 5's enumeration can initialize — and on a rig with an undeclared emission path, emit — before any config exists to gate it. The row above's startup cross-check is downstream of this window. | Use the least-active connection path Micro-Manager supports, document every unavoidable device initialization, and state the ordering limit in setup text: no *agent- or tool-directed* action before review, not "no hardware contact before review." |
| **M5 `iChrome-MLE-TCP.Label`/`State` semantics are undocumented** | **Yes, M5 profile content** | **Medium-high.** Phase 5 must ask whether the declaration remains categorical, is excluded, or belongs under illumination. | Resolve through Phase 5's human-decision workflow. No generic schema change is currently indicated. |
| **Continuous-focus/PFS coordination is not modeled:** raw focus-stage writers neither coordinate with continuous focus nor verify enabled/locked state (design/34 `:83`–`:108`, `:122`–`:164`) | **Likely future schema extension; no safe current declaration** | **High on a PFS rig; not a blocker for generic Phase 5.** An autofocus enable is not merely categorical: safe behavior depends on lock/failure/timeout state and on the policy for every Z-moving path. Context-dependent approach ceilings may also exceed the current flat stage-range model. | Do not allow Phase 5 to put `TIPFSStatus.State` or an equivalent continuous-focus enable in `categorical_properties`. Preserve the focus-stage/autofocus/offset relationship from inventory, but emit exclusion or unresolved review instructions until a typed capability and rig-verified `require_off`, `move_then_rearm`, or true `preserve` policy exist. Do not copy the observed 2440–2450 um engagement position; require reviewed bounds for the objective/sample-holder combination (design/34 `:151`–`:182`, `:223`–`:234`). |
| **Asynchronous named-stage settling and missing Z read-back:** `move_named_stage` can report the previous target, while `move_stage_z` reports the requested target without measuring it (design/34 `:110`–`:120`, `:236`–`:274`) | **Possibly future settling-policy fields; bounds alone are insufficient** | **Medium.** Phase 5 can collect reviewed `TIPFSOffset` bounds, but cannot make an unsafe movement implementation reliable. A generated profile must not imply that an in-range command was achieved or settled. | Keep this as runtime capability work: return measured Z; poll named stages until within a configured target tolerance and stable or timed out; and, for PFS offset, observe the responding TIZDrive too. Until that lands, Phase 5 should mark PFS-offset workflows unsupported even if the offset has reviewed bounds. |
| **M5 acquisition budgets were copied from the fictional example, including the exposure limit** (`:790`) | **Yes, M5 values — but not through Phase 5** | **Low.** This is a defect of `microclaw init`'s copy-the-example path (`:350`) plus one deployed M5 config. Phase 5 — no inferred limits, always `reviewed: false` — *replaces* that path rather than inheriting it. | Fix the M5 config as its own rig-config review item. Optionally have Phase 5 detect unchanged example values, but that is net-new hardening, not dangling work Phase 5 must absorb. |
| **Phase 2 XY typed-actuator ambiguity; proposed `axis` field** | **Possible strict-schema change** | **Medium only for rigs exposing writable XY position properties.** Adding `axis` would change Phase 5 output. Current behavior over-refuses rather than under-refuses. | Decide before Phase 5 if these rigs are in scope. Otherwise refuse to generate ambiguous XY typed entries and defer the schema extension. |
| **Future typed MicroFPGA pulse-duration/dose actuator** (`:709`) | **Yes, a substantial future schema extension** | **Low for current Phase 5; potentially high later.** It would require a typed kind expressing level × duration and rig measurements. | Do not block Phase 5. Mark such controls unresolved or excluded; never invent bounds. |
| **Phase 3's human confirmation gate was never validated — "the probe self-confirms"** (`:796`) | No | **Medium for how Phase 5 is gated, not for what it emits.** The problems are related but not identical: Phase 3 concerns a runtime confirmation boundary, while Phase 5 is an operator workflow whose no-default, explicit-input, and unresolved-case behavior can largely be tested automatically. | Automate the mechanical workflow checks, and include an operator-driven rig transcript showing that unresolved choices cannot be silently accepted. Do not use a self-confirming probe as evidence of the human boundary. |
| **Camera ROI has no typed capability** (`:708`, `:831`) | **Yes, a future schema extension** | **Low now; same class as the MicroFPGA row.** The schema cannot express an ROI bound, so Phase 5 cannot emit one. | Do not block Phase 5. Emit exclusion or unresolved review text; never invent geometry bounds. |
| **Authorization refusals carry a misleading generic hardware-error hint** (`:827`) | No | **Low, but Phase 5 inherits it.** A setup flow that surfaces refusals to an operator will show "device busy / stage at limit / not found" for what is actually a policy refusal. | Fix in the separate error-reporting cleanup already noted; until then, Phase 5 should render refusals with its own wording rather than reusing `execute_tool`'s. |
| **Unconfirmed SignalIO/Galvo bridge type ordinals** (`:717`) | No | **Low, and mostly not Phase 5's.** The unexercised ordinal fallback lives in Phase 2's device-type-scoped refusal net, not in the inventory; the table's new region is published enum, not observed behaviour. | Exercise on a rig with a galvo or DAC when one exists. Phase 5's only obligation is to present an unrecognized device type as unresolved rather than classify it. |
| **`TTL.State0` GenericDevice false positive** | **Profile content only**, through `excluded_properties` | **Low.** Phase 5 may recommend exclusion but must require human confirmation. | Already representable; no schema work required. |
| **Channel preset colliding with a typed actuator is only tested off-rig** | No expected change | **Low.** Phase 5 should surface preset effects and typed-property collisions from the inventory. | Add setup coverage, but no prerequisite schema work is indicated. |
| **Configuration edits require restart** | No | **Direct Phase 5 UX requirement, already designed.** | Preserve the disconnect, manual review, and normal-restart flow. Do not hot-load the generated profile. |
| **Session dose ledger is not durable across restarts** | Possibly future policy work, but no current schema change is designed | **Low.** Phase 5 can explain the actual meaning of `max_session_illuminated_ms`; it cannot make the ledger durable. | Add clear setup text; do not block Phase 5. |
| **Mid-acquisition cancellation and abort trigger** | No expected change | None material | Defer; unrelated to profile generation. |
| **Partial-failure and restart/session ledger rig semantics** | No immediate schema change | Low | Explain the process-session boundary during setup; otherwise independent. |
| **UV activation closed-loop test is deferred** | Potentially profile values, not the existing schema | Low | Depends on the future pulse-duration capability; do not block Phase 5. |
| **Block 11 Run B generated adapter** | No expected change | None | Independent. |
| **Block 12 optional classifier and ROI spike** | No expected change | None | Independent. |
| **Block 13 hook-worker isolation** | No expected change | None | Independent. It blocks the combined closeout smoke test, not Phase 5. |
| **Missing spiral and 2500-tile fixtures** | No | None | Independent. |
| **Historical calibration-artifact authoring gap** | No | None | Independent. |
| **Context-compaction attribution observation** | No | None | Independent. |
| **Clean hook save is not actually enforced** | No | None | Independent security/containment fix. |
| **REPL API-key precedence mismatch** | No | None | Independent. |
| **`run_timelapse` lacks an artifact declaration** | No | None | Independent. |
| **Combined end-to-end rig smoke test** | No | Low | Valuable after the remaining relevant work, but it will not define Phase 5's configuration output. |

## Required Phase 5 behavior even without schema changes

Phase 5 should preserve the three separate inventory regions (`facts`,
`heuristic_candidates`, and `human_decisions`) and must not turn driver-reported
technical limits or heuristic relationships into safety decisions. In
particular, it should:

- require explicit operator classification of every illumination enable,
  emission, and power candidate surfaced by the inventory, without claiming
  that heuristic discovery exhausts every physical emission path;
- preserve the observed relationship among the configured core focus stage,
  autofocus device, and offset stage as a review question. Do not flatten them
  into independent controls or infer a continuous-focus movement policy;
- refuse to classify an autofocus or continuous-focus enable such as
  `TIPFSStatus.State` as a generic categorical property. Until continuous focus
  has a typed capability with rig-verified enable, lock, failure, timeout, and
  Z-movement semantics, emit an exclusion or unresolved review instruction;
- preserve duplicate percent/native-unit actuator representations as an
  unresolved choice rather than declaring both. Note this bullet predates
  Phase 2, which shipped `units: native` + `full_scale`: the mW variant is now
  expressible, so the operator's decision is *which* representation to declare,
  not "neither";
- require human-entered safety bounds. Phase 5 structurally cannot emit example
  values — it infers no limits and writes `reviewed: false` — so a check for
  unchanged example limits is defence-in-depth against the `microclaw init`
  path, not a Phase 5 obligation;
- never copy an observed focus-engagement or approach position into a safety
  bound. Where safe Z limits depend on objective or sample-holder context and the
  current schema cannot express that context, leave the policy unresolved rather
  than collapsing it into one apparently universal range;
- copy only structural identifiers and explicit operator decisions into the
  profile; never copy observed current or allowed property values from the
  inventory. This is an inherited design constraint, not a preference: Block 9b
  deliberately "emits no YAML aid at all" because a config-shaped derivative
  "invites an operator to mistake an observed property for a reviewed decision"
  (`:373`–`:378`). Phase 5 is the sanctioned place where inventory becomes
  config, so it carries that rationale rather than being exempt from it;
- emit exclusions or unresolved review instructions for actuator kinds the
  current schema cannot express;
- do not present reviewed bounds as proof that an asynchronous device achieved
  or settled at its target. PFS-offset use remains unsupported until the runtime
  path performs target-based polling with a timeout and observes the responding
  focus drive where required. **Clarified by checklist v2 Block 4c
  (2026-08-03): "unsupported" constrains what the profile may *claim*, not
  whether the stage may be declared. Setup authors the offset stage under
  `named_stages` — as this document's own row already allowed ("Phase 5 can
  collect reviewed `TIPFSOffset` bounds") and as Block 0b's Nikon worksheet
  already did — and carries the non-arrival caveat in its review notes. The
  continuous-focus *enable* remains excluded. See design/33 §"Block 4c
  landed".**;
- emit `categorical_properties` whenever guaranteed mode is in force, even if
  empty (`:476`), since that is the surface on which an ambiguous `Label`/`State`
  decision is recorded;
- use the least-active Micro-Manager connection path for enumeration, document
  every device initialization it cannot avoid, and describe the ordering
  guarantee accurately — no agent- or tool-directed hardware action before
  review, *not* no hardware contact before review (`:363`–`:369`);
- always write `reviewed: false`, disconnect, and require manual review followed
  by a normal restart; and
- never expose the agent, mutation tools, or inferred authorization during
  setup.

## Bookkeeping notes

Two stale lines, both of which a Phase 5 implementer would plausibly read as
current:

1. **Checklist closeout.** The final-closeout text saying Block 14 Phases 2, 4,
   and 5 were all unstarted (checklist `:1338`) is stale. The detailed Block 14
   section records Phase 2 merged at `47f6702` and Phase 4 at `5458483`
   (checklist `:1199`, `:1209`). Only Phase 5 remains unstarted, and the closeout
   line should say so.
2. **Inventory schema version (closed).** design/33 now points to
   `microclaw.rig_inventory.validate_inventory_schema` and
   `SUPPORTED_INVENTORY_SCHEMAS`; the producer emits from the same constant, so
   Phase 5 can refuse unsupported payloads without pinning a prose literal.
