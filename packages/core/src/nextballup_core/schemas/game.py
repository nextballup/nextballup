from __future__ import annotations

import uuid
from datetime import date as _date
from datetime import datetime
from datetime import time as _time

from pydantic import BaseModel, ConfigDict, Field

from nextballup_core.enums import GameStatus, GameType


class CreateGameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    team_id: uuid.UUID
    opponent_name: str | None = Field(default=None, max_length=255)
    game_type: GameType
    date: _date
    time: _time | None = None
    location: str | None = Field(default=None, max_length=255)
    is_home: bool = True
    periods: int = Field(default=4, ge=1, le=10)
    period_length_minutes: int = Field(default=8, ge=1, le=60)
    notes: str | None = Field(default=None, max_length=2000)


class GameSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    team_id: uuid.UUID
    opponent_name: str | None
    game_type: GameType
    date: _date
    time: _time | None
    location: str | None
    is_home: bool
    status: GameStatus
    score_team: int | None = None
    score_opponent: int | None = None
    notes: str | None = None
    periods: int
    period_length_minutes: int
    created_at: datetime


class GameListResponse(BaseModel):
    games: list[GameSummary]
    total: int
    page: int
    per_page: int
    has_next: bool


class UpdateGameRequest(BaseModel):
    """All fields optional — coaches PATCH only what they're changing.

    `status` may be flipped to `completed` once the score is final, or to
    `failed` to mark a botched processing run for ops review. The router
    blocks transitioning out of a terminal state (`completed`/`failed`)
    without an admin role to keep audits unambiguous.
    """

    model_config = ConfigDict(extra="forbid")

    opponent_name: str | None = Field(default=None, max_length=255)
    location: str | None = Field(default=None, max_length=255)
    time: _time | None = None
    is_home: bool | None = None
    periods: int | None = Field(default=None, ge=1, le=10)
    period_length_minutes: int | None = Field(default=None, ge=1, le=60)
    notes: str | None = Field(default=None, max_length=2000)
    score_team: int | None = Field(default=None, ge=0, le=999)
    score_opponent: int | None = Field(default=None, ge=0, le=999)
    status: GameStatus | None = None
