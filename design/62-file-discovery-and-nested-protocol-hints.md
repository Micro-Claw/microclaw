# design/62 — make nested acquisition parameters and local inputs discoverable

## Observed failures

Session `sarah-check-downloads/20260827_160401_908523_microclaw_history.jsonl`
(76 turns, M5, 2026-08-27) contains three capability-discovery failures. They
are listed in the order of what they cost, which is not the order they occur in.

### 1. The project's own label names cannot be read without a dataset (costs exposures)

Turns 51–53. The user named a `.ilp` and asked for a position of apoptotic
mitochondria. The agent needed the project's three label roles, correctly
refused to invent them, and probed for them with a placeholder dataset path:

```json
{"dataset_path": "C:\\Users\\ries\\Downloads\\__probe_labels__",
 "adapter": "ilastik_pixel_classification",
 "parameters": {"project_path": "...\\260825_Mito-classify_M5.ilp",
                "background_label": "__unknown__", ...},
 "output_dir": "C:\\Users\\ries\\Downloads\\__probe_out__"}
```

It returned `FileNotFoundError ... __probe_labels__`. The agent's conclusion
(turn 53) was the expensive one: *"I can't extract the labels without a dataset,
and I shouldn't invent them. So the survey genuinely has to come first."*

The labels are a plain local read — `PixelClassification/LabelNames`,
`ilastik_adapter.py:389`. The reason the probe failed is ordering in
`completed_dataset.run_analysis_on_saved_dataset`:

```text
:367  instance = cls(**parameters)      # stores strings; opens nothing
:368  Path(output_dir).mkdir(...)       # left __probe_out__ behind
:369  dataset = Dataset(dataset_path)   # FileNotFoundError, here
```

`_check_configured_labels` lives in `analyze_completed_dataset`, behind the
dataset view, behind ilastik discovery and the executable check. The comment at
`ilastik_adapter.py:395` records the same defect being moved earlier once
already — out of `pool_probability_map`, because a mistyped label was refused
*after* ilastik had scored every field. It did not move far enough.

Cost: a survey had to be acquired — dose on the sample — before a string in an
HDF5 file could be read. This is the failure the session paid for.

### 2. `protocol_params` does not say what goes in it (costs one round trip)

Turn 73 called `run_tile_acquisition(..., protocol_params={"n_frames": 1,
"interval_s": 0}, exposure_ms=100)`; turn 74 returned `TypeError:
run_tile_acquisition() got an unexpected keyword argument 'exposure_ms'`; turn
75 retried correctly. No dose, no hardware, one wasted call.

The implementation contract was sound; the hint was not.
`run_adaptive_survey.protocol_params` says "Optionally channel and exposure_ms
for either" (`tools_schema.py:1671`). The same prose in
`run_tile_acquisition` (`:1449`), `run_multiposition_acquisition` (`:1333`) and
`run_multiposition_with_autofocus` (`:1569`) does not. Three copies, one
updated: copied prose is how the hints drifted.

### 3. No tool advertises itself as the way to locate a named local file (cost: unclear)

Turn 46: the user described the `.ilp` as "in downloads something with M5 in the
filename". Turn 47 the agent asked for the full path. Turn 48 the user gave it.

Scored honestly, this is the weakest of the three. The agent asked for the path
as blocker **1 of 4** — the other three were the untrustworthy rig state, the
unresolved 676/37 filter, and which channel the sample was labelled in. It was
not stuck on the filename, and a discovery tool would not have unblocked that
turn. The defect is still real: `inspect_artifacts(paths, hash=false)` already
performs bounded local enumeration, but its name and hint frame it as provenance
inspection of "workspace artifact paths", and `open_artifact` describes its
`path` as "The artifact path a tool returned" — so nothing advertises a route
from a user's description to a path.

Prompting the agent to ask the user for information the machine can safely
inspect is not the right fix. That sentence applies hardest to (1).

## Decision 1: refuse a wrong label before the dataset, not after the survey

Move the project-open block out of `analyze_completed_dataset` and into
`IlastikCompletedDatasetAdapter.__init__`, in this order:

1. confirm `project_path` is a file;
2. compute its SHA-256 and, when `project_sha256` was supplied, verify the pin;
3. import HDF5 support;
4. open the `.ilp` and read `LabelNames`, `ilastikVersion` and the training
   resolution; and
5. run `_check_configured_labels` and the `_trained_labels` check.

Store the computed digest, its `computed`/`verified` source, version and training
resolution on the adapter for the later manifest and batch. Digest verification
must precede HDF5 parsing: a caller who supplied a pin asked microclaw not to
trust or interpret different bytes. Moving parsing earlier while leaving the
pin check behind would violate that boundary.

The ilastik *executable* checks
(`discover_ilastik`, `executable_path.is_file()`, the launcher) stay in
`analyze_completed_dataset` — they are about the runtime, not the project.

Then `run_analysis_on_saved_dataset` refuses at `:367`, which is **before**
`mkdir` and before `Dataset(dataset_path)`. Three things follow at once:

- The turn-51 probe works. A real `project_path` plus placeholder labels returns
  `configured labels are absent from project LabelNames: [...]; available
  labels: [...]` with no dataset, no exposure and no ilastik installation.
