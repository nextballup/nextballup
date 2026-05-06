# Data Subject Access Request (DSAR) Playbook

**Owner:** Privacy / Compliance lead.
**Last reviewed:** 2026-04-25.

## Self-serve paths

The platform exposes self-serve endpoints that satisfy the most common
DSAR types without operator intervention:

- **Access (GDPR Art. 15 / CCPA "Right to Know"):**
  `GET /api/v1/auth/me/export`. Returns a JSON bundle containing the
  user's profile fields, team memberships, videos they uploaded, audit
  events where they were the actor, auth session metadata, verification
  and password-reset token metadata, MFA enrollment summary, owned
  billing accounts, recorded privacy consents, and attributed CSP
  reports. Token hashes and secret material are excluded.
- **Erasure (GDPR Art. 17 / CCPA "Right to Delete"):**
  `DELETE /api/v1/auth/me`. Anonymizes the user's row (preserving
  audit lineage as `actor_user_id` references), clears biometric
  fields, voids the password hash, deactivates memberships, and
  revokes every refresh session. Pending verification and password reset
  tokens are invalidated and request IP/User-Agent fields are scrubbed.
- **Email-verification status:** `GET /api/v1/auth/email/verify/status`
  exposes whether the account is verified and when.
- **MFA status:** `GET /api/v1/auth/mfa/status`.

## Operator-assisted paths

When a request comes in via support (email, paper letter, court
order):

1. **Verify the requester's identity.** Email-from-of-record at minimum;
   for high-stakes requests (legal hold, court order) ask for
   government ID via the secured upload channel — counsel coordinates.
2. **Look up the account** by email-lower (the platform stores
   `lower(email)` in a unique index).
3. **Run the appropriate self-serve flow** on behalf of the user, or
   use the SQL-level helper queries in this doc.
4. **File the request** in the privacy intake spreadsheet (counsel
   owns) with the date, requester, type, and outcome.

### Useful SQL

```sql
-- Confirm an email exists.
SELECT id, full_name, role, is_active, is_verified, created_at
FROM users
WHERE lower(email) = lower('<requester>');

-- Pull all audit rows actor_user_id == <id> (for an extended export).
SELECT created_at, action, resource_type, resource_id, team_id, ip_address, extra
FROM audit_logs
WHERE actor_user_id = '<id>'
ORDER BY created_at DESC;

-- Pull videos uploaded by user.
SELECT v.id, v.team_id, v.filename, v.status, v.created_at
FROM videos v
WHERE v.uploaded_by = '<id>';
```

## Sub-processor pass-through

Customers can ask for a list of sub-processors that touch their data.
Provide the current [VENDOR_REGISTER.md](../soc2/VENDOR_REGISTER.md)
snapshot. Customers can ask for advance notice of new sub-processors;
this is contractual — counsel owns the customer-side commitment, and
the engineering team must update the register *before* the new vendor
sees production traffic.

## Special cases

- **Minor's account.** Erasure must come from the parent or guardian
  of record (or be initiated by the team's institutional admin under
  the consent ledger). Counsel confirms the requester's authority
  before we run the erasure SQL.
- **Account on legal hold.** If the user's data is under hold, return
  the data we can (the access export still works) and document the
  hold. Erasure is paused until the hold is released.
- **Court-ordered preservation.** Treat as a hold. Document the order
  in the evidence pack.

## Evidence

Each DSAR produces:

- An entry in the privacy intake spreadsheet.
- A copy of the access export bundle (when applicable), encrypted at
  rest.
- The erasure SQL run output (when applicable).
- The audit-log rows for the action (`auth.data.exported` /
  `auth.account.deleted`) confirming the platform recorded it.

We do **not** store the raw PII bundle in the repo or in evidence
folders.
