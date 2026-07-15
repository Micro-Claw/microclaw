# design/23 — Redundant imaging, and the agent that couldn't think in code

Source: `20260714_131941_microclaw_history_nestor_stage_scan.json` (258 messages,
Nestor stage-scan run, 2026-07-14).

Two operator complaints:

1. The agent imaged the same grid points multiple times when once would do.
2. The agent did not offer to write code when writing code was the better answer —
   it stayed inside `snap_and_analyze` / `find_features`.

Both are real, and they have **different causes and different fixes**:

- **Complaint 1 is a set of tool-surface defects.** Each redundancy episode traces
  to a specific fact microclaw failed to hand the agent: the hooked branch drops
  the tile coordinates it computed (F1), the hook docs actively *deny* that
  coordinates exist in the metadata (F2), `mark_position` can't accept a coordinate
  (F3), and `ImageStats` omits SNR (F7). In every case the agent needed a fact the
  tools withheld, and the cheapest way to obtain it was to drive the hardware again.
- **Complaint 2 is a prompt defect** (F4): the code-writing ladder only fires on
  requests already shaped like hook requests.

Fixing the prompt would not have prevented Episodes A or B; those needed the tool
fixes. What the two complaints *share* is a **disposition**: the agent treats its
tool surface as the boundary of the possible. That explains the *shape* of the
failure rather than its origin. A code-fluent agent has a fallback when the tool
surface is thin — it can compute what the tools don't return. This agent had no
such fallback, so it fell back on the hardware instead, and the sample paid.

Tool gaps set the traps; code-blindness is why the agent walked into them carrying
the microscope instead of a calculator.

---

## Stage 1 — Quantifying the redundant imaging

Reconstructed every exposure at a known (x, y) by replaying the transcript: tile
grids expanded from `grid_center_x_um/y_um` + rows/cols/step, multiposition runs
from their per-position results, and `find_features` / `snap_and_analyze` from the
stage position at the time.

| | |
|---|---|
| Total exposures at a known coordinate | 491 |
| Distinct coordinates visited | 342 |
| Coordinates imaged more than once | 126 |
| **Redundant exposures** (beyond the first at each coord) | **149** |

Attribution of the 149:

| Cause | Exposures | Verdict |
|---|---|---|
| User explicitly asked for a repeat (msg 135, *"repeat the search in the same area, I had mistaken the laser and filter"*) | 100 | legitimate |
| 488-channel pass over the 10 keepers (msg 203) | 10 | legitimate — new channel |
| **Agent-caused waste** | **39** | **bug** |

The 10 final cells tell the story on their own: each was imaged **4 times** (survey,
re-mark, 488, re-measure) where **2** — one per channel — was the floor. 40
exposures on 10 keepers; minimum necessary 20.

### What was NOT the problem

Worth stating, because it contradicts the obvious hypothesis. The **search expansion
was correct.** The user repeatedly asked for new, non-overlapping areas (msgs 102,
112, 124), and the agent's grids at msgs 91, 103, 113, 125, 181, 187 share **zero**
tile coordinates with each other. It did the non-overlap geometry right.

The duplication is entirely in **bookkeeping and measurement passes**, in three
distinct episodes.

---

## Stage 2 — The three redundancy episodes

### Episode A — Re-running a 9-tile grid to learn where it was (msgs 71→77, 9 wasted)

The agent ran a hooked 3×3 survey (msg 71) without pinning a center. The result came
back `grid_center_x_um: 250, grid_center_y_um: -7529.1` — not where the agent thought
the stage was. It read the hook log and hit a wall (msg 75):

> "**I don't have the XY coordinates of each tile.** The hook log records position
> *labels* only... I can't reconstruct each tile's absolute XY from the label alone."

So it re-ran **the identical 9-tile grid** (msg 77), pinned to the reported center
with `mark_positions=true`, purely to make the coordinates appear somewhere it could
read them. The hook logs confirm the fields were identical (`filament_r2_c2` SNR
73.098 → `filament2_r2_c2` SNR 73.105).

**The agent was wrong — the coordinates were fully reconstructible.** It had the grid
center, `rows`, `cols`, `step_um`, and the label format `<name>_r<row>_c<col>`
(documented at `tools_schema.py:757`). Every one of the 342 coordinates in this
analysis was reconstructed from exactly that. But expecting the agent to do
trigonometry on label strings is the wrong fix.

