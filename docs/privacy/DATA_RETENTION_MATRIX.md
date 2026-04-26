# Data Retention Matrix (Privacy View)

**Owner:** Privacy / Compliance lead.

The machine-readable engineering source is
[`retention_policy.yaml`](./retention_policy.yaml). It intentionally records
only horizons that are traceable to runtime configuration. Do not encode
new fixed retention durations here until they are approved and wired to
configuration or cleanup code.

> **Note for counsel:** This document describes the platform's
> default behavior. The customer-facing privacy policy is **not** this
> file; counsel writes the customer-facing language separately and
> uses this matrix as input.

Rows with deleted parent teams are hidden by both RLS read policies and
application-layer checks. Database write policies also refuse writes for
soft-deleted teams, while audit insertion remains append-only so deletion
and incident trails can still be recorded.
