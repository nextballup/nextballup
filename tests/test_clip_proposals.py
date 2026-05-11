from __future__ import annotations

import uuid

from nextballup_clips import ClipEvent, build_clip_proposals

PASS_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
SHOT_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")
REJECTED_ID = uuid.UUID("00000000-0000-0000-0000-000000000003")
OVERRIDE_ID = uuid.UUID("00000000-0000-0000-0000-000000000004")
APPROVED_ID = uuid.UUID("00000000-0000-0000-0000-000000000005")
MACHINE_ONLY_ID = uuid.UUID("00000000-0000-0000-0000-000000000006")
UNKNOWN_CONFIDENCE_ID = uuid.UUID("00000000-0000-0000-0000-000000000007")


def test_build_clip_proposals_ranks_high_value_events_and_clamps_to_duration() -> None:
    proposals = build_clip_proposals(
        [
            ClipEvent(
                id=PASS_ID,
                event_type="pass",
                event_time_ms=2_000,
                confidence=0.95,
                review_status="needs_review",
            ),
            ClipEvent(
                id=SHOT_ID,
                event_type="shot_made",
                event_time_ms=58_500,
                confidence=0.78,
                review_status="needs_review",
            ),
        ],
        duration_seconds=60,
    )

    assert [proposal.source_event_id for proposal in proposals] == [SHOT_ID, PASS_ID]
    assert proposals[0].start_time_ms == 54_500
    assert proposals[0].end_time_ms == 60_000
    assert proposals[0].label == "Made shot"
    assert proposals[0].reason == "Alpha made shot candidate at 00:58. Coach review required."
    assert proposals[0].rank_score > proposals[1].rank_score


def test_build_clip_proposals_skips_non_review_queue_events_by_default() -> None:
    proposals = build_clip_proposals(
        [
            ClipEvent(
                id=REJECTED_ID,
                event_type="shot_attempt",
                event_time_ms=4_000,
                confidence=0.99,
                review_status="rejected",
            ),
            ClipEvent(
                id=APPROVED_ID,
                event_type="shot_made",
                event_time_ms=8_000,
                confidence=0.99,
                review_status="approved",
            ),
        ]
    )

    assert proposals == []


def test_build_clip_proposals_uses_bounded_model_window_overrides() -> None:
    proposals = build_clip_proposals(
        [
            ClipEvent(
                id=OVERRIDE_ID,
                event_type="shot_attempt",
                event_time_ms=20_000,
                confidence=0.8,
                metadata={"clip_pre_ms": True, "clip_post_ms": 20_000},
            )
        ],
        duration_seconds=120,
    )

    assert proposals[0].start_time_ms == 16_000
    assert proposals[0].end_time_ms == 35_000


def test_build_clip_proposals_keeps_machine_only_and_downranks_unknown_confidence() -> None:
    proposals = build_clip_proposals(
        [
            ClipEvent(
                id=UNKNOWN_CONFIDENCE_ID,
                event_type="shot_made",
                event_time_ms=8_000,
                confidence=None,
                review_status="needs_review",
            ),
            ClipEvent(
                id=MACHINE_ONLY_ID,
                event_type="pass",
                event_time_ms=10_000,
                confidence=0.9,
                review_status="machine_only",
            ),
        ],
        duration_seconds=30,
    )

    assert [proposal.source_event_id for proposal in proposals] == [
        MACHINE_ONLY_ID,
        UNKNOWN_CONFIDENCE_ID,
    ]
    assert proposals[0].review_status == "machine_only"
