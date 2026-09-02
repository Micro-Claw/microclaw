# A hardware write that raised leaves the device state unknown

Closes register rows **R50** (design/38 F12), **R60**, and **R51** (design/38
F13). All three are HIGH, all three settle off-rig, and the first two are one
defect at three call sites.

## Problem

Micro-Manager can raise on a property write **after the device has applied it**.
The acknowledgement times out; the value is on the rig. Microclaw currently
treats the exception as proof of the opposite at every site that has an opinion:

| site | what it does today |
|---|---|
| `tools.py:3527` `set_device_property` | bare `core.set_property`; the raw Java exception propagates, no read-back |
| `tools.py:10438` `set_emu_laser_power_percentage` | bare `core.set_property`; reads back on the **next** line — but only on the success path |
| `authorization.py:2013` channel executor | raises `"NO WRITE REACHED THE DEVICE, so no channel change was made"` |

The channel executor states the false premise twice more, as comments it acts
on: `authorization.py:1914` *"A set that \*raised\* never reached the device"*
and `:1946` *"A set that raised did not [reach the device]"*. That belief is not
cosmetic — it routes the rollback bookkeeping. A restore that fails is filed as
a loud `rollback_failures` when the write landed and a quiet `unrestored` when
it did not, and the discriminator is the premise above. So a write that landed,
raised, and could not be restored is reported today as *nothing changed*, in the
one case where something did.

Worst for illumination, which is where all three sites can end up: an operator
told a laser-enable failed may believe the laser is off when it is on, or retry
and double-apply.

## Evidence

**M5, design/38 G7.a** (`design/38-plus-acquisition-session-findings.md:283`).
`set_device_property` on `All: 3. TTL Enable` raised
`Cannot set property "All: 3. TTL Enable" to "1" [ Error in device
"iChrome-MLE-TCP": Serial timeout occurred. (17) ]`. The agent read the property
back and found `1`. **The write had landed and the error was the acknowledgement
timing out.** The agent handled it; nothing in microclaw made it.

**M5, Block 2 G4** (register `R60`). The same serial timeout 17 on a laser
enable, after which the agent told the operator the laser *"was not enabled"* —
a conclusion the exception did not support. A later retry read `1`.

**Design/32 Block 7b's gate caught this shape once already**, and design/52b
fixed it for exactly one path: `hook_decisions.py:961` marks
`baseline_stale = True` when an illumination write raises, and re-reads before
the next decision rather than trusting its cached belief. That is the correct
behaviour, implemented once, in the one place a hook could reach. The three
sites above are the rest of it.

## What is already right — do not re-fix

- **The agent prompt's certainty rule.** `agent.py:137` already says *"Hardware
  state has to be read, not assumed. Never tell the user illumination was off, a
  shutter was closed, or a laser was never enabled unless `get_system_state`
  reported it."* R60's agent half is closed by that line; R60's remaining work is
  the code message only.
- **The illumination ratchet.** `set_device_property` calls `check_illumination`
  with `previous_percent=None`, which reads the live property
  (`safety.py:1249`). A landed-but-raised power write does not poison the
  ratchet — the next write re-reads. No fix owed there.
- **`shutter_declared_illumination`.** `guard.shutter_all` deliberately swallows
  per-device failures so one dead shutter cannot stop the others, and the tool
  returns both `attempted` and `shuttered`, so the set difference already
  discloses which shutters did not report success. Checked, excluded.
- **Success-path verification.** design/53 settled that read-back verification is
  **plan-level, not per-write**. Nothing here adds a read to the success path.

## Decision

### D1 — Read back on the exception path only, and compare to the requested value

One extra value read, only when a write has already failed. Comparing that value
may also require a `get_property_type` bridge round trip. No pre-read: the
question is not *"what was it before"* but *"is the requested value on the device
now"*, and one post-exception value read answers that. The success path is
untouched, so no normal write gets slower.

The comparison must use the existing tolerance rule, not `==`: a Float property
that reads back `"1.0000"` for a requested `"1"` has landed.
`authorization.py:1780` `_verify_property` already encodes this. Extract a pure
`_property_values_equal(property_type, actual, expected)` comparison and share
it; do not write a second one. `_verify_property` keeps its existing value read
and error message, resolves the type once, and passes the captured values to the
comparison. The failure-path helper must likewise compare the `actual` it
already captured, not call another read helper and read the property a second
time.
One read is one observation; two reads could disagree and would make the report
and its `value` field internally inconsistent.