**Root cause — `tools.py:1577`.** `run_tile_acquisition` *computes the exact per-tile
coordinates*, hands them to `run_multiposition_acquisition`, and the hooked branch
throws them away:

```python
positions = [
    {"name": f"{name}_r{r}_c{c}",
     "x_um": x_start + c * step_um,
     "y_um": y_start + r * step_um}          # ← known, exactly, right here
    for r in range(rows) for c in range(cols)
]
```

The **non-hooked** branch (`tools.py:1505-1520`) attaches `x_um`/`y_um` to every
result row, with a comment naming this precise failure mode: *"The agent used to
publish X/Y columns filled from its own call ordering rather than from anything a
tool returned (design/19 F3, design/20 S1)."*

**The hooked branch (`tools.py:1495`) never got that treatment.** It returns only
`status`, `dataset_path`, `positions` (a count), `log_path`, `hint`, `grid_center_*`.
Same class of bug design/19 and design/20 already fixed — still live on the hooked
path, which is the path every survey in this session took.

**And there is a second, worse cause underneath it.** The hook could have logged each
tile's XY *directly from the image metadata* — the coordinates are right there in
every multi-position acquisition. It didn't, because `hook_docs.py:32` explicitly
instructs generated hooks that those keys do not exist. They do. See F2.

### Episode B — Re-imaging keepers to re-mark the position list (msgs 143 and 195, 16 wasted)

Twice, the agent had a position list already containing every tile with correct
coordinates, then:

1. `clear_position_list()` (msgs 141, 193)
2. `run_multiposition_acquisition(protocol="timelapse", n_frames=1,
   mark_positions=true, ...)` over the keepers — **which re-images every one** —
   solely to get them back into the list with tidier names (`cell_01`…`cell_10`).

**Root cause — `mark_position` takes no coordinates.** Its schema
(`tools_schema.py:514`) marks *the current stage position*: "Mark the current stage
position... Call this after the biologist has navigated to a site of interest."

There is no `mark_position(x_um=…, y_um=…)` and no batch form. So an agent holding ten
known coordinates has no zero-exposure way to write them into the list. A
`move_stage_xy` → `mark_position` loop would have worked (moves, no snaps) and the
agent should have composed it — but the **path of least resistance is an acquisition
tool's `mark_positions=true` flag, and that flag necessarily images.** The API makes
the wasteful route the obvious one.

