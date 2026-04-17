from __future__ import annotations

from collections.abc import Iterator

import pytest
from nextballup_api.main import _validate_startup_secrets

from nextballup_core.settings import reload_settings


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Iterator[None]:
    yield
    reload_settings()


def _set_hardened_production_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("CSRF_SECRET", "csrf-production-secret")
    monkeypatch.setenv(
        "DATABASE_URL_RUNTIME",
        "postgresql+asyncpg://nextballup_app:nextballup_app_pw@localhost:5432/nextballup",
    )
    monkeypatch.setenv("COOKIE_SECURE", "true")
    monkeypatch.setenv("COOKIE_SAMESITE", "strict")
    monkeypatch.setenv("COOKIE_HOST_PREFIX", "true")
    monkeypatch.delenv("COOKIE_DOMAIN", raising=False)


def test_startup_validation_accepts_hardened_production_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_hardened_production_env(monkeypatch)
    reload_settings()
    _validate_startup_secrets()


def test_startup_validation_requires_explicit_csrf_secret_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_hardened_production_env(monkeypatch)
    monkeypatch.setenv("CSRF_SECRET", "")
    reload_settings()
    with pytest.raises(RuntimeError, match="CSRF_SECRET must be configured"):
        _validate_startup_secrets()


def test_startup_validation_requires_runtime_db_role_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_hardened_production_env(monkeypatch)
    monkeypatch.setenv("DATABASE_URL_RUNTIME", "")
    reload_settings()
    with pytest.raises(RuntimeError, match="DATABASE_URL_RUNTIME must be configured"):
        _validate_startup_secrets()


@pytest.mark.parametrize(
    ("env_name", "env_value", "expected_message"),
    [
        ("COOKIE_SECURE", "false", "COOKIE_SECURE must be true"),
        ("COOKIE_SAMESITE", "lax", "COOKIE_SAMESITE must be 'strict'"),
        ("COOKIE_HOST_PREFIX", "false", "COOKIE_HOST_PREFIX must be true"),
    ],
)
def test_startup_validation_rejects_insecure_cookie_posture_in_production(
    monkeypatch: pytest.MonkeyPatch,
    env_name: str,
    env_value: str,
    expected_message: str,
) -> None:
    _set_hardened_production_env(monkeypatch)
    monkeypatch.setenv(env_name, env_value)
    reload_settings()
    with pytest.raises(RuntimeError, match=expected_message):
        _validate_startup_secrets()


def test_startup_validation_rejects_cookie_domain_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_hardened_production_env(monkeypatch)
    monkeypatch.setenv("COOKIE_DOMAIN", "example.com")
    reload_settings()
    with pytest.raises(RuntimeError, match="COOKIE_DOMAIN must be unset"):
        _validate_startup_secrets()
