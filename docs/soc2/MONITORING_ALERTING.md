# Monitoring & Alerting

**Owner:** Platform / SRE.
**Last reviewed:** 2026-04-25.

The platform emits structured JSON request logs (carrying `request_id`),
Celery worker logs, and DB-side audit-log rows. This document defines
which signals matter, how they're observed, and which fire pages.

## Required metrics

| Metric | Source | Threshold | Why |
| --- | --- | --- | --- |
| API request rate / 5xx ratio | Reverse proxy / FastAPI middleware | < 1% 5xx over 5min window | Service availability. |
| API p95 latency | Same | < 800ms outside upload init | UX / cost. |
| Auth failure rate (`auth.login.failed`) | Audit log | > 100/min from one IP → page | Credential stuffing detection. |
| Refresh-replay (`auth.refresh.failed` with `replay_detected` reason) | Audit log | Any spike → page | Account compromise. |
| Email delivery failure (`auth.email_verification.rejected` with `delivery_failed`) | Audit log | > 5/hour → page | Provider outage. |
| Password reset delivery failure (`auth.password_reset.rejected` with `delivery_failed`) | Audit log | > 5/hour → page | Provider outage / account recovery broken. |
| MFA failures (`auth.mfa.totp.disabled` with invalid code) | Audit log | > 10/min/user → page | Targeted attack on a known account. |
| Worker queue depth | Redis | > 100 pending dispatches → page | Worker stalled. |
| Worker stale-job recoveries | Audit log | > 5/hour → page | Worker crash loop. |
| Storage 5xx ratio | Provider | > 1% over 5min | Object storage outage. |
| Quota denials (`billing.quota.denied`) | Audit log | > 50/hour for one account → ticket | Possible misconfigured customer or upsell signal. |
| `audit_logs` table mutation attempt | Postgres trigger | Any → page | The trigger raises; if you see it in metrics, the audit-log immutability invariant just fired. |

## Required alerts

Alerts route through a single paging service. Severity in the alert
matches [INCIDENT_RESPONSE.md](./INCIDENT_RESPONSE.md).

- **SEV-1**: API 5xx ratio > 5% sustained 5min; refresh-replay storm;
  storage failures > 10%; audit-log trigger fires.
- **SEV-2**: Worker queue depth growing for 30min; email delivery
  failure spike; auth failure flood from a single IP.
- **SEV-3**: Quota denials concentrated on a single account.

## Required dashboards

- **Service health** — RPS, 5xx, p50/p95 latency by route.
- **Worker** — Queue depth per stage, success/failure ratio, average
  job duration, stale-job recovery count.
- **Auth** — login success/failure, MFA success/failure, refresh
  rotation throughput, replay-detected count.
- **Billing** — quota check rate, quota denial rate, subscription
  status distribution.
- **Audit log** — events per hour by action, top actors, top resources.

## Logging baseline

- All logs are JSON, single-line.
- `request_id` is the linking key across logs and audit rows.
- Bodies are not logged. Field names are logged on validation errors;
  field values are not.
- Secrets are scrubbed at the formatter — defense in depth on top of
  the deny-prefix env var allowlist used by the worker subprocess
  helper.

## Synthetic checks

External uptime check pings `/health` (no DB dep) every 30s and
`/readyz` (DB + Redis check) every 60s from at least two regions.

## Open work

- [ ] Per-tenant rate-limit dashboards once usage metrics are
      aggregated by `team_id`.
- [ ] Long-term audit-log dashboards (parquet on S3, query via
      Athena).
- [ ] DR-region alerting — match prod alert rules in the failover
      region.
