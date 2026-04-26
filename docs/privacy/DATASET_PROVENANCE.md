# Dataset & Model Provenance

**Owner:** ML platform / Privacy lead.
**Last reviewed:** 2026-04-25.

A model artifact may not be promoted to commercial use without a
complete provenance trail. This document defines what "complete"
means and where it is recorded.

## What gets recorded

`cv_model_artifacts` is the registry. Every row that the worker is
allowed to select for an active stage must satisfy:

| Field | Required value |
| --- | --- |
| `status` | `active` |
| `commercial_use_allowed` | `true` |
| `artifact_uri` | object-storage URI of the actual artifact |
| `artifact_sha256` | hex SHA-256 of the artifact bytes |
| `model_version` | semver-style version unique within `(stage, model_version)` |
| `dataset_version_ref` | dataset-registry id, format `<id>@<version>` |
| `config_hash` | hash of the experiment config used to produce it |
| `license` | spdx-style license identifier of the artifact + weights |
| `min_plan_tier` | integer; only callers whose plan tier ≥ this can use it |
| `registered_by` | user id of the operator who promoted it |

`artifact_sha256`, `dataset_version_ref`, and `config_hash` together
let any future caller reproduce or audit the model end-to-end.

## What every dataset must record

The dataset registry (today: `nextballup-vision-training/registry/`)
must include for every dataset version:

- `dataset_id`, `version`, `sport`, `modality`
- `storage_uri` and `content_sha256`
- `provenance` — short string describing source (e.g. `internal_pilot`,
  `licensed:<provider>`, `synthetic`)
- `license` — what we are allowed to do with the data
- `commercial_use_allowed` — boolean
- `annotation_schema_ref`
- `notes` — anything that doesn't fit a column

Datasets with `commercial_use_allowed=false` may **not** be referenced
by an artifact whose `commercial_use_allowed=true`. Add a periodic
check to the promotion CI.

## Promotion gate

Before flipping an artifact's `status` to `active`:

1. The training repo emits a promotion manifest listing every field
   above plus the evaluation report.
2. The evaluation report passes the slice + threshold gates defined
   in `nextballup-vision-training/configs/promotion/`.
3. A second reviewer (not the model author) signs off in the PR that
   inserts the registry row.
4. Counsel reviews the license claim if the artifact uses any data
   not produced internally.

## Consent linkage

When an artifact is trained on customer-provided footage, every
contributing tenant must have `commercial_ml_training_allowed=true` on
a current `team_privacy_consents` row. The training pipeline records
the contributing consent ids in the manifest. If a tenant later
revokes the consent, the artifact must be retired (`status=retired`)
and a successor trained without that data.

## What we don't do today (commit to before launch)

- [ ] Automatic checker that walks
      `cv_model_artifacts → dataset_version_ref → consent ids` and
      flags broken provenance.
- [ ] Integration between the training repo's promotion manifest and
      the registry insert (today there's a manual step — write a CLI).
- [ ] Per-artifact license attestation document referenced by URI.

These are the gates that turn "we have a contract with our trainer"
into "we can prove it to an auditor".
