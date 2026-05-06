from __future__ import annotations

import pytest
from httpx import AsyncClient

from nextballup_core.constants import ErrorCode

API = "/api/v1"


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize(
    "path",
    [
        f"{API}/videos/upload",
        f"{API}/teams/join",
    ],
)
async def test_static_action_paths_do_not_fall_through_to_uuid_routes(
    client: AsyncClient, path: str
) -> None:
    response = await client.get(path)

    assert response.status_code == 405
    body = response.json()
    assert body["error"]["code"] == ErrorCode.METHOD_NOT_ALLOWED
    assert "uuid_parsing" not in response.text


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize(
    "path",
    [
        f"{API}/videos/not-a-video-id",
        f"{API}/games/not-a-game-id",
        f"{API}/teams/not-a-team-id",
    ],
)
async def test_non_uuid_resource_ids_do_not_trigger_path_validation_noise(
    client: AsyncClient, path: str
) -> None:
    response = await client.get(path)

    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == ErrorCode.NOT_FOUND
    assert "uuid_parsing" not in response.text
