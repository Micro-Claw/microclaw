# design/31 — native Micro-Manager position-list files

## Status

Proposed, 2026-07-21.

## Goal

Make `save_position_list` and `load_position_list` exchange the same position-list
files as Micro-Manager, so saving or loading through either application produces
the same list in Micro-Manager's Position List Manager and in microclaw.

This replaces microclaw's private JSON array format with Micro-Manager's native,
typed Property Map JSON. The conventional filename suffix exposed by the tools
will be `.pos`.

## Evidence and current behavior

Microclaw currently persists only its Python projection:

```json
[
  {"name": "P1", "x_um": 1.0, "y_um": 2.0, "z_um": 3.0}
]
```

`MicroscopeController.save_position_list()` writes `_positions` with
`json.dumps`, and `load_position_list()` reads that array. The tool wrappers in
`microclaw/tools.py` provide workspace confinement, artifact reporting, and
post-load safety filtering. Although an integration test happens to use a
`.pos` suffix, the file contents are not a Micro-Manager position list.

`tests/fixtures/PD_PositionList2.pos` is a Micro-Manager 2.0 Property Map. Its top-level header
declares UTF-8, format `Micro-Manager Property Map`, and version 2.0. Its map has
a `StagePositions` property-map array. Each entry contains a label, default
stage names, grid row/column, arbitrary properties, and one or more device
positions. A device position carries the device label and a one- or two-value
`Position_um` array. The supplied file contains 25 XY positions for
`SmarActXY`.

Micro-Manager's public `PositionList` API already provides `save(String)` and
`load(String)`, as well as `toPropertyMap()` and `replaceWithPropertyMap()`.
`PositionListManager.getPositionList()` returns the current list and
`setPositionList()` makes a list current for both acquisition and the Position
List dialog. Therefore Micro-Manager, rather than microclaw, should own parsing
and serialization of this format.

References:

