# design/41 — the smiley run: the deliverable had nowhere to go

Source: one M5 session run 2026-08-05 by the operator, in
`~/Documents/Documents - Beyonce/Projects/Micro-Claw/smiley_run/` — history
`20260805_151048_746130_microclaw_history.jsonl`, its confirmations log, and the
run outputs (`smiley/ch640`, `smiley/ch561`, `smiley_mosaic_640.tiff`,
`snr_640.log`, `snr_561.log`).

The acquisition worked. Autofocus converged with interior peaks on both passes,
the focus lock held, 18 frames landed across 9 tiles in two channels in 10.1 s
total, and the stage-coordinate mosaic built from the saved dataset at zero
extra dose. Nothing below is about the acquisition.

What failed is everything after it. The user asked for the routine as a
standalone pycro-manager script — the thing `CLAUDE.md` says every tool call
must compile to — and there was no tool that could produce one or write one to
disk. The session ended with the assistant offering to re-paste a truncated
script into chat for the user to copy by hand.

---

## F1 — "compiles to a standalone script" is a principle with no implementation

**FIXED — block 41b, merged 2026-08-06.** `export_session_script` emits from the
record; analysis is inlined from source; unemittable tools refuse with a reason
and a loud `RuntimeError`. M5 gate rounds 4–5: the script ran to completion with
microclaw closed and its datasets are byte-identical to the session's, one
acquisition per position — the reconstructed `build_mosaic` that re-imaged every
kept tile is exactly what the offline-mosaic refusal now prevents.

The user's ask (`:44`): *"Can you compile this whole routine to a pycro-manager
script that can run outside of microclaw?"* Then (`:50`): *"Can you leave it
as-is and save this script next to the file it made?"*

The honest answer the assistant gave (`:51`) is correct and damning:

> My file-writing tools are `generate_and_save_hook` (writes only to the hooks
> directory […]) and the acquisition/mosaic tools (which write datasets and
> TIFFs, not source files). None of them can drop a `.py` at a path I choose.

So the model did the only thing left: it wrote the script **from memory, into
chat**. That reconstruction is not equivalent to what ran, and it said so — it
could not reproduce `compute_stats` (`analyzer_version 0.1.0`) or `run_autofocus`
because it cannot see their source.

It is worse than "not bit-identical". The reconstructed `build_mosaic` (`:47`)
**re-images every kept tile**:

```python
def build_mosaic(core, tiles_to_place, z_um, wavelength, out_path):
    """Re-image kept tiles in `wavelength` and place them on one canvas by
    stage XY, using the recorded affine. Overlaps: later tile wins."""
    enable_channel(core, wavelength)
```

The session built its mosaic offline from the saved NDTiff at **zero dose**
(`:33`, `build_stage_coordinate_mosaic`). The script the user was told to copy
and run doubles the dose on a bleaching sample. An LLM reconstructing microclaw
internals it cannot read is not an acceptable export path, however careful the
caveats.

### Decision — emit from the record, don't reconstruct from memory

Every tool call is already recorded append-only. Give each hardware and
acquisition tool an **emitter** that renders *its own call* as pycro-manager
source, and add one tool that walks the session record and writes the file.

```python
# tools.py — the emitter lives next to the tool it emits, never in a registry.
@emits(lambda p: f"core.set_xy_position({p['x_um']}, {p['y_um']})")
def move_stage_xy(ctrl, guard, x_um, y_um): ...

def export_session_script(ctrl, guard, output_path, records):
    """Write the session's hardware calls as a runnable pycro-manager script.

    Analysis functions are INLINED FROM SOURCE (inspect.getsource of the pure
    numpy functions in image_analysis), not re-derived: the emitted snr() is
    the snr() that ran. Inlining, not importing, keeps the script standalone.
    """
    path = guard.resolve_in_workspace(output_path)   # same gate as the mosaic
```

Three properties that make this worth building rather than prompting harder for:

- **`resolve_in_workspace`** is the existing path gate — "next to the file it
  made" is already a legal destination, because the mosaic went there.
- **Inlined source, not reimplementation.** `snr`, `compute_stats`, and the
  autofocus sweep are pure numpy. Their real source goes in the file, so the
  emitted script's numbers *are* the session's numbers.
- **A tool with no emitter emits a refusal, not a guess** — the script carries a
  literal `# NOT EMITTED: <tool>` line and fails loudly at that point, rather
  than a plausible-looking fabrication of what that step did.

Scope note: the emitted script reproduces the *hardware routine*. Nothing about
this puts microclaw runtime state in the script — that is the point of it.

---

## F2 — `max_tokens=4096` truncated the script and ended the turn

**FIXED — block 41a, merged 2026-08-05.** Cap raised to 8192; truncation is a
named recoverable condition and unwinds any `tool_use` blocks it cut. See
design/16 §5 "The invariant is not about Stop".

