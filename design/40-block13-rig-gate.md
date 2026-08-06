# Block 13 — rig gate runbook

Branch `design40/platform-defects`. Read this on the rig, on this branch.

Block 13 fixes seven platform defects found on the Nikon and on M5. None of them
is rig-specific. This gate has **five steps**; each maps to one checklist gate
item. G3 needs transmitted light, G4 needs fluorescence — everything else runs on
any rig, including Demo.

## Before you start — confirm you are on the right code

```powershell
cd C:\path\to\microclaw
git merge-base --is-ancestor c2fc7ab HEAD
if ($?) { "PIN OK - implementation is present" } else { "PIN FAIL - stop, wrong branch" }
```

`$?` is used rather than `$LASTEXITCODE` because a cmdlet in between silently
staled that variable on an earlier gate. Read the printed words, not an exit code.

Then confirm the package matches the checkout:

```powershell
pip install -e .
python -c "import microclaw, sys; print('LOADED FROM', microclaw.__file__)"
```

Start microclaw normally (`microclaw serve` or however you usually open it) and
work through the steps as ordinary requests. **Do not run scripts.** Every step
below is a thing you ask microclaw to do in plain language; the evidence is the
session history it writes.

At the end, note the history file path — everything is checked against it:

```powershell
$H = "path\to\your\new_microclaw_history.jsonl"
```

Paste this helper once; every count below uses it:

```powershell
function Count-Hits($pattern) {
  @(Select-String -Path $H -Pattern $pattern -SimpleMatch -AllMatches |
      ForEach-Object { $_.Matches }).Count
}
```

Two things it gets right that the obvious one-liner does not. `-SimpleMatch`
matches the pattern **literally**, which matters because tool results are stored
as *escaped* JSON — the file contains `\"snr_valid\": false`, backslashes and
all — so a regex would have to escape them correctly. And the `@( … ).Count`
wrapper returns a real **0** on no match; `(Select-String …).Matches.Count`
returns nothing at all when there are no matches, so a failing check would print
a blank line that is easy to record as "fine".

Sanity-check the helper before trusting any result — this must print a number,
and that number must be greater than zero:

```powershell
Count-Hits 'tool_use'
```

**These patterns were validated by literal string count against the pre-fix M5
smiley history. The PowerShell itself was not executed before shipping** — no
Windows shell was available — so if the helper misbehaves, say so and report the
raw counts however you can get them. That is a finding about this runbook, not
about the code.

---

## G1 — a refused marked survey does not poison the position list

*Checklist item: a hooked tile survey with `mark_positions=True` that fails
mid-run leaves a state the next identical call can run from.*

1. Ask microclaw to **show the current position list** and note the count.
2. Ask it to run a **hooked tile survey with position marking on**, using a grid
   that deliberately runs off the edge of your stage travel — big enough that the
   safety limits must refuse it. Use the `snr_observer` hook.
3. It should refuse. **Ask again to show the position list.**
4. Now ask for the **same survey with a grid that fits**, marking on.

**PASS** when all three hold:

- the oversized grid is refused;
- the position list count in step 3 is **unchanged from step 1**;
- the in-bounds survey in step 4 completes.

Record the two counts and the refusal message.

> Known-bad reference: on the run that motivated this (design/40 defect 3), 25
> entries were left behind by a failed grid and the next call was refused
> quoting the **old** grid's coordinates. If you see coordinates you did not just
> ask for, that is the failure.

---

## G2 — `rank_hook_log` can read the log its own survey just wrote

*Checklist item: `rank_hook_log` ranks the log its own hooked survey just wrote.*

1. Using the survey from G1 step 4 (or any hooked survey with `snr_observer`),
   ask microclaw to **rank that hook log by SNR**.

**PASS** when a ranking comes back with at least one ranked row. **FAIL** if it
errors, in particular with anything about *missing required field(s)*.

Then check the history:

```powershell
Count-Hits '\"ranked_entry_count\"'
```

**PASS** when this is 1 or more.

> Validated both directions: this pattern counts **0** on the pre-fix M5 smiley
> history and is emitted by the fixed `rank_hook_log` on every call.
>
> `-SimpleMatch` is not optional. Tool results are stored inside the history as
> **escaped** JSON — the file literally contains `\"snr_valid\": false` — so the
> backslashes are part of the text being searched. Without `-SimpleMatch` the
> pattern is treated as a regex and the escaping has to be got exactly right;
> with it, the string is matched literally and there is nothing to get wrong.

