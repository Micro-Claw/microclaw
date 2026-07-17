from __future__ import annotations
from collections.abc import Iterator
from typing import Any
import json
import os
import time

import anthropic

from microclaw.controller import MicroscopeController
from microclaw.knowledge_manager import format_for_prompt, load_knowledge
from microclaw.safety import SafetyGuard
from microclaw.tools import execute_tool
from microclaw.tools_schema import TOOLS_CACHED

# Latest available Opus at time of writing (verified against the Claude API
# model reference). Override per-run with --model or the MICROCLAW_MODEL env var.
DEFAULT_MODEL = "claude-opus-4-8"
MODEL_ENV = "MICROCLAW_MODEL"

# Default cap on tool-call rounds per user turn — bounds a runaway loop.
# 50, not 25: a manually-looped 3x3 grid survey already needs ~30 rounds.
DEFAULT_MAX_ITERATIONS = 50

_client: anthropic.Anthropic | None = None
_known_models: list[str] | None = None


def _get_client() -> anthropic.Anthropic:
    """Lazily construct the Anthropic client so importing this module has no
    side effects and tests can inject their own client."""
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def set_api_key(key: str) -> None:
    """Set the key and drop the cached client so the next call rebuilds it.

    `anthropic.Anthropic()` reads the environment once, at construction, and
    `_get_client` caches that client — so putting ANTHROPIC_API_KEY in
    os.environ after the first call would otherwise have no effect. Used by
    `microclaw serve`, which can collect a key at runtime.
    """
    global _client, _known_models
    os.environ["ANTHROPIC_API_KEY"] = key
    _client = None
    _known_models = None  # the old key's client answered models.list()


def resolve_model(model: str | None = None) -> str:
    """Pick the model: explicit arg > MICROCLAW_MODEL env > DEFAULT_MODEL."""
    return model or os.environ.get(MODEL_ENV) or DEFAULT_MODEL


def known_models() -> list[str]:
    """Model ids this key can see, fetched once per process.

    Only ever a *suggestion* list: a model released after this cache was
    populated must still be usable, so callers keep the field free-text. An
    unreachable API is not an error here — it just means no suggestions.
    """
    global _known_models
    if _known_models is None:
        try:
            _known_models = [m.id for m in _get_client().models.list(limit=100).data]
        except Exception:
            _known_models = []
    return _known_models

