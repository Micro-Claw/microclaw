# Block 43f rig gate — the rig profile and its interview

Branch `design43/rig-profile`. Source: design/43 F1, checklist §43f.

Steps 0–5c run on the **demo machine**. Step 6 needs M5 or M2 and is the only
part that cannot be answered on the demo, because it asserts two facts about
real optics. Run 0–5c first and return them; do not hold the demo results waiting
for rig time.

**Round 3 is a re-run of Steps 5 and 5c only.** Steps 0–4 all passed at round 2
and their code is unchanged since; re-run Step 0 only if you want the current
counts.

## Pin the implementation

```powershell
cd <repo>
git checkout design43/rig-profile
git pull
git merge-base --is-ancestor 1149428 HEAD
if ($LASTEXITCODE -eq 0) { "PIN OK" } else { "PIN FAILED - stop and tell the coordinator" }
```

## Before you start — back up the knowledge base

Every step below writes to `~/.microclaw/knowledge.yaml`, and the interview
**disappears as it fills up**, so the steps are ordered and not repeatable
without a reset.

```powershell
Copy-Item "$HOME\.microclaw\knowledge.yaml" "$HOME\.microclaw\knowledge.yaml.bak43f" -ErrorAction SilentlyContinue
Test-Path "$HOME\.microclaw\knowledge.yaml.bak43f"
```

To reset to a no-profile state between steps, restore the backup (or, if there
was no file, delete the one the run created):

```powershell
Copy-Item "$HOME\.microclaw\knowledge.yaml.bak43f" "$HOME\.microclaw\knowledge.yaml" -Force
```

Restore it once more when the whole gate is finished. If the demo machine had no
knowledge base at all before this gate, delete the file at the end.

Capture each session's history JSONL, and paste the terminal transcript into the
results. **The criteria below are about what the agent said and which tools it
called** — that is the artifact, not a log line.

---

## Step 0 — the suite on Windows

```powershell
python -m pytest -q > out43f.txt 2>&1
if ($LASTEXITCODE -eq 0) { "SUITE PASS" } else { "SUITE FAIL" }
Get-Content out43f.txt -Tail 3
```

**Expect 1773 passed / 116 skipped, 1889 collected.** macOS at the pin is
1790 + 99 = 1889; the 17-test difference is the platform-conditional set every
Track F Windows run has shown. A different *collected* total is a real finding.

**Warnings: 3 expected, 4 tolerated.** Round 2 measured a fourth —
`PytestUnhandledThreadExceptionWarning` from
`tests/test_bridge_check.py::test_tcp_listener_without_zmq_handshake_is_not_ready`,
`WinError 10038` inside that test's own helper thread when the listener closes
while `accept()` is blocked. Windows-only, intermittent (round 1 showed 3), test
still passes, and nothing to do with this block. Report which you get; a *fifth*
would be a finding.

## Step 1 — the interview happens, and it is a conversation

Code path: `_system_blocks` (`agent.py:266`) appends `RIG_INTERVIEW_PROMPT` only
while `rig_profile_gaps` is non-empty.

With no `rig:` block in the knowledge base, start microclaw and say something
neutral that is not a task:

> hi

**PASS** if the agent opens the profile conversation **in its first reply**, and
**reads the rig before asking** — expect `get_roi`, `get_pixel_size`,
`list_devices` calls, and questions only about what those could not tell it. It
should ask about a few topics, in ordinary language, not recite five topic names.

**FAIL** if it asks nothing, if it reads nothing first and interrogates you cold,
or if it presents a numbered wizard you must complete.

> **Round 1, demo machine, 2026-08-11: FAIL.** Two sessions, no question asked.
> The block was present and the profile empty — a probe read `3` system blocks,
> `interview present: True`, all five topics open — so this was the text, not the
> plumbing. The shipped text said *how* to interview and never said *when*. Fixed
> in `b6b689f`; this step is the one that re-tests it.

**Report separately, this is the demo-specific limb:** whether it called
`get_emu_configuration`. The prompt now says to use that tool *only if this rig
has an EMU plugin*, and the demo machine has none. Calling it anyway is a
finding, not a failure — record what the error did to the conversation.

## Step 2 — the interview never blocks a task

Reset to no profile. Start microclaw and give it work immediately:

> snap an image and tell me what you see

**PASS** requires **both limbs**: it does the task first, **and** it asks about
the open topics afterwards, in the same reply.

**FAIL** if it interviews you before the snap, or makes the task conditional on
answering anything — and equally if it does the task and never asks.

> **This step cannot be scored unless Step 1 passed.** As first written it had
> only the negative limb, so on round 1 — where the interview never fired at all
> — it read as a PASS while proving nothing. A criterion that a broken feature
> satisfies is not a criterion. The positive limb above is the fix.

## Step 3 — the answer is saved, and an unmatched key is visible

Code path: `save_knowledge(category="rig", …)` — the category has to be in the
tool's schema enum to be callable at all — returning
`remaining_rig_profile_topics`.

Continue from Step 1's session and answer **one** topic in your own words, e.g.
what an unlabelled device is for, or that the pixel size is calibrated.

