# M5 rig gate — EMU names (design/39)

Branch: `design-39-emu-names`.
Sample: **none needed.** No gate below requires a specimen.

**Dose: zero.** Nothing here exposes. This block only changes how the EMU
config is *read*. G2 and G3 do **move hardware** — the BFP flipper and the
filter wheels — so before you start, note where they are:

```
Position of Two-state device 3 (Thorlabs ELL6) : ______
Filter wheel 1 slot / Filter wheel 2 slot      : ______ / ______
```

Put them back at the end. Nothing else touches the rig.

Budget about 25 minutes. Most of it is conversation, not commands.

## G0 — pin and sanity

```
git fetch origin
git checkout design-39-emu-names
git pull
git merge-base --is-ancestor a7ac7d5 HEAD
if ($LASTEXITCODE -eq 0) { "PIN OK" } else { "PIN FAILED - STOP" }
```

Then:

```
pip install -e . > g0_install.txt 2>&1
python -m pytest -p no:cacheprovider -q > g0_tests.txt 2>&1
```

Expected: `PIN OK`, suite green (1491 passed, 99 skipped). Send both files.

## G1 — the regression this block exists for

On 2026-08-05 the agent asserted the 640 nm laser was at slot 1. It is at slot
3. You corrected it twice. This gate is whether that still happens.

Start a **fresh** session — the point is what it concludes unaided — and ask:

> Which EMU slot is the 640 nm laser on, and what is its trigger line?

Expected:

- **Slot 3**, trigger `Mode3`, first time, with no correction from you.
- It should cite the *configured name* as its source, not the device property
  string. `iChrome-MLE-TCP-Laser 1: 3. Level %` is slot 3's line — the iChrome's
  internal numbering runs opposite to the EMU slots, and reading a wavelength
  out of that string is the original bug.

**This gate is about what the agent says, not what a field contains.** If it
reaches slot 3 but explains it by reasoning about device ordering, that is a
fail even though the answer is right — it got there by the route that produced
the wrong answer last time. Send the transcript either way.

Then, still without correcting anything:

> What are slots 0, 1 and 2?

Expected: 405, 488, 561. Any slot reported without a name is a fail — the names
are all in the config.

## G2 — "change the BFP"

The whole point of naming the two-state devices. Ask:

> Put the BFP in.

Expected:

- It identifies **BFP** as `Two-state device 3` → `Thorlabs ELL6` / `State`,
  and moves it, **without asking you which numbered device the BFP is.**
- It asks for confirmation before moving, as usual. Confirm it.
- Being asked "which two-state device is the BFP?" is the failure this gate
  looks for. The config answers that.

Also ask (no move needed):

> What do the other two-state devices control?

Expected: device 1 = **3D**, device 2 = **TIRF**, and devices 4–6 reported as
unnamed/unallocated — *not* invented, and not named "None".

## G3 — filter wheels, both of them

Ask:

> What filters are in each wheel?

Expected — both wheels, all six slots each, whitespace stripped:

| slot | wheel 1 | wheel 2 |
| --- | --- | --- |
| 0 | 525/45 | 525/50 |
| 1 | 600/60 | 600/52 |
| 2 | 676/37 | 676/37 |
| 3 | 685/70 | 685/70 |
| 4 | 452/45 | 457/15 |
| 5 | *empty* | *empty* |

Slot 5 must be reported as **empty**, not as a filter named "None".

Then the ambiguity check — `676/37` and `685/70` are in **both** wheels:

> Move to the 676/37 filter.

Expected: it **asks which wheel**, or reports the name as ambiguous. Silently
picking a wheel is a fail. If it asks, answer, let it move, then restore.

## G4 — htSMLM is detected

Ask:

> Is htSMLM installed?

Expected: `htsmlm_installed: true` **and** `htsmlm_configured: true`. Before
this block it reported `false` on this rig — the jar lives in
`<MM>\EMU\htsmlm-2.1.0.jar` and only `mmplugins`/`plugins` were searched.

If either is still false, send the whole tool result: the jar path and the
config's `pluginName` are what I need.

## G5 — a rig with no names invents none

**Run this against the demo config, not M5.** Load the demo configuration in
Micro-Manager, start a fresh microclaw session, and ask:

> What lasers and filters does this rig have?

Expected: it reports what is there and **does not produce a wavelength or a
filter name for anything.** A rig with no EMU `parameters` block must yield no
names at all. An invented "405" here is worse than the bug being fixed.

---

## What to send back

- `g0_install.txt`, `g0_tests.txt`
- The transcript for each of G1–G5 (the wording matters more than the fields)
- The BFP and filter-wheel positions restored, confirmed

If a gate fails, **stop and send it** rather than working around it. A first
failure is a data point, not a diagnosis — I would rather see the transcript
than a retry that hides what happened.