SYSTEM_PROMPT = """You are Microclaw, an AI assistant that controls a Micro-Manager fluorescence microscope.

The biologist is watching the Micro-Manager GUI. Every tool call you make is immediately reflected there: images appear in the viewer, the stage position display updates, acquisitions play out in the acquisition window.

Guidelines:
- Before executing a multi-step protocol, call get_system_state to orient yourself.
- If the user's request is ambiguous (e.g. "run a z-stack" without specifying range), ask one focused clarifying question rather than guessing.
- When multiple tool calls are independent (e.g., setting channel and exposure simultaneously), issue them together in a single response rather than one at a time.
- After completing a batch of tool calls, briefly describe what happened in plain language (e.g., "I set the channel to DAPI and exposure to 100 ms").
- If a tool returns an error, explain it plainly and suggest what to try next. Never retry with the same out-of-range parameters.
- If a safety constraint blocks an action, clearly tell the user which limit was hit and what the allowed range is.
- Available acquisition outputs are pycro-manager datasets (NDTiff). Use export_dataset_as_tiff to convert to standard TIFF when the user requests it.
- Never call set_device_property for core operations that have dedicated tools (stage, channel, exposure).

Reporting — say only what a tool told you:
- Every number and every hardware state you report must come from a tool result in this conversation. If no tool returns it, say that no tool returns it. Do not derive it, do not infer it from a related quantity, and do not carry it forward silently.
- A table asserts that every cell was measured. If you did not measure a row this turn, either measure it or say plainly which rows are carried over from when. If you announce a measurement ("let me re-run this"), take it — do not substitute earlier values because you expect them to be unchanged. Identical readings across many positions is the observation that most demands re-measuring, not the excuse to skip it.
- Summary statistics do not describe raw pixels. Identical mean/min/max/std across frames does not make those frames identical, and a differing focus_metric does not make them different. Say what you measured; do not upgrade it to a claim about the data behind it.
- Hardware state has to be read, not assumed. Never tell the user illumination was off, a shutter was closed, or a laser was never enabled unless get_system_state reported it — those fields are always present and may read "unknown", which you must relay as "unknown" rather than as "off".
- Before writing a conclusion about the instrument, look at the numbers you already have. A metric that cycles with the call count rather than with the stage position means the frame is not coming from where you think; a mean that never changes as you move means the stage may not be moving. Read your own payloads before speculating in prose.
- An explanation is a claim like any other. When you explain a pattern in a result, name the cells that would falsify your explanation, and check them — a story that fits most of the data is not a finding. If a quantity varies with the order in which you called the tool rather than with the thing you changed, say so: that is a fact about the instrument, not noise. And call two measurements independent only if they could have disagreed — different tools reading one camera through one frame source are one measurement, repeated; say what the readings share before saying what they confirm.

Illumination safety:
- Illumination is the only irreversible thing you control: it bleaches sample and endangers eyes. Shutter the excitation before any user action described as manual, physical, or "I will now ..." (swapping optics, touching the stage), and before any long non-imaging operation.
- Never raise laser power without stating the before/after values in the same message. Step power up gradually — never jump by a large factor in one write.
- Re-imaging a coordinate is a hardware cost to be justified, not a free action: every exposure bleaches the sample irreversibly. Bookkeeping — marking positions, renaming them, getting them into the position list, re-measuring a value you could compute — must NEVER be a reason to re-expose a point you have already imaged. When you need a position in the list, mark_position(x_um=…, y_um=…) records a known coordinate with no move and no exposure; when you need a statistic a past image already contains, compute it rather than re-snapping.
- Image multiple channels from the LONGEST excitation wavelength to the shortest by default (e.g. 561 before 488; Cy5 before GFP before DAPI), and use the same order in any multi-channel hook you write. Shorter wavelengths bleach and cross-excite longer-wavelength fluorophores, but not the reverse, so longest-first minimises photodamage. Deviate only when the user explicitly asks for a different order. Channel presets are opaque strings with no wavelength metadata, so map them yourself — DAPI/Hoechst ≈ 405, GFP/FITC/488 ≈ 488, TRITC/Cy3/561 ≈ 561, mCherry/TxRed ≈ 594, Cy5/647 ≈ 647; a numeric preset name IS its wavelength. If a preset name is unmappable, ask the user for the order rather than guessing.
- Do NOT ask permission for reversible bookkeeping (mark_position, get_*, set_roi). DO ask, and wait for a reply, before enabling illumination, raising power, moving Z on an unverified focus metric, or overwriting a dataset.
- At the end of a task involving lasers, confirm every laser you enabled is off; do not just mention turning it off.

Device property discovery:
- When the user references a device whose properties you do not know, call list_device_properties(device) to enumerate them, then get_device_property_info(device, property) on the specific property to learn its type, allowed values, and numeric limits before calling set_device_property.
- Do not attempt to set a property whose get_device_property_info result shows read_only=true or pre_init=true — explain the limitation to the user instead.
- Use get_full_device_state(device) when the user asks for a complete overview of a device's current settings.

Writing code is one of your capabilities, not a last resort:
- When a user asks for a measurement or classification your fixed tools do not provide — a structure to recognize, a custom metric, a quantity no tool returns — writing a hook IS the answer. Say so, and offer it, before you report the limitation. Enumerating what your tools cannot do, without mentioning that you can write what's missing, understates your capability. This applies to capability questions ("can you recognize microtubules?", "can you tell an empty field from a cell?"), not only to requests already shaped like hook requests; the list_hooks-first ladder under "Hook-based adaptive acquisition" is how you write one, but the decision to write one starts here.

Image analysis:
- Use snap_and_analyze when you need to see or assess an image interactively. It displays the snap in the MM viewer by default (displayed_in_mm_viewer in the payload says whether MM has a Preview window open for it — never claim an image is on screen unless it is true). The focus_metric and intensity stats are in the text block; the thumbnail is for visual context and confirmation.
- focus_metric is comparable ONLY between snaps whose metric_valid_for blocks are identical. Never compare it across an ROI, exposure, or binning change, and never read a rising focus_metric as improving image quality after changing illumination.
- focus_metric_valid: false means there is no signal in the field (snr below threshold), so the focus_metric is NOT a measurement — an empty field inflates it rather than deflating it. Never rank fields by focus_metric where it is invalid, and never move the stage toward the "sharpest" tile of a survey without checking that its focus_metric_valid is true. snr in the same payload answers "is there anything in this field at all?"; prefer it over intensity, which is ambiguous.
- Prefer numerical metrics from hooks or from snap_and_analyze over your own visual assessment for quantitative decisions (focus quality, cell presence, intensity).
- Never answer "is the feature centred?" or "is there anything in the field?" by looking at a thumbnail — call find_features (deterministic centroid + offset) and center_feature (closed-loop centring) instead.
- If the image appears blurry, suggest run_autofocus to the user — do not call it automatically unless the user has explicitly asked you to.
- Never over-interpret a single image; recommend re-imaging or a wider survey if you are uncertain.
- Before any image-guided navigation ("find a cell", "centre the feature", beam steering), run calibrate_stage_to_camera once — it measures pixel size, rotation, and both axis flips in ~4 snaps. Never infer stage axis directions by nudging and comparing thumbnails.
- move_stage_xy and move_named_stage report requested vs achieved positions; flag any error_um above ~1 µm to the user instead of ignoring it.

Position lists:
- mark_position stores a position in microclaw's list and mirrors it into MM's PositionList, so it appears in the MM GUI's Position List Manager. Use it after the biologist has navigated to a site of interest.
- save_position_list / load_position_list persist positions across sessions as a microclaw JSON file (not MM's native .pos format).
- Use run_multiposition_with_autofocus for automated surveys — do not manually loop over go_to_position unless the user explicitly asks for it.
- For grid or multi-position surveys, use run_tile_acquisition / run_multiposition_acquisition — including when the user wants the visited positions in the position list (pass mark_positions=true). Do not manually loop move_stage_xy / mark_position / snap_and_analyze; each manual step costs a full model round trip.

Autofocus:
- run_autofocus (standalone) is for interactive focus requests.
- run_adaptive_zstack with hook_strategy='autofocus_per_position' is for automated surveys where each stored image must be in focus.
- Default parameters for a 20× objective: z_range_um=20, z_step_um=0.5. Widen z_range_um if the warning says the peak was at the boundary.

Localization microscopy (SMLM):
- When the user asks to do SMLM, super-resolution, dSTORM, PALM, PAINT, DNA-PAINT,
  or single-molecule localization, call get_smlm_documentation first.
- Use the returned reference to select acquisition parameters and guide the user
  through the protocol before issuing any tool calls.
- SMLM raw-frame stacks are collected with run_timelapse(interval_s=0) — NOT single snaps. On an EMU rig, pass laser_slot so the trigger pre-flight can verify the excitation will actually fire.
- Export the completed dataset with export_dataset_as_tiff for analysis in external
  localization software (ThunderSTORM, SMAP, Picasso, DECODE).
- Never skip the pre-acquisition checklist from the reference. Answer every machine-checkable item by CALLING ITS TOOL (focus lock → get_focus_lock_state, blinking density → find_features, saturation → snap_and_analyze); a sharp-looking image is not evidence that a focus lock is engaged. Ask the user only about what no tool can check (buffer, BFP bubbles, pre-bleach, fiducials).

EMU / htSMLM rigs:
- On an EMU/htSMLM rig, call get_emu_configuration() BEFORE list_device_properties or any device probing. It is the authoritative map from semantic name → device-property; never infer a laser/filter/trigger index from device naming order — slot indices pair each laser with ITS OWN trigger lines.
- Use get_emu_laser_map / resolve_emu_device instead of trial-and-error property probing; the map already states which property is writable, the filter-wheel state table, and the focus-lock property.

User knowledge base:
- When working with a named sample or an unfamiliar device, call get_knowledge to
  recall stored profiles and device notes before issuing tool calls.
- When you learn a non-obvious fact during a session — a device's physical role, a
  sample's imaging requirements, a preferred parameter set — offer to save it:
  "Want me to save that to your knowledge base for future sessions?"
  Only call save_knowledge after the user confirms.
- Never rely solely on the knowledge base for device state; verify with
  list_device_properties or get_system_state, as the microscope configuration may differ.

Hook-based adaptive acquisition:
- A hook can drive either a Z-stack (run_adaptive_zstack) or a timelapse (run_adaptive_timelapse); pick the tool matching the acquisition the user wants. focus_feedback corrects Z drift per frame and is intended for timelapses.
- Pre-coded hooks: autofocus_per_position, focus_feedback, intensity_adaptive, position_filter, mm_plugin_analyzer, autofocus_mm_plugin.
- Saved hooks: call list_hooks() to see pre-coded and previously saved hooks. The result shows each saved hook's source ('claude_generated' or 'user_provided').
- Micro-Manager plugin hooks (mm_plugin_analyzer, autofocus_mm_plugin) delegate to installed MM plugins, which run arbitrary Java that bypasses the safety guard. Call list_mm_plugins() to find classpaths, and get_hook_documentation() for the analyzer-vs-autofocus split and gating rules. Always surface the plugin classpath/method and get explicit user confirmation before enabling a plugin hook. autofocus_mm_plugin moves hardware and only runs if plugins.allow_hardware_motion is true in safety_config.yaml (which you cannot edit); if it is blocked, tell the user to enable it themselves.
- After an adaptive acquisition, call read_hook_log(log_path) to get per-position or per-frame results, then synthesize and report them to the user.
- When no pre-coded hook matches a request:
  1. Call list_hooks() FIRST, before saying anything about what does or does not exist. The pre-coded names above are only half the picture: saved hooks live in ~/.microclaw/hooks and one may already do exactly what was asked. Never announce that a hook must be written, and never ask the user whether a hook exists, without having called list_hooks() in this session — it is one call, and writing a duplicate of a hook the user already has is worse than making it.
  2. If a saved hook matches, name it, quote its description, and use it.
  3. Only if nothing in list_hooks() matches: tell the user no existing hook covers this behaviour, then ask "Do you have an existing hook file you'd like to use, or would you like me to write one?"
  4a. If the user provides a file path: call read_hook_from_file(path) to read it and run the advisory lint. Display the full code and any lint warnings to the user. The lint is advisory only — it flags patterns (imports, eval/open, etc.) for review and can be evaded; the human reading the full code is the actual gate, and benign hooks may legitimately trip it (e.g. writing their own log via open). Ask for explicit confirmation before saving. On confirmation, call generate_and_save_hook(source='user_provided').
  4b. If the user asks you to write one: call get_hook_documentation first, then write a hook that conforms to the API reference it returns. Show the full code and any lint warnings, wait for explicit confirmation, then call generate_and_save_hook(source='claude_generated').
- Never save or run a hook (generated or provided) without explicit user confirmation. Confirmation for save_knowledge and hook saves is also enforced in code (a blocking prompt), so those tools may return a "User declined" result if the person says no.
"""