Resolve `_property_type_name` exactly once for each diagnostic read and retain
the resulting type name beside `value`; executor refinement reuses it for both
the original and requested comparisons. The type lookup is a bridge round trip
and can itself raise because the device has just errored. An unresolvable type is
not evidence about the value, so it degrades to `unknown`, never to a string
comparison — a Float read back as `"1.0000"` against a requested `"1"` would
otherwise compare unequal and could be misclassified as unchanged. Resolving
once also prevents a first comparison from succeeding and a second type lookup
from failing during executor refinement.

```python
# authorization.py, beside _verify_property.

def read_back_after_failed_write(core, device, prop, requested):
    """Disambiguate a write that raised. Never raises."""
    try:
        actual = str(core.get_property(device, prop))
    except Exception as exc:
        return {
            "outcome": "unknown", "value": None, "property_type": None,
            "sentence": (f"{device}.{prop} could not be read back after the failed "
                         f"write ({_clean_exception_message(exc)}), so whether the "
                         f"value landed is UNKNOWN; read it once the link recovers."),
        }
    try:
        property_type = _property_type_name(core, device, prop)
    except Exception as exc:
        return {
            "outcome": "unknown", "value": actual, "property_type": None,
            "sentence": (f"{device}.{prop} reads {actual!r}, but its property type "
                         f"could not be resolved ({_clean_exception_message(exc)}), so "
                         f"whether that is the requested value is UNKNOWN."),
        }
    if _property_values_equal(property_type, actual, requested):
        return {
            "outcome": "landed", "value": actual, "property_type": property_type,
            "sentence": (f"write_reported_failure_but_value_changed: {device}.{prop} "
                         f"reads {actual!r}, the requested value, so the state you "
                         f"asked for is on the device now. Do not retry."),
        }
    return {
        "outcome": "requested_value_not_observed", "value": actual,
        "property_type": property_type,
        "sentence": (f"{device}.{prop} reads {actual!r}, not the requested "
                     f"{requested!r}. The requested value is not currently present; "
                     f"this read alone cannot prove whether the write changed the "
                     f"property before the error."),
    }
```

`_verify_property` reads `actual` and `property_type`, calls
`_property_values_equal`, and retains its existing failure message using that
captured `actual`; it must not read again merely to format the error. Tests
assert that the exception-path helper makes exactly one `get_property` call and
at most one `get_property_type` call, including the Float-equivalence case
(`"1"` requested, `"1.0000"` read).

### D2 — Three outcomes from a snapshot, four once the executor adds its baseline

`landed`, `requested_value_not_observed`, `unknown`. The middle outcome says
only what the snapshot establishes: the requested value is not present now. It
does **not** say that the write never landed, never changed the property, or made
no change. A device may clamp `"1"` to `"0.5"`, partially apply a command, or
revert after applying it. The standalone tools have deliberately taken no
pre-read, so they have no baseline against which to distinguish those cases.
`landed` is an observation on the same terms: it says the requested value is
present, not that this write is what put it there. Lacking `originals`, the
tools cannot see the overlap case the executor handles below — a property that
already held the requested value reads identically to one the failed write
changed. The state the caller asked for is on the device either way, which is
what makes "do not retry" sound advice and a causal claim unnecessary.

The channel executor is different: it already captured `originals` before the
plan. It refines the snapshot helper's result for its failed write using the
captured `value`, even when the helper called that value `landed`:

- if the read-back equals that write's saved original, it is `at_original` and
  needs no restore — this test comes first, including when original and requested
  are equal;
- otherwise, if it equals that write's requested value, it is `landed`;
- if it equals neither, it is `changed_unexpectedly` and is treated as possibly
  landed;
- an unreadable value or unresolved type remains `unknown` and is treated as
  possibly landed.

Both comparisons use `_property_values_equal` with the retained
`property_type`; raw string equality and a second bridge lookup must not silently
replace the Float tolerance rule. Refinement itself performs no I/O and cannot
raise because of device communication.

