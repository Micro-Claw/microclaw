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
