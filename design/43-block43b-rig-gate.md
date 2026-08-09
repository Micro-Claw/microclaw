# Block 43b rig gate — M5

This gate checks that Micro-Manager's Property Browser follows property writes
made by microclaw. Everything below is PowerShell. Where a step says “record”,
paste the value into the results table; a step with no recorded value has not
been run.

## Step 0 — pin the implementation

```powershell
cd C:\Users\ries\microclaw
git fetch origin
git checkout design43/refresh-gui
git pull
git merge-base --is-ancestor 74dc87f HEAD
if ($LASTEXITCODE -eq 0) { "PIN OK - the gated implementation is in this checkout" }
else { "PIN FAILED - stop, you are not testing the right code" }
```

`74dc87f` is the gated implementation, pinned by the coordinator at push time.
The ancestor check is intentional: an amended runbook or later review commit
must not invalidate the pin. `$LASTEXITCODE`, not `%ERRORLEVEL%`, verifies the
command in PowerShell; `%ERRORLEVEL%` only prints its own name there.

Off-rig at `74dc87f` on macOS, re-measured by the coordinator: **1673 passed,
99 skipped, 3 expected warnings, 1772 collected, 0 failures.** The baseline this
branch started from (`04c0654`) was 1667 / 99 / 1766, so the six added IDs are
this block's own tests and nothing was lost. Compare against these numbers
below, remembering that Windows legitimately skips more for the same collection.

Reinstall this checkout, then run the entire suite:

```powershell
pip install -e . > install-43b.txt 2>&1
python -m pytest -q > suite-43b.txt 2>&1
$suiteExit = $LASTEXITCODE
Get-Content suite-43b.txt -Tail 8
"pytest exit code: " + $suiteExit
python -m pytest -q --collect-only > collect-43b.txt 2>&1
"collection exit code: " + $LASTEXITCODE
Get-Content collect-43b.txt -Tail 3
```

Record the pytest exit code, failures, collected total, passes, and skips. A
zero exit code is not enough: compare failures and the collected total with the
off-rig report for this commit, and compare the skip count with the previous
run on this same machine. Windows may legitimately skip more than macOS, but a
suite can become green because its subject tests started skipping. Any new
failure, lost test ID, changed collected total, or unexplained skip increase:
stop and send `suite-43b.txt` and `collect-43b.txt`.

## G1 — prove the deployed bridge shadow exists

Start Micro-Manager and its ZMQ server, then run:

```powershell
python -c "from microclaw.controller import MicroscopeController; c=MicroscopeController(); print('refresh_gui_from_cache callable:', callable(getattr(c.studio.app(), 'refresh_gui_from_cache', None)))"
"bridge probe exit code: " + $LASTEXITCODE
```

Record both printed lines. PASS requires `refresh_gui_from_cache callable:
True` and exit code 0. This is the live Windows/pyjavaz fact that inspecting the
Java interface off-rig could not establish.

## G2 — a channel switch updates the Property Browser

1. Open Micro-Manager's Property Browser and expose the four iChrome laser
   enable properties used by the M5 channel map.
2. In microclaw, call `set_channel` for a channel different from the current
   one (for example, switch from `488` to `640`) and approve the illumination
   action when asked.
3. Without clicking Refresh in Micro-Manager, compare all four displayed
   enable values with direct `get_device_property` reads of those same
   device/property pairs.

PASS requires one channel tool call to update all four displayed values, with
the Property Browser and direct reads agreeing. Record the requested channel,
the four property names, the four displayed values, and the four read-back
values. Do not repeatedly switch channels merely to test repainting; each
switch is a real illumination-state operation.

## G3 — zero-dose focus-lock and raw-property checks

Keep the Property Browser open on the PIZStage focus-lock property. Call
`set_focus_lock` once to toggle it, without clicking Refresh in Micro-Manager,
and compare the displayed value with `get_focus_lock_state`. Restore the
original lock state when finished. PASS requires every toggle to repaint the
Property Browser and agree with the read-back; this zero-dose check may be
repeated if the first observation is unclear.

As a one-line non-regression check, use `set_device_property` to write that same
non-illumination focus-lock property to the value it already holds; without a
manual refresh, record that the Property Browser still agrees with
`get_device_property`. This callsite refreshed before the migration and must
continue to do so.

## G4 — rollback repaint (best-effort on M5)

A rollback needs a channel plan to fail after at least one write succeeds. M5
has no safe, deterministic control for that condition: unplugging the iChrome,
editing its live EMU configuration, or racing a serial disconnect would create
an uncontrolled hardware/configuration fault and might fail on the first write,
which is not a partial application. Do not manufacture such a fault for this
gate.

If an ordinary channel switch naturally fails partway during G2:

1. Do not click Refresh in Micro-Manager.
2. Record the exact exception class and message from microclaw.
3. Compare every attempted property's Property Browser value with a direct
   `get_device_property` read.

PASS requires the rollback exception to remain the reported exception and the
Property Browser to show the values the hardware holds after rollback. If no
natural partial failure occurs, record **NOT EXERCISED — no natural partial
channel-plan failure**. This limb is best-effort on the rig; deterministic unit
tests cover all three rollback exception exits and a repaint failure that must
not replace them.

## Results

| gate | result | evidence |
|---|---|---|
| Step 0 pin | | |
| Step 0 full suite exit/failures/collected | | |
| Step 0 passes/skips; previous M5 skips | | |
| G1 bridge shadow + exit code | | |
| G2 channel/property-browser/read-back values | | |
| G3 focus-lock toggle/read-back | | |
| G3 `set_device_property` non-regression | | |
| G4 rollback repaint | | |

Send back this completed table, `suite-43b.txt`, and `collect-43b.txt`.
