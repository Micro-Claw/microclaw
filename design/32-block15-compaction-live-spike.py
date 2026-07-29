"""G1: does the real Anthropic API accept a COMPACTED history?

Every compaction test in tests/ mocks the client, so the checkpoint this branch
builds has never been sent to the API. Compaction produces a `user` checkpoint
message immediately followed by the next real `user` prompt — two consecutive
same-role messages. The Messages API documents that as legal (adjacent same-role
messages are combined into one turn), but "documented" is not "observed", and a
400 here would invalidate the compaction design rather than a parameter.

Drives real agent turns against a live core with water marks low enough to force
compaction partway through, then keeps going so at least one turn is sent with a
checkpoint in front of it.

Example (PowerShell):
  python design/32-block15-compaction-live-spike.py --config C:\\path\\safety_config.yaml `
      --high-water 6000 --low-water 3000 > g1.txt 2>&1

Spends real tokens and moves the demo stage. Do not point it at a live sample.
"""
import argparse
import json

from microclaw import credentials
from microclaw.agent import run_agent, set_api_key
from microclaw.config import load_safety_config_or_exit
from microclaw.controller import MicroscopeController
from microclaw.conversation import AuditLog, ConversationStore, estimate_tokens
from microclaw.safety import SafetyGuard

# Tool-calling prompts, so the history fills with real tool_use/tool_result
# pairs rather than prose. Compaction has to survive tool pairs, not sentences.
DEFAULT_PROMPTS = [
    # Turn 0 must declare an artifact: the allowlist check below needs a real
    # dataset path recorded early enough to be compacted away later.
    # save_dir is required by run_timelapse and has no default -- omit it and
    # the agent correctly stops to ask, spending a turn and declaring nothing
    # (observed on the first live run).
    "Run a timelapse of 2 frames with interval_s=0, saving to {save_dir}. "
    "Tell me the dataset path it wrote.",
    "Call get_system_state and tell me the current stage position and exposure.",
    "List the properties of the camera device.",
    "Snap and analyze one image. Report the focus metric and whether it is valid.",
    "Read back the exposure, then snap and analyze once more and compare.",
    "Summarise, in two sentences, every measurement you have taken so far.",
    "Which of the values you reported came from a tool call this turn?",
]

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--config", required=True)
parser.add_argument("--port", type=int, default=4827)
parser.add_argument("--model")
# Deliberately far below the 120k/90k shipped defaults: a demo session will
# never reach those, and the point is to observe a compacted request on the wire.
# Calibrated against a real 7-turn demo run that ended at ~3000 estimated tokens
# and never tripped a 6000 high-water mark. 1500 fires around turn 3, leaving
# several later turns to be sent WITH a checkpoint in front of them.
parser.add_argument("--high-water", type=int, default=1500)
parser.add_argument("--low-water", type=int, default=800)
parser.add_argument("--save-dir", default="g1_timelapse",
                    help="Where turn 0's timelapse writes; must be inside the "
                         "configured workspace if one is set.")
parser.add_argument("--prompt", action="append", default=[])
args = parser.parse_args()

prompts = [
    # Only the placeholder is substituted -- a custom --prompt containing braces
    # must not be run through str.format.
    p.replace("{save_dir}", args.save_dir) for p in (args.prompt or DEFAULT_PROMPTS)
]

# env > keyring > file, the same resolution `serve` does. run_agent alone only
# sees ANTHROPIC_API_KEY, so a key stored from the browser would otherwise look
# like an API failure on turn 0 (first run of this spike did exactly that).
key, source = credentials.load_api_key()
if not key:
    raise SystemExit(
        "No Anthropic API key found in the environment, keyring, or config file.\n"
        "Set ANTHROPIC_API_KEY, or start `microclaw serve` once and enter the key\n"
        "in the browser so it is stored, then re-run this spike."
    )
set_api_key(key)
print(f"API key: {credentials.mask(key)} (from {source})", flush=True)

parsed = load_safety_config_or_exit(args.config)
guard = SafetyGuard(parsed.constraints)
ctrl = MicroscopeController(port=args.port, guard=guard)
if not ctrl.is_connected():
    raise SystemExit("Not connected. Is the ZMQ server enabled in Tools -> Options?")

# enabled=False: this probe should not litter the rig with transcripts. G2
# covers the on-disk audit; this one is only about the wire format.
store = ConversationStore(
    AuditLog(None, enabled=False),
    high_water_tokens=args.high_water,
    low_water_tokens=args.low_water,
)
history = []
turns = []
first_compacted_turn = None

