# Nikon Ti stopgap: operator run sheet

Use Microclaw commit `b7175948701e2e3fbf265eba28981276ed85d194` for this
shipment. Do not use the old July 16 commit `722184a`: the safety-config format
has become stricter since then, and these files target the newer schema. Pinning
this exact commit keeps every returned message interpretable even if `main`
changes while the remote tests are in flight.

Confidence about the upcoming block 2 check is high: its checklist scope checks
laser enables reported by EMU against `illumination.shutters`. This rig reported
no EMU laser map, so that particular cross-check should find no candidate here.
That is not a promise that future startup checks cannot uncover another issue.

## Fill the worksheet

1. Open `design/34-nikon-stopgap-worksheet.yaml` in a plain-text editor.
2. Fill every blank after a colon. Measure or deliberately decide each value for
   the microscope as it is configured today. Do not copy the observed Z,
   TIPFSOffset, exposure, ROI, or binning values from the old session: they show
   where the rig happened to be, not where it is safe to go.
3. Pay special attention to `stage.z_max`. It is the hard collision-safe ceiling
   for the installed objective and sample holder. Confirm it locally at the rig.
4. Keep units as written: distances are micrometres, exposure and illumination
   are milliseconds, duration is seconds, and bytes are uncompressed raw bytes.
5. Send the completed worksheet back for review before trying to start
   Microclaw. Do not change `reviewed: false` in the worksheet.

## Make the running config

The worksheet itself is intentionally invalid YAML for Microclaw's strict
schema. After the completed worksheet has been reviewed, copy each answer into
the matching place in `design/34-nikon-stopgap-draft.yaml`:

- Replace every `{unbounded: true, reason: "Draft only: ..."}` with the finite
  number from the worksheet.
- Replace every `null` camera/acquisition value with the finite number from the
  worksheet.
- Read the complete resulting file. Only you, the rig operator, may change
  `reviewed: false` to `reviewed: true`.
- Save the result as `safety_config.yaml` at the location used by your launcher,
  or pass its full path with your usual `--safety-config` option.

In PowerShell, record the exact version before running:

```powershell
git rev-parse HEAD > nikon-microclaw-version.txt 2>&1
```

The file must contain exactly
`b7175948701e2e3fbf265eba28981276ed85d194`. If it does not, stop and send that
file back.

## If startup refuses

Do not edit around the refusal and do not paraphrase it. Copy the exact refusal
text, unedited, and send it back together with `nikon-microclaw-version.txt` and
the safety config you tried. These refusals are valuable real-rig evidence for
the next usability work.

To preserve console output in PowerShell, append this to the same Microclaw
command you normally run:

```powershell
> nikon-startup.txt 2>&1
```

Then send `nikon-startup.txt` back without opening and resaving it.

## PFS limitation in this stopgap

This config explicitly removes `TIPFSStatus.State`. Microclaw cannot turn Nikon
PFS on or off at all—not even disarm it—until the typed continuous-focus work in
blocks 7a-7b is complete. Use the Micro-Manager controls under the lab's normal
operating procedure when PFS state must change. `TIPFSOffset` motion remains
structurally available only inside the bounds you supply, but its known delayed
read-back means you must not treat the first reported achieved position as proof
that it has settled.
