"""The desktop shortcut (design/17 v3).

`plan()` is a pure function of the environment, so the path and argument logic —
the parts that can be wrong in a way nobody notices — are testable on any OS. The
PowerShell call itself is exercised by design/17-install-spike.py on Windows.

The load-bearing property is the argument list. After v3 the double-click *is* the
whole user interface, so a shortcut that carried `--allow-remote` or a `--host`
would expose microscope control to the network with nothing in front of it.
"""
import os
import sys

import pytest

from microclaw import assets, shortcut


@pytest.fixture
def local_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(shortcut, "user_data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(shortcut, "desktop_dir", lambda: tmp_path / "Desktop")
    (tmp_path / "Desktop").mkdir()
    return tmp_path


# ---- what it launches ----

def test_shortcut_launches_serve_and_nothing_else(local_dirs):
    """No --allow-remote, no --host: a double-click is the loopback GUI."""
    p = shortcut.plan()
    assert p["args"][-1] == "serve"
    joined = " ".join(p["args"])
    assert "--allow-remote" not in joined
    assert "--host" not in joined
    assert "--safety-config" not in joined     # the per-user default, gated on review


def test_launcher_prefers_the_console_script_beside_python(tmp_path, monkeypatch):
    """Windows venv layout: python.exe lives in Scripts\\, next to microclaw.exe."""
    venv = tmp_path / "Scripts"
    venv.mkdir()
    (venv / "python.exe").touch()
    (venv / "microclaw.exe").touch()
    monkeypatch.setattr(sys, "executable", str(venv / "python.exe"))
    target, args = shortcut.launcher()
    assert target == str(venv / "microclaw.exe")
    assert args == ["serve"]


def test_launcher_finds_the_console_script_under_conda(tmp_path, monkeypatch):
    """Conda layout: python.exe at the env root, script one level down.

    The lab rig runs this. A launcher that checks only the sibling silently falls
    back to `-m microclaw` (design/17 spike Q4).
    """
    env = tmp_path / "envs" / "microclaw"
    (env / "Scripts").mkdir(parents=True)
    (env / "python.exe").touch()
    (env / "Scripts" / "microclaw.exe").touch()
    monkeypatch.setattr(sys, "executable", str(env / "python.exe"))
    target, args = shortcut.launcher()
    assert target == str(env / "Scripts" / "microclaw.exe")
    assert args == ["serve"]


def test_launcher_falls_back_to_the_module(tmp_path, monkeypatch):
    bare = tmp_path / "bin"
    bare.mkdir()
    (bare / "python.exe").touch()
    monkeypatch.setattr(sys, "executable", str(bare / "python.exe"))
    target, args = shortcut.launcher()
    assert target == str(bare / "python.exe")
    assert args == ["-m", "microclaw", "serve"]


# ---- where it writes ----

def test_wrapper_and_icon_are_machine_local(local_dirs):
    """Not the roaming config dir: that is a network share on the lab rig, and a
    shortcut whose target lives on a share breaks when the network does."""
    p = shortcut.plan()
    assert p["workdir"] == local_dirs / "data"
    assert p["cmd"].parent == local_dirs / "data"
    assert p["icon"].parent == local_dirs / "data"
    assert p["lnk"].parent == local_dirs / "Desktop"


def test_dest_overrides_the_desktop(local_dirs):
    other = local_dirs / "elsewhere"
    assert shortcut.plan(other)["lnk"] == other / shortcut.LNK_NAME


def test_dry_run_writes_nothing(local_dirs):
    p = shortcut.install(dry_run=True)
    assert not p["lnk"].exists()
    assert not p["cmd"].exists()
    assert not p["icon"].exists()


# ---- the .cmd wrapper ----

def test_wrapper_sets_the_env_var_and_quotes_the_target():
    text = shortcut._wrapper_text(r"C:\Program Files\env\microclaw.exe", ["serve"])
    assert f"set {shortcut.FROM_SHORTCUT_ENV}=1" in text
    assert '"C:\\Program Files\\env\\microclaw.exe" serve' in text
    assert text.endswith("\r\n") and "\r\n" in text     # batch wants CRLF


def test_wrapper_quotes_module_args_individually():
    text = shortcut._wrapper_text(r"C:\py\python.exe", ["-m", "microclaw", "serve"])
    assert '"C:\\py\\python.exe" -m microclaw serve' in text


# ---- removal ----

def test_remove_is_idempotent(local_dirs):
    assert shortcut.remove() == []          # nothing there yet, not an error


def test_remove_deletes_what_install_wrote(local_dirs, monkeypatch):
    monkeypatch.setattr(shortcut, "_powershell", lambda *a, **k: None)
    p = shortcut.install()
    p["lnk"].write_text("stub")             # PowerShell was stubbed out
    gone = shortcut.remove()
    assert set(gone) == {p["lnk"], p["cmd"], p["icon"]}
    assert not p["icon"].exists()


# ---- failure modes ----

def test_unreachable_desktop_raises_rather_than_traceback(local_dirs, monkeypatch):
    """A redirected desktop can be an offline network share."""
    monkeypatch.setattr(shortcut, "desktop_dir", lambda: local_dirs / "no" / "such")
    with pytest.raises(shortcut.ShortcutError, match="not reachable"):
        shortcut.install()
    # and it bailed before writing anything, so a retry starts clean
    assert not (local_dirs / "data").exists()


def test_icon_is_materialized_out_of_the_package(tmp_path):
    """The .lnk stores an absolute path; it cannot point inside a wheel."""
    dest = assets.materialize_icon(tmp_path / "sub" / "microclaw.ico")
    assert dest.read_bytes()[:4] == b"\x00\x00\x01\x00"


# ---- the console pause ----

def test_pause_is_a_noop_without_the_env_var(monkeypatch):
    monkeypatch.delenv(shortcut.FROM_SHORTCUT_ENV, raising=False)
    called = []
    monkeypatch.setattr("atexit.register", lambda f: called.append(f))
    shortcut.pause_on_exit()
    assert called == []


def test_pause_registers_when_launched_from_the_shortcut(monkeypatch):
    monkeypatch.setenv(shortcut.FROM_SHORTCUT_ENV, "1")
    called = []
    monkeypatch.setattr("atexit.register", lambda f: called.append(f))
    shortcut.pause_on_exit()
    assert len(called) == 1


def test_pause_survives_a_closed_stdin(monkeypatch):
    """Ctrl-C at the prompt, or no stdin at all, must not raise from atexit."""
    monkeypatch.setenv(shortcut.FROM_SHORTCUT_ENV, "1")
    captured = []
    monkeypatch.setattr("atexit.register", lambda f: captured.append(f))
    shortcut.pause_on_exit()
    monkeypatch.setattr("builtins.input", lambda *a: (_ for _ in ()).throw(EOFError))
    captured[0]()          # must not raise


# ---- platform gate ----

@pytest.mark.skipif(os.name == "nt", reason="checks the non-Windows branch")
def test_not_supported_off_windows():
    assert shortcut.supported() is False
