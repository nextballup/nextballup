# NextBallUp

**AI vision platform for basketball player analysis.** Hidden impact metrics beyond box scores — conversion rates, Spatial IQ, predictive features, and automatic tagging for coaches and recruiters.

## Repo Status

This repository is a **Claude Code-oriented scaffold with a runnable Phase 1 backend foundation**. It now includes the first FastAPI/auth/database slice while still leaving the frontend, worker behavior, and CV pipeline for later phases.

Checked in now:

- Product and system specs
- `docker-compose.yml` for local PostgreSQL, Redis, and MinIO
- Python workspace/package manifests
- Environment template and security/compliance guidance
- FastAPI app foundation (`apps/api`) with auth, teams, games, video upload,
  signed playback delivery
- Celery worker + beat with PENDING-only claim, heartbeat, stale recovery,
  abandoned upload cleanup (`apps/worker`)
- Next.js 15 frontend for auth / games / upload / playback (`apps/web`)
- Core/domain package (`packages/core`)
- Database models and Alembic migrations (`packages/db`, `alembic/`)
- Backend integration tests + frontend vitest suite

Not checked in yet:

- Real CV inference/training in the default setup path (worker runs a
  placeholder `transcode` stage)
- Seed data and richer feature slices beyond the auth / team / video / game
  / playback foundation

## Bootstrap

### Prerequisites

- Python 3.12.x with [uv](https://docs.astral.sh/uv/) (`.python-version` is pinned)
- Node.js 20+ with pnpm
- Docker and Docker Compose
- FFmpeg (`brew install ffmpeg`) when working on ingest, clip generation, or CV

### Local Setup

```bash
# Copy environment config
cp .env.example .env

# Generate JWT keys
mkdir -p keys
openssl genpkey -algorithm RSA -out keys/jwt-private.pem -pkeyopt rsa_keygen_bits:2048
openssl rsa -pubout -in keys/jwt-private.pem -out keys/jwt-public.pem

# Start services (PostgreSQL, Redis, MinIO) — localhost-only port bindings
docker compose up -d

# Install shared dev tooling plus the lightweight backend workspace
uv sync

# Apply database migrations (creates users, teams, team_memberships, audit_logs)
uv run alembic upgrade head

# Run the API (bind localhost only)
uv run uvicorn nextballup_api.main:app --reload --host 127.0.0.1 --port 8000

# Run the Celery worker (needs CELERY_BROKER_URL=redis://127.0.0.1:6379/1 in .env)
uv run celery -A nextballup_worker.celery_app worker --loglevel=info \
  --queues=nextballup.default,nextballup.transcode,nextballup.maintenance

# Run Celery beat (periodic dispatch + stale-job / abandoned-upload cleanup)
uv run celery -A nextballup_worker.celery_app beat --loglevel=info

# Run tests (uses the nextballup_test DB created by infra/scripts/init-db.sql)
uv run pytest
```

### Frontend (`apps/web`)

```bash
cd apps/web
cp .env.example .env.local    # defaults proxy /api/v1 → http://127.0.0.1:8000
pnpm install
pnpm dev --hostname 127.0.0.1 --port 3000   # same-origin via rewrites
pnpm test                      # vitest + msw + @testing-library/react
pnpm build                     # production build
```

The Next.js rewrite keeps the browser same-origin with the backend, so
httpOnly auth cookies work without any cross-site cookie gymnastics. Change
`API_UPSTREAM_URL` to point the rewrite at your production API.

Local development defaults are intentionally locked to `127.0.0.1`. The API,
frontend, PostgreSQL, Redis, and MinIO should not bind to `0.0.0.0` unless
you explicitly need LAN testing from another device.

The CV package is intentionally kept outside the root uv workspace so a plain
`uv sync` never pulls Torch or OpenMMLab dependencies.

## Baseline Decisions

- Tenant isolation is a baseline security control: the initial Alembic migration must enable PostgreSQL row-level security on tenant-scoped tables.
- The auth model is custom FastAPI-issued JWTs stored in httpOnly cookies. Do not add NextAuth/Auth.js as a parallel auth system.
- Logout invalidates issued JWTs by rotating a server-side `session_version`, so old access/refresh tokens stop working even if copied elsewhere.
- Python is pinned to 3.12.x to avoid accidental adoption of unsupported Torch / CV wheels.
- The CV package is intentionally excluded from the root uv workspace until the M5 Max arrives and the CV layer is actively being built.

## Architecture

See [CLAUDE.md](./CLAUDE.md) for the lean operational guide Claude Code should load first. See [AUDIT_DECISIONS.md](./AUDIT_DECISIONS.md) for detailed audit rationale, later-phase CV notes, and security/compliance deep dives.

## Documentation

| Document | Purpose |
|----------|---------|
| [CLAUDE.md](./CLAUDE.md) | Lean operational guide and implementation constraints for Claude Code |
| [AUDIT_DECISIONS.md](./AUDIT_DECISIONS.md) | Detailed audit rationale, later-phase architecture notes, and compliance/CV guidance |
| [PRD.md](./PRD.md) | Product requirements — features and behavioral specs |
| [API_SPEC.md](./API_SPEC.md) | Full REST API specification with schemas |
| [DATABASE_SCHEMA.md](./DATABASE_SCHEMA.md) | SQLAlchemy ORM models and migration strategy |
| [FRONTEND_ARCH.md](./FRONTEND_ARCH.md) | Next.js frontend architecture and design system |

## License

Proprietary — all rights reserved.
