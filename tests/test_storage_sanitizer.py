"""Unit tests for the defense-in-depth filename sanitizer.

The API-layer validator is expected to reject unsafe filenames before they
ever reach storage. These tests are about the *second* line of defense —
if a future code path constructs a storage key with a weaker validator
(admin backfill, reprocessing job, etc.), the key must still be safe.
"""

from __future__ import annotations

from typing import cast

import pytest
from botocore.exceptions import ClientError
from nextballup_api.storage import (
    S3StoragePresigner,
    StorageFailureError,
    _sanitize_filename_component,
    storage_key_for_video,
)

from nextballup_core.enums import UploadMethod
from nextballup_core.settings import Settings, get_settings


def _storage_settings(**overrides: object) -> Settings:
    return get_settings().model_copy(
        update={
            "s3_endpoint_url": "https://example-account.r2.cloudflarestorage.com",
            "s3_access_key": "test-access-key",
            "s3_secret_key": "test-secret-key",
            "s3_bucket_raw": "nextballup-alpha-raw",
            "s3_region": "auto",
            **overrides,
        }
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("clip.mp4", "clip.mp4"),
        ("Game 1 vs Lincoln.mp4", "Game_1_vs_Lincoln.mp4"),
        ("../../etc/passwd", "etc_passwd"),
        ("null\x00byte.mp4", "null_byte.mp4"),
        ("control\x1fchars.mp4", "control_chars.mp4"),
        ("..hidden..mp4", "hidden__mp4"),
        ("中文.mp4", "mp4"),  # non-ASCII → collapsed to underscores, then trimmed
        ("", "upload"),
        ("///", "upload"),
        ("...", "upload"),
    ],
)
def test_sanitize_filename_component(raw: str, expected: str) -> None:
    assert _sanitize_filename_component(raw) == expected


def test_sanitize_filename_truncates_long_names() -> None:
    long = "a" * 500 + ".mp4"
    assert len(_sanitize_filename_component(long)) == 200


def test_storage_key_structure_is_stable() -> None:
    """Tenant + video ID must come first in the key so RLS-equivalent prefix
    scoping at the bucket layer (IAM conditions) stays enforceable."""
    key = storage_key_for_video(team_id="team-1", video_id="vid-2", filename="clip.mp4")
    assert key == "raw/team-1/vid-2/clip.mp4"


def test_storage_key_with_traversal_filename_stays_safe() -> None:
    key = storage_key_for_video(team_id="team-1", video_id="vid-2", filename="../../../root.mp4")
    # Exactly four path segments: `raw/{tenant}/{video}/{sanitized}`.
    assert key.count("/") == 3
    assert ".." not in key


def test_s3_presigner_rejects_invalid_endpoint_before_upload() -> None:
    settings = _storage_settings(s3_endpoint_url="not-a-url")

    with pytest.raises(StorageFailureError) as exc_info:
        S3StoragePresigner(settings)

    assert exc_info.value.details == {"reason": "invalid_endpoint_url"}


def test_s3_multipart_requires_provider_upload_id(monkeypatch: pytest.MonkeyPatch) -> None:
    class _MalformedMultipartClient:
        def create_multipart_upload(self, **_kwargs: object) -> dict[str, object]:
            return {}

        def generate_presigned_url(self, *_args: object, **_kwargs: object) -> str:
            raise AssertionError("parts must not be presigned without an UploadId")

    monkeypatch.setattr(
        "nextballup_api.storage.boto3.client",
        lambda *_args, **_kwargs: _MalformedMultipartClient(),
    )
    presigner = S3StoragePresigner(_storage_settings())

    with pytest.raises(StorageFailureError, match="multipart upload id"):
        presigner.presign_upload(
            key="raw/team/video/clip.mov",
            content_type="video/quicktime",
            file_size_bytes=2 * 1024 * 1024 * 1024,
        )


def test_s3_head_object_failure_keeps_sanitized_provider_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FailingHeadClient:
        def head_object(self, **kwargs: object) -> dict[str, object]:
            assert kwargs["Bucket"] == "nextballup-alpha-raw"
            assert kwargs["Key"] == "raw/team-secret/video-secret/clip.mov"
            raise ClientError(
                {
                    "Error": {
                        "Code": "SignatureDoesNotMatch",
                        "Message": "credential scope contains test-secret-key",
                    },
                    "ResponseMetadata": {
                        "HTTPStatusCode": 403,
                        "RequestId": "r2-request-123",
                    },
                },
                "HeadObject",
            )

    monkeypatch.setattr(
        "nextballup_api.storage.boto3.client",
        lambda *_args, **_kwargs: _FailingHeadClient(),
    )
    presigner = S3StoragePresigner(_storage_settings())

    with pytest.raises(StorageFailureError) as exc_info:
        presigner.head_object(key="raw/team-secret/video-secret/clip.mov")

    details = exc_info.value.details
    assert details["operation"] == "head_object"
    assert details["provider_error_code"] == "SignatureDoesNotMatch"
    assert details["http_status_code"] == 403
    assert details["request_id"] == "r2-request-123"
    assert "storage_key_sha256" in details
    serialized = repr(details)
    assert "raw/team-secret/video-secret/clip.mov" not in serialized
    assert "test-secret-key" not in serialized
    assert "credential scope" not in serialized


def test_s3_multipart_presign_uses_r2_compatible_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _MultipartClient:
        def create_multipart_upload(self, **kwargs: object) -> dict[str, object]:
            assert kwargs["Bucket"] == "nextballup-alpha-raw"
            assert kwargs["Key"] == "raw/team/video/clip.mov"
            assert kwargs["ContentType"] == "video/quicktime"
            return {"UploadId": "upload-alpha-1"}

        def generate_presigned_url(
            self,
            operation: str,
            **_kwargs: object,
        ) -> str:
            params = cast("dict[str, object]", _kwargs["Params"])
            expires_in = cast("int", _kwargs["ExpiresIn"])
            assert operation == "upload_part"
            assert params["Bucket"] == "nextballup-alpha-raw"
            assert params["UploadId"] == "upload-alpha-1"
            assert expires_in > 0
            return f"https://storage.example/part-{params['PartNumber']}"

    monkeypatch.setattr(
        "nextballup_api.storage.boto3.client",
        lambda *_args, **_kwargs: _MultipartClient(),
    )
    presigner = S3StoragePresigner(_storage_settings())

    upload = presigner.presign_upload(
        key="raw/team/video/clip.mov",
        content_type="video/quicktime",
        file_size_bytes=2 * 1024 * 1024 * 1024,
    )

    assert upload.method is UploadMethod.MULTIPART
    assert upload.upload_id == "upload-alpha-1"
    assert upload.parts is not None and len(upload.parts) >= 20
