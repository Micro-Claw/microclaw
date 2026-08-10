# Block 43h rig gate — emitted adaptive programs

Implementation ancestor: 36c1bd7

Use PowerShell from the checked-out repository. uv is the single launcher for
every Python/project command below; do not substitute bare Python for one line.
Git commands only establish the checkout.

## Step 0 — pin and run the full Windows suite

    git merge-base --is-ancestor 36c1bd7 HEAD
    if ($LASTEXITCODE -ne 0) { throw "Block 43h implementation is not in this checkout" }

    uv run python -m pytest -q > suite-43h.txt 2>&1
    if ($LASTEXITCODE -ne 0) { Get-Content suite-43h.txt; throw "Full suite failed" }
    Get-Content suite-43h.txt

    uv run python -m pytest --collect-only -q > collected-43h.txt 2>&1
    if ($LASTEXITCODE -ne 0) { Get-Content collected-43h.txt; throw "Collection failed" }

Expected on the Windows rig for this branch: **1712 passed + 116 skipped = 1828
collected**, with 3 expected warnings. The total is derived on this branch, not
copied from an earlier block. Compare 116 skips with the previous run on this
same host; stop if tests failed, collection is not 1828, or the skip count rose.

## Step 1 — offline export checks

These checks do not book microscope time.

    uv run python -m pytest -q tests/test_session_script_export.py > export-tests-43h.txt 2>&1
    if ($LASTEXITCODE -ne 0) { Get-Content export-tests-43h.txt; throw "Exporter checks failed" }
    Get-Content export-tests-43h.txt

Pass when the file reports 79 passed. It covers all three seed shapes, exact
source inlining, saved-hook provenance, full-precision named-position
resolution, and narrow refusals.

## Step 2 — make one real adaptive program during an ordinary session

Fold this into a session with a sample and a small, safe plan. At least one
request must be phrased exactly at the user level, with no tool name, for
example:

> Watch this field for three frames, record the image quality each time, and
> give me a standalone script I can keep and rerun later.

Pass when the agent reaches the adaptive acquisition and exports a Python script
without being coached toward an exporter or acquisition function. Record the
session history and the emitted script.

Before ending the live session, inspect the script:

- Its event seed matches the requested frame interval/count (or Z range, if that
  is the safe session available).
- It contains the exact hook class, UntrustedHookAdapter, and the adaptive
  decision source, rather than the tiles/frames observed in this run.
- _LIMITS contains the rig limits in force at export, and the header says
  editing the dictionary edits those recorded limits.
- The script contains no filesystem path into this Microclaw checkout.

## Step 3 — close Microclaw and run the emitted program

Exit the Microclaw server/UI completely. Confirm no Microclaw process remains;
leave Micro-Manager and the bridge available. From PowerShell run the artifact
itself through the same launcher:

    uv run python .\PATH-TO-EXPORTED-SCRIPT.py > emitted-run-43h.txt 2>&1
    if ($LASTEXITCODE -ne 0) { Get-Content emitted-run-43h.txt; throw "Emitted adaptive run failed" }
    Get-Content emitted-run-43h.txt

Pass only if the script completes the real multi-frame acquisition with
Microclaw closed, writes the dataset, and writes the hook observations expected
for the acquired frames. Importing or compiling the file is not evidence.
Confirm from the acquisition display/dataset that exactly the seed program ran;
do not infer success only from an empty terminal.

Archive suite-43h.txt, collected-43h.txt, export-tests-43h.txt, the session
history, emitted script, emitted-run-43h.txt, dataset path, and the operator's
observed pass/fail notes under
Documents\Documents - Beyonce\Projects\Micro-Claw.
