# Block 59a demo gate

Run this on the Windows demo machine with `MMConfig_demo.cfg` loaded and the
Micro-Manager ZMQ server listening on port 4827. The program discovers the
inventory; do not rename or assume devices before running it.

From a checkout of `design59/optical-path`, first pin the implementation. This
accepts later documentation commits while refusing a checkout that predates the
implementation:

```powershell
git merge-base --is-ancestor e9962e6 HEAD
if ($LASTEXITCODE -ne 0) { throw "Block 59a implementation is not an ancestor of HEAD" }
```

Close Microclaw, leave Micro-Manager and its ZMQ server running, then run:

```powershell
uv run python design/59-block59a-demo-gate.py
if ($LASTEXITCODE -ne 0) { throw "Block 59a demo gate did not pass every limb" }
```

The gate does not install or replace the active safety document. It discovers
two suitable StateDevices and their real labels, writes a generated safety file
only inside `block59a-demo-evidence`, and verifies the production document's
bytes and modification time did not change. One limb temporarily moves a
discovered objective dependency to a non-matching discrete position and always
restores its entry label.

Return the entire `block59a-demo-evidence` directory, including
`discovery.json`, `generated-safety-config.yaml`, `gate.txt`, both numbered
system-state payloads, the non-matching payload, and `results.json`. `gate.txt`
is owned by the program, not `Start-Transcript`. A NOT EXERCISED result is a
failed gate: report its reason and return the existing evidence directory; do
not rename devices, edit the generated safety file, or retry. If the program
dies before producing every artifact, return the directory it did create plus
the complete console error and stop rather than rerunning.

If every limb reports NOT EXERCISED with `Couldn't create Core`, the gate never
reached Micro-Manager: check that the ZMQ server is enabled on port 4827 in
Micro-Manager's options, then run the command again. That is the one retry this
runbook asks for; `setup-error.txt` in the evidence directory carries the full
traceback either way.
