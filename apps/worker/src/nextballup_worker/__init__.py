"""nextballup_worker — Celery worker for NextBallUp processing pipeline.

The worker currently materializes a browser-safe MP4 mezzanine for accepted
uploads, plus beat-scheduled dispatch of PENDING jobs, stale-job recovery, and
abandoned-upload cleanup. Real downstream CV stages still land in subsequent
phases — the runtime and control-plane shape is stable so those additions only
add stages, not surgery.
"""

from __future__ import annotations

__version__ = "0.1.0"
