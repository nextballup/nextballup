from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

DEFAULT_LIMIT = 25
MIN_WINDOW_MS = 1_000
MAX_MODEL_WINDOW_MS = 15_000


@dataclass(frozen=True, slots=True)
class ClipEvent:
    id: uuid.UUID
    event_type: str
    event_time_ms: int
    confidence: float | None = None
    review_status: str = "needs_review"
    created_at: datetime | None = None
    metadata: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ClipProposal:
    id: str
    source_event_id: uuid.UUID
    event_type: str
    label: str
    reason: str
    start_time_ms: int
    end_time_ms: int
    rank_score: float
    confidence: float | None
    review_status: str
    created_at: datetime | None = None


_WINDOWS_MS: dict[str, tuple[int, int]] = {
    "shot_attempt": (4_000, 6_000),
    "shot_made": (4_000, 7_000),
    "rebound": (3_000, 5_000),
    "pass": (2_000, 4_000),
}
_DEFAULT_WINDOW_MS = (3_000, 5_000)

_LABELS: dict[str, str] = {
    "shot_attempt": "Shot attempt",
    "shot_made": "Made shot",
    "rebound": "Rebound",
    "pass": "Pass",
}

_EVENT_WEIGHTS: dict[str, float] = {
    "shot_made": 0.98,
    "shot_attempt": 0.92,
    "rebound": 0.72,
    "pass": 0.42,
}

_REVIEW_BONUS: dict[str, float] = {
    "needs_review": 0.04,
    "machine_only": 0.02,
}
_REVIEW_QUEUE_STATUSES = {"needs_review", "machine_only"}


def build_clip_proposals(
    events: Iterable[ClipEvent],
    *,
    duration_seconds: float | None = None,
    limit: int = DEFAULT_LIMIT,
    include_rejected: bool = False,
) -> list[ClipProposal]:
    """Turn event rows into deterministic review-queue clip windows."""

    if limit <= 0:
        return []

    duration_ms = _duration_ms(duration_seconds)
    proposals: list[ClipProposal] = []
    for event in events:
        if event.review_status not in _REVIEW_QUEUE_STATUSES and not (
            include_rejected and event.review_status == "rejected"
        ):
            continue
        center_ms = _clip_center_ms(event.event_time_ms, duration_ms)
        pre_ms, post_ms = _window_for_event(event)
        start_ms = max(0, center_ms - pre_ms)
        end_ms = center_ms + post_ms
        if duration_ms is not None:
            end_ms = min(duration_ms, end_ms)
            if end_ms <= start_ms:
                end_ms = duration_ms
                start_ms = max(0, duration_ms - MIN_WINDOW_MS)
        if end_ms <= start_ms:
            continue

        label = _LABELS.get(event.event_type, _humanize_event_type(event.event_type))
        proposals.append(
            ClipProposal(
                id=f"event:{event.id}",
                source_event_id=event.id,
                event_type=event.event_type,
                label=label,
                reason=_reason(label, event),
                start_time_ms=start_ms,
                end_time_ms=end_ms,
                rank_score=_rank_score(event),
                confidence=event.confidence,
                review_status=event.review_status,
                created_at=event.created_at,
            )
        )

    proposals.sort(
        key=lambda proposal: (
            -proposal.rank_score,
            proposal.start_time_ms,
            proposal.source_event_id,
        )
    )
    return proposals[:limit]


def _duration_ms(duration_seconds: float | None) -> int | None:
    if duration_seconds is None or duration_seconds <= 0:
        return None
    return int(duration_seconds * 1_000)


def _clip_center_ms(event_time_ms: int, duration_ms: int | None) -> int:
    center_ms = max(0, event_time_ms)
    if duration_ms is not None:
        center_ms = min(center_ms, duration_ms)
    return center_ms


def _window_for_event(event: ClipEvent) -> tuple[int, int]:
    pre_ms, post_ms = _WINDOWS_MS.get(event.event_type, _DEFAULT_WINDOW_MS)
    metadata = event.metadata or {}
    return (
        _window_override(metadata.get("clip_pre_ms"), default=pre_ms),
        _window_override(metadata.get("clip_post_ms"), default=post_ms),
    )


def _window_override(value: object, *, default: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        return default
    return max(MIN_WINDOW_MS, min(MAX_MODEL_WINDOW_MS, value))


def _rank_score(event: ClipEvent) -> float:
    confidence = _bounded_confidence(event.confidence)
    event_weight = _EVENT_WEIGHTS.get(event.event_type, 0.35)
    review_bonus = _REVIEW_BONUS.get(event.review_status, 0.0)
    return round(min(1.0, (event_weight * 0.4) + (confidence * 0.56) + review_bonus), 4)


def _bounded_confidence(value: float | None) -> float:
    if value is None:
        return 0.0
    return max(0.0, min(1.0, value))


def _reason(label: str, event: ClipEvent) -> str:
    timestamp = _format_time(event.event_time_ms)
    return f"Alpha {label.lower()} candidate at {timestamp}. Coach review required."


def _format_time(value_ms: int) -> str:
    total_seconds = max(0, value_ms) // 1_000
    minutes, seconds = divmod(total_seconds, 60)
    return f"{minutes:02d}:{seconds:02d}"


def _humanize_event_type(value: str) -> str:
    return value.replace("_", " ").strip().title() or "Event"
