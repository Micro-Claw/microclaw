from types import SimpleNamespace

import pytest

from microclaw import paths, tools
from microclaw import __main__ as cli
from microclaw.conversation import AuditLog, load_history
from microclaw.safety import (
    ForbiddenProperty,
    IlluminationConstraints,
    IlluminationProperty,
    SafetyConstraints,
    SafetyGuard,
    SafetyViolation,
)


@pytest.fixture(autouse=True)
def reset_session_grants(monkeypatch):
    tools.SESSION_GRANTS.clear()
    monkeypatch.setattr(tools, "CONFIRM_AUDIT_FN", None)
    yield
    tools.SESSION_GRANTS.clear()


def test_non_grantable_self_modification_kinds_raise():
    for kind in ("knowledge", "hook"):
        with pytest.raises(ValueError, match="self-modification"):
            tools.SESSION_GRANTS.grant(kind, None, "summary", "stdin")


def test_stdin_grant_auto_approves_with_a_distinct_audit_row(
    monkeypatch, tmp_path,
):
    answers = iter(["session"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    path = tmp_path / "confirmations.jsonl"
    monkeypatch.setattr(tools, "CONFIRM_AUDIT_FN", AuditLog(path).append)

    assert tools._require_confirmation("enable 488", "illumination", "enable")
    grant = tools.SESSION_GRANTS.active()[0]
    assert tools._require_confirmation("enable 561", "illumination", "enable")
    record = load_history(path).messages[-1]
    assert record["decision"] == f"auto-approved:{grant['id']}"
    assert record["grant_id"] == grant["id"]



def test_terminal_operator_lists_and_revokes_a_grant_through_repl(
    monkeypatch, tmp_path, capsys,
):
    grant = tools.SESSION_GRANTS.grant(
        "illumination", "enable", "enable 488", identity="stdin"
    )
    path = tmp_path / "confirmations.jsonl"
    monkeypatch.setattr(tools, "CONFIRM_AUDIT_FN", AuditLog(path).append)
    monkeypatch.setattr(cli, "run_agent", lambda *a, **k: pytest.fail("agent ran"))
    answers = iter(["grants", "1", "exit"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))

    cli._repl(
        SimpleNamespace(profile=False, model=None), object(), object(), [],
        SimpleNamespace(model_messages=lambda: [], append=lambda message: None),
    )

    assert tools.SESSION_GRANTS.active() == []
    output = capsys.readouterr().out
    assert "illumination/enable" in output
    assert "Revoked illumination/enable" in output
    record = load_history(path).messages[-1]
    assert record["decision"] == f"revoked:{grant['id']}"
    assert record["grant_id"] == grant["id"]

    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    assert not tools._require_confirmation("enable 488", "illumination", "enable")


def test_grant_is_only_process_memory_and_writes_no_user_state():
    """Nothing under microclaw's real user directories may change.

    Do not try to relocate those directories with XDG_* and assert they stay
    absent: `paths.user_config_dir` reads APPDATA on Windows and LOCALAPPDATA
    for data, so the env vars move nothing there and the real, already-existing
    directory fails an `exists()` assertion. That is how this test failed on
    M5 at block 43c's gate while passing on macOS -- a platform-conditional
    test defect, with the product code correct.

    Comparing a recursive snapshot instead is platform-independent and can
    still fail for the reason the test exists: a grant persisted anywhere under
    either directory shows up as a new path.
    """
    def snapshot():
        listing = set()
        for directory in (paths.user_config_dir(), paths.user_data_dir()):
            if directory.exists():
                listing |= {
                    (path, path.stat().st_mtime_ns if path.is_file() else None)
                    for path in directory.rglob("*")
                }
        return listing

    before = snapshot()
    tools.SESSION_GRANTS.grant(
        "illumination", "enable", "enable 488", identity="stdin"
    )
    assert snapshot() == before
    # A new process would construct the same fresh registry at import time.
    assert tools.SessionGrants().active() == []


def test_active_enable_grant_does_not_bypass_power_cap(monkeypatch):
    guard = SafetyGuard(SafetyConstraints(illumination=IlluminationConstraints(
        shutters=[IlluminationProperty(
            device="Laser", property="Enable", on_value="1", off_value="0"
        )],
        power_properties=[ForbiddenProperty("Laser", "Power")],
        max_power_percent=30.0,
        max_power_step_factor=3.0,
    )))
    tools.SESSION_GRANTS.grant(
        "illumination", "enable", "enable Laser", identity="stdin"
    )
    guard.check_illumination(
        object(), "Laser", "Enable", "1", confirm_fn=tools._require_confirmation
    )
    with pytest.raises(SafetyViolation, match="max_power_percent"):
        guard.check_illumination(object(), "Laser", "Power", "31")


def test_grant_does_not_match_other_questions_of_the_same_kind(monkeypatch):
    tools.SESSION_GRANTS.grant(
        "illumination", "enable", "enable Laser", identity="stdin"
    )
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    assert not tools._require_confirmation(
        "retarget Core.Shutter", "illumination", subject=None
    )