- The stray `__probe_out__` directory is no longer created. The comment at
  `completed_dataset.py:362` says a directory made before an argument error
  "survives the failure and then blocks the very name the caller retries with";
  a dataset that does not exist is that same class of error and was landing on
  the wrong side of the `mkdir`.
- A mistyped label on a real run refuses in milliseconds rather than after the
  batch.

This is discovery-by-refusal, which is accepted deliberately: the check already
exists, no new tool and no new layer is introduced, and the user must choose
which label is numerator and which is denominator in any case — microclaw cannot
infer that. Make the route explicit in the `parameters` description, one
sentence, per the measured finding that a statically-knowable rule belongs in
the parameter and not in the tool prose:

> If the project's label names are unknown, call with `project_path` and
> placeholder label names: the refusal lists the project's own labels and reads
> no dataset.

## Decision 2: describe the actual nested protocol schema

One `_PROTOCOL_PARAMS_SCHEMA` constant in `tools_schema.py`, used by
`run_tile_acquisition`, `run_multiposition_acquisition`,
`run_multiposition_with_autofocus` and `run_adaptive_survey`:

```json
{
  "protocol_params": {
    "description": "Parameters for the selected per-position protocol. Put channel and exposure_ms here, never at the top level.",
    "properties": {
      "channel": {"type": "string", "description": "Optional channel for each frame."},
      "exposure_ms": {"type": "number", "description": "Optional exposure in ms for each frame."},
      "n_frames": {"type": "integer"},
      "interval_s": {"type": "number"},
      "laser_slot": {"type": "integer", "description": "Timelapse only: EMU trigger slot to pre-flight."},
      "z_start_um": {"type": "number"},
      "z_end_um": {"type": "number"},
      "z_step_um": {"type": "number"}
    }
  }
}
```

The description carries the literal valid shapes, because the available JSON
Schema subset cannot express the protocol-dependent alternatives cleanly:

```text
timelapse: {n_frames, interval_s, optional channel, optional exposure_ms, optional laser_slot}
zstack: {z_start_um, z_end_um, z_step_um, optional channel, optional exposure_ms}
```

Do not add a top-level `exposure_ms` alias: two spellings make precedence
ambiguous and preserve the wrong mental model.

## Decision 3: validate the selected protocol before planning or motion

This is a behaviour change, not a hint fix: it moves a class of refusal ahead of
dose planning, position-list mutation and stage movement. The shared schema
advertises the union of supported keys and cannot express which subset belongs
to the selected protocol. Enforce that distinction once:

- `timelapse`: require `n_frames` and `interval_s`; allow `channel`,
  `exposure_ms` and `laser_slot`;
- `zstack`: require `z_start_um`, `z_end_um` and `z_step_um`; allow `channel`
  and `exposure_ms`; and
- `snap`: accept no `protocol_params` until parameters are deliberately defined
  for its display-only branch. In particular, do not silently accept exposure
  or channel values that `_run_protocol_at` ignores — its snap branch reads no
  key of `params` at all.

**Not a new helper — widen `_protocol_shape_kwargs`** (`tools.py:5775`). It
already takes `(protocol, params)`, already owns which keys each protocol
requires, and already raises for a missing one; the callers already turn that
into "protocol_params for 'zstack' is missing 'z_start_um'". A parallel
`_validate_protocol_params` would be a second function keyed on the same pair
carrying the same per-protocol table — the drift in Observed failure 2 is a case
study, where three copies of per-protocol prose left one updated. Widen the
existing helper to refuse an incompatible key as well as a missing one, rename
it if "shape kwargs" no longer describes it, and move the inline
hook-capability check (`:5964-5972`) into it — that consolidates three scattered
checks instead of adding a fourth.

Its existing downstream calls are not sufficient for every public entry point.
Each of the four outer tools must invoke the widened helper at its own boundary,
even where a forwarded inner tool validates the same dictionary again:

- `run_tile_acquisition` validates before reading the default X/Y center. Its
  current first action is `get_x_position`/`get_y_position`, before it forwards
  to `run_multiposition_acquisition`; downstream validation cannot make a
  malformed tile call hardware-free.
- `run_multiposition_acquisition` validates before position-list preflight,
  planning, marking or motion.
- `run_multiposition_with_autofocus` validates before resolving its compatibility
  log/setup and before forwarding. Duplicate validation in the non-deprecated
  inner path is intentional: every public boundary owns its no-effects refusal.
- `run_adaptive_survey` validates before position-list preflight, planning or
  any acquisition setup.

The widened helper must either return a normalized `params` dictionary alongside
the event shape or require every argument that the inner public tool requires.
Choose the latter for timelapse: `interval_s` remains required, matching
`run_timelapse`'s signature. `_protocol_shape_kwargs` currently uses
`params.get("interval_s", 0)` for event planning, but the plain per-position
path later splats the original dictionary into `run_timelapse`, where an omitted
`interval_s` raises only after XY has moved. Preflight must reject that omission;
the planner's private default must not advertise a public default that execution
does not have.