- [Micro-Manager `PositionList` API](https://micro-manager.org/apidoc/mmstudio/2.0.0/org/micromanager/PositionList.html)
- [Micro-Manager `PositionList` usage and manager behavior](https://micro-manager.org/apidoc/mmstudio/2.0.0/org/micromanager/class-use/PositionList.html)

### Live spike result (2026-07-21)

`design/31-native-position-file-spike.py` passed against MMCore 12.5.0 on the
Windows test rig. The rig reported configured devices `XY` and `Z`, while the
fixture names `SmarActXY`. The verified pycro-manager surface is:

- `PositionList.load(str)` loaded all 25 fixture positions into a temporary
  object without changing the Position List GUI. This is expected: parsing does
  not publish a list; production load must explicitly call
  `studio.positions().set_position_list(candidate)` after validation.
- `MultiStagePosition.get_label()` returned `spiral_01`.
- `get_default_xy_stage()` and `get_default_z_stage()` both returned the empty
  strings stored in the fixture.
- The first `StagePosition` exposed its device as raw field `stageName`, its axis
  count as raw field `numAxes`, and coordinates as raw fields `x` and `y`.
- `PositionList.save(str)` produced a file that reloaded as 25 positions.
- The original and reloaded `to_property_map()` values compared equal.

This establishes lossless native load/save and the spellings required for this
XY fixture. It does not establish one-axis/Z field behavior or the semantics of
non-empty default-stage fields; cover those with a second small fixture or the
live integration tests before relying on them.

One further limit of this spike: the rig's configured devices are `XY`/`Z`,
while the fixture names `SmarActXY`, so every fixture entry would be classified
as unsupported under the proposed device-name rules. The spike inspected the
temporary Java object directly; it did not execute the proposed projection. It
therefore verified only that the fields can be read — not the positive
projection branch (`stageName` matches the configured XY stage → emit
`x_um`/`y_um`) nor the `set_position_list` publish. The fixture/contract test
must fake the configured XY device name as `SmarActXY` to exercise
match-and-emit. Planned live integration test 2 exercises publication by loading
through microclaw. Spellings are live-verified; match-and-emit and publication
remain required tests.

## Requirements

1. Saving produces a file that Micro-Manager 2.x can load without conversion.
2. Loading a Micro-Manager `.pos` file updates both microclaw's internal list
   and Micro-Manager's current GUI/acquisition list.
3. Native fields that microclaw does not understand are preserved on a
   load-then-save round trip: device labels, defaults, grid coordinates, and
   per-position properties must not be reconstructed from the lossy Python
   projection.
4. A malformed or unsafe file must not partially replace either current list.
5. All filesystem access remains confined by `SafetyGuard.resolve_in_workspace`.
6. Tool descriptions, prompts, README text, artifacts, and tests describe the
   real native format and `.pos` suffix.

## Non-goals

- Implementing or versioning Micro-Manager's Property Map format in Python.
- Supporting position fields that microclaw cannot act on, such as arbitrary
  extra one-axis stages. Micro-Manager may retain and use them; microclaw's
  navigable projection remains XY plus the configured focus device.
- Automatically moving hardware while loading a list.
- Reading, migrating, or diagnosing the old private JSON-array format. It is
  removed rather than retained as a second position-list format.

## Proposed design

### One authoritative native list, one derived projection

The current Micro-Manager `PositionList` is the authoritative rich model. The
existing `_positions` list remains a small, Python-native projection used by
microclaw tools and acquisition code:

```text
native PositionList (authoritative, lossless)
           |
           +-- Micro-Manager GUI and acquisition
           |
           +-- projection --> [{name, x_um, y_um, z_um}, ...]
```

Operations that microclaw creates (`mark_position`, delete, and clear) continue
to write through to the native list. Saving reads the native list directly.
Loading first constructs a temporary native list, derives and validates a
projection, and only then publishes both states.

Before every operation that reads or consumes positions, microclaw refreshes
and validates `_positions` from the current native list. This includes going to
a position, deleting, and starting any position-based acquisition. Mutating
operations refresh before they calculate their change. The native list can be
edited in Micro-Manager at any time, so a cached projection must never be
assumed current at an operation boundary.

Two categories of operation treat a failed refresh differently:

- **Hardware-driving or state-publishing** operations (go-to, delete, acquisition)
  must not proceed on an inconsistent list. They refuse and return a conflict, as
  below.
- **Read-only inspection** (listing the positions) must still return the projected
  entries it can derive, with the `position_list_conflict` attached alongside
  them rather than in place of them. Refusing to list a bad list would make it
  undiagnosable through microclaw; listing is exactly how a user inspects the
  problem before deciding how to fix it. This result is ephemeral: listing must
  not replace `_positions` with a partial projection, so the cache remains the
  last fully validated projection.

Saving is deliberately exempt from this gate — see the note under
`save_position_list`, since a native save does not require a navigable
projection.

Projection returns structured data rather than a bare list:

```python
@dataclass(frozen=True)
class PositionProjection:
    positions: list[dict]       # fully navigable entries only
    native_entries: list[dict]  # every label/index, coordinates optional
    issues: list[dict]          # all conflicts, keyed by native index/label
```

`native_entries` lets inspection and offline verification report entries that
cannot be navigated on this rig; `positions` is safe to publish as `_positions`
only when `issues` is empty (or after an explicit, operation-local resolution of
unsupported-only entries). Each issue has a stable `code`, native index, label
when available, details, and allowed resolutions. All consumers use this one
result type rather than independently interpreting partial lists.

When a hardware-driving operation's refresh finds an inconsistency, the requested
operation does not proceed and neither representation is changed. The tool returns
a structured `position_list_conflict` describing all problems and the available
resolutions; the agent must present those choices and ask the user what to do.
Typical choices are:

- cancel and let the user repair the list in Micro-Manager;
- omit unsupported-only entries from microclaw's projection while preserving
  them unchanged in the native list; or
- remove specifically identified invalid entries from the native list after an
  explicit confirmation.

Unsafe coordinates, duplicate or empty labels, incomplete XY pairs, and
non-finite values are always conflicts. Microclaw must not guess which duplicate
to keep, silently filter a position, overwrite the native list from a stale
cache, or move hardware until the user resolves the conflict. Resolution is a
separate confirmed operation followed by a fresh preflight; it is not a flag
that lets the original operation continue against unchecked data.

This distinction matters: rebuilding a loaded file with `create2_d` and
`create1_d` would discard `GridRow`, `GridCol`, `Properties`, default-stage
fields, and device positions unknown to microclaw.

### Controller API

Keep the public Python method names, but change their implementation in
`microclaw/controller.py`.

`save_position_list(path)`:

1. Fetch `studio.positions().get_position_list()` once, into a local reference.
2. Project that fetched object and retain the `PositionProjection` result.
3. Call that same Java object's `save(str(path))` method regardless of projection
   issues.
4. If projection is valid, replace `_positions` with `projection.positions`. If
   it is inconsistent, leave the last fully validated `_positions` unchanged and
   attach the conflict to the successful result so the caller learns the saved
   file is not currently navigable by microclaw.
5. Do not serialize `_positions` in Python.

Saving is intentionally *not* gated on projection validity. `PositionList.save()`
serializes the Java object directly; it needs no microclaw projection at all. A
legitimate Micro-Manager list can contain a user-renamed duplicate label or a
device microclaw is not configured for, and microclaw must still be able to
persist it faithfully — refusing to save such a list would contradict the whole
premise that Micro-Manager owns the format and that saving is lossless capture of
GUI edits.

Save the *same* fetched object it projected, never a re-fetch: `getPositionList()`
returns a copy, so serializing the copy that was just projected closes the window
in which a concurrent GUI edit could make the saved bytes disagree with the
reported projection. Saving also avoids changing the GUI merely to save.

`load_position_list(path)` becomes a transactional prepare/commit operation:

1. Construct a new `org.micromanager.PositionList` with `JavaObject` on the
   controller's existing bridge port.
2. Call `candidate.load(str(path))`. Micro-Manager validates and parses its own
   file format. Hash the workspace-confined file immediately before and after
   this Java call. If the hashes differ, discard the candidate and return a
   `file_changed_during_load` conflict; Python and Java may otherwise have read
   different file versions even when they run on the same host.
3. Derive a microclaw projection from `candidate`, without consulting or
   changing the current list.
4. Validate the complete projection, including numeric types, finite values,
   stage safety limits, non-empty labels, and unique labels.
5. If validation fails, return a structured conflict for the agent to present
   to the user, and leave both the old native list and `_positions` unchanged.
   The conflict payload records the resolved workspace path and the matching
   post-load content hash, computed by reading its bytes Python-side. The
   resolution flow below compares against this recorded hash.
6. Commit by calling `studio.positions().set_position_list(candidate)`, then
   assign the already-validated projection to `_positions`.

The commit order deliberately updates the Java list first. If the bridge call
fails, the Python state stays unchanged. Assignment to `_positions` cannot fail
after validation.

The tool layer currently loads first and removes unsafe entries afterward. That
is not atomic and briefly publishes unsafe positions to the GUI. Move safety
validation into the prepare phase. The recommended contract is to reject the
entire candidate if any navigable position is unsafe, returning all validation
problems as a `position_list_conflict` and asking the user how to proceed. This
is simpler and safer than mutating the candidate, and preserves the native file
exactly when loading succeeds. Remove the current `rejected` response field;
there is no compatibility requirement for the proprietary format or its result
shape.

A failed-load resolution never retains a Java candidate across user turns. If
the user elects a permitted transformation, a separate confirmed resolution
call re-reads the file into a fresh candidate, verifies that the resolved path
and content hash match the conflict the user reviewed, applies only the
specifically approved changes, validates again, and commits. If the file
changed, it returns a new conflict and asks again. Alternatively, the user can
repair the file in Micro-Manager and retry the ordinary load. This prevents a
resolution from mutating the pre-existing GUI list or acting on stale contents.
The resolution retry repeats the before/after hash check around Java parsing;
matching the old hash before parsing is not enough if the file changes during
the parse.

### Projection rules

Refactor `_read_mm_position_list()` so it can project any supplied Java
`PositionList`, not only the current GUI list, for example:

```python
def _project_mm_position_list(self, plist) -> PositionProjection: ...
```

For each `MultiStagePosition`:

- `name` comes from `get_label()`.
- A two-axis `StagePosition` is XY only when its device label matches the
  configured XY stage. If the native entry names no device but declares a
  `DefaultXYStage`, use that default according to Micro-Manager semantics.
- A one-axis `StagePosition` is Z only when its device label matches the
  configured focus device, with the analogous `DefaultZStage` fallback.
- Other device positions remain in the native object but are omitted from the
  microclaw projection.
- Preserve full floating-point values internally. Rounding is presentation,
  not persistence; the current three-decimal rounding in the read path should
  be removed. Remove the same rounding from `add_position`, so marked and loaded
  positions follow one precision rule and mark-save-load is idempotent.
- A position is navigable by microclaw if it has a complete XY pair, a focus Z,
  or both. A native entry containing only unsupported devices is retained in
  Micro-Manager but should be reported as unsupported rather than silently
  appearing as a microclaw position with no coordinates.

The live spike establishes `stageName`, `numAxes`, `x`, `y`,
`get_default_xy_stage()`, and `get_default_z_stage()` for this XY fixture. A
one-axis/Z fixture must still verify that path. Do not infer Z solely from
`numAxes == 1`, because a position list can contain other one-axis stages.

Projection is device-name-bound, and this limits cross-rig interop. A `.pos`
authored on another rig names its own devices —
`tests/fixtures/PD_PositionList2.pos` uses
`SmarActXY` with an empty `DefaultXYStage`. Loaded or verified on a rig whose XY
stage is configured under a different name, every entry projects to
"unsupported" with no coordinates, and the empty `DefaultXYStage` provides no
fallback. The native list is still retained losslessly, but microclaw cannot
navigate it and `rank_hook_log` verification reports coordinates as unavailable.
Do not assume that a populated `DefaultXYStage` or `DefaultZStage` maps differently
named devices across rigs; this fixture stores both as empty, so their non-empty
semantics remain unverified.
Until then, cross-rig navigation requires matching device names. A future
explicit, user-approved device-name mapping is out of scope here.

### Tool API and filenames

The wrappers in `microclaw/tools.py` retain their current names and workspace
guard. Update their documentation and status text to say “Micro-Manager `.pos`
position list.”

Require a `.pos` suffix for new saves. If the caller omits it, append `.pos`
rather than writing a native file with a misleading `.json` name. Normalize the
suffix first and then pass that exact final path to
`guard.resolve_in_workspace`, so confinement, the Java call, status, and the
artifact all refer to the same path. Loading may
accept any filename because Micro-Manager itself historically documents JSON or
text suffixes, but the content must be a native Micro-Manager PositionList.

The artifact remains:

```json
{"kind": "position_list", "path": "/resolved/path/positions.pos"}
```

Artifact and status paths must use the final suffixed path.

## Error handling

- Convert Java I/O/parser failures into a concise `ValueError` (or a dedicated
  position-list format error) that includes the path but not a bridge traceback.
- Distinguish invalid native format, unsafe coordinates, duplicate/empty labels,
  and entries with no supported stages.
- Never fall back to parsing the file as JSON in Python after Java loading
  fails. The old private format has no detection or migration path.
- Never change the native GUI list or `_positions` on a failed load.
- Saving should propagate overwrite and filesystem errors; it must not report an
  artifact unless the native `save` call succeeds.

An unsupported-only native entry requires an explicit user policy choice the
first time it is encountered. The recommended choice is to retain it in
Micro-Manager and omit it from microclaw's navigable projection. Record that
choice for the current operation only and re-run preflight; do not silently turn
it into a permanent global policy.

## Additional implementation requirements

The following are established parts of the implementation, not optional follow-up
work.

### `rank_hook_log` also reads the on-disk position-list format

`rank_hook_log` in `microclaw/tools.py` accepts a `position_list_path` and
parses it as the *old* microclaw projection:

```python
saved = json.loads(Path(position_list_path).read_text(encoding="utf-8"))
selected = saved.get("positions", saved) if isinstance(saved, dict) else saved
actual = [p.get("name", p.get("position")) for p in selected]
# ... p.get("x_um"), p.get("y_um"), p.get("z_um") ...
```

This is a third consumer of the saved format that requirement 6 does not list
(it enumerates tool descriptions, prompts, README, artifacts, and tests, but not
this reader). Once `save_position_list` writes a native `.pos` Property Map,
`json.loads` still succeeds — the file is JSON — so nothing raises. But
`saved.get("positions", saved)` returns the whole Property Map dict, iterating it
yields string keys, and `p.get(...)` then raises `AttributeError` or silently
produces garbage. The verification result becomes wrong without an error.

The fix must route this reader through the same native projection helper as load,
not `json.loads`. A concrete draft is in "Draft: `rank_hook_log` fix" below. This
means the "deliberately offline" contract of `rank_hook_log` is relaxed to "no
hardware moves and no image analysis": projecting a native file requires the Java
parser over the bridge, which is not a hardware operation. Note that change in
the tool docstring.

`rank_hook_log`'s file check is read-only replay against the *file the caller
passed*, not against the current native GUI list, and is therefore exempt from
the refresh/`position_list_conflict` machinery described under the preflight
model. It projects the file for comparison only; it drives no hardware and
publishes no state, so it must not run the safety preflight and must not raise a
conflict — a saved file with an out-of-bounds entry is still a valid thing to
verify a ranking against. `project_position_list_file` (drafted below) performs
exactly this: parse and project, no safety validation beyond reporting structural
projection issues. `rank_hook_log` is the offline file comparison; there is no
separate live-list ranking operation implied by this design.

### `import_mm_positions` uses the same load-then-filter antipattern

`import_mm_positions` in `microclaw/tools.py` calls
`import_from_mm_position_list()` and then `_validate_stored_positions()`, which
mutates the store by removing entries after the fact and returns a `rejected`
field — the exact non-atomic pattern this design removes from `load_position_list`
(it loads, then filters unsafe positions afterward). Under the new model this tool
must route through the same refresh/preflight path, surface a
`position_list_conflict` instead of silently dropping entries, and drop its
`rejected` field. Otherwise the codebase keeps one position-ingest tool that
refuses conflicts and a sibling that silently filters. `_validate_stored_positions`
should be retired once both ingest paths use the preflight.

### Save/load I/O executes in the Java process, not in Python

`PositionList.save(path)` and `candidate.load(path)` run on the Micro-Manager
(Java) host over the ZMQ bridge; the byte-level write and read happen wherever
that process runs, not in the microclaw process. `resolve_in_workspace` resolves
the path against microclaw's filesystem view only. Consequences:

- Requirement 5's confinement guarantee is weaker than it reads: microclaw
  validates the string, but Java writes to whatever that path resolves to on its
  side. This is safe **only under co-location** — microclaw and Micro-Manager on
  the same machine with the same paths — which is the supported deployment.
- Overwrite/permission/parse failures surface as bridge exceptions from another
  process. The Error handling "convert Java I/O/parser failures into a concise
  `ValueError`" bullet must catch bridge exceptions, not Python `OSError`.

### Co-location precondition

This design supports only the existing deployment in which microclaw and
Micro-Manager run on the same host and see the same absolute paths. Enforce and
document that precondition. A future split-host deployment requires an explicit
file-transfer protocol and is outside this design.

## Draft: `rank_hook_log` fix

Add a non-destructive, read-only projection helper on the controller that reuses
the load prepare-phase without committing:

```python
# microclaw/controller.py
def project_position_list_file(self, path: str) -> PositionProjection:
    """Project a native .pos file to microclaw entries WITHOUT publishing it.

    Constructs a throwaway candidate PositionList, lets Micro-Manager parse the
    file, and derives the projection. Does not touch the current GUI list or
    self._positions. Raises ValueError on an unparseable/non-native file.
    """
    from pycromanager import JavaObject
    candidate = JavaObject("org.micromanager.PositionList", port=self._port)
    try:
        candidate.load(str(path))
    except Exception as e:  # bridge/parse failure -> concise error
        raise ValueError(f"{path} is not a native Micro-Manager position list.") from e
    return self._project_mm_position_list(candidate)
```

Then `rank_hook_log` reads through it instead of `json.loads`:

```python
# microclaw/tools.py — needs `import math` at module scope.
# A completed hook log may predate the rounding-removal change and still carry
# 3-decimal values, which
# differ from a full-precision .pos by up to ~5e-4 µm. A picometer tolerance
# would spuriously fail exactly those historical replays, so use 1e-3 µm (1 nm).
POSITION_ABS_TOL_UM = 1e-3

# inside rank_hook_log, replacing the json.loads block
if position_list_path:
    position_list_path = guard.resolve_in_workspace(position_list_path)
    projection = ctrl.project_position_list_file(position_list_path)
    selected = projection.native_entries
    actual = [p["name"] for p in selected]
    expected = [r["position"] for r in rows[:len(actual)]]
    coordinate_matches = []
    for saved_position, ranked in zip(selected, rows):
        axes = ("x_um", "y_um", "z_um")
        presence_matches = all((a in saved_position) == (a in ranked) for a in axes)
        values_match = presence_matches and all(
            a not in saved_position or math.isclose(
                saved_position[a], ranked[a], rel_tol=0.0, abs_tol=POSITION_ABS_TOL_UM
            )
            for a in axes
        )
        coordinate_matches.append(values_match)
    result["position_list_verification"] = {
        "path": position_list_path,
        "matches_ranking_prefix": actual == expected and all(coordinate_matches),
        "label_match": actual == expected,
        "coordinate_matches": coordinate_matches,
        "projection_issues": projection.issues,
        "expected": expected, "actual": actual,
    }
```

Notes:

- The projection helper yields canonical `name`/`x_um`/`y_um`/`z_um` keys, so the
  old `p.get("name", p.get("position"))` and `saved.get("positions", saved)`
  fallbacks are no longer needed — the native file has one shape.
- Update the `rank_hook_log` docstring: it is offline in the sense of no hardware
  motion and no image analysis, but it now parses the position file through the
  Java bridge.
- Add `import math` at module scope, and compare coordinates with the `1e-3` µm
  (1 nm) absolute tolerance, not exact equality. This is deliberately looser than
  representational exactness so a full-precision `.pos` still matches an older,
  3-decimal-rounded hook log; anything tighter reintroduces spurious mismatches.
- Add a fixture test that ranks a hook log against
  `tests/fixtures/PD_PositionList2.pos` and
  asserts `label_match` and `coordinate_matches` for the `spiral_*` labels.

## Test plan

### Unit tests

- Replace the proprietary controller round-trip test with Java-object fakes that
  assert `PositionList.save(path)` and `PositionList.load(path)` are called.
- Assert saving uses the manager's current native list, including a GUI-only
  entry, rather than `_positions`.
- Assert every hardware-driving position consumer refreshes from the native list
  at its operation boundary and never uses a deliberately stale `_positions` cache.
- Assert saving persists an inconsistent native list (duplicate label, unconfigured
  device) rather than refusing, and returns the projection conflict alongside the
  successful save.
- Assert the read-only listing tool returns the projectable entries with a
  `position_list_conflict` attached, rather than refusing, when the native list is
  inconsistent, and does not replace `_positions` with that partial result.
- Assert `import_mm_positions` surfaces a `position_list_conflict` instead of
  silently dropping entries, and no longer returns a `rejected` field.
- Assert `rank_hook_log`'s file check projects the passed file without preflight,
  does not raise a conflict on an out-of-bounds saved entry, and never touches the
  live native list or `_positions`.
- Assert conflicts return every detected inconsistency, perform no movement or
  acquisition, change neither representation, and require a separate confirmed
  resolution followed by a fresh preflight.
- Assert load resolution re-reads the file, verifies its path and content hash,
  rejects a changed file, and never retains a candidate Java proxy across turns.
- Assert a file change between the pre-load and post-load hash reads returns
  `file_changed_during_load` and publishes neither the candidate nor `_positions`.
- Assert unsupported-only entries offer the preserve-and-omit choice and that
  choosing it applies only to the retried operation.
- Assert load parses into a temporary list and calls `set_position_list` exactly
  once only after validation.
- Assert malformed, unsafe, duplicate-label, non-finite, and bridge-failure
  cases leave both old states unchanged.
- Cover XY-only, Z-only, XY+Z, extra one-axis devices, empty/default stage names,
  and unsupported-only entries.
- Assert native metadata is not rebuilt or discarded during load and save.
- Assert an old private JSON array is rejected as an invalid native file without
  special detection, migration guidance, or fallback parsing.
- Assert offline ranking retains unsupported labels, treats missing-axis presence
  as a mismatch, and compares coordinate values with the documented `1e-3 µm`
  (1 nm) absolute tolerance rather than exact equality. Include a case where a
  3-decimal-rounded log still matches a full-precision `.pos`.
- Update workspace-confinement, artifact-path, schema-description, README, and
  agent-prompt assertions for `.pos`.

### Fixture/contract tests

- Commit `tests/fixtures/PD_PositionList2.pos` (or a minimal derived fixture retaining the same
  header and typed structure) and assert it projects to 25 ordered positions,
  from `spiral_01` at `(6073.4, 1555.7)` through `spiral_25` at
  `(6473.4, 1155.7)` when `SmarActXY` is the configured XY device.
- Add a fixture containing XY, focus Z, another one-axis device, nonzero grid
  coordinates, and custom properties.
- Assert that projecting `tests/fixtures/PD_PositionList2.pos` under a different configured XY
  device name yields all-unsupported entries (no coordinates) while the native
  list is retained unchanged, documenting the device-name-bound interop limit.

### Live Micro-Manager integration tests

1. Mark XY+Z positions through microclaw, save `.pos`, clear, load through
   Micro-Manager, and verify labels/device coordinates in its current list.
2. Create/edit a list in the Position List Manager, save it there, load through
   microclaw, and verify the internal projection and GUI list agree.
3. Load `tests/fixtures/PD_PositionList2.pos`, save it through microclaw, reload the result in
   Micro-Manager, and compare the two `PositionList.toPropertyMap()` values.
4. Verify the exact pycro-manager spellings for `PositionList.load/save`, stage
   device labels, and default-stage accessors on the supported MM build.

The strongest acceptance criterion is the property-map comparison in test 3;
comparing only microclaw's projection would miss metadata loss.

## Rollout

1. **Complete:** `design/31-native-position-file-spike.py` passed against
   MMCore 12.5.0. It loaded `tests/fixtures/PD_PositionList2.pos` into a temporary
   Java `PositionList` without publishing it to the GUI or moving hardware, and
   verified the XY bridge spellings, native save/reload, and Property Map
   equality. One-axis/Z and non-empty default-stage behavior remain for a
   focused fixture or live integration test.
2. **In progress:** candidate projection and transactional load helpers are
   implemented and unit-tested. Load/import now return structured conflicts
   without post-publication filtering. The remaining position-consuming tools
   must still adopt the refresh preflight before this step is complete.
3. **Complete in code; live verification pending:** save uses the native Java
   method, appends `.pos`, preserves inconsistent native lists losslessly, and
   refreshes the cache only after tool-layer safety validation.
4. **In progress:** tool schemas, the agent prompt, README, and affected unit
   tests describe the native format. Complete the remaining consumer and live
   integration updates in the same implementation series.
5. Exercise both directions against a live Micro-Manager build before release.

## Decision summary

Use Micro-Manager's `PositionList.load/save` as the only file-format
implementation. Treat the Java `PositionList` as the lossless authoritative
state, derive microclaw's compact navigable projection from it, and validate a
temporary candidate before atomically publishing a load. This produces true
interoperability while avoiding a brittle duplicate implementation of
Micro-Manager's typed Property Map schema.
