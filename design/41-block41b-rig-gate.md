# Block 41b — rig gate runbook

Branch `design41/script-export`. Read this on the rig, on this branch.

Block 41b implements `CLAUDE.md`'s stated principle that everything must compile
to a standalone pycro-manager script. The new tool is **`export_session_script`**:
you ask microclaw to export the session, it writes a `.py` next to the files it
made, and that script runs without microclaw.

Three steps. Any rig, including Demo. **G1 and G2 use one session; G3 uses a
second.**

## Before you start — confirm you are on the right code

```powershell
cd C:\path\to\microclaw
git merge-base --is-ancestor 8bfdceb HEAD
if ($?) { "PIN OK - implementation is present" } else { "PIN FAIL - stop, wrong branch" }
```

`$?` rather than `$LASTEXITCODE`: a cmdlet in between silently stales that
variable. Read the printed words.

```powershell
pip install -e .
python -c "import microclaw; print('LOADED FROM', microclaw.__file__)"
```

---

## G1 — an exported session runs with microclaw shut down

*Checklist item, as reworded 2026-08-06: the emitted script's **hardware routine
completes**, and the script stops only at a declared refusal. See the note at the
end for why the original "it completes" could not be met as written.*

**Session A — build it only from tools that emit.** Open microclaw and ask it to:

1. move the stage somewhere;
2. set an exposure;
3. snap and analyze;
4. **mark two or three positions**, then run a **small unhooked multi-position
   acquisition** (or a timelapse) over them.

Step 4 is deliberately the ordinary workflow — mark positions, then acquire from
the list without passing coordinates. The first M5 run of this gate (2026-08-06)
failed there twice: `mark_position` refused to emit, and behind it the
per-position emitter could not run. Both are fixed; keep the step as written so
the path stays covered.

**Do not build a mosaic in this session** — the offline mosaic is a deliberate,
approved refusal, and including it here would stop the script before the end. G3
is where that is tested on purpose.

Then ask microclaw: **"export this session as a standalone script next to the
data."** Note the path it reports.

Now **close microclaw completely.** Leave MMStudio running. Then:

```powershell
python C:\path\to\the\exported_script.py
```

**PASS** when the script runs to the end with microclaw not running, and creates
a dataset. Record any traceback verbatim.

Sanity checks on the file itself:

```powershell
Select-String -Path $S -Pattern 'import microclaw' -SimpleMatch | Measure-Object | Select-Object -ExpandProperty Count
```

(where `$S` is the exported script path). **PASS is 0.** An emitted script that
imports microclaw is not standalone and fails the block's whole premise.

---

## G2 — the script's dataset matches the session's, and the dose is the same

*Checklist item: the script's dataset and the session's dataset agree on frame
count and stage coordinates. Dose is compared explicitly.*

Compare the dataset G1's script just wrote against the one the original session
wrote.

1. **Frame count** — same number of images.
2. **Stage coordinates** — the positions match those in the session.
3. **Dose** — count the acquisitions in the emitted script:

```powershell
Select-String -Path $S -Pattern 'Acquisition(directory=' -SimpleMatch -AllMatches |
  ForEach-Object { $_.Matches } | Measure-Object | Select-Object -ExpandProperty Count
```

**PASS** when this equals the number of acquisitions **you actually asked for**
in session A — no more. One extra means the script images something the session
did not, which is the defect this block exists to fix.

> Validated against the committed export of the real M5 smiley run: it contains
> exactly **2** `Acquisition(directory=` blocks for the 2 acquisitions that
> session ran, and **0** attributable to its three mosaic steps.

---

## G3 — a session with an unsupported step stops there, and does not re-image

*Checklist item: a session using a tool with no emitter produces a script that
stops at the `# NOT EMITTED` line rather than running past it.*

**Session B.** Do a short acquisition, then ask microclaw to **build a
stage-coordinate mosaic** of that dataset — reading from the saved data, as
normal. Then export the session.

Open the exported script and read the end of it.

**PASS** when all three hold:

```powershell
Select-String -Path $S2 -Pattern '# NOT EMITTED: build_stage_coordinate_mosaic' -SimpleMatch | Measure-Object | Select-Object -ExpandProperty Count
```
— **1 or more**, and the line carries a **reason**, not just the tool name.

```powershell
Select-String -Path $S2 -Pattern 'enable_channel' -SimpleMatch | Measure-Object | Select-Object -ExpandProperty Count
```
— **PASS is 0.**

```powershell
Select-String -Path $S2 -Pattern 'Re-image' -SimpleMatch | Measure-Object | Select-Object -ExpandProperty Count
```
— **PASS is 0.**

Then run it (microclaw closed, MMStudio running). **PASS** when it performs the
hardware routine and then **stops loudly** with `RuntimeError: NOT EMITTED: …`.
Stopping there is the correct result. Running past it is a failure.

> Both directions are validated on real artifacts. The **known-bad** is the
> hand-written script the assistant pasted into chat on 2026-08-05, which is
> preserved in that session's history: it contains `Re-image kept tiles` once and
> `enable_channel` four times, because its reconstructed `build_mosaic`
> **re-imaged every kept tile** — doubling the dose on a bleaching sample. The
> **known-good** is this block's committed export of the same session: both
> patterns count **0**. These two checks are the entire point of the block.

---

## Recording the result

For each of G1–G3 write **PASS**, **FAIL**, or **SKIPPED (reason)**, and paste
the counts, any traceback, and **the exported scripts themselves** — those are
the primary evidence, more than the numbers.

Say which rig each step ran on. A step you could not run is not a pass.

**If a step fails, keep the artifact exactly as it is.** The failing script *is*
the evidence, and a re-test must write to a **new folder** rather than
regenerating over it — on 2026-08-06 the first M5 failure's script was
overwritten by the fix verification, and the artifact that documented the FAIL
was lost. The error text and the session history survived, which is the only
reason the finding held up. Name re-test folders with a round suffix.

## Note on the reworded G1

The checklist's original wording was *"run the emitted script … It completes."*
That cannot be met for any session containing an offline mosaic, because the
mosaic is an approved architectural refusal: `build_stage_coordinate_mosaic`
depends transitively on the package calibration module, so inlining it would not
be standalone, and fabricating a replacement is the defect being fixed. The
criterion is therefore split: **G1** proves a fully-emittable session runs to
completion, and **G3** proves an unsupported step stops loudly instead of being
guessed. Together they are what the original item was reaching for. This
reconciliation is the coordinator's, made 2026-08-06; the checklist item is
updated to match in the same merge.
