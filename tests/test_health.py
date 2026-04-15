from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio(loop_scope="session")
async def test_health_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert isinstance(body["version"], str) and body["version"]


@pytest.mark.asyncio(loop_scope="session")
async def test_liveness_returns_alive(client: AsyncClient) -> None:
    response = await client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


@pytest.mark.asyncio(loop_scope="session")
async def test_readiness_reports_database_ok(client: AsyncClient) -> None:
    response = await client.get("/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "status": "ready",
        "database": "ok",
        "redis": "not_configured",
        "storage": "not_configured",
    }


@pytest.mark.asyncio(loop_scope="session")
async def test_health_is_also_mounted_under_api_prefix(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.asyncio(loop_scope="session")
async def test_request_id_header_is_echoed(client: AsyncClient) -> None:
    response = await client.get("/health", headers={"X-Request-ID": "rid-abc-123"})
    assert response.status_code == 200
    assert response.headers.get("X-Request-ID") == "rid-abc-123"


@pytest.mark.asyncio(loop_scope="session")
async def test_invalid_request_id_is_replaced(client: AsyncClient) -> None:
    response = await client.get("/health", headers={"X-Request-ID": "bad value with spaces"})
    assert response.status_code == 200
    assert response.headers.get("X-Request-ID") != "bad value with spaces"


@pytest.mark.asyncio(loop_scope="session")
async def test_request_id_generated_when_absent(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    request_id = response.headers.get("X-Request-ID")
    assert request_id and len(request_id) >= 16
