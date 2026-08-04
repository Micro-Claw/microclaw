# design/36 — rig gate: does the focus metric peak at focus?

This runbook verifies branch `fix/focus-metric-inversion`. The metric
implementation is pinned at `408c447`, and the three design/37 fixes it now
carries (F3 operator-established state, F4 `start_live_view` readiness, F5
headless-sweep disclosure) at `c452328`. Both are ancestor checks: later
runbook or correction commits on this branch are valid descendants.

Run G0–G3 on **M5**, on the same field type that failed on 2026-08-04 (640 nm,
camera-triggered, the sample that produced `m5-autofocus-fail`). G4 is the
plugin-authorization gate and needs no sample. G5 is a live-view probe that
decides which fix to write for design/37 F4 — it has no pass/fail, and it is
here so that one trip to the rig settles both questions.

Preserve and return the complete evidence directory.

**What is being tested is a claim about your microscope, not about the code.**
Everything in design/36 so far is offline: the failure is reproduced from first
principles and the replacement is right-signed on synthetic fields. Whether it
peaks at *your* focus, on *your* sample, is what these gates answer, and only
you can run them. G2 is the one that matters — it is the comparison neither
failing session could make.

All commands are Windows PowerShell 5.1-safe. Set the invocation once.

```powershell
$Run = "uv run"     # uv-managed checkout
# $Run = ""         # microclaw and python already on PATH -- uncomment instead

function mc { if ($Run) { uv run microclaw @args } else { microclaw @args } }
function py { if ($Run) { uv run python @args }    else { python @args } }
```

`$LASTEXITCODE` is valid after `git`, `mc`, and `py`. It is stale after
PowerShell cmdlets such as `Select-String`.

---

## G0 — branch identity and full suite

```powershell
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$Evidence = "design36-$Stamp"
New-Item -ItemType Directory -Path $Evidence
git fetch origin > "$Evidence\git-fetch.txt" 2>&1
git switch fix/focus-metric-inversion > "$Evidence\git-switch.txt" 2>&1
echo $LASTEXITCODE > "$Evidence\git-switch-exit.txt"
git pull --ff-only > "$Evidence\git-pull.txt" 2>&1
git status --short > "$Evidence\status.txt" 2>&1
git rev-parse HEAD > "$Evidence\head.txt" 2>&1
git merge-base --is-ancestor 408c447 HEAD > "$Evidence\implementation-ancestor.txt" 2>&1
echo $LASTEXITCODE > "$Evidence\implementation-ancestor-exit.txt"
git merge-base --is-ancestor c452328 HEAD > "$Evidence\design37-fixes-ancestor.txt" 2>&1
echo $LASTEXITCODE > "$Evidence\design37-fixes-ancestor-exit.txt"
```

Reinstall before anything else — a stale editable install has produced errors
that looked like test failures and were not:

```powershell
py -m pip install -e . > "$Evidence\pip-install.txt" 2>&1
py -m pytest tests -q > "$Evidence\pytest.txt" 2>&1
echo $LASTEXITCODE > "$Evidence\pytest-exit.txt"
```

**PASS:** both ancestor exits 0, pytest exit 0.

## G1 — the offline reproduction, on this machine

```powershell
py design\36-focus-metric-spike.py > "$Evidence\spike.txt" 2>&1
echo $LASTEXITCODE > "$Evidence\spike-exit.txt"
```

**PASS:** every `NEW tenengrad` line reads `peaks AT FOCUS`, and at least three
of the four `OLD nLV` lines read `peaks at defocus`. This is a numerical check,
not a rig check; it is here so the evidence directory carries the premise.

---

## G2 — the metric against your own manual focus (THE gate)

Bring up the same field and the same illumination as the failed session. Focus
**by eye**, as you did on 2026-08-04, and leave the stage there.

1. Record the manual focus Z. In microclaw:

   > What is the current stage position?

   Save the reply. Copy the console text into `$Evidence\g2-entry-z.txt`.

2. Snap and record the metric at your focus:

   > Snap and analyze the current field. Report focus_metric, focus_metric_kind,
   > snr and focus_metric_valid verbatim.

   Save to `$Evidence\g2-focus-metric.txt`. `focus_metric_kind` must read
   `tenengrad_gated` — if it still says `normalized_laplacian_variance_gated`,
   the install is stale; stop and redo the `pip install -e .` in G0.

3. Ask for a sweep across the range that failed before:

   > Run autofocus with a 20 µm range and a 0.5 µm fine step. Do not move the
   > stage anywhere first.

   Save the whole payload — both metric curves — to `$Evidence\g2-autofocus.txt`.

**PASS requires all three:**

- `converged: true` and `moved: true`.
- `final_z_um` within **±1 µm** of the manual focus Z from step 1.
- The coarse `metric_curve` has its maximum at an interior point, near that Z,
  and falls off toward both ends. On 2026-08-04 this curve was
  `[6.93, 4.75, 2.09, 4.23, 1.94, 3.04, 7.77, 11.94, 18.8]` — lowest in the
  middle, highest at the edge. It must now be the other way up.

**FAIL is informative either way.** If the curve peaks at your focus but
`converged` is false, that is a threshold problem (`MIN_CONTRAST`), not a sign
problem, and is a much smaller fix. If the curve is still U-shaped, the offline
reproduction does not describe your rig and design/36 needs re-deriving against
real frames — do not let anyone patch a threshold to make this pass.

Return the full payload whatever happens.

## G3 — the widened sweep no longer makes it worse

Same field, stage still at manual focus:

> Run autofocus again with a 40 µm range, same 0.5 µm fine step.

Save to `$Evidence\g3-autofocus-40um.txt`.

