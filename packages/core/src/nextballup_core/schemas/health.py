from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

DependencyStatus = Literal["ok", "error", "timeout", "not_configured"]


class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str


class LivenessResponse(BaseModel):
    status: Literal["alive"]


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    database: DependencyStatus
    redis: DependencyStatus
    storage: DependencyStatus
