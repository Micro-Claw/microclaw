# Block 43n rig gate — two-channel search and acquire

Run from branch `design43/acquire-on-hit`. Save every transcript, hook log,
dataset path, audit extract, and exported script with the step number.

**Run Part A on the demo machine before booking M5 time.** Part A closes the
whole mechanism; Part B is only what demo hardware physically cannot show. This
ordering is 43f's and 43j's lesson — both caught real defects on the demo, and
43f's Step 1 failure would have burned an M5 session had it been found there.

## What demo hardware can and cannot prove

A demo camera proves event counts, search-before-acquire phase order, hit and
dose bounds, decision records, result fields, per-hit Z in submitted events, the
`Channel`-group preset route, and standalone script replay.

Identical demo frames **cannot** prove that 561 evidence selected a biological
hit, that 488 differs optically from 561, that an enable line emitted light, or
that the acquired burst contains the intended structure. Do not book demo time as
evidence for any of those.

**Corrected after demo round 1 — the routes are not split the way this page
first claimed.** The demo rig has *both* a `Channel` config group and a property
authorization map, so its `channel_source` reads `config-group` while
`set_channel` still executes through `execute_channel_plan` and produces effect
**triples**: the execution route branches on `_has_channel_authorization_map`,
not on channel source. So **demo does exercise the effects-triple route**, and
round 1 found a blocking exporter defect there that the original M5-first
ordering would have found on the rig. What remains M5-only is the **EMU
laser-map channel source** — a rig with no `Channel` group at all — plus
camera-triggered dose and the optical claims.

---

# Step 0 — pin and offline suite (demo machine)

In PowerShell, from the checkout:

```powershell
git merge-base --is-ancestor 9e77560 HEAD
Write-Host "implementation ancestor exit code (expected 0):" $LASTEXITCODE
python -m pytest -q -p no:cacheprovider > block43n-pytest.txt 2>&1
Write-Host "pytest exit code (expected 0):" $LASTEXITCODE
Get-Content block43n-pytest.txt
```

Expected macOS baseline for this branch: **1910 collected, 1811 passed, 99
skipped, 3 warnings**. The warnings are one `StarletteDeprecationWarning` and two
empty-image `phase_cross_correlation` warnings. On Windows, record the exact
counts.

**Expect exactly 3 warnings, not 4.** This branch carries the fix for the
`test_bridge_check.py` socket race that intermittently added a fourth. That fix
was verified on macOS only, which cannot raise `WinError 10038`, so **this step
is where it is actually proved.** Run:

```powershell
$warned = 0
for ($i = 1; $i -le 100; $i++) {
  python -m pytest -q -p no:cacheprovider tests/test_bridge_check.py > bridge-run.txt 2>&1
  if ($LASTEXITCODE -ne 0) { Write-Host "FAILED on iteration" $i; break }
  if (Select-String -Path bridge-run.txt -Pattern "PytestUnhandledThreadExceptionWarning" -Quiet) {
    $warned = $warned + 1
    Write-Host "thread-exception warning on iteration" $i
  }
}
Write-Host "iterations showing the warning (expected 0):" $warned
```

A single clean run proves nothing here — the race appeared in 43f round 2 and not
in round 1 on the same machine. Report `$warned` and the iteration count.

Stop if the ancestor check, suite exit code, collection total, or test totals do
not match the applicable platform baseline.

**One exception, added after round 1.** If the only failure is
`test_webserve.py::test_browser_opens_only_once_the_port_accepts`, record it and
**continue** — it is not this block's, it is not in any file this block touched,
and it passed on Windows at 43j's pin. Run Step 0b below and carry on to Part A.
Any other failure still stops the gate.

## Step 0b — the webserve flake probe (NOT part of this block's verdict)

Piggybacking on the visit; it does not affect whether 43n passes. Skip it freely
if time is short.

