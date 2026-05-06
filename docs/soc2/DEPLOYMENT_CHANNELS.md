# Deployment Channels

**Owner:** Engineering lead.
**Last reviewed:** 2026-05-04.

These channel definitions are engineering operating rules. They are not a
public launch plan, legal approval, or compliance claim.

## Channel map

| Channel | Domain | Environment | Purpose | Hard boundary |
| --- | --- | --- | --- | --- |
| Public | `nextballup.com`, `www.nextballup.com` | Separate marketing/waitlist surface | Brand, waitlist, contact, approved public docs | Do not point directly at the app with open registration. Do not claim production CV analytics or legal compliance. |
| Alpha | `alpha.nextballup.com` | `APP_ENV=staging` | Internal and highly trusted workflow testing | Separate DB/S3/Redis/secrets. Lock at proxy/VPN/basic-auth/IP allowlist. Postmark email allowed; `BILLING_PROVIDER=billing_disabled` allowed. No customer-facing CV claims. |
| Beta | `beta.nextballup.com` | `APP_ENV=production` | Invite-only pilot users | Separate prod-grade DB/S3/Redis/secrets/email/backups. Invite-only access. Real billing provider required. No demo-only data or dev CV bridge. |

## Current CV truth

No licensed commercial basketball dataset has been obtained yet. The platform
may pilot film upload, archive, transcode, playback, account recovery, team
management, and retention workflows before commercial CV is ready.

Production CV analytics require all of the following before `CV_PIPELINE_ENABLED`
is enabled in beta/production:

- a dataset registered in the vision-training repo with
  `commercial_use_allowed=true`
- `demo_only=false`
- a structured `commercial_use_evidence_ref`
- passing evaluation and promotion gates
- a platform `cv_model_artifacts` row with `status=active`,
  `commercial_use_allowed=true`, and the expected artifact checksum

Public or broadcast-derived basketball datasets such as BARD or SpaceJam may
be useful for internal research, taxonomy design, and pre-commercial proof of
concept work, but they do not become production-path data unless counsel and
the operator approve the underlying media-rights position.

## Required before any real user pilot

- [ ] Registration is invite-only, allowlisted, or blocked at the edge.
- [ ] `FRONTEND_APP_URL` is set to the channel's HTTPS origin.
- [ ] Same-origin API routing is configured for the channel.
- [ ] `COOKIE_SECURE=true`, `COOKIE_SAMESITE=strict`,
      `COOKIE_HOST_PREFIX=true`, and `COOKIE_DOMAIN` unset.
- [ ] `DATABASE_URL_RUNTIME` uses the non-owner `nextballup_app` role.
- [ ] DB, Redis, object storage, and secrets are separate from local/dev.
- [ ] Email provider is real for alpha/beta; logging/noop providers stay local/dev.
- [ ] Billing provider is `billing_disabled` only for private alpha, or a real
      registered provider for beta/production. `stub` stays local/dev.
- [ ] Backups and restore drill ownership are documented.
- [ ] Demo CV preview remains disabled outside development/test.
- [ ] Any youth/K-12 pilot has counsel-reviewed consent language and the
      platform consent gate enabled.

For the current Render-based alpha path, use
[ALPHA_RENDER_DEPLOYMENT.md](./ALPHA_RENDER_DEPLOYMENT.md).

## Public site rule

The public root domain should stay a marketing/waitlist/docs surface until
the operator intentionally wants public signups. It should not expose the app
registration flow by default, and it should not describe production CV
analytics before the dataset, model, and promotion gates are complete.
