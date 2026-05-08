from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from nextballup_api.billing import CheckoutSession, register_billing_provider
from nextballup_api.main import _validate_startup_secrets
from pydantic import ValidationError

from nextballup_core.demo_preview import _build_demo_preview_env
from nextballup_core.settings import get_settings, reload_settings


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Iterator[None]:
    yield
    reload_settings()


def _set_hardened_production_env(monkeypatch: pytest.MonkeyPatch) -> None:
    class _StartupBillingProvider:
        name = "startup_real_billing"

        def create_checkout_session(
            self,
            *,
            billing_account_id: uuid.UUID,
            plan_code: str,
            success_url: str,
            cancel_url: str,
        ) -> CheckoutSession:
            raise AssertionError("startup validation should not create checkout sessions")

        def cancel_subscription(self, *, external_subscription_id: str) -> None:
            raise AssertionError("startup validation should not cancel subscriptions")

    register_billing_provider("startup_real_billing", lambda _s: _StartupBillingProvider())
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("CV_DEMO_PREVIEW_ENABLED", "false")
    monkeypatch.setenv("FRONTEND_APP_URL", "https://nextballup.com")
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
    monkeypatch.setenv("EMAIL_DELIVERY_PROVIDER", "postmark")
    monkeypatch.setenv("EMAIL_VERIFICATION_FROM_ADDRESS", "no-reply@nextballup.com")
    monkeypatch.setenv("POSTMARK_SERVER_TOKEN", "postmark-token-for-startup-tests")
    monkeypatch.setenv("BILLING_PROVIDER", "startup_real_billing")
    monkeypatch.setenv("REGISTRATION_MODE", "invite_only")
    monkeypatch.setenv("REGISTRATION_INVITE_CODES", "PILOT-CODE-AAAA")
    monkeypatch.setenv("S3_ENDPOINT_URL", "https://example-account.r2.cloudflarestorage.com")
    monkeypatch.setenv("S3_ACCESS_KEY", "r2-access-key-for-startup-tests")
    monkeypatch.setenv("S3_SECRET_KEY", "r2-secret-key-for-startup-tests")
    monkeypatch.setenv("S3_BUCKET_RAW", "nextballup-alpha-raw")
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
    with pytest.raises(RuntimeError, match="DATABASE_URL_RUNTIME or DATABASE_RUNTIME_PASSWORD"):
        _validate_startup_secrets()


def test_startup_validation_can_derive_runtime_db_url_from_managed_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_hardened_production_env(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgresql://owner:ownerpw@db.internal:5432/alpha")
    monkeypatch.setenv("DATABASE_URL_RUNTIME", "")
    monkeypatch.setenv("DATABASE_RUNTIME_USERNAME", "nextballup_app")
    monkeypatch.setenv("DATABASE_RUNTIME_PASSWORD", "runtime secret+/")
    reload_settings()

    settings = get_settings()
    assert settings.database_url == "postgresql+asyncpg://owner:ownerpw@db.internal:5432/alpha"
    assert (
        settings.runtime_database_url()
        == "postgresql+asyncpg://nextballup_app:runtime%20secret%2B%2F@db.internal:5432/alpha"
    )
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
    "env_name",
    ["S3_ENDPOINT_URL", "S3_ACCESS_KEY", "S3_SECRET_KEY", "S3_BUCKET_RAW"],
)
def test_startup_validation_requires_object_storage_in_production(
    monkeypatch: pytest.MonkeyPatch, env_name: str
) -> None:
    _set_hardened_production_env(monkeypatch)
    monkeypatch.setenv(env_name, "")
    reload_settings()
    with pytest.raises(RuntimeError, match="Object storage must be configured"):
        _validate_startup_secrets()


@pytest.mark.parametrize(
    ("endpoint", "expected_message"),
    [
        ("not-a-url", "S3_ENDPOINT_URL must be an absolute https URL"),
        ("http://127.0.0.1:9000", "S3_ENDPOINT_URL must be an absolute https URL"),
        ("https://localhost:9000", "S3_ENDPOINT_URL must not point at localhost"),
    ],
)
def test_startup_validation_rejects_insecure_storage_endpoint_in_production(
    monkeypatch: pytest.MonkeyPatch, endpoint: str, expected_message: str
) -> None:
    _set_hardened_production_env(monkeypatch)
    monkeypatch.setenv("S3_ENDPOINT_URL", endpoint)
    reload_settings()
    with pytest.raises(RuntimeError, match=expected_message):
        _validate_startup_secrets()


