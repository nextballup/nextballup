# Security Audit

Generated: 2026-05-11

Scope: `nextballup/` only. The sibling `nextballup-vision-training/` repo was treated as an external integration target, not as part of this audit.

Method: manual threat modeling, route/data-flow tracing, targeted `rg` scans, dependency audits, git-history checks for committed env/key material, and one local regression test added for a confirmed hardening gap.

## Phase 1 - Threat Model

### Application Inference

- Product surface: basketball film archive/review platform with auth, teams, games, upload, worker processing, signed playback, and admin audit viewing (`README.md:3`, `README.md:20`, `README.md:22`, `README.md:27`).
- Stack: FastAPI API, PostgreSQL 16, Redis, Celery worker, Next.js 15 frontend (`CLAUDE.md:7`, `CLAUDE.md:9`, `CLAUDE.md:93`, `CLAUDE.md:97`).
- Auth model: custom RS256 JWTs in httpOnly cookies; access cookie at `/`, refresh cookie narrowed to `/api/v1/auth/refresh`; cookie mutations require double-submit CSRF (`CLAUDE.md:11`, `README.md:149`, `apps/api/src/nextballup_api/security/cookies.py:61`, `apps/api/src/nextballup_api/security/cookies.py:80`, `apps/api/src/nextballup_api/middleware/csrf.py:43`).
- Tenancy model: PostgreSQL RLS is mandatory, but app-layer team checks are also required (`README.md:148`, `README.md:152`, `CLAUDE.md:120`, `CLAUDE.md:121`, `apps/api/src/nextballup_api/permissions.py:68`, `apps/api/src/nextballup_api/permissions.py:93`).
- High-value data classes: user identity/session state, team memberships, games, raw videos, processed mezzanine/playback artifacts, demo preview artifacts, audit logs, billing/usage rows, MFA secrets, password-reset/email-verification tokens (`README.md:20`, `README.md:22`, `README.md:27`, `packages/core/src/nextballup_core/settings.py:180`, `packages/core/src/nextballup_core/settings.py:172`, `apps/api/src/nextballup_api/billing.py:1`).
- External integrations: object storage via S3-compatible presigning, Redis for abuse controls/Celery, Postmark email, Render deployment, optional alpha detector preview, and provider-registered billing (`packages/core/src/nextballup_core/settings.py:76`, `packages/core/src/nextballup_core/settings.py:168`, `apps/api/src/nextballup_api/email_delivery.py:148`, `render.yaml:12`, `render.yaml:66`, `apps/api/src/nextballup_api/billing.py:12`).
- Deployment surface: Render private API service, workers, Redis, Postgres, public web service at `alpha.nextballup.com`, and disabled preview environments (`render.yaml:1`, `render.yaml:12`, `render.yaml:66`, `render.yaml:165`, `render.yaml:176`, `render.yaml:190`).

### Trust Boundaries And Assets

- Browser to Next.js/API boundary: same-origin `/api/v1` rewrite forwards to API upstream (`apps/web/next.config.ts:64`, `apps/web/next.config.ts:68`).
- Cookie-auth boundary: ambiguous cookie plus Bearer credentials are rejected (`apps/api/src/nextballup_api/deps.py:27`, `apps/api/src/nextballup_api/deps.py:36`).
- CSRF boundary: only cookie-authenticated mutating requests are blocked; Bearer-only requests bypass by design (`apps/api/src/nextballup_api/middleware/csrf.py:59`, `apps/api/src/nextballup_api/middleware/csrf.py:68`, `apps/api/src/nextballup_api/middleware/csrf.py:74`).
- Tenant boundary: `require_team_member` and `require_team_coach` gate team data and writes; admin bypass is explicit (`apps/api/src/nextballup_api/permissions.py:68`, `apps/api/src/nextballup_api/permissions.py:72`, `apps/api/src/nextballup_api/permissions.py:93`).
- Storage boundary: upload URLs are presigned, uploaded object metadata is rechecked, and playback URLs are capped to token TTL (`apps/api/src/nextballup_api/routers/videos.py:337`, `apps/api/src/nextballup_api/routers/videos.py:813`, `apps/api/src/nextballup_api/routers/videos.py:314`).
- Worker boundary: externally supplied media is decoded by FFmpeg either directly or through the optional container wrapper (`apps/worker/src/nextballup_worker/runtime/media.py:298`, `apps/worker/src/nextballup_worker/runtime/media.py:260`).
- Admin boundary: cross-tenant audit log viewer requires platform admin role (`apps/api/src/nextballup_api/routers/admin.py:61`, `apps/api/src/nextballup_api/routers/admin.py:94`).

