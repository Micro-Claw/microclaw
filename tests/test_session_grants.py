import pytest

from microclaw import tools
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


def test_stdin_grant_auto_approves_with_a_distinct_audit_row_and_revokes(
    monkeypatch, tmp_path,
):
    answers = iter(["session", "n"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    path = tmp_path / "confirmations.jsonl"
    monkeypatch.setattr(tools, "CONFIRM_AUDIT_FN", AuditLog(path).append)

    assert tools._require_confirmation("enable 488", "illumination", "enable")
    grant = tools.SESSION_GRANTS.active()[0]
    assert tools._require_confirmation("enable 561", "illumination", "enable")
    record = load_history(path).messages[-1]
    assert record["decision"] == f"auto-approved:{grant['id']}"
    assert record["grant_id"] == grant["id"]

    assert tools.SESSION_GRANTS.revoke(grant["id"])
    assert not tools._require_confirmation("enable 488", "illumination", "enable")


def test_grant_is_only_process_memory_and_has_no_serializable_state_file(tmp_path):
    before = set(tmp_path.iterdir())
    tools.SESSION_GRANTS.grant(
        "illumination", "enable", "enable 488", identity="stdin"
    )
    assert set(tmp_path.iterdir()) == before
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
