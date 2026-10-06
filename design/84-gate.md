# Gate 84, round 2 — the same session on block 84c

**Machine: the llama.cpp workstation** (`IIC-LLS-Wkstn`), with Micro-Manager's
stock demo configuration — no microscope. **Windows PowerShell 5.1** and
**Chrome**. Microclaw and the model run on the same machine, so the timings are
a local deployment's.

What this checks: every model call goes to the local server (84a), the
conversation budget comes from the server and compaction happens before the
server's context fills (84b), the server's prompt cache is reused between
rounds, a thumbnail does not count as text, and the browser works with a
keyless or keyed local server. The scorer reads the session's own history and
usage files; steps 6 and 7 are the parts only you can judge.

**Round 2 (block 84c).** Round 1 (2026-10-05) passed its behaviour but paid
three slow rebuilds in a row — 134 s, 133 s and 99 s — because each compaction
was committed while the history was still above the threshold. 84c commits a
compaction only when it gets under the threshold, and says what it did. This is
the **same session, same server settings**, so the two can be compared. A
replay of round 1's history through 84c predicts one rebuild where round 1 had
three. Your machine already has the clone, the environment and the demo bounds;
steps 1–3 below are written to be safe to re-run.

About **30–45 minutes**, most of it waiting for the model. Nothing here costs
money or exposes a sample.

## 0 — the model server

In its own window, leave this running for the whole gate (the command you
already use, unchanged):

```powershell
llama-server.exe -m Qwen3.8-27B-UD-Q4_K_XL.gguf --host 0.0.0.0 --port 8080 --jinja -c 131072 -np 1 -fa on --cache-type-k q8_0 --cache-type-v q8_0 --api-key testkey
```

Check it from a second PowerShell window:

```powershell
Invoke-RestMethod http://127.0.0.1:8080/health
```

Expect `status` `ok`.

## 1 — get the branch

```powershell
if (-not (Test-Path C:\code\microclaw)) { git clone https://github.com/iandobbie/microclaw.git C:\code\microclaw }
cd C:\code\microclaw
git fetch origin
git checkout design84/84c-compaction
git pull
git merge-base --is-ancestor 4c99b81 HEAD
if ($LASTEXITCODE -eq 0) { "PIN OK - the implementation is in this tree" } else { "STOP - wrong tree, do not run the gate" }
```

Expect `PIN OK`.

If this clone existed before `988abea`, its recorded fixtures were checked out
with Windows line endings and every replay fails; `git pull` does not rewrite
them. Refresh them once (harmless on a fresh clone):

```powershell
Remove-Item -Recurse -Force tests\fixtures\llamacpp
git checkout -- tests/fixtures/llamacpp
```

## 2 — the environment

This is the README's source install. It needs the network once.

```powershell
cd C:\code\microclaw
uv venv --python 3.12
uv pip install --python .venv\Scripts\python.exe -e ".[serve,test]"
.venv\Scripts\python.exe -c "import microclaw, sys; print(microclaw.__file__)"
```

The last line must print a path under `C:\code\microclaw\microclaw\`. A
`SyntaxWarning` about `'\P'` in `mmpycorex\install.py` is harmless.

Check the scorer before spending an hour on a session:

```powershell
.venv\Scripts\python.exe -m pytest -q design\84-gate-selftest.py
```

Expect `13 passed`. Each graded limb is run there on a real session built from
recordings of this server, and on a mutant that breaks exactly that limb. If it
fails, stop and send the output.

## 3 — safety bounds

There is no microscope, so copy the reviewed demo bounds rather than run setup.
Round 1 copied them already; this copies only if none is there and never
overwrites one:

```powershell
New-Item -ItemType Directory -Force "$env:APPDATA\microclaw" | Out-Null
if (Test-Path "$env:APPDATA\microclaw\safety_config.yaml") { "safety_config.yaml present - leaving it" } else { Copy-Item tests\fixtures\50a-demo-safety_config.yaml "$env:APPDATA\microclaw\safety_config.yaml" }
```

## 4 — Micro-Manager

Start Micro-Manager with the stock **`MMConfig_demo.cfg`**, then tick
**Tools → Options → Run pycro-manager server on port 4827**. Check:

```powershell
.venv\Scripts\python.exe -m microclaw check-bridge
```

Expect it to report the bridge answering and exit without an error.

## 5 — start Microclaw on the local model

Leave this window open and **unredirected** for the whole session: `serve`
prints nothing under redirection until it exits.

```powershell
cd C:\code\microclaw
$env:MICROCLAW_LLM_BASE_URL = 'http://127.0.0.1:8080'
.venv\Scripts\python.exe -m microclaw serve
```

No key is set yet, and this server requires one, so expect
`[microclaw] Local context budget: n_ctx=32768 (default; …), …, high=4096,
low=3072 (provisional 4096-token history floor); probe failures: /props HTTP
401, …` and `No local LLM API key set; none is needed unless the server was
started with --api-key.` That is correct: the budget is re-read when you enter
the key in step 6. A browser opens at `http://127.0.0.1:8000`; if it
is not Chrome, copy that address into Chrome. Close the other browser's tab.

