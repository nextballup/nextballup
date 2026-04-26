# Vendor / Sub-processor Register (Template)

**Owner:** Security / Compliance lead.
**Last reviewed:** 2026-04-25.

This register lists every third-party service that touches NextBallUp
production data, the data class it sees, and our review status. Auditor
+ counsel + customer DPAs all draw from this list.

> **Action item:** populate the rows below with real vendor names + DPA
> dates before the first SOC 2 audit window. The structure is the
> evidence shape the auditor expects; the contents are intentionally
> blank in the repo so they don't claim anything that isn't true.

## Active sub-processors

| Vendor | Service | Data class | DPA on file | SOC 2 / ISO 27001 evidence | Last review | Owner |
| --- | --- | --- | --- | --- | --- | --- |
| _TBD: managed Postgres provider_ | Database hosting | All app data | _TBD_ | _TBD (request from vendor)_ | _TBD_ | Platform |
| _TBD: object storage provider_ | Media storage | Raw + mezzanine videos, CV artifacts | _TBD_ | _TBD_ | _TBD_ | Platform |
| _TBD: email delivery provider_ | Transactional email | Email address, full name | _TBD_ | _TBD_ | _TBD_ | Platform |
| _TBD: error tracking_ | Error reports | Stack traces, request IDs (no PII bodies) | _TBD_ | _TBD_ | _TBD_ | Engineering |
| _TBD: log shipping_ | Structured logs | Audit + app logs | _TBD_ | _TBD_ | _TBD_ | Platform |
| _TBD: billing provider_ | Subscription / payment | Billing email, plan, charges | _TBD_ | _TBD_ | _TBD_ | Finance |

## Inactive / candidate

| Vendor | Status | Notes |
| --- | --- | --- |
| _Stripe_ | Candidate | Interface stub lives at `apps/api/src/nextballup_api/billing.py` (`StubBillingProvider`). |
| _SendGrid / Postmark / SES_ | Candidate | Provider abstraction at `apps/api/src/nextballup_api/email_delivery.py` accepts a registration; production deploys plug in their own provider implementation. |

## Per-vendor onboarding checklist

When adding a new vendor:

1. **Contract.** DPA signed; data classes named explicitly.
2. **Evidence.** Request the vendor's most recent SOC 2 / ISO report.
   Save in the evidence pack.
3. **Data classes.** Confirm what the vendor will see. Cross-check
   against [DATA_RETENTION.md](./DATA_RETENTION.md).
4. **Authentication.** SSO + MFA for any human accounts. API keys
   stored in the deploy secret store; per-env keys.
5. **Sub-processor disclosure.** Update this file *before* traffic
   starts flowing.
6. **Customer notice.** If the vendor change affects the published
   sub-processor list on the marketing site, send the agreed customer
   notification (typically 30 days advance for enterprise plans).

## Annual review

Each calendar year:

- Refresh every vendor's SOC 2 / ISO report.
- Confirm DPAs are still current.
- Decommission any vendor whose service has been inactive for the year.
- Sign off on the register and file in the year's evidence pack.