**PASS:** converges to within ±1.5 µm of the same Z as G2. The old metric's
signature failure was that widening the window moved the answer further away
(59.9 µm at ±10, 69.9 µm at ±20); the answer must now be stable.

---

## G4 — the hardware-motion plugin gate says what to do (no sample needed)

This is the second half of the 2026-08-04 session: `allow_hardware_motion: true`
was set, and startup refused with a rule and no remedy.

1. With your **current** `safety_config.yaml` (the one with
   `allow_hardware_motion: true` and `mode: guaranteed`), before restarting
   anything:

   ```powershell
   mc check-config > "$Evidence\g4-check-config-before.txt" 2>&1
   echo $LASTEXITCODE > "$Evidence\g4-check-config-before-exit.txt"
   ```

   **PASS:** it refuses **offline**, names `degraded_trusted_plugins`, and says
   the motion flag alone is not sufficient. The point of this gate is that you
   learn this without a restart and without the rig.

2. Edit the file yourself — microclaw cannot — and set
   `property_authorization.mode: degraded_trusted_plugins`, leaving
   `allow_hardware_motion: true`. Then:

   ```powershell
   mc check-config > "$Evidence\g4-check-config-after.txt" 2>&1
   echo $LASTEXITCODE > "$Evidence\g4-check-config-after-exit.txt"
   ```

   **PASS:** exit 0, and the output says the completeness claim is suspended.

3. Start microclaw against the rig and copy the startup banner into
   `$Evidence\g4-startup.txt`.

   **PASS:** it starts, and prints the DEGRADED authorization-map verdict
   rather than starting silently. A silent start is a fail — the suspension of
   the completeness guarantee must be visible every session it is in force.

4. Ask microclaw to run MM's own autofocus, and save the exchange:

   > List the Micro-Manager autofocus plugins, then run OughtaFocus as a
   > hardware-motion plugin hook on a single frame.

   **PASS:** it surfaces the classpath, asks for explicit confirmation before
   enabling, and the hook runs. Record what it did with Z.

   This is the first time an opaque motion plugin has been run under microclaw.
   Microclaw guards the *result* (it reads Z afterwards and checks the limits)
   but cannot intercept the plugin's motion. Have your hand near the stage, and
   do not run this with an objective close to a sample you care about.

5. **Decide deliberately whether to keep degraded mode.** If you are not
   actively using a motion plugin, set both settings back and restart into
   guaranteed mode. Record which you chose in `$Evidence\g4-final-mode.txt`.

---

## G5 — which way does the live-view restore fail? (design/37 F4)

**This is a probe, not a pass/fail gate.** It does not test a fix; it decides
which fix to write. Both outcomes below are useful, and neither is a failure.

Twice in the 14:38 session you were told live view was running when it was not,
and you diagnosed it yourself: *"the snap and analyze call after live view
killed it."* We now know the start was fine — `snap_and_analyze` reported
`"paused for the snap, then restored"`, which only happens when microclaw saw
live **on** — so it is `_pause_live`'s restore that failed silently. MM's
bytecode gives two ways that can happen, and they need different fixes. Nothing
microclaw currently reads can tell them apart — `live_view` in the payload is
MM's own flag, and under one of the two mechanisms that flag is the thing that
is lying. Hence asking you to look at a screen. (`CMMCore.isSequenceRunning()`
would answer it in software, and is what the fix will use; microclaw does not
call it yet, which is why this probe still needs your eyes.)

**Put the laser on and get signal in the field first.** The 2026-08-04 run of
this probe was done dark, and a dark stream looks the same as a frozen dark
frame — which is why the screen observation is missing from that evidence. You
need to be able to *see* the difference.

1. **The two calls must land in one batch.** That is the whole probe: in the
   incident, `start_live_view` and `snap_and_analyze` were tool calls inside a
   single assistant turn, microseconds apart. Splitting them across two of your
   turns puts seconds between them and the failure does not reproduce — which is
   exactly what happened on 2026-08-04. So ask for both in **one** instruction
   and do not answer anything in between:

   > Start live view and then snap and analyze the current field, both in the
   > same step.

2. **Look at the MM viewer**, then ask microclaw what it thinks:

   > Is live view running right now? Read it, do not infer it.

If `start_live_view` returns an **error** rather than "Live view started",
record that and carry on — it means MM failed the start outright and reverted
its own flag, which is mechanism (1) below caught a step earlier than expected.
Save it to `$Evidence\g5-start-error.txt`.

Record three things in `$Evidence\g5-live-restore.txt`:

- **What the viewer is doing** — streaming, or frozen on a still image.
- **What microclaw reported** for live mode (true/false).
- **Whether Micro-Manager showed an error dialog** at any point, and its text.
  This is the corroborating tell and it is easy to miss or dismiss.

How to read it:

| viewer | microclaw says | means | fix |
| --- | --- | --- | --- |
| frozen | **false** | the sequence-start threw; MM reverted the flag and told nobody | verify the restore and report it honestly |
| frozen | **true** | MM skipped the start and left the flag set — *the flag is lying* | verifying the flag would not have caught it; needs a real liveness check |
| streaming | true | it restored correctly this time — say so, and note what was different | not reproducible from software; needs more runs |

The middle row is the one to watch for. If the flag reads true over a frozen
viewer, then "poll until `is_live_mode_on()`" — the obvious fix, and the one
already shipped for `start_live_view` — would report success and still leave you
looking at a dead window. That is the design/18 lesson repeating: a window
existing is not pixels painting.

If you can spare it, run steps 1–3 twice. A restore that works once and fails
once is itself the answer to the third row.

## Returning evidence

Zip the whole `$Evidence` directory and return it, plus the session history
JSONL from any microclaw session above. Note anything that surprised you, even
where the gate passed.
