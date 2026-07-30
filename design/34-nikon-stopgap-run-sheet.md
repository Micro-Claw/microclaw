# Nikon Ti stopgap: operator run sheet

Use the **tag** `nikon-shipment-1` for this shipment:

```powershell
git fetch --tags
git checkout nikon-shipment-1
```

`nikon-shipment-1` is a tag, **not a branch.** On GitHub it appears under
*Releases / Tags*, not in the branch dropdown — if you look for it in the branch
list you will not find it, and nothing is wrong.

**Expect a "detached HEAD" message from that checkout.** It looks alarming and
mentions discarding commits and undoing the operation. It is normal and expected
here: it simply means you are viewing an exact fixed version rather than a moving
branch, which is precisely what we want. You are not going to commit anything, so
none of that advice applies to you. Confirm it worked with:

```powershell
git describe --tags
```

That must print exactly `nikon-shipment-1`. If it does, the checkout succeeded
regardless of what the longer message said.

Do not use the old July 16 commit `722184a`: the safety-config format has become
stricter since then, and these files target the newer schema. Do not use `main`
either — it moves while your tests are in flight, and pinning keeps every message
you send back interpretable.

The tag is used instead of a bare commit hash on purpose. This run sheet, the
worksheet, and the probe kit all live in the same repository, so any hash written
into this file necessarily names a commit older than the file itself. The tag is
applied after all of them landed, so it is the one name that resolves to a
checkout containing everything you were sent.

Confidence about the upcoming block 2 check is high: its checklist scope checks
laser enables reported by EMU against `illumination.shutters`. This rig reported
no EMU laser map, so that particular cross-check should find no candidate here.
That is not a promise that future startup checks cannot uncover another issue.

## Fill the worksheet

1. Make a working copy of `design/34-nikon-stopgap-worksheet.yaml` named
   `safety_config.yaml`, then open that copy in a plain-text editor. This
   worksheet is the config; there is no second draft/template to transfer into.
2. Fill every blank after a colon. Measure or deliberately decide each value for
   the microscope as it is configured today. Do not copy the observed Z,
   TIPFSOffset, exposure, ROI, or binning values from the old session: they show
   where the rig happened to be, not where it is safe to go.
3. Pay special attention to `stage.z_max`. It is the hard collision-safe ceiling
   for the installed objective and sample holder. Confirm it locally at the rig.
4. Keep units as written: distances are micrometres, exposure and illumination
   are milliseconds, duration is seconds, and bytes are uncompressed raw bytes.
5. Send the completed `safety_config.yaml` back for review before trying to
   start Microclaw. Do not change `reviewed: false` yet.

## Review and run the completed config

The unfilled worksheet intentionally fails the strict parser on its blank stage
and named-stage bounds. After every answer in your `safety_config.yaml` has been
reviewed:

- Search the entire file for a colon followed only by a comment. There must be
  no unanswered value.
- Read the complete file. Only you, the rig operator, may then change
  `reviewed: false` to `reviewed: true`.

### Step 1 — tell Microclaw where the file is

Keep your completed `safety_config.yaml` somewhere you can name, for example
`C:\microclaw\safety_config.yaml`, and pass that path explicitly. (Microclaw also
has a per-user default location, which `microclaw init` creates and reports, but
you do **not** need `init` for this shipment — you already have the file.)

**`--safety-config` must come BEFORE the subcommand.** This is the single easiest
thing to get wrong:

```powershell
microclaw --safety-config C:\microclaw\safety_config.yaml authorization-map
```

Putting it after the subcommand — `microclaw authorization-map --safety-config ...`
— fails with an "unrecognized arguments" error. If you see that message, it is
almost always this, not a problem with your file.

### Step 2 — check the config without running a session

Run this first. It connects **read-only**, prints what the config permits, and
exits. It moves no hardware and takes no images:

```powershell
microclaw --safety-config C:\microclaw\safety_config.yaml authorization-map > nikon-authmap.txt 2>&1
```

What "it worked" looks like: the command exits without a refusal and
`nikon-authmap.txt` lists the devices and permitted operations. You should expect
to see your stage and named-stage ranges, and to see **no** entry permitting
`TIPFSStatus`. That absence is correct and intended — see the PFS section below.

Send `nikon-authmap.txt` back whether it succeeds or fails. It is the single most
useful file you can return.

### Step 3 — start a session

Only after step 2 succeeds:

```powershell
microclaw --safety-config C:\microclaw\safety_config.yaml serve > nikon-serve.txt 2>&1
```

This serves the interactive web GUI on localhost and prints the address to open.
Stop it with Ctrl+C when you are finished.

Important limitation: the offline YAML parser alone does **not** reject blank
`camera.max_exposure_ms` or the nine blank `acquisition` fields. Normal
guaranteed-mode live startup does reject them by name, but if live authorization
were bypassed, null exposure/acquisition limits would not be enforced. Therefore
all ten fields must be visibly filled before review; “the file parsed” is not
evidence that they were supplied.

In PowerShell, record the exact version before running, from inside the repository
you checked out:

```powershell
git rev-parse HEAD > nikon-microclaw-version.txt 2>&1
git describe --tags >> nikon-microclaw-version.txt 2>&1
```

`nikon-microclaw-version.txt` should name the tag `nikon-shipment-1`. Send this
file back with every other result — we interpret your evidence against it. If
`git describe` reports anything other than `nikon-shipment-1`, stop and send the
file back before running anything else.

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
