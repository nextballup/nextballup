# Production Readiness Checklist

**Owner:** Engineering lead.
**Last reviewed:** 2026-04-25.

A deploy is **not** production-ready until every box below is checked. CI
and a human reviewer both look at this list during the deploy approval
step (see [CHANGE_MANAGEMENT.md](./CHANGE_MANAGEMENT.md)).

## Secrets and identity

- [ ] `JWT_PRIVATE_KEY` / `JWT_PUBLIC_KEY` provisioned in the secret store
      (not files on disk in the container image). Per-env keypairs.
- [ ] `CSRF_SECRET` set to a high-entropy value (≥ 32 bytes). Different
      per-env. The startup validator refuses to boot otherwise.
- [ ] `MFA_SECRET_KEY` set to a high-entropy value, distinct from
      `CSRF_SECRET` and the JWT private key. Rotation procedure is
      documented in this file.
- [ ] `DATABASE_URL_RUNTIME` points at the `nextballup_app` non-owner role
      (migration `0008`). Owner connection (`DATABASE_URL`) is reserved for
      Alembic and never used at request time.
- [ ] `REDIS_URL` is set; rate limiting falls closed otherwise.
- [ ] No raw provider credentials live in the repo. Email and billing
      provider keys are injected only through the deploy environment.

## Cookies and same-origin

- [ ] `COOKIE_SECURE=true`.
- [ ] `COOKIE_SAMESITE=strict`.
- [ ] `COOKIE_HOST_PREFIX=true`.
- [ ] `COOKIE_DOMAIN` is unset (required for `__Host-` prefix).
- [ ] `FRONTEND_APP_URL` is the exact HTTPS origin for the deployed app
      channel and does not point at localhost. Startup validation refuses
      staging/production localhost values.
- [ ] Frontend reaches the API via a same-origin proxy (Next.js
      `next.config.ts` rewrite, or an equivalent edge rule).
- [ ] CORS origins list (`CORS_ORIGINS`) is the production frontend(s)
      only. No wildcards. No HTTP origins in production.

## Deployment channel

- [ ] The deploy target is one of the channels in
      [DEPLOYMENT_CHANNELS.md](./DEPLOYMENT_CHANNELS.md).
- [ ] `nextballup.com` / `www.nextballup.com` remains a public
      marketing/waitlist/docs surface unless the operator intentionally opens
      public app signups.
- [ ] `alpha.nextballup.com` is locked down at the edge and uses isolated
      staging resources.
- [ ] `beta.nextballup.com` is invite-only and uses isolated production-grade
      resources.
- [ ] Public copy does not claim production CV analytics until a commercial
      dataset, passing eval report, and active commercial artifact exist.

## Data tier

- [ ] PostgreSQL 16 with `uuid-ossp`, `pg_trgm`, `btree_gist` extensions
      pre-loaded.
- [ ] Alembic head matches the deployed image. CI fails the deploy
      otherwise.
- [ ] Daily base backup + continuous WAL archive configured. See
      [BACKUP_RESTORE.md](./BACKUP_RESTORE.md).
- [ ] Object storage bucket lifecycle deletes objects after the configured
      `RAW_VIDEO_RETENTION_DAYS` window even if the worker sweeper falls
      behind.

## Worker

- [ ] Worker container runs the hardened `infra/compose/worker-transcode.hardened.yml`
      shape (read-only root, dropped caps, pids/cpus/memory limits, tmpfs
      tmp). FFmpeg sandbox image present in registry.
- [ ] `WORKER_MEDIA_CONTAINER_SANDBOX_ENABLED=true` for all transcode
      workers.
- [ ] Beat scheduler runs as a single-replica deployment (no double-fire
      on dispatch / cleanup).

## Privacy and consent

- [ ] `REQUIRE_PRIVACY_CONSENT_FOR_SENSITIVE_UPLOADS=true`.
- [ ] `REQUIRE_VERIFIED_EMAIL_FOR_SENSITIVE_ACTIONS=true` (this is the
      env-driven kill-switch over `should_require_verified_email_for_sensitive_actions`).
- [ ] Email delivery provider configured (`EMAIL_DELIVERY_PROVIDER=postmark`
      or an explicitly registered real provider; `logging`/`noop` are dev-only).
      Provider keys are in the deploy secret store, not in the image.
- [ ] Privacy policy / data-retention pages on the marketing site reflect
      the matrix in [DATA_RETENTION.md](./DATA_RETENTION.md).

## CV pipeline

- [ ] `CV_PIPELINE_ENABLED=false` until at least one
      `cv_model_artifacts` row with `commercial_use_allowed=true`,
      `status=active`, and an attached evaluation report exists.
- [ ] `CV_DEMO_PREVIEW_ENABLED=false` (the local demo bridge is dev-only;
      startup refuses to boot otherwise).

## Build and CI

- [ ] CI green on the deploy commit. `ci.yml` and `security.yml` both
      green.
- [ ] No `pip-audit` or `pnpm audit --audit-level high` findings on the
      deploy commit.
- [ ] No new copyleft (GPL/AGPL/SSPL/EUPL/OSL/CPAL) dependencies pulled in.
- [ ] `gitleaks` clean over the full history.

## Observability

- [ ] Structured JSON request logs ship off-box.
- [ ] `/health` and `/readyz` endpoints reachable from the load balancer.
- [ ] Audit log ships to long-term storage (object lock / WORM) within
      24h. See [MONITORING_ALERTING.md](./MONITORING_ALERTING.md).

## Rotation procedures

- **JWT keypair.** Generate the new keypair offline → push to secret
  store as `JWT_PRIVATE_KEY_NEXT` → roll the deployment to make it
  active → keep the old key as `JWT_PRIVATE_KEY_PREV` for one access-token
  TTL window → drop. Refresh tokens survive because they are stateful
  rows.
- **CSRF secret.** Same pattern. The CSRF cookie is short-lived enough
  (24h default) that simply rotating the secret invalidates all live
  tokens within a day; no clients should be holding outstanding CSRF
  tokens past that window.
- **MFA secret.** Hardest to rotate because it decrypts persistent TOTP
  secrets. The current process: provision a new key version, run a
  one-shot job that re-encrypts every `user_totp_secrets.secret_ciphertext`
  with the new key (decrypt with the previous key, encrypt with the new),
  then promote the new key. The `cipher` column reserves room for a
  future `aes-gcm-kms-v1` cipher whose key custody lives in cloud KMS.
- **Database role passwords.** ALTER ROLE in a maintenance window. A
  non-zero downtime rotation is feasible by overlapping two passwords in
  the secret store and rotating each pod with a rolling restart.

## Final go/no-go

The deploy approver signs the bottom of this file (or its evidence pack
twin) with their name + commit SHA. CI rejects deploys whose approval is
older than 24h.
