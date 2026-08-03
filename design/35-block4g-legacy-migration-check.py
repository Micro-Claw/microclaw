"""Exercise the legacy-newline-pin migration in an isolated temporary registry.

Touches no microscope hardware and no real hook registry: it builds a throwaway
HOOKS_DIR/MANIFEST in a temp directory, plants a hook pinned under the *old*
newline-normalized convention, and walks the whole migration. Nothing outside
the temp directory is read or written.

Exists because Block 4g's G2 otherwise depends on the gate machine happening to
own a hook saved under the old convention. If it owns none, the migration path
ships to every operator untested. This makes G2 runnable anywhere.

Run:  python design\\35-block4g-legacy-migration-check.py > legacy-synthetic.txt 2>&1
Exit: 0 if every check passed, 1 otherwise.
"""
import hashlib
import json
import sys
import tempfile
from pathlib import Path

from microclaw import completed_dataset, hook_manager

CODE = (
    "class LegacyGateHook:\r\n"
    "    def analyze_frame(self, image, metadata):\r\n"
    "        return None\r\n"
    "    def analyze_saved_frame(self, image, metadata, context):\r\n"
    "        return {'ok': True}\r\n"
)
NAME = "block4g_legacy"


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="mc-4g-legacy-"))
    hook_manager.HOOKS_DIR = root
    hook_manager.MANIFEST = root / "manifest.json"
    completed_dataset.MANIFEST = hook_manager.MANIFEST

    hook_path = root / f"{NAME}.py"
    raw = CODE.encode("utf-8")
    hook_path.write_bytes(raw)

    # The old convention: hash the newline-normalized *text*, not the bytes on
    # disk. On Windows this is exactly what save_hook used to record.
    legacy_pin = hashlib.sha256(
        CODE.replace("\r\n", "\n").encode("utf-8")
    ).hexdigest()
    hook_manager.MANIFEST.write_text(json.dumps({NAME: {
        "description": "synthetic legacy-pinned hook",
        "path": str(hook_path),
        "source": "user_provided",
        "sha256": legacy_pin,
        "accepted_warnings": [],
    }}, indent=2), encoding="utf-8")

    checks: dict[str, object] = {
        "planted_pin_differs_from_bytes": legacy_pin != hashlib.sha256(raw).hexdigest(),
    }

    before = hook_manager.describe_saved_hook(NAME)
    checks["describe_flags_legacy_pin"] = (
        before["provenance"].get("legacy_newline_pin") is True
    )
    checks["describe_reports_mismatch"] = (
        before["provenance"].get("matches_manifest") is False
    )

    for label, call in (
        ("live_load", lambda: hook_manager.load_hook_class(NAME)),
        ("offline_load", lambda: completed_dataset._load_saved_adapter(NAME)),
    ):
        try:
            call()
        except RuntimeError as exc:
            text = str(exc)
            checks[f"{label}_refused_actionably"] = (
                "legacy newline-normalized" in text and "re-save" in text
            )
            checks[f"{label}_message"] = text
        else:
            checks[f"{label}_refused_actionably"] = False
            checks[f"{label}_message"] = "NO REFUSAL — the legacy pin was accepted"

    # The consent action. In the real flow a human reads the source first; here
    # the source is this file's own constant, reviewed when this script was
    # written.
    code = hook_path.read_bytes().decode("utf-8")
    hook_manager.save_hook(NAME, code, "re-approved after review", "user_provided")

    after = hook_manager.describe_saved_hook(NAME)
    checks["after_matches_manifest"] = (
        after["provenance"].get("matches_manifest") is True
    )
    checks["after_legacy_flag_cleared"] = (
        after["provenance"].get("legacy_newline_pin") is False
    )
    checks["after_live_load"] = (
        hook_manager.load_hook_class(NAME).__name__ == "LegacyGateHook"
    )
    checks["after_offline_load"] = (
        completed_dataset._load_saved_adapter(NAME)[0].__name__ == "LegacyGateHook"
    )

    print(json.dumps(checks, indent=2))
    print(f"\ntemporary registry: {root}")
    failed = [k for k, v in checks.items() if v is False]
    if failed:
        print(f"FAILED CHECKS: {failed}")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