```powershell
python design\35-webserve-flake-probe.py --iterations 40 --load 0 > webserve-probe-idle.txt 2>&1
Write-Host "idle exit code (expected 0):" $LASTEXITCODE
python design\35-webserve-flake-probe.py --iterations 40 --load 8 > webserve-probe-load.txt 2>&1
Write-Host "loaded exit code (expected 0):" $LASTEXITCODE
Get-Content webserve-probe-idle.txt, webserve-probe-load.txt
```

Return both tables whole. **Every round reading `OK` is a real and useful result**
— it means the trigger is something the probe does not model, and rules out both
current hypotheses rather than proving the test is fine. The column to read first
is `e@listen`: it is 0.00 on macOS, meaning the worker makes no poll iterations
before `listen()`. If it climbs on Windows, the two platforms take different
paths through the same test and that is the finding.

---

# Part A — demo pre-gate

Run all of Part A before booking M5.

## A1 — reach the feature from operator language

Start Microclaw and give the agent exactly this request (do not name a tool):

> Search these marked fields in 561 one at a time. Whenever the saved detection
> hook reports a hit, acquire a three-frame burst at that field in 488, at most
> two fields. Save the search, bursts, hook log, and a standalone replay script.

Substitute the demo machine's own preset names for 561/488 if those presets do
not exist there; the routing claim is what is under test, not the labels.

Before confirmation, capture the proposed call. It must be one adaptive survey
with the search channel in `protocol_params.channel` and an `acquire_on_hit`
object carrying the acquire channel, `protocol="timelapse"`, `n_frames=3`, and
`max_hits=2`. It must not propose a manual loop over a prior hook log, and must
not propose two separate acquisitions.

**`exposure_ms` is optional and its absence is not a failure** — an earlier
version of this step said to stop without it, which was wrong and cost round 1 a
false alarm. Both phases fall back to the rig's current exposure, and the
reservations are computed from that fallback. What must hold is that whatever
exposure is in force appears in the reservation arithmetic (checked in A2/A3),
not that the agent named it here.

Stop if the feature is not reached, or if either channel or `max_hits` is absent.
**Reach has failed first-round on three separate blocks; it is the most likely
thing on this page to be wrong.**

## A2 — positive-hit mechanism

Round 1 did not run this step — its hook found nothing, so the whole positive
path is still unexercised. "Use a deterministic hook" was also all this step used
to say, which was not enough to act on. Here is the exact one.

**Do not reach for this from operator language.** A1 already proved reach; A2 is
a mechanism test, so naming the tool and dictating the hook is correct. Paste
this as one message:

> Save this hook exactly as written, under the name `a2_deterministic`, then use
> it.
>
> ```python
> from microclaw.hook_decisions import AcquireAt, ContinueSurvey, HookResult
>
> class A2Deterministic:
>     """Deterministic gate hook: ignores image content entirely."""
>
>     def __init__(self, **_kwargs):
>         self.frame = -1
>
>     def analyze_frame(self, image, metadata):
>         self.frame += 1
>         actions = []
>         if self.frame > 0:
>             actions.append(AcquireAt(self.frame - 1))
>         actions.append(AcquireAt(self.frame))
>         actions.append(ContinueSurvey())
>         return HookResult({"frame": self.frame, "deterministic": True},
>                           tuple(actions))
> ```
>
> Then run an adaptive survey over the marked fields field_1, field_2 and
> field_3, searching in Rhodamine with one frame per field, and acquire_on_hit
> set to channel FITC, protocol timelapse, 3 frames, max_hits 2. Save the hook
> log beside the data. When it finishes, show me the complete result JSON and
> every decision record in the hook log.

The hook re-requests the previous tile before the current one, which is what
produces all three acquire-phase records in a single run. Driven through the real
`UntrustedHookAdapter` before being written here, it yields, in order: accept
(field_1), duplicate (field_1), accept (field_2), duplicate (field_2), max_hits
exhausted (field_3), then `planned survey cursor is already at the end` — that
last one is **normal** and appears in round 1's passing log too.

