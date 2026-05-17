from __future__ import annotations
from typing import Any

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

    while True:
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