Note the irony at `tools.py:1489`: in the hooked path, `mark_positions` is *already*
implemented as a pure list write with no imaging ("The grid coordinates are known up
front, so marking needs no stage reads and no visit loop"). **The capability exists
internally. It just isn't exposed as a tool.**

### Episode C — The 488 re-measurement loop (msgs 203→244, 10 wasted)

The agent ran a 10-position snap pass in the 488 channel (msg 203), got the results,
and then said (msg 205):

> "the snap tool gives me mean/min/max intensity — **not the background-relative
> SNR** that would let me call 'signal present' cleanly... Let me revisit each
> position and run `find_features`"

It then drove `go_to_position` + `find_features` across all ten cells (msgs 205–244)
— **a second full pass over the sample**, ten more exposures, because the first tool
returned the wrong statistic.

This is where the two complaints meet. The agent had, *at that moment*, a saved hook
of its own authorship that computes exactly an SNR-gated score. It could have run one
hooked pass. It could have written a four-line hook. Instead it brute-forced the
hardware a second time, because *"which of my tools returns SNR?"* is the only
question it knew how to ask.

---

## Stage 3 — Why it never offered to write code

At msg 56 the operator asks: **"can you recognize microtubules"**. The agent answers
*no* (msg 57), and the reasoning is purely tool-bounded:

> "**My image tools don't do morphological classification.** `find_features` gives me
> spot counts, centroids, SNR, and background — it detects blobs, not filament
> networks. `snap_and_analyze` gives focus and intensity numbers. Neither returns
> anything that distinguishes 'microtubules'... **There's no tool in this rig that
> recognizes a biological structure by name.**"

Its offered alternatives: improve focus, then eyeball a thumbnail. It never mentions
that it can *write* the missing capability — despite `generate_and_save_hook` sitting
in its toolset.

Only when the operator spells it out at msg 58 — *"can you write a custom function to
identify microtubules?"* — does the agent call `list_hooks`, then produce a genuinely
good Sato/Frangi multi-scale ridge filter with an SNR gate (msg 65), correctly named
`filament_score` rather than oversold as a microtubule classifier. **The capability
was excellent. The initiative was absent.**

The same blind spot recurs:

- msgs 33–55: the agent fights `snap_and_analyze`'s `focus_metric` being fooled by
  noise on empty fields. It *correctly diagnoses this* — "sharp noise reads as high
  Laplacian variance just like sharp edges do" (msg 43) — and never once considers
  writing a focus metric that isn't fooled, which is exactly the kind of function it
  proves it can write 20 messages later. **This one is microclaw's fault, not the
  agent's — the metric really is inverted on empty fields. See design/25.**
- msg 205: needs SNR, has a tool that returns intensity, drives the stage again rather
  than computing it. **Also microclaw's fault — see F7.**

### Root cause — `agent.py:150-162`

The entire code-writing pathway lives under the heading **"Hook-based adaptive
acquisition"**, and its entry condition is:

> `- When no pre-coded hook matches a request:`
> `  1. Call list_hooks() FIRST...`
> `  3. Only if nothing in list_hooks() matches: tell the user no existing hook covers
>      this behaviour, then ask "Do you have an existing hook file you'd like to use,
>      or would you like me to write one?"`

That ladder only fires **once the request has already been framed as a hook request.**
"Can you recognize microtubules?" is a *capability* question. It never enters the
ladder. Nothing in the prompt tells the agent that when its fixed tools cannot measure
something, **writing code is an available answer** — so it does what the prompt taught
it to do: enumerate tools, and report the gap.

The prompt treats `generate_and_save_hook` as a **fallback within an acquisition
workflow**, not as a **first-class capability the agent possesses**.

---

## Stage 4 — Recommended fixes

**F1 — Return per-tile coordinates from hooked acquisitions.** (`tools.py:1495`)
The hooked branch already receives `resolved` (name, x, y, z for every tile) and drops
it. Echo it back, exactly as the non-hooked branch does — add a `tiles` key to the
returned payload:

```python
return {**hooked, "tiles": [{"position": n, "x_um": round(x, 3), "y_um": round(y, 3),
                             **({"z_um": round(z, 3)} if z is not None else {})}
                            for n, x, y, z in resolved]}
```

`read_hook_log` then joins to this on `position`, and no re-scan is ever needed to
answer "where was tile r2_c1?". Kills Episode A outright and closes the design/19 F3
gap on the path that actually gets used. *Smallest fix, highest value.*

**F2 — The coordinates were always in the metadata. Fix the documentation that says
otherwise.**

design/19 concluded there is no `XPosition_um_Intended` in hook metadata. It drew that
from a **single-position z-stack**, which carries no XY at all. The spike
`design/23-hook-metadata-coords-spike.py` (no hardware — it drives pycro-manager's own
event builder and metadata assembler) shows it does not generalise:

| acquisition | `XPosition_um_Intended` in hook metadata? |
|---|---|
| **multi-position tile grid** (`xy_positions` set) — *every Nestor survey* | **YES** — `-1034.2` |
| single-position z-stack (no `xy_positions`) — *what design/19 examined* | no |

The gate is `acq_eng_metadata.py:73` — the key is stamped exactly when the event
carries XY, so it is present for every multi-position grid and absent only for the
z-stack design/19 looked at. pycro-manager asserts this against real hardware in its
own suite (`pycromanager/test/test_acquisition.py:610`).

**Confirmed on a real Micro-Manager demo config** (spike `--live`, Windows rig,
2026-07-15). The offline cases drive the `acq_eng_py` *port*; the `--live` path runs a
real demo-config acquisition through the same `image_process_fn` seam microclaw's hooks
use (`tools.py:644`) and dumps the metadata dict a hook actually receives over the
ZMQ→Java bridge. It carries, spelled exactly as `where()` reads them: `PositionName`
(`'c561_r0_c0'`), `Axes.position` (`'c561_r0_c0'` — the fallback), `XPosition_um_Intended`
/ `YPosition_um_Intended`, and all three of X/Y/Z for a grid+z acquisition. So F2's
`where()` works against the real backend, not just the port.

**The same dump surfaced a live latent bug that reinforces this fix.** The real metadata
uses `PositionIndex`, `Frame`/`FrameIndex`, and `Time` — there is **no** `position_index`
or `time` key. But `PositionFilterHook` reads `metadata.get("position_index", -1)`
(`hooks.py:216`) and the focus/intensity hooks read `metadata.get("time")`; on this rig
both silently return the default (`-1` / `None`). `PositionFilterHook` still rejects dim
images correctly but collapses every rejection under `position_index = -1`, so after the
first rejection it stops logging which positions it dropped. This is exactly the
wrong-key-name failure class F2 ends — one more reason to route every hook through
`self.log()/where()` and read the *stamped* keys, not guessed ones. (Migrating those
hooks' own `position_index`/`time` reads to the real `PositionIndex`/`FrameIndex` is a
small follow-up, tracked with the F2 migration.)

The over-generalisation was then written into `hook_docs.py:32-33` as a flat
prohibition:

> "Do not guess stage-coordinate metadata names (**there is no
> `"XPosition_um_Intended"`**); a multi-position acquisition names its positions."

