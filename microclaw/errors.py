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


_HARDWARE_HINT = (
    "This may be a hardware error (device busy, stage at limit, device not "
    "found) or a connection problem."
)


def hint_for_error(exc: Exception) -> str:
    """Return the hint that fits this exception, not a blanket hardware guess.

    A hint that names the wrong subsystem is worse than none: it points the
    model at the stage while the real fault is a filesystem path, and a model
    that trusts it will re-run the acquisition instead of fixing the path.

    Matches on the message as well as the type, because pycro-manager re-raises
    a hook's exception as a bare Exception ("exception in image processor:
    [Errno 2] No such file or directory") — the original class is gone by the
    time it reaches us.
    """
    text = str(exc)
    in_hook = "exception in image processor" in text
    where = (
        " It was raised inside the hook, mid-acquisition, so the stage has "
        "already moved and a partial dataset exists — fix the path before "
        "re-running, and do not treat the run as untouched."
        if in_hook else ""
    )
    if isinstance(exc, FileNotFoundError) or "No such file or directory" in text:
        return (
            "A path does not exist. microclaw creates save_dir and log_path "
            "parents itself, so this is usually a path pointing outside the "
            "workspace, a misspelled dataset directory, or a hook writing "
            "somewhere it was not given." + where
        )
    if isinstance(exc, PermissionError) or "Permission denied" in text:
        return "A path is not writable by this user." + where
    if isinstance(exc, (TypeError, ValueError)):
        return (
            "This is an argument error, not a hardware fault: a tool was called "
            "with a missing, extra, or wrong-typed parameter. Re-read the tool "
            "schema rather than retrying the same call."
        )
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return (
            "microclaw could not reach Micro-Manager. Check that MM is open and "
            "that Tools → Options → 'Run pycro-manager server on port 4827' is "
            "ticked."
        )
    if in_hook:
        return "The hook raised during acquisition." + where
    return _HARDWARE_HINT
