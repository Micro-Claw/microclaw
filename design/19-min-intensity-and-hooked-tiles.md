# design/19 — The pixel minimum was already there, and tiles can't take a hook

## Symptom

Lab, 2026-07-10 (history `20260710_115106_microclaw_history.json`, MM demo
camera). The user asked for a 3×3 grid at half-ROI steps, reporting the max and
min of each image.

The agent scanned the grid, then reported this table:

| Position | Max intensity | Min (background) | Mean |
|---|---|---|---|
| grid_r0_c0 | 1182 | ~662 | 662.1 |
| … | 1182 | ~662 | 662.1 |

The min column is wrong. The true pixel minimum was **142**. `snap_and_analyze`
returns no minimum, so the agent substituted `find_features`'
`background_level` — a modal background estimate, not a minimum — and put it in
a column headed "Min".

The user then asked for a tool that computes the real minimum. The agent wrote a
`pixel_minmax` hook and, because no grid tool accepts a hook, ran **nine
separate `run_adaptive_zstack` calls with `z_start == z_end == 54`** — a
single-plane "z-stack" per position, used purely as a vehicle to get a hook
attached to one frame. Nine datasets and nine log files landed in `D:\microtest`
to recover nine numbers.

Total cost: ~28 exposures for a 9-point task, one wrong answer, and a
disposable hook.

Both halves of that are our bugs, not the agent's.

## F1 — `min_intensity` is computed and thrown away

`image_analysis.py:56`:

```python
def compute_stats(image: np.ndarray) -> ImageStats:
    return ImageStats(
        focus_metric=laplacian_variance(image),
        mean_intensity=float(np.mean(image)),
        max_intensity=float(np.max(image)),
        min_intensity=float(np.min(image)),      # <-- computed
        saturated_fraction=...,
    )
```

`tools.py:766-772`, the `snap_and_analyze` payload:

```python
        "mean_intensity": round(stats.mean_intensity, 1),
        "max_intensity": round(stats.max_intensity, 1),
        "saturated_fraction": round(stats.saturated_fraction, 4),
```

`grep -rn min_intensity microclaw/` outside `image_analysis.py` returns nothing.
The field exists on `ImageStats`, is populated on every snap, and is surfaced by
no tool. The agent's statement "there's no tool that returns the exact pixel
minimum" was true of the tool surface and false of the code, one stack frame
down.

Everything after message 46 in that history — the hook, the nine acquisitions,
the nine logs — was the cost of a dropped dict key.

## F2 — no grid tool accepts a hook

`hook_strategy` reaches an `Acquisition` through exactly two tools:

| tool | event shape | hook? |
|---|---|---|
| `run_zstack` | z | no |
| `run_timelapse` | t | no |
| `run_adaptive_zstack` | z | **yes** |
| `run_adaptive_timelapse` | t | **yes** |
| `run_multiposition_acquisition` | loop over p | no |
| `run_tile_acquisition` | loop over p | no |

The adaptive pair is the only door, and both are single-XY. That is why a grid
scan had to be spelled as nine degenerate z-stacks. The workaround's shape *is*
the bug report.

## F3 — the hook's coordinates were all `null`, and the table hid it

Every entry in every log read:

```json
{"position_index": null, "x_um": null, "y_um": null,
 "pixel_min": 142, "pixel_max": 1182, "pixel_mean": 662.0634765625}
```

The generated hook guessed `metadata["XPosition_um_Intended"]`; the key wasn't
there, and a single-position z-stack has no `position` axis to index. The agent
then published a table with X and Y columns anyway, filled from its own call
ordering rather than from the log it had just read. The columns *look* sourced.
They aren't.

Under the fix in F2 this corrects itself: a real multi-position event carries
`axes["position"]` and a `position_label`, so a hook can key its log to the grid
point without guessing.

## Not in scope