**That instruction is why the generated hook logged only a label.** The agent followed
its documentation, the documentation was wrong, and the cost was a re-imaged grid. Two
changes:

1. **Correct `hook_docs.py`** — a multi-position acquisition's metadata carries
   `XPosition_um_Intended` / `YPosition_um_Intended` / `PositionName`; a
   single-position acquisition does not, so read them with `.get()` and fall back to
   `metadata["Axes"]["position"]`.
2. **Stamp coordinates in `HookBase`**, so no hook has to remember. Add
   `HookBase.where(metadata) -> {"position", "x_um", "y_um", "z_um"}` (all via `.get()`,
   position falling back to `Axes["position"]`), and have `HookBase.log(metadata,
   **fields)` merge it into every entry. Every pre-coded hook then routes through
   `self.log(...)`, and every hook log is self-describing without a position-list join.

F1 stays worth doing anyway — it costs three lines and covers the case where the hook
errors out or the log is never read.

**F3 — Give `mark_position` optional coordinates. Skip the batch tool.**

Add `x_um`/`y_um`/`z_um` as optional properties: supplied → record without moving or
imaging; omitted → current stage position, as today. This is one optional-argument
change to an existing tool rather than a whole new tool the agent must discover, and it
removes the *stage move*, which is the real cost. A batch tool would earn its keep only
if per-call cost were the problem, and it isn't — the round trips are serialised anyway
(pyjavaz holds one lock across every bridge call), so a batch tool wouldn't parallelise
anything.

Return `"imaged": False` in the result. That is deliberate: it tells the agent, in the
payload it actually reads, that marking is free.

Two things the implementation must get right:

- **Validate supplied coordinates.** Explicit x/y/z is unvalidated caller input, unlike
  the current stage position, which is reachable by definition. Call `guard.check_xy` /
  `guard.check_z` *before* anything is written to the list.
- **The O(N²) trap was measured and does not bite — batch is optional.** `ctrl.add_position`
  (`controller.py:310`) rewrites MM's *entire* PositionList on every call
  (`_write_position_to_mm`, `:324`), so marking 100 tiles is 100 full list rebuilds and
  100 GUI repaints. Timed on the real demo config (spike `--live`, Windows rig, 2026-07-15):

  | N | total | per-call |
  |---|---|---|
  | 10 | 0.37 s | 36.7 ms |
  | 50 | 0.91 s | 18.1 ms |
  | 100 | 2.61 s | 26.1 ms |

  Per-call cost is flat/noisy in the 18–37 ms band (the N=10 figure is warmup), not the
  monotonic climb an O(N²) rebuild would show — 100 marks is 2.6 s total. So ship F3 as
  the single optional-arg change and **do not** build `ctrl.add_positions(entries)`. Keep
  the batch idea in the back pocket only if a future workflow marks many hundreds of
  positions and 2.6-s-per-100 starts to matter.

The schema description should say it outright: *"pass x_um/y_um to record a position
WITHOUT moving there and WITHOUT imaging it — never re-run an acquisition just to mark
positions."*

**F4 — Reframe code-writing in the system prompt as a capability, not a fallback.**
(`agent.py:150`) Add an entry point that fires on the *capability gap*, not on the
request shape:

> When a user asks for a measurement or classification your fixed tools do not provide
> — a structure to recognize, a custom metric, a quantity no tool returns — writing a
> hook **is** the answer. Say so, and offer it, before you report the limitation.
> Enumerating what your tools cannot do, without mentioning that you can write what's
> missing, understates your capability.

