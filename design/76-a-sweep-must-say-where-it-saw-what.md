# A sweep must say where it saw what

Closes register row **R91**. Settles LOCAL — the fixture is a real Nikon sweep
already on disk. No rig is needed to decide it or to gate it.

## Problem

A `stop_when_found` property sweep reports **which** values it observed and not
**where**. The caller has already paid for the whole window; the coordinates are
in the payload, but only as two parallel lists it has to index by hand.

Measured, on the Nikon Ti, 2026-09-04 (`design/76-r91-nikon-sweep.json`, that
session's own recorded tool result, verbatim). One sweep, 491 planes,
`[2110, 2600]` at 1 µm, asking for `["Locked in focus"]`, `converged: false`:

| planes | reading | measured Z |
|---|---|---|
| 0–276 | `Out of focus search range` | 2110.0 – 2385.0 |
| **277–290** | **`Within range of focus search`** | **2385.975 – 2399.0** |
| 291–490 | `Out of focus search range` | 2400.0 – 2599.0 |

**The sweep found the capture band exactly** — 14 contiguous planes, 13 µm wide
— and said so only as `observed ['Out of focus search range', 'Within range of
focus search']`. The model then tried to recover the position from the arrays,
wrote *"roughly indices 249–262 … around 2360–2375 µm"* — **wrong by about 20
µm** — moved to 2367 (out of range), swept `[2355, 2385]` at 0.5 µm for 61 more
planes that **missed the band's lower edge by one plane**, and spent a further
287 re-finding it.

**348 planes after the answer was already in the payload.** At this sweep's
floor — 9 serialized bridge round trips and ≥0.10 s of settle sleep per plane
(`sweep_autofocus`, `microclaw/autofocus.py:339-380`) — that is ~3,100 round
trips, ≥35 s of sleep, and three model round trips.

This is not the model being careless with an array. A 491-element list is not a
readable answer to "where was it in range", and the payload is the only thing
that can turn one into the other.

## Decision

### D1 — every property sweep reports its value spans

In `_sweep_payload` (`microclaw/tools.py:6260`), inside the branch that already
emits `readings`/`in_range` for a zero-exposure probe, add contiguous runs of
equal reading with the **measured** Z interval and the plane indices of each:

```python
payload["value_spans"] = [
    {"value": v, "z_um": [z_lo, z_hi], "planes": [i_lo, i_hi], "in_range": bool}
    for each contiguous run of equal reading
]
```

Measured Z, never requested — the two differ on every plane of the fixture, and
requested Z is what the model already mis-derived. On a matched sweep too, not
only a refusal: a caller that stopped early still wants to know how far into the
band it stopped.

### D2 — the no-match refusal names the coordinates

The `length == 0` branch of `_band_admit` (`microclaw/autofocus.py:137`) is the
one that fired on the Nikon, and prose is what the model actually read. It
currently ends `observed ['A', 'B']`. It must say **where** each was observed.

That needs the measured positions, which `_band_admit` does not receive.
**Extend the existing signature and the `FocusProbe.admit` contract; do not add
a second function** (`CLAUDE.md` §"Fold into what exists"). Three call sites
pass `sweep.metric_values` today (`autofocus.py:528`, `:562`, `:634`) and the
`SweepResult` is in scope at all three.

### D3 — the constant-reading branch gets nothing, deliberately

Its window returned **one** value; a span list would say "this value, the whole
window", which the sentence already says better. Three of the Nikon's eight
sweeps were exactly that shape and would gain nothing. Recorded so a later
reader does not "finish the job" by adding it.

### D4 — the exporter must inline whatever this adds

`_band_admit`, `longest_true_run` and `_strings` are in the standalone
exporter's inline list (`microclaw/tools.py:1029-1032`), and
`test_emitted_inline_defines_every_name_it_uses` enforces it. **A new helper
that is not added there makes every exported script raise `NameError` at runtime
on the rig** — the exact failure block 13 and 41b produced when both branches
were green alone. Prefer no new module-level helper at all; if there is one, it
goes in that tuple in the same commit.

### D5 — nothing about the sweep's control flow changes

No early stop, no new argument, no change to what is swept or admitted. The
summary is computed from lists the sweep already holds, so it spends no
exposure, moves no stage and makes no bridge call. **R92** (no coarse-to-fine)
and **R93** (a post-engage value asked of a disengaged lock) are separate rows
and stay out of this block — R93 may well close as unnecessary once this lands.

## Evidence

1. **The fixture is the rig's own payload**, `design/76-r91-nikon-sweep.json`:
   491 `readings` and 491 `measured_z_positions` extracted verbatim from that
   session's recorded tool result. Do not regenerate it, do not round it. The
   test asserts the three spans in the table above **exactly**, including
   `2385.975` and the plane indices `[277, 290]`.
2. **Assert the cost is zero**: no snap, no `set_position`, no `get_position`,
   no `get_property` beyond what the sweep already made. A pure function of two
   lists — assert it that way, not by inspection.
3. **Watch it fail.** This is a new field, so the pre-fix tree fails it
   honestly: `git checkout <before> -- microclaw/`, confirm the span assertion
   fails because `value_spans` is absent *and* the refusal string carries no Z,
   restore. (Not a regression test — `feedback_watch_it_fail_not_regressions`
   does not apply.)
4. **The exported script still defines every name it uses** — run
   `test_emitted_inline_defines_every_name_it_uses` and say so in the report.

## Blocks

**76a — the spans.** D1–D5 and their tests. `microclaw/tools.py`
(`_sweep_payload`), `microclaw/autofocus.py` (`_band_admit` and the `admit`
contract), and the exporter's inline tuple if a helper appears. Settles LOCAL.

**No rig gate.** Nothing here touches hardware, dose or motion, and the fixture
is a real rig's own recorded payload. The demo machine adds nothing a fixture
does not already give. The confirmation that matters is the next Nikon session's
history, scored the way `R88` was — which costs nothing and waits on nobody.

## Run ledger

Baseline before the block: `main` `fea3a89`.

| block | branch | start | implementation | gate | merge |
|---|---|---|---|---|---|
| 76a | `design76/sweep-value-spans` | | | | |
