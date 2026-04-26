from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import fakeredis.aioredis
import nextballup_api.routers.csp as csp_router
import pytest
from httpx import AsyncClient
from nextballup_api.deps import get_app_settings
from nextballup_api.main import app
from nextballup_worker.runtime.cleanup import cleanup_expired_csp_reports
from sqlalchemy import delete, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from nextballup_core.constants import ErrorCode
from nextballup_core.settings import Settings
from nextballup_db.models.csp import CspReport
from nextballup_db.models.user import User

API = "/api/v1"


def _csp_payload() -> dict[str, object]:
    return {
        "csp-report": {
            "document-uri": "https://app.nextballup.test/games/1",
            "violated-directive": "script-src-elem",
            "blocked-uri": "https://evil.example/script.js",
            "source-file": "https://app.nextballup.test/static/app.js",
            "line-number": 12,
            "column-number": 4,
        }
    }


@pytest.mark.asyncio(loop_scope="session")
async def test_csp_report_is_persisted(client: AsyncClient, db_session: AsyncSession) -> None:
    response = await client.post(
        f"{API}/_csp-report",
        content=json.dumps(_csp_payload()),
        headers={"content-type": "application/csp-report", "user-agent": "csp-test-agent"},
    )

    assert response.status_code == 204
    report = (await db_session.execute(select(CspReport))).scalars().one()
    assert report.document_uri == "https://app.nextballup.test/games/1"
    assert report.violated_directive == "script-src-elem"
    assert report.blocked_uri == "https://evil.example/script.js"
    assert report.source_file == "https://app.nextballup.test/static/app.js"
    assert report.line_number == 12
    assert report.column_number == 4
    assert report.user_agent == "csp-test-agent"
    assert report.reporter_ip == "127.0.0.1"
    assert report.user_id is None


@pytest.mark.asyncio(loop_scope="session")
async def test_csp_report_is_attributed_when_access_cookie_is_valid(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    register = await client.post(
        f"{API}/auth/register",
        json={
            "email": "csp-user@example.com",
            "password": "Password1!",
            "full_name": "CSP User",
            "role": "coach",
        },
    )
    assert register.status_code == 201
    user = await db_session.scalar(select(User).where(User.email == "csp-user@example.com"))
    assert user is not None

    response = await client.post(
        f"{API}/_csp-report",
        content=json.dumps(_csp_payload()),
        headers={"content-type": "application/csp-report"},
    )

    assert response.status_code == 204
    report = (await db_session.execute(select(CspReport))).scalars().one()
    assert report.user_id == user.id


@pytest.mark.asyncio(loop_scope="session")
async def test_csp_reports_are_append_only(client: AsyncClient, db_session: AsyncSession) -> None:
    response = await client.post(
        f"{API}/_csp-report",
        content=json.dumps(_csp_payload()),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 204
    report = (await db_session.execute(select(CspReport))).scalars().one()
    report_id = report.id

    with pytest.raises(DBAPIError):
        await db_session.execute(
            update(CspReport).where(CspReport.id == report_id).values(blocked_uri="rewritten")
        )
        await db_session.commit()
    await db_session.rollback()

    with pytest.raises(DBAPIError):
        await db_session.execute(delete(CspReport).where(CspReport.id == report_id))
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio(loop_scope="session")
async def test_expired_csp_reports_prune_through_controlled_db_function(
    db_session: AsyncSession,
) -> None:
    old = CspReport(
        received_at=datetime.now(tz=UTC) - timedelta(days=91),
        document_uri="https://app.nextballup.test/old",
        violated_directive="script-src",
        user_agent="old-agent",
        reporter_ip="127.0.0.1",
    )
    fresh = CspReport(
        received_at=datetime.now(tz=UTC),
        document_uri="https://app.nextballup.test/fresh",
        violated_directive="script-src",
        user_agent="fresh-agent",
        reporter_ip="127.0.0.1",
    )
    db_session.add_all([old, fresh])
    await db_session.commit()

    pruned = await cleanup_expired_csp_reports(
        db_session,
        settings=Settings.model_construct(csp_report_retention_days=90),
    )

    assert pruned == 1
    remaining = (await db_session.scalars(select(CspReport))).all()
    assert [r.document_uri for r in remaining] == ["https://app.nextballup.test/fresh"]


@pytest.mark.asyncio(loop_scope="session")
async def test_csp_report_rejects_oversized_payload(client: AsyncClient) -> None:
    response = await client.post(
        f"{API}/_csp-report",
        content=json.dumps({"csp-report": {"document-uri": "x" * 9000}}),
        headers={"content-type": "application/csp-report"},
    )

    assert response.status_code == 413


@pytest.mark.asyncio(loop_scope="session")
async def test_csp_report_is_rate_limited(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(
        "nextballup_api.security.rate_limit.Redis.from_url",
        lambda *args, **kwargs: fake,
    )
    monkeypatch.setattr(csp_router, "CSP_REPORT_RATE_LIMIT_ATTEMPTS", 1)
    settings = Settings.model_construct(
        redis_url="redis://localhost:6379/0",
        trusted_proxy_ips=[],
        rate_limit_fail_closed=False,
        app_env="test",
    )
    app.dependency_overrides[get_app_settings] = lambda: settings
    try:
        first = await client.post(
            f"{API}/_csp-report",
            content=json.dumps(_csp_payload()),
            headers={"content-type": "application/csp-report"},
        )
        second = await client.post(
            f"{API}/_csp-report",
            content=json.dumps(_csp_payload()),
            headers={"content-type": "application/csp-report"},
        )
    finally:
        app.dependency_overrides.pop(get_app_settings, None)
        await fake.aclose()

    assert first.status_code == 204
    assert second.status_code == 429
    assert second.json()["error"]["code"] == ErrorCode.RATE_LIMITED
