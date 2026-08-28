# Block 59b implementation report — turn 1

## Checklist landing points

- 29, 29a: the generic reference is `microclaw/optics_docs.py:1`. Its first
  section keeps “how light paths usually work”, the usual ordering, and the
  inverted/epi/TIRF/spinning-disk/optosplitter/multi-camera caveat together.
  It covers ports and split vocabulary, motorized-versus-manual visibility,
  the manual prism first on a blank frame with a valid lock, objective terms,
  unnamed turret positions, capture bands, offsets, and wrong-surface locks.
  The adjacent-caveat and no-Nikon tests are `tests/test_optics_docs.py:6` and
  `:15`.
- 30: `get_optical_path_documentation`, decorated with `@emits_nothing`, is at
  `microclaw/tools.py:8198`; registry entry at `microclaw/tools.py:9055`; schema
  at `microclaw/tools_schema.py:1977`.
- 31: the orientation hint names the tool at `microclaw/tools.py:3065`; the
  shipped-prompt assertions are `tests/test_optics_docs.py:23`.
- 33: the sole `SYSTEM_PROMPT` addition is `microclaw/agent.py:141`. The Nikon
  rigs section was not touched.
- 34a: retained adapter name/description and per-field unknown/error handling
  are in `microclaw/authorization.py:789`; call-retention and failure tests are
  `tests/test_optical_path_state.py:269` and `:286`.
- 34b, 34e: independently sourced ordered role lists, adapter-only matching,
  label-only matching, and the device-label negative control are at
  `microclaw/tools.py:3025`, tested at `tests/test_optical_path_state.py:247`.
- 34b1: the ASCII-boundary tokenizer and compound-first vocabulary are at
  `microclaw/tools.py:2974`; positive, negative, and structural-order fixtures
  are at `tests/test_optical_path_state.py:223`, `:231`, and `:236`.
- 34c: conditional `positions_unnamed`, including the identity-scoped
  `devices/` route and no-renaming statement, is at `microclaw/tools.py:3038`,
  tested by `tests/test_optical_path_state.py:247`.
- 34c1, 34c1a: the fixed discriminator/condition shape, exact identity match,
  retirement on camera/adapter/allowed changes, and ordinary-entry negative
  control are at `microclaw/tools.py:2982-2999` and `:3051-3062`, tested at
  `tests/test_optical_path_state.py:300` and `:321`.
- 34c2: structured maps are omitted from prompt formatting at
  `microclaw/knowledge_manager.py:87`; live orientation applicability is
  evaluated at `microclaw/tools.py:3051`. Prompt/legacy coverage is
  `tests/test_knowledge_manager.py:105` and orientation coverage is
  `tests/test_optical_path_state.py:300`.
- 34c3, 34c4: fail-closed identity validation and controller-resolved save
  conditions are at `microclaw/tools.py:2991` and `:8288-8322`. Confirmation,
  wrong-condition, unreadable-field, missing-target, and invalid-position tests
  are at `tests/test_tools.py:4529-4614`.
- 34d: the bridge-shaped orientation fake rejects both state-label writer
  spellings at `tests/test_optical_path_state.py:107`; the documentation tool is
  exercised against a write-rejecting bridge at `tests/test_optics_docs.py:36`.
  The explicitly scoped module source assertion is `tests/test_optics_docs.py:29`.
  The next-turn setup program is intentionally absent from this turn and thus
  not named in this turn's executable assertion.
- 35: schema parity, exporter/decorator coverage, focused tests, and the full
  suite are green.

## Watch-it-fail evidence: tokenizer

I evaluated the exact `_PORT_LABEL_WORDS.search()` pattern from `main` before
landing the replacement. The false positives were:

- `Brightfield` → `right`
- `Photoactivation` → `photo`
- `Portrait` → `port`
- `Outside` → `side`
- `Photobleach` → `photo`

The design's separate statement that the `Trinocular` fixture fails on `main`
is not true for the stated `.search()` fixture. Although `trinocular` is absent
as a complete alternate, main's substring matcher finds `ocular` inside
`Trinocular`, so that positive fixture passes for the wrong reason. Under the
new boundary rule, omitting the `trinocular` compound would make it fail; the
new vocabulary includes the compound explicitly.

## Mutation evidence

- Adapter retention: I mutated `_optical_path_state` to call
  `get_device_name` and `get_device_description` again for every retained
  StateDevice. `test_adapter_metadata_is_retained_once_and_failures_preserve_the_entry`
  failed with `adapter_name_calls == ['Path', 'Path']` instead of `[]`.
- Compound ordering: I moved `eye` before `eyepiece` in the alternates.
  `test_port_tokenizer_lists_compounds_before_their_prefixes` failed with the
  compound at index 1 and its prefix at index 0. The runtime match alone stays
  green because Python's regex engine backtracks to the longer alternate after
  the short alternate fails its lookahead; the explicit structural assertion is
  therefore what enforces the requested order.

Both mutations were restored before the final runs.

## Test results

Focused command:

`/Users/zachcm/miniforge3/envs/microclaw/bin/python -m pytest -q -p no:cacheprovider tests/test_optical_path_state.py tests/test_optics_docs.py tests/test_knowledge_manager.py tests/test_tools.py tests/test_schema_parity.py tests/test_session_script_export.py`

Result: **756 passed / 0 skipped / 0 failed** (2 warnings).

Full command (run outside the filesystem sandbox because localhost socket tests
cannot bind inside it):

`/Users/zachcm/miniforge3/envs/microclaw/bin/python -m pytest -q -p no:cacheprovider`

Result: **2408 passed / 99 skipped / 0 failed** (3 warnings), 127.66 seconds.

## Design judgments and deliberately untouched work

- I left the Nikon prompt prose and its PFS offset/immersion facts exactly where
  they are. Their eventual movement may be appropriate, but items 32 and 34 are
  explicitly assigned to 59c and this turn authorized only one prompt line.
- I did not create either demo gate program or the scorer (items 36–44).
- As noted above, the design's claimed `Trinocular` failure on main is factually
  wrong because substring `ocular` matches. I preserved the requested resulting
  vocabulary and structural ordering, but report the actual evidence rather than
  claiming a failure that did not occur.
- The design says alternate ordering is required because `eye` consumes
  `Eyepiece`; Python regex alternation backtracks after the boundary failure, so
  runtime behavior does not depend on that order in this implementation. I kept
  and structurally tested the mandated compound-first order because it remains
  clearer and protects the specified representation.
