# Block 58a demo gate

**Run the script. Do not paste commands.**

```powershell
git checkout design58/discovery
powershell -NoProfile -ExecutionPolicy Bypass -File design\58-block58a-demo-gate.ps1
```

Windows demo machine. **Micro-Manager is not needed** — nothing in this block
touches hardware. The script prints an evidence directory under `Documents\`
when it finishes; send that whole folder back.

## Why this is a script and not a list of steps

Round 1 shipped as seven copy-paste PowerShell blocks and **failed four limbs
while printing `BLOCK 58a DEMO GATE PASSED`.** Pasted interactively, a `throw`
ends the current pipeline, not the session, so every later block ran anyway. It
also called bare `python` where this project uses `uv run`, and captured no
artifact — the evidence that reached the coordinator was console scrollback.

The rule that came out of it: **if every step of a gate is a literal command, it
is a program and it ships as one.** A runbook is for steps a human performs and
judges. These only compute.

`58-block58a-demo-gate.py` runs every limb, reports each independently, writes
`results.json` and a transcript, and exits nonzero if any failed. It reports
each limb rather than aborting at the first failure because round 1's cascade
hid five untested limbs behind one `TypeError`.

## Do not repoint the demo machine's remote

Its clone still names **`zacsimile/microclaw`**, from before the 2026-08-26
transfer. That is not a misconfiguration — GitHub redirects Git traffic, so
every pre-transfer GitHub Desktop clone is in exactly that state, and it is the
only fixture available for requirement 3 ("a rename or an organization transfer
must not silently cut it off"). Round 1's real defect was that
`clone_provenance` refused it.

Limb 1b tests this directly. If the remote has been repointed to
`Micro-Claw/microclaw` it reports **NOT EXERCISED** rather than passing, so the
requirement cannot be made green by editing the fixture.

## What each limb settles

| Limb | Settles |
|---|---|
| 1 | Provenance records a real clone and no longer refuses a redirected name |
| 1b | **Requirement 3** — a pre-transfer remote still discovers `origin/main` |
| 2 | `check_for_update` finds `origin/main` from an ancestor install and caches it atomically |
| 3 | The block branch's own HEAD is refused *specifically* as `diverged`, not silently |
| 4 | A dirty checkout is byte-identical afterwards — status, HEAD, branch, contents |
| 5 | Staging materializes exactly the discovered candidate and its marker matches |
| 6 | The private repo's public 404 is cached without clobbering a prior success |
| 7 | `--no-update-check` and `MICROCLAW_UPDATE_CHECK=0` perform no check at all |
| 8 | The gate left the checkout as it found it |

Implementation pinned by ancestry, not an exact tip, so amending this file
cannot invalidate it.
