import json
import pytest
from microclaw.hook_manager import (
    lint_hook_code,
    load_hook_class,
    save_hook,
    list_saved_hooks,
    read_hook_from_file,
)


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
