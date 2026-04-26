from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from nextballup_api.main import _validate_startup_secrets
from pydantic import ValidationError

from nextballup_core.demo_preview import _build_demo_preview_env
from nextballup_core.settings import get_settings, reload_settings


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Iterator[None]:
    yield
    reload_settings()


def _set_hardened_production_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("CV_DEMO_PREVIEW_ENABLED", "false")
    monkeypatch.setenv("CSRF_SECRET", "csrf-production-secret")
    monkeypatch.setenv(
        "DATABASE_URL_RUNTIME",
        "postgresql+asyncpg://nextballup_app:nextballup_app_pw@localhost:5432/nextballup",
    )
    monkeypatch.setenv("COOKIE_SECURE", "true")
    monkeypatch.setenv("COOKIE_SAMESITE", "strict")
    monkeypatch.setenv("COOKIE_HOST_PREFIX", "true")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    monkeypatch.setenv("MFA_SECRET_KEY", "mfa-production-secret-that-is-at-least-32-bytes")
    monkeypatch.setenv("EMAIL_DELIVERY_PROVIDER", "ses")
    monkeypatch.setenv("BILLING_PROVIDER", "stripe")
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


def test_startup_validation_requires_redis_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_hardened_production_env(monkeypatch)
    monkeypatch.setenv("REDIS_URL", "")
    reload_settings()
    with pytest.raises(RuntimeError, match="REDIS_URL must be configured"):
        _validate_startup_secrets()


@pytest.mark.parametrize(
    ("env_name", "env_value", "expected_message"),
    [
        ("MFA_SECRET_KEY", "", "MFA_SECRET_KEY must be configured"),
        ("MFA_SECRET_KEY", "short", "MFA_SECRET_KEY must be configured"),
        ("EMAIL_DELIVERY_PROVIDER", "logging", "EMAIL_DELIVERY_PROVIDER must be"),
        ("EMAIL_DELIVERY_PROVIDER", "noop", "EMAIL_DELIVERY_PROVIDER must be"),
        ("BILLING_PROVIDER", "stub", "BILLING_PROVIDER must be"),
    ],
)
def test_startup_validation_rejects_dev_providers_and_short_mfa_secret_in_production(
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


def test_startup_validation_requires_broker_when_demo_preview_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("CV_DEMO_PREVIEW_ENABLED", "true")
    monkeypatch.setenv("CELERY_BROKER_URL", "")
    reload_settings()
    with pytest.raises(RuntimeError, match="requires CELERY_BROKER_URL"):
        _validate_startup_secrets()


def test_startup_validation_requires_demo_preview_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    training_root = tmp_path / "training"
    training_root.mkdir()
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("CV_DEMO_PREVIEW_ENABLED", "true")
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://127.0.0.1:6379/1")
    monkeypatch.setenv("CV_DEMO_TRAINING_REPO_ROOT", str(training_root))
    monkeypatch.setenv("CV_DEMO_CONFIG_PATH", str(training_root / "missing.yaml"))
    monkeypatch.setenv("CV_DEMO_CHECKPOINT_PATH", str(training_root / "missing.pth"))
    reload_settings()
    with pytest.raises(RuntimeError, match="dependencies are not available"):
        _validate_startup_secrets()


def test_demo_preview_subprocess_env_excludes_platform_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("HOME", "/Users/tester")
    monkeypatch.setenv("JWT_PRIVATE_KEY", "secret")
    monkeypatch.setenv("DATABASE_URL", "postgresql://secret")
    monkeypatch.setenv("CSRF_SECRET", "csrf-secret")

    env = _build_demo_preview_env()

    assert env["PATH"] == "/usr/bin"
    assert env["HOME"] == "/Users/tester"
    assert "JWT_PRIVATE_KEY" not in env
    assert "DATABASE_URL" not in env
    assert "CSRF_SECRET" not in env


def test_sensitive_upload_consent_defaults_on_outside_tests() -> None:
    development = get_settings().model_copy(update={"app_env": "development"})
    production = get_settings().model_copy(update={"app_env": "production"})
    test = get_settings().model_copy(update={"app_env": "test"})

    assert development.should_require_sensitive_upload_consent() is True
    assert production.should_require_sensitive_upload_consent() is True
    assert test.should_require_sensitive_upload_consent() is False


def test_demo_preview_effective_sample_fps_is_capped() -> None:
    settings = get_settings().model_copy(
        update={"cv_demo_sample_fps": 24.0, "cv_demo_max_sample_fps": 4.0}
    )

    assert settings.effective_cv_demo_sample_fps() == 4.0


def test_settings_rejects_invalid_trusted_proxy_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRUSTED_PROXY_IPS", "127.0.0.1,not-an-ip")

    with pytest.raises(ValidationError, match="TRUSTED_PROXY_IPS"):
        reload_settings()
    monkeypatch.setenv("TRUSTED_PROXY_IPS", "[]")
    reload_settings()


def test_settings_accepts_trusted_proxy_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRUSTED_PROXY_IPS", "127.0.0.1,10.0.0.0/8")
    reload_settings()

    settings = get_settings()

    assert settings.trusted_proxy_ips == ["127.0.0.1", "10.0.0.0/8"]


def test_settings_rejects_invalid_cors_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "https://app.example.com,ftp://bad.example.com")

    with pytest.raises(ValidationError, match="CORS_ORIGINS"):
        reload_settings()
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000")
    reload_settings()