### Top 10 Likely Attack Paths

1. Session theft/replay through refresh JWT reuse. Status: mitigated by cookie-only transport, path-scoped refresh cookie, refresh-session table, replay revocation, and `session_version` checks (`packages/core/src/nextballup_core/schemas/auth.py:94`, `apps/api/src/nextballup_api/security/cookies.py:76`, `apps/api/src/nextballup_api/routers/auth.py:342`, `apps/api/src/nextballup_api/routers/auth.py:353`, `apps/api/src/nextballup_api/deps.py:61`).
2. CSRF against cookie-authenticated mutations. Status: mitigated by HMAC double-submit middleware except for intentional auth-bootstrap exemptions (`packages/core/src/nextballup_core/settings.py:351`, `packages/core/src/nextballup_core/settings.py:358`, `apps/api/src/nextballup_api/middleware/csrf.py:65`, `apps/api/src/nextballup_api/middleware/csrf.py:74`).
3. IDOR across teams, games, and videos. Status: no confirmed IDOR found; routes clear/bind tenant context and call membership/coach guards before returning tenant data or mutating resources (`apps/api/src/nextballup_api/routers/games.py:197`, `apps/api/src/nextballup_api/routers/games.py:204`, `apps/api/src/nextballup_api/routers/videos.py:1521`, `apps/api/src/nextballup_api/routers/videos.py:1527`, `apps/api/src/nextballup_api/routers/teams.py:567`, `apps/api/src/nextballup_api/routers/teams.py:569`).
4. Role escalation through mass assignment. Status: no confirmed issue; request schemas forbid extra fields and admin self-registration is rejected (`packages/core/src/nextballup_core/schemas/auth.py:36`, `packages/core/src/nextballup_core/schemas/auth.py:60`, `packages/core/src/nextballup_core/schemas/team.py:18`, `packages/core/src/nextballup_core/schemas/video.py:40`).
5. Upload abuse, path traversal, type smuggling, or object tampering. Status: core path is hardened; file names reject separators/`..`, content type and extension must match, size bounds are enforced, and completed objects are rechecked (`apps/api/src/nextballup_api/routers/videos.py:371`, `apps/api/src/nextballup_api/routers/videos.py:401`, `apps/api/src/nextballup_api/routers/videos.py:414`, `apps/api/src/nextballup_api/routers/videos.py:439`, `apps/api/src/nextballup_api/routers/videos.py:813`).
6. FFmpeg decoder compromise from attacker-controlled media. Status: finding F-002; production requires container sandbox, but staging allows direct subprocess sandboxing and current Render alpha sets container sandbox off (`apps/worker/src/nextballup_worker/tasks.py:271`, `render.yaml:240`, `render.yaml:242`, `apps/worker/src/nextballup_worker/runtime/media.py:298`).
7. Demo preview artifact leakage after membership revocation. Status: finding F-003; demo-preview storage redirects use a 7200-second presigned URL rather than the playback token verifier path (`packages/core/src/nextballup_core/settings.py:113`, `apps/api/src/nextballup_api/routers/videos.py:1879`, `apps/api/src/nextballup_api/routers/videos.py:1887`, `apps/api/src/nextballup_api/routers/videos.py:1702`).
8. Secrets in repository or runtime logs. Status: no committed `.env`/key files found in the tracked tree; `.gitignore` ignores env and key material (`.gitignore:139`, `.gitignore:215`). Dev logging provider records full email links but staging/production startup rejects `logging`/`noop` providers (`apps/api/src/nextballup_api/email_delivery.py:95`, `apps/api/src/nextballup_api/main.py:146`).
9. Payment/billing webhook or amount tampering. Status: no live webhook surface in this repo; billing is an interface with stub/disabled providers and production refuses the stub (`apps/api/src/nextballup_api/billing.py:12`, `apps/api/src/nextballup_api/billing.py:104`, `apps/api/src/nextballup_api/billing.py:131`, `apps/api/src/nextballup_api/main.py:155`).
10. CI/infra privilege escalation or secret exposure. Status: no `pull_request_target` found; CI and security workflows use read-only repository permissions and include dependency/secret scanning (`.github/workflows/ci.yml:3`, `.github/workflows/ci.yml:8`, `.github/workflows/security.yml:7`, `.github/workflows/security.yml:62`).

