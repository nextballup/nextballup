from __future__ import annotations

from collections.abc import Iterator

import pytest
from httpx import AsyncClient
from nextballup_worker.observability import (
    _reset_worker_metrics_server_for_tests,
    start_worker_metrics_server,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nextballup_core.constants import AuditAction
from nextballup_core.settings import Settings, reload_settings
from nextballup_db.models.audit import AuditLog

API = "/api/v1"


@pytest.fixture(autouse=True)
def _reload_settings_after_test() -> Iterator[None]:
    yield
    reload_settings()


@pytest.mark.asyncio(loop_scope="session")
async def test_metrics_endpoint_requires_shared_token_and_audits_rejection(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OBSERVABILITY_METRICS_ENABLED", "true")
    monkeypatch.setenv("OBSERVABILITY_METRICS_TOKEN", "metrics-secret")
    reload_settings()

    rejected = await client.get(f"{API}/_metrics")
    assert rejected.status_code == 403, rejected.text
    audit = await db_session.scalar(
        select(AuditLog).where(AuditLog.action == AuditAction.METRICS_SCRAPE_REJECTED)
    )
    assert audit is not None
    assert (audit.extra or {}).get("has_presented_token") is False

    accepted = await client.get(f"{API}/_metrics", headers={"X-Metrics-Token": "metrics-secret"})
    assert accepted.status_code == 200, accepted.text
    assert accepted.headers["content-type"].startswith("text/plain; version=0.0.4")


@pytest.mark.asyncio(loop_scope="session")
async def test_metrics_endpoint_exposes_api_counters(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OBSERVABILITY_METRICS_ENABLED", "true")
    monkeypatch.setenv("OBSERVABILITY_METRICS_TOKEN", "metrics-secret")
    reload_settings()

    csp = await client.post(
        f"{API}/_csp-report",
        json={"csp-report": {"violated-directive": "script-src", "blocked-uri": "inline"}},
    )
    assert csp.status_code == 204, csp.text
    weird = await client.post(
        f"{API}/_csp-report",
        json={"csp-report": {"violated-directive": "x-" + ("a" * 80)}},
    )
    assert weird.status_code == 204, weird.text

    response = await client.get(f"{API}/_metrics", headers={"X-Metrics-Token": "metrics-secret"})
    assert response.status_code == 200, response.text
    body = response.text
    assert 'api_csp_reports_total{directive="script-src"}' in body
    assert 'api_csp_reports_total{directive="other"}' in body
    assert "x-aaaaaaaa" not in body


def test_worker_metrics_server_starts_on_loopback_offset_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reset_worker_metrics_server_for_tests()
    calls: list[tuple[int, str, object]] = []

    def fake_start_http_server(port: int, *, addr: str, registry: object) -> None:
        calls.append((port, addr, registry))

    monkeypatch.setattr(
        "nextballup_worker.observability.start_http_server",
        fake_start_http_server,
    )
    monkeypatch.setattr("nextballup_worker.observability._process_port_offset", lambda: 2)

    settings = Settings(
        observability_worker_metrics_enabled=True,
        observability_worker_metrics_port=9108,
        observability_worker_metrics_port_span=8,
    )
    endpoint = start_worker_metrics_server(settings)
    again = start_worker_metrics_server(settings)

    assert endpoint == ("127.0.0.1", 9110)
    assert again == endpoint
    assert calls == [(9110, "127.0.0.1", calls[0][2])]


def test_worker_metrics_host_must_be_loopback() -> None:
    with pytest.raises(ValueError, match="loopback"):
        Settings(
            observability_worker_metrics_enabled=True,
            observability_worker_metrics_host="0.0.0.0",
        )
