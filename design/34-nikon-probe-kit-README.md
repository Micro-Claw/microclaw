# Nikon PFS probe kit — operator run sheet

Use this kit only with the agreed Nikon Ti configuration, objective, sample holder,
and oil-immersion setup. Keep Micro-Manager open with its pycromanager/ZMQ server
enabled. Open PowerShell in the folder containing `design`. Replace every value in
angle brackets with the reviewed value supplied for this session. Do not guess.

If a probe refuses to start, stop. Send the exact refusal text back, unedited. Do
not try to fix the script or continue with a later probe. A normal run worked when
it writes matching JSON and TXT files without `ABORTED`, `INCONCLUSIVE`,
`RESTORATION FAILED`, or `Z ENVELOPE BREACH`; send back both files plus the captured
console file.

Probe 0 has a third possible outcome: `never armed — inconclusive`. This means the
On command did not produce an armed first read. Send the files back, re-run that
case when instructed, and do not interpret it as either real verdict.

## Safety and order

1. Run Probe E first (about 5 seconds). It is read-only.
2. Manually place TIZDrive at the confirmed out-of-range position and run Probe 0
   out-of-range (about 35 seconds). Then manually place it at the confirmed lockable
   position and run Probe 0 in-range (about 35 seconds). Probe 0 changes autofocus
   state. Arming PFS can cause servo-driven TIZDrive motion even though the script
   commands no Z move.
3. Send both Probe 0 JSON/TXT pairs and console captures back for review.
4. Run Probe S only with PFS already locked and only with the reviewed targets and
   bounds (typically under 1 minute). It commands the PFS offset servo and can move
   TIZDrive. It intentionally leaves the final locked PFS/offset state in place.

**Do not run probes 1–4 until both Probe 0 outputs have been sent back and reviewed
here, and you receive explicit permission.** They mutate autofocus state and the
absolute, relative, and panel cases can move TIZDrive. Dry-run them first.

Probe U is not in this shipment. A bridge-only script cannot prove Studio's internal
Stage Control call path, and an instrumented UI action cannot yet be made sufficiently
self-validating for an unattended first round trip.

## Commands

The `--config-path` must name the exact `.cfg` currently loaded in Micro-Manager.
The scripts hash it; Micro-Manager does not expose a portable authoritative loaded
configuration path through this bridge.

```powershell
python design\34-probeE-nikon-environment.py --output-dir evidence --rig-id <RIG_ID> --config-path "C:\<PATH>\<CONFIG>.cfg" > probeE-console.txt 2>&1

python design\34-probe0-pfs-null-control.py --output-dir evidence --rig-id <RIG_ID> --config-path "C:\<PATH>\<CONFIG>.cfg" --case out-of-range --out-of-range-z <OUT_Z> --in-range-z <IN_Z> --position-tolerance <TOLERANCE_UM> > probe0-out-console.txt 2>&1

python design\34-probe0-pfs-null-control.py --output-dir evidence --rig-id <RIG_ID> --config-path "C:\<PATH>\<CONFIG>.cfg" --case in-range --out-of-range-z <OUT_Z> --in-range-z <IN_Z> --position-tolerance <TOLERANCE_UM> > probe0-in-console.txt 2>&1

python design\34-probeS-pfs-offset-settling.py --output-dir evidence --rig-id <RIG_ID> --config-path "C:\<PATH>\<CONFIG>.cfg" --targets <TARGET1> <TARGET2> <TARGET3> --max-offset-delta <MAX_DELTA> --total-excursion-budget <TOTAL_BUDGET> --z-minimum <MIN_Z> --z-maximum <MAX_Z> --settle-tolerance <OFFSET_TOLERANCE> --settle-timeout <SECONDS> > probeS-console.txt 2>&1

python design\34-probes1-4-pfs-motion.py --output-dir evidence --rig-id <RIG_ID> --config-path "C:\<PATH>\<CONFIG>.cfg" --case absolute --z-ceiling <RUN_CEILING> --confirmed-z-ceiling <CONFIRMED_CEILING> --z-minimum <MIN_Z> --step <SIGNED_STEP> --max-step <MAX_STEP> --travel-budget <BUDGET> --outcome-tolerance <TOLERANCE_UM> --dry-run > probe1-dry-console.txt 2>&1
```

After permission, repeat the last command without `--dry-run`, first for `absolute`,
then `relative`, then `panel`, then `api-vs-property`, capturing each to a differently
named console file. Expected runtime is about 10–25 seconds per case. For `panel`,
the script prints a timed instruction for exactly one Stage Control move; follow it,
but do not judge the result—the script classifies the measured outcome. This is the
one case where the script cannot stop the GUI-driven movement. It prints the absolute
ceiling; stop manually before approaching it. The script still aborts its observation
as soon as any measured Z breaches the envelope. The
`api-vs-property` case commands no Z move, although arming PFS may servo-drive Z.

## What to send back

Send the entire `evidence` folder and every `*-console.txt`. Do not rename or edit
anything. Each successful invocation creates one matching JSON/TXT pair. The JSON
contains measurements and identity; the TXT is a short operator record. Probe 0's
two case verdicts may differ; that is expected, and neither case is interpreted alone.

## File manifest (SHA-256)

Verify with `Get-FileHash design\<filename> -Algorithm SHA256`. A mismatch or missing
file means the copy is incomplete; do not run it. The README cannot contain its own
stable digest (editing a file changes its digest), so its shipping digest must be
provided alongside the bundle. The executable kit hashes are:

<!-- HASHES_START -->
- `nikon_kit_common.py`: `4e40115d46da4c6a6ac38fa5292ccffc8687300fec4434ad335d6bb0982b3149`
- `34-probe0-pfs-null-control.py`: `f0778f5f604eb8eff4e081c1d6395a158fc8ef1270d7b4ccf7965af2021ca391`
- `34-probeS-pfs-offset-settling.py`: `4254fac086a09c2ecc65278f7bad0fec0a87414773861230e2b3abe5586155bc`
- `34-probeE-nikon-environment.py`: `5361acd558ba054f45cc3c7897ccf30417dbe8f67c76551bf385eec6aa4630d4`
- `34-probes1-4-pfs-motion.py`: `826ffbd81ebda195545d420083f6dd15d90e022859924c9d23e74ec41d008127`
<!-- HASHES_END -->
