# Block 41c — rig gate runbook

Branch `design41/emu-channel-plan`. Read this on the rig, on this branch.

Block 41c gives a rig with **no Micro-Manager `Channel` config group** real
channels: they are the **named laser slots of its EMU configuration**, and
`set_channel` applies them through the same capture/authorize/replay executor a
preset uses. M5's EMU slot order runs opposite to the iChrome's own channel
numbers — slot 3 is `640` and its enable is `Laser 1: 1. Enable` — and that
off-by-one, hand-applied to laser enables, is what this block removes.

**Two things it deliberately does not do**, so you know what to expect:

- An EMU-sourced channel is **laser-only**. EMU carries no laser-to-filter
  association, none was invented, so **you still move the emission filter
  yourself** with `set_device_property`. Those filter writes in the history are
  expected and correct.
- A switch drives **every named laser slot**: the target to its declared
  `on_value`, every other named slot to its declared `off_value`, in that order
  (off first). It will therefore turn off a laser you had on. G3 is about
  putting that back.

Three criteria. **G1 and G3 are one M5 session; G2 is a short Demo session.**

## Before you start — confirm you are on the right code

```powershell
cd C:\path\to\microclaw
git merge-base --is-ancestor 9812956 HEAD
if ($?) { "PIN OK - implementation is present" } else { "PIN FAIL - stop, wrong branch" }
```

`$?` rather than `$LASTEXITCODE`: a cmdlet in between silently stales that
variable. Read the printed words.

```powershell
pip install -e .
python -c "import microclaw; print('LOADED FROM', microclaw.__file__)"
```

### Your safety config must declare the laser enables

A laser slot whose enable is **not** an `illumination.shutters` entry is not
offered as a channel — on purpose. Before the session, confirm M5's config has
all four:

```yaml
illumination:
  shutters:
    - device: 'iChrome-MLE-TCP'
      property: 'Laser 4: 1. Enable'   # EMU slot 0 = 405
      on_value: '1'
      off_value: '0'
    - device: 'iChrome-MLE-TCP'
      property: 'Laser 3: 1. Enable'   # EMU slot 1 = 488
      on_value: '1'
      off_value: '0'
    - device: 'iChrome-MLE-TCP'
      property: 'Laser 2: 1. Enable'   # EMU slot 2 = 561
      on_value: '1'
      off_value: '0'
    - device: 'iChrome-MLE-TCP'
      property: 'Laser 1: 1. Enable'   # EMU slot 3 = 640
      on_value: '1'
      off_value: '0'
```

and that `channels.allowed`, if you set it at all, lists the **configured
names** — `['405', '488', '561', '640']` — not slot numbers.

If startup refuses here, **that is a finding**: paste the refusal. It should
name the exact device/property to declare.

---

## Run G1 and G3 as ONE M5 session, in this order

Block 41b cost five rig rounds because each run halted one step before the next
defect. Build **one** session that exercises every path, with **both known
refusals last**. Do not reorder.

**Record first, before anything else.** Ask microclaw for
`get_emu_laser_map` and write down which slots read `enabled="1"` at session
start. This is G3's baseline and you cannot recover it later.

1. **`get_available_channels`.** Expect four channels named `405`, `488`, `561`,
   `640`, a `source` line saying they come from the EMU laser map, and a note
   that a switch moves laser enables only. **If any slot appears under
   `unavailable`, paste the reason** — that is the rig telling you something.
2. **`set_channel` to `640`.** You should get exactly **one** illumination
   confirmation, naming the enable being turned **on**. Approve it.
3. Move the emission filter to **685/70** yourself (`set_device_property` on
   `Thorlabs Filter Wheel`). Expected; not a defect.
4. Mark two or three positions and run a **small multi-position acquisition**.
   **Do not pass a `channel` argument** — set the channel first, then acquire.