## Phase 2 - Targeted Scan Notes

- OAuth: not implemented in this repo. No `oauth`, `redirect_uri`, or callback route was found by targeted search.
- Billing webhooks: not implemented in this repo. Search found provider-interface references only, not Stripe webhook handlers.
- SSRF: no user-controlled URL fetcher found in API routes. Postmark uses a fixed API URL (`apps/api/src/nextballup_api/email_delivery.py:157`, `apps/api/src/nextballup_api/email_delivery.py:198`), and object storage endpoints are deployment configuration validated as HTTPS/non-local in staging/production (`apps/api/src/nextballup_api/main.py:100`, `apps/api/src/nextballup_api/main.py:102`, `apps/api/src/nextballup_api/main.py:104`).
- Dependency audit: `pnpm audit --audit-level high --prod` returned no known vulnerabilities. `uv run --no-sync pip-audit --strict --desc --requirement /tmp/nbu-audit-requirements.txt --no-deps --disable-pip` returned no known vulnerabilities.
- Secrets/history: tracked env/key-like files are examples only. Local untracked `.env*` and `keys/*.pem` files exist, but `.gitignore` covers `.env`, `.env.*.local`, `keys/`, and `*.pem` (`.gitignore:139`, `.gitignore:140`, `.gitignore:141`, `.gitignore:215`, `.gitignore:216`).

## Phase 3 - Validation

- Added a regression test showing production startup rejects `APP_DEBUG=true`
  (`tests/test_startup_hardening.py`).
- Ran `uv run pytest tests/test_startup_hardening.py::test_startup_validation_rejects_debug_mode_in_production`; result: `PASS`.
- Existing worker startup tests already validate the staging-vs-production sandbox split: production rejects missing container sandbox, while staging accepts subprocess-only sandboxing (`tests/test_worker.py:1780`, `tests/test_worker.py:1794`, `tests/test_worker.py:1808`, `tests/test_worker.py:1823`).
- Existing video tests validate the demo preview artifact path issues a 7200-second storage URL (`tests/test_videos.py:1649`, `tests/test_videos.py:1652`).

## Ranked Findings

