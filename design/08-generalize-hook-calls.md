# Generalizing hook-based acquisitions beyond Z-stacks

## Context

`run_adaptive_acquisition` (`microclaw/tools.py:811`) currently hard-codes a
**Z-stack** event list. It builds events with `multi_d_acquisition_events(z_start=,
z_end=, z_step=, ...)` and attaches the hook's `post_hardware_hook_fn` /
`image_process_fn` to the `Acquisition`.

```python
# tools.py:861-873 (current)
acq_kwargs = {"z_start": z_start_um, "z_end": z_end_um, "z_step": z_step_um}
if channel:
    acq_kwargs.update(channel_group="Channel", channels=[channel])
events = multi_d_acquisition_events(**acq_kwargs)

hook_fn_kwargs = {}
if hasattr(hook, "post_hardware_hook_fn"):
    hook_fn_kwargs["post_hardware_hook_fn"] = hook.post_hardware_hook_fn
if hasattr(hook, "image_process_fn"):
    hook_fn_kwargs["image_process_fn"] = hook.image_process_fn

with Acquisition(directory=save_dir, name=name, show_display=True, **hook_fn_kwargs) as acq:
    acq.acquire(events)
```

### Does it *have* to be a Z-stack?

**No.** Nothing in the hook machinery is Z-specific. A hook is just one or both of
two callables that pycromanager/AcqEngJ invoke during *any* acquisition:

- `post_hardware_hook_fn(event)` — runs after hardware moves, before capture.
- `image_process_fn(image, metadata, event_queue)` — runs per captured image.

The only Z-specific part is the **event list**. `multi_d_acquisition_events` is
already the shared builder used by both `run_zstack` (`tools.py:339`) and
`run_timelapse` (`tools.py:372`); the timelapse path just passes
`num_time_points` / `time_interval_s` instead of `z_start` / `z_end` / `z_step`.

In fact the mismatch is already visible in the hook library:
`FocusFeedbackHook` (`microclaw/hooks.py`) is documented as *"Corrects Z drift
per frame during timelapse"* and keys its log on `metadata.get("time")` — yet
today it can **only** be launched against a Z-stack event list. So generalizing
isn't speculative; one shipped hook is mis-served by the current signature.

---

## Proposals

Four approaches, roughly from smallest-diff to largest-refactor.

### Approach A — Add a `mode` switch to `run_adaptive_acquisition`

Keep one adaptive tool, branch the event-building on a `mode` argument.

```python
def run_adaptive_acquisition(
    ctrl, guard,
    save_dir: str,
    hook_strategy: str,
    mode: str = "zstack",                 # "zstack" | "timelapse"
    hook_params: dict | None = None,
    channel: str | None = None,
    # zstack params
    z_start_um: float | None = None,
    z_end_um: float | None = None,
    z_step_um: float | None = None,
    # timelapse params
    n_frames: int | None = None,
    interval_s: float | None = None,
    name: str = "adaptive",
    log_path: str | None = None,
) -> dict:
    ...
    if mode == "zstack":
        guard.check_z(z_start_um); guard.check_z(z_end_um)
        acq_kwargs = {"z_start": z_start_um, "z_end": z_end_um, "z_step": z_step_um}
    elif mode == "timelapse":
        acq_kwargs = {"num_time_points": n_frames, "time_interval_s": interval_s}
    else:
        return {"error": f"Unknown mode '{mode}'. Use 'zstack' or 'timelapse'."}
    if channel:
        acq_kwargs.update(channel_group="Channel", channels=[channel])
    events = multi_d_acquisition_events(**acq_kwargs)
    # ... unchanged hook attach + Acquisition block ...
```

**Benefits**

- Smallest conceptual change; one tool, one schema entry to update.
- Mode-specific validation stays explicit.

**Drawbacks**

- Parameter soup: many mutually-exclusive optional args; the LLM must learn
  which combine with which `mode`. Easy to call with a missing param.
- Doesn't reduce duplication between `run_zstack` / `run_timelapse` and the
  adaptive tool — three places still independently build events + guard Z.
- Adding a third acquisition shape later (e.g. multi-position) means editing the
  branch again.

---

### Approach B — Extract a shared `_acquire_with_hooks` helper (recommended)

Separate the two concerns that are currently entangled: **building the event
list** and **running an acquisition (optionally with a hook)**. Then *every*
acquisition tool — plain or adaptive — funnels through one runner.

```python
def _build_events(*, channel=None, exposure_ms=None, **acq_kwargs):
    """Thin wrapper over multi_d_acquisition_events with channel handling."""
    if channel:
        acq_kwargs.update(channel_group="Channel", channels=[channel])
        if exposure_ms is not None:
            acq_kwargs["channel_exposures_ms"] = [exposure_ms]
    return multi_d_acquisition_events(**acq_kwargs)


def _acquire_with_hooks(save_dir, name, events, hook=None) -> str:
    """Run one Acquisition, attaching hook callables if a hook is given.
    Returns the on-disk dataset path."""
    hook_fn_kwargs = {}
    if hook is not None:
        if hasattr(hook, "post_hardware_hook_fn"):
            hook_fn_kwargs["post_hardware_hook_fn"] = hook.post_hardware_hook_fn
        if hasattr(hook, "image_process_fn"):
            hook_fn_kwargs["image_process_fn"] = hook.image_process_fn
    with Acquisition(directory=save_dir, name=name, show_display=True, **hook_fn_kwargs) as acq:
        acq.acquire(events)
    return acq._dataset_disk_location or str(Path(save_dir) / name)


def _resolve_hook(ctrl, guard, hook_strategy, hook_params, log_path):
    """Existing registry/saved-hook lookup + ctrl/guard injection, factored out."""
    ...
    return hook  # or raise / return error
```