5. **`set_channel` to `561`.** Again exactly one confirmation, on the enable.
6. Move the emission filter to **600/60**. Run the **second** acquisition.
7. Ask microclaw to **export this session as a standalone script** next to the
   data. Note the path.
8. **Restore the entry state**: `set_channel` back to whichever laser was on when
   you started (step 0). If none was on, ask microclaw to shutter declared
   illumination instead.
9. **Refusals, last.** In this order:
   - ask for an acquisition **passing `channel='640'`** — it must **refuse**,
     saying this rig cannot drive a channel axis and to call `set_channel` first;
   - ask for `set_channel('DAPI')` — it must **refuse**, naming the EMU laser map
     and listing the four real names.

   A refusal here is a **PASS**. Paste both messages.

---

## G1 — the switch went through the plan, not through raw enable writes

*Checklist item, as reworded 2026-08-06: **no raw `set_device_property` write to
a laser enable** in the history. The original "no raw `set_device_property` call"
cannot be met, because an EMU-sourced channel is laser-only by design and the
emission filter is still moved by hand. See the note at the end.*

Write the checker once:

```powershell
@'
import json, re, sys

LASER_ENABLE = re.compile(r"^Laser \d+: 1\. Enable$")   # M5 iChrome, EMU-mapped

calls = []
for line in open(sys.argv[1], encoding="utf-8"):
    line = line.strip()
    if not line:
        continue
    for block in json.loads(line).get("content") or []:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            calls.append((block.get("name"), block.get("input") or {}))

raw = [i for n, i in calls if n == "set_device_property"]
enables = [i for i in raw if LASER_ENABLE.match(str(i.get("property", "")))]
channels = [str(i.get("preset")) for n, i in calls if n == "set_channel"]

print("set_channel calls:      ", channels)
print("raw set_device_property:", raw)
print("raw writes to a laser enable:", enables)

failures = []
if enables:
    failures.append("raw write(s) to a laser enable: %r" % (enables,))
if len(set(channels)) < 2:
    failures.append("expected two distinct channels through set_channel, got %r" % (channels,))

if failures:
    print("G1 FAIL")
    for item in failures:
        print("  -", item)
    sys.exit(1)
print("G1 PASS")
'@ | Set-Content -Encoding utf8 check_41c.py
```

Then run it over this session's history (it is the newest
`*_microclaw_history.jsonl` in your history folder):

```powershell
python check_41c.py C:\path\to\<session>_microclaw_history.jsonl
```

Read the printed **G1 PASS** / **G1 FAIL** words, and paste the whole output —
the `raw set_device_property` list is evidence either way.

> **Validated both directions before this runbook shipped.** On the
> reconstruction of the 2026-08-05 M5 writes (`Thorlabs Filter Wheel.State = 1`,
> `Laser 2: 1. Enable = 1`, `Laser 1: 1. Enable = 0`, verbatim from design/41 F6)
> it prints **G1 FAIL** and names both enable writes. On a session that switches
> with `set_channel` and only moves the filter by hand it prints **G1 PASS**. The
> reconstruction was built from the writes quoted in design/41 rather than from
> the captured session file, which is not in this repository — **coordinator:
> re-run the checker against the real 2026-08-05 history and confirm FAIL before
> this gate is accepted.**

Then confirm the hardware end state matches what the hand-written sequence
produced. After step 6, `get_emu_laser_map` must show:

- slot 2 (`561`) enabled `1`; slots 0, 1 and 3 enabled `0`;
- trigger mode and sequence on slot 2 **unchanged** by the switch — the plan
  writes enables only, so whatever armed the line before is still what arms it;
- `Thorlabs Filter Wheel.State` at the value **you** set in step 6.

Finally, the exported script from step 7:

```powershell
Select-String -Path $S -Pattern "set_config('Channel'" -SimpleMatch | Measure-Object | Select-Object -ExpandProperty Count
```
— **PASS is 0.** A script that calls `set_config('Channel', ...)` would fail
outright on this rig, which has no such group.

