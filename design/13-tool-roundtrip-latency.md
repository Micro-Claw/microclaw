# 13 — Why the 3×3 grid prompt took ~52 s (and didn't finish)

Evidence: `20260703_125632_microclaw_history.json` (saved with `--save-history`)
and `profile-output.txt` (cProfile of `__main__.main()` for the same run).

Prompt under test:

> "Using the current imaging settings, image a 3x3 grid in steps of half the
> size of the field of view. Place all of the positions used in the stage
> position list. Snap an image at each position you move to once it is placed
> in the stage position list, to display to the user. Don't save or analyse
> the snapped images."

## TL;DR

The 52 s is almost entirely **network wait on the Anthropic API, multiplied by
API round-trip count** — not local compute and not slow hardware calls. The
agent made **25 sequential `messages.create` calls** (~2.0 s each) because the
task decomposed into three *dependent* tool calls per grid position
(`move_stage_xy` → `mark_position` → `snap_image`), each requiring its own
model round trip. Worse, 25 is exactly `DEFAULT_MAX_ITERATIONS`, so the run
**hit the iteration cap and returned incomplete**: position 8 was never
snapped and position 9 was never visited.

The one-shot tool that fits this task (`run_tile_acquisition`,
`protocol="snap"`) could not be used because it has no way to satisfy "place
all of the positions in the stage position list". That capability gap is the
root cause; the latency is the symptom.

## Reading the profile

```
5630116 function calls in 52.522 seconds
   ncalls  tottime  percall  filename:lineno(function)
       64   49.673    0.776  {method 'read' of '_ssl._SSLSocket' objects}
     2227    0.711           {method 'acquire' of '_thread.lock' objects}
128017/25    0.377   (1.567 cum)  anthropic/_utils/_transform.py:154(_transform_recursive)
```

- **94.6 % of wall time (49.7 s) is blocked in SSL socket reads** — waiting on
  api.anthropic.com. Nothing local is slow.
- The `/25` primitive-call count on `_transform_recursive` confirms **25
  `messages.create` requests**. 49.7 s / 25 ≈ **2.0 s average per API round
  trip** — normal Claude API latency for a short tool-use response. The delay
  is the *count*, not the per-call cost.
- The Anthropic SDK's request-body transform costs 1.57 s cumulative across
  the whole run (typing introspection on the growing message list). Real, but
  a rounding error next to the network time. Not worth optimizing.
- Micro-Manager / pyjavaz time doesn't even make the top 20 — the demo-config
  stage moves and snaps are effectively free here.

## Reading the history

The transcript is a strict serial chain. After two orientation rounds
(`get_system_state`, then `get_pixel_size` + `get_roi` batched — the batching
guideline worked where calls were independent), every position costs three
rounds because each call *depends on the previous one's effect*:

```
round n   : move_stage_xy(x, y)      → must complete before marking
round n+1 : mark_position("grid_rX_cY")  → reads current stage position
round n+2 : snap_image()             → must happen at the marked position
```

Parallel tool use can't help — these are ordered by data flow. So the task
needs 2 + 9×3 + 1(final text) = **30 rounds**, but `DEFAULT_MAX_ITERATIONS = 25`
(`agent.py:20`). The history ends at round 25 (`mark_position("grid_r2_c1")`),
meaning the user got the "Stopped after 25 tool rounds without completing"
message, ~52 s in, with 8/9 positions marked and 7/9 snapped.

So there are really two bugs:

1. **Latency**: round-trip count scales linearly with grid size (a 5×5 grid
   would be ~77 rounds ≈ 2.5 min).
2. **Correctness**: any grid ≥ 8 positions exceeds the iteration budget and
   silently truncates the acquisition.

## Why the model didn't use the one-shot tool

`run_tile_acquisition(rows, cols, step_um, protocol="snap")`
(`tools.py:786`) computes the grid and snaps at every tile in a single tool
call — exactly this task — but neither it nor `run_multiposition_acquisition`
can add the visited positions to the position list. The prompt made the
position list a hard requirement, so the model (correctly, given its tools)
fell back to the manual loop. The system prompt even discourages manual
loops ("do not manually loop over go_to_position") but offers no compliant
alternative for *snap + mark* surveys.

## Fixes, in order of impact

### 1. Let the batch tools mark positions (primary fix, ~8–10× speedup)

Add a `mark_positions` flag to `run_multiposition_acquisition` and thread it
through `run_tile_acquisition`. The whole prompt then becomes: 2 orientation
rounds + 1 `run_tile_acquisition` round + 1 closing text ≈ **4 API calls,
~8 s**, and the iteration cap becomes irrelevant.

