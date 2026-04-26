from __future__ import annotations

import logging

from _pytest.logging import LogCaptureFixture

from nextballup_core.logging import install_log_redaction_filter


def test_log_redaction_removes_jwt_and_sensitive_headers(caplog: LogCaptureFixture) -> None:
    token = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxIn0.signature_123"
    access_key = "AKIA1234567890ABCDEF"
    logger = logging.getLogger("nextballup.tests.redaction")
    install_log_redaction_filter(logging.getLogger())

    with caplog.at_level(logging.INFO):
        logger.info(
            "authorization=%s cookie=sessionid=abc x-csrf-token=csrfsecret %s",
            f"Bearer {token}",
            access_key,
        )

    assert token not in caplog.text
    assert access_key not in caplog.text
    assert "sessionid=abc" not in caplog.text
    assert "csrfsecret" not in caplog.text
    assert "[REDACTED]" in caplog.text
