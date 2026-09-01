# Block 62d — demo-machine gate

**One command.** Everything else in design/62 is settled off-rig; this is the
only gate the whole design asks anyone to run, and it needs no microscope.

## Run it

In PowerShell on the demo machine, on this branch
(`design62/inspect-artifacts-discovery`):

```powershell
cd C:\path\to\microclaw
git fetch origin
git checkout design62/inspect-artifacts-discovery
git merge-base --is-ancestor ddb60a8 HEAD
if ($LASTEXITCODE -eq 0) { "implementation present" } else { "WRONG TREE - stop" }

py -3 design\62-block62d-demo-gate.py --downloads "$env:USERPROFILE\Downloads"
"exit code: $LASTEXITCODE"
```

Then send me `block62d-demo-gate.log` from the current directory. The script
writes its own log, because PowerShell 5.1's `Start-Transcript` does not capture
a native child process's stdout — that came back empty twice on design/58.

If `py -3` is not on PATH, use the project's own interpreter
(`.venv\Scripts\python.exe` or whatever `install.bat` created). The script needs
nothing installed beyond microclaw itself, and **no safety config and no
`workspace_dir`** — the product does not require one for a read-only listing, so
the gate must not either.

## What it checks, and what it cannot

| limb | claim | why this machine |
| --- | --- | --- |
| A | a real Windows folder resolves and traverses; returned paths are drive-rooted with backslashes | macOS cannot instantiate a Windows path at all — `Path.resolve()` raises `NotImplementedError` |
| B | the exact string the tool returned is directly usable as an input `paths` entry, and hashes | POSIX paths cannot establish Windows path serialisation |
| C | **a bound is not an absence**: with `max_files` below the folder's file count, an absent pattern gives `matches=[]` **with** `truncated=true` and `examined_count`, while the same pattern unbounded gives a real negative | platform-independent in principle, but this is the limb design/62 §3 exists for and the real folder is here |
| D | **junctions are not followed during recursion** | design/62 asserts this is "already true". It is untested, it is about a Windows-only object, and design/58 was already bitten by a junction in this repo. Creates its own junction with `mklink /J` (no admin needed), in a temp fixture it removes afterwards |

**Two claims are deliberately not limbs.** Case-insensitive *matching* and
case-insensitive *ordering* are pure Python over `entry.name`, asserted directly
in `tests/test_inspect_artifacts_discovery.py`. A Windows run of them could not
fail, and a limb that cannot fail is not a criterion.

## Reading the result

- Every limb reports independently; one failure does not hide the others, and the
  script exits nonzero if any limb failed.
- **`NOT EXERCISED` is never a pass.** If limb C says your `Downloads` holds
  fewer than three files, or limb D says `mklink /J` failed, that limb produced
  no evidence and its row stays open — tell me rather than treating it as green.
- Limb D prints `Path.is_symlink()` for the junction whatever the verdict. That
  single boolean is the fact design/62 assumed; I want it in the log either way.
- The fixture path and `exists=False` are printed after limb D, so the log itself
  shows nothing was left behind.

## Already done off-rig

The gate was run against `design/62-block62d-gate-selftest.py`, which forces the
gate's own `IS_WINDOWS` flag and drives all four limb bodies on macOS with
`mklink` stubbed to a symlink. It found three defects before this reached you: a
`\U` escape that made the gate a syntax error on line 1, an `os.name` mutation
that made `pathlib` construct `WindowsPath` and die, and a control that silently
did not fire because `rglob` does not cross a symlinked directory. Limb D's
control now demonstrably fails against a link-following traversal.