`agent.py:291` caps every model reply at 4096 tokens. The script did not fit.
Message `:47` ends mid-function, inside an unclosed ``` fence:

```
    verify_trigger(core, wavelength)

    # gather images + their stage XY
```

`stop_reason` was then `max_tokens`, which falls through to `agent.py:388`:

```python
if response.stop_reason != "tool_use":
    yield {"type": "error",
           "message": f"[Unexpected stop reason: {response.stop_reason}]"}
    return
```

The turn dies. The user typed *"Nooo please continue"* (`:48`), the model resumed
mid-file, and the deliverable now exists as two chat fragments that have to be
spliced by hand across an unbalanced code fence.

**Fix, in order of what actually matters:**

1. **F1 removes the need.** A long deliverable should never travel through the
   chat channel. This is the fix; 2 and 3 are hygiene.
2. `max_tokens` is a **named, expected** condition, not "unexpected". Report it
   as truncation and say the reply was cut, so the user isn't guessing.
3. Raise the cap. 4096 is low for a model that must both narrate and act.

**Latent defect on the same path:** truncation inside a `tool_use` block appends
an assistant turn with an unanswered tool call. `_unwind_cancel` exists exactly
to stop that (`agent.py:229`, design/16 §5) and the `max_tokens` path does not
call it. The next prompt 400s from a history that looks fine in the viewer. It
did not bite here only because the truncation happened to land in a text block.

---

## F3 — only 529 is retried; every other API failure kills the session

**FIXED — block 41a, merged 2026-08-05.** 429/5xx/connection/timeout share the
backoff, `retry-after` is honoured to a 60 s cap, and the rollback moved to the
common failure boundary. See design/16 §5 "The invariant is not about Stop".

This is the most likely mechanism behind "I ran out of turns", which the
operator could not otherwise account for (they had credits, and the longest turn
used ~17 of the 50 allowed rounds).

`_stream_one_round` retries exactly one exception type:

```python
except anthropic._exceptions.OverloadedError:      # 529 only
    if attempt == len(_RETRY_DELAYS): raise _Overloaded from None
```

A `RateLimitError` (429), `InternalServerError` (500), `APIConnectionError`, or
a read timeout is **not** caught here. It escapes `run_agent_iter` into
`webserve.py:591`'s blanket `except Exception`, which emits an error banner and
ends the turn. A 429 is plausible precisely here: the model was streaming large
code blocks, which is when output-token rate limits bind.

Two things follow, and they are independent:

- **Retry the retryable.** 429 and 5xx belong in the same backoff as 529 —
  respecting `retry-after` when the response carries it.
- **`del messages[start:]` only runs on the `_Overloaded` path** (`agent.py:369`).
  Every other escape leaves the partial turn in `messages`. The rollback belongs
  with the failure, not with one flavour of it.

---

## F4 — every tile was saturated, so the SNR gate decided nothing

**FIXED — block 13, merged 2026-08-06.** `snr_validity()` states the rule once:
saturation above 0.01% invalidates SNR *and* the focus metric; `compute_stats`
reports `snr: null` with `snr_invalid_reason`. Confirmed on M5 at 80 ms
(0.0135% saturated → refused, focus metric refused with it). The gate also found
that the payload rounded `saturated_fraction` to the same resolution as its own
threshold, so a clipped frame could print `0.0`; now reported to 6 places.

The user asked to keep a tile only if it had signal in **both** channels, SNR > 3
(`:6`). All 9 tiles passed in both, and the reported margins were 173–716 (640)
and 277–904 (561). Those numbers are not real SNRs. From the hook logs:

| tile | 640 SNR | sat % | 561 SNR | sat % |
| --- | --- | --- | --- | --- |
| L_eye_a | 360.3 | 0.048 | 527.3 | 0.201 |
| R_eye_b | 715.8 | 0.222 | 903.7 | 0.383 |
| mouth_L | 452.6 | 0.261 | 396.4 | 0.609 |
| mouth_R | 451.2 | 0.341 | 389.3 | 0.636 |

**`max_intensity` is 65535 on all 18 frames.** Every tile clipped, in both
channels. `saturated_fraction` was computed, logged, and ignored — by the hook,
which has no saturation term, and by the assistant, which read the logs and
tabulated only SNR.

The exposure was approved **before focusing**: the operator said "exposure is
good" (`:6`) against a snap at Z 42.376 with `max_intensity` 6637 and
`saturated_fraction` 0.0 (`:14`). Autofocus then moved to Z 44.376, the beads
came into focus, and the same exposure clipped. Nothing re-checked it.

- A gate whose margin is 50–300× is not measuring the thing it claims to. Every
  tile "passing" is the symptom, and here it happened to match the truth (all 9
  tiles did have beads) — which is exactly why it went unnoticed.
- `snr()` uses p99.5, not `max()`, so it degrades rather than pinning; that is
  why the number looks like a plausible large SNR instead of an obvious error.
- The autofocus fine sweep peaked at `focus_metric` 9.99e7, ~500× the in-focus
  snap's 1.9e5 at the same field. At least the top of that sweep was measured on
  clipped data. Tenengrad on a clipped bead measures the edge of a plateau. This
  run converged with interior peaks and the result looks right; the concern is
  that the sweep has no way to tell us when it wasn't.

**Decision.** `saturated_fraction` is already in `ImageStats` — the fix is to
make it *mean* something, not to add a validator layer:

- `compute_stats` reports SNR as **invalid above a saturation fraction**, the
  same way `focus_metric_valid` already gates the focus metric below `min_snr`.
  One existing pattern, extended; no new gate object.
- `snap_and_analyze` and the acquisition tools surface clipping in the result
  the model reads, so "exposure is good" can be re-asked after a focus move.

---

## F5 — `axis_selection` cost two rounds to a question the dataset answers

**FIXED — block 13, merged 2026-08-06.** Singleton axes default to their only
value; an ambiguous case names every non-position axis with its values. Confirmed
on M5: one call, no `axis_selection`, result `selection: {"time": 0, "z": 0}` —
the three calls of the smiley run became one.

Three calls to `build_stage_coordinate_mosaic` to place 9 tiles (`:33`–`:37`):

```
{"time": 0}         -> axis_selection must fix every non-position axis: ['z']
{"z": 0}            -> axis_selection must fix every non-position axis: ['time']
{"z": 0, "time": 0} -> ok
```

The error names only what this call omitted, so fixing it reveals the next
omission. Both axes were **length 1** — a timelapse with `n_frames=1` — so both
calls had exactly one legal completion and the tool made the caller guess it
twice. On the scarce-turn budget of F2/F3 that is two rounds spent on ceremony.

`tools.py:1274`:

```python
    missing = non_position_axes - set(axis_selection)
    if missing:
        raise ValueError(f"axis_selection must fix every non-position axis: {sorted(missing)}")