This one is **derived, not observed** — it is not in the session above — but it
is live on `main` and the pre-fix evidence needs no rig:

```text
_protocol_shape_kwargs("timelapse", {"n_frames": 5})
  -> {'num_time_points': 5, 'time_interval_s': 0}          # plans fine
signature(run_timelapse).bind(..., n_frames=5)
  -> missing a required argument: 'interval_s'             # after XY has moved
```

So `run_multiposition_acquisition(protocol="timelapse",
protocol_params={"n_frames": 5})` today plans a dose, authorizes it, moves the
stage to the first position and only then fails on a binding error.

**Requiring `interval_s` is an intentional break, not a pure fix.** The two
branches disagree: the hooked branch never calls `run_timelapse` — it builds
events through `_acquire_positions_with_hook(..., **shape)` (`:6032-6041`) — so
an omitted `interval_s` silently takes the planner's 0 and the run *succeeds*
there today. Preflight makes that call refuse. That is the right direction,
because the published timelapse shape already lists `interval_s` and the hooked
branch's tolerance was accidental rather than designed, but it removes a
currently-working call and should be recorded as such.

**`snap` needs a real helper case, not only a new call site.** `:6083` guards
`_plan_protocol_repetitions` behind `if protocol != "snap"`, and the helper
raises `ValueError("Unknown protocol 'snap'")` today, so the snap limb has no
code to run in until snap becomes a real case: return `{}`, refuse non-empty
`protocol_params`, and call it on that path.

**Why the strict list is not over-reach.** `_run_protocol_at` splats `**params`
into the inner tool (`:5887-5895`), and the existing inline refusal covers only
`hook_strategy` and `HOOK_CAPABILITY_ARGS` — that narrow list is the point fix
from block 43j, whose review round 1 found that unfiltered forwarding let
`hook_strategy` resolve one hook per position *and* have its dose silently
discarded, because `_reservation` short-circuits the `_plan_with_hook_dose`
result (design/35, 43j row). Key-specific containment has already failed once
here; this generalises it.

`laser_slot` is **kept**, for timelapse only. `run_timelapse` accepts it
(`:4069`) and `run_zstack` does not, so it forwards through `protocol_params`
today and a bare seven-key list would silently remove it — and a multi-position
timelapse on an EMU rig is exactly where a per-position trigger slot matters.
Keeping it means publishing it: add it to `_PROTOCOL_PARAMS_SCHEMA` as a
timelapse-only key, because an accepted-but-unpublished parameter is the defect
this whole block is about. `log_path` and `hook_params` are dropped without
ceremony — both are meaningless without `hook_strategy`, which is already
refused.

For example:

```text
protocol="zstack",
protocol_params={"z_start_um": 0, "z_end_um": 2, "z_step_um": 1,
                 "n_frames": 1}
```

refuses with:

```text
protocol_params for 'zstack' contains incompatible key 'n_frames';
timelapse parameters do not apply to a zstack.
```

This validation is the primary protection. The error translation in Decision 4
remains defensive coverage for unexpected `TypeError`s arising elsewhere.

## Decision 4: close the loop at the registry boundary

Decision 3 stops the malformed public call. This is the defensive half: a
targeted hint for a `TypeError` that reaches the boundary anyway. The rejection
already happens during Python signature binding, before every hardware action. `hint_for_error` (`errors.py:67`) receives only an exception,
however, not the tool name. A global match for `unexpected keyword argument
'n_frames'` would also catch `run_zstack` or another tool that has no
`protocol_params` and prescribe an invalid retry.

In `execute_tool`, where the selected function and the exception are both in
hand, detect an unexpected keyword only when all three of these hold:

- the unexpected key is one of `exposure_ms`, `channel`, `n_frames`,
  `interval_s`, `laser_slot`, `z_start_um`, `z_end_um`, or `z_step_um`; and
- the tool that raised accepts `protocol_params` —
  `"protocol_params" in inspect.signature(fn).parameters`; and
- the callable named by the Python binding error is the selected registry
  function itself (`fn.__name__`), not a function it called internally.

Discriminate on the **signature, not the published schema**. `fn` is already the
object that raised, `inspect` is already imported (`tools.py:3`), and `tools.py`
imports no schema module today — `TOOLS` is a flat list with no name lookup, so
the schema route adds a module edge and a map to build. More importantly the
signature is what raised the top-level `TypeError`: a schema predicate could
agree with the description while disagreeing with the function, which is the
drift this block exists to remove. Checking the callable name is equally
load-bearing because `execute_tool` also catches exceptions raised after
binding, inside forwarding calls. Before the new preflight, the per-position
loop splat raw `protocol_params` through to the inner tool
(`tools.py:5887-5895`, `run_zstack(..., **params)`). A zstack whose required
keys were all present **and** which carried a stray one could therefore reach
this inner failure:

```text
protocol="zstack",
protocol_params={"z_start_um": 0, "z_end_um": 2, "z_step_um": 1, "n_frames": 1}
  -> run_zstack() got an unexpected keyword argument 'n_frames'
```