Also lift this out from under the "Hook-based adaptive acquisition" heading — the
heading itself is what makes the whole capability look like an acquisition-strategy
detail rather than a general power the agent has.

**F5 — A "don't re-image what you've already imaged" instruction.** Photobleaching is
irreversible; a re-scan is not free. The prompt should state that re-imaging a
coordinate is a *hardware cost to be justified*, and that bookkeeping (marking,
renaming, re-measuring) must never be a reason to re-expose the sample.

**F6 — The focus metric is inverted on empty fields → see design/25.**
`normalized_laplacian_variance` inflates when there is no signal (the design/14
scale-free denominator collapses toward the noise floor), so at msg 39 the agent ranked
the *emptiest* tile as the sharpest. The fix is a PSF-matched low-pass pre-filter plus
the SNR gate below, and possibly a switch to DCTS. It's a focus problem rather than a
redundant-imaging one, so it has its own document:
`design/25-focus-metric-on-empty-fields.md`.

**F7 — Put SNR in the standard image statistics, from one shared definition.** This is
the cheapest fix in the document, but it has a trap in it.

`ImageStats` (`image_analysis.py:11`) carries `focus_metric`, `mean/max/min intensity`,
and `saturated_fraction` — no background, no SNR. Every consequence in this session
flows from that gap:

- the agent couldn't tell an empty field from a cell in the msg-37 survey (it had
  intensity, which is ambiguous, and focus_metric, which was inverted);
- at msg 205 it had 10 snap results, needed SNR, and **drove the stage over all ten
  cells a second time** to get it (Episode C, 10 wasted exposures);
- and design/25's focus gate needs an SNR estimate anyway, so this is a prerequisite.

**The trap: `detect_features` already computes an SNR, and it is a different quantity.**
`image_analysis.py:136` returns `peak / std(image - background)`. Adding a
robust noise-floor SNR to `ImageStats` without touching that would leave **two different
statistics both named `snr`**, returned by different tools, which the agent compares
against each other — precisely the design/20 failure class of numbers that look
comparable and aren't. So define it **once**:

```python
# image_analysis.py — the single definition. Used by compute_stats AND detect_features.
def snr(image: np.ndarray, background: float | None = None) -> float:
    """Signal over the noise floor: (p99.5 - bg) / (1.4826 * MAD).

    Robust at both ends: a percentile rather than max() so one hot pixel is not
    "signal", and MAD rather than std() so the noise estimate is not inflated by the
    signal it is meant to be measured against.
    """
```

Two consequences to handle deliberately, not silently:

1. **This changes the number `find_features` reports.** `peak/std` and
   `(p99.5-bg)/(1.4826·MAD)` are on different scales. Any saved hook or user
   expectation keyed to the old value needs review — including the agent's own
   `filament_position_filter` from this session, which gates on SNR.
2. **Calibrate `min_snr` against the new definition on real frames** before shipping the
   design/25 gate. The `min_snr=3.0` figure is a placeholder until it is measured on the
   rig. <min_snr can change from microscope to microscope and from sample to sample. how often will we re-calibrate it?>

Then extend `ImageStats`:

```python
class ImageStats(NamedTuple):
    focus_metric: float
    focus_metric_valid: bool     # False when snr < min_snr (design/25)
    background_level: float      # robust: median
    snr: float                   # the shared definition above
    mean_intensity: float
    max_intensity: float
    min_intensity: float
    saturated_fraction: float
```

**One caveat on where the gate lives.** `ImageStats.focus_metric` currently holds the
*raw* `laplacian_variance`, and every tool overrides it with
`normalized_laplacian_variance` via `_focus_metric_payload` (`tools.py:847`, `:1382`).
That field is effectively dead. So `focus_metric_valid` must be set where the normalized
metric is actually computed — in the payload builders — or it will report on a number
nobody reads. The stub in Stage 5 resolves this by moving the normalized metric *into*
`compute_stats`, so there is one computation and one validity flag, and the payload
builders stop overriding a field they were only overriding because it was wrong.

With that, every tool returning per-position stats (`snap_and_analyze`,
`run_multiposition_acquisition`, `run_tile_acquisition` with `protocol="snap"`) reports
SNR for free, and "is there signal at this position?" stops being a question you have to
re-image the sample to answer.

---

## Stage 5 — Implementation stubs