```

**Decision.** Singleton axes default to their only value — a length-1 axis has
nothing to select. When something genuinely ambiguous remains, the error states
the **full** non-position axis set with each axis's available values, so one
correction is always enough.

---

## F6 — no Channel group, so channels were raw property writes

M5 has no `Channel` config group (`get_available_channels` → `{"channels": []}`,
`:4`; see also the standing note that M5 has only a `System` group). The
channel-plan executor (design/33 Phase 4) drives config-group presets, so it had
nothing to drive. The two-channel requirement was met by hand (`:27`):

```json
{"device": "Thorlabs Filter Wheel", "property": "State", "value": "1"}
{"device": "iChrome-MLE-TCP", "property": "Laser 2: 1. Enable", "value": "1"}
{"device": "iChrome-MLE-TCP", "property": "Laser 1: 1. Enable", "value": "0"}
```

The writes are correct — EMU slot order is reversed, so slot 3 (640) really is
`Laser 1` — but the assistant's own narration got it wrong in the same breath,
calling it *"640 (slot 1 enable) disabled"*. A reversed index map being hand-
applied to laser enables is one off-by-one away from arming the wrong line, and
the illumination confirmation only fires on the enable, never the disable.

design/39 already reads EMU's `parameters` block, so the slots carry their
configured names ("640", "561"). **Decision: build the channel plan from the EMU
laser map when no `Channel` config group exists** — same executor, same typed
plan, sourced from the map instead of from presets. This is not a new layer; it
is the missing source for one that exists. It also makes channel switching
emittable under F1, which raw `set_device_property` calls are not, in any
readable way.

---

## F7 — entry state was not restored

**FIXED — block 41a, merged 2026-08-05.** Prompt wording only: the laser bullet
now names the entry state — what microclaw turned on it turns off, what it found
on it leaves on.

640 was enabled at 2.24% when the session opened (`:2`). It was left **off**
(`:42`), and the assistant flagged this plainly rather than hiding it. The filter
wheel was likewise left at 600/60 from the 561 pass, not returned to the 685/70
it was set to at `:11`, and live view was left running.

This is small and it was reported honestly, but it cuts against "the user owns
the session": the operator's pre-existing laser state is not microclaw's to
clean up. The system prompt already says *"leave operator-established lasers as
found unless asked"* (`agent.py:103`) and the model read that as covering only
lasers it enabled. The rule should name the entry state explicitly: what
microclaw turned on, microclaw turns off; what it found on, it leaves on.

---

## Priority

F1 is the block. It is the stated architectural principle, the user asked for it
directly, and its absence is what turned a clean run into a manual copy-paste.
F3 and F2 are the session-survival pair and are cheap. F4 is a correctness bug
in a decision the user explicitly specified. F5, F6, F7 are follow-ons.
