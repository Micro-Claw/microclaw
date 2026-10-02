"""Where the Anthropic API key lives when it isn't in the environment.

`microclaw serve` can collect a key from the browser on first run, so it needs
somewhere to put it. Resolution order is env var > OS credential store > a
user-level config file > unset.

On the OS credential store vs. a config file: Microclaw runs on a Windows lab
machine, where the reflexive POSIX answer is wrong. `os.chmod(path, 0o600)` on
Windows only toggles the read-only attribute — it writes no ACL — so a "0600"
config file stays readable by every account on the box. `keyring` brokers to the
Keychain / Credential Manager / Secret Service instead, which is the only one of
these that is actually a secret store. The file fallback exists because keyring
is awkward on headless or locked-down machines; it is convenience, not
protection, and the UI says so.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from microclaw.paths import user_config_dir

ENV_VAR = "ANTHROPIC_API_KEY"

# keyring service/username pair; arbitrary, but must stay stable across versions
# or previously-saved keys become unreachable.
_KEYRING_SERVICE = "microclaw"
_KEYRING_USERNAME = "anthropic-api-key"

_TOML_KEY = "anthropic_api_key"
LLM_ENV_VAR = "MICROCLAW_LLM_API_KEY"
_LLM_KEYRING_USERNAME = "local-llm-api-key"
_LLM_TOML_KEY = "llm_api_key"


def config_path() -> Path:
    """User-level config file — follows the user, not the working directory."""
    return user_config_dir() / "config.toml"


def _keyring():
    """Return the keyring module, or None if unusable on this machine."""
    try:
        import keyring
        from keyring.backends.fail import Keyring as FailKeyring
    except Exception:
        return None
    try:
        if isinstance(keyring.get_keyring(), FailKeyring):
            return None  # no backend (typical on a bare headless box)
    except Exception:
        return None
    return keyring


def _read_config_key() -> str | None:
    return _read_toml_key(_TOML_KEY)


def _read_toml_key(name: str) -> str | None:
    path = config_path()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    import tomllib
    try:
        return tomllib.loads(text).get(name) or None
    except Exception:
        return None


def load_stored_key() -> tuple[str | None, str | None]:
    """The persisted key, ignoring the environment: (key, source) or (None, None).

    Separate from `load_api_key` because a key set for this process only still
    leaves whatever was persisted earlier in place, to be picked up on the next
    start. Callers that need to say so have to look past os.environ.
    """
    kr = _keyring()
    if kr is not None:
        try:
            key = kr.get_password(_KEYRING_SERVICE, _KEYRING_USERNAME)
        except Exception:
            key = None
        if key:
            return key, "keyring"

    key = _read_config_key()
    if key:
        return key, "file"
    return None, None


def load_api_key() -> tuple[str | None, str | None]:
    """Return (key, source) where source is 'env', 'keyring', 'file' or None."""
    env = os.environ.get(ENV_VAR)
    if env:
        return env, "env"
    return load_stored_key()


def store_api_key(key: str) -> tuple[str, str | None]:
    """Persist `key`, preferring the OS credential store.

    Returns (destination, path) where destination is 'keyring' or 'file'.
    """
    kr = _keyring()
    if kr is not None:
        try:
            kr.set_password(_KEYRING_SERVICE, _KEYRING_USERNAME, key)
            return "keyring", None
        except Exception:
            pass  # fall through to the file

    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # json.dumps gives us a correctly-escaped TOML basic string.
    _write_config_key(_TOML_KEY, key)
    if os.name != "nt":
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    return "file", str(path)


def _write_config_key(name: str, key: str) -> None:
    path = config_path()
    import tomllib
    try:
        values = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        values = {}
    values[name] = key
    path.write_text("\n".join(f"{k} = {json.dumps(v)}" for k, v in values.items()) + "\n", encoding="utf-8")


def load_llm_api_key() -> tuple[str | None, str | None]:
    env = os.environ.get(LLM_ENV_VAR)
    if env:
        return env, "env"
    kr = _keyring()
    if kr is not None:
        try:
            key = kr.get_password(_KEYRING_SERVICE, _LLM_KEYRING_USERNAME)
            if key: return key, "keyring"
        except Exception: pass
    key = _read_toml_key(_LLM_TOML_KEY)
    return (key, "file") if key else (None, None)


def store_llm_api_key(key: str) -> tuple[str, str | None]:
    kr = _keyring()
    if kr is not None:
        try:
            kr.set_password(_KEYRING_SERVICE, _LLM_KEYRING_USERNAME, key)
            return "keyring", None
        except Exception: pass
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_config_key(_LLM_TOML_KEY, key)
    if os.name != "nt":
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    return "file", str(path)


def mask(key: str) -> str:
    """A suffix, enough to confirm *which* key is set. Never echo the key."""
    return "…" + key[-4:] if len(key) > 4 else "…"