**`at_original` is deliberately a present-state observation, not a historical
claim.** The write may have landed and the device reverted, or something outside
microclaw may have restored the original between the write and the read — not
hypothetical, since microclaw must open mid-session and is never assumed to be
MM's only client. Both nevertheless leave the property in the observed pre-plan
state. The executor therefore marks that exceptional entry already restored and
skips it rather than issuing a redundant write that could create a new
ambiguity. This is the strongest positive criterion available for staying quiet;
without it every serial timeout goes loud and the M5 2026-08-06 finding is
reinstated. The operator-facing sentence reports only the observation: the
property holds its pre-plan value.

### D3 — A raised write still raises, carrying the read-back

`set_device_property` and `set_emu_laser_power_percentage` catch, read back, and
raise a stable Python `RuntimeError` containing the cleaned original message and
the appended `sentence`, chained with `from exc`. Do not try to reconstruct an
arbitrary Java exception type with a new message. A `landed` outcome does **not**
become a success return: the device is in a state we did not confirm healthy,
and the caller must see that the requested value is present *and* that the device
errored.
At both sites, the `try` covers **only** `core.set_property`. In
`set_emu_laser_power_percentage`, the existing success-path `get_property` stays
outside that catch: if the set returns and the subsequent read raises, that is a
failed success-path verification read, not a write that reported failure. Do not
run the exception-path helper a second time or attach the landed-write token in
that case.
After the diagnostic read, each tool attempts `ctrl.refresh_gui()` because the
write may have landed; a refresh failure is swallowed so it cannot replace the
hardware error that describes the uncertain write. `execute_tool` already turns
the raised message into a model-visible `error`, so no new plumbing or dedicated
exception type is needed.

### D4 — The channel executor refines the read-back against its saved original, and rollback believes it

Only when `accepted < len(attempted)` — that is the discriminator for "the last
attempted write raised", and it correctly excludes a cancellation between writes,
which raises with nothing outstanding. The read-back must run **before** the
rollback loop, which rewrites `originals` and destroys the evidence.

Then:

- For the exceptional write, compare the captured read-back with both `originals[index]` and the requested value before rollback, **in that order**. If it matches the saved original (`at_original`), mark that entry already restored and do not issue a redundant restore write. This precedence matters when original and requested are equal: rollback already has its desired state, regardless of whether the raised write landed. The diagnostic read has observed that state; another write could itself land unexpectedly and raise, invalidating the observation. Every other outcome — `landed`, `changed_unexpectedly`, `unknown` — is restored as today, and any failure of that restore is a `rollback_failures` entry raising `ChannelPlanSafeStateError`. A write known to have landed, changed to a third value, **or whose state could not be resolved**, whose restoration then could not be verified either, is exactly what **SAFE STATE NOT VERIFIED** means.
- **Do not rename `landed = index < accepted` to `possibly_landed`. Delete it.** Its only consumers are the two `(rollback_failures if landed else unrestored)` selectors (`authorization.py:1957` and `:1966`), and the bullet below removes the quiet half. Once the one quiet case is handled by *not attempting* the restore, every restore failure that can still occur sits on an entry that definitely landed (`index < accepted`) or possibly landed, so all of them are loud and there is no selector left to compute. Both loops lose it — the restore loop and the verify-the-restore loop. A `possibly_landed` variable that nothing reads is the dead machinery this block is otherwise removing.
- the `accepted == 0` branch stops asserting `NO WRITE REACHED THE DEVICE, so no channel change was made` unconditionally and reports the refined read-back result instead. Only `at_original` may report that the property holds its pre-plan value; `landed`, `changed_unexpectedly`, and `unknown` say exactly what was observed or could not be established. No branch concludes that the plan changed nothing.
- the skipped entry is **not** reported in `rolled_back`, which would claim a restoration nobody performed. Report it as observed already at its pre-plan value. The distinction is the whole subject of this block, one field over.
- **`unrestored` becomes unreachable, so delete it** along with its message clause. It was populated only when a restore failed on an entry believed not to have landed; after the skip, the only entry that can be classified `at_original` is the exceptional one, and that one is never restored or reported as restored. Its message clause already argued for the skip — *"that restore only rewrites the value the property already held"* — and having made that the behaviour, the clause has nothing left to describe. A runner who believes the category still has a reachable case must name that case rather than keep it on the chance that it does.
- the comments at `:1914` and `:1946` are rewritten to the real rule.

