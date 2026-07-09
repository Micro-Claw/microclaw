"""Where `microclaw serve` puts the Anthropic API key.

The file fallback is exercised directly (keyring is stubbed out): a real keyring
call would prompt for the login keychain on a dev machine and hang CI.
"""
import os

import pytest

from microclaw import credentials


@pytest.fixture
def no_keyring(monkeypatch):
    monkeypatch.setattr(credentials, "_keyring", lambda: None)


@pytest.fixture
def config_home(monkeypatch, tmp_path):
    monkeypatch.delenv(credentials.ENV_VAR, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    return tmp_path


def test_env_var_wins_over_every_store(monkeypatch, config_home):
    monkeypatch.setattr(credentials, "_keyring", lambda: _FakeKeyring("from-keyring"))
    monkeypatch.setenv(credentials.ENV_VAR, "from-env")
    assert credentials.load_api_key() == ("from-env", "env")


def test_keyring_wins_over_the_config_file(monkeypatch, config_home):
    monkeypatch.setattr(credentials, "_keyring", lambda: None)
    credentials.store_api_key("from-file")          # lands in the file
    monkeypatch.setattr(credentials, "_keyring", lambda: _FakeKeyring("from-keyring"))
    assert credentials.load_api_key() == ("from-keyring", "keyring")


def test_unset_when_nothing_is_stored(no_keyring, config_home):
    assert credentials.load_api_key() == (None, None)


def test_file_round_trip(no_keyring, config_home):
    where, path = credentials.store_api_key("sk-ant-plain")
    assert where == "file"
    assert credentials.config_path().exists()
    assert credentials.load_api_key() == ("sk-ant-plain", "file")


def test_file_round_trip_escapes_awkward_characters(no_keyring, config_home):
    """The key is written as a TOML basic string, so quotes and backslashes in
    it must survive rather than corrupt the file."""
    key = 'sk-ant-"quoted"\\and\\slashed'
    credentials.store_api_key(key)
    assert credentials.load_api_key() == (key, "file")


@pytest.mark.skipif(os.name == "nt", reason="chmod writes no ACL on Windows")
def test_file_is_owner_only_on_posix(no_keyring, config_home):
    credentials.store_api_key("sk-ant-plain")
    assert credentials.config_path().stat().st_mode & 0o777 == 0o600


def test_keyring_failure_falls_back_to_the_file(monkeypatch, config_home):
    class Broken:
        def set_password(self, *a):
            raise RuntimeError("no backend")

    monkeypatch.setattr(credentials, "_keyring", lambda: Broken())
    where, _ = credentials.store_api_key("sk-ant-plain")
    assert where == "file"


def test_mask_shows_only_a_suffix():
    assert credentials.mask("sk-ant-api03-AA8f") == "…AA8f"
    assert credentials.mask("abc") == "…"


class _FakeKeyring:
    def __init__(self, key):
        self.key = key

    def get_password(self, service, username):
        return self.key

    def set_password(self, service, username, key):
        self.key = key