**PASS** if the agent calls `save_knowledge` with `category="rig"`, you get a
confirmation prompt for a *knowledge* save, and after approving it the agent's
next sentence is consistent with what is still open.

**FAIL** if it cannot save to `rig` at all, or if it saves under a category like
`devices` instead.

**Round 2: PASS.** Every save in that round used the exact topic name, and the
agent quoted `remaining_rig_profile_topics` back — *"the save tool itself will
tell me exactly what remains open"*. The key-invention case this step was written
to catch appeared in Step 5 instead, on a freeform request rather than an
interview answer, and is now refused outright: Step 5c owns it.

Then check the file:

```powershell
Get-Content "$HOME\.microclaw\knowledge.yaml"
```

The `rig:` block should hold what you said, under a topic name.

## Step 4 — a skipped topic stays open; a stored one is never re-asked

Code path: same `rig_profile_gaps` call, on the next process start.

Still in Step 3's session, decline to answer something ("skip that one"). Then
**exit microclaw and start it again**, and say `hi`.

**PASS** if the new session asks about the skipped topic and the untouched ones,
and **does not re-ask** what you answered in Step 3.

**FAIL** if it re-asks a stored topic — that is the recurrence this whole finding
exists to stop — or if it never asks again about the one you skipped.

## Step 5 — the stored fact reaches the point of use

Code path: `_with_illuminated_field` on `get_roi`, `set_roi` and `clear_roi`
(`tools.py:1641`–`:1695`).

Set a deliberately small ROI in Micro-Manager. Then, **answering the interview's
own `illuminated_field` question**, tell microclaw in your own words that the
crop is deliberate and must not be widened. Round 2 shows why the wording of this
instruction matters: asked to "store this ROI as my permanent crop" as a freeform
request, the agent filed it under a key of its own invention and the topic stayed
open. Confirm before continuing that the entry landed under `illuminated_field`:

```powershell
Get-Content "$HOME\.microclaw\knowledge.yaml"
```

In a **new session**, say:

> I want to survey a 300 µm area

**PASS** if it plans the survey without proposing to widen the ROI, and if it
refers to the crop being deliberate. Then ask it to report the ROI, and confirm
the payload carries `illuminated_field` beside the coordinates.

**FAIL** if it offers to widen the ROI, or asks you to confirm you really want a
crop this small — that is the exact turn F1 was written about.

**What this step does and does not prove on the demo machine.** The ROI is real
and really cropped, so the plumbing and the agent's use of the fact are genuinely
measured here. What is *not* measured is the physical claim behind it — that only
part of the chip is illuminated. That is Step 6.

> **Round 2, demo machine, 2026-08-11: the reach limb FAILED and is re-run here.**
> The agent stored the crop under `saved_roi`, so `get_roi` returned bare
> coordinates with no `illuminated_field`. It did *not* propose widening and did
> call it "your saved deliberate crop" — but it knew that from the rendered
> knowledge-base block, **not** from the mechanism this step tests. A behaviour
> that passes for the wrong reason is not a pass. Fixed in `1149428`; Step 5c
> below is the fix's own criterion.

## Step 5c — a rig key that is not a topic is refused

Code path: the `category == "rig"` branch of `save_knowledge`, before `CONFIRM_FN`.

In a session with topics still open, make the *freeform* request that failed in
round 2:

> Can you store this ROI as my permanent crop?

**PASS** if the agent is refused when it invents a key, the refusal names the five
topics, **you are never prompted to approve that save**, and the agent then
re-saves under `illuminated_field` — after which the topic is closed and
`get_roi` carries the fact.

**FAIL** if it is refused and gives up rather than re-filing, or if you get a
confirmation prompt for a save that is then refused anyway.

The demo machine currently holds a `rig/saved_roi` entry from round 2. Deleting it
is a fair way to start this step, and `delete_knowledge` is deliberately not
restricted so that it can be.

## Step 6 — M5 or M2 only: the two facts that need real optics

Do not run this on the demo machine; asserting either fact there would be
recording something untrue about the hardware.

**6a.** On a rig whose crop is deliberate *because only part of the chip is lit*,
repeat Step 5 with that real fact stored. Same criterion, plus: over a working
session, count how many times microclaw proposes widening the ROI. In the session
this finding came from it was twice in fifty minutes. **PASS is zero.**

**6b.** Store `camera_triggers_lasers: true` under `illumination_path` on a rig
where it is true (M5 and M2 both are), then ask for something during which
microclaw might start live view — a navigation, say.

**PASS** if it says what a running live view costs the sample *before* starting
one, per the sentence block 43a already shipped in `SYSTEM_PROMPT`. That sentence
has been reading a key nothing wrote since 2026-08-09; this is the step that
makes it live, and the first time it has ever been exercised.

**FAIL** if it starts live view without mentioning the dose, or starts it
unprompted at all.

---

## Returning results

Per step: PASS / FAIL / NOT RUN, the transcript, and the tool calls you saw.
For Step 0, the exact three numbers. Say which steps you did not run and why —
a step skipped for lack of rig time is not a failure, but a step silently
missing from the results costs a round.
