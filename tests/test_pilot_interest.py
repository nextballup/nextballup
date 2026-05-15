from __future__ import annotations

import fakeredis.aioredis
import nextballup_api.routers.marketing as marketing_router
import pytest
from httpx import AsyncClient
from nextballup_api.deps import get_app_settings
from nextballup_api.main import app
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nextballup_core.constants import AuditAction, ErrorCode
from nextballup_core.settings import Settings
from nextballup_db.models.audit import AuditLog

API = "/api/v1"


def _payload(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "full_name": "  Jamie Pilot ",
        "email": "jamie@example.com",
        "role": "head_coach",
        "organization": "Central Rec Basketball",
        "message": "Interested in alpha access for a 12-game season.",
    }
    body.update(overrides)
    return body


@pytest.mark.asyncio(loop_scope="session")
async def test_pilot_interest_accepts_unauthenticated_submission(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    response = await client.post(f"{API}/pilot-interest", json=_payload())

    assert response.status_code == 202, response.text
    assert response.json() == {"status": "received"}

    row = (
        (
            await db_session.execute(
                select(AuditLog).where(AuditLog.action == AuditAction.PILOT_INTEREST_RECEIVED)
            )
        )
        .scalars()
        .one()
    )
    assert row.actor_email == "jamie@example.com"
    assert row.resource_type == "pilot_interest"
    assert row.team_id is None
    assert row.actor_user_id is None
    extra = row.extra or {}
    # Whitespace on full_name is stripped before persistence.
    assert extra["full_name"] == "Jamie Pilot"
    assert extra["role"] == "head_coach"
    assert extra["organization"] == "Central Rec Basketball"
    assert "Interested in alpha access" in (extra["message"] or "")


@pytest.mark.asyncio(loop_scope="session")
async def test_pilot_interest_response_does_not_echo_input(
    client: AsyncClient,
) -> None:
    response = await client.post(
        f"{API}/pilot-interest",
        json=_payload(full_name="Reflected Probe", email="probe@example.com"),
    )

    assert response.status_code == 202, response.text
    body = response.text
    # Neutral response — never reflects submitter content (anti-reflected-XSS
    # discipline + prevents enumerating which submissions made it through).
    assert "Reflected Probe" not in body
    assert "probe@example.com" not in body
    assert "Central Rec" not in body


@pytest.mark.asyncio(loop_scope="session")
async def test_pilot_interest_rejects_malformed_email(
    client: AsyncClient,
) -> None:
    response = await client.post(
        f"{API}/pilot-interest",
        json=_payload(email="not-an-email"),
    )

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == ErrorCode.VALIDATION_FAILED


@pytest.mark.asyncio(loop_scope="session")
async def test_pilot_interest_rejects_unknown_role(
    client: AsyncClient,
) -> None:
    response = await client.post(
        f"{API}/pilot-interest",
        json=_payload(role="superstar"),
    )

    assert response.status_code == 422


@pytest.mark.asyncio(loop_scope="session")
async def test_pilot_interest_rejects_extra_fields(
    client: AsyncClient,
) -> None:
    response = await client.post(
        f"{API}/pilot-interest",
        json={**_payload(), "tracking_id": "abc"},
    )

    assert response.status_code == 422


@pytest.mark.asyncio(loop_scope="session")
async def test_pilot_interest_is_rate_limited(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
) -> None:
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(
        "nextballup_api.security.rate_limit.Redis.from_url",
        lambda *args, **kwargs: fake,
    )
    monkeypatch.setattr(marketing_router, "_PILOT_RATE_LIMIT_ATTEMPTS", 1)
    settings = Settings.model_construct(
        redis_url="redis://localhost:6379/0",
        trusted_proxy_ips=[],
        rate_limit_fail_closed=False,
        app_env="test",
    )
    app.dependency_overrides[get_app_settings] = lambda: settings
    try:
        first = await client.post(f"{API}/pilot-interest", json=_payload())
        second = await client.post(f"{API}/pilot-interest", json=_payload())
    finally:
        app.dependency_overrides.pop(get_app_settings, None)
        await fake.aclose()

    assert first.status_code == 202
    assert second.status_code == 429
    assert second.json()["error"]["code"] == ErrorCode.RATE_LIMITED

    # The rejected attempt is also audited so abusive sources are visible.
    rejected_count = await db_session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(AuditLog.action == AuditAction.PILOT_INTEREST_REJECTED)
    )
    assert rejected_count == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_pilot_interest_does_not_require_csrf_token(
    client: AsyncClient,
) -> None:
    # The endpoint is in csrf_exempt_paths so a missing/invalid CSRF header
    # must not block a marketing submission. (Authenticated coach mutations
    # in other routers still require the header.)
    response = await client.post(
        f"{API}/pilot-interest",
        json=_payload(),
        headers={"x-csrf-token": "definitely-not-valid"},
    )
    assert response.status_code == 202