**Nine bitwise-identical frames.** All nine logs report
`pixel_mean: 662.0634765625` to the last bit, 512 µm apart. This was the MM demo
camera (confirmed with the user), which synthesises a position-independent
image. No detector proposed. Worth remembering that on real hardware this
signature means the stage isn't moving, and nothing in microclaw would have said
so.

---

# Fixes — implemented

All three landed on `design/19-min-intensity-and-hooked-tiles`, Option B for
Fix 2 as recommended below:

| | commit | |
|---|---|---|
| F1 | `458a888` | `min_intensity` in the `snap_and_analyze` payload |
| F2, F3 | `3168661` | `hook_strategy` on both grid tools; one Acquisition wide |

Implementation notes that differ from what is written below are marked
**Implemented:** in place. The design was otherwise followed as drafted —
notably, `_acquire_with_hooks` and `_build_acquisition_events` needed no change,
exactly as Option B predicted.

## Fix 1 — surface `min_intensity` (one line) ✅

```python
# tools.py, snap_and_analyze payload
         "mean_intensity": round(stats.mean_intensity, 1),
+        "min_intensity": round(stats.min_intensity, 1),
         "max_intensity": round(stats.max_intensity, 1),
```

Schema text at `tools_schema.py:365` currently promises "(focus metric, mean
intensity, saturation)". Make it say what it returns:

```python
            "numerical stats (focus metric, mean/min/max intensity, saturation). "
```

`tests/test_integration.py:124,133` assert the payload's field set; add
`min_intensity` to both tuples.

Do this first and independently. It closes the reported bug on its own.

**Implemented** in `458a888`, as its own commit, verified to pass standalone
with Fix 2/3 stashed. The unit test builds a frame whose minimum (142), mean
and modal background all differ, so a mean-shaped assertion cannot pass it.

## Fix 2 — `hook_strategy` on the grid tools ✅ *(Option B)*

The user asked whether `_acquire_with_hooks()` can be leveraged here. It can —
completely, without modification. But the interesting question is *which loop
disappears*, and there are two answers with very different consequences.

### Option A — loop the existing per-position runner

Thread `hook_strategy` through `run_multiposition_acquisition` →
`_run_protocol_at`, and have the `zstack`/`timelapse` branches call
`run_adaptive_*` instead of `run_*`. This is what the agent did by hand, moved
inside the tool.

It reuses `_acquire_with_hooks` at one remove and is a ~10-line change.

**It also has a trap.** `HookBase._write_log` (`hooks.py:20`) does
`Path(self.log_path).write_text(json.dumps(self._log))` — it rewrites the whole
file from the instance's `_log` list. A loop constructs a *fresh hook per
position*, so `_log` starts empty each time. Point every position at one
`log_path` and each position **truncates the previous position's results**;
you'd read back the last tile and call it the grid. Option A therefore forces
either N log files (what the agent did, by hand, and then had to correlate by
call order) or an append-mode change to `HookBase`.

Plus: N `Acquisition` objects, N datasets, N MM display windows, and per-image
metadata that still has no `position` axis (F3 stays broken).

### Option B — one Acquisition over the whole grid *(recommended)*

`_acquire_with_hooks` is already event-shape agnostic; its docstring says so:

> the same runner serves plain and adaptive acquisitions of any event shape

And `_build_acquisition_events` forwards `**acq_kwargs` straight into
`multi_d_acquisition_events`, whose signature is:

```
(num_time_points, time_interval_s, z_start, z_end, z_step,
 channel_group, channels, channel_exposures_ms,
 xy_positions, xyz_positions, position_labels, order='tpcz')
```

`xy_positions` and `position_labels` are already reachable through the code we
have. **No change to `_acquire_with_hooks` or `_build_acquisition_events` is
required.** A grid becomes one event list, one `Acquisition`, one hook instance,
one `_log` that accumulates across all nine frames, one dataset with a
`position` axis. `order='tpcz'` puts position outermost, so a per-position
z-stack or timelapse composes for free by also passing `z_start/z_end/z_step` or
`num_time_points`.