Telling that caller to put `n_frames` in `protocol_params` would prescribe the
call it already made. Decision 3's preflight now makes this particular inner
failure unreachable through a public outer tool and, critically, refuses it
before the stage moves. The callable-name check still prevents a misleading
hint if a forwarded or substituted function raises the same shape of TypeError
despite that preflight.

A fourth condition — that the key is published as a nested property — buys
nothing beyond these three, because the shared schema publishes the whole key
union on all four outer tools, while Decision 3's widened
`_protocol_shape_kwargs` decides which shape applies to the selected protocol.

Return the targeted hint from that branch:

```text
`exposure_ms` is a per-position protocol parameter; pass
protocol_params={..., "exposure_ms": 100}.
```

The generic `hint_for_error` argument message remains the fallback. This is one
registry-level implementation rather than four per-tool guards, and it cannot
misroute either an error from a tool that has no such nesting or an error from
an inner forwarded function. The first case matters because `run_zstack` takes
`z_start_um`, `channel` and `exposure_ms` at the top level
(`tools.py:3879-3884`) and `run_timelapse` takes `n_frames`, `interval_s`,
`channel` and `exposure_ms` (`:4063-4067`). On those tools top-level is correct,
and a hint that said otherwise would be worse than none. A small helper may own
the translation if keeping `execute_tool` compact is useful; it must accept the
selected function explicitly and compare the error's callable name with it.

## Decision 5: fold file discovery into `inspect_artifacts`

Do **not** add a `find_files` tool. `find_files(dir, glob)` and
`inspect_artifacts([dir], hash=false)` are almost the same thing, and both would
then need cross-referencing prose to keep the agent off the wrong one. The
diagnosis in §3 above is that the *name and hint* are wrong; adding an 82nd tool
does not repair a description, it duplicates a capability.

Extend the tool that exists:

```text
inspect_artifacts(paths, name_glob="*", recursive=true, hash=true,
                  max_files=1000, max_depth=16, ...)
```

No new bound is introduced. `max_files` keeps its existing meaning — files
**examined**, not files matched — and remains the only count bound; a separate
`max_results` would be a second spelling of the same limit, ambiguous against
`max_files` in exactly the way this document rejects a second spelling of
`exposure_ms`. One consequence must be visible in the result: a search can reach
the bound before it reaches a match, so an empty `matches` alongside
`truncated: true` and the examined count means *not found within the bound*, not
*not found*. An empty list is a statement, not a silence.

Two behaviour changes are needed, and neither is free:

- **`name_glob`**, matched against basenames only, applied as a filter on the
  result rather than on what is walked. Without it a bare listing of a real
  Downloads folder trips `max_files=1000` before it answers anything.
- **`recursive=false`, and bounded discovery at the depth limit.**
  There is currently no non-recursive mode: `max_depth` *refuses* when children
  remain (`tools.py:8305`, `if children and depth >= max_depth: return
  refusal(...)`), so a top-level listing of a folder with any subtree returns an
  error rather than the entries. For discovery (`hash=false`, no
  `manifest_path`), return the matching files reached within the requested
  boundary with `truncated: true` plus the count examined. For provenance
  (`hash=true` or `manifest_path` supplied), retain the refusal whenever a depth,
  file-count or byte bound prevents complete traversal. A partial hash set must
  never look like a complete manifest to an existing caller.

This split preserves the existing completeness contract for provenance while
allowing an explicitly bounded filename search to return useful candidates.
`recursive=false` is not truncation merely because child directories exist: it
is the complete result for the explicitly requested top-level scope.

Rewrite the description to lead with discovery and mention hashing second:

> List or search a local folder the user named, to resolve an incomplete
> filename. Use this when the user says a file is in Downloads/Desktop/a named
> folder but does not give its exact path. Returns absolute candidate paths;
> pass the chosen direct path to the consuming tool. With `hash=true` it also
> computes SHA-256 for provenance. Reads directory metadata only — never file
> contents unless hashing was asked for — and touches no hardware.

For the session's request the call is
`inspect_artifacts(paths=["C:\\Users\\ries\\Downloads"], name_glob="*M5*.ilp",
recursive=false, hash=false)` returning the one absolute `.ilp` path.

The returned path is a proposal, not authority to open, execute, hash or consume
the file. The next domain tool receives that direct path and applies its own
checks; for ilastik that remains project-file validation and SHA-256 reporting.
`open_artifact` stays for images and datasets — an `.ilp` goes to the ilastik
adapter, not ImageJ — and its `path` description should stop implying that a
tool must have returned the path.

### Bounds and privacy

- Require explicit `paths`; never guess or expose the home directory.
- Recursion and depth stay bounded and explicit. Discovery-only calls stop
  deterministically and report `truncated` with the count examined when their
  requested recursive scope is incomplete. Hash/manifest calls refuse instead
  of returning incomplete provenance.
- `max_files` bounds files examined and is the only count bound. A truncated
  discovery result must never be readable as an exhaustive negative answer.
- Match basenames only. Reject separators and `..` in `name_glob`, so `paths`
  remains the sole search root.
