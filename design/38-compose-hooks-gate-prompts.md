# M5 rig gate — composed hooks and autofocus sweep dose

Branch: `codex/compose-hooks-autofocus-dose`.
Sample: the bead slide, or equivalent.

**Dose warning, read this first.** Unlike previous gates, the sweeps dominate.
Five positions with autofocus at `z_range_um=10, z_step_um=1.0` is 5 stored
frames plus **70 sweep exposures**. That dose was always being taken — this block
is what makes it *visible and reserved* — but do not run a wide sweep on
anything precious. Keep `z_range_um` at 10 or below unless you have reason not to.

## H0 — pin and sanity

```
git fetch origin
git checkout codex/compose-hooks-autofocus-dose
git pull
git merge-base --is-ancestor a0305eb HEAD
if ($LASTEXITCODE -eq 0) { "PIN OK" } else { "PIN FAILED - STOP" }
```

Then:

```
pip install -e . > h0_install.txt 2>&1
python -m pytest -p no:cacheprovider -q > h0_tests.txt 2>&1
```

Expected: `PIN OK`, suite green. Send both files.

## H1 — the thing G2 could not do (the block's whole claim)

G2 autofocused across a five-tile plus and produced one dataset, but the mosaic
had to be built afterwards because only one hook could run. Now both should run
in a single acquisition.

