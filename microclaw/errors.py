"""Error types and translation for microclaw.

SafetyViolation lives in safety.py to avoid circular imports.
This module documents the full error taxonomy used across the package.

Error sources:
- SafetyViolation (safety.py): tool call would violate a user-defined constraint
- Exception from pycromanager/MM (Java): device not found, stage at limit, ZMQ timeout
- ValueError / TypeError: agent called a tool with invalid arguments
- ConnectionError: MM not running or ZMQ server disabled
"""

# Known Java exception fragments → one-line actionable messages. A 16-line JVM
# stack trace teaches the model nothing and costs ~200 tokens per occurrence
# (design/14 §7). The "sequence acquisition" needle was verified to survive
# the ZMQ bridge intact.
_JAVA_HINTS = (
    (
        "sequence acquisition is running",
        "Live view is running; a snap requires it stopped. microclaw pauses "
        "live automatically in snap tools — retry the call, or call "
        "stop_live_view first.",
    ),
    (
        "Device not found",
        "No such device label. Call list_devices() for the loaded labels.",
    ),
)


def humanize_java_error(exc: Exception) -> str:
    """Map a pycromanager/Java exception onto a one-line actionable message.

    Unknown exceptions are trimmed to their first line so a Java stack trace
    never lands in the model's context verbatim.
    """
    text = str(exc)
    for needle, hint in _JAVA_HINTS:
        if needle in text:
            return hint
    first_line = text.splitlines()[0].strip() if text.strip() else ""
    return first_line or repr(exc)
