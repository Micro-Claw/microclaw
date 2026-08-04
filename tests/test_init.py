from types import SimpleNamespace

import yaml

from microclaw import __main__ as cli


def _args(path, **overrides):
    values = {
        "path": str(path), "force": False, "no_edit": True,
        "from_example": False, "port": 4827,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_init_noninteractive_redirects_without_reading_or_writing(tmp_path, monkeypatch, capsys):
    target = tmp_path / "safety.yaml"
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr("builtins.input", lambda _: (_ for _ in ()).throw(
        AssertionError("non-interactive init must not read stdin")
    ))

    assert cli.init(_args(target)) is None

    assert not target.exists()
    output = capsys.readouterr().out
    assert "first-launch-setup" in output
    assert "Non-interactive" in output


def test_init_interactive_offer_runs_setup_and_preserves_flags(tmp_path, monkeypatch):
    target = tmp_path / "safety.yaml"
    seen = []
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: "yes")

    def setup(args):
        seen.append(args)
        target.write_text("draft", encoding="utf-8")

    monkeypatch.setattr(cli, "first_launch_setup", setup)
    cli.init(_args(target, force=True))

    assert seen[0].out == str(target)
    assert seen[0].force is True
    assert seen[0].port == 4827


def test_init_yes_skips_offer_but_starts_setup(tmp_path, monkeypatch):
    target = tmp_path / "safety.yaml"
    seen = []
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr("builtins.input", lambda _: (_ for _ in ()).throw(
        AssertionError("--yes must skip only the preliminary offer")
    ))
    monkeypatch.setattr(cli, "first_launch_setup", lambda args: seen.append(args))

    cli.init(_args(target, yes=True))

    assert len(seen) == 1


def test_init_from_example_is_explicit_and_no_edit_still_applies(tmp_path, monkeypatch):
    target = tmp_path / "safety.yaml"
    monkeypatch.setattr(cli, "_open_in_editor", lambda _: (_ for _ in ()).throw(
        AssertionError("--no-edit must suppress the editor")
    ))

    cli.init(_args(target, from_example=True))

    loaded = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert loaded["reviewed"] is False
    assert loaded["stage"]["x_min"] == -5000.0
