from __future__ import annotations
from typing import Any
import time

import anthropic

from microclaw.controller import MicroscopeController
from microclaw.safety import SafetyGuard
from microclaw.tools import execute_tool
from microclaw.tools_schema import TOOLS_CACHED

client = anthropic.Anthropic()
MODEL = "claude-opus-4-7"

SYSTEM_PROMPT = """You are Microclaw, an AI assistant that controls a Micro-Manager fluorescence microscope.

The biologist is watching the Micro-Manager GUI. Every tool call you make is immediately reflected there: images appear in the viewer, the stage position display updates, acquisitions play out in the acquisition window.

Guidelines:
- Before executing a multi-step protocol, call get_system_state to orient yourself.
- If the user's request is ambiguous (e.g. "run a z-stack" without specifying range), ask one focused clarifying question rather than guessing.
- After each tool call, briefly describe what happened in plain language (e.g., "I moved the stage to Z=50 µm").
- If a tool returns an error, explain it plainly and suggest what to try next. Never retry with the same out-of-range parameters.
- If a safety constraint blocks an action, clearly tell the user which limit was hit and what the allowed range is.
- Available acquisition outputs are pycro-manager datasets (NDTiff). Use export_dataset_as_tiff to convert to standard TIFF when the user requests it.
- Never call set_device_property for core operations that have dedicated tools (stage, channel, exposure).

Image analysis:
- Use snap_and_analyze when you need to see or assess an image interactively. The focus_metric (Laplacian variance) and intensity stats are in the text block; the thumbnail is for visual context and confirmation.
- Prefer numerical metrics from hooks over your own visual assessment for quantitative decisions (focus quality, cell presence, intensity).
- If the image appears blurry, suggest run_autofocus to the user — do not call it automatically unless the user has explicitly asked you to.
- Never over-interpret a single image; recommend re-imaging or a wider survey if you are uncertain.

Position lists:
- Position lists are stored in MM's native format and visible in the MM GUI. Use mark_position after the biologist has navigated to a site of interest.
- Use save_position_list / load_position_list to persist positions across sessions.
- Use run_multiposition_with_autofocus for automated surveys — do not manually loop over go_to_position unless the user explicitly asks for it.

Autofocus:
- run_autofocus (standalone) is for interactive focus requests.
- run_adaptive_acquisition with hook_strategy='autofocus_per_position' is for automated surveys where each stored image must be in focus.
- Default parameters for a 20× objective: z_range_um=20, z_step_um=0.5. Widen z_range_um if the warning says the peak was at the boundary.

Hook-based adaptive acquisition:
- Pre-coded hooks: autofocus_per_position, focus_feedback, intensity_adaptive, position_filter.
- Saved hooks: call list_hooks() to see pre-coded and previously saved hooks. The result shows each saved hook's source ('claude_generated' or 'user_provided').
- After an adaptive acquisition, call read_hook_log(log_path) to get per-position or per-frame results, then synthesize and report them to the user.
- When no pre-coded hook matches a request:
  1. Tell the user that no pre-coded hook covers this behaviour.
  2. Ask: "Do you have an existing hook file you'd like to use, or would you like me to write one?"
  3a. If the user provides a file path: call read_hook_from_file(path) to read and AST-scan it. Display the full code and any warnings to the user. Ask for explicit confirmation before saving. On confirmation, call generate_and_save_hook(source='user_provided').
  3b. If the user asks you to write one: follow the hook template (class with image_process_fn), show the full code and any warnings, wait for explicit confirmation, then call generate_and_save_hook(source='claude_generated').
- Never save or run a hook (generated or provided) without explicit user confirmation.
"""


def run_agent(
    user_message: str,
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    history: list[dict] | None = None,
) -> tuple[str, list[dict]]:
    """Run one user turn through the agent loop.

    Returns (assistant_text_reply, updated_history).
    Pass history on repeated calls for multi-turn conversations.
    """
    messages: list[dict[str, Any]] = list(history or [])
    messages.append({"role": "user", "content": user_message})

    _RETRY_DELAYS = (5, 15, 30)

    while True:
        for attempt, delay in enumerate([0] + list(_RETRY_DELAYS)):
            if delay:
                print(
                    f"Anthropic API overloaded — retrying in {delay}s "
                    f"(attempt {attempt}/{len(_RETRY_DELAYS)})..."
                )
                time.sleep(delay)
            try:
                response = client.messages.create(
                    model=MODEL,
                    max_tokens=4096,
                    system=[
                        {
                            "type": "text",
                            "text": SYSTEM_PROMPT,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                    tools=TOOLS_CACHED,
                    messages=messages,
                )
                break
            except anthropic.OverloadedError:
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
