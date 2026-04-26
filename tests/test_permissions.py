from __future__ import annotations

import pytest
from nextballup_api.permissions import require_verified_account

from nextballup_core.enums import UserRole
from nextballup_core.errors import ForbiddenError
from nextballup_core.settings import get_settings
from nextballup_db.models.user import User


def _user(*, role: UserRole = UserRole.COACH, is_verified: bool = False) -> User:
    return User(
        email="verification-test@example.com",
        password_hash="!",
        full_name="Verification Test",
        role=role,
        is_verified=is_verified,
    )


def test_verified_account_gate_blocks_unverified_coach_when_required() -> None:
    settings = get_settings().model_copy(
        update={"require_verified_email_for_sensitive_actions": True}
    )

    with pytest.raises(ForbiddenError) as exc:
        require_verified_account(_user(is_verified=False), settings=settings)

    assert exc.value.details == {"reason": "email_unverified"}


def test_verified_account_gate_allows_verified_coach_when_required() -> None:
    settings = get_settings().model_copy(
        update={"require_verified_email_for_sensitive_actions": True}
    )

    require_verified_account(_user(is_verified=True), settings=settings)


def test_verified_account_gate_is_default_on_for_staging_and_prod() -> None:
    staging = get_settings().model_copy(update={"app_env": "staging"})
    production = get_settings().model_copy(update={"app_env": "production"})
    test = get_settings().model_copy(update={"app_env": "test"})

    assert staging.should_require_verified_email_for_sensitive_actions() is True
    assert production.should_require_verified_email_for_sensitive_actions() is True
    assert test.should_require_verified_email_for_sensitive_actions() is False
