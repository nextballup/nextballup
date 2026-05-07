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

For browser uploads, add an R2 CORS policy for the alpha host. Multipart
uploads must be able to read each part's `ETag` response header before the app
can call `/videos/{id}/complete`.

```json
[
  {
    "AllowedOrigins": ["https://alpha.nextballup.com"],
    "AllowedMethods": ["PUT", "GET", "HEAD"],
    "AllowedHeaders": ["content-type", "x-amz-*"],
    "ExposeHeaders": ["ETag", "x-amz-checksum-sha256"],
    "MaxAgeSeconds": 3600
  }
]
```

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

The alpha Render worker intentionally uses the POSIX media subprocess sandbox
(`WORKER_MEDIA_SUBPROCESS_SANDBOX=true`) with CPU/output limits instead of the
containerized FFmpeg sandbox. Render does not provide the Docker-in-Docker
shape that the production checklist expects. Keep
`WORKER_MEDIA_CONTAINER_SANDBOX_ENABLED=true` for production/beta transcode
workers on infrastructure that supports that hardened container profile.

The worker must not use Render's small `/tmp` volume for full-game transcode
scratch space. The Blueprint attaches `alpha-worker-media-scratch` at
`/var/data` and sets `WORKER_MEDIA_TEMP_DIR=/var/data/nextballup-transcode`.
If a worker event says `/tmp exceeded the limit of 2GB`, the worker is not on
the current Blueprint/env or the scratch disk is missing.

Alpha playback transcode is optimized for pilot turnaround, not archival
quality. The worker caps playback artifacts at 720p/30fps with
`WORKER_PLAYBACK_MAX_WIDTH=1280`, `WORKER_PLAYBACK_MAX_FPS=30`,
`WORKER_PLAYBACK_CRF=26`, and `WORKER_PLAYBACK_PRESET=superfast`. The raw
upload remains in R2 for retention while available; this setting only affects
the browser playback mezzanine.

## Alpha Detector Preview

The alpha detector preview is disabled by default and must stay separate from
the commercial CV artifact path.

To enable it for private alpha only:

1. Enable `CV_ALPHA_DETECTOR_PREVIEW_ENABLED=true` on the Render API so
   coaches can queue the preview from the video page.
2. Route preview jobs to `CELERY_DEMO_PREVIEW_QUEUE=nextballup.demo_preview`.
   The Render transcode worker intentionally does not consume this queue.
3. Run a separate alpha preview worker with access to the already-trained
   detector config, checkpoint, and eval report, for example:
   `scripts/local_alpha_demo_preview_worker.sh`.
4. Copy `.env.alpha-preview.local.example` to `.env.alpha-preview.local`,
   fill the Render Postgres/Key Value and R2 values locally, then run
   `scripts/local_alpha_demo_preview_preflight.sh`.
5. Set `CV_ALPHA_DETECTOR_CONFIG_PATH`,
   `CV_ALPHA_DETECTOR_CHECKPOINT_PATH`, and
   `CV_ALPHA_DETECTOR_EVAL_REPORT_PATH`.
6. The eval report must identify a basketball `detect` artifact and include
   `internal_alpha_poc_only` and `not_commercial_lineage` in
   `known_failure_modes`.
7. Generated annotated preview MP4s are uploaded to object storage under the
   video artifact prefix and are served through the authenticated API route.
8. Do not insert an `ACTIVE` commercial `cv_model_artifacts` row unless the
   lineage is rights-cleared and `commercial_use_allowed=true`.

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