Landing order is a dependency order, not a priority order: F7 defines the SNR every later
piece gates on, F2 makes hooks self-describing, and F1/F3 are the two small tool-surface
changes that kill Episodes A and B. Signatures below are against the code as it stands
today; line numbers are from this commit.

### F7 — one SNR definition, shared (`image_analysis.py`)

```python
# microclaw/image_analysis.py

class ImageStats(NamedTuple):
    focus_metric: float          # normalized_laplacian_variance — the number tools report
    focus_metric_valid: bool     # False when snr < min_snr: no signal to be sharp about
    background_level: float      # robust: median (the camera offset, Evolve512 ≈ 400)
    snr: float                   # the shared definition below
    mean_intensity: float
    max_intensity: float
    min_intensity: float
    saturated_fraction: float


def snr(image: np.ndarray, background: float | None = None) -> float:
    """Signal over the noise floor: (p99.5 - bg) / (1.4826 * MAD).

    Robust at both ends: a percentile rather than max() so one hot pixel is not
    "signal", and MAD rather than std() so the noise estimate is not inflated by the
    signal it is meant to be measuring against.

    The ONE definition. detect_features must call this rather than keeping its own
    peak/std (image_analysis.py:136) — two different quantities both named `snr`,
    returned by different tools, is the design/20 failure class exactly.
    """
    img = image.astype(np.float64)
    if img.ndim == 3:
        img = img.mean(axis=-1)
    bg = float(np.median(img)) if background is None else background
    mad = float(np.median(np.abs(img - np.median(img))))
    noise = 1.4826 * mad
    if noise <= 0:                  # flat frame (all-zero, or saturated everywhere)
        return 0.0
    return float((np.percentile(img, 99.5) - bg) / noise)


MIN_SNR = 3.0   # PLACEHOLDER — calibrate on the rig before shipping (see below)


def compute_stats(image: np.ndarray, min_snr: float = MIN_SNR) -> ImageStats:
    """Now returns the NORMALIZED focus metric, not the raw laplacian_variance.

    Callers already overrode focus_metric with normalized_laplacian_variance at both
    payload sites (tools.py:847, :1382) because the raw value was not comparable. Doing
    it here means one computation, one validity flag, and no dead field to mistake for
    the live one. `laplacian_variance` stays exported for anyone who wants the raw number.
    """
    bit_max = float(np.iinfo(image.dtype).max) if np.issubdtype(image.dtype, np.integer) else 1.0
    bg = ...            # float(np.median(img)) — computed once, passed to both below
    s = snr(image, background=bg)
    return ImageStats(
        focus_metric=normalized_laplacian_variance(image, background=bg),
        focus_metric_valid=s >= min_snr,
        background_level=round(bg, 1),
        snr=round(s, 2),
        ...
    )
```

Then both payload sites collapse. `_focus_metric_payload` (`tools.py:837`) stops
recomputing the metric and becomes a stamp-merger over the stats it is handed:

```python
def _focus_metric_payload(ctrl: MicroscopeController, stats: ImageStats) -> dict:
    return {
        "focus_metric": _round_sig(stats.focus_metric),
        "focus_metric_valid": stats.focus_metric_valid,   # design/25: no signal ⇒ no focus
        "background_level": stats.background_level,
        "snr": stats.snr,
        **_metric_stamp(ctrl),
    }
```

and `_run_protocol_at`'s snap branch (`tools.py:1382`) drops its own
`normalized_laplacian_variance` call, reading `stats.focus_metric` /
`stats.focus_metric_valid` / `stats.snr` instead. It keeps omitting `_metric_stamp` — the
grid still shares one stamp, applied once by the caller (`tools.py:1531`).

**Two things this fix must not do silently:**

1. **`find_features`'s `snr` changes value.** `peak/std` and `(p99.5-bg)/(1.4826·MAD)` are
   on different scales. The session's own saved `filament_position_filter` gates on SNR;
   it needs re-reading against the new number, as does any user hook.
2. **`MIN_SNR = 3.0` is a placeholder, not a measurement.** Calibrate it on real frames —
   empty field vs. faint cell vs. bright cell — before the design/25 gate ships.
   *Open question, carried from Stage 4: min_snr plausibly varies by microscope and by
   sample, so who re-calibrates it and how often? A single module constant is the
   simplest thing that could work; if it turns out to need per-rig values, it belongs in
   `safety_config.yaml` alongside the other rig facts.*

