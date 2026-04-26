# Evidence Collection

**Owner:** Security / Compliance lead.
**Cadence:** Quarterly (year-quarter folders under `docs/evidence/`).

The auditor reads evidence, not policies. The pack below is what we
preserve every quarter so that a future audit can reach back across the
audit window without scrambling.

## Per-quarter evidence pack

Folder: `docs/evidence/<yyyy-qN>/`. Items:

1. **`access-review.md`** — produced by the
   [ACCESS_REVIEW.md](./ACCESS_REVIEW.md) process. Includes the SQL
   queries used, the raw row counts, removed accounts, and the
   reviewer signature.
2. **`restore-drill.md`** — produced by the
   [BACKUP_RESTORE.md](./BACKUP_RESTORE.md) drill. Includes the chosen
   recovery target time, time-to-restore, smoke-check output, and any
   issues filed.
3. **`vendor-register-snapshot.md`** — a frozen copy of
   [VENDOR_REGISTER.md](./VENDOR_REGISTER.md) at quarter end, plus any
   new SOC 2 / ISO reports collected.
4. **`vuln-scan-summary.md`** — `pip-audit` and `pnpm audit` results
   over the quarter. CI history is the source of truth; this doc
   summarizes the trend and any exceptions filed.
5. **`incident-list.md`** — every SEV-1 / SEV-2 with date, scope,
   resolution time, link to postmortem.
6. **`changes-summary.md`** — high-risk PRs (auth / billing / privacy
   / migration) merged this quarter, with the reviewer names and
   commit SHAs.
7. **`monitoring-alert-summary.md`** — alert noise / signal trends,
   any alert tuning that happened.
8. **`config-snapshots/`** — frozen exports of:
   - `pg_policies` from prod (RLS posture).
   - Production environment variable allow-list (names only, not
     values).
   - CI workflow files at quarter end.
   - `pip-licenses` output.

## Per-incident evidence

For every SEV-1 / SEV-2 (in addition to the postmortem):

- Channel transcript export.
- Audit-log queries + result counts.
- Metric screenshots covering the incident window.
- Commands run + their output.

These live in `docs/evidence/<yyyy-qN>/incidents/<inc-id>/`.

## Per-deploy evidence

For every production deploy:

- Approver name + commit SHA on the deploy ticket.
- CI run id (the green run that approved the deploy).
- Migration list + downgrade test result if any DB migration ran.

CI captures most of this; the deploy ticket is the human attestation.

## Storage

Evidence files are committed to the repo so they get the same review,
backup, and history as code. Sensitive raw exports (e.g. an IAM
credential rotation log) live in the secret store and the evidence
file references them by id.

## What we do *not* commit

- Customer PII (raw export bundles).
- Secrets, even old ones.
- Vendor SOC 2 reports under NDA — store in a secured drive and
  reference by document id in the evidence file.