```python
# tools.py

def _run_protocol_at(
    ctrl, guard, pos_label, x_um, y_um, z_um, protocol, pos_save_dir, params,
    mark_position_in_list: bool = False,
) -> dict:
    guard.check_xy(x_um, y_um)
    ctrl.core.set_xy_position(x_um, y_um)
    _wait(ctrl, ctrl.core.get_xy_stage_device())
    if z_um is not None:
        guard.check_z(z_um)
        ctrl.core.set_position(z_um)
        _wait(ctrl, ctrl.core.get_focus_device())
    if mark_position_in_list:
        # Mirror into microclaw's list + MM's PositionList (same path as
        # the mark_position tool), so the GUI list shows the grid.
        ctrl.add_position(pos_label, round(x_um, 3), round(y_um, 3),
                          round(z_um, 3) if z_um is not None else None)
    if protocol == "snap":
        ctrl.studio.live().snap(True)
        return {"position": pos_label, "status": "snapped", "saved": False,
                **({"marked": True} if mark_position_in_list else {})}
    # ... zstack / timelapse branches unchanged, but only mkdir when saving:
    Path(pos_save_dir).mkdir(parents=True, exist_ok=True)
    ...


def run_multiposition_acquisition(
    ctrl, guard, protocol, save_dir=None, position_names=None, positions=None,
    name="multipos", protocol_params=None,
    mark_positions: bool = False,          # NEW
) -> dict:
    ...
    for pos_label, x_um, y_um, z_um in resolved:
        result = _run_protocol_at(
            ctrl, guard, pos_label, x_um, y_um, z_um, protocol,
            pos_save_dir, params, mark_position_in_list=mark_positions,
        )
    ...


def run_tile_acquisition(
    ctrl, guard, rows, cols, step_um, protocol, save_dir=None,
    name="tile", protocol_params=None,
    mark_positions: bool = False,          # NEW
) -> dict:
    ...
    positions = [
        {"name": f"{name}_r{r}_c{c}", ...} ...
    ]
    return run_multiposition_acquisition(
        ..., mark_positions=mark_positions,
    )
```

Two small warts to fix while in there:

- `save_dir` is currently required even for `protocol="snap"` (display-only),
  and `_run_protocol_at` mkdirs it unconditionally. Make it optional and only
  create directories on the saving protocols — otherwise the model must invent
  a junk path for a task that saves nothing.
- Position labels ignore the `name` prefix (`f"r{r}_c{c}"`); use
  `f"{name}_r{r}_c{c}"` so repeated grids don't collide in the position list.

Schema addition (`tools_schema.py`, on both tools):

```python
"mark_positions": {
    "type": "boolean",
    "description": (
        "Also record every visited position into the stage position list "
        "(microclaw's list + MM's Position List Manager), as mark_position "
        "would. Default false."
    ),
    "default": False,
},
```

System-prompt nudge (`agent.py`), extending the existing anti-manual-loop
guideline so the model reaches for the batch tool even when the user asks for
position-list bookkeeping:

```
- For grid or multi-position surveys, use run_tile_acquisition /
  run_multiposition_acquisition — including when the user wants the visited
  positions in the position list (pass mark_positions=true). Do not manually
  loop move_stage_xy / mark_position / snap_image; each manual step costs a
  full model round trip.
```

### 2. Raise / parameterize the iteration cap (correctness guard)

Even with fix 1, some legitimately chatty tasks will exceed 25 rounds. Bump
the default and surface partial progress in the bail-out message:

```python
# agent.py
DEFAULT_MAX_ITERATIONS = 50  # was 25; a 3x3 manual grid alone needs 30

# and in the bail-out return, tell the user *and the next turn's model*
# what was completed:
return (
    f"Stopped after {max_iterations} tool rounds without completing. "
    f"Progress so far is preserved in the conversation — you can say "
    f"'continue' to resume, or narrow the task.",
    messages,
)
```

