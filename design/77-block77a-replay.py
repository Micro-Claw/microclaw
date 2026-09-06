"""Block 77a's gate: does the reconciled runtime text change what the model does?

This is evidence-gathering, not a test. It calls a live model, costs API tokens,
and **its output must not be committed** -- fold the measured finding into
design/77's ledger.

## What is measured

The incident is in the operator's own session
(`20260905_165524_640862_microclaw_history.jsonl`). At line 120 the assistant
**successfully ran** `run_analysis_on_saved_dataset`. At line 122 it called
`load_skill("hook-authoring")`. At line 124 it told the microscopist that the
offline orchestrator was "a design/26 proposal, not yet implemented" and that it
therefore could not deliver the overlay. The skill overrode the installed schema
*and* the model's own successful call two turns earlier.

So the gate holds everything constant except the tree the runtime text comes
from. `--tree` puts a checkout on `sys.path` ahead of everything else; the
control is a pre-77a checkout and the arm under test is the 77a branch. Nothing
is reconstructed by editing prompt strings, because the skill file is half the
variable and cannot be patched that way.

    control:  --tree /path/to/a/checkout/at/32a95f1
    fixed:    --tree /path/to/the/design77-truthful-guidance/worktree

Two arms:

* **A (plan)** replays the operator's opening request and stops at the model's
  first turn that asks a question instead of calling a tool -- the same place
  line 8 stopped. It scores the plan.
* **B (crux)** rebuilds the decision point at line 124: the six 640 datasets are
  saved, `frame_statistics` has already succeeded on them, and the model has just
  called `load_skill("hook-authoring")`. **That one tool result is answered live
  from `--tree`.** Everything else is recorded.

## Scoring is mechanical, and deliberately so

design/61's first spike ran a regex over assistant prose and scored a message
that was *refusing* to give numbers as a proposal. Sixteen samples of that would
have looked like data. Every criterion here is a substring or a tool call:

* `dev_ref`   -- `design/NN` in anything the model said. D1's criterion, and the
                 one with a control that fires: the pre-fix skill hands the model
                 the string, so a control run is expected to produce it.
* `offline`   -- the model named an executable offline verb
                 (`analyze_completed_dataset` / `analyze_saved_frame`) or the tool
                 that runs one. This is obtainable only from the reconciled skill
                 or from the code; the stale skill denies it exists.
* `attach`    -- the model named `hook_strategy`, i.e. analysis attached to an
                 acquisition rather than deferred.
* `unavail`   -- the model told the operator the capability does not exist.

"Accounts separately for the overlay" (D4) is `offline and attach` in one turn:
the live observer and the retrospective annotation are different deliverables and
the plan has to carry both.

The one thing no regex settles is whether a *stated reason* is invented, and the
fixture makes even that mechanical for arm B: the model is holding a payload in
which `run_analysis_on_saved_dataset` has just completed successfully, so any
claim that custom offline analysis cannot run is contradicted **by construction**
by the transcript it was given.

## The fixtures are recordings

Recorded tool results come from the session JSONL, keyed by tool name, first
result per name. Three tools are answered **live from `--tree`** instead, because
they are the surface 77a changed: `load_skill`, `list_hooks`, `describe_hook`.
Anything the session never recorded is answered with an explicit
"not available in this replay" error **and counted**, so a sample that leaned on
one is visible in the report rather than silently scored.

`--offline` picks which world `list_hooks` describes, because the acceptance
evidence asks for three and they are not the same claim:

    available      a saved offline adapter is already in the manifest
    empty          the manifest is empty -- "no adapter written yet", which is
                   NOT "custom offline execution is unavailable"
    missing-dep    an adapter is present whose tracker import cannot be satisfied
                   -- that blocks THAT adapter, not the path

**`missing-dep` is not yet a finished world.** It differs from `available` only
in the adapter's source, which the model sees through `describe_hook`; the
import failure itself would surface when the adapter *runs*, and this replay has
no recorded failure to answer that with. So a `missing-dep` sample measures
whether the model reads the source and distinguishes the two claims -- not
whether it handles the runtime failure. Do not report it as the latter. Closing
that would mean running the real runner against a stub dataset and using its
genuine failure manifest as the recorded answer; it is deliberately not
fabricated here.

## Cost

Measured character counts against the kinesin session, at `claude-opus-4-8`
(microclaw's `DEFAULT_MODEL`, and the model that ran the incident):

    system prompt      31,627 ch     tool schema      94,823 ch   -- both cached
    arm A messages      1,395 ch
    arm B pruned       45,875 ch
    arm B full        389,318 ch

All three cache breakpoints microclaw uses are set here -- system, tools and the
conversation prefix -- so a run's fixture is written to cache once and read by
every later sample. At $5/$25 per MTok with a 1.25x cache write and a 0.1x cache
read, eight samples cost roughly **$0.9 per arm per tree**, or **$1.8** for arm B
with `--full-history`. Those are chars/4 estimates, not `count_tokens` figures;
treat them as the order of magnitude, and note the ephemeral cache lives five
minutes, so samples have to run back to back to get the read price.

Pass `--budget` to stop before the sample that would exceed it. Spend is
reported **after every sample**, because a run that is killed mid-flight never
reaches the end-of-run total and its cost is then invisible: block 77a's own
gate was authorised at $10, reported $3.97 from the runs that printed, and
actually cost **$12.13** — the difference was three killed runs, two of them at
fourteen turns a sample. A budget metered only at the end is not a budget.

`--dry-run` costs nothing and exercises every code path here except the API.

    python design/77-block77a-replay.py --dry-run --arm both
    python design/77-block77a-replay.py --session <jsonl> --tree <checkout> \
        --arm b --samples 8 --offline available
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

ACQUISITION_TOOLS = frozenset({
    "run_timelapse", "run_zstack", "run_multiposition_acquisition",
    "run_tile_acquisition", "run_adaptive_survey", "run_mda",
})
LIVE_FROM_TREE = ("load_skill", "list_hooks", "describe_hook")

DEV_REF = re.compile(r"\bdesign/\d+", re.I)
OFFLINE_NAMES = ("analyze_completed_dataset", "analyze_saved_frame",
                 "run_analysis_on_saved_dataset")
# Naming the verb is cheap; defining it is the deliverable.
OFFLINE_VERBS = ("analyze_completed_dataset", "analyze_saved_frame")
OFFLINE_VERB_DEFS = tuple(f"def {v}" for v in OFFLINE_VERBS)
# Extended after reading the first control transcripts: the pre-fix model
# reproduced the incident's substance -- "the offline adapter path for generated
# hooks isn't shipped yet" -- without ever quoting a design number. `dev_ref` is
# therefore a *secondary* criterion here, not the measurement. Fitting a scorer
# to observed control output is a real hazard, so this list was frozen before
# the tree under test was ever run, and every addition is quoted from a control
# sample rather than imagined.
UNAVAILABLE_PHRASES = (
    "not yet implemented", "not implemented", "is a proposal",
    "no wired offline", "cannot run a custom", "can't run a custom",
    "no offline execution", "does not support custom offline",
    "isn't shipped", "is not shipped", "not shipped yet",
    "isn't wired", "is not wired", "not wired yet",
)


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

def load_session(path: Path) -> list[dict]:
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def recorded_results(messages: list[dict]) -> dict[str, str]:
    """Real recorded tool results keyed by tool name; first result per name.

    Lossy on purpose: a replay that tried to match arguments would have to decide
    what "the same call" means, and the arms here never need a second result from
    one tool. The loss is stated in the report rather than hidden.
    """
    uses = {b["id"]: b["name"] for m in messages
            if isinstance(m.get("content"), list)
            for b in m["content"] if b.get("type") == "tool_use"}
    table: dict[str, str] = {}
    for m in messages:
        if not isinstance(m.get("content"), list):
            continue
        for b in m["content"]:
            if b.get("type") != "tool_result":
                continue
            name = uses.get(b.get("tool_use_id"))
            raw = b.get("content")
            text = raw if isinstance(raw, str) else (
                raw[0].get("text", "") if raw else "")
            if name and name not in table:
                table[name] = str(text)
    return table


def find_result(messages: list[dict], name: str, *, contains: str) -> str | None:
    """The recorded result of the call to `name` whose input mentions `contains`.

    `recorded_results` keeps the first result per tool name, which is fine for a
    tool called once and a trap for one called many times on different inputs.
    Arm B's pruned fixture hit that trap live: the session's first
    `run_analysis_on_saved_dataset` was a near-blank TIRF check, so a fixture
    claiming the six kinesin movies were saved handed the model a payload from a
    different dataset. It noticed, and spent its decision turn on the
    contradiction instead of the decision.
    """
    uses = {}
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if (block.get("type") == "tool_use" and block.get("name") == name
                    and contains in json.dumps(block.get("input", {}))):
                uses[block["id"]] = True
            elif block.get("type") == "tool_result" and block.get("tool_use_id") in uses:
                raw = block.get("content")
                return raw if isinstance(raw, str) else (
                    raw[0].get("text", "") if raw else "")
    return None


def first_user_message(messages: list[dict]) -> str:
    for m in messages:
        if m.get("role") == "user" and isinstance(m.get("content"), str):
            return m["content"]
    raise SystemExit("This session has no plain-text opening user message.")


def operator_replies(messages: list[dict]) -> list[str]:
    """Every plain-text operator turn after the first, in order, verbatim.

    Arm A hands these back one at a time whenever the model stops to ask, so the
    replay stays the operator's own words rather than an invented interlocutor.
    """
    plain = [m["content"] for m in messages
             if m.get("role") == "user" and isinstance(m.get("content"), str)]
    return plain[1:]


# --------------------------------------------------------------------------
# the live half: the three tools 77a changed
# --------------------------------------------------------------------------

def install_tree(tree: Path) -> object:
    """Import microclaw from `tree`, and prove it came from there.

    An editable install resolves to whichever checkout is first on sys.path, so a
    control run launched from the wrong directory silently measures the arm under
    test. This refuses rather than reporting that number.
    """
    sys.path.insert(0, str(tree))
    for name in [n for n in sys.modules if n == "microclaw" or n.startswith("microclaw.")]:
        del sys.modules[name]
    import microclaw  # noqa: PLC0415
    resolved = Path(microclaw.__file__).resolve().parent.parent
    if resolved != tree.resolve():
        raise SystemExit(
            f"--tree {tree} but `import microclaw` resolved to {resolved}. "
            "Run this from a directory that is not another checkout."
        )
    return microclaw


def live_answers(tree: Path, offline: str, hooks_dir: Path) -> dict[str, str]:
    """Answer the three changed tools from the installed tree, not a recording.

    The manifest is built here rather than read from the operator's machine, so
    the three `--offline` worlds are reproducible and no real saved hook leaks
    into the measurement.
    """
    install_tree(tree)
    from microclaw import hook_manager, tools  # noqa: PLC0415

    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook_manager.HOOKS_DIR = hooks_dir
    hook_manager.MANIFEST = hooks_dir / "manifest.json"
    manifest: dict[str, dict] = {}
    if offline in ("available", "missing-dep"):
        body = ("import trackpy\n" if offline == "missing-dep" else "")
        code = (f"{body}class KinesinOverlay:\n"
                '    """Track puncta and write an annotated movie."""\n'
                "    def analyze_completed_dataset(self, dataset_view, selection, context):\n"
                "        return [{'tracks': 0}]\n")
        path = hooks_dir / "kinesin_overlay.py"
        path.write_bytes(code.encode("utf-8"))
        import hashlib  # noqa: PLC0415
        manifest["kinesin_overlay"] = {
            "path": str(path), "source": "user_provided",
            "sha256": hashlib.sha256(code.encode("utf-8")).hexdigest(),
            "description": "Track puncta and write an annotated movie.",
            "accepted_warnings": hook_manager.lint_hook_code(code),
        }
    hook_manager.MANIFEST.write_text(json.dumps(manifest), encoding="utf-8")

    answers = {
        "list_hooks": json.dumps(tools.list_hooks(None, None), default=str),
        "load_skill": json.dumps(
            tools.load_skill(None, None, "hook-authoring"), default=str),
    }
    if manifest:
        answers["describe_hook"] = json.dumps(
            tools.describe_hook(None, None, "kinesin_overlay"), default=str)
    return answers


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------

def score(said: str, calls: list[str]) -> dict:
    """Every field here is a substring or a tool name. No prose judgement.

    The scored field is `named_verb`: did the model name `analyze_completed_dataset`
    or `analyze_saved_frame` as its plan. Three things settled that choice, and
    all three came from measurement rather than design.

    * A phrase list for "unavailable" is unbounded. Eight control samples made
      that claim in five different wordings -- "isn't shipped yet", "the
      sanctioned offline-movie path doesn't exist" -- and chasing them fits the
      scorer to whatever the control happened to say. `unavail` therefore
      survives as reported context and is never counted.
    * `authored` -- a `def` in the emitted code, or a `generate_and_save_hook`
      call -- is the stronger signal and this fixture cannot reach it. The
      system prompt requires the model to show the code and wait for explicit
      confirmation before saving a hook, so the replay ends at the question by
      construction. Scoring the consequence of required behaviour as failure
      made both trees read 0, and it is still reported when present.
    * `run_analysis_on_saved_dataset` alone does not discriminate: measured at
      1/8 on the control and 5/6 on the tree under test, because a model that
      believes the path is unavailable still names the tool while declining to
      use it. Only the verb separates them -- **0/8 against 6/6**.
    """
    return {
        "named_verb": sorted({n for n in OFFLINE_VERBS if n in said}),
        "authored": sorted({n for n in OFFLINE_VERB_DEFS if n in said})
                    + (["generate_and_save_hook"]
                       if "generate_and_save_hook" in calls else []),
        "dev_ref": sorted(set(DEV_REF.findall(said))),
        "offline": sorted({n for n in OFFLINE_NAMES if n in said}),
        "attach": "hook_strategy" in said,
        "unavail": sorted({p for p in UNAVAILABLE_PHRASES if p in said.lower()}),
        "acquired": sorted(set(calls) & ACQUISITION_TOOLS),
    }


def verdict(s: dict, *, finished: bool = True) -> str:
    """One label per sample, from the scored fields alone.

    A sample that ran out of turns while still calling tools never reached its
    decision, so it reports NO_DECISION. Scoring it NEITHER would count a limb
    that could not run its mechanism as a measurement -- the first control run
    of this gate did exactly that, four times, and read as a real null.
    """
    if not finished:
        return "NO_DECISION"
    if s["dev_ref"]:
        return "DEV_REFERENCE"
    if s["named_verb"] and s["attach"]:
        return "BOTH_ACCOUNTED"
    if s["named_verb"]:
        return "PLANS_ADAPTER"
    if s["attach"]:
        return "ATTACH_ONLY"
    return "NO_ADAPTER"


# --------------------------------------------------------------------------
# the two arms
# --------------------------------------------------------------------------

def arm_a_messages(session: list[dict]) -> list[dict]:
    return [{"role": "user", "content": first_user_message(session)}]


def arm_b_messages(session: list[dict], live: dict[str, str],
                   *, full_history: bool) -> list[dict]:
    """Rebuild the moment the stale skill was read, with the skill answered live.

    Full history is the faithful replay: the session's own 122 lines, with the
    `load_skill` result at line 123 swapped for this tree's. The pruned form is a
    constructed decision point drawn from the same session's messages and
    payloads -- it is ~8x cheaper and it is NOT a verbatim replay; say so when
    reporting it.
    """
    skill_call = {"type": "tool_use", "id": "replay_skill", "name": "load_skill",
                  "input": {"name": "hook-authoring"}}
    skill_result = {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "replay_skill",
         "content": live["load_skill"]}]}
    if full_history:
        prefix = _verbatim_prefix_through_skill_call(session)
        return [*prefix, skill_result]

    stats = find_result(session, "run_analysis_on_saved_dataset",
                        contains="mt_kin")
    if stats is None:
        raise SystemExit(
            "This session recorded no run_analysis_on_saved_dataset call over a "
            "kinesin dataset, which is the fact arm B's pruned fixture rests on. "
            "Use --full-history, which asserts nothing of its own."
        )
    return [
        {"role": "user", "content": first_user_message(session)},
        {"role": "assistant", "content": [
            {"type": "text", "text":
             "The six 640 timelapses are saved, one dataset per field. Before I "
             "build the walking-kinesin tracker and the yellow-box overlay, let "
             "me check there is signal in them."},
            {"type": "tool_use", "id": "replay_stats", "name":
             "run_analysis_on_saved_dataset",
             "input": {"adapter": "frame_statistics", "input_kind": "frames"}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "replay_stats",
             "content": stats}]},
        {"role": "assistant", "content": [
            {"type": "text", "text":
             "There is signal. Now the tracker and the overlay: let me read the "
             "hook-authoring reference before I say where they run."},
            skill_call]},
        skill_result,
    ]


def _verbatim_prefix_through_skill_call(session: list[dict]) -> list[dict]:
    """The session's own turns up to and including its `load_skill` tool_use.

    Located by finding the call, never by a hard-coded line number: the same
    instrument then works on another session's recording.
    """
    for index, message in enumerate(session):
        content = message.get("content")
        if message.get("role") != "assistant" or not isinstance(content, list):
            continue
        for block in content:
            if (block.get("type") == "tool_use"
                    and block.get("name") == "load_skill"
                    and block.get("input", {}).get("name") == "hook-authoring"):
                # Deep-copied: this re-ids the call so the swapped result
                # answers it, and a shallow copy would write that id back into
                # the caller's recording -- which the selftest caught by finding
                # the second call to this function reading a mutated session.
                prefix = copy.deepcopy(session[:index])
                trimmed = copy.deepcopy(content)
                for b in trimmed:
                    if (b.get("type") == "tool_use"
                            and b.get("name") == "load_skill"):
                        b["id"] = "replay_skill"
                return [*prefix, {"role": "assistant", "content": trimmed}]
    raise SystemExit(
        "This session never called load_skill('hook-authoring'), which is the "
        "moment arm B measures."
    )


def run_sample(client, model, system, tools_schema, messages, table, live,
               *, max_turns: int, replies: list[str]) -> dict:
    """Drive one sample and return its scored transcript."""
    said, calls, unrecorded, pending = [], [], Counter(), list(replies)
    finished = False
    for _ in range(max_turns):
        response = client.send(model=model, system=system, messages=messages,
                               tools=tools_schema)
        said.extend(b["text"] for b in response["content"] if b["type"] == "text")
        uses = [b for b in response["content"] if b["type"] == "tool_use"]
        calls.extend(b["name"] for b in uses)
        messages = [*messages, {"role": "assistant", "content": response["content"]}]
        if not uses:
            if not pending:
                finished = True
                break
            messages.append({"role": "user", "content": pending.pop(0)})
            continue
        results = []
        for b in uses:
            if b["name"] in LIVE_FROM_TREE and b["name"] in live:
                content = live[b["name"]]
            elif b["name"] in table:
                content = table[b["name"]]
            else:
                unrecorded[b["name"]] += 1
                content = json.dumps(
                    {"error": f"{b['name']} was not recorded in this session; "
                              "this replay cannot answer it."})
            results.append({"type": "tool_result", "tool_use_id": b["id"],
                            "content": content})
        messages.append({"role": "user", "content": results})
    text = "\n".join(said)
    scored = score(text, calls)
    return {"verdict": verdict(scored, finished=finished), **scored,
            "finished": finished, "calls": calls,
            "unrecorded": dict(unrecorded), "said": text}


# --------------------------------------------------------------------------
# clients
# --------------------------------------------------------------------------

def _cache_conversation_prefix(messages: list[dict]) -> list[dict]:
    """microclaw's third cache breakpoint, on the last content block.

    Arm B's fixture is identical across every sample of a run, so without this
    the whole replayed history is re-billed as fresh input each time -- which on
    `--full-history` is ~97k tokens per sample rather than ~97k once.
    """
    if not messages:
        return messages
    last = messages[-1]
    content = last.get("content")
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    if not (isinstance(content, list) and content and isinstance(content[-1], dict)):
        return messages
    content = [*content[:-1], {**content[-1], "cache_control": {"type": "ephemeral"}}]
    return [*messages[:-1], {**last, "content": content}]


# $ per million tokens for claude-opus-4-8, microclaw's DEFAULT_MODEL: input,
# output, cache write (1.25x input) and cache read (0.1x input).
PRICES = {"in": 5.00, "out": 25.00, "write": 6.25, "read": 0.50}


class LiveClient:
    """Mirrors microclaw's own call shape: same tools, same cached system block."""

    def __init__(self, max_tokens: int):
        import anthropic  # noqa: PLC0415
        self._client = anthropic.Anthropic()
        self._max_tokens = max_tokens
        self.usage = Counter()

    def spent(self) -> float:
        """Dollars this client has actually spent, from reported usage.

        Reported, not estimated: a gate with a budget has to be scored on what
        the API says it billed, the same way every other limb in this repository
        is scored from artifacts rather than from the plan.
        """
        u = self.usage
        return (u["input_tokens"] * PRICES["in"]
                + u["output_tokens"] * PRICES["out"]
                + u["cache_creation_input_tokens"] * PRICES["write"]
                + u["cache_read_input_tokens"] * PRICES["read"]) / 1e6

    def send(self, *, model, system, messages, tools):
        # microclaw caches system AND tools; the tool schema is ~24k tokens and
        # would otherwise be the largest per-sample cost in every arm. Caching
        # it here is both cheaper and the more faithful call shape.
        cached_tools = [*tools[:-1],
                        {**tools[-1], "cache_control": {"type": "ephemeral"}}]
        with self._client.messages.stream(
            model=model, max_tokens=self._max_tokens,
            system=[{"type": "text", "text": system,
                     "cache_control": {"type": "ephemeral"}}],
            messages=_cache_conversation_prefix(messages), tools=cached_tools,
        ) as stream:
            response = stream.get_final_message()
        for field in ("input_tokens", "output_tokens",
                      "cache_creation_input_tokens", "cache_read_input_tokens"):
            self.usage[field] += getattr(response.usage, field, None) or 0
        return {"content": [_echoable(b.model_dump()) for b in response.content]}