### F2 — stamp coordinates in `HookBase` (`hooks.py`), and fix the docs (`hook_docs.py`)

```python
# microclaw/hooks.py — HookBase (currently :12-32)

class HookBase:
    ...

    @staticmethod
    def where(metadata: dict) -> dict:
        """Where this image was taken, from its own metadata. Never raises.

        A multi-position acquisition stamps PositionName / XPosition_um_Intended /
        YPosition_um_Intended; a Z-stack stamps ZPosition_um_Intended. Neither stamps
        the other's keys (acq_eng_metadata.add_image_metadata gates each on the event
        carrying that coordinate), so every read is a .get() and a missing key means
        "this acquisition has no such axis", not "wrong name". Verified without
        hardware in design/23-hook-metadata-coords-spike.py.
        """
        axes = metadata.get("Axes") or {}
        out: dict = {"position": metadata.get("PositionName", axes.get("position"))}
        for key, field in (("XPosition_um_Intended", "x_um"),
                           ("YPosition_um_Intended", "y_um"),
                           ("ZPosition_um_Intended", "z_um")):
            value = metadata.get(key)
            if value is not None:
                out[field] = round(float(value), 3)
        return out

    def log(self, metadata: dict, **fields) -> None:
        """Append ONE self-describing entry: where the image was + what the hook measured.

        Every hook routes its per-image record through here instead of appending to
        self._log directly, so no hook log can ever again say "filament_r2_c2, SNR 73"
        without saying where r2_c2 was (Episode A).
        """
        self._log.append({**self.where(metadata), **fields})
        self._write_log()
```

Then migrate the pre-coded hooks (`AutofocusHook`, `focus_feedback`,
`intensity_adaptive`, `position_filter`, the two `mm_plugin_*`) from
`self._log.append({...}); self._write_log()` to `self.log(metadata, ...)`. The
post/pre-hardware hooks get an `event`, not a `metadata` — they keep appending directly,
or gain a sibling `where_event(event)` reading `event["x"]/["y"]/["z"]`. Either is fine;
what must not happen is a *third* spelling of "where was this".

`hook_docs.py:30-33` — the instruction that caused the bug — becomes:

```text
To key a log entry to the image's place in the acquisition, call self.log(metadata, ...)
on HookBase: it stamps position/x_um/y_um/z_um for you from the image metadata.

If you read the metadata yourself: a multi-position acquisition carries "PositionName",
"XPosition_um_Intended" and "YPosition_um_Intended"; a Z-stack carries
"ZPosition_um_Intended". An acquisition without a given axis does not carry its keys, so
read every one with .get() and fall back to metadata["Axes"]["position"] for identity.
```

Also add a line to the HookBase-provides list (`hook_docs.py:140-144`):

```text
  self.log(metadata, **fields)  — append ONE entry stamped with position + stage XY/Z.
                                  Prefer this over appending to self._log by hand.
  HookBase.where(metadata)      — just the {position, x_um, y_um, z_um} dict.
```

### F1 — echo per-tile coordinates from the hooked branch (`tools.py:1495`)

```python
    if hook_strategy:
        ...
        hooked = _acquire_positions_with_hook(
            ctrl, guard,
            positions=[{"name": n, "x_um": x, "y_um": y, "z_um": z}
                       for n, x, y, z in resolved],
            ...
        )
        if "error" in hooked:
            return hooked
        # The coordinates are known exactly, right here — the hooked branch used to
        # drop them, so "where was tile r2_c1?" had no answer short of re-imaging the
        # grid (design/23 Episode A). The non-hooked branch has attached them since
        # design/19 F3; this is the same fix on the path every survey actually takes.
        # read_hook_log joins to this on `position`.
        return {**hooked, "tiles": [
            {"position": n, "x_um": round(x, 3), "y_um": round(y, 3),
             **({"z_um": round(z, 3)} if z is not None else {})}
            for n, x, y, z in resolved
        ]}
```

Redundant with F2 by design, and worth both: F2 fails when the hook errors out or the log
is never read, F1 fails when the agent reads only the log. Three lines buys the overlap.

Test: `run_tile_acquisition(rows=3, cols=3, step_um=100, center_x_um=250,
center_y_um=-7529.1, hook_strategy=...)` returns nine `tiles` rows whose coordinates
match the non-hooked branch's `results` rows for the same grid, exactly. That equality —
hooked and non-hooked agreeing on where they went — is the assertion worth having.