---

## G3 — a transmitted-light field is reported honestly (needs brightfield/phase)

*Checklist item: a field the operator calls usable is reported consistently with
the SNR decision.*

**Skip and say so if this rig has no transmitted-light path.**

1. Set up brightfield or phase on a field where **you can plainly see cells**.
2. Ask microclaw to **snap and analyze** it.

**PASS** when the reply says SNR is **not scored** for this frame, with a reason
naming negative-going contrast — *and* still reports a focus metric it treats as
valid. The point of the fix is that SNR refuses while the focus metric survives,
because Tenengrad does not care about polarity.

**FAIL** if it reports a small SNR and calls the focus metric invalid (the old
behaviour), or if it refuses the focus metric too.

```powershell
Count-Hits '\"snr_invalid_reason\"'
```

**PASS** when 1 or more.

> Validated both directions: counts **0** on the pre-fix smiley history; present
> on any refused frame after the fix.

---

## G4 — a clipped fluorescence field reports invalid SNR, not a big number

*Checklist item: a deliberately over-exposed field is reported as a saturated,
invalid SNR rather than a large one.*

This reproduces directly on the M5 smiley conditions — beads at 100 ms after
autofocus clipped every frame — so it is a re-run of a known-clipping field, not
a new setup.

1. On a fluorescent sample, ask microclaw to **snap and analyze**.
2. Raise the exposure (ask it to) until the image is plainly clipping, and
   **snap and analyze again**.

**PASS** when the clipped snap reports SNR as **invalid / not a number**, with a
reason naming saturation. **FAIL** if it reports a large SNR — that is exactly
the defect: the old code reported 173–716 on frames where every pixel was at
65535.

```powershell
Count-Hits '\"snr_valid\"'
```

**PASS** when 1 or more.

> **Do not** check `saturated_fraction` here. Measured on the pre-fix history it
> counts **19** — it was always computed and simply ignored — so a criterion
> built on it passes on a broken run. `\"snr_valid\"` counts **0** there. That
> difference is the whole finding, and it is why this step checks the flag rather
> than the number everyone looks at.

---

## G5 — a single-frame multi-position dataset mosaics in one call

*Checklist item: an `n_frames=1` multi-position dataset mosaics in **one** call
to `build_stage_coordinate_mosaic` with no `axis_selection` argument.*

1. Run a **multi-position acquisition with `n_frames=1`** over a few positions.
2. Ask microclaw to **build a stage-coordinate mosaic of that dataset**. Ask once,
   plainly, and do not supply any axis hints.

**PASS** when the mosaic is built on the **first** attempt.

```powershell
Count-Hits '"name":"build_stage_coordinate_mosaic"'
```

This counts **one per actual call**. **PASS is 1.** Report the raw number.

> The obvious pattern — the bare tool name — is wrong, and this is worth knowing
> before you write your own check. On the pre-fix history the bare name counts
> **5** for **3** real calls: two of the five are the assistant *mentioning* the
> tool in prose. There is no clean multiplier to divide out. Anchoring the
> pattern to the `"name":"…"` field is what makes the count mean calls. Note also
> that the file has **no space after the colon**; `"name": "…"` counts 0 on every
> run and would silently pass.

> Known-bad, measured on the smiley history: **three** calls, with
> `axis_selection` escalating `{'time': 0}` → `{'z': 0}` → `{'z': 0, 'time': 0}`,
> because the error named only the axis that call omitted. Both axes were
> length 1, so both refusals had exactly one legal completion.

---

## Recording the result

For each of G1–G5 write **PASS**, **FAIL**, or **SKIPPED (reason)**, and paste:

- the position-list counts from G1;
- the four `Select-String` numbers;
- the exact text of any refusal or warning you were asked to read;
- the history file itself.

A step you could not run is not a pass. Say which rig each step ran on — Demo,
M2, M5 and the Nikon are different claims.

If anything fails, stop there and report. Do not work around it: a criterion that
needed a workaround is a finding about the code or about this runbook, and both
are worth more than a green row.
