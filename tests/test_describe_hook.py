import hashlib
import json
from pathlib import Path

from microclaw import hook_manager
from microclaw.hooks import PRECODED_HOOK_REGISTRY
from microclaw.tools import describe_hook, list_hooks


def _install_saved(tmp_path, monkeypatch, name, code, *, pinned=True):
    path = tmp_path / f"{name}.py"
    # Bytes written, same bytes pinned - see the note in test_completed_dataset's
    # offline_home fixture for what writing text here cost.
    source_bytes = code.encode("utf-8")
    path.write_bytes(source_bytes)
    entry = {
        "description": "fixture",
        "path": str(path),
        "source": "user_provided",
        "accepted_warnings": ["accepted fixture warning"],
    }
    if pinned:
        entry["sha256"] = hashlib.sha256(source_bytes).hexdigest()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({name: entry}), encoding="utf-8")
    monkeypatch.setattr(hook_manager, "HOOKS_DIR", tmp_path)
    monkeypatch.setattr(hook_manager, "MANIFEST", manifest)
    return path, entry


def test_install_fixture_pins_the_bytes_it_writes_under_windows_translation(
    tmp_path, monkeypatch,
):
    """Same guard as test_completed_dataset's, for the describe fixture.

    These two fixtures produced all 23 Windows failures between them, and both
    kept writing text after the product code stopped.
    """
    original_write_text = Path.write_text

    def windows_write_text(path, data, *args, **kwargs):
        if path.suffix == ".py":
            data = data.replace("\n", "\r\n")
        return original_write_text(path, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", windows_write_text)
    _install_saved(tmp_path, monkeypatch, "pinned", "class Pinned:\n    pass\n")
    described = hook_manager.describe_saved_hook("pinned")
    assert described["provenance"]["matches_manifest"] is True
    assert described["provenance"]["legacy_newline_pin"] is False


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
    # Tamper in bytes, and assert against those same bytes below. Writing text
    # here let Windows translate the newlines while the assertion hashed the
    # untranslated string - the same two-convention defect this block repairs,
    # surviving one more round in the tampering step after the install helper
    # above was fixed.
    path.write_bytes(changed.encode("utf-8"))

    result = describe_hook(mock_ctrl, unconstrained_guard, "changed")
    provenance = result["provenance"]
    assert result["class_name"] == "H"
    assert provenance["matches_manifest"] is False
    assert provenance["manifest_sha256"] == entry["sha256"]
    assert provenance["actual_sha256"] == hashlib.sha256(changed.encode("utf-8")).hexdigest()
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


def test_describe_reports_exactly_what_resolve_hook_strips(monkeypatch, tmp_path):
    """The stripped list and the popping loop must stay one list.

    A second copy drifts the moment the list grows — Block 7b extends it from
    eight names to eighteen — and describe_hook would then report a parameter as
    accepted while _resolve_hook drops it. That is worse than silence, because
    the operator acts on it.
    """
    from microclaw import tools
    from microclaw.hook_manager import FORBIDDEN_SAVED_HOOK_PARAMS

    signature = ", ".join(f"{p}=None" for p in FORBIDDEN_SAVED_HOOK_PARAMS)
    source = (
        "class Everything:\n"
        f"    def __init__(self, {signature}):\n"
        "        self.seen = dict(locals())\n"
        "        del self.seen['self']\n"
        "    def analyze_frame(self, image, metadata):\n"
        "        return None\n"
    )
    path = tmp_path / "everything.py"
    # Prophylactic, not a live failure: this test never asserts on the hash, so
    # the text/string mismatch was harmless here. Made consistent anyway so a
    # future matches_manifest assertion cannot fail on Windows alone.
    source_bytes = source.encode("utf-8")
    path.write_bytes(source_bytes)
    manifest = {"everything": {
        "description": "d", "path": str(path), "source": "user_provided",
        "sha256": hashlib.sha256(source_bytes).hexdigest(),
        "accepted_warnings": [],
    }}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(hook_manager, "MANIFEST", tmp_path / "manifest.json")

    described = describe_hook(None, None, "everything")
    assert described["parameter_handling"]["stripped"] == list(
        FORBIDDEN_SAVED_HOOK_PARAMS
    )

    # And the runtime actually drops every one of them. `log_path` is excluded
    # here because a saved hook declaring it is refused before the strip loop is
    # reached — its presence in the list is defensive only.
    strippable = [p for p in FORBIDDEN_SAVED_HOOK_PARAMS if p != "log_path"]
    runtime_source = source.replace(", log_path=None", "")
    runtime_path = tmp_path / "runtime.py"
    runtime_path.write_text(runtime_source, encoding="utf-8")
    import importlib.util
    spec = importlib.util.spec_from_file_location("runtime_everything", runtime_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr("microclaw.hook_manager.list_saved_hooks",
                        lambda: {"everything": {}})
    monkeypatch.setattr("microclaw.hook_manager.load_hook_class",
                        lambda name: module.Everything)
    smuggled = {p: "smuggled" for p in strippable}
    adapter = tools._resolve_hook(object(), object(), "everything", smuggled, None)
    assert all(value is None for value in adapter.hook.seen.values())