for index, prompt in enumerate(prompts):
    before = store.compaction_count
    # The model view for THIS turn is built inside run_agent via context_provider,
    # but build it here too so the shape that went over the wire is recorded even
    # if the call raises.
    sent = store.model_messages(history + [{"role": "user", "content": prompt}])
    leading_user_run = 0
    for message in sent:
        if message.get("role") == "user":
            leading_user_run += 1
        else:
            break
    try:
        reply, history = run_agent(
            prompt, ctrl, guard, history, model=args.model,
            context_provider=store.model_messages, on_message=store.append,
        )
    except Exception as exc:  # noqa: BLE001 — the whole point is to see it
        print(json.dumps({
            "verdict": "FAIL",
            "turn": index,
            "compactions_before_turn": before,
            "leading_consecutive_user_messages": leading_user_run,
            "error_type": type(exc).__name__,
            "error": str(exc)[:2000],
        }, indent=2))
        raise SystemExit(1)

    compacted = store.compaction_count > 0
    if compacted and first_compacted_turn is None:
        first_compacted_turn = index
    turns.append({
        "turn": index,
        "messages_in_history": len(history),
        "messages_sent_to_model": len(sent),
        "leading_consecutive_user_messages": leading_user_run,
        "compaction_count": store.compaction_count,
        "estimated_tokens_sent": store.last_estimated_tokens,
        "estimated_tokens_full_history": estimate_tokens(history),
        "reply_head": reply[:200],
    })
    print(json.dumps(turns[-1], indent=2), flush=True)

view = store.model_messages(history)
verbatim = [m for m in view if m is not store._checkpoint]

# The security-relevant property, checked on real session data rather than a
# fixture: /api/artifact resolves against the FULL durable record, so an artifact
# declared in turn 0 must stay downloadable after that turn has been compacted
# out of what the model sees. Narrowing this to the model view would make the
# server refuse files it really did produce.
from microclaw.webserve import _declared_artifacts  # noqa: E402 — after the run

allowed = _declared_artifacts(store.audit.records)
in_model_view = _declared_artifacts(
    json.loads(json.dumps(verbatim, default=str))
)
compacted_away = sorted(allowed - in_model_view)

print(json.dumps({
    # PASS needs all four: compaction happened, at least one turn was SENT with
    # a checkpoint in front of it, the API never refused that shape, and the
    # D1 floor left recent turns verbatim.
    "verdict": "PASS" if (
        store.compaction_count >= 1
        and first_compacted_turn is not None
        and first_compacted_turn < len(prompts) - 1
        and len(verbatim) > 0
        and len(allowed) > 0
    ) else "INCONCLUSIVE",
    "artifacts_in_durable_record": sorted(allowed),
    "artifacts_still_in_model_view": sorted(in_model_view),
    # Non-empty here is the interesting case: proof the allowlist outlives
    # compaction. Empty just means nothing got compacted away this run.
    "artifacts_compacted_away_but_still_downloadable": compacted_away,
    "compaction_count": store.compaction_count,
    "first_turn_after_compaction": (
        None if first_compacted_turn is None else first_compacted_turn + 1
    ),
    "turns_sent_with_a_checkpoint_prefix": sum(
        1 for t in turns if t["compaction_count"] > 0
    ),
    "max_leading_consecutive_user_messages": max(
        (t["leading_consecutive_user_messages"] for t in turns), default=0
    ),
    "final_messages_in_history": len(history),
    "final_verbatim_messages_in_model_view": len(verbatim),
    "final_estimated_tokens_sent": store.last_estimated_tokens,
    "final_estimated_tokens_full_history": estimate_tokens(history),
    # An INCONCLUSIVE run tested nothing. Say what to change rather than leaving
    # the operator to infer it from the token trace.
    "next_step": (
        None if store.compaction_count >= 1 else
        f"No compaction: the session peaked at {estimate_tokens(history)} estimated "
        f"tokens, under the {args.high_water} high-water mark. Re-run with "
        f"--high-water {max(400, estimate_tokens(history) // 2)} --low-water "
        f"{max(200, estimate_tokens(history) // 4)}, or add more --prompt turns."
    ),
    "artifact_note": (
        None if allowed else
        "No artifact was declared, so the allowlist property is untested. Check "
        "turn 0's reply_head: if the agent asked a clarifying question instead of "
        "running the timelapse, pass --save-dir a path inside the workspace."
    ),
}, indent=2))
