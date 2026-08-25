"""Error types and translation for microclaw.

SafetyViolation lives in safety.py to avoid circular imports.
This module documents the full error taxonomy used across the package.

Error sources:
- SafetyViolation (safety.py): tool call would violate a user-defined constraint
- Exception from pycromanager/MM (Java): device not found, stage at limit, ZMQ timeout
- ValueError / TypeError: agent called a tool with invalid arguments
- ConnectionError: MM not running or ZMQ server disabled
"""


class SetupRefusal(ValueError):
    """Restricted in-app setup cannot safely continue."""

# Known Java exception fragments → one-line actionable messages. A 16-line JVM
# stack trace teaches the model nothing and costs ~200 tokens per occurrence
# (design/14 §7). The "sequence acquisition" needle was verified to survive
# the ZMQ bridge intact.
_JAVA_HINTS = (
    (
        "sequence acquisition is running",
        "Micro-Manager reports a running sequence. Snap tools pause Live view "
        "automatically, so one snap retry is reasonable. For a multiposition or "
        "other acquisition, do not blindly retry: call stop_live_view, inspect "
        "camera/sequence state, and acquire again only after it is idle.",
    ),
    (
        "Device not found",
        "No such device label. Call list_devices() for the loaded labels.",
    ),
    (
        # Micro-Manager: 'Configuration group "X" or its preset "y" does not
        # exist'. Demo gate, 2026-08-14: a preset asked for as "20x" instead of
        # "20X" collected the blanket hardware hint, pointing the model at a
        # busy device and a stage at its limit for what was a casing typo.
        # Preset and group names are exact and case-sensitive.
        "or its preset",
        "No such config group or preset. Names are exact and case-sensitive — "
        "call list_config_groups() for the real ones rather than adjusting the "
        "hardware.",
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
    # Keep this module independent of authorization.py: tools.py imports this
    # module while authorization exceptions travel through the tool wrapper.
    # Exact class-name matching avoids turning that relationship into a circular
    # import.  Check the safe-state subtype before its partial-application parent.
    error_type = type(exc).__name__
    if error_type == "ChannelPlanSafeStateError":
        return (
            "Rollback failed, so the rig's state is unverified. Stop and surface "
            "this error to the operator; do not continue operating the rig or "
            "retry the plan."
        )
    if error_type == "ChannelPlanPartialApplicationError":
        return (
            "Some writes landed and rollback was attempted. Read the error's "
            "applied, attempted, and rolled-back pair lists to determine the "
            "resulting state; do not blindly retry the plan."
        )
    if error_type == "ChannelPlanError":
        return (
            "No write reached the device, so no channel change was made and the "
            "rig's state is not in question. This is a device or link failure — "
            "diagnose the device named in the message before retrying the plan."
        )
    if error_type == "SetupRefusal":
        # A setup refusal is a decision about session state — the capability was
        # never granted, is already spent, the draft is incomplete, or the
        # operator declined. It subclasses ValueError, so without this branch it
        # collected the argument-error hint below and told the model to re-read
        # the tool schema and fix its parameters. The M5 gate saw exactly that on
        # the replay refusal (2026-08-14): the remedy it named was the one thing
        # that could not possibly help.
        return (
            "This is a setup-state decision, not an argument error and not a "
            "hardware fault: no change to the call's parameters would alter it. "
            "The refusal names what has to happen next — usually restarting "
            "Microclaw, or completing the draft — and it is never a different "
            "set of arguments."
        )
    if error_type == "RigAuthorizationError":
        return (
            "This is an authorization decision, not a hardware fault. The write "
            "was refused by the rig's authorization map or declined by the "
            "operator, so no hardware diagnosis is warranted. Retrying the "
            "identical call will fail identically; the profile or the operator's "
            "answer must change first."
        )
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
    # Both are OSError siblings rather than FileNotFoundError subclasses, so
    # they fell past the path branch above and collected the hardware hint on a
    # tool that touches no hardware (block 43e's M5 gate, both on the first two
    # calls of the session).
    if isinstance(exc, NotADirectoryError) or "directory name is invalid" in text.lower():
        return (
            "A path that must be a directory is a file. An NDTiff dataset IS a "
            "directory — pass the dataset directory, not a .tif or .tiff inside "
            "or beside it." + where
        )
    if isinstance(exc, FileExistsError):
        return (
            "An output directory already exists. Offline analysis refuses to "
            "write into one, so a previous analysis is never silently "
            "overwritten or mixed with a new one. Choose a new output_dir." + where
        )
    # Micro-Manager's unknown-group/preset error. Kept beside the message
    # rewrite in _JAVA_HINTS deliberately: that table only replaces the error
    # text, so without this branch the tool returns an actionable message and a
    # hint contradicting it with a hardware guess. Demo gate, 2026-08-14.
    if "or its preset" in text and "does not exist" in text:
        return (
            "This is a naming error, not a hardware fault. Config group and "
            "preset names are exact and case-sensitive; list_config_groups() "
            "returns the real ones. Nothing on the rig needs diagnosing, and "
            "the same call will fail identically."
        )
    if isinstance(exc, (TypeError, ValueError)):
        # "Re-read the tool schema" is useless when the missing values are an
        # adapter's constructor arguments: the schema names the tool's own
        # parameters, not the adapter's. A demo-machine session spent four calls
        # and two abandoned output directories putting them in
        # model_project_config, which is right beside it and looks like where a
        # project path belongs. Say where they go.
        if "__init__() missing" in text and "positional argument" in text:
            return (
                "An offline adapter was constructed without its required "
                "arguments. Adapter constructor arguments go in the tool's "
                "`parameters` object — not in `model_project_config`, which "
                "only records provenance. The error names every argument the "
                "adapter still needs."
            )
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
    if isinstance(exc, KeyError):
        return (
            "This is a lookup error, not a hardware fault. Check the requested "
            "name against the available names in the tool result."
        )
    if in_hook:
        return "The hook raised during acquisition." + where
    return _HARDWARE_HINT