F3 dissolves: each image's `metadata["Axes"]["position"]` is populated by
pycro-manager, and `export_dataset_as_tiff` already lists `position` in its
preferred axis order (`tools.py`, `preferred = [... "position"]`), so the
existing exporter handles the result unchanged.

**What Option B gives up**, and must compensate for:

1. **pycro-manager moves the stage, not `_run_protocol_at`.** So
   `guard.check_xy` is no longer called per move. Validate *every* grid point
   before building events — which is strictly better: the refusal now arrives
   before the objective moves at all, not on tile 7 of 9. This is the same
   ordering argument `_acquire_with_hooks` already makes for `save_dir`.
2. **`protocol="snap"` has no hooked form.** A snap is `studio.live().snap()`,
   not an `Acquisition`; there are no acquisition images for a hook to process.
   `hook_strategy` + `protocol="snap"` must be a hard error pointing at
   `protocol="timelapse", protocol_params={"n_frames": 1}` — the same advice
   the snap protocol already carries.
3. **`mark_positions` no longer happens inside the visit loop.** Harmless: the
   grid coordinates are computed up front in `run_tile_acquisition`, so mark
   them from the computed list before the acquisition. No stage reads needed.
4. **One dataset, not N.** Better for a grid, but it *is* a behaviour change for
   `zstack`/`timelapse` tiles. Hence: only take the B path when
   `hook_strategy` is supplied. The unhooked protocols keep the existing loop
   and the existing on-disk layout.
5. **`z_start`/`z_end` change meaning next to `xyz_positions`** (found while
   implementing). `multi_d_acquisition_events` reads them as absolute Z beside
   `xy_positions`, but as offsets *relative to each point's Z* beside
   `xyz_positions`:

   ```
   m(xyz_positions=[(0,0,50)], z_start=-1, z_end=1, z_step=1)  -> z = 49, 50, 51
   m(xy_positions=[(0,0)],     z_start=10, z_end=12, z_step=1) -> z = 10, 11, 12
   ```

   The unhooked loop moves to `z_um` and then has `run_zstack` sweep the
   **absolute** range, so `z_um` never changes which planes are captured. To keep
   one `protocol_params` meaning one thing, the hooked path drops `z_um` and uses
   `xy_positions` whenever a Z range is present. `xyz_positions` is used only for
   the rangeless shapes (`timelapse`), where `z_um` sets the focus plane.

   Related: a Z range in the event list is swept by pycro-manager, so — exactly
   as in (1) — nothing guards it per plane. `_acquire_positions_with_hook` must
   `check_z` both ends of the range up front, as `run_zstack` already does.

### Option C — teach `_run_protocol_at` a "hooked single frame" protocol

A narrow `protocol="hooked_snap"`. Rejected: it is Option A with a nicer name,
keeps the N-datasets and N-logs problems, and adds a fourth protocol string that
means "timelapse of one frame" — a concept the codebase already has and already
documents.

### Recommendation

Option B, gated on `hook_strategy` being present. It is the only one that fixes
F3, it is the only one where the log is a single coherent record of the grid,
and it requires *zero* change to the hook machinery — the reuse the user asked
about is already sitting in `_acquire_with_hooks`; what's missing is a caller
that hands it multi-position events.

Keep the Option A loop for `hook_strategy=None` so today's tile behaviour
(especially display-only `snap`) is untouched.

### Stubs

Implemented close to these, with two departures forced by point 5 above:
`_acquire_positions_with_hook` takes an `exposure_ms` (guarded, and written to
the core when no channel carries it, mirroring `run_zstack`), and it branches on
whether the event shape sweeps Z — `xyz_positions` only for the rangeless
shapes, `xy_positions` plus a guarded absolute range otherwise. The `z_um`
fallback to `ctrl.core.get_position()` survives, but only on the rangeless path.

New, in `tools.py`, next to the adaptive pair:

```python
def _acquire_positions_with_hook(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    positions: list[dict],          # [{name, x_um, y_um, z_um?}, ...]
    save_dir: str,
    name: str,
    hook_strategy: str,
    hook_params: dict | None = None,
    log_path: str | None = None,
    channel: str | None = None,
    **shape_kwargs: Any,            # z_start/z_end/z_step | num_time_points/...
) -> dict:
    """One Acquisition across every position, with a single hook instance.

    The hook sees `metadata["Axes"]["position"]`, so its log keys to the grid
    point without guessing metadata names (design/19 F3). Contrast the
    per-position loop, where a fresh hook per position truncates a shared log.
    """
    save_dir = guard.resolve_in_workspace(save_dir)
    if log_path:
        log_path = guard.resolve_in_workspace(log_path)   # see run_adaptive_zstack

    # Every point checked before any motion: the refusal must not arrive on
    # tile 7 of 9, with the objective already out over the sample.
    for p in positions:
        guard.check_xy(p["x_um"], p["y_um"])
        if p.get("z_um") is not None:
            guard.check_z(p["z_um"])
    if channel:
        guard.check_channel(channel)

    try:
        hook = _resolve_hook(ctrl, guard, hook_strategy, hook_params, log_path)
    except ValueError as e:
        return {"error": str(e)}

    labels = [p["name"] for p in positions]
    if any(p.get("z_um") is not None for p in positions):
        shape_kwargs["xyz_positions"] = [
            (p["x_um"], p["y_um"], p.get("z_um", ctrl.core.get_position()))
            for p in positions
        ]
    else:
        shape_kwargs["xy_positions"] = [(p["x_um"], p["y_um"]) for p in positions]

    events = _build_acquisition_events(
        channel=channel, position_labels=labels, **shape_kwargs
    )
    dataset_path = _acquire_with_hooks(guard, save_dir, name, events, hook)
    return _adaptive_result(dataset_path, log_path)
```

`run_multiposition_acquisition` grows the parameters and one branch, before its
visit loop:

```python
def run_multiposition_acquisition(
    ctrl, guard, protocol, save_dir=None, position_names=None, positions=None,
    name="multipos", protocol_params=None, mark_positions=False,
+   hook_strategy: str | None = None,
+   hook_params: dict | None = None,
+   log_path: str | None = None,
) -> dict:
    ...
    # (existing validation, then `resolved` is built)

+   if hook_strategy:
+       if protocol == "snap":
+           return {"error":
+               "hook_strategy needs acquisition images; 'snap' is display-only. "
+               "Use protocol='timelapse' with protocol_params={'n_frames': 1, "
+               "'interval_s': 0} to capture one hooked frame per position."}
+       if mark_positions:
+           for pos_label, x_um, y_um, z_um in resolved:
+               ctrl.add_position(pos_label, round(x_um, 3), round(y_um, 3),
+                                 round(z_um, 3) if z_um is not None else None)
+       shape = _protocol_shape_kwargs(protocol, params)   # see below
+       return _acquire_positions_with_hook(
+           ctrl, guard,
+           positions=[{"name": n, "x_um": x, "y_um": y, "z_um": z}
+                      for n, x, y, z in resolved],
+           save_dir=save_dir, name=name, hook_strategy=hook_strategy,
+           hook_params=hook_params, log_path=log_path,
+           channel=params.get("channel"), **shape,
+       )

    for pos_label, x_um, y_um, z_um in resolved:      # unhooked path, unchanged
        ...
```

with the protocol → event-shape mapping split out so both tools share it:

```python
def _protocol_shape_kwargs(protocol: str, params: dict) -> dict:
    """protocol_params (tool-facing) -> multi_d_acquisition_events kwargs."""
    if protocol == "zstack":
        return {"z_start": params["z_start_um"], "z_end": params["z_end_um"],
                "z_step": params["z_step_um"]}
    if protocol == "timelapse":
        return {"num_time_points": params["n_frames"],
                "time_interval_s": params.get("interval_s", 0)}
    raise ValueError(f"Unknown protocol '{protocol}'.")
```

