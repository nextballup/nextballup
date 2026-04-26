# SOC 2 Readiness Pack

**These documents are *readiness controls*, not certification evidence.**
SOC 2 Type 1 / Type 2 attestation is issued only by an external auditor after
they observe the controls in operation across the audit window. Nothing in
this folder claims compliance; everything here is intended to (a) make the
controls explicit so engineering and operations stay aligned and (b) shorten
the future audit by giving counsel and the auditor a complete map of what
the platform does today.

## Index

| Document | Purpose |
| --- | --- |
| [PRODUCTION_READINESS.md](./PRODUCTION_READINESS.md) | Pre-deploy checklist: secrets, dependencies, monitoring, key rotation. |
| [INCIDENT_RESPONSE.md](./INCIDENT_RESPONSE.md) | Severity matrix, on-call rotation, declaration / escalation playbook, postmortem template. |
| [BACKUP_RESTORE.md](./BACKUP_RESTORE.md) | Backup cadence, retention, restore drill cadence, verification scripts. |
| [ACCESS_REVIEW.md](./ACCESS_REVIEW.md) | Who reviews what, on what cadence. Quarterly checklist + evidence shape. |
| [VENDOR_REGISTER.md](./VENDOR_REGISTER.md) | Sub-processor template + review cadence. |
| [DATA_RETENTION.md](./DATA_RETENTION.md) | Per-data-class retention horizons, deletion paths, exceptions. |
| [CHANGE_MANAGEMENT.md](./CHANGE_MANAGEMENT.md) | Required PR controls, migration safety rules, deploy approvals. |
| [EVIDENCE_COLLECTION.md](./EVIDENCE_COLLECTION.md) | Quarterly evidence pack contents and storage. |
| [MONITORING_ALERTING.md](./MONITORING_ALERTING.md) | Required metrics, alerts, dashboards, ownership. |
| [MFA_LOGIN_CHALLENGE.md](./MFA_LOGIN_CHALLENGE.md) | Future-work design for the MFA prompt at login (today only enrollment endpoints exist). |
| [SSO_FUTURE.md](./SSO_FUTURE.md) | SAML / OIDC architecture sketch, scoped to enterprise plans. |

## Document conventions

- **Owner.** Every doc names a single owner role. If your role is the owner,
  the doc is yours to keep current.
- **Last reviewed.** Update this each quarter even if the doc didn't change.
  An auditor reads the dates first.
- **Evidence type.** Each control says explicitly what evidence proves it
  was operating: a config file, a CI run id, an audit-log query, a screenshot.

## What this folder is not

- A privacy policy. See [`docs/privacy/`](../privacy/README.md) for the
  privacy / data-processing pack.
- An SLA / customer-facing terms document.
- A security marketing page.
- A substitute for counsel review when you're about to claim compliance to
  a customer or in marketing copy.

## Customer claims

Until SOC 2 attestation is in hand, customer-facing language is restricted
to:

- "SOC 2 readiness program in progress; controls listed in security
  package available under NDA."
- Specific control descriptions backed by the docs in this folder.

Do **not** claim "SOC 2 compliant" or "SOC 2 Type II" anywhere outward-
facing without the actual attestation letter.