It ignores image content on purpose, so it proves the mechanism and nothing about
detection. That is this step's remit; real detection is B2.

If the three marked fields no longer exist on the machine, ask the agent to
re-mark three separated fields first, then paste the message above.

Retain the complete audit and hook log.

**Expect to be asked about the acquire channel before any search frame, and
expect the order to look backwards.** Both reservations are taken, and the
acquire channel's enable effects are authorized, *before* the search channel is
written to hardware — so the `illumination/enable` prompts for the acquire
channel arrive first, then the search switch, then the search frames.
Authorizing is not switching: the authorization is a policy check and a
confirmation with no device write behind it. Approve both reservations and both
enable streams.

Pass requires all of:

- all search frames precede all acquire frames, with exactly one acquire-channel
  phase switch regardless of hit count;
- the audit contains separate search and acquire enable confirmations, and the
  acquire illuminated dose equals returned acquire frames times the acquire
  exposure, never exceeding `max_hits × 3 × exposure_ms`;
- each accepted action says `planned tile recorded for acquire phase`; a repeated
  tile says `planned tile is already recorded for acquire phase`; a third unique
  tile says `acquire phase max_hits exhausted`;
- the result reports `hits_recorded`, `hits_acquired`, `max_hits_reached`, and
  `acquire_phase_ran`;
- acquired positions are unique, and their submitted Z values equal their
  hit-time focus Z values.

With the hook above and `max_hits=2` the expected result is exact, so score
against these numbers rather than judging: `hits_recorded=2`, `hits_acquired=2`,
`max_hits_reached=true`, `acquire_phase_ran=true`,
`acquire_frames_reserved=6` (2 × 3), `acquire_frames_accounted=6`,
`acquire_frames_unused=0`, and a second dataset named `<name>_acquire` beside the
search one. Round 1's zero-hit run reserved the same 6 and accounted 0, so the
pair of runs brackets the reservation arithmetic from both ends.

These are counting and record-reading limbs only. **Do not score any optical
claim here** — that is B2, and a demo run that matches every count above is not
evidence that the feature imaged anything real.

## A3 — zero-hit run

**Already PASSED twice — rounds 1 and 2 — without being run deliberately.** Any
survey whose hook finds nothing *is* this step, and both rounds' first survey
(the `spot_detection_acquire` one, which found 0 spots) is exactly that. Check
the evidence you already have before re-running anything.

To run it deliberately, paste this — it is A2's message with one word changed:

> Save this hook exactly as written, under the name `a3_never_hits`, then use it.
>
> ```python
> from microclaw.hook_decisions import ContinueSurvey, HookResult
>
> class A3NeverHits:
>     """Deterministic zero-hit hook: never requests an acquire."""
>
>     def __init__(self, **_kwargs):
>         self.frame = -1
>
>     def analyze_frame(self, image, metadata):
>         self.frame += 1
>         return HookResult({"frame": self.frame, "hit": False},
>                           (ContinueSurvey(),))
> ```
>
> Then run an adaptive survey over the marked fields field_1, field_2 and
> field_3, searching in Rhodamine with one frame per field, and acquire_on_hit
> set to channel FITC, protocol timelapse, 3 frames, max_hits 2. Save the hook
> log beside the data. When it finishes, show me the complete result JSON.

Pass requires a successful search result with `hits_recorded=0`,
`hits_acquired=0`, `max_hits_reached=false`, and `acquire_phase_ran=false`; no
acquire-channel switch, no acquire dataset, and zero acquire frames and dose.
Search frames must still exist. A run with no frames at all cannot pass.

Expected exactly: `acquire_frames_reserved=6`, `acquire_frames_accounted=0`,
`acquire_frames_unused=6`, and `frames_acquired=3` — the mirror of A2's
`6 / 6 / 0`. Both rounds have already produced these numbers.