**This does not reinstate the M5 2026-08-06 defect**, and a reader who knows that
history will think it does. That defect was `SAFE STATE NOT VERIFIED` raised
*while simultaneously asserting nothing had changed* — one false certainty
justifying the loudest available error. The rule above is loud when two direct
operations fail: the diagnostic read cannot establish what the property holds,
and restoration cannot establish the original value either. That is what an
unverified safe state is.
The 2026-08-06 case stays quiet, but by a different route than the one that was
built for it: that device still answers reads **with the saved original value**,
giving a positive `at_original`, so its restore is never issued and there is no
failed restoration left to classify quietly or loudly. The doomed second write
that produced the original misleading error is simply not made. Merely answering
with some value other than the request is not enough to earn that.

### D5 — Two prompt lines (R51, and what D1–D4 need the agent to do with the report)

In the same bullet list, `agent.py:137`–`149`:

- extend the certainty bullet: a tool error naming `write_reported_failure_but_value_changed`, reporting an unexpected value, or reporting the read-back as unknown is **not** a report that the write failed to take effect. Relay it as it came.
- extend the read bullet beyond blank frames (**R51**): before ending a session or handing off, call `get_system_state` and report `declared_illumination_properties`. If that call fails, say that final illumination state could not be verified; do not omit it or infer a state. The field exists (`tools.py:4030`); on M5 G7.c the agent said *"I can't confirm the illumination state on my own"*, which was already untrue.

### D6 — The emitted script must not say nothing happened where the record says otherwise

*A new capability is not finished until it can appear in an exported script.*
This block changes what a failed write records, and `_recorded_outcome`
(`tools.py:794`) reads that record structurally: any top-level `error` returns
`("nothing", ...)`, so `export_session_script` emits

```
# SKIPPED: set_device_property — the recorded call did not succeed: write_reported_failure_but_value_changed: ... reads '1', the requested value ...
# The session completed nothing here, so neither does this script.
```

— two adjacent lines, the second contradicting the first, in the artifact the
user walks away with.

**Keep the routing.** `_recorded_outcome`'s docstring already anticipated this
(`tools.py:827`): `"nothing"` is *not* a claim that no hardware moved, and not
retrying a failed write is the safer of the two errors. Promoting this to
`"partial"` would hard-refuse the whole export, and that document's own demo
gate settled that failed calls are ordinary and refusing on them makes the
export useless on real sessions. **Change only the second line**: when the
recorded reason carries the `write_reported_failure_but_value_changed` token,
emit:

```
# The requested value was observed on the device after the failed write; this script deliberately does not repeat that uncertain call, so replay may begin from a different device state.
```

This reports the observation without claiming that the failed write caused it,
and tells the reader why the standalone script does not reproduce the call.

That token is fixed vocabulary from design/38 F12, written by exactly one code
path, so match that literal token in the recorded reason. This is not the
prose-parsing `_recorded_outcome` forbids — its rule exists to stop `status`
strings being parsed. Do not expand this block by changing `execute_tool`'s
record shape.

**D6 is part of 72a.** It is the defect's shadow in the exporter and is required
by test 14 and the block acceptance below.

## Why a green suite missed this — the fake asserts the premise

`tests/test_channel_plan_executor.py:69`:

```python
def set_property(self, d, p, v):
    self.write_count += 1
    self.calls.append(("set", d, p, str(v)))
    if self.fail_on == self.write_count: raise RuntimeError("injected write failure")
    if self.fail_rollback == (d, p, str(v)): raise RuntimeError("injected rollback failure")
    self.values[(d, p)] = str(v)          # <- never reached on a failure
```

Every failure this file can inject raises **before** the assignment, so a write
that lands and then raises is unrepresentable in it. The suite then asserts the
assumption outright: `:339` `assert core.values == {(d, p): "old" ...}`, and
`:198`, `:239`, `:298`, `:413` do the same. That is *a fake that encodes your
assumption is not a test of it*, in the file that owns the defect.

**So the fix starts in the fake.** Add a failure mode that applies the value and
*then* raises — the serial-timeout shape — and the existing tests keep their
current mode unchanged.