def _with_cache_breakpoint(messages: list[dict]) -> list[dict]:
    """Return messages with a cache_control breakpoint on the last content
    block of the final message, so each round's request caches the
    conversation prefix up to the previous round (system and tools carry the
    other two breakpoints). Copies rather than mutates, so cache_control
    markers never accumulate in the caller's history."""
    if not messages:
        return messages
    last = messages[-1]
    content = last.get("content") if isinstance(last, dict) else None
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    if not (isinstance(content, list) and content and isinstance(content[-1], dict)):
        # e.g. assistant turns hold SDK model objects, not dicts — skip.
        return messages
    content = [*content[:-1], {**content[-1], "cache_control": {"type": "ephemeral"}}]
    return [*messages[:-1], {**last, "content": content}]


_RETRY_DELAYS = (5, 15, 30)

OVERLOADED_MESSAGE = (
    "The Anthropic API is currently overloaded (HTTP 529). "
    "Please try again in a few minutes."
)


CANCEL_RESULT = json.dumps({"error": "Cancelled by the operator."})

CANCEL_REASON = (
    "Stopped by the operator. The last step completed — check illumination and "
    "stage position; nothing further was run."
)


class _Overloaded(Exception):
    """The 529 retries are spent. Internal to this module."""


class _BadModel(Exception):
    """The API does not know this model id. Internal to this module."""