| ID | Severity | File:line | Attack path | Exploit scenario | Blast radius | Confidence | Suggested patch |
| --- | --- | --- | --- | --- | --- | --- | --- |
| F-001 | Medium | `packages/core/src/nextballup_core/settings.py:54`, `apps/api/src/nextballup_api/main.py:126`, `apps/api/src/nextballup_api/main.py:236` | Debug-mode information disclosure | A production or staging deploy omits `APP_DEBUG=false`; startup previously passed every other hardening check and FastAPI could boot with debug mode enabled. A request that triggers an unhandled exception can expose traceback/code-path detail to the client. | API internals, code paths, exception messages; can accelerate follow-on attacks. | 0.95 | Resolved in this change: staging/production startup now rejects `APP_DEBUG=true`. |
| F-002 | Medium | `render.yaml:240`, `render.yaml:242`, `apps/worker/src/nextballup_worker/tasks.py:271`, `apps/worker/src/nextballup_worker/runtime/media.py:298`, `Dockerfile.backend:14`, `scripts/render_runtime.sh:44` | Malicious media decode reaches worker process | An attacker with upload rights submits a valid-looking video that triggers a decoder exploit. In staging, current config runs FFmpeg as a direct subprocess. The backend image now defines an unprivileged `nextballup` runtime user and Render start scripts drop long-running processes after preparing writable runtime directories. | Staging worker container, object-storage/DB-adjacent environment, uploaded film pipeline. | 0.84 | Residual: require containerized media sandbox for staging too, or block staging upload processing until an equivalent isolation boundary exists. |
| F-003 | Medium | `packages/core/src/nextballup_core/settings.py:113`, `apps/api/src/nextballup_api/routers/videos.py:1879`, `apps/api/src/nextballup_api/routers/videos.py:1887`, `tests/test_videos.py:1652` | Demo preview artifact persists after access change | A team member fetches the demo preview artifact URL, is removed from the team, then continues using the presigned storage URL until its 7200-second expiry. Normal playback is session-aware and membership-checked at verify time, but demo preview redirects are not. | Annotated preview MP4 for a team video, for up to the configured storage URL TTL. | 0.90 | Do not shorten the alpha URL TTL until access is moved behind a session-verified streaming/proxy path; the longer TTL is an explicit alpha operability tradeoff for large preview MP4s. |

## Finding Details

### F-001 - `settings.py` / `main.py`

Evidence:

- `app_debug` defaults to `True` (`packages/core/src/nextballup_core/settings.py:54`).
- The staging/production validation block now appends an `APP_DEBUG` failure.
- `create_app()` passes the setting directly into FastAPI (`apps/api/src/nextballup_api/main.py:236`, `apps/api/src/nextballup_api/main.py:239`).
- Render alpha sets `APP_DEBUG=false`, and startup now fails closed if a future
  staging/production deploy omits it (`render.yaml:200`, `render.yaml:202`).

Previously possible exploit:

1. Operator sets `APP_ENV=production` and all required secrets/storage/Redis settings, but omits `APP_DEBUG=false`.
2. Startup rejects the configuration because `_validate_startup_secrets()` checks debug mode.
3. The app does not boot, so no framework debug output is exposed.

Residual action:

- Keep the normal regression test that rejects `APP_DEBUG=true` in staging/production.

### F-002 - `apps/worker` / `Dockerfile.backend` / `render.yaml`

Evidence:

- Production worker startup requires `WORKER_MEDIA_CONTAINER_SANDBOX_ENABLED=true` (`apps/worker/src/nextballup_worker/tasks.py:273`).
- Staging accepts either container sandbox or direct subprocess sandbox (`apps/worker/src/nextballup_worker/tasks.py:275`, `apps/worker/src/nextballup_worker/tasks.py:280`).
- Current Render alpha explicitly sets direct subprocess sandbox on and container sandbox off (`render.yaml:240`, `render.yaml:242`).
- Direct mode runs `subprocess.run(...)` for FFmpeg when the container flag is false (`apps/worker/src/nextballup_worker/runtime/media.py:306`, `apps/worker/src/nextballup_worker/runtime/media.py:308`, `apps/worker/src/nextballup_worker/runtime/media.py:312`).
- The safer container command already exists and uses no network, read-only filesystem, capability drop, no-new-privileges, non-root container user, and tmpfs (`apps/worker/src/nextballup_worker/runtime/media.py:267`, `apps/worker/src/nextballup_worker/runtime/media.py:271`, `apps/worker/src/nextballup_worker/runtime/media.py:279`, `apps/worker/src/nextballup_worker/runtime/media.py:280`, `apps/worker/src/nextballup_worker/runtime/media.py:282`, `apps/worker/src/nextballup_worker/runtime/media.py:284`, `apps/worker/src/nextballup_worker/runtime/media.py:286`).
- `Dockerfile.backend` defines a non-login `nextballup` user (`uid=10001`, `gid=10001`) and installs `gosu` so Render start scripts can prepare mounted writable paths as root, then drop privileges before starting API, beat, or worker processes (`Dockerfile.backend:14`, `scripts/render_runtime.sh:44`).

