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
git merge-base --is-ancestor c5917cc HEAD
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
defect. Build **one** session that exercises every path, with the two deliberate
**probe** refusals last. Do not reorder.

"Refusals last" here means the two refusals you go looking for in step 9, so a
`SafetyViolation` does not stop you discovering a later defect in the *session*.
It is not advice about the exported script, and it is not a reason to avoid
failures elsewhere — if something fails in the middle, leave it and carry on.
Step 10 exists precisely to export a session that contains failures.

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
   - ask for `set_channel('DAPI')` — it must **refuse** and name the four real
     channels.

   A refusal here is a **PASS**. Paste both messages.

   **A `set_channel` that fails on a serial timeout is also worth pasting.** On
   2026-08-06 two of four switches raised `ChannelPlanSafeStateError ... SAFE
   STATE NOT VERIFIED` for plans where `applied=[]` — nothing had been written.
   That is fixed: a plan where no write reaches the device now reports
   `NO WRITE REACHED THE DEVICE` and does **not** claim an unverified safe state.
   If you see `SAFE STATE NOT VERIFIED` on this rig, check whether the same
   message says `applied=[]`; if it does, that is the defect returning.

   > **What the second refusal does and does not test.** With a non-null
   > `channels.allowed`, it comes from `guard.check_channel` — *"Channel 'DAPI'
   > is not in the allowed list: ['405','488','561','640']"* — which fires in
   > `set_channel` **before** `execute_channel_plan` and therefore before
   > `authorize_channel` ever runs. That is correct, and it does list the four
   > real names, but the EMU-source explanation added to `authorize_channel` is
   > **not** exercised by it, and on any rig with a non-null allowlist an unknown
   > name can never reach it. Do not read this step as having covered that path.
   >
   > To reach it, either run once with `channels.allowed` **absent** and ask for
   > a bogus channel, or leave in `channels.allowed` a name the EMU map does not
   > offer (an unnamed slot's, say) and ask for that. Both make `check_channel`
   > pass and `authorize_channel` refuse. Optional — say whether you did it.
10. **Export the session a second time**, to a different filename, now that it
    contains two refused calls. Note that path too.

    This is the one place the M5 gate exercises the skip-and-continue path on
    hardware, and step 7's export cannot: it was taken before anything had
    failed. The second script must contain a `# SKIPPED:` line for each refused
    call and **no `raise RuntimeError`** from them, and it must still run to the
    end and perform both channel switches and both acquisitions. Demo round 2
    failed exactly here — a refused call's `raise` sat in front of the work that
    had really happened, and the script did nothing.

    ```powershell
    Select-String -Path $S2 -Pattern '# SKIPPED:' -SimpleMatch | Measure-Object | Select-Object -ExpandProperty Count
    ```
    — **PASS is 2 or more**: one per refused call in step 9, plus one for every
    other call that failed during the session. The 2026-08-06 M5 run had **4**,
    because two channel switches also hit serial timeouts. Do not read a higher
    count as a failure; read the lines, and check each names a real failure from
    the session.

    ```powershell
    Select-String -Path $S2 -Pattern 'raise RuntimeError' -SimpleMatch | Measure-Object | Select-Object -ExpandProperty Count
    ```
    — **PASS is 0**, unless you also built a mosaic or an adaptive run, which
    refuse by design. If it is not 0, say which step produced it.

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

> **Validated both directions, against the real captured evidence.** Run on
> `smiley_run/20260805_151048_746130_microclaw_history.jsonl` — the M5 session
> this block comes from — it prints **G1 FAIL**, exit 1, naming three raw enable
> writes:
>
> ```
> Laser 2: 1. Enable = 1
> Laser 1: 1. Enable = 0
> Laser 2: 1. Enable = 0
> ```
>
> and correctly *not* counting the two `Thorlabs Filter Wheel.State` writes
> beside them. That session has five raw writes where design/41 F6 quoted three,
> so the checker handles more than the finding described. On a session that
> switches with `set_channel` and only moves the filter by hand it prints
> **G1 PASS**.

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

**Dose.** Count the acquisitions the script would run, and compare it against the
acquisitions that actually *completed* in the session — not the ones you asked
for:

```powershell
Select-String -Path $S -Pattern 'acq.acquire(events)' -SimpleMatch -AllMatches |
  ForEach-Object { $_.Matches } | Measure-Object | Select-Object -ExpandProperty Count
```

**PASS** when it equals the number of acquisitions that completed. Count the
datasets on disk to get that number; do not count from memory.

> This is the check the first M5 run of this gate failed. The session made five
> `run_multiposition_acquisition` calls, of which **two** completed — two failed
> on trigger arming and one was refused by the channel-axis guard — and the
> export emitted **all five**. Run standalone it would have imaged every position
> five times instead of twice, 2.5× the session's dose on a bleaching sample. A
> call that did not succeed is now `# NOT EMITTED` with its reason, including a
> partly-completed one, so this count should now match. If it does not, that is
> the same defect and the script is the evidence — keep it.

Then run the exported script with microclaw closed and MMStudio running; it
should perform the switches and the acquisitions that completed, and stop loudly
at the first step microclaw refused to emit. **Stopping there is a PASS** —
running past it is not.

---

## G2 — Demo non-regression: a preset rig behaves exactly as before

*Checklist item: preset-sourced plans behave exactly as before.*

Short session on the **Demo** machine (it has a real `Channel` group).

1. `get_available_channels` — expect `DAPI`, `FITC`, `Rhodamine`, `Cy5` and a
   `source` line naming the Micro-Manager `'Channel'` config group. If it
   mentions EMU at all, that is a **FAIL**.
2. `set_channel` to two different presets in turn. Each must apply and verify as
   it did before this block. Say in your result which presets you used.

   **The Float read-back needs a preset the stock demo config does not ship.**
   Measured 2026-08-06: the stock `Channel` presets expand to `Dichroic.Label`,
   `Emission.Label`, `Excitation.Label` and `Core.Shutter` — every one a String.
   Nothing in them exercises the numeric comparison, and M5's laser enables are
   categorical too, so **no rig in this gate reaches it by default**. To test it,
   add a camera `Exposure` setting to one demo preset in the Micro-Manager
   Group/Preset editor and use that preset here. If you do not, record that half
   of G2 as **SKIPPED**, not PASS — a criterion that cannot fail is the defect
   this checklist keeps re-learning, and a PASS here would be exactly that.
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

- `core.set_property(...)` / `core.wait_for_device(...)` /
  `_verify_property(core, ...)` — an authorization-map session replayed the
  expanded preset, and the script reproduces exactly those writes;
- `core.set_config('Channel', ...)` — a session with no `property_authorization`
  delegated to Micro-Manager, and the script reproduces that.

**FAIL** if you see `set_config` from a session that *did* have an
authorization map, or expanded writes from one that did not: the script would
then not be what ran.

5. **Run the exported script**, microclaw closed and MMStudio running:

```powershell
python C:\path\to\the\exported_script.py
```

**PASS** when it runs to the end. **This step is the point of G2, not step 4** —
a grep proves the line was written, and this defect only exists when the line
*runs*. The first version of this emitter hand-wrote the read-back as
`assert str(core.get_property(...)) == '10'`, which fails on a write that
succeeded, because Micro-Manager reformats a Float read-back (`"10"` comes back
`"10.0000"`). It is now the executor's own `_verify_property`, inlined, so the
script compares a Float numerically exactly as the rig session did. A
`ChannelPlanError: Read-back verification failed for <camera>.Exposure:
requested '10', got '10.0000'` here is that defect returning — record it
verbatim.

> Round 1, 2026-08-06: the script ran to completion against a live core with
> microclaw closed, every channel write going through `_verify_property`, with
> `_property_type_name` calling `get_property_type` over the pyjavaz bridge, and
> `Core.Shutter` correctly getting no `wait_for_device`. The inlined check works
> standalone on hardware. The Float branch of it remains untested — see step 2.
>
> **Round 2 is this step's known-bad, and it is why "runs to the end" is the
> criterion.** The session's first multiposition call was rejected by the tool
> layer (`TypeError: unexpected keyword argument 'channel'`) and did nothing.
> The exporter refused it — `# NOT EMITTED` plus a `raise` at line 97 — which
> put a hard stop *in front of* the acquisition that had actually run, at lines
> 99–106. The script contributed **zero** acquisitions. A call that completed
> nothing is now a `# SKIPPED` comment and the script carries on; only a genuine
> cannot-emit step, or one that partly completed, still halts.

**A session with a failed call in the middle is the normal case, not something
to avoid by ordering.** Do not rearrange a session to put failures last. The
41b-era advice about placing known refusals last still holds for *genuine*
cannot-emit refusals — the offline mosaic, an adaptive run — because those halt
by design and hide anything after them. It does **not** apply to a call the rig
or the tool layer rejected: those are ordinary, they happen wherever they happen,
and the export has to survive them in place. If a call fails mid-session, leave
it there and export anyway — that is the evidence this step wants.

Also count the acquisitions, as in G1:

```powershell
Select-String -Path $S -Pattern 'acq.acquire(events)' -SimpleMatch -AllMatches |
  ForEach-Object { $_.Matches } | Measure-Object | Select-Object -ExpandProperty Count
```

**PASS** when it equals the number of acquisitions that *completed*. In round 1
this was 2 where 1 had run: the rejected call was emitted anyway — with **no
channel at all**, since the rejected argument was never where the emitter reads
one — so it acquired in whatever state was current, FITC, a channel the session
never asked to image, leaving three datasets per position where the session made
one. Round 2 measured 1, correctly. Both halves must hold together: **the count
matches *and* the script reaches the end.** Round 1 ran to the end with the wrong
count; round 2 had the right count and did not run. Either alone is a FAIL.

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
- **which Demo presets you used in G2, and whether one carried a camera
  exposure** — a G2 run with no Float effect has not tested the read-back
  tolerance, and should be recorded as SKIPPED for that part rather than PASS;
- **the acquisition count from the script beside the number of datasets on
  disk**, for the dose check in G1;
- **both G1 exports** — step 7's and step 10's — and the result of running each.
  The second is the only one that carries failed calls, and it is the one that
  proves the script survives them;
- the exported scripts themselves, and the output of running each one.

If any tool call failed during the session — a refusal, a trigger that was not
armed, a position that did not complete — **say so and keep it in the run**. A
session with failures in it is more valuable evidence than a clean one: the
export defect above was invisible across every earlier gate precisely because
those sessions had none.

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
