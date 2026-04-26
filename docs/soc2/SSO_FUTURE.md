# Future Work: Enterprise SSO

**Owner:** Auth platform.
**Status:** Architecture only. Not implemented. Gated to enterprise plan.

## Why scope it now

School / district / league procurement frequently demands SSO before a
contract can close. We do not implement it speculatively because:

- Real-world SSO integrations carry vendor-specific quirks (Okta, Azure
  AD, Google Workspace).
- A poorly-built SSO surface is a credential-stuffing target.

This doc captures the design so the work can move quickly when the
first paying customer is on the line.

## Surfaces

- `auth_methods` table: per-billing-account allowed login methods
  (`password`, `oidc`, `saml`).
- Per-billing-account `identity_provider` config row (issuer URL, JWKS
  URL, ACS URL, signing cert, attribute mapping).
- New endpoints under `/auth/sso/<provider_id>/`:
  - `GET initiate` — redirects to the IdP.
  - `POST callback` — validates assertion, provisions / matches user,
    issues the same access + refresh cookies the password path uses.
- Just-in-time provisioning: first SSO login creates a `users` row
  pre-verified, role inferred from the IdP attribute mapping.

## Standards

- **OIDC** first. Authorization Code + PKCE. JWT validation against the
  IdP's published JWKS, enforced `iss` / `aud`.
- **SAML 2.0** second. SP-initiated SSO. Assertion signing required;
  encryption optional (preferred). XML signature verification with a
  hardened parser (no XXE, no external entities, deterministic
  canonicalization).

## Security non-negotiables

- IdP discovery / JWKS fetch is server-side only and cached.
- `nonce` + `state` enforced on OIDC.
- SAML assertions validated for signature, issuer, NotBefore /
  NotOnOrAfter, audience, recipient, replay (`AssertionID` cache).
- The platform never returns SSO tokens to the browser; the JWT cookie
  flow remains the boundary.
- Every SSO login is audited (`auth.sso.login.succeeded` /
  `auth.sso.login.failed`).

## Plan gating

The `enterprise` plan's `features` JSONB has `sso=true`. Lower tiers
return 403 from the `/auth/sso/...` surface even if the routes are
mounted.

## Migration risk

- Mixed mode (some users on password, some on SSO) requires the
  `auth_methods` row per account so we don't lock anyone out.
- Existing users matched to SSO by email — the IdP must guarantee
  email uniqueness (or use `sub` as the canonical id).

## Open questions

- Group / role mapping: do customers want their IdP-side groups to
  map to NextBallUp `team_role`? Almost certainly yes; needs admin UX.
- SCIM provisioning: necessary for the truly large customers; defer
  until at least two customers ask.
