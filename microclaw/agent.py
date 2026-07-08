from __future__ import annotations
from typing import Any
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


def _get_client() -> anthropic.Anthropic:
    """Lazily construct the Anthropic client so importing this module has no
    side effects and tests can inject their own client."""
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def resolve_model(model: str | None = None) -> str:
    """Pick the model: explicit arg > MICROCLAW_MODEL env > DEFAULT_MODEL."""
    return model or os.environ.get(MODEL_ENV) or DEFAULT_MODEL

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

Illumination safety:
- Illumination is the only irreversible thing you control: it bleaches sample and endangers eyes. Shutter the excitation before any user action described as manual, physical, or "I will now ..." (swapping optics, touching the stage), and before any long non-imaging operation.
- Never raise laser power without stating the before/after values in the same message. Step power up gradually — never jump by a large factor in one write.
- Do NOT ask permission for reversible bookkeeping (mark_position, get_*, set_roi). DO ask, and wait for a reply, before enabling illumination, raising power, moving Z on an unverified focus metric, or overwriting a dataset.
- At the end of a task involving lasers, confirm every laser you enabled is off; do not just mention turning it off.

Device property discovery:
- When the user references a device whose properties you do not know, call list_device_properties(device) to enumerate them, then get_device_property_info(device, property) on the specific property to learn its type, allowed values, and numeric limits before calling set_device_property.
- Do not attempt to set a property whose get_device_property_info result shows read_only=true or pre_init=true — explain the limitation to the user instead.
- Use get_full_device_state(device) when the user asks for a complete overview of a device's current settings.

Image analysis:
- Use snap_and_analyze when you need to see or assess an image interactively. It displays the snap in the MM viewer by default (displayed_in_mm_viewer in the payload says whether the user can see it — never claim an image is on screen unless it is true). The focus_metric and intensity stats are in the text block; the thumbnail is for visual context and confirmation.
- focus_metric is comparable ONLY between snaps whose metric_valid_for blocks are identical. Never compare it across an ROI, exposure, or binning change, and never read a rising focus_metric as improving image quality after changing illumination.
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
- Never skip the pre-acquisition checklist from the reference (buffer, channel,
  TIRF mode, focus lock, fiducials). Ask the user to confirm each point.

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
  1. Tell the user that no pre-coded hook covers this behaviour.
  2. Ask: "Do you have an existing hook file you'd like to use, or would you like me to write one?"
  3a. If the user provides a file path: call read_hook_from_file(path) to read it and run the advisory lint. Display the full code and any lint warnings to the user. The lint is advisory only — it flags patterns (imports, eval/open, etc.) for review and can be evaded; the human reading the full code is the actual gate, and benign hooks may legitimately trip it (e.g. writing their own log via open). Ask for explicit confirmation before saving. On confirmation, call generate_and_save_hook(source='user_provided').
  3b. If the user asks you to write one: call get_hook_documentation first, then write a hook that conforms to the API reference it returns. Show the full code and any lint warnings, wait for explicit confirmation, then call generate_and_save_hook(source='claude_generated').
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
    """
    model = resolve_model(model)
    messages: list[dict[str, Any]] = list(history or [])
    messages.append({"role": "user", "content": user_message})

    system_blocks: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }
    ]
    kb_text = format_for_prompt(load_knowledge())
    if kb_text:
        system_blocks.append(
            {"type": "text", "text": kb_text, "cache_control": {"type": "ephemeral"}}
        )

    _RETRY_DELAYS = (5, 15, 30)

    for _iteration in range(max_iterations):
        for attempt, delay in enumerate([0] + list(_RETRY_DELAYS)):
            if delay:
                print(
                    f"Anthropic API overloaded — retrying in {delay}s "
                    f"(attempt {attempt}/{len(_RETRY_DELAYS)})..."
                )
                time.sleep(delay)
            try:
                response = _get_client().messages.create(
                    model=model,
                    max_tokens=4096,
                    system=system_blocks,
                    tools=TOOLS_CACHED,
                    messages=_with_cache_breakpoint(messages),
                )
                break
            except anthropic._exceptions.OverloadedError:
                if attempt == len(_RETRY_DELAYS):
                    return (
                        "The Anthropic API is currently overloaded (HTTP 529). "
                        "Please try again in a few minutes.",
                        list(history or []),
                    )
        else:
            # unreachable — satisfied by the return inside the except above
            raise RuntimeError("unexpected loop exit")

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            text = next(
                (b.text for b in response.content if hasattr(b, "text")), ""
            )
            return text, messages

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result_json = execute_tool(
                        block.name, block.input, ctrl, guard
                    )
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result_json,
                        }
                    )
            messages.append({"role": "user", "content": tool_results})
            continue

        return f"[Unexpected stop reason: {response.stop_reason}]", messages

    return (
        f"Stopped after {max_iterations} tool rounds without completing. "
        "Progress so far is preserved in the conversation — say 'continue' to "
        "resume where this left off, or narrow the task.",
        messages,
    )