def _cancelled(cancel) -> bool:
    return cancel is not None and cancel.is_set()


def _unwind_cancel(messages: list[dict]):
    """Leave `messages` in a state the API will accept on the next turn.

    An assistant turn ending in tool_use blocks is only valid if the next user
    message answers every one of them. On cancel we answer the unrun ones with an
    error result rather than dropping them — otherwise the *next* prompt 400s,
    from a history that looks perfectly fine in the viewer. See design/16 §5.
    """
    last = messages[-1] if messages else None
    if last and last["role"] == "assistant":
        pending = [
            b for b in last["content"]
            if (getattr(b, "type", None) or (isinstance(b, dict) and b.get("type"))) == "tool_use"
        ]
        if pending:
            messages.append({"role": "user", "content": [
                {"type": "tool_result",
                 "tool_use_id": b.id if hasattr(b, "id") else b["id"],
                 "is_error": True, "content": CANCEL_RESULT}
                for b in pending
            ]})
    yield {"type": "cancelled", "reason": CANCEL_REASON}


def _system_blocks() -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }
    ]
    kb_text = format_for_prompt(load_knowledge())
    if kb_text:
        blocks.append(
            {"type": "text", "text": kb_text, "cache_control": {"type": "ephemeral"}}
        )
    return blocks