**You will still be prompted to authorize the acquire channel on this run, and
the audit will still carry its enable entry. That is expected and is not a
failure of this step** — authorization happens up front for a phase that may
never execute, as in A2. What must be absent is the *switch*: no device write to
the acquire channel, no acquire dataset, no acquire frames, no acquire dose.
Score this step on device writes and frame/dose counts, not on the presence of
the prompt. If you cannot tell an authorization from a switch in the audit, stop
and report that — it is a finding about the audit, not a pass or fail here.

## A4 — standalone replay with Microclaw closed

Export the A2 session script. Confirm it contains the hook source, the adaptive
decision loop, `hits = []`, hit-time `core.get_position`, both recorded channel
effects, and an acquire loop using each fresh hit's `z_um`. **It must not contain
the A2 run's hit list as acquire coordinates.**

Close Microclaw completely, leave Micro-Manager/pycro-manager available, then:

```powershell
python .\<exported-script-name>.py > block43n-standalone.txt 2>&1
Write-Host "standalone exit code (expected 0):" $LASTEXITCODE
Get-Content block43n-standalone.txt
```

Pass requires new search frames, acquire frames bounded by `max_hits × 3`, one
acquire switch only when the new hit set is non-empty, and per-hit restored Z. A
script that replays the recorded session's coordinates fails even if it exits
zero.

**The script must be run, and the run's output captured.** Round 1's export
parsed, compiled, and was written — and then died at line 1917 on
`NameError: _verify_property is not defined`, because a helper its own emitted
lines call was not inlined. Every static check the exporter performs passed. Only
running it found this. Treat "the script was produced and looks right" as no
evidence at all.

**An empty capture is EXPECTED and is not a failure.** The emitted script
contains no `print()` and no `logging.basicConfig`, so a completely successful
run writes nothing to stdout or stderr. Round 2's `block43n-standalone.txt` was
0 bytes and the run had in fact succeeded — the coordinator initially mis-scored
that step from the empty file alone, which is the mistake this paragraph exists
to stop you repeating.

**Score this step on the datasets, not on the transcript.** A standalone run
writes a fresh, suffixed set beside the live one — round 2 produced
`search_561_2`, `search_a2_2`, `search_a2_acquire_2`, and second copies of both
hook logs. Check:

- the acquire dataset exists for the standalone run and its stack is the same
  size as the live one (round 2: 3,176,982 bytes both, 6 frames);
- the standalone hook log carries the full decision trace independently — round 2
  reproduced accept / duplicate / accept / duplicate / max_hits-exhausted with a
  timestamp minutes after the live run;
- the hits were chosen during that run rather than replayed, which the script's
  structure shows: `hits = []` built at run time, `for hit in hits:`, and no
  literal recorded coordinates in the acquire loop.

Paste the exit code, which is the one thing the transcript does tell you.

Static checks that are still worth doing first, because they are free and they
localise a failure before you spend a run on it: the script must contain **no**
`import microclaw` or `from microclaw` line, and must itself define every helper
it calls — `_verify_property`, `AcquireAt`, `ContinueSurvey`, `HookResult`,
`UntrustedHookAdapter`. Round 2's script passes all of these.

On demo the selected hit set may legitimately be identical to A2's, because the
frames are identical — so score the *structural* claim (fresh selection at run
time, no literal recorded coordinates in the file), not set inequality.

Also export the **A3** zero-hit session and confirm it produces a runnable
script. A zero-hit run is a successful search and must still emit its program;
refusing to export it was a defect found in review and is pinned by a test.

## A5 — `Channel`-group preset route

**BLOCKED on this machine as configured, and the block is this step's own
mistake.** A5 was written to prove the `set_config` preset route, and asserted an
export containing `core.set_config` plus `core.wait_for_config`. That route is
not reachable here: `set_channel` branches on `_has_channel_authorization_map`,
**not** on channel source, and the demo config carries a map. Rounds 1 and 2 both
show `channel_source: "config-group"` alongside effect **triples**, and their
exports contain `core.set_property` + `_verify_property`, never `set_config`.

