"""Schemas for the self-serve compliance endpoints (GDPR Art. 15 + 17).

These shapes are deliberately narrow: a data export is about what we hold
*for this user*, not about reconstructing the whole tenant's state. Videos
and team rosters are surfaced as summaries (IDs + non-PII metadata), not
as full content, because the user is not the controller of those tenant
assets — the team is.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr

from nextballup_core.enums import UserRole


class UserProfileExport(BaseModel):
    """Every PII field we store against the user row itself."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    full_name: str
    role: UserRole
    phone: str | None = None
    institution: str | None = None
    avatar_url: str | None = None
    height_inches: int | None = None
    weight_lbs: int | None = None
    position: str | None = None
    graduation_year: int | None = None
    handedness: str | None = None
    is_active: bool
    is_verified: bool
    biometric_consent: bool
    parental_consent_on_file: bool
    date_of_birth_verified: bool
    created_at: datetime


class TeamMembershipExport(BaseModel):
    """Tenant associations this user had. `jersey_number` is included
    because it's the one PII-adjacent field on the membership row."""

    team_id: uuid.UUID
    team_name: str
    team_role: str
    jersey_number: int | None
    is_active: bool
    joined_at: datetime


class VideoSummaryExport(BaseModel):
    """Videos this user uploaded. Content is not included — it's a
    tenant asset, not a user asset — but metadata is, so the user can
    see what they contributed."""

    id: uuid.UUID
    game_id: uuid.UUID
    team_id: uuid.UUID
    filename: str
    file_size_bytes: int | None
    status: str
    created_at: datetime


class AuditEventExport(BaseModel):
    """Audit entries where this user was the actor. Tenant-scoped events
    they were only tangentially involved in (e.g., a teammate's upload)
    are excluded."""

    action: str
    resource_type: str | None
    resource_id: uuid.UUID | None
    team_id: uuid.UUID | None
    ip_address: str | None
    created_at: datetime
    extra: dict[str, Any] | None


class RefreshSessionExport(BaseModel):
    id: uuid.UUID
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    revoked_reason: str | None
    replaced_by_session_id: uuid.UUID | None
    ip_address: str | None
    user_agent: str | None


class EmailVerificationTokenExport(BaseModel):
    id: uuid.UUID
    created_at: datetime
    expires_at: datetime
    used_at: datetime | None
    requested_ip: str | None
    requested_user_agent: str | None
    confirmed_ip: str | None


class PasswordResetTokenExport(BaseModel):
    id: uuid.UUID
    created_at: datetime
    expires_at: datetime
    used_at: datetime | None
    requested_ip: str | None
    requested_user_agent: str | None
    reset_ip: str | None


class MfaEnrollmentExport(BaseModel):
    enrolled: bool
    confirmed_at: datetime | None
    disabled_at: datetime | None
    last_used_at: datetime | None
    recovery_codes_total: int
    recovery_codes_unused: int


class BillingAccountExport(BaseModel):
    id: uuid.UUID
    name: str
    status: str
    billing_email: str | None
    deleted_at: datetime | None
    created_at: datetime


class UsageEventExport(BaseModel):
    id: uuid.UUID
    billing_account_id: uuid.UUID
    team_id: uuid.UUID | None
    event_key: str
    quantity: int
    occurred_at: datetime
    event_metadata: dict[str, Any] | None


class TeamPrivacyConsentExport(BaseModel):
    id: uuid.UUID
    team_id: uuid.UUID
    label: str
    consent_source: str
    covers_video_uploads: bool
    covers_cv_processing: bool
    commercial_ml_training_allowed: bool
    minors_authorized: bool
    athlete_pii_authorized: bool
    evidence_uri: str | None
    evidence_sha256: str | None
    effective_at: datetime
    expires_at: datetime | None
    revoked_at: datetime | None


class CspReportExport(BaseModel):
    id: uuid.UUID
    received_at: datetime
    document_uri: str | None
    violated_directive: str | None
    blocked_uri: str | None
    source_file: str | None
    line_number: int | None
    column_number: int | None
    user_agent: str | None
    reporter_ip: str | None


class UserDataExport(BaseModel):
    """Top-level envelope returned by GET /auth/me/export."""

    exported_at: datetime
    user: UserProfileExport
    team_memberships: list[TeamMembershipExport]
    videos_uploaded: list[VideoSummaryExport]
    audit_events: list[AuditEventExport]
    refresh_sessions: list[RefreshSessionExport]
    email_verification_tokens: list[EmailVerificationTokenExport]
    password_reset_tokens: list[PasswordResetTokenExport]
    mfa: MfaEnrollmentExport
    billing_accounts_owned: list[BillingAccountExport]
    usage_events_for_member_teams: list[UsageEventExport]
    team_privacy_consents_recorded: list[TeamPrivacyConsentExport]
    csp_reports_attributed: list[CspReportExport]


class AccountDeleteResponse(BaseModel):
    """Shape of the DELETE /auth/me response.

    We acknowledge synchronously: anonymization is a single transaction,
    not a background job, so the caller can treat the 200 as 'your PII is
    gone from the user row'. Tenant-owned content (videos, team history)
    is retained with the actor FK nulled where applicable.
    """

    deleted_at: datetime
    user_id: uuid.UUID