```powershell
Select-String -Path $S -Pattern 'Laser 1: 1. Enable' -SimpleMatch | Measure-Object | Select-Object -ExpandProperty Count
```
— **PASS is 2 or more** (each channel writes it). The exported script must
contain the *reversed* pair, because that is what actually ran.

Run the exported script with microclaw closed and MMStudio running; it should
perform the switches and acquisitions, and stop only where 41b's approved
refusals stop it.

---

## G2 — Demo non-regression: a preset rig behaves exactly as before

*Checklist item: preset-sourced plans behave exactly as before.*

Short session on the **Demo** machine (it has a real `Channel` group).

1. `get_available_channels` — expect `DAPI`, `FITC`, `Rhodamine`, `Cy5` and a
   `source` line naming the Micro-Manager `'Channel'` config group. If it
   mentions EMU at all, that is a **FAIL**.
2. `set_channel` to two different presets in turn. Each must apply and verify as
   it did before this block.
3. Run a small acquisition **passing `channel='DAPI'`** — on this rig that must
   still work, unchanged. (It is the path G1 step 9 refuses on M5.)
4. Export the session and check that the channel switches emitted at all:

```powershell
Select-String -Path $S -Pattern '# NOT EMITTED: set_channel' -SimpleMatch | Measure-Object | Select-Object -ExpandProperty Count
```
— **PASS is 0.** Before this block every `set_channel` refused to emit; that is
the capability G2 confirms did not arrive at the cost of the preset path.

Then read the two `# RECORDED TOOL: set_channel` sections. **Either** shape is
correct, and which one you get says which path ran:

- `core.set_property(...)` / `wait_for_device` / `assert ... get_property(...)`
  — an authorization-map session replayed the expanded preset, and the script
  reproduces exactly those writes;
- `core.set_config('Channel', ...)` — a session with no `property_authorization`
  delegated to Micro-Manager, and the script reproduces that.

**FAIL** if you see `set_config` from a session that *did* have an
authorization map, or expanded writes from one that did not: the script would
then not be what ran.

---

## G3 — the entry state, observed in the same M5 run

*Checklist item: a laser found on at session start is still on at the end, and
one microclaw enabled is off.*

Compare `get_emu_laser_map` **now** against the baseline you wrote down before
step 1.

**PASS** when both hold:

- every slot that read `enabled="1"` at session start reads `"1"` again;
- every slot microclaw enabled during the session that was **not** on at start
  reads `"0"`.

Step 8 is what makes this true. **If step 8 was needed and you forgot it, say
so** — that is a usability finding about the prompt (41a's territory), not a
failure of the plan, and it is worth more than a clean pass.

Note explicitly whether the plan turning off a laser you had on felt right or
wrong in practice. That judgement cannot be made off the rig and it is the one
thing this gate cannot measure.

---

## Recording the result

For each of G1–G3 write **PASS**, **FAIL**, or **SKIPPED (reason)**, and paste:

- the full `check_41c.py` output;
- both refusal messages from step 9, verbatim;
- the `get_emu_laser_map` readings at start and end;
- the exported scripts themselves.

Say which rig each step ran on. A step you could not run is not a pass. If a step
fails, **keep the artifacts exactly as they are** and re-test into a new folder
with a round suffix — on 2026-08-06 a failing artifact was overwritten by its own
fix verification and lost.

## Note on the reworded G1

The checklist's original wording was *"with no raw `set_device_property` call in
the history"*. That cannot be met as written: an EMU-sourced channel is
laser-only because EMU's configuration carries no laser-to-filter association,
so the emission filter is still an explicit, separately-authorized
`set_device_property` write. Inventing that association is precisely what the
block forbids. The criterion is therefore narrowed to **no raw write to a laser
enable**, which is the off-by-one the block exists to prevent, and the filter
writes are recorded rather than forbidden. If a channel on this rig should move
the filter too, the supported way to say so is a Micro-Manager `Channel` preset,
which microclaw then prefers over the EMU map.