- Deterministic, case-insensitive name order on Windows.
- Do not follow directory symlinks/junctions during recursion (already true).
- Do not read contents or write a manifest unless asked.
- Keep `guard.resolve_readable_path`, which is deliberately *not* confined by
  `workspace_dir` (`safety.py:1313-1325`) — which is exactly why "require an
  explicit directory" is load-bearing and not decoration. The
  `tests/test_path_boundaries.py` tripwire already names `inspect_artifacts`;
  confirm the new arguments cannot reach a write/serve resolver.
- An unreadable directory or disappearing entry is reported, not silently
  skipped. A missing directory names the resolved path.

## Agent instruction

Put the routing rule in the tool description (above), not in `agent.py`. Add a
system-prompt sentence only if the replay in the acceptance tests shows the hint
alone does not carry it — the measured finding is that a statically-knowable
rule belongs in the parameter description, and this block should not
re-introduce prose in two places.

## Acceptance tests

1. `IlastikCompletedDatasetAdapter(project_path=<real .ilp>, background_label=
   "__unknown__", ...)` raises, naming the project's actual labels — with no
   dataset argument in existence and no ilastik installed.
2. A supplied, incorrect `project_sha256` refuses before `h5py.File` is called;
   a correct or omitted pin stores the digest and its source for the later
   manifest.
3. `run_analysis_on_saved_dataset` with a real `project_path`, placeholder
   labels and a **non-existent** `dataset_path` returns the label refusal, not
   `FileNotFoundError`, and creates no `output_dir`. This is turn 51 replayed.
4. A correct label set with a non-existent dataset still fails on the dataset —
   the reordering must not swallow that error.
5. The published schema for every tool taking `protocol_params` contains a
   nested `exposure_ms` property and the words "never at the top level", from
   one shared constant (assert the four schemas are the same object, so a future
   copy-and-edit fails the test rather than drifting).
6. The widened `_protocol_shape_kwargs` accepts exactly the documented timelapse
   and z-stack shapes, requires both `n_frames` and `interval_s` for timelapse,
   reports missing required keys, and rejects keys belonging
   to the other protocol — including the hook-capability keys folded in from
   `tools.py:5964-5972`, which must keep refusing. `laser_slot` is accepted for
   timelapse and rejected for zstack, and is published in the schema. `snap`
   rejects non-empty `protocol_params` rather than silently ignoring them, which
   requires snap to become a real case in that helper and to be called on the
   `:6083` path — assert the snap route reaches it at all, since today it does
   not. Parameterize over all four outer tools **and over the hooked and plain
   branches**, which are the two paths that disagree today — a hooked
   multiposition timelapse omitting `interval_s` currently succeeds and must
   now refuse. Assert refusal occurs before
   planning, position-list inspection or mutation, stage reads or motion,
   compatibility-log/setup work, live-view changes or exposure. Include a tile
   acquisition with a default center and assert `get_x_position` and
   `get_y_position` were not called; include the deprecated autofocus wrapper
   and assert it refuses at its own boundary rather than relying on the
   forwarded multiposition call.
7. Replay the formerly reachable forwarding case through `execute_tool`:
   `run_multiposition_acquisition(protocol="zstack",
   protocol_params={"z_start_um": 0, "z_end_um": 2, "z_step_um": 1,
   "n_frames": 1})`. It returns the targeted incompatible-key refusal, never
   calls `run_zstack`, and makes no stage or camera call.
8. `execute_tool` translating a `TypeError` on any of the eight nested keys, for
   every tool in `TOOL_REGISTRY` that takes `protocol_params`, produces a hint
   naming `protocol_params`. Parameterized over the registry by signature, not
   per tool. The same errors on tools without `protocol_params` retain the
   generic hint; `run_zstack` and `run_timelapse` are the named cases, and this
   negative limb is what prevents a hint prescribing an invalid retry.
   The inner-forwarding case is the second negative limb: inject a registry
   function that accepts `protocol_params` but raises a TypeError naming a
   different callable, `run_zstack() got an unexpected keyword argument
   'n_frames'`. It must retain the generic hint and must **not** tell the caller
   to put `n_frames` in `protocol_params` again. This case is intentionally
   synthetic now: test 7 proves the malformed public call is stopped by
   preflight, while this test proves the defensive translator does not misread
   an internal exception.
9. **Replay, not string presence.** Tests 5 and 8 assert the words exist, which is
   not evidence a model reads them. Run `design/59-hint-replay-spike.py` against
   turn 73's recorded payload with the old and the new schema and count how
   often `exposure_ms` lands nested. Size the sample before reading a
   difference: one run of a wording measures very little. This is off-rig and
   needs no microscope.
10. `inspect_artifacts(Downloads, name_glob="*M5*.ilp", recursive=false,
   hash=false)` returns the exact absolute path, reads no file contents,
   computes no digest.
11. Matching is basename-only and deterministic; `name_glob` containing a
   separator or `..` is refused.
12. `recursive=false` never enters a child directory and reports a complete
    top-level result. An incomplete recursive discovery (`hash=false`) returns
    `truncated: true` and a count; the same incomplete traversal with
    `hash=true` or `manifest_path` refuses and writes no partial manifest.
13. A `name_glob` whose match lies beyond `max_files` returns empty `matches`
    **with** `truncated: true` and the examined count — never a bare empty list
    that reads as "no such file". This is the limb that stops a bound from
    being reported as a fact about the folder.