Arm the rig as usual (640 nm, TTL armed — see design/38's G1.a table). Then ask
microclaw:

> Acquire a 5-tile plus centred on the current position with a half-FOV step,
> using the 640 nm laser. Autofocus at each field with a 10 µm range and 1 µm
> step, and stitch the tiles in the same acquisition — I want the autofocus hook
> and a mosaic-stitching hook composed in one run, not two acquisitions. Save to
> `D:\SSD\gate_h1`.

Expected:

- **One** acquisition and one dataset with a `position` axis. If the agent runs
  two acquisitions, that is a failure of this gate — report it.
- `hook_strategy` is a **list**, e.g.
  `["autofocus_per_position", "<a stitcher>"]`.
- The hook log contains **both** kinds of record, each carrying a
  `hook_strategy` field naming which hook produced it. Autofocus entries with
  `best_z_um`/`converged`, and stitcher entries alongside them.
- A mosaic artifact is emitted by the run itself.

Record: the dataset path, the log path, and the reservation fields in the result
(`reservation_frames_planned`, `hook_extra_exposures_planned`).

**Sanity-check the numbers.** With 5 positions at `z_range_um=10, z_step_um=1.0`,
the reservation should plan 5 stored frames plus 14 sweep planes per position —
75 total, 70 extra. If the reported extra is 0, the sweep dose is not being
reserved and this gate fails.

**Open the mosaic and look at it.** Same check as design/38 G3: beads in the
half-FOV overlaps must superimpose, not reflect across the diagonal. A
transposed affine still produces a plausible-looking wrong mosaic.

## H2 — the reservation actually binds before the stage moves

This is the point of counting sweep dose: an acquisition that cannot fit its
budget must be refused **before** any hardware moves.

Ask microclaw:

> What is my current acquisition frame cap? Then try the same 5-tile plus with a
> 40 µm autofocus range and 0.5 µm step, and tell me what happens.

At 40 µm / 0.5 µm the sweep is far larger, so unless your cap is very high this
should be **refused during planning**. Expected: a refusal that names the frame
budget, with **no stage movement and no exposure**. Confirm the stage did not
move (`get_xy_position` before and after, or watch it).

If your cap is high enough that it is accepted, say so and skip — do **not** run
it. Report your cap so we can tell whether the check was exercised.

## H3 — the deprecated tool still works, and says so

Ask microclaw:

> Use `run_multiposition_with_autofocus` directly on two positions with a 10 µm
> range and 1 µm step, saving to `D:\SSD\gate_h3`.

Expected:

- It runs and returns a **deprecation note** telling you to use
  `run_multiposition_acquisition(hook_strategy='autofocus_per_position')`.
- It writes **one dataset with a `position` axis**, not one folder per position.
  That change is deliberate and approved — confirm it happened, and confirm the
  note says so.
- Autofocus still ran per position.

Dose: 2 frames + 28 sweep planes.

## H4 — leave the rig as you found it

`All: 3. TTL Enable = 1`, all four `Use TTL = 1`,
`Laser 1: 6. Status = AVAILABLE ENABLED USETTL`.

Note the exit report on the way out and paste it — with the design/38 change,
microclaw writes nothing on exit and only tells you what is still on.

## Return kit

- `h0_install.txt`, `h0_tests.txt`
- Session histories for H1–H3
- The H1 hook log and mosaic artifact
- The reservation fields from H1 and your frame cap from H2
- Anything that surprised you, in your own words

---

# Second gate round — H5–H7

H1's composition and dose reservation **passed**; H3 passed. What failed was the
mosaic getting written *by the acquisition*, twice, for two different reasons —
both now fixed. This round proves the inline path and the two refusals.

Re-pin:

```
git fetch origin
git checkout codex/compose-hooks-autofocus-dose
git pull
git merge-base --is-ancestor 13f5bf3 HEAD
if ($LASTEXITCODE -eq 0) { "PIN OK" } else { "PIN FAILED - STOP" }
pip install -e . > h5_install.txt 2>&1
python -m pytest -p no:cacheprovider -q > h5_tests.txt 2>&1
```

## Read this before you start: two of your saved hooks will now refuse

Saved hooks are re-checked against the current contract when they load, not only
when they are saved. Running that check over the copies of your M5 hooks in this
repo's fixtures:

| Hook | Verdict |
|---|---|
| `filament_position_filter` (legacy + migrated) | loads |
| `mosaic_cell_counter` (legacy + migrated) | loads |
| `uv_activation`, `uv_activation_wind_down` | loads |
| `mosaic_stitcher` (legacy) | loads |
| `mosaic_stitcher_rot` (legacy) | loads |
| **`mosaic_stitcher` (migrated/v2)** | **REFUSES** — `EmitArtifact` first positional argument is not provably a string |
| **`mosaic_stitcher_rot` (migrated/v2)** | **REFUSES** — same |

**Corrected 2026-08-12 (block 45), and the original wording was wrong in both
halves.** It said these two carried a *reversed* `EmitArtifact` and "would have
run, exposed the sample, and failed on the final tile." Neither is true. The
sources say `EmitArtifact(self.filename, canvas)` — the documented
`(filename, payload)` order, not reversed — and
`test_stitcher_migrations_preserve_canvas_and_parent_writes` shows they write a
correct `mosaic.tiff`, so nothing would have failed on the final tile. They
refuse because `_hook_contract_analysis`'s `provably_string` gate deliberately
rejects any two-positional `EmitArtifact` whose first argument is not statically
a string, and `self.filename` is an attribute. **M5's own registry confirmed this
on 2026-08-12**: `mosaic_stitcher_v2` and `mosaic_stitcher_rot_v2` refuse with
the `provably_string` contract text and nothing resembling a swap.

**They need re-saving in the keyword form,
`EmitArtifact(filename=self.filename, payload=canvas)`,
before they can be used again** — ask microclaw to do it, as it did for
`plus_mosaic_stitcher` during the H1 round.

If your rig's registry differs from these fixtures, the survey in H6 is the
authoritative one.

## H5 — the mosaic must be written BY the acquisition

The one claim this block has not yet demonstrated on hardware. Same five-tile
plus as H1, same autofocus, but the mosaic must come out of the run itself — no
offline `build_stage_coordinate_mosaic` rescue.

Arm the rig as usual, then ask microclaw:

> Acquire a 5-tile plus centred on the current position with a half-FOV step,
> using the 640 nm laser. Autofocus at each field with a 10 µm range and 1 µm
> step, and stitch the tiles inline in the same acquisition so the mosaic is
> written by the run itself. Save to `D:\SSD\gate_h5`.

Expected:

- One acquisition, one `position`-axis dataset, per-hook log attribution — as H1.
- `reservation_frames_planned: 75`, `hook_extra_exposures_planned: 70`.
- **A mosaic TIFF written by the run**, listed in the result's artifacts with a
  sha256. If the agent falls back to building it offline, that is a failure of
  this gate — tell it to do it inline and report what it says.
- The agent should pass `artifact_limits` without being told. If it forgets, the
  run must be **refused during planning** — see H7 — and then it should retry
  with a budget. Either order is a pass; a silent no-mosaic is not.

Open the mosaic. Beads in the half-FOV overlaps must superimpose, not reflect
across the diagonal.

Dose: 5 frames + 70 sweep exposures.

## H6 — a broken saved hook is refused before anything moves (zero dose)

Pick one of the two hooks in the table above that you have **not** re-saved. Ask:

> Run a 2-position acquisition using the `<name>` hook, saving to `D:\SSD\gate_h6`.

Expected: **refused, naming the contract violation and the reversed
`EmitArtifact` arguments.** No stage movement, no exposure, no dataset directory.
Confirm the stage did not move.

Then ask:

> Describe the `<name>` hook.

Expected: `resolve_refusal.would_refuse: true` with the contract violation in
`reasons` — where before this fix it said `false`.

Also, for the record:

> List my saved hooks and tell me which ones would refuse to run and why.

Paste that output — it is the authoritative registry survey for your rig.

### What the migration found — block 45, M5, 2026-08-12

This survey's numbers were re-measured when block 45 migrated the registry, and
**the "12 saved hooks" recorded here was already stale**: M5 carries **21**, of
which **9** were unresolvable. Nine is the number that held; twelve was not.
Treat a live `list_hooks` as authoritative and never carry a count forward.

Recoverable by re-saving alone (pin-only refusal — the source was already
contract-clean, the manifest hash simply predated byte-pinning):
`filament_position_filter_v2`, `mosaic_cell_counter_v2`, `uv_activation`,
`uv_activation_wind_down`.

Needed rewritten source, not a re-save: `mosaic_cell_counter`, `mosaic_stitcher`
and `mosaic_stitcher_rot` still used the pre-Block-7 contract (subclass
`HookBase`, take `log_path`); `mosaic_stitcher_v2` and `mosaic_stitcher_rot_v2`
needed the keyword `EmitArtifact` form. All five have corrected sources in
`tests/fixtures/hooks/m5_migrated/`.

Outcome: **9 unresolvable → 4**, the remainder being the four `_v2` entries,
which the operator will finish in place. The round also established that a `_v2`
name is a **separate registry entry**, not an alias for the un-suffixed one —
re-saving one leaves the other refused, which is how four were missed.

**There is no way to remove a saved hook.** A superseded entry can only be
re-saved, never retired, so a registry accumulates duplicates that `list_hooks`
must keep reporting. Recorded as an open finding by block 45; no tool was added.

## H7 — an emitting hook with no budget is refused during planning (zero dose)

Ask microclaw to run the H5 acquisition again but **explicitly without**
`artifact_limits`:

> Run the same 5-tile plus with the stitcher composed, but do not pass
> artifact_limits. Tell me exactly what happens and whether anything was exposed.

Expected: refused during planning, naming the emitting hook and the missing
`artifact_limits`. **Nothing exposed, no autofocus sweep, stage unmoved** — check
the position before and after. This is the gate for "decided before the stage
moves", which is the whole point of both fixes in this round.

## H8 — leave the rig as you found it

`All: 3. TTL Enable = 1`, all four `Use TTL = 1`,
`Laser 1: 6. Status = AVAILABLE ENABLED USETTL`. Paste the exit report.

## Return kit

- `h5_install.txt`, `h5_tests.txt`
- Session histories for H5–H7
- The H5 hook log, mosaic TIFF, and the result's artifact list
- The H6 registry survey output
- Stage positions before/after H6 and H7
