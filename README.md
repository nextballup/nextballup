# NextBallUp

**AI vision platform for basketball player analysis.** Hidden impact metrics beyond box scores — conversion rates, Spatial IQ, predictive features, and automatic tagging for coaches and recruiters.

## Quickstart

### Prerequisites

- Python 3.12+ with [uv](https://docs.astral.sh/uv/)
- Node.js 20+ with pnpm
- Docker and Docker Compose
- FFmpeg (`brew install ffmpeg`)

### Setup

```bash
# Clone and enter
git clone https://github.com/nextballup/nextballup.git
cd nextballup

# Copy environment config
cp .env.example .env

# Generate JWT keys
mkdir -p keys
openssl genpkey -algorithm RSA -out keys/jwt-private.pem -pkeyopt rsa_keygen_bits:2048
openssl rsa -pubout -in keys/jwt-private.pem -out keys/jwt-public.pem

# Start services (PostgreSQL, Redis, MinIO)
docker compose up -d

# Install Python dependencies
uv sync

# Run database migrations
uv run alembic upgrade head

# Install frontend dependencies
cd apps/web && pnpm install && cd ../..
```

### Run

```bash
# Terminal 1: API server
uv run fastapi dev apps/api/main.py

# Terminal 2: Celery worker
uv run celery -A apps.worker.celery_app worker --loglevel=info

# Terminal 3: Frontend
cd apps/web && pnpm dev
```

- **API**: http://localhost:8000
- **API Docs**: http://localhost:8000/docs
- **Frontend**: http://localhost:3000
- **MinIO Console**: http://localhost:9001 (minioadmin / minioadmin123)

### Test

```bash
uv run pytest                    # All tests
uv run pytest tests/unit         # Fast unit tests
uv run pytest tests/integration  # Needs Docker services running
```

## Architecture

See [CLAUDE.md](./CLAUDE.md) for the full architecture map, coding conventions, and constraints.

## Documentation

| Document | Purpose |
|----------|---------|
| [CLAUDE.md](./CLAUDE.md) | Project conventions, architecture, and constraints for Claude Code |
| [PRD.md](./PRD.md) | Product requirements — features and behavioral specs |
| [API_SPEC.md](./API_SPEC.md) | Full REST API specification with schemas |
| [DATABASE_SCHEMA.md](./DATABASE_SCHEMA.md) | SQLAlchemy ORM models and migration strategy |
| [FRONTEND_ARCH.md](./FRONTEND_ARCH.md) | Next.js frontend architecture and design system |

## License

Proprietary — all rights reserved.
