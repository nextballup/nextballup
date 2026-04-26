# Privacy / Legal Pack

**These documents are engineering controls and operational playbooks.**
They are **not** legal advice and do **not** constitute the customer-
facing privacy policy. The customer-facing policy is owned by counsel;
these documents describe what the platform actually does so counsel can
draft accurate language.

## Index

| Document | Purpose |
| --- | --- |
| [DSAR_PLAYBOOK.md](./DSAR_PLAYBOOK.md) | How we respond to subject access / erasure / portability requests. |
| [DATA_RETENTION_MATRIX.md](./DATA_RETENTION_MATRIX.md) | Per-class retention horizons (mirror of `docs/soc2/DATA_RETENTION.md`). |
| [MINOR_CONSENT_HANDLING.md](./MINOR_CONSENT_HANDLING.md) | Process for K-12 / youth team uploads. FERPA / COPPA-aware. |
| [DATASET_PROVENANCE.md](./DATASET_PROVENANCE.md) | What every CV dataset / model artifact must record before commercial use. |

## Hard rules baked into the platform

- **Tenant isolation.** RLS-forced policies on every team-scoped table.
  Cross-tenant reads are impossible at the DB layer when the runtime
  role is the app role (`nextballup_app`), regardless of app bugs.
- **Consent before sensitive uploads.** When the team is K-12 or youth
  and `REQUIRE_PRIVACY_CONSENT_FOR_SENSITIVE_UPLOADS=true`, the upload
  endpoint refuses without a current `team_privacy_consents` row that
  covers video upload + CV processing + minors.
- **Email-verified before state-changing actions.** When
  `REQUIRE_VERIFIED_EMAIL_FOR_SENSITIVE_ACTIONS=true`, the gate at
  `nextballup_api.permissions.require_verified_account` blocks team
  creation, invite issuance, video upload, and demo preview until the
  user clicks the link.
- **Audit log immutability.** Database-level triggers reject UPDATE /
  DELETE on `audit_logs`. Off-box archival to object-locked storage is
  the long-term plan (see `docs/soc2/BACKUP_RESTORE.md`).
- **Self-serve export.** `GET /api/v1/auth/me/export` returns a JSON
  bundle of the user's profile, memberships, uploaded videos, and
  audit-actor events.
- **Self-serve erasure.** `DELETE /api/v1/auth/me` anonymizes the row
  (preserving audit lineage), scrubs biometric fields, and revokes
  every refresh session + bumps `session_version`.

## Specific frameworks (engineering posture)

The platform's controls are designed to be compatible with — but not
to claim certification under — the following:

- **GDPR (EU).** Self-serve Art. 15 (access) and Art. 17 (erasure)
  endpoints. Consent ledger + lawful basis flags on
  `team_privacy_consents`. Sub-processor register at
  `docs/soc2/VENDOR_REGISTER.md`.
- **CCPA / CPRA (CA).** Same self-serve endpoints serve "Right to
  Know" / "Right to Delete". Sale of personal information is a flat
  no — there's no flag for it on any plan.
- **COPPA (US, < 13).** The `team_privacy_consents.minors_authorized`
  flag is required for any youth team upload. Verified parental
  consent evidence must be referenced via `evidence_uri` /
  `evidence_sha256`.
- **FERPA (US, K-12).** K-12 institution_type teams require the same
  consent ledger and additionally have a shorter
  `raw_video_retention_days` envelope — set on the billing account.
- **BIPA (IL biometric).** Player biometric fields (height, weight,
  position, handedness, jersey) are only collected with explicit
  `users.biometric_consent=true`. Self-erasure scrubs them.

For every framework above, **counsel review is required before any
customer-facing claim of compliance**.
