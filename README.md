# NextBallUp

**Production-minded basketball film archive and review platform foundation.**
Team management, game organization, upload, processing, and signed playback
are implemented now. Deep computer-vision analytics are still future work.

## Repo Status

This repository is a **Claude Code-oriented scaffold with a runnable Phase 7
backend/worker/frontend foundation**. It includes the current auth, teams,
games, upload, worker, and playback slices while still deferring real CV
inference/training.

Checked in now:

- Product and system specs
- `docker-compose.yml` for local PostgreSQL, Redis, and MinIO
- Python workspace/package manifests
- Environment template and security/compliance guidance
- FastAPI app foundation (`apps/api`) with auth, teams, games, video upload,
  signed playback delivery
- Celery worker + beat with PENDING-only claim, heartbeat, stale recovery,
  abandoned upload cleanup (`apps/worker`)
- Next.js 15 frontend for auth / games / upload / playback (`apps/web`),
  with a per-request CSP nonce middleware and a SHA-256 integrity attestation
  computed in the browser before `/videos/{id}/complete` for uploads ≤ 2 GB
- Admin-only audit log viewer (`GET /api/v1/admin/audit/logs` + `/admin/audit`
  page) for SOC 2 evidence + GDPR subject-access workflows
- Core/domain package (`packages/core`)
- Database models and Alembic migrations (`packages/db`, `alembic/`)
- Backend integration tests + frontend vitest suite

Not checked in yet:

- Real CV inference/training in the default setup path (worker runs a
  real ingest transcode stage that creates a browser-safe MP4 mezzanine, but
  downstream CV inference/training is still deferred)
- Richer analytics/clips/metrics surfaces beyond the current auth / team /
  video / game / playback foundation

Dev-only bridge available:

- The processed-video page can optionally shell out to the sibling training
  repo and render a local annotated MP4 overlay preview from a prototype
  RF-DETR checkpoint. This is gated behind `CV_DEMO_PREVIEW_ENABLED`, only
  allowed in `development`/`test`, and is intentionally not the production
  inference path.

## Bootstrap

### Prerequisites

- Python 3.12.x with [uv](https://docs.astral.sh/uv/) (`.python-version` is pinned)
- Node.js 20+ with pnpm
- Docker and Docker Compose
- FFmpeg (`brew install ffmpeg`) when working on ingest, clip generation, or CV
  (macOS local dev can fall back to the built-in `avconvert`, but FFmpeg
  remains the portable/default path and is required for the broadest codec
  support)

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

# (Optional) Seed a demo coach / player / team / game so the frontend is
# immediately usable. Refuses staging/production and non-local DATABASE_URL
# targets unless you explicitly opt in. Idempotent.
uv run python -m nextballup_api.seed

# Run the API (bind localhost only)
uv run uvicorn nextballup_api.main:app --reload --host 127.0.0.1 --port 8000

# Run the Celery worker (needs CELERY_BROKER_URL=redis://127.0.0.1:6379/1 in .env)
uv run celery -A nextballup_worker.celery_app worker --loglevel=info \
  --queues=nextballup.default,nextballup.transcode,nextballup.maintenance,nextballup.cpu

# Run Celery beat (periodic dispatch + stale-job / abandoned-upload cleanup)
uv run celery -A nextballup_worker.celery_app beat --loglevel=info

# Run tests (uses the nextballup_test DB created by infra/scripts/init-db.sql)
uv run pytest
```

### Optional local detector preview

If the sibling training repo already has a usable local checkpoint, you can
turn on a dev-only overlay preview on the processed video page:

```bash
export CV_DEMO_PREVIEW_ENABLED=true
export CV_DEMO_TRAINING_REPO_ROOT=../nextballup-vision-training
export CV_DEMO_CONFIG_PATH=../nextballup-vision-training/configs/experiments/basketball/detect/rfdetr_demo_local_overfit_v1.yaml
export CV_DEMO_CHECKPOINT_PATH=../nextballup-vision-training/runs/bb_detect_rfdetr_demo_local_overfit_v1/demo-01/checkpoints/checkpoint_best_total.pth
```

The backend will download the processed mezzanine locally, run the sibling
`scripts/local_demo_infer.py`, and serve the generated annotated MP4 back from
`/api/v1/videos/{id}/demo-preview/artifact`. This stays same-origin and
artifact-backed, but it is still a local developer bridge rather than the
long-term runtime/export path.

When this bridge is enabled, the paths above are resolved relative to the repo
root if they are not absolute, and the worker must consume the
`nextballup.cpu` queue so queued preview jobs do not stall silently.
Generated preview artifacts are local-only and are pruned by the maintenance
cleanup pass after `CV_DEMO_RETENTION_SECONDS` (default: 72 hours).

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
- The auth model is custom FastAPI-issued RS256 JWTs delivered **cookie-only**. The access JWT rides in the httpOnly access cookie, the refresh JWT rides in a path-scoped httpOnly refresh cookie (`/api/v1/auth/refresh` only), and `/auth/register`, `/auth/login`, and `/auth/refresh` never return tokens in the JSON body. Do not add NextAuth/Auth.js as a parallel auth system.
- Cookie-authenticated mutations are gated by a double-submit CSRF check — the browser mirrors the readable `nbu_csrf_token` cookie into the `X-CSRF-Token` header; the frontend `apiFetch` helper does this automatically.
- Logout invalidates issued JWTs by rotating a server-side `session_version`, so old access/refresh/playback tokens stop validating even if copied elsewhere.
- In staging/production, request and worker DB traffic runs as the CRUD-only `nextballup_app` role via `DATABASE_URL_RUNTIME`. The owner connection (`DATABASE_URL`) is reserved for Alembic. Local dev may leave `DATABASE_URL_RUNTIME` unset and fall back to the owner URL.
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
