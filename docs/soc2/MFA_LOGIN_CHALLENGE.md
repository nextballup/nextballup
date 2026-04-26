# Future Work: MFA Login Challenge

**Owner:** Auth platform.
**Status:** Designed, not implemented.

## Today

The MFA endpoints (`/auth/mfa/totp/setup`, `/confirm`, `/disable`,
`/status`) let admin and coach roles enroll a TOTP factor. Recovery
codes are minted and verified. None of this gates the **login** flow
yet — a user with TOTP enrolled today can still complete `/auth/login`
without producing a code.

This document captures the intended login challenge so the work is
clearly defined when we pick it up.

## Target login flow

1. Client → `POST /auth/login` with email + password.
2. Server verifies password (today).
3. **New step:** server checks for a confirmed
   `user_totp_secrets` row.
4. If absent: existing path — issue access + refresh cookies.
5. If present: server returns a small `mfa_required` envelope:

   ```json
   { "challenge_id": "<uuid>", "expires_at": "<iso>" }
   ```

   No auth cookies are set. The session_version + role + user id are
   bound into the challenge record server-side.

6. Client → `POST /auth/login/mfa` with `challenge_id` + `code`.
7. Server validates the TOTP code (or recovery code) against the
   bound user. On success, mint and set the auth cookies as in step 4.

## New table

```sql
CREATE TABLE mfa_login_challenges (
    id            UUID PRIMARY KEY,
    user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at    TIMESTAMPTZ NOT NULL,
    consumed_at   TIMESTAMPTZ,
    failed_count  INT NOT NULL DEFAULT 0,
    requested_ip  INET,
    requested_user_agent TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

- TTL ≈ 5 minutes.
- Single-use (`consumed_at`).
- Locks after `failed_count` >= 5 (per challenge), at which point the
  challenge is voided and the client must re-login.

## Recovery code path

`POST /auth/login/mfa` accepts either a TOTP code or an unused
recovery code. Successful recovery-code consumption is audit-logged
and the user is shown a banner asking them to regenerate codes.

## Remember-device

Out of scope for the first cut. The path is well-known: a stable
device cookie (`__Host-nbu_mfa_device`) bound to a server-side row
keyed by user + device hash, with a TTL ≤ 30 days and explicit revoke
on disable / logout. Document in this file when we pick it up.

## Migration risk

Switching the login behavior is breaking for anyone already enrolled
without a real MFA flow. Rollout:

1. Ship the challenge endpoint behind a feature flag.
2. Enable for admin role first, observe for one week.
3. Enable for coach role.
4. Enable for player role only after the `/auth/login` UI is updated
   to handle the `mfa_required` envelope.

## Tests required

- Happy path: login → challenge → code → cookies set.
- Wrong code increments `failed_count`; 5 wrongs voids the challenge.
- Expired challenge → 401.
- Replay of consumed challenge → 401.
- Recovery code consumption marks `mfa_recovery_codes.used_at`.
- A user with MFA disabled in-flight: logging in starts to skip MFA
  on the next attempt (no carryover).
- Cross-tenant: challenge row is scoped to the user, not exposed via
  another user's session.