### F3 — optional coordinates on `mark_position` (`tools.py:1203`, `tools_schema.py:513`)

```python
def mark_position(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    name: str,
    include_z: bool = True,
    x_um: float | None = None,
    y_um: float | None = None,
    z_um: float | None = None,
) -> dict:
    """Record a position in microclaw's list and MM's GUI list.

    With x_um/y_um: records that coordinate WITHOUT moving the stage and WITHOUT
    imaging. Without them: the current stage position, as before. The second form is
    why Episode B cost 16 exposures — the only zero-move way to write a known
    coordinate into the list was an acquisition tool's mark_positions=True flag, and
    that flag images.
    """
    if (x_um is None) != (y_um is None):
        return {"error": "Provide both x_um and y_um, or neither."}
    supplied = x_um is not None
    if supplied:
        x, y = round(x_um, 3), round(y_um, 3)
        z = round(z_um, 3) if z_um is not None else None
    else:
        x = round(ctrl.core.get_x_position(), 3)
        y = round(ctrl.core.get_y_position(), 3)
        z = round(ctrl.core.get_position(), 3) if include_z else None
    # Supplied coordinates are unvalidated caller input, unlike the current stage
    # position, which is reachable by definition. Guard BEFORE anything is written.
    guard.check_xy(x, y)
    if z is not None:
        guard.check_z(z)
    ctrl.add_position(name, x, y, z)
    return {
        "status": f"Position '{name}' marked (visible in MM's Position List Manager).",
        "x_um": x,
        "y_um": y,
        **({"z_um": z} if z is not None else {}),
        # In the payload the agent actually reads: marking is free.
        "imaged": False,
        "stage_moved": not supplied,
    }
```

Schema description (`tools_schema.py:514`) — the operative sentence is the second:

```text
"Mark a stage position. It is stored in microclaw's list and mirrored into MM's
 PositionList, so it appears immediately in the MM GUI's Position List Manager.
 Pass x_um/y_um to record a KNOWN coordinate WITHOUT moving there and WITHOUT imaging
 it — never re-run an acquisition just to get positions into the list. Omit them to
 mark wherever the stage currently sits, e.g. after the biologist has navigated to a
 site of interest."
```

**The O(N²) trap was measured, and does not bite — skip the batch.** `ctrl.add_position`
(`controller.py:310`) calls `_write_position_to_mm` (`:324`), which rebuilds and
round-trips MM's *entire* PositionList on every call, all serialised behind pyjavaz's one
bridge lock. The spike's `--live` timing (Windows demo config, 2026-07-15) shows per-call
cost flat at 18–37 ms across N=10/50/100 — 100 marks in 2.6 s, no quadratic climb. So
`mark_position` loops `add_position` and that is all F3 ships. **Do not build**
`ctrl.add_positions(entries)` on spec; the numbers say it earns nothing at the scales this
tool sees. If a future workflow marks many hundreds of positions, the batch is a
controller-level change (`_drop_label_from_plist` already leaves the `set_position_list`
write-back to the caller, `:348`, precisely so one can be added later) — but that is a
YAGNI until the numbers demand it.

### F4 / F5 — prompt (`agent.py:150`)

Not code, but it lands in the same commit series. F4's paragraph moves *out* from under
the "Hook-based adaptive acquisition" heading into the top-level capability section
(near `agent.py:106`, where the imaging tools are described), because the heading is
itself half the bug: it files code-writing as an acquisition-strategy detail. F5's
"re-imaging is a hardware cost" line belongs with the safety/sample-care instructions,
not with the hook ladder. The `list_hooks()`-first ladder stays exactly where it is and
keeps working; F4 adds a second door into it, opened by a capability gap rather than by a
request already shaped like a hook request.

---

## Coda — credit where due

The agent's *scientific* conduct in this session was genuinely strong, and the fixes
above shouldn't obscure it. It caught its own noise-driven focus-metric error and
retracted the conclusion (msg 43). It refused to call the ridge filter a microtubule
classifier, naming it `filament_score` and repeating the caveat every time it reported.
It recorded only the three filter-wheel positions the operator physically verified,
marking 2/4/5 `unknown` rather than guessing (msg 257). It confirmed both lasers off at
the end.

The failure here is not judgment. It is that the agent sees a fixed console of tools
where it should see a programmable instrument.
