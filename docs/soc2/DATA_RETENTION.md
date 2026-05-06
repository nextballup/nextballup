# Data Retention Matrix

**Owner:** Privacy / Compliance lead.
**Last reviewed:** 2026-04-25.

## Per-data-class retention

| Data class | Storage | Default retention | How it's deleted | Override path |
| --- | --- | --- | --- | --- |
| User profile (`users` row) | Postgres | While account is active. | `DELETE /api/v1/auth/me` anonymizes (preserves audit lineage) and revokes sessions. | Operator-initiated full delete is a one-shot SQL with explicit ticket. |
| Authentication tokens (`refresh_sessions`) | Postgres | Until `expires_at` or revoked. | Revoked at logout / verify / replay-detected. | Bulk revoke via `session_version++`. |
| Email verification tokens (`email_verification_tokens`) | Postgres | Until `used_at` or expiry; rows kept for audit lineage. | Auto-marked used on supersession. | None. |
| Password reset tokens (`password_reset_tokens`) | Postgres | Until `used_at` or expiry; token values are SHA-256 hashed. | Auto-marked used on supersession or successful reset; pruned by `cleanup_password_reset_tokens`. | `PASSWORD_RESET_TOKEN_TTL_MINUTES`. |
| MFA secrets (`user_totp_secrets`) | Postgres | While the user is enrolled; `disabled_at` marks deactivation but preserves history. | Disable endpoint sets `disabled_at`; full row delete on user deletion. | Force-disable via SQL with operator ticket. |
| MFA recovery codes (`mfa_recovery_codes`) | Postgres | Until `used_at`. Hashed at rest. | Replaced on each successful confirm. | None. |
| Audit log (`audit_logs`) | Postgres + S3 parquet | DB: indefinite (purge when partition exceeds 2 years). S3: 7 years (object lock). | Append-only — DB-level trigger forbids UPDATE/DELETE. | Legal-hold extension only via signed change ticket. |
| Raw uploaded videos | Object storage `nbu-raw-video` | `RAW_VIDEO_RETENTION_DAYS` (default 365) | Worker `cleanup_expired_raw_videos` sweeper after the row hits a terminal state. | Per-team override via plan column `raw_video_retention_days`. |
| Browser-safe mezzanine videos | Object storage `nbu-mezzanine` | 365 days minimum, 7 years maximum (per plan / consent). | Manual delete via team admin tooling (planned). | Customer-requested erasure. |
| CV artifacts (detections, tracks, events, metrics JSON) | Object storage `nbu-clips` / `nbu-reports` | Tied to mezzanine retention. | Bucket lifecycle policy. | None. |
| Demo preview MP4s | Local filesystem | `CV_DEMO_RETENTION_SECONDS` (default 72h). | `cleanup_expired_demo_previews` sweeper. | Dev-only; production must keep `CV_DEMO_PREVIEW_ENABLED=false`. |
| Privacy consent ledger (`team_privacy_consents`) | Postgres | Indefinite while team active; preserved on team archive. | Hard delete only on full tenant offboarding. | Customer can revoke (set `revoked_at`) without deleting the row. |
| Billing rows (`billing_accounts`, `subscriptions`, `usage_events`) | Postgres | 7 years for tax/audit. | Soft-delete `billing_accounts.status='closed'`; hard delete only on tax-window expiry. | None. |

## Deletion paths

- **User self-erasure (Art. 17 / CCPA-Delete):** `DELETE /api/v1/auth/me`
  anonymizes the row, scrubs biometric fields, voids password hash,
  revokes refresh sessions, deactivates memberships. The original
  audit lineage stays intact (FK SET NULL preserves `actor_user_id`
  pointing at an anonymized row).
- **User data export (Art. 15 / CCPA-Access):** `GET /api/v1/auth/me/export`
  returns a JSON bundle of the user's profile, memberships,
  uploaded videos, audit-actor events, auth session metadata, MFA
  enrollment summary, owned billing accounts, recorded consents, and
  attributed CSP reports. Secret material and token hashes are excluded.
- **Tenant offboarding:** Operator-only flow (planned, not yet wired):
  cascade-delete all team-scoped rows, abort multipart uploads, list
  + delete bucket prefixes, write a final audit-log row with the
  operator + reason.
- **Raw video retention:** automatic via the worker sweeper; can be
  accelerated by setting `raw_retention_expires_at` to the past.

## Special cases

- **Minor / K-12 data.** Privacy consent (`team_privacy_consents`)
  must include `minors_authorized=true` *and* `parental_consent_on_file`
  evidence. The privacy consent ledger gate is enforced at upload init
  (`_resolve_upload_privacy_consent`). FERPA-aligned retention rules
  may require shorter horizons for K-12 customers — capture the per-
  customer override on the team's billing account.
- **Biometric data (jersey numbers / position / handedness / heights).**
  These fields exist on `users` and are scrubbed on self-erasure. They
  are only collected with explicit `biometric_consent=true` (Pydantic
  validator + DB column).
- **Legal hold.** A row that would otherwise be deleted is held until
  legal release. Implement as a `retention_locked_until` column when
  the first hold lands; today the manual process is to set the row's
  retention timestamps to the future and document the hold.

## Customer-facing publication

The customer-facing privacy / retention page on the marketing site must
match this matrix. Counsel reviews the public language; engineering
reviews the technical accuracy. Update both together.

## Open work

- [ ] `retention_locked_until` column on `videos` to formalize legal
      hold (currently manual).
- [ ] Tenant offboarding endpoint (admin-only).
- [ ] Audit-log archival job (parquet + S3 object-lock).