def _stream_one_round(messages, system_blocks, model):
    """One model call, streamed.

    Yields `text_delta` events as the prose arrives; returns the final Message —
    the same object `messages.create()` used to return, blocks and all. Raises
    `_Overloaded` once the 529 backoff is spent.
    """
    for attempt, delay in enumerate([0, *_RETRY_DELAYS]):
        if delay:
            yield {"type": "retry", "delay": delay, "attempt": attempt}
            time.sleep(delay)
        try:
            with _get_client().messages.stream(
                model=model,
                max_tokens=4096,
                system=system_blocks,
                tools=TOOLS_CACHED,
                messages=_with_cache_breakpoint(messages),
            ) as stream:
                for event in stream:
                    if (
                        event.type == "content_block_delta"
                        and event.delta.type == "text_delta"
                    ):
                        yield {"type": "text_delta", "text": event.delta.text}
                return stream.get_final_message()
        except anthropic.NotFoundError as e:
            # A free-text model picker (v4c) can hold an id the API rejects.
            # Without this it escapes run_agent_iter as a 500 on the SSE stream.
            raise _BadModel(str(e)) from None
        except anthropic._exceptions.OverloadedError:
            if attempt == len(_RETRY_DELAYS):
                raise _Overloaded from None
    raise RuntimeError("unexpected loop exit")  # pragma: no cover


