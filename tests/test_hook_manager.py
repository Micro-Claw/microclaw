import hashlib
import json
import inspect
from pathlib import Path

import pytest
from microclaw.hook_manager import (
    describe_saved_hook,
    lint_hook_code,
    load_hook_class,
    save_hook,
    list_saved_hooks,
    read_hook_from_file,
    validate_hook_contract,
)
from microclaw.hook_decisions import EmitArtifact


def test_clean_code_passes():
    code = (
        "import numpy as np\n"
        "class MyHook:\n"
        "    def image_process_fn(self, img, meta, q):\n"
        "        return img, meta\n"
    )
    assert lint_hook_code(code) == []


def test_eval_is_blocked():
    code = "eval('os.system(\"rm -rf /\")')"
    warnings = lint_hook_code(code)
    assert any("eval" in w for w in warnings)


def test_exec_is_blocked():
    code = "exec('import os')"
    warnings = lint_hook_code(code)
    assert any("exec" in w for w in warnings)


def test_syntax_error_reported():
    code = "def broken(:"
    warnings = lint_hook_code(code)
    assert any("Syntax" in w for w in warnings)


def test_static_contract_rejects_analyze_instead_of_image_process_fn():
    assert validate_hook_contract("class Hook:\n    def analyze(self, image): pass\n")


def test_adaptive_preflight_and_runtime_use_the_same_contract_validator():
    source_error = validate_hook_contract(
        "class Hook:\n    def image_process_fn(self, image, metadata, queue): pass\n",
        required_callback="analyze_frame",
    )

    class Hook:
        def image_process_fn(self, image, metadata, queue):
            pass

    runtime_error = validate_hook_contract(Hook(), required_callback="analyze_frame")
    assert source_error == runtime_error
    assert "analyze_frame" in source_error[0]


def test_static_contract_accepts_runner_callback_signature():
    code = (
        "class Hook:\n"
        "    def image_process_fn(self, image, metadata, event_queue): pass\n"
    )
    assert validate_hook_contract(code) == []


def test_static_contract_rejects_reversed_emit_artifact_arguments():
    code = (
        "class Hook:\n"
        " def analyze_frame(self, image, metadata):\n"
        "  return HookResult({}, (EmitArtifact(image, self.filename),))\n"
    )
    assert "not provably a string" in validate_hook_contract(code)[0]


def test_session_a_hook_source_is_rejected_verbatim():
    source = (Path(__file__).parent / "fixtures" / "hooks" /
              "session_a_plus_mosaic_stitcher.py").read_text(encoding="utf-8")
    errors = validate_hook_contract(source)
    assert any("not provably a string" in error for error in errors)


def test_documented_emit_artifact_order_matches_runtime_signature():
    assert list(inspect.signature(EmitArtifact).parameters)[:2] == ["filename", "payload"]
    from microclaw.hook_docs import HOOK_REFERENCE
    assert "EmitArtifact(filename, payload)" in HOOK_REFERENCE


@pytest.mark.parametrize("filename_expr", ['"x.bin"', "f'{name}.bin'", "str(name)"])
def test_provable_positional_filename_is_accepted(filename_expr):
    code = (
        "class Hook:\n"
        " def analyze_frame(self, image, metadata):\n"
        f"  return HookResult({{}}, (EmitArtifact({filename_expr}, image),))\n"
    )
    assert validate_hook_contract(code) == []


def test_keyword_emit_artifact_is_never_subject_to_positional_type_guessing():
    code = (
        "class Hook:\n"
        " def analyze_frame(self, image, metadata):\n"
        "  return HookResult({}, (EmitArtifact(filename=self.out_name, payload=image),))\n"
    )
    assert validate_hook_contract(code) == []


def test_other_typed_actions_are_checked_from_their_runtime_signatures():
    code = (
        "class Hook:\n"
        " def analyze_frame(self, image, metadata):\n"
        "  return HookResult({}, (AcquireAt(),))\n"
    )
    errors = validate_hook_contract(code)
    assert any("AcquireAt is missing required arguments ['position']" in error
               for error in errors)


