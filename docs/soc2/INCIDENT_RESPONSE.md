# Incident Response Runbook

**Owner:** Engineering on-call lead.
**Last reviewed:** 2026-04-25.

## Severity matrix

| Sev | Definition | Examples | Response time | Page? |
| --- | --- | --- | --- | --- |
| **SEV-1** | Customer-impacting outage, data exposure, or active intrusion. | Auth fully down. Cross-tenant data leak. Suspected stolen DB / object store credentials. Audit log mutated. | < 15 min ack, all-hands. | Yes, immediately. |
| **SEV-2** | Significant degradation. | Upload fails > 25% of attempts. Worker queue backed up > 1h. Refresh-token replay storm. | < 30 min ack. | Yes, primary on-call. |
| **SEV-3** | Single-tenant or single-feature impact, workaround exists. | One team's transcodes failing. Demo preview broken. | < 4h ack. | No (ticket). |
| **SEV-4** | Cosmetic / docs / minor regressions. | Audit page paginates wrong. | Next business day. | No. |

## Roles during an incident

- **Incident commander (IC).** Owns the call, makes go/no-go calls, keeps
  the channel calm, decides when to declare resolved.
- **Operations lead.** Drives mitigations, runs commands, captures
  artifacts.
- **Comms lead.** Owns customer + internal status updates. Posts to the
  status page if SEV-1/2.
- **Scribe.** Pastes timestamps, decisions, command output to the channel.
  Source of truth for the postmortem.

For SEV-3/4, IC + ops can be the same person.

## Declaration

1. Whoever spots it pages on-call.
2. On-call IC opens an incident channel: `#inc-<yyyymmdd>-<short-name>`.
3. IC sets a severity in the channel topic. If unsure, **escalate up**.
4. IC pings the comms lead if SEV-1/2.
5. Scribe starts the timeline file from the
   [`POSTMORTEM_TEMPLATE.md`](./POSTMORTEM_TEMPLATE.md).

## Mitigations (common cases)

- **Suspected credential leak.** Rotate the affected secret (see
  [PRODUCTION_READINESS.md](./PRODUCTION_READINESS.md) → "Rotation
  procedures"). Bump every active user's `session_version` (one-shot SQL:
  `UPDATE users SET session_version = session_version + 1`) to invalidate
  all live tokens.
- **Cross-tenant leak suspected.** Set `CV_PIPELINE_ENABLED=false` and
  block `/videos/upload` at the load balancer if the source isn't pinned.
  Pull the latest hour of audit log for `videos.*` and `teams.*` actions
  and bin by `actor_user_id` × `team_id`.
- **Stolen refresh token replay storm.** The per-user replay-detection
  path automatically revokes the family + bumps `session_version`. If the
  attack is account-wide, run the bulk session-version bump above.
- **Audit log mutation suspected.** Confirm: query `pg_trigger` for
  `trg_audit_logs_no_update` / `trg_audit_logs_no_delete`. If present and
  triggers fired, the row was rejected. If missing, the database was
  altered out-of-band — escalate to SEV-1, snapshot, and rebuild from a
  known-good backup.
- **Worker stuck.** Check `processing_jobs` for jobs with stale heartbeat;
  the periodic recovery sweeper (`recover_stale_jobs`) marks them FAILED
  after `WORKER_STALE_HEARTBEAT_SECONDS`. Manual: bump
  `WORKER_STALE_HEARTBEAT_SECONDS` lower temporarily, restart beat.

## Post-incident

Within 5 business days the IC publishes the postmortem to
`docs/postmortems/yyyy-mm-dd-<title>.md`, using
[`POSTMORTEM_TEMPLATE.md`](./POSTMORTEM_TEMPLATE.md). The postmortem is
distributed to engineering + leadership; SEV-1 postmortems are read in
the next all-hands.

Customer-facing summaries (status page) follow within 24h of resolution.
Counsel reviews customer-facing language for any SEV-1 with data exposure
implications.

## Evidence to capture

For every SEV-1 and SEV-2:

1. The incident channel transcript.
2. Audit-log queries used (SQL + result row counts).
3. Metric screenshots covering the incident window.
4. Commands run and their output.
5. Final postmortem doc.

These land in the quarterly evidence pack — see
[EVIDENCE_COLLECTION.md](./EVIDENCE_COLLECTION.md).
