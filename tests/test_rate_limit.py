from __future__ import annotations

import fakeredis.aioredis
import pytest
from nextballup_api.security.rate_limit import enforce_rate_limit
from starlette.requests import Request

from nextballup_core.errors import TooManyRequestsError
from nextballup_core.settings import Settings


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/login",
            "headers": [],
            "client": ("198.51.100.10", 443),
        }
    )


class _FakeRegisteredScript:
    def __init__(self, client: _FakeRedis, script: str) -> None:
        self.client = client
        self.script = script

    async def __call__(self, *, keys: list[str], args: list[int]) -> list[int]:
        self.client.script_calls.append((self.script, keys, args))
        self.client.count += 1
        return [self.client.count, args[0]]


class _FakeRedis:
    def __init__(self) -> None:
        self.script_calls: list[tuple[str, list[str], list[int]]] = []
        self.count = 0
        self.closed = False

    def register_script(self, script: str) -> _FakeRegisteredScript:
        return _FakeRegisteredScript(self, script)

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio(loop_scope="session")
async def test_rate_limit_uses_single_atomic_redis_script(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeRedis()
    monkeypatch.setattr(
        "nextballup_api.security.rate_limit.Redis.from_url",
        lambda *args, **kwargs: fake,
    )
    settings = Settings.model_construct(
        redis_url="redis://localhost:6379/0",
        trusted_proxy_ips=[],
        rate_limit_fail_closed=False,
        app_env="test",
    )

    await enforce_rate_limit(
        request=_request(),
        settings=settings,
        scope="auth",
        subject="Coach@Example.com",
        max_attempts=1,
        window_seconds=60,
    )

    assert fake.closed is True
    assert len(fake.script_calls) == 1
    script, keys, args = fake.script_calls[0]
    assert "INCR" in script
    assert "EXPIRE" in script
    assert keys == ["rate_limit:auth:198.51.100.10:coach@example.com"]
    assert args == [60]


@pytest.mark.asyncio(loop_scope="session")
async def test_rate_limit_returns_retry_after_from_atomic_script(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeRedis()
    fake.count = 1
    monkeypatch.setattr(
        "nextballup_api.security.rate_limit.Redis.from_url",
        lambda *args, **kwargs: fake,
    )
    settings = Settings.model_construct(
        redis_url="redis://localhost:6379/0",
        trusted_proxy_ips=[],
        rate_limit_fail_closed=False,
        app_env="test",
    )

    with pytest.raises(TooManyRequestsError) as exc:
        await enforce_rate_limit(
            request=_request(),
            settings=settings,
            scope="auth",
            subject="coach@example.com",
            max_attempts=1,
            window_seconds=60,
        )

    assert exc.value.details["retry_after_seconds"] == 60
    assert fake.closed is True


@pytest.mark.asyncio(loop_scope="session")
async def test_rate_limit_script_sets_ttl_without_extending_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(
        "nextballup_api.security.rate_limit.Redis.from_url",
        lambda *args, **kwargs: fake,
    )
    settings = Settings.model_construct(
        redis_url="redis://localhost:6379/0",
        trusted_proxy_ips=[],
        rate_limit_fail_closed=False,
        app_env="test",
    )
    key = "rate_limit:auth:198.51.100.10:coach@example.com"

    await enforce_rate_limit(
        request=_request(),
        settings=settings,
        scope="auth",
        subject="coach@example.com",
        max_attempts=10,
        window_seconds=60,
    )
    ttl_after_first_hit = await fake.ttl(key)

    await enforce_rate_limit(
        request=_request(),
        settings=settings,
        scope="auth",
        subject="coach@example.com",
        max_attempts=10,
        window_seconds=120,
    )
    ttl_after_second_hit = await fake.ttl(key)

    assert await fake.get(key) == "2"
    assert ttl_after_first_hit == 60
    assert 0 < ttl_after_second_hit <= ttl_after_first_hit
    assert ttl_after_second_hit != 120
