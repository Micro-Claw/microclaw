# Block 43m rig gate — an EMU Windows rig

The whole acceptance criterion is **one green full-suite run on an EMU Windows
rig**. There is no session to drive and nothing to observe on the microscope:
these two defects were invisible on macOS and on every rig that never ran the
suite, which is how they survived two merges.

Run it on **M2 or M5** — either works, both are Windows with an EMU
configuration, and the EMU half of the block is only exercised where EMU is
actually installed. The demo machine adds nothing here.

## Step 0 — pin and run

```powershell
cd C:\Users\ries\microclaw
git fetch origin
git checkout design43/windows-suite-integrity
git pull
git merge-base --is-ancestor f49deb5 HEAD
if ($LASTEXITCODE -eq 0) { "PIN OK" } else { "PIN FAILED - stop" }

pip install -e . > install-43m.txt 2>&1
python -m pytest -q > suite-43m.txt 2>&1
Get-Content suite-43m.txt -Tail 3
```

## G1 — the suite is green

- [ ] **0 failed.** Record the full last line.
- [ ] **1766 collected.** macOS measures 1766; a different total means something
      other than this block changed and is worth reporting either way. Judge by
      failures and collected total, never the passed count — Windows skips more
      than macOS for the same collection.

The known-bad for this criterion is `Micro-Claw/43a-m2/suite-43a.txt`: 9 failed /
1639 passed / 116 skipped / 1764 collected. "0 failed" fails there and passes on
a green macOS run, so it is validated in both directions.

## G2 — the two defect classes are closed where they actually bit

If G1 is green these are already true, and they are worth recording separately
because they are what the block is *for*:

```powershell
Select-String -Path suite-43m.txt -Pattern "test_session_script_export|test_channel_less_rig" | Select-Object -First 5
```

- [ ] No `FAILED` line mentions `test_session_script_export` — the eight
      encoding failures are gone on a Windows code page.
- [ ] No `FAILED` line mentions `test_channel_less_rig` — the unit test no longer
      reads this machine's Micro-Manager installation.

## If it is not green

Send `suite-43m.txt` and stop. Do not adjust anything on the rig to make it pass:
the point of this block is that the suite tells the truth on the hardware, so a
failure here is the gate working.

Two specific things worth pasting if they appear:

- a `FAILED` line naming `test_test_text_io_always_names_its_encoding` — that is
  the new guard firing, and it names the offending file and line, so the fix is
  mechanical.
- any failure that is *new* relative to `43a-m2/suite-43a.txt`. This branch
  touches only `tests/`, so a new failure in product code would mean the rig
  differs from M2 in a way nobody has recorded.

## Results

| gate | result | evidence |
|---|---|---|
| Step 0 pin | | |
| G1 0 failed | | |
| G1 collected total | | |
| G2 export tests | | |
| G2 channel test | | |

Send back this table and `suite-43m.txt`.
