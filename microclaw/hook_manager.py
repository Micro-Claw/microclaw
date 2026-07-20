from __future__ import annotations
import ast
import hashlib
import importlib.util
import json
from pathlib import Path

HOOKS_DIR = Path.home() / ".microclaw" / "hooks"
MANIFEST = HOOKS_DIR / "manifest.json"

# Modules whose import or use is worth flagging for a human to review. Broad on
# purpose (os/open catch benign log-writing hooks too), because this is an
# ADVISORY lint, not a gate — see lint_hook_code.
_BANNED_MODULES = {
    "anthropic", "ctypes", "httpx", "importlib", "os", "requests",
    "shutil", "socket", "subprocess", "urllib",
}
_BANNED_NAMES = {"eval", "exec", "compile", "__import__", "open"}


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


# Back-compat alias: the tool layer and older callers referenced this name. It
# is an advisory lint, not validation — prefer lint_hook_code in new code.
validate_hook_code = lint_hook_code


def save_hook(name: str, code: str, description: str, source: str) -> None:
    """Save validated hook code to disk and update manifest.

    source must be 'claude_generated' or 'user_provided'.
    """
    HOOKS_DIR.mkdir(parents=True, exist_ok=True)
    hook_path = HOOKS_DIR / f"{name}.py"
    hook_path.write_text(code, encoding="utf-8")

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {}
    manifest[name] = {
        "description": description,
        "path": str(hook_path),
        "source": source,
        # Pin the exact bytes the user confirmed, plus the advisory warnings they
        # saw and accepted, so load_hook_class can detect on-disk tampering
        # (TOCTOU) and re-surface any *new* warnings from a broadened lint.
        "sha256": hashlib.sha256(code.encode()).hexdigest(),
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


def load_hook_class(name: str):
    """Dynamically import a saved hook and return its class.

    Verifies the on-disk bytes against the hash pinned at save time (refusing a
    hook edited after the user confirmed it) and refuses legacy unpinned entries.
    """
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {}
    if name not in manifest:
        raise KeyError(f"No saved hook named '{name}'.")
    entry = manifest[name]
    code = Path(entry["path"]).read_text(encoding="utf-8")
    if "sha256" not in entry:
        raise RuntimeError(
            f"Hook '{name}' predates hash-pinning; re-save it via "
            "generate_and_save_hook to record a hash before running."
        )
    if hashlib.sha256(code.encode()).hexdigest() != entry["sha256"]:
        raise RuntimeError(
            f"Hook '{name}' changed on disk since it was saved; refusing to load."
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
    for attr in dir(mod):
        cls = getattr(mod, attr)
        if isinstance(cls, type) and hasattr(cls, "image_process_fn"):
            return cls
    raise AttributeError(f"No class with image_process_fn found in hook '{name}'.")


def list_saved_hooks() -> dict[str, dict]:
    """Return manifest entries for all saved hooks."""
    if not MANIFEST.exists():
        return {}
    return {
        k: {"description": v["description"], "source": v["source"]}
        for k, v in json.loads(MANIFEST.read_text(encoding="utf-8")).items()
    }