`$env:` here sets the variable for this window only; nothing is written to the
machine's settings.

## 6 — drive the session

**Before entering any key**, note: **(O1)** is the **Send** button enabled, and
is there no "No … key" banner? (A key chip labelled `key` should be visible.)

**Turn 0** — send this with no key set. The server requires `testkey`, so this
turn must fail:

> Call get_system_state once and tell me the stage position.

**(O2)** Copy the error text it shows. It should say the local server rejected
its API key and name `MICROCLAW_LLM_API_KEY`.

Now click the **key** chip, enter `testkey`, leave **remember** unticked, and
save. **(O3)** What did the confirmation toast say? The `serve` window should
now print `[microclaw] Local context budget: n_ctx=131072, fixed=33864,
high=84920, low=63690` — **(O3b)** copy that line.

**Turn 1:**

> Call get_system_state once and tell me the stage XY and Z position and the current channel.

**Turn 2:**

> Snap one image with snap_and_analyze with return_thumbnail set to true, and tell me its mean intensity, snr and whether focus_metric_valid is true.

**Turn 3:**

> Run a 200-frame timelapse on the demo camera at 5 ms exposure with the snr_observer hook, saving into a folder called block84-frames, and tell me the hook log path when it finishes.

**Turns 4 onward** — send this, unchanged, until the meter below shows
`compactions 1` or more, then send it **once more**:

> Read the hook log from the timelapse you just ran and tell me whether the SNR drifted over the run.

The meter: in the second PowerShell window, after each reply,

```powershell
cd C:\code\microclaw
.venv\Scripts\python.exe design\84-gate.py --session . --progress
```

Each read adds roughly 19,000 tokens of history; compaction should come within
about five reads. **(O4)** Two kinds of toast can now appear; note each one's
text as it appears:
- **"Conversation history is above the compaction threshold; recent turns cannot
  be folded yet, so this request is being sent as is."** — expected once, around
  the large hook-log read, and the replies after it should **not** be slow.
- **"Compacted context: A → B estimated tokens; rebuilding N tokens, about S
  s."** — expected once. Roughly how many seconds was it from **Send** to the
  first text on that turn, against the S it predicted?

Round 1 had three slow turns in a row; say how many slow turns this round had.

**Last turn:**

> Before we finish, tell me the illumination state.

**(O5)** Did it call `get_system_state` for this (visible in the transcript),
and does its answer say only what that tool returned — including `unknown`
where the tool said unknown?

Then press **Ctrl+C** in the `serve` window.

## 7 — score it

The server must still be running: the budget limb reads the server's `n_ctx`
and asks the product's own `make_conversation_store` what this server's budget
is, with the key, because the server answers nothing but `/health` without it.

```powershell
cd C:\code\microclaw
New-Item -ItemType Directory -Force block84c-evidence | Out-Null
.venv\Scripts\python.exe design\84-gate.py --session . --base-url http://127.0.0.1:8080 --api-key testkey > block84c-evidence\gate.log 2>&1
Get-Content block84c-evidence\gate.log
Copy-Item (Get-ChildItem *_microclaw_history.jsonl | Sort-Object LastWriteTime | Select-Object -Last 1) block84c-evidence\
Copy-Item (Get-ChildItem *_microclaw_usage.jsonl | Sort-Object LastWriteTime | Select-Object -Last 1) block84c-evidence\
Copy-Item (Get-ChildItem *_microclaw_confirmations.jsonl | Sort-Object LastWriteTime | Select-Object -Last 1) block84c-evidence\ -ErrorAction SilentlyContinue
```

The last line of `gate.log` reads `<k>/<n> graded limbs passed`. **NOT
EXERCISED is never a pass.** REPORT lines are measurements and are not graded.

## 8 — send

Send the `block84c-evidence` folder, your answers to **O1–O5**, and a copy-paste
of the `serve` window's output (select all in that window). The datasets in
`block84-frames` can stay where they are.

Afterwards, to remove the demo bounds: `Remove-Item "$env:APPDATA\microclaw\safety_config.yaml"`.