`run_tile_acquisition` just forwards; its grid math is untouched:

```python
def run_tile_acquisition(
    ctrl, guard, rows, cols, step_um, protocol, save_dir=None, name="tile",
    protocol_params=None, mark_positions=False,
+   hook_strategy: str | None = None,
+   hook_params: dict | None = None,
+   log_path: str | None = None,
) -> dict:
    ...
    return run_multiposition_acquisition(
        ctrl, guard, protocol=protocol, save_dir=save_dir, positions=positions,
        name=name, protocol_params=protocol_params, mark_positions=mark_positions,
+       hook_strategy=hook_strategy, hook_params=hook_params, log_path=log_path,
    )
```

Schema (`tools_schema.py`, both tools) — reuse the `hook_strategy` /
`hook_params` / `log_path` property blocks already written for
`run_adaptive_zstack` at line 803, and add to the tile description:

> Pass `hook_strategy` to run one hooked acquisition across the whole grid: a
> single dataset with a `position` axis and one hook log covering every tile.
> Not compatible with `protocol='snap'` (display-only, no acquisition images).

## Fix 3 — stop the shared-log truncation trap regardless ✅

Even with Option B, `HookBase._write_log` rewrites the file from `self._log`.
That is correct for one hook instance and silently lossy for many. Today nothing
in-tree constructs a hook per position — `run_multiposition_with_autofocus`
loops over `_run_autofocus_passes` directly and never touches `AutofocusHook` —
so the trap is unsprung. It is exactly what Option A would have introduced, and
what a future looping caller will step in. Cheapest guard: a docstring line on
`_write_log` reading "rewrites the whole file from `self._log`; one hook
instance per `log_path`."

**Implemented** as that docstring line. `hook_docs.py` gained the other half of
the same lesson: generated hooks are told to key their log to
`metadata["Axes"]`, and told that there is no `"XPosition_um_Intended"` to guess
at — the key the run's hook invented, which produced the all-`null` logs of F3.

## Tests ✅

All written, in `tests/test_tools.py::TestHookedGridAcquisition` unless noted.
Suite goes 589 → 600.

* ✅ `min_intensity` present in `snap_and_analyze` (extend the two existing tuples).
* ✅ `min_intensity` correct on a synthetic frame with a known floor — the run's
  142-vs-662 gap is exactly what a mean-shaped assertion would have missed.
* ✅ `_acquire_positions_with_hook` builds one event list with `len(positions)`
  distinct `position_labels`, and `_acquire_with_hooks` is called **once**.
* ✅ An out-of-bounds tile raises before `Acquisition` is constructed (guard runs
  ahead of motion).
* ✅ `hook_strategy` + `protocol="snap"` returns the error, not a stack trace.
* ✅ A fake hook accumulating `metadata["Axes"]["position"]` across a 2×2 grid
  yields four entries with four distinct indices in one log — the F3 regression.

Added beyond the list, both covering point 5's trap and both mutation-checked
(flipping the `sweeps_z` branch to `False` fails exactly these two):

* A `zstack` range stays **absolute** when the positions carry `z_um`.
* An out-of-bounds *Z range* refuses before the `Acquisition`, as `run_zstack`
  already does for the same range.

Plus one holding the line under the fix: unhooked tiles still take the
per-position loop. It is the only new test that passes against the pre-fix
source — the other nine fail, which is what makes them regression tests.

## Loose ends from the same run

Neither is worth a code change on its own; noting them so they aren't rediscovered.

* The stage was left at `grid_r2_c2`. No tool restores the entry position after
  a grid. Arguably correct (the user may want to be at the last tile), but it's
  unstated either way.
* The agent signed off with "no lasers or illumination were enabled during this
  task." `get_system_state` returns x/y/z/exposure/live_view — it has no
  illumination field, so that claim had nothing behind it. If we want the agent
  to be able to say this, `get_system_state` has to report shutter/laser state.