def test_subprocess_blocked():
    code = "import subprocess\nsubprocess.run(['rm', '-rf', '/'])"
    warnings = lint_hook_code(code)
    assert any("subprocess" in w for w in warnings)


def test_os_system_blocked():
    code = "import os\nos.system('rm -rf /')"
    warnings = lint_hook_code(code)
    assert any("os.system" in w for w in warnings)


def test_import_blocked():
    code = "__import__('os').system('rm -rf /')"
    warnings = lint_hook_code(code)
    assert any("__import__" in w for w in warnings)


@pytest.mark.parametrize("code", [
    "import os as o\no.system('x')",                       # aliased import
    "import importlib\nimportlib.import_module('os')",     # dynamic import
    "import os\ngetattr(os, 'system')('x')",               # getattr evasion
    "open('/etc/passwd').read()",                          # raw open
    "import socket\nsocket.socket()",                      # network
])
def test_evasions_produce_a_warning(code):
    assert lint_hook_code(code), f"expected ≥1 warning for: {code!r}"


@pytest.mark.parametrize("module", ["requests", "urllib.request", "httpx", "anthropic"])
def test_acquisition_time_network_clients_produce_a_warning(module):
    assert lint_hook_code(f"import {module}\n"), module


def test_save_hook_records_source(tmp_path, monkeypatch):
    monkeypatch.setattr("microclaw.hook_manager.HOOKS_DIR", tmp_path)
    monkeypatch.setattr("microclaw.hook_manager.MANIFEST", tmp_path / "manifest.json")
    code = "class H:\n    def image_process_fn(self, img, meta, q): return img, meta\n"
    save_hook("my_hook", code, "A test hook", source="user_provided")
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["my_hook"]["source"] == "user_provided"


def test_list_saved_hooks_returns_source(tmp_path, monkeypatch):
    monkeypatch.setattr("microclaw.hook_manager.HOOKS_DIR", tmp_path)
    monkeypatch.setattr("microclaw.hook_manager.MANIFEST", tmp_path / "manifest.json")
    code = "class H:\n    def image_process_fn(self, img, meta, q): return img, meta\n"
    save_hook("gen_hook", code, "Generated hook", source="claude_generated")
    hooks = list_saved_hooks()
    assert hooks["gen_hook"]["source"] == "claude_generated"
    assert "description" in hooks["gen_hook"]


def test_list_saved_hooks_empty_when_no_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr("microclaw.hook_manager.MANIFEST", tmp_path / "manifest.json")
    assert list_saved_hooks() == {}


def test_read_hook_from_file_valid(tmp_path):
    hook_file = tmp_path / "my_hook.py"
    code = "class H:\n    def image_process_fn(self, img, meta, q): return img, meta\n"
    hook_file.write_text(code)
    returned_code, warnings = read_hook_from_file(str(hook_file))
    assert returned_code == code
    assert warnings == []


def test_read_hook_from_file_with_warnings(tmp_path):
    hook_file = tmp_path / "bad_hook.py"
    hook_file.write_text("eval('rm -rf /')\n")
    _, warnings = read_hook_from_file(str(hook_file))
    assert any("eval" in w for w in warnings)


def test_read_hook_from_file_not_found():
    with pytest.raises(FileNotFoundError):
        read_hook_from_file("/nonexistent/path/hook.py")


_CLEAN_HOOK = (
    "class H:\n"
    "    def image_process_fn(self, img, meta, q):\n"
    "        return img, meta\n"
)


