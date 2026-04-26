# Access Review

**Owner:** Security / Compliance lead.
**Last reviewed:** 2026-04-25.
**Cadence:** Quarterly.

## What we review

| Surface | Review question | Source of truth |
| --- | --- | --- |
| `users` with `role='admin'` | Does this person still need cross-tenant access? | `SELECT id, email, created_at FROM users WHERE role='admin' AND is_active`; |
| Production database role grants | Does any role hold privileges it does not need? | `\du` in the prod database; compared against migration `0008` baseline. |
| Object storage IAM | Which IAM principals can read raw video buckets? | Cloud provider IAM exports. |
| Secret store readers | Who has decrypt access to JWT/CSRF/MFA secrets? | Cloud secret manager. |
| Source repository write access | Who can merge to `main`? | GitHub org audit log. |
| External service consoles | Who has admin access to email/billing providers? | Provider-side audit logs. |
| MFA enrollment | Every admin and coach with state-changing perms must have an active TOTP enrollment. | `SELECT u.email FROM users u LEFT JOIN user_totp_secrets s ON s.user_id = u.id AND s.confirmed_at IS NOT NULL AND s.disabled_at IS NULL WHERE u.role IN ('admin','coach') AND s.id IS NULL;` |

## Process

1. Reviewer (security lead) opens an access-review ticket the first week
   of each quarter.
2. For each row of each surface above: confirm the person is still
   employed and still needs the access. Anything ambiguous goes back to
   the person's manager for explicit confirmation.
3. Removals are filed as PRs (IAM-as-code where possible) or
   manager-confirmed tickets.
4. Review document goes into `docs/evidence/<yyyy-qN>/access-review.md`
   with the queries used, the result counts, and a list of removals.

## Failure modes

- **Reviewer is also the only admin.** Bus-factor risk; backlog a
  second admin enrollment.
- **External console doesn't expose member list via API.** Manual export
  + screenshot acceptable; capture the export filename in the evidence
  doc.
- **Recently-departed person still in the export.** SEV-2 incident.
  Run the access-review query post-mitigation to confirm nothing was
  missed.

## Evidence shape

Each quarterly review produces:

- The list of accounts reviewed per surface.
- The list of accounts removed and the date.
- Any exceptions (with a written justification and an expiry date).
- Reviewer signature + date.