def test_startup_validation_rejects_storage_bucket_paths_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_hardened_production_env(monkeypatch)
    monkeypatch.setenv("S3_BUCKET_RAW", "nextballup-alpha-raw/prefix")
    reload_settings()
    with pytest.raises(RuntimeError, match="S3_BUCKET_RAW must be a bucket name"):
        _validate_startup_secrets()


@pytest.mark.parametrize(
    ("env_name", "env_value", "expected_message"),
    [
        ("MFA_SECRET_KEY", "", "MFA_SECRET_KEY must be configured"),
        ("MFA_SECRET_KEY", "short", "MFA_SECRET_KEY must be configured"),
        ("EMAIL_DELIVERY_PROVIDER", "logging", "EMAIL_DELIVERY_PROVIDER must be"),
        ("EMAIL_DELIVERY_PROVIDER", "noop", "EMAIL_DELIVERY_PROVIDER must be"),
        ("BILLING_PROVIDER", "stub", "BILLING_PROVIDER must not be 'stub'"),
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
        ("EMAIL_DELIVERY_PROVIDER", "missing_email_provider", "No email delivery provider"),
        ("BILLING_PROVIDER", "missing_billing_provider", "No billing provider"),
    ],
)
def test_startup_validation_rejects_unknown_providers_in_production(
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


def test_startup_validation_rejects_postmark_without_token_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_hardened_production_env(monkeypatch)
    monkeypatch.setenv("POSTMARK_SERVER_TOKEN", "")
    reload_settings()
    with pytest.raises(RuntimeError, match="POSTMARK_SERVER_TOKEN"):
        _validate_startup_secrets()


def test_startup_validation_rejects_unverified_postmark_sender_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_hardened_production_env(monkeypatch)
    monkeypatch.setenv("EMAIL_VERIFICATION_FROM_ADDRESS", "no-reply@nextballup.invalid")
    reload_settings()
    with pytest.raises(RuntimeError, match="EMAIL_VERIFICATION_FROM_ADDRESS"):
        _validate_startup_secrets()


def test_startup_validation_accepts_billing_disabled_in_staging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_hardened_production_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("FRONTEND_APP_URL", "https://alpha.nextballup.com")
    monkeypatch.setenv("BILLING_PROVIDER", "billing_disabled")
    reload_settings()
    _validate_startup_secrets()


def test_startup_validation_rejects_billing_disabled_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_hardened_production_env(monkeypatch)
    monkeypatch.setenv("BILLING_PROVIDER", "billing_disabled")
    reload_settings()
    with pytest.raises(RuntimeError, match="billing_disabled"):
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


@pytest.mark.parametrize(
    "frontend_url",
    [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://[::1]:3000",
        "/reset-password",
    ],
)
def test_startup_validation_rejects_local_or_relative_frontend_url_in_production(
    monkeypatch: pytest.MonkeyPatch,
    frontend_url: str,
) -> None:
    _set_hardened_production_env(monkeypatch)
    monkeypatch.setenv("FRONTEND_APP_URL", frontend_url)
    reload_settings()
    with pytest.raises(RuntimeError, match="FRONTEND_APP_URL must"):
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

    path_entries = env["PATH"].split(os.pathsep)
    assert path_entries[0] == "/usr/bin"
    assert env["HOME"] == "/Users/tester"
    assert "JWT_PRIVATE_KEY" not in env
    assert "DATABASE_URL" not in env
    assert "CSRF_SECRET" not in env


def test_demo_preview_subprocess_env_allows_uv_binary_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tool_dir = tmp_path / "tools"
    tool_dir.mkdir()
    uv_bin = tool_dir / "uv"
    uv_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("UV_BIN", str(uv_bin))

    env = _build_demo_preview_env()

    assert env["UV_BIN"] == str(uv_bin)
    assert str(tool_dir) in env["PATH"].split(os.pathsep)


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
