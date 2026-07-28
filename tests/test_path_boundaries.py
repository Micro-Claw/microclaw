"""Tripwires for the write/serve/local-read path boundary."""

import ast
from pathlib import Path


ROOT = Path(__file__).parents[1]


def _callers(source: str, resolver: str) -> set[str]:
    """Return top-level functions/methods containing a resolver call.

    Nested helper calls belong to their top-level owner: that is the public
    boundary a reviewer deliberately approves.
    """
    tree = ast.parse(source)
    found = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            owners = [(node.name, node)]
        elif isinstance(node, ast.ClassDef):
            owners = [
                (f"{node.name}.{child.name}", child) for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
        else:
            owners = []
        for name, owner in owners:
            if any(
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == resolver
                for call in ast.walk(owner)
            ):
                found.add(name)
    return found


def test_only_deliberately_reviewed_functions_may_use_permissive_read_resolution():
    expected = {
        "microclaw/tools.py": {
            "export_dataset_as_tiff", "load_position_list", "read_hook_log",
            "rank_hook_log", "inspect_artifacts", "compare_revisit_frames",
            "calibrate_snr_threshold", "read_hook_from_file",
        },
        "microclaw/hooks.py": {"SNRObservationHook.__init__"},
        "microclaw/webserve.py": set(),
    }
    actual = {
        filename: _callers((ROOT / filename).read_text(encoding="utf-8"),
                           "resolve_readable_path")
        for filename in expected
    }
    assert actual == expected


def test_tripwire_detects_a_new_permissive_resolver_caller():
    source = """
def approved(guard, path):
    return guard.resolve_readable_path(path)

def new_write_path(guard, path):
    return guard.resolve_readable_path(path)
"""
    approved = {"approved"}
    assert _callers(source, "resolve_readable_path") != approved


def test_all_write_and_serve_owners_remain_on_the_confined_resolver():
    expected = {
        "microclaw/tools.py": {
            "_acquire_with_hooks", "run_zstack", "run_timelapse",
            "export_dataset_as_tiff", "save_position_list",
            "run_multiposition_acquisition", "run_multiposition_with_autofocus",
            "_prepare_log_path", "run_adaptive_zstack", "run_adaptive_timelapse",
            "_acquire_positions_with_hook", "_acquire_survey_with_detector",
            "inspect_artifacts", "calibrate_snr_threshold", "run_mda",
        },
        "microclaw/hooks.py": set(),
        "microclaw/webserve.py": {"build_app"},
    }
    actual = {
        filename: _callers((ROOT / filename).read_text(encoding="utf-8"),
                           "resolve_in_workspace")
        for filename in expected
    }
    assert actual == expected