14. Missing and unreadable directories produce useful errors and touch no
    hardware; complete `hash=true` behaviour is unchanged for existing callers.

The top-level-parameter test of the previous draft — "a top-level `exposure_ms`
is rejected before any stage motion" — has been dropped. It passes on the
unfixed tree, because Python signature binding already rejects it; a limb that
cannot fail is not a criterion.

## Rig gate

None. Every limb above is settled off-rig: two are pure-Python ordering, one is
a local filesystem, one is a schema, and the replay runs against a payload the
rig already produced. Do not book a session for this block.

## Non-goals

- No new discovery tool; no whole-machine search, home-directory crawl, or
  filename index.
- No opening arbitrary files merely because they were found.
- No biological interpretation of an ilastik project or its output. Listing a
  project's label names is not interpretation.
- No compatibility support for top-level acquisition parameters that belong to
  the selected protocol.

## Coordinator findings at assignment — 2026-09-01

Read this before assigning any block. Three corrections to the document above,
found by checking its claims against `main` at `586c666`.

### F1 — there is a **fifth** `protocol_params`, and its shape is different

Decision 2 says "one `_PROTOCOL_PARAMS_SCHEMA` constant, used by
`run_tile_acquisition`, `run_multiposition_acquisition`,
`run_multiposition_with_autofocus` and `run_adaptive_survey`". Those four exist
and the prose is as described. But `run_adaptive_survey` publishes a **second**
`protocol_params`, nested inside `acquire_on_hit` (`tools_schema.py:1751`), and
its documented shape is not the shared one:

```text
acquire_on_hit.protocol_params
  timelapse: {n_frames, interval_s, exposure_ms}          # no channel
  zstack:    {z_offset_start_um, z_offset_end_um,          # RELATIVE to the hit
              z_step_um, exposure_ms}                      # no channel
```

`channel` is deliberately absent — `acquire_on_hit.channel` is applied once
after the search, not per frame — and its Z keys are **offsets from the hit's
own Z**, resolved at `tools.py:8652-8657`, which is why that branch does not
call `_protocol_shape_kwargs` at all.

Two consequences, and neither is optional:

- **Decision 2 must not reach it.** Publishing the shared constant here would
  advertise `channel`, `laser_slot` and absolute `z_start_um` on a path that
  accepts none of them, and would drop the three `z_offset_*` keys that path
  requires — the exact defect this block exists to remove, introduced by its own
  fix. Acceptance test 5's "assert the four schemas are the same object" gains a
  negative limb: assert `acquire_on_hit`'s is **not** that object.
- **Decision 3 already reaches it, whether the block intends it or not.** The
  timelapse branch calls `_protocol_shape_kwargs(acquire_protocol,
  acquire_params)` at `tools.py:8651`. Widening that helper to refuse an
  incompatible key therefore lands on `acquire_on_hit` too. Under the shared
  table `{n_frames, interval_s, exposure_ms}` happens to be legal, so the likely
  outcome is "no change" — but that is a coincidence to be **asserted**, not
  assumed. 62c owes a test that an `acquire_on_hit` timelapse still plans, and a
  decision (stated in the block) on whether its zstack branch gets a case of its
  own or stays out of the helper.

### F2 — every line number in this document is stale by roughly +940

The document was written before design/63 through design/68 landed. Verified
positions on `main` at `586c666`:

| document says | actually |
| --- | --- |
| `_protocol_shape_kwargs`, `tools.py:5775` | `tools.py:6715` |
| inline hook-capability refusal, `:5964-5972` | `:6929-6940` |
| `_run_protocol_at` splat, `:5887-5895` | `_run_protocol_at` at `:6748` |
| `if protocol != "snap"` guard, `:6083` | `:6922` (`save_dir`) and `:6964` (hook) |
| `inspect_artifacts` depth refusal, `:8305` | `:9349` |
| `hint_for_error`, `errors.py:67` | unchanged |
| `ilastik_adapter.py:389` / `:395` | unchanged |
| `completed_dataset.py:367-369` | `:367-369`, unchanged |

Hand runners **symbol names**, and tell them the document's line numbers are
approximate. Everything the document asserts about the *code* was confirmed:
the project-open block does sit inside `analyze_completed_dataset` after the
executable checks; `_protocol_shape_kwargs` does default `interval_s` to 0; the
inline refusal does cover only `hook_strategy` and `HOOK_CAPABILITY_ARGS`;
`fn` is in scope at `execute_tool`'s `except Exception` boundary (`:10709`).

### F4 — `z_step_um` means two different things on the same tool

Found by the coordinator running 62b's preserved tests, 2026-09-01. Exactly one
of that file's 32 registry parameterizations failed, and the reason is not a test
bug.

`run_multiposition_with_autofocus` accepts **`z_step_um` at the top level** —
schema description *"Fine step size for autofocus in µm"*, paired with
`z_range_um` — **and** takes `protocol_params`, whose `z_step_um` is the zstack
protocol's plane spacing. Two genuinely different quantities, same name, same
tool. Measured: it is the only overlap among the four, and the tool is already
marked deprecated in its own description.

