# Backup & Restore Runbook

**Owner:** Platform / SRE.
**Last reviewed:** 2026-04-25.

## Backups in scope

| Asset | Cadence | Retention | Storage class |
| --- | --- | --- | --- |
| PostgreSQL base backup | Daily, off-peak | 35 days | Object lock + cross-region replicate |
| PostgreSQL WAL archive | Continuous | 35 days | Object lock + cross-region replicate |
| Audit log table snapshot | Weekly to S3 (parquet) | 7 years | Object lock |
| Object-storage media (raw / mezzanine / artifacts) | Native bucket versioning | Mezzanine 365d, raw per `RAW_VIDEO_RETENTION_DAYS` | S3 versioning + cross-region replicate |
| Email-verification token table | Snapshotted with the rest of the DB | DB retention | DB |
| Password-reset token table | Snapshotted with the rest of the DB | DB retention | DB |
| Billing tables (plans, accounts, subscriptions, usage_events) | DB | DB | DB |

Secrets, JWT keys, and MFA cipher keys are **not** backed up alongside
the database. They live in the deploy secret store (e.g. AWS Secrets
Manager) and have their own rotation and recovery process.

## RPO / RTO targets

- **RPO** (data loss tolerance): ≤ 5 minutes for the database (continuous
  WAL). ≤ 24h for media (daily replication).
- **RTO** (time to restore): ≤ 4h for SEV-1 outages. The quarterly
  restore drill below verifies this is achievable.

## Quarterly restore drill

Performed by the on-call lead each quarter. The drill is the only proof
the backups actually work.

1. Pick a backup older than 24h.
2. Spin up an isolated `nextballup_restore_<quarter>` Postgres instance.
3. Restore the base backup + WAL up to a chosen recovery target time.
4. Run the smoke check script (`infra/scripts/restore-drill-smoke.sh` —
   needs to be added before the first drill; see "Open work" below).
5. Verify:
   - Alembic `head` matches the prod head.
   - Row counts of `users`, `teams`, `videos`, `audit_logs` are within
     1% of prod (allowing for the recovery cutoff).
   - A spot-check tenant's RLS policies still filter rows.
6. Tear down. File the drill report at
   `docs/evidence/<yyyy-qN>/restore-drill.md`.

## Restore from disaster

For a real outage, the on-call IC owns the call. High-level steps:

1. Confirm scope: schema corruption vs. row-level mistake vs. wholesale
   data loss.
2. Stop write traffic at the load balancer (return 503 from `/api/v1/*`).
3. If row-level: write a targeted SQL fix in a transaction with a
   pre-commit assertion on row counts. Apply during a freeze window.
4. If wholesale: restore the most recent base backup + replay WAL to the
   chosen recovery target time. Promote, sanity-check, then unfreeze.
5. Audit log row count comparison: run
   `SELECT count(*) FROM audit_logs` before and after — restore must not
   shrink the audit log.

## Object-storage restore

Native bucket versioning lets us restore deleted or overwritten objects:

```bash
aws s3api list-object-versions \
    --bucket nbu-raw-video \
    --prefix raw/<team_id>/<video_id>/ \
    | jq '.Versions[] | {VersionId, Key, LastModified}'
aws s3api copy-object \
    --bucket nbu-raw-video \
    --copy-source 'nbu-raw-video/<key>?versionId=<id>' \
    --key '<key>'
```

Cross-region replication is the disaster-region fallback. The DR runbook
(separate doc, owned by SRE) has the full failover steps.

## Open work

- [ ] `infra/scripts/restore-drill-smoke.sh` does not exist yet — write
      it before the first drill.
- [ ] Audit log parquet snapshot job is documented but not deployed.
      Implement before the first SOC 2 audit window starts.
- [ ] Object-lock retention durations differ across regions — confirm
      they all meet the 7y audit log requirement.

## Evidence

Each drill produces:

- `docs/evidence/<yyyy-qN>/restore-drill.md` — narrative + commands.
- Time-to-restore measurement.
- Row-count comparisons.
- Sign-off: drill owner + reviewer.
