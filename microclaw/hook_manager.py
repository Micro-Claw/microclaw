from __future__ import annotations
import ast
import hashlib
import importlib.util
import inspect
import json
from pathlib import Path
from typing import Any

HOOKS_DIR = Path.home() / ".microclaw" / "hooks"
MANIFEST = HOOKS_DIR / "manifest.json"

# Every hook_params key _resolve_hook strips before constructing a saved hook,
# and the exact list describe_hook reports as stripped. ONE definition: Block 7b
# grew this from eight names to eighteen, and a second copy would have had
# describe_hook calling `out_path` an accepted parameter while _resolve_hook
# dropped it.
FORBIDDEN_SAVED_HOOK_PARAMS = (
    "ctrl", "guard", "credentials", "candidates", "progress",
    "survey_events", "event_queue", "log_path", "illumination_envelope",
    "illumination_device", "illumination_property", "device", "property",
    "path", "out_path", "output_path", "save_dir", "artifact_dir",
)

# Modules whose import or use is worth flagging for a human to review. Broad on
# purpose (os/open catch benign log-writing hooks too), because this is an
# ADVISORY lint, not a gate — see lint_hook_code.
_BANNED_MODULES = {
    "anthropic", "ctypes", "httpx", "importlib", "os", "requests",
    "shutil", "socket", "subprocess", "urllib",
}
_BANNED_NAMES = {"eval", "exec", "compile", "__import__", "open"}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _legacy_text_sha256(data: bytes) -> str | None:
    """Return the pre-byte-pinning digest after universal-newline decoding."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return _sha256(normalized.encode("utf-8"))


def verify_saved_hook_bytes(name: str, entry: dict[str, Any]) -> bytes:
    """Read and verify the exact saved artifact bytes against its consent pin."""
    source = Path(entry["path"]).read_bytes()
    if "sha256" not in entry:
        raise RuntimeError(
            f"Hook '{name}' predates hash-pinning; re-save it via "
            "generate_and_save_hook to record a hash before running."
        )
    if _sha256(source) != entry["sha256"]:
        if _legacy_text_sha256(source) == entry["sha256"]:
            raise RuntimeError(
                f"Hook '{name}' uses a legacy newline-normalized hash that does "
                "not pin its on-disk bytes. Review and re-save it via "
                "generate_and_save_hook before running."
            )
        raise RuntimeError(
            f"Hook '{name}' changed on disk since it was saved; refusing to load."
        )
    return source


def lint_hook_code(code: str) -> list[str]:
    """Return advisory warnings for patterns worth a human's attention.

    ADVISORY ONLY — not a security boundary. A denylist AST scan cannot be
    complete (getattr, string-built imports, C extensions all evade it), so this
    exists to *surface* patterns for the human reviewing the full code, which is
    the actual gate. It resolves import aliases (import os as o) so trivial
    renames don't hide a flagged module.
    """
    warnings: list[str] = []
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [f"Syntax error: {e}"]

    aliases: dict[str, str] = {}          # local name -> real module
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                aliases[a.asname or a.name] = a.name
                if a.name.split(".")[0] in _BANNED_MODULES:
                    warnings.append(f"Imports flagged module: {a.name}")
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] in _BANNED_MODULES:
                warnings.append(f"Imports from flagged module: {node.module}")
        elif isinstance(node, ast.Name) and node.id in _BANNED_NAMES:
            warnings.append(f"Flagged name: {node.id}")
        elif isinstance(node, ast.Attribute):
            base = getattr(node.value, "id", None)
            real = aliases.get(base, base)
            if real in _BANNED_MODULES:
                warnings.append(f"Access to flagged module: {real}.{node.attr}")

    return warnings


def _hook_contract_analysis(code: str) -> tuple[list[str], bool]:
    """Statically reject hook source that cannot satisfy the runner contract.

    This deliberately does not import or execute the source: without an actual
    OS sandbox, doing so would turn validation into arbitrary code execution.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [f"Syntax error: {e}"], False
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
    hooks = [
        node for node in classes
        if any(isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
               and item.name in {"analyze_frame", "image_process_fn"} for item in node.body)
    ]
    if not hooks:
        return [
            "No top-level class defines analyze_frame(self, image, metadata) or "
            "image_process_fn(self, image, metadata, event_queue)."
        ], False
    errors: list[str] = []
    for cls in hooks:
        fn = next(item for item in cls.body if getattr(item, "name", None) in
                  {"analyze_frame", "image_process_fn"})
        positional = len(fn.args.posonlyargs) + len(fn.args.args)
        required = 3 if fn.name == "analyze_frame" else 4
        if positional < required and fn.args.vararg is None:
            errors.append(
                f"{cls.name}.{fn.name} must accept " +
                ("self, image, and metadata." if fn.name == "analyze_frame" else
                 "self, image, metadata, and event_queue.")
            )
    from microclaw import hook_decisions

    action_types = {
        cls.__name__: cls for cls in hook_decisions._ACTION_TYPES.values()
    }
    decision_names = set(action_types) | {"HookResult"}
    module_bindings: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            module_bindings.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            module_bindings.update(
                target.id for root in targets for target in ast.walk(root)
                if isinstance(target, ast.Name) and isinstance(target.ctx, ast.Store)
            )
    imports_all_decisions = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            module_bindings.update(alias.asname or alias.name.split(".")[0]
                                   for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module == "microclaw.hook_decisions" and any(
                alias.name == "*" for alias in node.names
            ):
                imports_all_decisions = True
            module_bindings.update(
                alias.asname or alias.name for alias in node.names
                if alias.name != "*"
            )

    def provably_string(node: ast.expr) -> bool:
        return (
            isinstance(node, ast.Constant) and isinstance(node.value, str)
            or isinstance(node, ast.JoinedStr)
            or (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "str")
        )

    can_emit_artifacts = False
    missing_decision_names: set[str] = set()
    for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
        name = call.func.id if isinstance(call.func, ast.Name) else None
        if (name in decision_names and name not in module_bindings
                and not imports_all_decisions and name not in missing_decision_names):
            errors.append(
                f"{name} is called but is not imported or defined. Add: "
                f"from microclaw.hook_decisions import {name}"
            )
            missing_decision_names.add(name)
        if name == "EmitArtifact":
            can_emit_artifacts = True
        cls = action_types.get(name)
        if cls is None:
            continue
        signature = inspect.signature(cls)
        parameters = list(signature.parameters.values())
        positional = [p for p in parameters if p.kind in (
            inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD
        )]
        parameter_names = set(signature.parameters)
        keyword_names = [keyword.arg for keyword in call.keywords if keyword.arg is not None]
        has_dynamic_keywords = any(keyword.arg is None for keyword in call.keywords)
        if len(call.args) > len(positional):
            errors.append(
                f"{name} accepts at most {len(positional)} positional arguments; "
                f"signature is {name}{signature}."
            )
            continue
        unknown = sorted(set(keyword_names) - parameter_names)
        duplicates = sorted(set(keyword_names) & {p.name for p in positional[:len(call.args)]})
        if unknown:
            errors.append(f"{name} has unknown keyword arguments {unknown}; signature is {name}{signature}.")
        if duplicates:
            errors.append(f"{name} supplies {duplicates} both positionally and by keyword.")
        if not has_dynamic_keywords:
            supplied = {p.name for p in positional[:len(call.args)]} | set(keyword_names)
            missing = [
                p.name for p in parameters
                if p.default is inspect.Parameter.empty
                and p.kind not in (inspect.Parameter.VAR_POSITIONAL,
                                   inspect.Parameter.VAR_KEYWORD)
                and p.name not in supplied
            ]
            if missing:
                errors.append(
                    f"{name} is missing required arguments {missing}; "
                    f"signature is {name}{signature}."
                )
        # EmitArtifact's two fields have deliberately easy-to-swap types. With
        # two positional arguments, accept only a filename that is statically
        # certain to be a string. Dynamic values use the documented keyword form.
        if name == "EmitArtifact" and len(call.args) == 2 and not provably_string(call.args[0]):
            errors.append(
                "EmitArtifact's first positional argument is not provably a string. "
                "Use EmitArtifact(filename=<bare filename>, payload=<bytes or ndarray>). "
                f"Runtime signature: EmitArtifact{signature}."
            )
    return errors, can_emit_artifacts


def validate_hook_contract(
    candidate: str | object, required_callback: str | None = None
) -> list[str]:
    """Validate the callback contract used by both save preflight and runners."""
    if isinstance(candidate, str):
        errors = _hook_contract_analysis(candidate)[0]
        if errors or required_callback is None:
            return errors
        tree = ast.parse(candidate)
        classes = sorted(
            (node for node in tree.body if isinstance(node, ast.ClassDef)),
            key=lambda node: node.name,
        )
        selected = next(node for node in classes if any(
            isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            and item.name in {"analyze_frame", "image_process_fn"}
            for item in node.body
        ))
        callbacks = {
            item.name for item in selected.body
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        if required_callback not in callbacks:
            return [f"This runner requires {required_callback}; the hook does not define it."]
        return []
    target = getattr(candidate, "hook", candidate)
    if required_callback and not callable(getattr(target, required_callback, None)):
        return [f"This runner requires {required_callback}; the hook does not define it."]
    return []


def hook_source_can_emit_artifacts(code: str) -> bool:
    """Whether saved source statically constructs an ``EmitArtifact`` action."""
    return _hook_contract_analysis(code)[1]


# Back-compat alias: the tool layer and older callers referenced this name. It
# is an advisory lint, not validation — prefer lint_hook_code in new code.
validate_hook_code = lint_hook_code


def save_hook(name: str, code: str, description: str, source: str) -> None:
    """Save validated hook code to disk and update manifest.

    source must be 'claude_generated' or 'user_provided'.
    """
    HOOKS_DIR.mkdir(parents=True, exist_ok=True)
    hook_path = HOOKS_DIR / f"{name}.py"
    source_bytes = code.encode("utf-8")
    hook_path.write_bytes(source_bytes)

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {}
    manifest[name] = {
        "description": description,
        "path": str(hook_path),
        "source": source,
        # Pin the exact bytes the user confirmed, plus the advisory warnings they
        # saw and accepted, so load_hook_class can detect on-disk tampering
        # (TOCTOU) and re-surface any *new* warnings from a broadened lint.
        "sha256": _sha256(source_bytes),
        "accepted_warnings": sorted(lint_hook_code(code)),
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def read_hook_from_file(path: str) -> tuple[str, list[str]]:
    """Read a user-provided hook file and run the advisory lint.

    Returns (code, warnings). Does NOT save — caller must call save_hook after
    user confirmation.
    """
    code = Path(path).read_text(encoding="utf-8")
    warnings = lint_hook_code(code)
    return code, warnings


def select_hook_class(module, verbs):
    """Select the first alphabetically named, module-defined hook class."""
    for name in sorted(vars(module)):
        cls = vars(module)[name]
        if (isinstance(cls, type) and cls.__module__ == module.__name__
                and any(callable(getattr(cls, verb, None)) for verb in verbs)):
            return cls
    return None


def load_hook_class(name: str):
    """Dynamically import a saved hook and return its class.

    Verifies the on-disk bytes against the hash pinned at save time (refusing a
    hook edited after the user confirmed it) and refuses legacy unpinned entries.
    """
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {}
    if name not in manifest:
        raise KeyError(f"No saved hook named '{name}'.")
    entry = manifest[name]
    source = verify_saved_hook_bytes(name, entry)
    code = source.decode("utf-8")
    contract_errors, can_emit_artifacts = _hook_contract_analysis(code)
    if contract_errors:
        raise ValueError(
            f"Saved hook '{name}' violates the current hook contract: "
            f"{contract_errors}. Review and re-save corrected source before running it."
        )
    new_warnings = set(lint_hook_code(code)) - set(entry.get("accepted_warnings", []))
    if new_warnings:
        raise RuntimeError(
            f"Hook '{name}' has lint warnings the user never accepted: "
            f"{sorted(new_warnings)}. Re-save it to review and accept them."
        )
    spec = importlib.util.spec_from_file_location(name, entry["path"])
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    cls = select_hook_class(mod, ("analyze_frame", "image_process_fn"))
    if cls is not None:
        # Derived from source before import, never by probing untrusted behavior.
        cls.can_emit_artifacts = can_emit_artifacts
        return cls
    raise AttributeError(
        f"No class with analyze_frame or image_process_fn found in hook '{name}'."
    )


def _render_ast_default(node: ast.expr) -> str:
    """Render a constructor default without evaluating executable expressions."""
    try:
        return repr(ast.literal_eval(node))
    except (ValueError, TypeError):
        return ast.unparse(node)


def _ast_constructor_parameters(cls: ast.ClassDef) -> list[dict[str, Any]]:
    init = next(
        (node for node in cls.body
         if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
         and node.name == "__init__"),
        None,
    )
    if init is None:
        return []

    positional = [*init.args.posonlyargs, *init.args.args]
    defaults: list[ast.expr | None] = [None] * (
        len(positional) - len(init.args.defaults)
    ) + list(init.args.defaults)
    parameters: list[dict[str, Any]] = []
    for arg, default in zip(positional, defaults):
        if arg.arg == "self":
            continue
        item: dict[str, Any] = {"name": arg.arg, "required": default is None}
        if default is not None:
            item["default"] = _render_ast_default(default)
        parameters.append(item)

    for arg, default in zip(init.args.kwonlyargs, init.args.kw_defaults):
        item = {"name": arg.arg, "required": default is None}
        if default is not None:
            item["default"] = _render_ast_default(default)
        parameters.append(item)
    if init.args.vararg:
        parameters.append({"name": init.args.vararg.arg, "required": False})
    if init.args.kwarg:
        parameters.append({"name": init.args.kwarg.arg, "required": False})
    return parameters


def _hookbase_aliases(tree: ast.Module) -> set[str]:
    aliases = {"HookBase"}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "microclaw.hooks":
            aliases.update(
                item.asname or item.name for item in node.names
                if item.name == "HookBase"
            )
    return aliases


_SOURCE_REFUSAL_NOTE = (
    "Re-review alone will not make this hook usable. The reasons listed in "
    "insufficient_for are properties of the source, not of its pin, so "
    "re-saving the same source reproduces them. The source has to change "
    "first: a saved hook must not inherit HookBase, must not take log_path, "
    "and provides analyze_frame(image, metadata) returning a HookResult."
)


def saved_hook_source_refusal(code: str) -> dict[str, Any]:
    """Return the resolve-time hard refusals that are properties of source."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return {"reasons": []}
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
    cls = next((
        node for node in classes
        if any(isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
               and item.name in {"analyze_frame", "image_process_fn"}
               for item in node.body)
    ), None)
    if cls is None:
        return {"reasons": []}
    parameter_names = {
        item["name"] for item in _ast_constructor_parameters(cls)
    }
    aliases = _hookbase_aliases(tree)
    hookbase_subclass = any(
        (isinstance(base, ast.Name) and base.id in aliases)
        or (isinstance(base, ast.Attribute) and base.attr == "HookBase")
        for base in cls.bases
    )
    reasons = []
    if hookbase_subclass:
        reasons.append("saved hook subclasses HookBase")
    if "log_path" in parameter_names:
        reasons.append("saved hook constructor takes log_path")
    return {
        "reasons": reasons,
        **({"insufficient_for": reasons, "note": _SOURCE_REFUSAL_NOTE}
           if reasons else {}),
    }


def describe_saved_hook(name: str) -> dict[str, Any]:
    """Describe saved hook source using AST only; never import or execute it.

    Description deliberately remains available when the file hash differs from
    its manifest pin, or when a legacy manifest has no pin. Refusing to *run*
    changed or unpinned source is protective; refusing to *read and describe*
    it would hide the change an operator needs to inspect. Integrity state and
    both hashes are therefore returned prominently instead of gating the read.
    """
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {}
    if name not in manifest:
        return {"error": f"No saved hook named '{name}'."}
    entry = manifest[name]
    path = Path(entry["path"])
    provenance: dict[str, Any] = {
        "path": str(path),
        "source": entry.get("source"),
        "accepted_warnings": entry.get("accepted_warnings", []),
        "manifest_sha256": entry.get("sha256"),
    }
    try:
        raw = path.read_bytes()
        code = raw.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        return {
            "error": f"Could not read saved hook '{name}': {exc}",
            "name": name,
            "kind": "saved",
            "provenance": {**provenance, "actual_sha256": None,
                           "matches_manifest": False},
        }
    actual_sha256 = _sha256(raw)
    legacy_newline_pin = (
        entry.get("sha256") is not None
        and entry["sha256"] != actual_sha256
        and _legacy_text_sha256(raw) == entry["sha256"]
    )
    provenance.update({
        "actual_sha256": actual_sha256,
        "legacy_newline_pin": legacy_newline_pin,
        "matches_manifest": (
            entry.get("sha256") is not None
            and entry["sha256"] == actual_sha256
        ),
    })
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return {
            "error": f"Could not parse saved hook '{name}': {exc}",
            "name": name,
            "kind": "saved",
            "provenance": provenance,
        }

    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
    cls = next((
        node for node in classes
        if any(isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
               and item.name in {"analyze_frame", "image_process_fn"}
               for item in node.body)
    ), None)
    if cls is None:
        return {
            "error": (
                f"No top-level class defines analyze_frame or image_process_fn "
                f"in saved hook '{name}'."
            ),
            "name": name,
            "kind": "saved",
            "provenance": provenance,
        }

    callback = (
        "analyze_frame"
        if any(getattr(node, "name", None) == "analyze_frame" for node in cls.body)
        else "image_process_fn"
    )
    parameters = _ast_constructor_parameters(cls)
    parameter_names = {item["name"] for item in parameters}
    refusal_reasons = []
    if entry.get("sha256") is None:
        refusal_reasons.append("saved hook has no manifest sha256 pin")
    elif legacy_newline_pin:
        refusal_reasons.append(
            "saved hook uses a legacy newline-normalized hash; review and re-save it"
        )
    elif entry["sha256"] != actual_sha256:
        refusal_reasons.append("saved hook file sha256 does not match manifest")
    # Two kinds of refusal, and re-review clears only the first. A pin reason is
    # about this file's provenance; the reasons below are properties of the
    # source itself, and re-saving the same bytes reproduces them exactly.
    unpinned_only = list(refusal_reasons)
    source_refusal = saved_hook_source_refusal(code)
    refusal_reasons.extend(source_refusal["reasons"])
    contract_errors, can_emit_artifacts = _hook_contract_analysis(code)
    refusal_reasons.extend(
        f"current hook contract violation: {error}" for error in contract_errors
    )
    source_reasons = refusal_reasons[len(unpinned_only):]
    stripped = [
        parameter for parameter in FORBIDDEN_SAVED_HOOK_PARAMS
        if parameter in parameter_names
    ]
    remedy = None
    if refusal_reasons:
        remedy = {
            "tool": "read_hook_from_file",
            "path": str(path),
            "then": "generate_and_save_hook(source='user_provided')",
            "reexposes": False,
        }
        if source_reasons:
            # Unlike save-time source_refusal, this list also includes contract
            # errors found while describing already-saved bytes. Both use the
            # same note because neither kind is cleared by re-saving unchanged
            # source.
            # The remedy alone is a false promise here, and a reader who ranks
            # the reasons by eye gets it backwards: the demo gate of 2026-08-11
            # saw the agent call these two "just describing its structure, not
            # faults" and offer a re-review that could not have worked.
            remedy["insufficient_for"] = source_reasons
            remedy["note"] = _SOURCE_REFUSAL_NOTE
    return {
        "name": name,
        "kind": "saved",
        "class_name": cls.name,
        "class_docstring": ast.get_docstring(cls, clean=True),
        "constructor_parameters": parameters,
        "callback": callback,
        "can_emit_artifacts": can_emit_artifacts,
        "resolve_refusal": {
            "would_refuse": bool(refusal_reasons),
            "reasons": refusal_reasons,
            **({"remedy": remedy} if remedy else {}),
        },
        "parameter_handling": {"stripped": stripped, "injected": []},
        "provenance": provenance,
    }


def list_saved_hooks() -> dict[str, dict]:
    """Return manifest entries for all saved hooks."""
    if not MANIFEST.exists():
        return {}
    return {
        k: {"description": v["description"], "source": v["source"]}
        for k, v in json.loads(MANIFEST.read_text(encoding="utf-8")).items()
    }