## Tests

Tests 1–4, 6–8, and 10–14 are watched failing on the pre-fix tree for their
stated reasons (`git checkout <before> -- microclaw/`, run, restore). Tests 5
and 9 are deliberate before-and-after regression guards: their boundaries are
already correct and this block must preserve them.

**Prove those two bite, by mutation rather than by watching them fail.** A guard
that passes on every tree is not yet evidence that it guards anything, and their
subject is structural, so a pre-fix run never reaches the assertion. For test 5,
widen D3's `try` to cover the success-path `get_property` and confirm it fails;
for test 9, drop the `accepted < len(attempted)` condition and confirm it fails.
Restore both. This is the repo's standing rule for order- and structure-shaped
tests, and it is what keeps a limb that cannot fail from counting as a criterion.

1. `test_failed_property_write_that_landed_reports_the_read_back` — `set_device_property`, fake applies then raises; the raised message contains `write_reported_failure_but_value_changed` and both values. Pre-fix: the raw Java message only.
2. `test_failed_property_write_with_requested_value_not_observed_says_what_it_read` — same tool, fake raises without applying; message names the value read and says only that the requested value is not currently present. It does **not** claim that the write never landed or made no change.
3. `test_failed_property_write_with_unreadable_device_reports_unknown` — read-back also raises; message says UNKNOWN. Proves the helper cannot itself raise out of the tool.
4. `test_failed_laser_power_write_that_landed_reports_the_read_back` — `set_emu_laser_power_percentage`, the illumination case the evidence is about.
5. `test_failed_laser_success_path_read_is_not_reported_as_a_failed_write` — `set_emu_laser_power_percentage` has `set_property` return and its following success-path `get_property` raise; the raw read failure propagates without a diagnostic retry or `write_reported_failure_but_value_changed`. This passes before and after and fixes the boundary of D3's `try`.
6. `test_channel_plan_first_write_landed_then_raised_does_not_claim_no_change` — `accepted == 0`, applies-then-raises; `NO WRITE REACHED THE DEVICE` absent, read-back present. Pre-fix: the string is there and is false.
7. `test_channel_plan_landed_write_that_cannot_be_restored_is_a_safe_state_failure` — applies-then-raises **and** refuses the restore; expects `ChannelPlanSafeStateError`. Pre-fix: `ChannelPlanError` with `unrestored`, the quiet misfile. This is the behavioural half of D4 and the reason it is not a wording change.
8. `test_channel_plan_first_write_rejected_but_read_back_is_original_skips_restore` — revise the existing 2026-08-06 M5 test: replace its `NO WRITE REACHED` assertion with the observation that the property holds its pre-plan value, keep the ordinary `ChannelPlanError`, and assert there is **no second `set_property` call** for the exceptional entry. **State in the test why it stays quiet**: its fake overrides `set_property` only, so the device still answers the diagnostic read with the saved original; that observation makes a restore unnecessary. Its docstring currently says the device *"refuses this property in both directions, as a dead link does"*, which its own fake does not do — a real dead link refuses reads too and is test 10. Fix the docstring rather than the fake; the two tests are the two halves and both are real. And **its `assert "could not be restored either" in message` must go**: under D4 the restore is never attempted, so there is no failed restore left to report.
9. `test_cancellation_between_writes_does_not_read_back` — the `accepted == len(attempted)` guard; asserts no extra `get_property` call. This passes before and after.
10. `test_channel_plan_unknown_write_that_cannot_be_restored_is_a_safe_state_failure` — the write raises, its diagnostic read raises, and its restore raises; expects `ChannelPlanSafeStateError`. Proves `unknown` cannot avoid the loud rollback-failure path.
11. `test_failed_property_write_with_unresolvable_type_reports_unknown` — `get_property` returns a value but `get_property_type` raises; the helper retains the observed value, reports UNKNOWN, and does not fall back to string equality.
12. `test_channel_plan_unexpected_changed_value_that_cannot_be_restored_is_a_safe_state_failure` — original is `"0"`, requested is `"1"`, the raised write leaves `"0.5"`, and restore fails; expects `ChannelPlanSafeStateError` and reports `changed_unexpectedly`. Proves that “different from requested” is not treated as “unchanged.”
13. `test_channel_plan_original_equal_to_requested_is_at_original_and_skips_restore` — original and requested are both `"1"`, the write raises, and read-back is `"1"`; asserts `at_original`, no restore write, and exactly one `get_property_type` call for the diagnostic. Proves that matching the original takes precedence over the snapshot helper's `landed` result in the overlap case and that refinement reuses the resolved type.
14. `test_exported_script_does_not_claim_nothing_happened_for_a_landed_write` (`tests/test_session_script_export.py`) — export a session whose recorded `set_device_property` carries the landed token; the emitted script must not contain "The session completed nothing here" and must contain "The requested value was observed on the device after the failed write" plus "this script deliberately does not repeat that uncertain call". Pre-fix: it emits the contradictory old sentence directly under a comment saying the requested value was observed.

