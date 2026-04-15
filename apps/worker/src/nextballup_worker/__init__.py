"""nextballup_worker — Celery worker for NextBallUp processing pipeline.

Phase 4 ships the `transcode` placeholder task path, beat-scheduled dispatch of
PENDING jobs, stale-job recovery, and abandoned-upload cleanup. Real transcode
/ CV work lands in subsequent phases — the runtime and control-plane shape is
stable so those additions only add stages, not surgery.
"""

from __future__ import annotations

__version__ = "0.1.0"
