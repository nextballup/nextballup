# Change Management

**Owner:** Engineering lead.
**Last reviewed:** 2026-04-25.

## Required PR controls

Every change to the production codebase must:

1. Go through a pull request. No direct pushes to `main`.
2. Pass CI: ruff, mypy, pytest, frontend lint/typecheck/test/build,
   `pip-audit`, `pnpm audit --audit-level high`, gitleaks, license check.
3. Have one approval from someone other than the author. Two approvals
   for changes to:
   - `alembic/versions/*` (any DB migration)
   - `apps/api/src/nextballup_api/security/*`
   - `apps/api/src/nextballup_api/routers/auth.py`
   - `apps/api/src/nextballup_api/routers/admin.py`
   - `apps/api/src/nextballup_api/billing.py`
   - `apps/api/src/nextballup_api/email_*`
   - `infra/**`
   - `.github/workflows/*`
4. Include test coverage proportional to risk:
   - Auth / billing / privacy changes: positive **and** negative path
     tests, including replay / cross-tenant probes.
   - DB migrations: reversibility verified by `alembic downgrade` round
     trip, with a comment in the migration if anything is intentionally
     irreversible.
   - Worker changes: a runtime test that exercises the actual code path,
     not just unit-level mocks.
5. Reference the issue or design doc that motivated it. "Drive-by" PRs
   that touch security / billing / privacy surfaces require a written
   rationale in the PR description.

## Migration safety

- **One migration per PR.** No bundling unrelated schema changes.
- **Reversible by default.** `downgrade()` actually reverses
  `upgrade()`. If reversibility is impossible (data destruction, type
  narrowing), the migration includes a `# IRREVERSIBLE: <reason>`
  comment and the PR description states why.
- **No locking surprises.** New columns must have a server default or
  be nullable. Adding a column with a non-null default and no fallback
  rewrites the entire table on Postgres — split into add-nullable +
  backfill + alter-not-null.
- **RLS migrations review.** Anything that adds, drops, or alters an
  RLS policy is a two-approval change and should include a
  cross-tenant test.
- **Plan / billing rows.** Plans are seeded by migration. Updates use
  `UPDATE` keyed on `code`. **Never** `DELETE FROM plans` — historical
  subscriptions reference them.

## Deploy approval

The deploy approver verifies:

1. CI is green on the deploy commit.
2. The [PRODUCTION_READINESS.md](./PRODUCTION_READINESS.md) checklist is
   complete.
3. Any migrations included have been smoke-tested against a
   prod-shaped staging database.
4. Customer-facing changes (new feature / billing change) have their
   docs / email / status-page updates queued.

The approver signs the deploy ticket with their name + the commit SHA
they approved. Approvals expire after 24h.

## Hotfix path

For SEV-1 incidents, the hotfix path:

1. Create a `hotfix/<short-name>` branch from the live deploy SHA.
2. Push the minimum fix.
3. CI must still pass — no `--no-verify`.
4. One approval from the IC plus one from the on-call engineer is
   sufficient (vs the normal two-approval rule).
5. The hotfix is merged back into `main` with the same change.

## Source-of-truth docs

If a change makes one of these stale, update it in the same PR:

- `README.md`, `CLAUDE.md`
- `API_SPEC.md`
- `DATABASE_SCHEMA.md`
- `FRONTEND_ARCH.md`
- This file or its siblings in `docs/soc2/`