```text
run_multiposition_with_autofocus  top-level overlap with the eight: ['z_step_um']
run_tile_acquisition              none
run_multiposition_acquisition     none
run_adaptive_survey               none
```

Two consequences:

- **Decision 4 behaves correctly here, by luck rather than design.** A top-level
  `z_step_um` on that tool *binds*, so no `TypeError` is raised and the hint never
  fires — which is the right outcome. But the eight-key list was written on the
  assumption that these keys are never legitimate top-level parameters of a tool
  that also takes `protocol_params`, and on one tool that assumption is false.
  The correct test is not to drop the combination but to **assert** it: that
  `z_step_um` binds on this tool and produces no nested hint. A silently skipped
  parameterization would leave the assumption unexamined.
- **Decision 2's shared description needs one disambiguating clause.** Its
  literal sentence — "Put channel and exposure_ms here, never at the top level" —
  names only the two keys that are *not* in collision, so it is not false. But an
  agent reading it on the autofocus wrapper sees `z_step_um` published in two
  places with nothing distinguishing them. Say what the nested Z keys are (the
  acquisition protocol's plane spacing) so the focus-search parameter is not
  confusable with it. This can be done in the shared constant without breaking
  the same-object identity that acceptance test 5 requires.

Renaming either parameter is **out of scope** — it is a public surface change
design/62 did not decide, on a deprecated tool.

### F3 — acceptance test 9 is an API spend, not a runner task

Test 9 replays turn 73's payload through a model with the old and the new
schema. `design/59-hint-replay-spike.py` exists but is written around design/59's
orientation payload and its two recorded session transcripts; it needs adapting.
The payload is present at
`~/Documents/Documents - Beyonce/Projects/Micro-Claw/sarah-check-downloads/20260827_160401_908523_microclaw_history.jsonl`.

This limb is **coordinator-owned and priced before it runs**, not handed to an
implementation runner: it costs API credits, and the document's own instruction
("size the sample before reading a difference") means a useful run is tens of
samples across two conditions, not one. It does not gate 62b's merge.

**Operator decision, 2026-09-01: run it, priced first.** The coordinator adapts
the spike and states a sample size and a token estimate for the two conditions
*before* spending anything; the operator approves or declines the spend at that
point. If declined, the outcome is recorded here as a deliberate non-run, not
left as a blank — see [[feedback_no_credit_overage]].

## The gate story: no microscope, and that is by design

§"Rig gate" above says none, and it is right — the five decisions are two
pure-Python reorderings, a local filesystem, a JSON schema, and a replay against
a payload the rig already produced. **No block of design/62 books a session on
M5, M2 or the Nikon, and no block ships a rig-gate runbook.** Reaffirmed with
the operator 2026-09-01: microscope access is not readily available right now,
which costs this block nothing.

Where a limb wants a machine that is not a microscope, it is the **demo
machine** — Windows path semantics for Decision 5 (`name_glob` against
backslash-separated basenames, case-insensitive ordering, a real `Downloads`
folder with a subtree) are the only thing macOS cannot settle. That is a demo
limb, folded into 62d, and it is the sole gate this whole block asks anyone to
run.

## Blocks

Four blocks. The split is by rollback boundary, not by file: Decision 3 is the
only behaviour change in design/62 and it removes a currently-working call, so it
gets a branch of its own rather than arriving inside a hint fix.

**62a — the ilastik project opens before the dataset.** Decision 1 alone.
Acceptance tests 1–4. Touches `microclaw/ilastik_adapter.py` and
`microclaw/completed_dataset.py` and nothing else, so it may run **concurrently
with 62b** in a separate worktree. This is the block the session actually paid
for: it is the one that stops a survey being acquired to read a string out of an
HDF5 file.

**62b — one published `protocol_params` schema, and a hint that cannot
misroute.** Decisions 2 and 4. Acceptance tests 5 and 8, plus F1's negative limb.
No acquisition behaviour changes: a malformed call is refused today by Python
signature binding and is still refused, only with a hint that names
`protocol_params`. Test 9 is not in this block — see F3.

**62c — the protocol preflight.** Decision 3 alone. Acceptance tests 6 and 7,
plus F1's `acquire_on_hit` assertion. **Depends on 62b merged**: it shares
`tools.py` and `tools_schema.py`, and the refusal it adds is what the schema 62b
publishes points at. This is the intentional break — see the ledger row.

**62d — discovery folded into `inspect_artifacts`.** Decision 5. Acceptance
tests 10–14, plus the demo-machine path-semantics limb. **Depends on 62c
merged** for the same shared-file reason. Assigned last deliberately: §3 scores
it as the weakest of the three observed failures, and a discovery tool would not
have unblocked the turn it came from.

### The intentional break, stated before it ships

62c makes `run_multiposition_acquisition(protocol="timelapse",
protocol_params={"n_frames": 5})` refuse. On `main` that call **succeeds** on the
hooked branch, which never calls `run_timelapse` and so silently takes the
planner's `interval_s=0`; on the plain branch it already fails, but only after
the stage has moved. Design/62 argues the tolerance was accidental and the
published shape already lists `interval_s`, and this coordinator agrees — but it
is a working call being removed, and `CLAUDE.md` requires that be the operator's
decision rather than the implementer's. Confirm before 62c is assigned; it does
not block 62a, 62b or 62d.

It is a refusal, not a confirmation: nothing stops to ask a human, and the
decline is the refusal itself. No prompt is added anywhere in design/62.

**Operator decision, 2026-09-01: require `interval_s`.** Refuse the omission at
every public boundary, as Decision 3 specifies. The two alternatives were
considered and rejected: publishing `interval_s: {default: 0}` would have to give
`run_timelapse` a default it does not have, spreading the disagreement rather
than closing it; and deferring the limb while shipping the incompatible-key
refusal leaves the plain branch still failing *after* the stage has moved, which
is the defect the block exists for. 62c is unblocked.

## Run ledger

design/62 is coordinated and owns its own blocks and ledger, like design/48
through design/60 and design/65. `design/35-usability-and-pfs-checklist.md`
points here and does not track these rows.

**Baseline on `main` at `586c666`, coordinator-measured on a clean tree, macOS:
2658 passed / 99 skipped / 3 warnings = 2757 collected, 181 s.** Every block's
suite number is read against this, and re-run by the coordinator rather than
accepted from a runner report.

| block | branch | start | implementation | gate | merge |
| --- | --- | --- | --- | --- | --- |
| 62a | `design62/ilastik-project-before-dataset` | `8176145` (2026-09-01) | turn 1 assigned 2026-09-01, **killed mid-flight by a Codex account usage limit** at 11:45; preserved unreviewed as `d1da105` and pushed. Tests only — a `write_ilastik_project` HDF5 fixture and three of the four acceptance tests; **no product change at all**, neither `ilastik_adapter.py` nor `completed_dataset.py` touched. Its accidental value is that it *is* step 3's pre-fix evidence, captured by the coordinator before any fix exists: tests 1 and 2 fail `DID NOT RAISE ValueError` (construction opens nothing today) and test 3 fails `FileNotFoundError ... missing-dataset` out of `ndstorage/_superclass.py:28` — **turn 51 of the session reproduced verbatim**, which is this block's whole thesis. Fixture itself unverified |  **none — no rig, no demo.** Tests 1–4 are pure-Python ordering with no dataset, no ilastik installation and no hardware; test 1's whole point is that it runs with neither | |
| 62b | `design62/protocol-params-schema-and-hint` | `8176145` (2026-09-01) | turn 1 assigned 2026-09-01, **killed by the same usage limit** at 11:45 but later, with edits landed; preserved unreviewed as `1d69416` and pushed. Substantively **both decisions**: `_PROTOCOL_PARAMS_SCHEMA` used as the same object by all four top-level sites (coordinator-verified: 1 distinct object across the four) with `acquire_on_hit` correctly left alone per F1, and `_hint_for_tool_error(fn, exc)` carrying all three of Decision 4's conditions. Absent: every piece of evidence — no pre-fix failures, no test-5 mutation, no suite run, no report. Its 118 test lines run **35 passed / 1 failed**, and that one failure is a real finding for the next turn: `execute_tool` reports `Missing required arguments` *before* signature binding, so a limb that passes only the nested key never reaches the `TypeError` on a tool with required arguments — the test must supply them | **none — no rig, no demo.** A schema is checked by reading it and the hint by driving `execute_tool`. Test 9's replay is F3's priced coordinator limb, approved to run subject to a stated estimate, and does not gate this merge | |
| 62c | `design62/protocol-preflight` | | | **none — no rig, no demo.** Every limb asserts a refusal happens *before* a hardware call, which is measured by counting calls on a fake | |
| 62d | `design62/inspect-artifacts-discovery` | | | **demo machine, one limb.** Windows basename matching, case-insensitive order and a real `Downloads` subtree; everything else settles on macOS. Runs as a script against a fake built from `pathlib`/`os.scandir` behaviour, not from our caller — the lesson design/60's gate paid for | |


### Coordination note: two concurrent runners share one account quota

Both 62a and 62b were assigned at 11:42 on 2026-09-01 and **both turns died at
11:45 on the same Codex account usage limit**, five minutes in. The blocks were
correctly independent — different files, different worktrees, no merge conflict
— but independence at the *repository* level is not independence at the *quota*
level, and running them concurrently spent the limit at twice the rate for no
schedule benefit, since neither finished.

`CLAUDE.md` §"The block workflow" step 2 records what a killed turn costs and how
to preserve it. It does not yet record this: **concurrency is free in worktrees
and not free in tokens.** Prefer sequential assignment unless a block is genuinely
blocked waiting on something else, and if two do run concurrently, expect the
limit sooner rather than later.

The preserved-turn rule worked exactly as written. 62a died early enough that only
tests existed and 62b died late enough that both product changes had landed; both
were committed with the state stated plainly in the message, pushed, and handed
forward in writing. What the rule does not say, and should: **run the killed
turn's tests before handing them on.** Doing so turned 62a's abandoned turn into
this block's pre-fix evidence and found a test defect in 62b's — two things the
next turn would otherwise have spent its own budget discovering.
