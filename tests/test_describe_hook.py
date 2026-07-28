import hashlib
import json

from microclaw import hook_manager
from microclaw.hooks import PRECODED_HOOK_REGISTRY
from microclaw.tools import describe_hook, list_hooks


def _install_saved(tmp_path, monkeypatch, name, code, *, pinned=True):
    path = tmp_path / f"{name}.py"
    path.write_text(code, encoding="utf-8")
    entry = {
        "description": "fixture",
        "path": str(path),
        "source": "user_provided",
        "accepted_warnings": ["accepted fixture warning"],
    }
    if pinned:
        entry["sha256"] = hashlib.sha256(code.encode()).hexdigest()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({name: entry}), encoding="utf-8")
    monkeypatch.setattr(hook_manager, "HOOKS_DIR", tmp_path)
    monkeypatch.setattr(hook_manager, "MANIFEST", manifest)
    return path, entry


def test_saved_parameters_defaults_required_and_source_never_executes(
    tmp_path, monkeypatch, mock_ctrl, unconstrained_guard
):
    code = '''
raise RuntimeError("describing imported this module")

class WindDown:
    """Stops after the requested quiet period."""
    def __init__(self, required_threshold, ridge_sigmas=(1.0, 2.0, 3.0),
                 dynamic=make_default()):
        pass

    def analyze_frame(self, image, metadata):
        return None
'''
    _install_saved(tmp_path, monkeypatch, "wind_down", code)

    result = describe_hook(mock_ctrl, unconstrained_guard, "wind_down")

    assert result["class_name"] == "WindDown"
    assert result["class_docstring"] == "Stops after the requested quiet period."
    assert result["callback"] == "analyze_frame"
    assert result["constructor_parameters"] == [
        {"name": "required_threshold", "required": True},
        {"name": "ridge_sigmas", "required": False,
         "default": "(1.0, 2.0, 3.0)"},
        {"name": "dynamic", "required": False, "default": "make_default()"},
    ]
    assert result["resolve_refusal"]["would_refuse"] is False


def test_saved_refusal_and_stripped_parameters(
    tmp_path, monkeypatch, mock_ctrl, unconstrained_guard
):
    code = '''
from microclaw.hooks import HookBase as Base

class Legacy(Base):
    def __init__(self, log_path=None, ctrl=None, event_queue=None):
        pass
    def image_process_fn(self, image, metadata, event_queue):
        return image, metadata
'''
    _install_saved(tmp_path, monkeypatch, "legacy", code)
    result = describe_hook(mock_ctrl, unconstrained_guard, "legacy")

    assert result["callback"] == "image_process_fn"
    assert result["resolve_refusal"]["would_refuse"] is True
    assert result["resolve_refusal"]["reasons"] == [
        "saved hook subclasses HookBase",
        "saved hook constructor takes log_path",
    ]
    assert result["parameter_handling"]["stripped"] == [
        "ctrl", "event_queue", "log_path"
    ]


def test_saved_log_path_alone_is_refused(
    tmp_path, monkeypatch, mock_ctrl, unconstrained_guard
):
    code = '''
class Logger:
    def __init__(self, log_path=None): pass
    def analyze_frame(self, image, metadata): return None
'''
    _install_saved(tmp_path, monkeypatch, "logger", code)
    result = describe_hook(mock_ctrl, unconstrained_guard, "logger")
    assert result["resolve_refusal"] == {
        "would_refuse": True,
        "reasons": ["saved hook constructor takes log_path"],
    }


def test_precoded_hook_reports_injected_parameters(
    monkeypatch, mock_ctrl, unconstrained_guard
):
    class Builtin:
        """A trusted fixture."""

        def __init__(self, ctrl, guard, threshold=3):
            pass

        def analyze_frame(self, image, metadata):
            return None

    monkeypatch.setitem(PRECODED_HOOK_REGISTRY, "builtin_fixture", Builtin)
    result = describe_hook(mock_ctrl, unconstrained_guard, "builtin_fixture")

    assert result["kind"] == "precoded"
    assert result["constructor_parameters"] == [
        {"name": "ctrl", "required": True},
        {"name": "guard", "required": True},
        {"name": "threshold", "required": False, "default": "3"},
    ]
    assert result["parameter_handling"] == {
        "stripped": [], "injected": ["ctrl", "guard"]
    }
    assert result["resolve_refusal"]["would_refuse"] is False


def test_hash_mismatch_is_described(
    tmp_path, monkeypatch, mock_ctrl, unconstrained_guard
):
    original = "class H:\n def analyze_frame(self, image, metadata): pass\n"
    path, entry = _install_saved(tmp_path, monkeypatch, "changed", original)
    changed = original.replace("pass", "return None")
    path.write_text(changed, encoding="utf-8")

    result = describe_hook(mock_ctrl, unconstrained_guard, "changed")
    provenance = result["provenance"]
    assert result["class_name"] == "H"
    assert provenance["matches_manifest"] is False
    assert provenance["manifest_sha256"] == entry["sha256"]
    assert provenance["actual_sha256"] == hashlib.sha256(changed.encode()).hexdigest()
    assert result["resolve_refusal"] == {
        "would_refuse": True,
        "reasons": ["saved hook file sha256 does not match manifest"],
    }


def test_unpinned_hook_is_described(
    tmp_path, monkeypatch, mock_ctrl, unconstrained_guard
):
    code = "class H:\n def analyze_frame(self, image, metadata): pass\n"
    _install_saved(tmp_path, monkeypatch, "legacy", code, pinned=False)
    result = describe_hook(mock_ctrl, unconstrained_guard, "legacy")

    assert result["class_name"] == "H"
    assert result["provenance"]["manifest_sha256"] is None
    assert result["provenance"]["actual_sha256"]
    assert result["provenance"]["matches_manifest"] is False
    assert result["resolve_refusal"] == {
        "would_refuse": True,
        "reasons": ["saved hook has no manifest sha256 pin"],
    }


def test_malformed_source_returns_error_and_does_not_break_listing(
    tmp_path, monkeypatch, mock_ctrl, unconstrained_guard
):
    _install_saved(tmp_path, monkeypatch, "broken", "class Broken(:\n")

    described = describe_hook(mock_ctrl, unconstrained_guard, "broken")
    listed = list_hooks(mock_ctrl, unconstrained_guard)

    assert "error" in described
    assert described["provenance"]["actual_sha256"]
    assert listed["saved"]["broken"]["description"] == "fixture"
    assert "describe_hook" in listed["hint"]
