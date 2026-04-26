# Minor / Guardian Consent Handling

**Owner:** Privacy / Compliance lead.
**Last reviewed:** 2026-04-25.

The platform serves K-12 / youth basketball teams whose rosters
include minors. Two regulatory regimes drive our controls:

- **COPPA** (US, < 13): verifiable parental consent required before
  collecting personal information from a child.
- **FERPA** (US, K-12 institutions receiving federal funds): student
  educational records — including some video — require institutional
  authorization.

Other jurisdictions (state biometric laws like BIPA, EU GDPR child
provisions, UK Age Appropriate Design Code) layer additional
requirements; counsel reviews per-jurisdiction obligations.

## Engineering controls

1. **Sensitive team detection.** A team is "sensitive" if its
   `level` is in `{youth, aau_club, middle_school, high_school}`
   *or* its `institution_type` is `k12_school`.
   See `apps/api/src/nextballup_api/routers/videos.py:_team_requires_privacy_consent`.

2. **Consent ledger.** `team_privacy_consents` records explicit
   permissions:
   - `covers_video_uploads`
   - `covers_cv_processing`
   - `commercial_ml_training_allowed`
   - `minors_authorized` (the COPPA-relevant flag)
   - `athlete_pii_authorized`
   - `evidence_uri` + `evidence_sha256` — pointers to the actual
     signed-form / consent record kept by the customer
   - `effective_at`, `expires_at`, `revoked_at`

3. **Upload gating.** When sensitive-team detection fires *and*
   `should_require_sensitive_upload_consent()` returns true (default
   true outside the test environment), the upload init endpoint
   refuses without a current consent row that:
   - is not revoked
   - is within `effective_at..expires_at`
   - covers video uploads + CV processing
   - is `athlete_pii_authorized`
   - is `minors_authorized` for sensitive teams

4. **Email verification.** When the gate is on, coaches must verify
   their email before issuing invites or uploading. This narrows the
   anonymous-onboarding attack surface that would otherwise allow a
   stranger to recruit minor athletes.

## Operator process

When a new K-12 team comes onboard:

1. Coach uploads the signed parental-consent forms / institutional
   authorization to a secured intake (out of scope for the platform).
2. Operator records the consent batch in `team_privacy_consents`
   with `minors_authorized=true`, `athlete_pii_authorized=true`, and
   the `evidence_uri` / `evidence_sha256` pointing at the stored
   document.
3. Coach can now upload film for that team.

Revocation is a single SQL: set `revoked_at=now()` on the consent
row. The next upload-init attempt is rejected; existing videos are
preserved per the retention matrix until the customer asks for
deletion.

## Player self-service

A minor's player account is a passive entity in the platform today —
they can view but not upload film. Player accounts created without
guardian involvement are flagged by the absence of
`parental_consent_on_file=true` on the `users` row. We do not collect
biometric fields (height, weight, position, handedness, jersey
number) without `biometric_consent=true`.

## Auditing the consent ledger

Quarterly the privacy lead runs:

```sql
SELECT t.name, c.label, c.minors_authorized, c.expires_at, c.revoked_at
FROM team_privacy_consents c
JOIN teams t ON t.id = c.team_id
WHERE t.level IN ('youth','aau_club','middle_school','high_school')
   OR t.institution_type = 'k12_school'
ORDER BY c.expires_at NULLS LAST;
```

Any expired consents on active teams are flagged for renewal.

## Open work

- [ ] Coach-facing UI for consent collection / renewal (today this is
      operator-only).
- [ ] Per-team `raw_video_retention_days` override surfaced on the
      billing account, defaulting to a tighter window for K-12 teams.
- [ ] Annual training prompt for coaches on K-12 teams (acknowledge
      a refreshed privacy notice).