Concrete exploit:

1. Attacker with coach/upload access initiates and completes an upload for a crafted video.
2. Worker downloads the object and invokes FFmpeg directly (`apps/worker/src/nextballup_worker/runtime/media.py:455`, `apps/worker/src/nextballup_worker/runtime/media.py:480`, `apps/worker/src/nextballup_worker/runtime/media.py:483`).
3. If the media parser is compromised, code runs in the worker container rather than in the existing networkless container sandbox path.

Patch:

- Make staging match production for untrusted media: require `WORKER_MEDIA_CONTAINER_SANDBOX_ENABLED=true` before workers start.
- If Render cannot provide the configured container runtime from inside the worker, route transcode work to a separately isolated service/job image instead of accepting subprocess-only FFmpeg for staging film.
- The non-root runtime user is implemented. Keep the Render start-script guard that prepares `/var/data/nextballup-transcode` and `$TMPDIR`, then drops to `nextballup`, so mounted disk permissions do not regress.

### F-003 - `routers/videos.py`

Evidence:

- Normal playback deliberately caps presigned URL lifetime to the shorter playback token TTL (`apps/api/src/nextballup_api/routers/videos.py:314`, `apps/api/src/nextballup_api/routers/videos.py:316`, `apps/api/src/nextballup_api/routers/videos.py:343`).
- Playback verify rechecks token/video/team match and current team membership (`apps/api/src/nextballup_api/routers/videos.py:1688`, `apps/api/src/nextballup_api/routers/videos.py:1700`, `apps/api/src/nextballup_api/routers/videos.py:1703`, `apps/api/src/nextballup_api/routers/videos.py:1705`).
- Demo preview artifact access checks membership before issuing the storage redirect (`apps/api/src/nextballup_api/routers/videos.py:1876`, `apps/api/src/nextballup_api/routers/videos.py:1877`).
- The returned demo-preview storage URL uses `settings.demo_preview_url_expires_seconds`, which defaults to 7200 seconds (`apps/api/src/nextballup_api/routers/videos.py:1884`, `apps/api/src/nextballup_api/routers/videos.py:1887`, `packages/core/src/nextballup_core/settings.py:113`).
- Existing test coverage asserts the current 7200-second value is in the returned redirect URL (`tests/test_videos.py:1649`, `tests/test_videos.py:1652`).

Concrete exploit:

1. Legitimate member requests `/api/v1/videos/{video_id}/demo-preview/artifact`.
2. API verifies current membership and redirects to a presigned object-storage URL.
3. Member is immediately removed from the team.
4. The object-storage URL remains valid until expiry because object storage cannot call back into `require_team_member`.

Patch:

- Keep the 7200-second alpha TTL for now as an explicit operability tradeoff
  for large detector-preview MP4s; shortening it immediately reintroduces
  expired-range-request failures during long preview playback.
- Follow-up patch: move demo-preview playback behind a session-verified
  streaming/proxy path or issue a demo-preview playback token that can be
  verified through the same membership/session-version path used by normal
  playback. Once that path exists, shorten the storage URL TTL.

## Systemic Issues

- Configuration hardening is strong and now includes `APP_DEBUG=false` as a
  staging/production startup invariant.
- Media isolation has two security postures: production requires the stronger container path, while staging accepts subprocess-only decoding (`apps/worker/src/nextballup_worker/tasks.py:273`, `apps/worker/src/nextballup_worker/tasks.py:275`). For customer or pilot film, staging should be treated as sensitive.
- Playback security has a strong shared pattern, but demo-preview artifacts bypass part of it. Normal playback is token and session-version aware (`apps/api/src/nextballup_api/routers/videos.py:343`, `apps/api/src/nextballup_api/routers/videos.py:1691`); demo preview uses a direct storage redirect with a longer TTL (`apps/api/src/nextballup_api/routers/videos.py:1884`, `packages/core/src/nextballup_core/settings.py:113`).
