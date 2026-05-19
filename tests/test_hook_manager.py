import json
import pytest
from microclaw.hook_manager import validate_hook_code, save_hook, list_saved_hooks, read_hook_from_file


def test_clean_code_passes():
    code = (
        "import numpy as np\n"
        "class MyHook:\n"
        "    def image_process_fn(self, img, meta, q):\n"
        "        return img, meta\n"
    )
    assert validate_hook_code(code) == []


def test_eval_is_blocked():
    code = "eval('os.system(\"rm -rf /\")')"
    warnings = validate_hook_code(code)
    assert any("eval" in w for w in warnings)


def test_exec_is_blocked():
    code = "exec('import os')"
    warnings = validate_hook_code(code)
    assert any("exec" in w for w in warnings)


def test_syntax_error_reported():
    code = "def broken(:"
    warnings = validate_hook_code(code)
    assert any("Syntax" in w for w in warnings)


def test_subprocess_blocked():
    code = "import subprocess\nsubprocess.run(['rm', '-rf', '/'])"
    warnings = validate_hook_code(code)
    assert any("subprocess" in w for w in warnings)


def test_os_system_blocked():
    code = "import os\nos.system('rm -rf /')"
    warnings = validate_hook_code(code)
    assert any("os.system" in w for w in warnings)


def test_import_blocked():
    code = "__import__('os').system('rm -rf /')"
    warnings = validate_hook_code(code)
    assert any("__import__" in w for w in warnings)


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
