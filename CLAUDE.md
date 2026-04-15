# NextBallUp

This file is the **lean operational guide for Claude Code**. Read `README.md` first, then this file. Detailed audit history, legal/compliance rationale, and later-phase CV notes live in [AUDIT_DECISIONS.md](./AUDIT_DECISIONS.md).

## Quick Facts

- **Repo state**: backend + Celery worker + Next.js 15 frontend are runnable (auth, teams, games list/detail/PATCH, video upload, placeholder transcode, beat-scheduled cleanup, signed playback delivery, frontend covers auth + games + upload + playback). Real CV execution is still deferred.
- **Current focus**: build upward from the existing backend/frontend/auth/video/worker slice
- **Stack**: Python 3.12.x, FastAPI, PostgreSQL 16, Redis, Next.js 15, Celery
- **Workspace rule**: root `uv` workspace is backend-only; `packages/cv_pipeline` is intentionally excluded for now
- **Auth**: custom FastAPI-issued JWTs in httpOnly cookies; do not add NextAuth/Auth.js
- **License**: Apache-2.0 / MIT / BSD only; no AGPL / GPL / SSPL

## Valid Commands

```bash
# Local services
docker compose up -d

# Root dev tooling plus lightweight backend workspace
uv sync

# Apply database migrations
uv run alembic upgrade head

# Run the FastAPI app (auto-reload in dev, localhost only)
uv run uvicorn nextballup_api.main:app --reload --host 127.0.0.1 --port 8000

# Run the Celery worker (placeholder transcode + maintenance tasks)
uv run celery -A nextballup_worker.celery_app worker --loglevel=info \
  --queues=nextballup.default,nextballup.transcode,nextballup.maintenance

# Run Celery beat (dispatches PENDING jobs + runs stale/abandoned cleanup)
uv run celery -A nextballup_worker.celery_app beat --loglevel=info

# Lint, type-check, test (backend)
uv run ruff check . && uv run ruff format --check .
uv run mypy packages apps tests
uv run pytest

# Frontend (apps/web) — same-origin proxy to the backend via Next.js rewrites
cd apps/web && pnpm install
pnpm dev --hostname 127.0.0.1 --port 3000   # http://127.0.0.1:3000
pnpm test       # vitest + msw
pnpm build
```

`apps/web` now covers auth, games (list + detail + PATCH), video upload, and
processed-video playback. Real CV execution is still deferred — the worker
runs a placeholder `transcode` stage that verifies the uploaded object and
marks the video PROCESSED with a passthrough mezzanine output.

## Current Build Order

Implement in this order unless the task explicitly says otherwise:

1. `packages/core`
2. `packages/db`
3. Alembic environment and first migration
4. `apps/api`
5. Tests
6. Team/invite flows
7. Video metadata and processing-job plumbing
8. Worker behavior
9. Frontend
10. CV pipeline

For Phase 1, stay narrow:

- Build `packages/core`, `packages/db`, `apps/api`, `alembic/`, and tests
- Leave frontend untouched
- Leave CV untouched
- Only touch `apps/worker` or `packages/clip_engine` if workspace packaging requires minimal placeholders

## Minimal Architecture

Active backend workspace members:

- `packages/core`: shared enums, settings, Pydantic schemas, constants
- `packages/db`: SQLAlchemy models, engine/session, repositories, migrations
- `packages/clip_engine`: placeholder package surface for future video work; keep minimal during backend foundation
- `apps/api`: FastAPI app
- `apps/worker`: Celery worker — runtime layer (`runtime/*`) is async-native and directly testable; `tasks.py` wraps each with `asyncio.run` + per-task engine; `celery_app.py` registers beat schedule for PENDING-job dispatch and stale/abandoned cleanup

Active frontend:

- `apps/web`: Next.js 15 (App Router, TypeScript strict, Tailwind 4, TanStack Query, hls.js). Same-origin to the backend via `next.config.ts` rewrites (`/api/v1/*` → `API_UPSTREAM_URL`). Auth flows use httpOnly cookies end-to-end — no tokens in localStorage, no NextAuth.
- Local dev networking is intentionally localhost-only: API/frontend should bind
  to `127.0.0.1`, and `docker-compose.yml` publishes Postgres/Redis/MinIO only
  on `127.0.0.1` as well. Do not loosen this default unless the task
  explicitly requires LAN/device testing.

Standalone for later:

- `packages/cv_pipeline`: intentionally outside root `uv` workspace until the M5 Max arrives and CV work actually begins

## Code Style

- Python 3.12 only
- `from __future__ import annotations` at top of Python files
- Absolute imports within packages
- Pydantic v2
- SQLAlchemy 2.0 style only
- Async FastAPI routes and async DB access
- Ruff + mypy clean for new code
- Add brief comments only when they clarify non-obvious logic

## Hard Constraints

- **Tenant isolation**: PostgreSQL row-level security must be enabled in the initial Alembic migration for every tenant-scoped table
- **App-layer tenancy**: query filters and guards still required even with RLS
- **Security baseline**:
  - bcrypt password hashing
  - RS256 JWTs
  - audit logging for state-changing actions
  - no plaintext secrets/tokens/PII in logs
- **Setup path**: keep CV-heavy dependencies out of default root setup
- **Docs discipline**: only update `README.md` and this file if runnable commands or actual repo workflow changed

## Definition Of Done

A backend feature is done when:

1. The schema/migration exists and is reversible where appropriate
2. The API contract matches `API_SPEC.md` or documents any intentional deviation
3. Auth, validation, and error handling are present
4. Audit logging is added for state-changing operations
5. Tests cover the happy path and meaningful error cases
6. Ruff and mypy pass for the new code
7. No unnecessary dependencies were introduced

## Source Of Truth

Use documents like this:

- `README.md`: repo state and valid setup commands
- `CLAUDE.md`: implementation constraints and build order
- `DATABASE_SCHEMA.md`: model and migration direction
- `API_SPEC.md`: endpoint contracts
- `PRD.md`: product behavior and scope
- `FRONTEND_ARCH.md`: frontend only, when that phase begins
- `AUDIT_DECISIONS.md`: deep security/compliance/CV rationale and later-phase decisions

## Notes For Claude

- Make reasonable assumptions, but document them briefly
- Prefer minimal vertical slices over broad scaffolding
- If workspace packaging blocks `uv sync`, you may add minimal placeholder package structure for existing workspace members that are required for installation
- Do not re-add `packages/cv_pipeline` to the root workspace during backend foundation
