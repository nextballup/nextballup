from __future__ import annotations

from ssl import CERT_REQUIRED

from nextballup_worker.celery_app import _resolve_backend, _resolve_broker, create_celery_app

from nextballup_core.settings import Settings


def test_rediss_broker_url_gets_required_ssl_policy() -> None:
    settings = Settings(
        celery_broker_url="rediss://:secret@example.com:6379/0",
        celery_result_backend="rediss://:secret@example.com:6379/0",
    )

    assert _resolve_broker(settings) == (
        "rediss://:secret@example.com:6379/0?ssl_cert_reqs=CERT_REQUIRED"
    )
    assert _resolve_backend(settings) == (
        "rediss://:secret@example.com:6379/0?ssl_cert_reqs=CERT_REQUIRED"
    )


def test_rediss_url_preserves_existing_query_params() -> None:
    settings = Settings(
        celery_broker_url="rediss://example.com:6379/0?socket_timeout=5",
        celery_result_backend="rediss://example.com:6379/1?ssl_cert_reqs=CERT_NONE",
    )

    assert _resolve_broker(settings) == (
        "rediss://example.com:6379/0?socket_timeout=5&ssl_cert_reqs=CERT_REQUIRED"
    )
    assert _resolve_backend(settings) == ("rediss://example.com:6379/1?ssl_cert_reqs=CERT_NONE")


def test_rediss_app_config_sets_explicit_ssl_policy() -> None:
    settings = Settings(
        celery_broker_url="rediss://example.com:6379/0",
        celery_result_backend="rediss://example.com:6379/1",
    )

    app = create_celery_app(settings)

    assert app.conf.broker_use_ssl == {"ssl_cert_reqs": CERT_REQUIRED}
    assert app.conf.redis_backend_use_ssl == {"ssl_cert_reqs": CERT_REQUIRED}
