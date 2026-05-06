# Alpha Render Deployment

**Channel:** `alpha.nextballup.com`  
**Environment:** `APP_ENV=staging`  
**Purpose:** private platform/CV POC, not commercial launch.

These steps deploy the app surface without enabling production CV analytics.
`CV_PIPELINE_ENABLED=false` and `CV_DEMO_PREVIEW_ENABLED=false` remain required
until a rights-cleared commercial artifact exists.

## Services

The root `render.yaml` blueprint creates:

- `nextballup-alpha-web` — public frontend, custom domain
  `alpha.nextballup.com`, Render subdomain disabled.
- `nextballup-alpha-api` — private Render service only; browsers never call it
  directly.
- `nextballup-alpha-worker` — Celery worker for transcode/cleanup jobs.
- `nextballup-alpha-beat` — single beat scheduler for dispatch/maintenance.
- `nextballup-alpha-db` — isolated alpha Postgres.
- `nextballup-alpha-redis` — isolated alpha Redis-compatible key-value store.

## Required Operator Inputs

Render will prompt for these `sync: false` values on first Blueprint creation:

- `JWT_PRIVATE_KEY`
- `JWT_PUBLIC_KEY`
- `POSTMARK_SERVER_TOKEN`
- `REGISTRATION_INVITE_CODES`
- `S3_ENDPOINT_URL`
- `S3_ACCESS_KEY`
- `S3_SECRET_KEY`

Use the same R2 values for API, worker, and beat when Render prompts for them.
The `nextballup-alpha-shared-secrets` environment group generates shared
`DATABASE_RUNTIME_PASSWORD`, `CSRF_SECRET`, and `MFA_SECRET_KEY` values once,
so API/worker/beat derive the same runtime database role connection.

## JWT Keys

Generate a dedicated alpha keypair. Do not reuse local dev keys.

```bash
openssl genrsa -out /tmp/nextballup-alpha-jwt-private.pem 3072
openssl rsa -in /tmp/nextballup-alpha-jwt-private.pem \
  -pubout -out /tmp/nextballup-alpha-jwt-public.pem
```

Paste the full PEM contents into Render secrets:

- `JWT_PRIVATE_KEY` gets `nextballup-alpha-jwt-private.pem`
- `JWT_PUBLIC_KEY` gets `nextballup-alpha-jwt-public.pem`

## Postmark

Configure Postmark before the first successful alpha deploy:

1. Verify `nextballup.com` or a sender such as `no-reply@nextballup.com`.
2. Add the DKIM/return-path DNS records Postmark gives you in Cloudflare DNS.
3. Use a server token as `POSTMARK_SERVER_TOKEN`.
4. Keep `EMAIL_VERIFICATION_FROM_ADDRESS=no-reply@nextballup.com`.

## Cloudflare R2

Create an alpha-only bucket:

- bucket: `nextballup-alpha-raw`
- endpoint: `https://<cloudflare-account-id>.r2.cloudflarestorage.com`
- access key: scoped to this bucket
- secret key: matching R2 secret
- region: `auto`

Do not store alpha raw footage in the public marketing Worker or in the repo.

## Render Creation

1. Render Dashboard -> Blueprints -> New Blueprint.
2. Select the Git repo containing `render.yaml`.
3. Use the root `render.yaml`.
4. Enter the required secret values.
5. Create the Blueprint.
6. Confirm the API predeploy step runs:

```bash
alembic upgrade head
python scripts/configure_runtime_db_role.py
```

The API must remain a private service. The frontend should call it via
`API_UPSTREAM_HOSTPORT`, not a public API hostname.

## Cloudflare Cutover

Keep the current `nextballup-alpha-holding` Worker until Render is green.
After Render creates the custom domain target for `alpha.nextballup.com`:

1. Remove `alpha.nextballup.com` from the holding Worker custom domains.
2. In Cloudflare DNS, create the CNAME Render requests for
   `alpha.nextballup.com`.
3. Keep the existing Cloudflare Access application for `alpha.nextballup.com`.
4. Test in incognito: Access login first, then the NextBallUp app.

Do not add `nextballup.com`, `www.nextballup.com`, or `beta.nextballup.com` to
this alpha Render service.

## Acceptance Checks

- `https://alpha.nextballup.com` requires Cloudflare Access login.
- Render `nextballup-alpha-web` has no usable `*.onrender.com` subdomain.
- API is private service only.
- `/api/v1/auth/registration/status` reports invite-only and does not leak
  codes.
- New user registration requires an invite code.
- Password reset and email verification send through Postmark.
- Upload init works against R2.
- Worker transcodes a small non-sensitive test clip.
- No CV analytics claims are visible in product copy.
