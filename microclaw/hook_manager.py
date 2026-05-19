from __future__ import annotations
import ast
import importlib.util
import json
from pathlib import Path

HOOKS_DIR = Path.home() / ".microclaw" / "hooks"
MANIFEST = HOOKS_DIR / "manifest.json"

# Specific call chains that are always banned.
_BANNED_CALL_CHAINS = {"os.system"}
# Any attribute access on these modules is banned.
_BANNED_MODULES = {"subprocess"}


def validate_hook_code(code: str) -> list[str]:
    """Return a list of safety warnings. Empty list means no issues found."""
    warnings: list[str] = []
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [f"Syntax error: {e}"]

    for node in ast.walk(tree):
        # Block dangerous builtins
        if isinstance(node, ast.Name) and node.id in {"eval", "exec"}:
            warnings.append(f"Dangerous call: {node.id}()")
        # Block os.system and any subprocess.* access
        if isinstance(node, ast.Attribute):
            base = getattr(node.value, "id", "?")
            chain = f"{base}.{node.attr}"
            if chain in _BANNED_CALL_CHAINS:
                warnings.append(f"Dangerous call: {chain}")
            if base in _BANNED_MODULES:
                warnings.append(f"Dangerous module access: {chain}")
        # Block __import__
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "__import__":
                warnings.append("Dangerous call: __import__()")

    return warnings


def save_hook(name: str, code: str, description: str, source: str) -> None:
    """Save validated hook code to disk and update manifest.

    source must be 'claude_generated' or 'user_provided'.
    """
    HOOKS_DIR.mkdir(parents=True, exist_ok=True)
    hook_path = HOOKS_DIR / f"{name}.py"
    hook_path.write_text(code)

    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    manifest[name] = {
        "description": description,
        "path": str(hook_path),
        "source": source,
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2))


def read_hook_from_file(path: str) -> tuple[str, list[str]]:
    """Read a user-provided hook file and run the AST safety scan.

    Returns (code, warnings). Does NOT save — caller must call save_hook after
    user confirmation.
    """
    code = Path(path).read_text()
    warnings = validate_hook_code(code)
    return code, warnings


def load_hook_class(name: str):
    """Dynamically import a saved hook and return its class."""
    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    if name not in manifest:
        raise KeyError(f"No saved hook named '{name}'.")
    spec = importlib.util.spec_from_file_location(name, manifest[name]["path"])
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
        for k, v in json.loads(MANIFEST.read_text()).items()
    }