`run_zstack` / `run_timelapse` shrink to event-building + `_acquire_with_hooks(...)`
with `hook=None`. The adaptive tool resolves a hook and passes it in. Crucially,
the *adaptive* concern becomes orthogonal to the *event-shape* concern:

```python
def run_adaptive_zstack(ctrl, guard, z_start_um, z_end_um, z_step_um, save_dir,
                        hook_strategy, hook_params=None, channel=None,
                        name="adaptive", log_path=None) -> dict:
    guard.check_z(z_start_um); guard.check_z(z_end_um)
    hook = _resolve_hook(ctrl, guard, hook_strategy, hook_params, log_path)
    events = _build_events(channel=channel, z_start=z_start_um,
                           z_end=z_end_um, z_step=z_step_um)
    path = _acquire_with_hooks(save_dir, name, events, hook)
    return _adaptive_result(path, log_path)


def run_adaptive_timelapse(ctrl, guard, n_frames, interval_s, save_dir,
                           hook_strategy, hook_params=None, channel=None,
                           name="adaptive", log_path=None) -> dict:
    hook = _resolve_hook(ctrl, guard, hook_strategy, hook_params, log_path)
    events = _build_events(channel=channel, num_time_points=n_frames,
                           time_interval_s=interval_s)
    path = _acquire_with_hooks(save_dir, name, events, hook)
    return _adaptive_result(path, log_path)
```

**Benefits**

- Removes the triplicated Acquisition/event boilerplate across `run_zstack`,
  `run_timelapse`, and adaptive; one place to fix display/path/hook bugs.
- Each tool keeps a **flat, non-overlapping** parameter list — best ergonomics
  for the LLM and the schema; required params are genuinely required.
- New acquisition shapes are a few lines (build events + call runner).
- Naturally fixes the `FocusFeedbackHook` timelapse gap.

**Drawbacks**

- Two adaptive tools instead of one → two schema entries, and the model picks.
- Slightly more surface area than Approach A (helpers + tools).
- Touches `run_zstack` / `run_timelapse` (regression-test the plain paths).

---

### Approach C — Make `hook_strategy` an optional arg on the existing tools

Don't add adaptive tools at all; fold the hook into `run_zstack` and
`run_timelapse` as optional parameters. "Adaptive" stops being a separate verb.

```python
def run_zstack(ctrl, guard, z_start_um, z_end_um, z_step_um, save_dir,
               channel=None, exposure_ms=None, name="zstack",
               hook_strategy=None, hook_params=None, log_path=None) -> dict:
    ...
    hook = _resolve_hook(...) if hook_strategy else None
    events = _build_events(channel=channel, exposure_ms=exposure_ms,
                           z_start=z_start_um, z_end=z_end_um, z_step=z_step_um)
    path = _acquire_with_hooks(save_dir, name, events, hook)
    ...
```

**Benefits**

- Fewest tools — collapses `run_adaptive_acquisition` away entirely.
- Conceptually clean: a hook is just an optional modifier of any acquisition.
- Zero new event-shape branching; works for timelapse for free.

**Drawbacks**

- Overloads tools the model already uses for the simple case; the schema for
  `run_zstack`/`run_timelapse` gains hook params + the `read_hook_log` follow-up
  workflow, which may make routine calls noisier.
- Loses the dedicated, discoverable "adaptive" tool name and its tailored
  description listing strategies — the agent may not realize hooks are possible.
- Result payload must conditionally include `log_path`/`hint` only when a hook
  ran, complicating one shared return shape.

---

### Approach D — One generic `run_acquisition` tool

A single tool takes a free-form `axes`/event spec plus an optional hook, e.g.
`{"z": {...}}` and/or `{"time": {...}}`, and builds events generically.

**Benefits**

- Maximum flexibility: combined Z+time+multi-position in one call; one tool to
  maintain.

**Drawbacks**

- Worst ergonomics for an LLM: nested, weakly-typed spec object; guard
  validation must introspect the spec; hard to express "required" per shape.
- Biggest departure from the current, working, well-scoped tool set; highest
  regression risk for the least near-term payoff.
- Over-engineered for two concrete shapes (zstack, timelapse) that exist today.

---

## Recommendation

**Approach B.** It directly answers the question — yes, a hook can attach to a
timelapse as easily as a z-stack — by making the hook orthogonal to the event
shape, while *reducing* duplication rather than adding a parameter-soup branch.
It keeps the per-tool schemas flat and honest (best for the agent), and it
closes the real `FocusFeedbackHook`-on-timelapse gap that exists today.

If minimizing tool count is the priority and you're comfortable overloading the
basic acquisition tools, **Approach C** is the tidiest mental model; it pairs
well with the same `_resolve_hook` / `_build_events` / `_acquire_with_hooks`
helpers, so B and C share the bulk of their implementation and the choice is
mainly about tool surface, not internals.

### Suggested follow-up (either B or C)

1. Add the `_build_events`, `_acquire_with_hooks`, `_resolve_hook`,
   `_adaptive_result` helpers in `tools.py`.
2. Refactor `run_zstack` / `run_timelapse` onto them (no behavior change).
3. Add `run_adaptive_timelapse` (B) **or** `hook_strategy` params (C).
4. Update `TOOL_REGISTRY` (`tools.py:1093+`) and `tools_schema.py:597`.
5. Add a test that drives `FocusFeedbackHook` through a timelapse event list to
   lock in the previously-impossible path.
