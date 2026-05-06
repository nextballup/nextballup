# Local Alpha Debugging

This profile gives you a fast local loop for alpha bugs without waiting on
Render. It mirrors the alpha channel shape while staying runnable on localhost.

The real Render alpha uses `APP_ENV=staging`. Local alpha intentionally uses
`APP_ENV=development`, because staging correctly refuses localhost frontend
URLs, insecure local cookies, and localhost object-storage endpoints.

## One-time Setup

```bash
cp .env.alpha.local.example .env.alpha.local
cp apps/web/.env.alpha.local.example apps/web/.env.alpha.local

mkdir -p keys
openssl genpkey -algorithm RSA -out keys/jwt-private.pem -pkeyopt rsa_keygen_bits:2048
openssl rsa -pubout -in keys/jwt-private.pem -out keys/jwt-public.pem

docker compose up -d
uv sync
pnpm --dir apps/web install

scripts/local_alpha_migrate.sh
scripts/local_alpha_seed.sh
```

The default `.env.alpha.local` template points at local Docker Postgres, Redis,
and MinIO. To debug Cloudflare R2-specific multipart/CORS behavior, replace the
`S3_*` values in `.env.alpha.local` with the R2 values from Render. Keep those
real values out of git.

## Daily Debug Layout

Run these in separate terminals from the repo root:

```bash
scripts/local_alpha_api.sh
```

```bash
scripts/local_alpha_worker.sh
```

```bash
scripts/local_alpha_beat.sh
```

```bash
scripts/local_alpha_web.sh
```

Then open:

```text
http://localhost:3000
```

## What This Catches Quickly

- Registration gate UI/API mismatches.
- Cookie/CSRF/session behavior.
- Upload initiation, multipart part upload UI, completion, and cancellation.
- Worker transcode and abandoned-upload cleanup behavior.
- Email verification flows through the local JSONL email log.

## What Still Requires Deployed Alpha

- Cloudflare Access behavior.
- Cloudflare edge/CSP/proxy behavior.
- R2 bucket CORS as seen by `https://alpha.nextballup.com`.
- Render memory limits and private-service networking.

## Local Email Links

With `EMAIL_DELIVERY_PROVIDER=logging`, verification and reset links are written
to:

```text
local_artifacts/local_alpha/email_verification.jsonl
```

Tail that file while debugging:

```bash
tail -f local_artifacts/local_alpha/email_verification.jsonl
```

## Safety Notes

- Do not commit `.env.alpha.local` or `apps/web/.env.alpha.local`.
- Do not point local debugging at the Render alpha database unless you
  intentionally want to mutate live alpha data.
- Prefer local MinIO for normal development. Use real R2 only when debugging
  browser multipart upload behavior.