Tests 1–4 and 11 also assert exactly one diagnostic `get_property` call. Test 1 uses a
Float property requested as `"1"` and read as `"1.0000"`, proving the shared
tolerance comparison without allowing a second observation.

## Gate — one demo-machine session

The code half is settled by tests 1–14; a rig cannot add to it, and **the demo
machine cannot produce the landed-then-raised case at all** — MMCore rejects an
illegal value before dispatch, and a legal one is applied. Do not write a limb
that pretends otherwise; that case is closed off-rig and by the two M5
observations already recorded.

What the demo session is for is D5, which is agent behaviour and has no unit
test:

- **Limb A (R51).** Drive an ordinary short session, then ask the agent to hand off or end. Score: did it call `get_system_state` and report `declared_illumination_properties`, without being asked for it by name? The demo config declares illumination in its safety config, so the field is present. If `get_system_state` itself fails, the correct scoreable response is that final illumination state could not be verified; silence or an inferred state fails the limb.
- **Limb B (no regression).** An ordinary successful `set_device_property` reports exactly as it does today — no read-back, no extra round trip.
- **Limb C, NOT EXERCISED by construction.** Record that the landed-then-raised path did not run and why. A limb that cannot run its mechanism is never a pass.

Per `CLAUDE.md` §6: **replay Limb A's operator prompt against a recorded payload
before the runbook ships** — R51 is a one-line prompt change and a mis-worded
question would score it wrong, which is exactly what cost design/59 three demo
rounds. And **establish the literal interpreter and command line from the demo
gates that already ran** (`design/58-block58d`, `design/69a-gate.md`) rather than
writing a new one.

## Blocks

### 72a — read back on the failure path, and two prompt lines

One block. `authorization.py`: `_property_values_equal` +
`read_back_after_failed_write`, the
executor's failure path (D4), the two false comments. `tools.py`: the two write
sites (D3) and the skipped-call comment (D6). `agent.py`: D5. `tests/test_channel_plan_executor.py`: the
applies-then-raises fake, plus tests 6–10 and 12–13; tests 1–5 and 11 go where those tools are
already tested (`tests/test_tools.py`, `tests/test_typed_actuators.py`), and
test 14 in `tests/test_session_script_export.py`.

Acceptance: the fourteen tests above; tests 1–4, 6–8, and 10–14 watched failing
on the pre-fix tree; tests 5 and 9 passing before and after as explicit regression
guards, each shown failing under the mutation named above; the full suite; and a
diff review confirming no read was added to any success path.

## Scope — what is deliberately not folded in

- **R57** (a full disk reported as a hardware fault). Adjacent theme, different
  mechanism: error *classification* in `errors.py:202` plus a free-space
  preflight. It would double this block and shares no code or test with it.
- **R28** (`laser_slot`'s pre-flight guarantees less than its schema sells).
  Same family of overclaiming, but a schema-wording fix in a different tool.
- **R42** (a model-invented rule overrode an operator instruction) and **R79**
  (saved knowledge does not separate measurement from inference). Prompt- and
  schema-shaped, no overlap with these call sites. R79 is LARGE.
- **R60's agent half**, already closed by `agent.py:137` — recorded above so the
  register row can be struck with a reason rather than a tick.

## Run ledger

| block | branch | start commit | assigned | merged |
|---|---|---|---|---|
| 72a | — | `49b3c74` | — | — |