So there is nothing to run here as written. Do not try to force it.

What that route's coverage actually is today:

- The **effects-triple** route is fully rig-proven by A2 and A4 on this machine.
- The **map-less `set_config`** route is exercised only by unit tests — the
  round-2 export tests supply `{"config_group": "Channel"}` for both phases. That
  is exactly the gap that hid round 1's `_verify_property` defect, in mirror
  image, so "unit-tested" should not be read as "safe."

Two ways forward; **the choice is the operator's and neither blocks the block:**

1. **Leave it unproven and say so in the report.** Legitimate: no rig this gate
   can reach is map-less, and inventing one is not evidence about a real rig.
2. **Prove it with a map-less safety config.** Only if you are comfortable
   swapping configs and restoring afterwards. Copy the deployed safety config,
   delete its `property_authorization` section, start Microclaw against the copy,
   and paste the A2 message with `a2_deterministic` and `max_hits=1`. Then pass
   requires search frames before acquire frames, one preset transition, hit
   counts matching datasets, and an export containing `core.set_config` plus
   `core.wait_for_config` for both phase presets — run with Microclaw closed as
   in A4. **Restore the original config afterwards and say in the report that you
   did.** A script that contains preset lines but produces no frames does not
   pass.

Either way, record which route this machine took, verbatim, from
`channel_effects`.

**Answered in round 1: the deployed demo config does carry a property
authorization map**, so this step exercises the effects-triple export route as
well as the preset route, and the export must contain `_verify_property` calls
*and* the `def _verify_property` that defines them. Re-confirm rather than
re-investigate; if a later demo build reports `channel_source: "config-group"`
with no `effects` in `channel_effects`, say so, because that changes which route
this step covers.

---

# Part B — M5, and only what demo cannot show

Do not start Part B until Part A passes. Every mechanism limb is already closed
by then; B re-runs the flow to exercise the **authorization-map route** and to
supply the **optical and dose-reality** evidence.

## B1 — reach and route on the EMU rig

Give the A1 request on M5. Confirm the feature is reached, and confirm the
channel **source** is the EMU laser map rather than a `Channel` config group —
this is the part demo cannot reach, since demo has a `Channel` group and M5 does
not. M5 has no `Channel` group, so a run whose `channel_source` reports
`config-group` here is a defect.

The effect-triple *execution* route is already covered by A2/A5; what is new here
is that the triples are derived from the EMU map. Report `channel_source` for
both phases verbatim.

## B2 — positive hit run with optical evidence

Fields where the operator can identify at least one real positive. All of A2's
counting limbs must still hold, but this step is scored on what only M5 shows:

- operator-visible search frames use 561 and the later burst uses 488, and the
  two are optically distinguishable;
- the saved 488 images visibly contain the intended structure;
- acquire dose is real light on a camera-triggered rig, and the illuminated
  milliseconds in the audit match the reservation.

**An inert run — matching counts without a real positive — does not pass this
step.** That is the entire reason M5 time is being spent.

## B3 — zero-hit run on M5

A3's limbs, re-scored where camera-triggered lasers make them physical: no
acquire-channel device write, and no light emitted for the acquire phase. The
authorization prompt and audit entry are still expected, exactly as in A3.

## B4 — standalone replay on M5

A4's procedure on the authorization-map route. The exported script must contain
recorded 561 and 488 effect triples, and must select fresh hits. Unlike demo, a
different hit set here is real evidence and should be reported as such.

---

# Report

Return the Step 0 transcript with exact platform suite counts and the socket-race
iteration result; the proposed tool call from A1 and B1; result JSON, hook logs,
and audit extracts for both enable streams and dose; event/Z evidence; dataset
paths; exported scripts and standalone transcripts; and for B2 the operator's
image observations.

Mark every demo observation separately from M5 optical evidence. If a Part A step
passed on demo and its M5 counterpart was not re-run, say so explicitly rather
than reporting the step as passed on M5.
