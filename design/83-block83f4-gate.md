# 83f-4 catalog intake gate

Run after review, repository creation and seed push by the coordinator. Set the
reviewed full SHA in intake.yml's `MICROCLAW_COMMIT` first; rebuild reads that same pin. Main gets the seed
except `test-branch/`; test gets that seed plus `test-branch/`'s contents at its
root. Enable Actions, fork PR Actions, and squash merging. No production policy
is supplied by this block.

Use **uv's Python interpreter**, in PowerShell or a POSIX shell, from the
MicroClaw checkout. `gh` must be installed and logged in to the operator's
account. The reviewed fixtures must be on `block-83f-4` before the run so the
artifact URLs resolve.

```powershell
uv run python design/83-block83f4-gate.py selftest
uv run python design/83-block83f4-gate.py run --evidence 83f4-evidence
uv run python design/83-block83f4-gate.py verify --evidence 83f4-evidence
uv run python design/83-block83f4-gate.py cleanup --evidence 83f4-evidence
```

`run` creates or reuses your fork and opens eight PRs against `test`, each on its
own fork branch. It waits up to 15 minutes per case, records PR JSON and workflow
conclusions, fetches the served catalog, and performs an isolated client fetch.
A good release and its withdrawal merge; the other six stay open with field
comments. The hostile workflow PR must not post its marker. Missing mechanisms
score NOT EXERCISED. Every FAIL or NOT EXERCISED exits nonzero.

`verify` refreshes evidence and scores each limb independently. `cleanup` closes
still-open gate PRs and deletes only the recorded fork branches; merged test
history remains. Do not run a second gate into the same catalog without arranging
new fixture versions: duplicate versions intentionally refuse.

Send back the **whole evidence folder**, including gate.log, state.json, all PR
and run JSON, served policy/catalog, client snapshots and scores.json. The YAML
structural test is not evidence that Actions works; this live gate is.