(The history *is* returned, so "continue" genuinely works today; the current
message just doesn't tell the user that.)

### 3. Cache the conversation prefix (secondary, shaves per-round latency)

`system` and `tools` already carry `cache_control` breakpoints, but the
message list — which regrows every round and dominates input tokens by round
10+ — is re-processed uncached on every request. Moving a third breakpoint
along the conversation makes each round's input a cache hit up to the previous
round. With 25 rounds this trims time-to-first-token on every call; expect
roughly 10–25 % off total wall time for long tool loops (it does not reduce
the round count, so do fix 1 first).

```python
# agent.py, inside the iteration loop, before messages.create:

def _with_cache_breakpoint(messages: list[dict]) -> list[dict]:
    """Return messages with cache_control on the last content block of the
    final message, without mutating the caller's history."""
    if not messages:
        return messages
    last = messages[-1]
    content = last["content"]
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    elif not isinstance(content, list):
        return messages  # response.content objects: skip, blocks are models
    content = [*content[:-1], {**content[-1], "cache_control": {"type": "ephemeral"}}]
    return [*messages[:-1], {**last, "content": content}]

response = _get_client().messages.create(
    model=model,
    max_tokens=4096,
    system=system_blocks,
    tools=TOOLS_CACHED,
    messages=_with_cache_breakpoint(messages),
)
```

Notes: the API allows 4 breakpoints total (tools + system use 2, this is the
3rd). Assistant messages appended from `response.content` are SDK model
objects, not dicts — the helper above only stamps the tool_result/user dicts
we build ourselves, which is fine because we always call the API right after
appending a tool_result. Cache reads bill at 10 % of input; with a growing
25-round transcript this is also a meaningful cost reduction.

### Alternative considered: a generic `run_tool_sequence` meta-tool (not preferred)

Recorded for reference; **the composite-tool approach (fix 1) is the preferred
solution** and this option is unlikely to be used, for the caveats below.

The idea: since the model already knows all 27 calls upfront for a
deterministic task like a grid, let it emit the whole plan as *one* tool
input and iterate locally — one API round trip for any dependent sequence,
not just grids. (Emitting them as 27 `tool_use` blocks in one response is
not reliable: parallel tool use has no ordering contract, so the model is
trained to serialize dependent calls across round trips. Making the batch a
tool *input* sidesteps that.)

```python
# tools.py — sketch only, not implemented

# Tools that are safe to batch: deterministic, no confirmation prompts,
# no mid-sequence decisions needed. Everything else stays interactive.
_SEQUENCEABLE_TOOLS = {
    "move_stage_xy", "move_stage_z", "mark_position", "snap_image",
    "set_exposure", "set_channel", "go_to_position",
}

def run_tool_sequence(ctrl, guard, steps: list[dict]) -> dict:
    """steps = [{"tool": "move_stage_xy", "input": {...}}, ...]
    Executes strictly in order; stops at the first error so later steps
    never run in a wrong state."""
    results = []
    for i, step in enumerate(steps):
        tool = step.get("tool")
        if tool not in _SEQUENCEABLE_TOOLS:
            results.append({"step": i, "tool": tool,
                            "error": "Not allowed in a sequence."})
            return {"status": f"stopped at step {i} of {len(steps)}",
                    "results": results}
        # execute_tool returns a JSON string (or content-block list for
        # image tools — excluded by the allowlist above).
        r = json.loads(execute_tool(tool, step.get("input", {}), ctrl, guard))
        results.append({"step": i, "tool": tool, **r})
        if "error" in r:
            return {"status": f"stopped at step {i} of {len(steps)}",
                    "results": results}
    return {"status": f"all {len(steps)} steps completed", "results": results}
```

Why it loses to the composite tool:

- **No mid-sequence adaptivity.** The model commits the entire plan before
  seeing any result. Fine for stage choreography; wrong for anything where a
  result should change the plan (autofocus, adaptive acquisition, reacting to
  the coordinate rounding visible in this run's history, where `mark_position`
  reported `512.01` for a requested `512.005`).
- **Output-token cost.** The model must generate all N step objects as
  output tokens — a 27-step plan adds several seconds of generation to the
  single round trip. `run_tile_acquisition`'s three-parameter input is
  cheaper and less error-prone to generate.
- **Safety/confirmation semantics.** Confirmation-gated tools (hook saves,
  save_knowledge) must be excluded via allowlist, and the allowlist becomes
  one more thing to keep in sync as tools are added.
- **Schema validation burden.** Nested tool inputs bypass the API's
  per-tool `input_schema` validation; malformed step inputs surface only at
  execution time.

### Not worth doing

- **Optimizing the SDK transform / typing overhead** (1.6 s total): noise.
- **Streaming**: improves perceived latency for text, but this run's output
  is dominated by tool calls; total wall time is unchanged.
- **Anything on the Micro-Manager side**: hardware time is invisible in the
  profile (demo config); on real hardware the stage/camera time inside one
  `run_tile_acquisition` call is irreducible physics anyway.

## Expected outcome

| | today | after fix 1 (+3) |
|---|---|---|
| API round trips | 25 (capped, incomplete) | ~4 |
| Wall time | 52.5 s | ~6–8 s |
| Task completed | 7/9 snaps, 8/9 marks | 9/9 |