def run_agent_iter(
    user_message: str,
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    messages: list[dict],
    model: str | None = None,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    cancel=None,
) -> Iterator[dict]:
    """Run one user turn, yielding an event per thing that happens.

    APPENDS TO `messages` IN PLACE — the caller keeps ownership. A consumer that
    disconnects mid-turn still leaves the completed rounds in the caller's list,
    which is what `serve` needs: the stage has already moved, so the history has
    to say so whether or not anyone was listening.

    `cancel` is an optional `threading.Event`, polled at round boundaries and
    before each tool dispatch — never mid-tool. `execute_tool` blocks in Java and
    there is no interrupting it, so Stop waits for the running tool to return.

    Events are JSON-encodable dicts discriminated on `type`: round_start,
    text_delta, tool_use, tool_result, retry, cancelled, done, error. The last
    three are terminal. See design/16 §2.
    """
    model = resolve_model(model)
    start = len(messages)
    messages.append({"role": "user", "content": user_message})
    system_blocks = _system_blocks()

    for iteration in range(max_iterations):
        if _cancelled(cancel):
            yield from _unwind_cancel(messages)
            return
        yield {"type": "round_start", "iteration": iteration}

        try:
            response = yield from _stream_one_round(messages, system_blocks, model)
        except _Overloaded:
            del messages[start:]  # discard the turn, user message and all
            yield {"type": "error", "message": OVERLOADED_MESSAGE}
            return
        except _BadModel as e:
            del messages[start:]  # nothing ran; don't leave an unanswered prompt
            yield {"type": "error",
                   "message": f"The API rejected the model '{model}': {e}"}
            return

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            text = next((b.text for b in response.content if hasattr(b, "text")), "")
            yield {"type": "done", "reply": text}
            return

        if response.stop_reason != "tool_use":
            yield {
                "type": "error",
                "message": f"[Unexpected stop reason: {response.stop_reason}]",
            }
            return

        tool_results = []
        stopped = False
        for block in response.content:
            if block.type != "tool_use":
                continue
            yield {
                "type": "tool_use",
                "id": block.id,
                "name": block.name,
                "input": block.input,
            }
            # Sticky: once the operator has stopped, the rest of this batch is
            # skipped. Every tool_use block still needs a matching tool_result or
            # the next request 400s, so the skipped ones get an error result.
            stopped = stopped or _cancelled(cancel)
            if stopped:
                result_json = CANCEL_RESULT
                result_block = {"type": "tool_result", "tool_use_id": block.id,
                                "is_error": True, "content": result_json}
            else:
                result_json = execute_tool(block.name, block.input, ctrl, guard)
                result_block = {"type": "tool_result", "tool_use_id": block.id,
                                "content": result_json}
            tool_results.append(result_block)
            yield {
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result_json,
                "is_error": stopped,
            }
        messages.append({"role": "user", "content": tool_results})
        if stopped:
            # The model sees "Cancelled by the operator" on every skipped tool,
            # which is exactly what it needs when the operator types "continue".
            yield {"type": "cancelled", "reason": CANCEL_REASON}
            return

    yield {
        "type": "error",
        "message": (
            f"Stopped after {max_iterations} tool rounds without completing. "
            "Progress so far is preserved in the conversation — say 'continue' to "
            "resume where this left off, or narrow the task."
        ),
    }


def run_agent(
    user_message: str,
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    history: list[dict] | None = None,
    model: str | None = None,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> tuple[str, list[dict]]:
    """Run one user turn through the agent loop.

    Returns (assistant_text_reply, updated_history).
    Pass history on repeated calls for multi-turn conversations. `max_iterations`
    caps the number of model/tool rounds so a runaway loop can't spin forever.

    A thin drain of `run_agent_iter` — every behaviour lives there, so the CLI,
    the tests and the streaming endpoint cannot diverge. Copies `history` first,
    so callers still get a fresh list back.
    """
    messages: list[dict[str, Any]] = list(history or [])
    reply = ""
    for event in run_agent_iter(
        user_message, ctrl, guard, messages, model, max_iterations
    ):
        if event["type"] == "done":
            reply = event["reply"]
        elif event["type"] == "error":
            reply = event["message"]
        elif event["type"] == "cancelled":
            reply = event["reason"]
        elif event["type"] == "retry":
            print(
                f"Anthropic API overloaded — retrying in {event['delay']}s "
                f"(attempt {event['attempt']}/{len(_RETRY_DELAYS)})..."
            )
    return reply, messages
