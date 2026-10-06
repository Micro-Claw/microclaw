# 83f-6 implementation report

`pack(..., locks=None)` and `pack --locks <folder>` now fill executable locks
from offline PEP 751 files. Interpreter selection and lock conversion share
`skill_store.PLATFORMS`. The existing manifest validator still owns exact-pin,
SHA-256, 64-hash and 512-entry validation.

Files changed:

- `microclaw/catalog_intake.py`: bounded TOML intake, fixed marker evaluation,
  fitting wheel selection, contextual refusals and CLI flag.
- `microclaw/skill_store.py`: shared platform facts and interpreter selection.
- `design/83f4-catalog-repo/{README.md,publishing/release.yml}`: uv commands and
  conditional `--locks locks`.
- `tests/test_catalog_intake.py`: real samples, mutated refusals, filtering,
  fixed marker environments and CLI coverage; template comparison updated.
- `tests/test_skill_store.py`: CLI packing followed by real offline installation
  of two pins; a false-marker dependency is absent from probed distributions.
- `tests/fixtures/skill_packages/build_release.py` and
  `wheels/fixture_companion-2.0.0-py3-none-any.whl`: second reproducible wheel.
- `tests/fixtures/skill_packages/pylock/{requirements.in,pylock.win_amd64.toml,pylock.macosx_arm64.toml,pylock.manylinux_x86_64.toml}`:
  unchanged reference copies.
- This report.

Refusals and example messages below omit the leading exception field, which is
listed separately. Package-level failures name the original entry index;
folder/file-level failures have no package entry to name.

| Refusal | Field | Example message |
| --- | --- | --- |
| Markdown manifest | `locks` | `locks: --locks requires an executable manifest` |
| Symlink folder | `locks` | `locks-link: expected a regular locks directory, not a symlink` |
| Non-directory | `locks` | `locks/requirements.in: expected a regular locks directory, not a symlink` |
| Folder inside package | `locks` | `package/locks: locks directory must be outside the package directory` |
| Missing file / symlink file | `locks.win_amd64` | `pylock.win_amd64.toml: expected a regular platform lock file` |
| Extra file | `locks.other` | `pylock.other.toml: unexpected platform file` |
| Unsupported platform | `locks.other` | `pylock.other.toml: unsupported platform; installer cannot select it` |
| Oversized file | `locks.win_amd64` | `pylock.win_amd64.toml: lock file exceeds byte limit` |
| Unreadable / invalid TOML | `locks.win_amd64` | `pylock.win_amd64.toml: expected a readable TOML lock file: Invalid initial character for a key part (at end of document)` |
| Missing / wrong lock version | `locks.win_amd64` | `pylock.win_amd64.toml: lock-version must have major version 1` |
| Invalid packages collection | `locks.win_amd64` | `pylock.win_amd64.toml: expected [[packages]] entries` |
| Missing name | `locks.win_amd64` | `pylock.win_amd64.toml: packages[0] (? 2026.7.22): name and version are required strings` |
| Missing version | `locks.win_amd64` | `pylock.win_amd64.toml: packages[0] (certifi ?): name and version are required strings` |
| Undecidable marker (including nested branches / extra) | `locks.win_amd64` | `pylock.win_amd64.toml: packages[0] (certifi 2026.7.22): undecidable marker variable(s): python_full_version` |
| Invalid marker | `locks.win_amd64` | `pylock.win_amd64.toml: packages[0] (certifi 2026.7.22): invalid marker: Expected a marker variable or quoted string` |
| VCS / directory / archive source | `locks.win_amd64` | `pylock.win_amd64.toml: packages[0] (certifi 2026.7.22): vcs, directory and archive sources are unsupported; use index or find-links wheels` |
| Duplicate canonical name | `locks.win_amd64` | `pylock.win_amd64.toml: packages[10] (Certifi 2026.7.22): duplicate canonical package name: certifi` |
| Invalid wheel collection | `locks.win_amd64` | `pylock.win_amd64.toml: packages[0] (certifi 2026.7.22): wheels must be an array` |
| Invalid wheel filename | `locks.win_amd64` | `pylock.win_amd64.toml: packages[0] (certifi 2026.7.22): invalid wheel filename: Invalid wheel filename (wrong number of parts): 'broken'` |
| Wrong platform wheels | `locks.macosx_arm64` | `pylock.macosx_arm64.toml: packages[2] (h5py 3.16.0): no wheel for macosx_arm64 and CPython 3.12; installs never build from source` |
| Musllinux-only wheels | `locks.win_amd64` | `pylock.win_amd64.toml: packages[0] (certifi 2026.7.22): no wheel for win_amd64 and CPython 3.12; installs never build from source` |
| Sdist-only entry | `locks.win_amd64` | `pylock.win_amd64.toml: packages[0] (certifi 2026.7.22): no wheel for win_amd64 and CPython 3.12; installs never build from source` |
| Missing fitting wheel hash | `locks.win_amd64` | `pylock.win_amd64.toml: packages[0] (certifi 2026.7.22): fitting wheel certifi-2026.7.22-py3-none-any.whl requires a sha256 hash` |
| Invalid hash (existing validator) | `locks.win_amd64` | `pylock.win_amd64.toml: packages[0] (certifi 2026.7.22): locks.win_amd64[0].hashes[0]: expected lowercase SHA-256 hex` |
| Invalid exact pin (existing validator) | `locks.win_amd64` | `pylock.win_amd64.toml: packages[0] (certifi *): locks.win_amd64[0].requirement: expected one unconditional exact version pin` |
| Hash bound (existing validator) | `locks.win_amd64` | `pylock.win_amd64.toml: packages[0] (certifi 2026.7.22): locks.win_amd64[0].hashes: exceeds 64 entries` |
| Entry bound (existing validator) | `locks.win_amd64` | `pylock.win_amd64.toml: locks.win_amd64: exceeds 512 entries` |

Test summary lines observed, verbatim:

```text
# Initial catalog intake + skill packages run
1806 passed in 9.97s
# Initial offline install run (fixture bytecode contamination)
1 failed, 170 deselected in 0.96s
# Offline install after copying source assets only
1 passed, 170 deselected in 5.46s
# Pre-change CLI regression check: --locks is unrecognized
1 failed, 159 deselected in 2.22s
# Additional platform tag and marker tests
1812 passed in 9.68s
# Final catalog intake + skill packages run
1813 passed in 10.05s
# Final offline install run
1 passed, 170 deselected in 0.58s
```

The three committed pylocks are byte-identical to `.83f6-uv-samples/`; that
reference folder remains untracked. No downloads, compilation, full-suite run,
push or merge were attempted. No functional requirement was deferred. Live
PyPI/demo-machine probes remain the coordinator's work. The existing Markdown
example workflow was left unchanged; its comparison test allows only the
requested template change. Test setup excludes bytecode created by prior
fixture imports, following the existing release builder's source-asset copying.