class TestHashPinnedLoad:
    @pytest.fixture(autouse=True)
    def _tmp_hooks(self, tmp_path, monkeypatch):
        monkeypatch.setattr("microclaw.hook_manager.HOOKS_DIR", tmp_path)
        monkeypatch.setattr("microclaw.hook_manager.MANIFEST", tmp_path / "manifest.json")
        self.dir = tmp_path

    def test_clean_hook_loads(self):
        save_hook("h", _CLEAN_HOOK, "clean", source="user_provided")
        cls = load_hook_class("h")
        assert hasattr(cls, "image_process_fn")

    def test_analyze_frame_only_hook_round_trips(self):
        code = (
            "class H:\n"
            "    def analyze_frame(self, image, metadata):\n"
            "        return None\n"
        )
        assert validate_hook_contract(code) == []
        save_hook("analysis", code, "analysis", source="claude_generated")
        cls = load_hook_class("analysis")
        assert hasattr(cls, "analyze_frame")

    def test_stale_session_a_reversed_emit_is_refused_at_load(self):
        source = (Path(__file__).parent / "fixtures" / "hooks" /
                  "session_a_plus_mosaic_stitcher.py").read_text(encoding="utf-8")
        # This represents a hook pinned before the newer contract check existed.
        save_hook("plus_mosaic_stitcher", source, "stale rig hook",
                  source="claude_generated")

        with pytest.raises(ValueError, match="current hook contract.*not provably a string"):
            load_hook_class("plus_mosaic_stitcher")

    def test_correct_preexisting_artifact_hook_still_loads_and_is_declared(self):
        code = (
            "from microclaw.hook_decisions import EmitArtifact, HookResult\n"
            "class H:\n"
            " def analyze_frame(self, image, metadata):\n"
            "  return HookResult({}, (EmitArtifact(filename='x.bin', payload=image),))\n"
        )
        save_hook("correct_old_hook", code, "correct", source="user_provided")

        cls = load_hook_class("correct_old_hook")
        assert cls.can_emit_artifacts is True

    def test_tampered_file_refused(self):
        save_hook("h", _CLEAN_HOOK, "clean", source="user_provided")
        # Edit the file on disk after save — TOCTOU.
        (self.dir / "h.py").write_text(_CLEAN_HOOK + "\nimport os\n")
        with pytest.raises(RuntimeError, match="changed on disk"):
            load_hook_class("h")

    def test_hook_with_accepted_warnings_loads_unchanged(self):
        code = (
            "class H:\n"
            "    def image_process_fn(self, img, meta, q):\n"
            "        open('/tmp/log', 'a').write('x')\n"
            "        return img, meta\n"
        )
        save_hook("logger", code, "writes a log", source="user_provided")
        cls = load_hook_class("logger")  # accepted warnings recorded → loads
        assert hasattr(cls, "image_process_fn")

    def test_legacy_unpinned_entry_refused(self):
        # Simulate a manifest written before hash-pinning (no sha256).
        (self.dir / "h.py").write_text(_CLEAN_HOOK)
        (self.dir / "manifest.json").write_text(json.dumps({
            "h": {"description": "old", "path": str(self.dir / "h.py"),
                  "source": "user_provided"}
        }))
        with pytest.raises(RuntimeError, match="re-save"):
            load_hook_class("h")

    def test_save_records_hash_and_accepted_warnings(self):
        save_hook("h", _CLEAN_HOOK, "clean", source="user_provided")
        entry = json.loads((self.dir / "manifest.json").read_text())["h"]
        assert "sha256" in entry
        assert entry["accepted_warnings"] == []

    def test_legacy_windows_newline_pin_requires_explicit_resave(self, monkeypatch):
        from microclaw import completed_dataset

        monkeypatch.setattr(completed_dataset, "MANIFEST", self.dir / "manifest.json")
        raw = _CLEAN_HOOK.replace("\n", "\r\n").encode("utf-8")
        (self.dir / "h.py").write_bytes(raw)
        (self.dir / "manifest.json").write_text(json.dumps({
            "h": {
                "description": "legacy Windows pin",
                "path": str(self.dir / "h.py"),
                "source": "user_provided",
                "sha256": hashlib.sha256(_CLEAN_HOOK.encode()).hexdigest(),
                "accepted_warnings": [],
            }
        }))

        with pytest.raises(RuntimeError, match="legacy newline-normalized.*re-save"):
            load_hook_class("h")
        with pytest.raises(RuntimeError, match="legacy newline-normalized.*re-save"):
            completed_dataset._load_saved_adapter("h")
        described = describe_saved_hook("h")
        assert described["provenance"]["legacy_newline_pin"] is True
        assert described["resolve_refusal"] == {
            "would_refuse": True,
            "reasons": [
                "saved hook uses a legacy newline-normalized hash; review and re-save it"
            ],
        }
