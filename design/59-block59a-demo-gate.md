# Block 59a demo gate

Run this on the Windows demo machine with `MMConfig_demo.cfg` loaded and the
Micro-Manager ZMQ server listening on port 4827. The program discovers the
inventory; do not rename or assume devices before running it.

From a checkout of `design59/optical-path`, first pin the implementation. This
accepts later documentation commits while refusing a checkout that predates the
implementation:

```powershell
git merge-base --is-ancestor IMPLEMENTATION_COMMIT HEAD
if ($LASTEXITCODE -ne 0) { throw "Block 59a implementation is not an ancestor of HEAD" }
```

`IMPLEMENTATION_COMMIT` is replaced with the committed implementation SHA in
this runbook before the branch is handed to the operator; it is not a command
placeholder that ships to the rig.

Close Microclaw, leave Micro-Manager and its ZMQ server running, then run:

```powershell
uv run python design/59-block59a-demo-gate.py
if ($LASTEXITCODE -ne 0) { throw "Block 59a demo gate did not pass every limb" }
```

Return the whole `block59a-demo-evidence` directory. `gate.txt` is owned by the
program (not `Start-Transcript`), `system-state.json` is the payload scored, and
`results.json` contains every independent PASS / FAIL / NOT EXERCISED verdict
plus both bridge-call counts and wall times. NOT EXERCISED makes the program
exit nonzero. The final limb revalidates the safety document that was active
before the gate and checks its hash, so the gate does not leave production
pointing at its evidence safety file.
