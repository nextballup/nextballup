"""Unit tests for the defense-in-depth filename sanitizer.

The API-layer validator is expected to reject unsafe filenames before they
ever reach storage. These tests are about the *second* line of defense —
if a future code path constructs a storage key with a weaker validator
(admin backfill, reprocessing job, etc.), the key must still be safe.
"""

from __future__ import annotations

import pytest
from nextballup_api.storage import (
    _sanitize_filename_component,
    storage_key_for_video,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("clip.mp4", "clip.mp4"),
        ("Game 1 vs Lincoln.mp4", "Game_1_vs_Lincoln.mp4"),
        ("../../etc/passwd", "etc_passwd"),
        ("null\x00byte.mp4", "null_byte.mp4"),
        ("control\x1fchars.mp4", "control_chars.mp4"),
        ("..hidden..mp4", "hidden__mp4"),
        ("中文.mp4", "mp4"),  # non-ASCII → collapsed to underscores, then trimmed
        ("", "upload"),
        ("///", "upload"),
        ("...", "upload"),
    ],
)
def test_sanitize_filename_component(raw: str, expected: str) -> None:
    assert _sanitize_filename_component(raw) == expected


def test_sanitize_filename_truncates_long_names() -> None:
    long = "a" * 500 + ".mp4"
    assert len(_sanitize_filename_component(long)) == 200


def test_storage_key_structure_is_stable() -> None:
    """Tenant + video ID must come first in the key so RLS-equivalent prefix
    scoping at the bucket layer (IAM conditions) stays enforceable."""
    key = storage_key_for_video(team_id="team-1", video_id="vid-2", filename="clip.mp4")
    assert key == "raw/team-1/vid-2/clip.mp4"


def test_storage_key_with_traversal_filename_stays_safe() -> None:
    key = storage_key_for_video(team_id="team-1", video_id="vid-2", filename="../../../root.mp4")
    # Exactly four path segments: `raw/{tenant}/{video}/{sanitized}`.
    assert key.count("/") == 3
    assert ".." not in key