# The SDK's model_dump() carries fields the API refuses on the way back in --
# a text block comes back with `parsed_output`, and echoing it 400s the NEXT
# request with `Extra inputs are not permitted`. Whitelist per block type rather
# than dropping Nones, so a field that is legitimately null still survives.
_ECHOABLE = {
    "text": ("type", "text", "citations"),
    "thinking": ("type", "thinking", "signature"),
    "redacted_thinking": ("type", "data"),
    "tool_use": ("type", "id", "name", "input"),
}


def _echoable(block: dict) -> dict:
    keep = _ECHOABLE.get(block.get("type"))
    if keep is None:
        return block
    return {k: block[k] for k in keep if k in block and block[k] is not None}


class ScriptedClient:
    """A fixed list of turns. The selftest's whole subject; costs nothing."""

    def __init__(self, turns: list[list[dict]]):
        self._turns = list(turns)
        self.sent: list[dict] = []

    def send(self, *, model, system, messages, tools):
        self.sent.append({"system": system, "messages": messages})
        return {"content": self._turns.pop(0) if self._turns else
                [{"type": "text", "text": "(scripted client exhausted)"}]}


# --------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--session", type=Path)
    ap.add_argument("--tree", type=Path)
    ap.add_argument("--arm", choices=("a", "b", "both"), default="both")
    ap.add_argument("--samples", type=int, default=4)
    ap.add_argument("--offline", choices=("available", "empty", "missing-dep"),
                    default="available")
    ap.add_argument("--full-history", action="store_true")
    # Arm B needs headroom: the model reads list_hooks/describe_hook before it
    # decides. Arm A needs a ceiling instead -- the real session reached its
    # plan in three tool rounds, and left to run for fourteen the model orients
    # forever and never produces the turn being scored. A sample that hits the
    # cap says NO_DECISION rather than pretending to a null.
    ap.add_argument("--max-turns", type=int, default=None,
                    help="Default: 6 for arm A, 14 for arm B.")
    ap.add_argument("--transcript", type=Path,
                    help="Write every sample's text and tool calls here. A "
                         "verdict that cannot be read back is not evidence.")
    ap.add_argument("--model")
    ap.add_argument("--budget", type=float, default=None,
                    help="Dollars. Stop before the next sample once reported "
                         "usage reaches this. Metering is per sample because a "
                         "run that is killed prints nothing, and its cost is "
                         "then invisible to whoever set the budget.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Score a scripted transcript; makes no API call.")
    args = ap.parse_args(argv)

    if args.dry_run:
        return _dry_run(args)
    if not (args.session and args.tree):
        ap.error("--session and --tree are required unless --dry-run")

    session = load_session(args.session)
    table = recorded_results(session)
    hooks = Path(os.environ.get("TMPDIR", "/tmp")) / f"77a-replay-{args.offline}"
    live = live_answers(args.tree, args.offline, hooks)

    from microclaw.agent import MAX_OUTPUT_TOKENS, SYSTEM_PROMPT, resolve_model
    from microclaw.tools_schema import TOOLS

    model = args.model or resolve_model()
    client = LiveClient(MAX_OUTPUT_TOKENS)
    arms = ("a", "b") if args.arm == "both" else (args.arm,)
    for arm in arms:
        messages = (arm_a_messages(session) if arm == "a"
                    else arm_b_messages(session, live,
                                        full_history=args.full_history))
        # Arm A scores the PLAN -- the turn the real session reached at line 8,
        # where the model stops and asks. Feeding the operator's later replies
        # turns it into a long session replay that measures something else and
        # costs several times as much; the first live run spent minutes a sample
        # doing exactly that, against a docstring that says it stops. Replies
        # stay available through operator_replies() for a future arm that wants
        # them, deliberately unused here.
        replies: list[str] = []
        tally = Counter()
        print(f"\n=== arm {arm.upper()} | tree {args.tree} | offline "
              f"{args.offline} | model {model} ===")
        for n in range(args.samples):
            result = run_sample(client, model, SYSTEM_PROMPT, TOOLS,
                                messages, table, live,
                                max_turns=args.max_turns or (6 if arm == "a" else 14),
                                replies=replies)
            tally[result["verdict"]] += 1
            print(f"  {n + 1:>2}. {result['verdict']:<20} "
                  f"verb={bool(result['named_verb'])} authored={result['authored']} "
                  f"dev_ref={result['dev_ref']} "
                  f"attach={result['attach']} calls={len(result['calls'])} "
                  f"unrecorded={result['unrecorded']}")
            print(f"      ${client.spent():.2f} cumulative")
            if args.budget and client.spent() >= args.budget:
                print(f"  -- STOPPING: ${client.spent():.2f} reached the "
                      f"${args.budget:.2f} budget after {n + 1} samples")
                break
            if args.transcript:
                with args.transcript.open("a", encoding="utf-8") as fh:
                    fh.write(f"\n\n===== arm {arm} | {args.tree} | sample "
                             f"{n + 1} | {result['verdict']} | calls="
                             f"{result['calls']} =====\n{result['said']}\n")
        print(f"  -- {dict(tally)} over {args.samples} samples")
        print(f"  -- ${client.spent():.2f} spent so far, {dict(client.usage)}")
    return 0


def _dry_run(args) -> int:
    """Exercise the scorer and the driver with no API and no session file."""
    stale = [[{"type": "text", "text":
               "The offline orchestrator is a design/26 proposal, not yet "
               "implemented, so I cannot run a custom tracker."}]]
    fixed = [[{"type": "text", "text":
               "I'll attach a tracking hook with hook_strategy during the "
               "movies, and write an analyze_completed_dataset adapter for the "
               "retrospective overlay."}]]
    for label, turns in (("stale", stale), ("fixed", fixed)):
        client = ScriptedClient(turns)
        result = run_sample(client, "scripted", "system", [],
                            [{"role": "user", "content": "go"}], {}, {},
                            max_turns=args.max_turns or 6, replies=[])
        print(f"{label:>6}: {result['verdict']:<20} dev_ref={result['dev_ref']} "
              f"offline={result['offline']} attach={result['attach']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
