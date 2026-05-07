from __future__ import annotations

import base64
import logging
import math
import re
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

import boto3
from anyio import to_thread
from botocore.client import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from nextballup_core.constants import ErrorCode
from nextballup_core.enums import UploadMethod
from nextballup_core.errors import AppError, ServiceUnavailableError
from nextballup_core.settings import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PresignedPart:
    part_number: int
    url: str


@dataclass(frozen=True)
class PresignedUpload:
    """Result of a presign request — covers both single PUT and multipart."""

    method: UploadMethod
    url: str | None = None  # set for PUT
    headers: dict[str, str] | None = None  # set for PUT
    upload_id: str | None = None  # set for MULTIPART
    parts: tuple[PresignedPart, ...] | None = None  # set for MULTIPART
    part_size_bytes: int | None = None  # set for MULTIPART


class StorageNotConfiguredError(ServiceUnavailableError):
    code = ErrorCode.STORAGE_NOT_CONFIGURED


class StorageFailureError(AppError):
    status_code = 502
    code = ErrorCode.STORAGE_FAILURE


class StoragePresigner(Protocol):
    """Operations the upload + playback flows need from the object store.
    The protocol keeps router/worker code testable — fakes implement just
    these methods without pulling in boto3."""

    def is_configured(self) -> bool: ...

    def presign_upload(
        self,
        *,
        key: str,
        content_type: str,
        file_size_bytes: int,
        checksum_sha256: str | None = None,
    ) -> PresignedUpload: ...

    def complete_multipart(
        self, *, key: str, upload_id: str, parts: list[dict[str, Any]]
    ) -> None: ...

    def abort_multipart(self, *, key: str, upload_id: str) -> None: ...

    def head_object(self, *, key: str) -> dict[str, Any] | None: ...

    def presign_get(
        self, *, key: str, expires_in: int, response_content_type: str | None = None
    ) -> str: ...

    def download_file(self, *, key: str, destination: str) -> None: ...

    def upload_file(
        self,
        *,
        key: str,
        source: str,
        content_type: str,
        metadata: dict[str, str] | None = None,
    ) -> None: ...

    def delete_object(self, *, key: str) -> None: ...


class S3StoragePresigner:
    """Boto3-backed presigner. Suitable for AWS S3 and MinIO (path-style addressing)."""

    def __init__(self, settings: Settings) -> None:
        if not settings.storage_configured():
            raise StorageNotConfiguredError("Object storage is not configured")
        self._settings = settings
        self._bucket = settings.s3_bucket_raw or ""
        endpoint = urlparse(settings.s3_endpoint_url or "")
        if endpoint.scheme not in {"http", "https"} or not endpoint.netloc:
            raise StorageFailureError(
                "Object storage endpoint is invalid",
                details={"reason": "invalid_endpoint_url"},
            )
        try:
            self._client = boto3.client(
                "s3",
                endpoint_url=settings.s3_endpoint_url,
                aws_access_key_id=settings.s3_access_key,
                aws_secret_access_key=settings.s3_secret_key,
                region_name=settings.s3_region,
                config=BotoConfig(
                    signature_version="s3v4",
                    s3={"addressing_style": "path"},
                    retries={"max_attempts": 3, "mode": "standard"},
                ),
            )
        except (BotoCoreError, ValueError) as exc:
            raise StorageFailureError(
                "Object storage client could not be initialized",
                details={"reason": "client_initialization_failed"},
            ) from exc

    def is_configured(self) -> bool:
        return True

    def presign_upload(
        self,
        *,
        key: str,
        content_type: str,
        file_size_bytes: int,
        checksum_sha256: str | None = None,
    ) -> PresignedUpload:
        threshold = self._settings.upload_multipart_threshold_bytes
        expires_in = self._settings.upload_url_expires_seconds
        if file_size_bytes <= threshold:
            params: dict[str, Any] = {
                "Bucket": self._bucket,
                "Key": key,
                "ContentType": content_type,
            }
            headers = {"Content-Type": content_type}
            if checksum_sha256 and self._settings.upload_presigned_put_checksum_header:
                checksum_b64 = base64.b64encode(bytes.fromhex(checksum_sha256)).decode("ascii")
                params["ChecksumSHA256"] = checksum_b64
                headers["x-amz-checksum-sha256"] = checksum_b64
            if checksum_sha256:
                params["Metadata"] = {"nbu-sha256": checksum_sha256}
                headers["x-amz-meta-nbu-sha256"] = checksum_sha256
            try:
                url = self._client.generate_presigned_url(
                    "put_object",
                    Params=params,
                    ExpiresIn=expires_in,
                    HttpMethod="PUT",
                )
            except (BotoCoreError, ClientError) as exc:
                raise StorageFailureError(
                    "Failed to presign upload URL", details={"key": key}
                ) from exc
            return PresignedUpload(
                method=UploadMethod.PUT,
                url=url,
                headers=headers,
            )

        part_size = self._settings.upload_multipart_part_size_bytes
        num_parts = max(1, math.ceil(file_size_bytes / part_size))
        upload_id: str | None = None
        try:
            response = self._client.create_multipart_upload(
                Bucket=self._bucket, Key=key, ContentType=content_type
            )
            upload_id = response.get("UploadId")
            if not isinstance(upload_id, str) or not upload_id:
                raise StorageFailureError(
                    "Storage did not return a multipart upload id",
                    details={"key": key},
                )
            parts = tuple(
                PresignedPart(
                    part_number=i,
                    url=self._client.generate_presigned_url(
                        "upload_part",
                        Params={
                            "Bucket": self._bucket,
                            "Key": key,
                            "UploadId": upload_id,
                            "PartNumber": i,
                        },
                        ExpiresIn=expires_in,
                    ),
                )
                for i in range(1, num_parts + 1)
            )
        except (BotoCoreError, ClientError) as exc:
            if upload_id is not None:
                self.abort_multipart(key=key, upload_id=upload_id)
            raise StorageFailureError(
                "Failed to initiate multipart upload", details={"key": key}
            ) from exc
        return PresignedUpload(
            method=UploadMethod.MULTIPART,
            upload_id=upload_id,
            parts=parts,
            part_size_bytes=part_size,
        )

    def complete_multipart(self, *, key: str, upload_id: str, parts: list[dict[str, Any]]) -> None:
        sorted_parts = sorted(parts, key=lambda p: int(p["PartNumber"]))
        try:
            self._client.complete_multipart_upload(
                Bucket=self._bucket,
                Key=key,
                UploadId=upload_id,
                MultipartUpload={"Parts": sorted_parts},
            )
        except (BotoCoreError, ClientError) as exc:
            raise StorageFailureError(
                "Failed to complete multipart upload",
                details={"key": key, "upload_id": upload_id},
            ) from exc

    def abort_multipart(self, *, key: str, upload_id: str) -> None:
        try:
            self._client.abort_multipart_upload(Bucket=self._bucket, Key=key, UploadId=upload_id)
        except (BotoCoreError, ClientError) as exc:
            logger.warning("Failed to abort multipart upload %s", upload_id, exc_info=True)
            raise StorageFailureError(
                "Failed to abort multipart upload",
                details={"key": key, "upload_id": upload_id},
            ) from exc

    def head_object(self, *, key: str) -> dict[str, Any] | None:
        try:
            response: dict[str, Any] = self._client.head_object(Bucket=self._bucket, Key=key)
            return response
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise StorageFailureError("Storage head_object failed", details={"key": key}) from exc
        except BotoCoreError as exc:
            raise StorageFailureError("Storage head_object failed", details={"key": key}) from exc

    def presign_get(
        self, *, key: str, expires_in: int, response_content_type: str | None = None
    ) -> str:
        params: dict[str, Any] = {"Bucket": self._bucket, "Key": key}
        if response_content_type:
            params["ResponseContentType"] = response_content_type
        try:
            url: str = self._client.generate_presigned_url(
                "get_object",
                Params=params,
                ExpiresIn=expires_in,
                HttpMethod="GET",
            )
        except (BotoCoreError, ClientError) as exc:
            raise StorageFailureError("Failed to presign GET URL", details={"key": key}) from exc
        return url

    def download_file(self, *, key: str, destination: str) -> None:
        try:
            self._client.download_file(self._bucket, key, destination)
        except (BotoCoreError, ClientError) as exc:
            raise StorageFailureError("Failed to download object", details={"key": key}) from exc

    def upload_file(
        self,
        *,
        key: str,
        source: str,
        content_type: str,
        metadata: dict[str, str] | None = None,
    ) -> None:
        extra_args: dict[str, Any] = {"ContentType": content_type}
        if metadata:
            extra_args["Metadata"] = metadata
        try:
            self._client.upload_file(
                source,
                self._bucket,
                key,
                ExtraArgs=extra_args,
            )
        except (BotoCoreError, ClientError) as exc:
            raise StorageFailureError("Failed to upload object", details={"key": key}) from exc

    def delete_object(self, *, key: str) -> None:
        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
        except (BotoCoreError, ClientError) as exc:
            raise StorageFailureError("Failed to delete object", details={"key": key}) from exc


def get_storage_presigner(settings: Settings) -> StoragePresigner | None:
    """Construct a presigner only when all S3 settings are populated. Routers
    treat a `None` return as the storage-not-configured signal."""
    if not settings.storage_configured():
        return None
    return S3StoragePresigner(settings)


async def storage_presign_upload(
    presigner: StoragePresigner,
    *,
    key: str,
    content_type: str,
    file_size_bytes: int,
    checksum_sha256: str | None = None,
) -> PresignedUpload:
    return await to_thread.run_sync(
        lambda: presigner.presign_upload(
            key=key,
            content_type=content_type,
            file_size_bytes=file_size_bytes,
            checksum_sha256=checksum_sha256,
        )
    )


async def storage_complete_multipart(
    presigner: StoragePresigner,
    *,
    key: str,
    upload_id: str,
    parts: list[dict[str, Any]],
) -> None:
    await to_thread.run_sync(
        lambda: presigner.complete_multipart(
            key=key,
            upload_id=upload_id,
            parts=parts,
        )
    )


async def storage_abort_multipart(
    presigner: StoragePresigner,
    *,
    key: str,
    upload_id: str,
) -> None:
    await to_thread.run_sync(lambda: presigner.abort_multipart(key=key, upload_id=upload_id))


async def storage_head_object(
    presigner: StoragePresigner,
    *,
    key: str,
) -> dict[str, Any] | None:
    return await to_thread.run_sync(lambda: presigner.head_object(key=key))


async def storage_presign_get(
    presigner: StoragePresigner,
    *,
    key: str,
    expires_in: int,
    response_content_type: str | None = None,
) -> str:
    return await to_thread.run_sync(
        lambda: presigner.presign_get(
            key=key,
            expires_in=expires_in,
            response_content_type=response_content_type,
        )
    )


async def storage_download_file(
    presigner: StoragePresigner,
    *,
    key: str,
    destination: str,
) -> None:
    await to_thread.run_sync(lambda: presigner.download_file(key=key, destination=destination))


async def storage_upload_file(
    presigner: StoragePresigner,
    *,
    key: str,
    source: str,
    content_type: str,
    metadata: dict[str, str] | None = None,
) -> None:
    await to_thread.run_sync(
        lambda: presigner.upload_file(
            key=key,
            source=source,
            content_type=content_type,
            metadata=metadata,
        )
    )


async def storage_delete_object(
    presigner: StoragePresigner,
    *,
    key: str,
) -> None:
    await to_thread.run_sync(lambda: presigner.delete_object(key=key))


# Only `[A-Za-z0-9._-]` survive sanitation. Anything else — unicode, spaces,
# path separators, control chars, shell metacharacters — collapses to `_`.
# The original filename stays on the Video row for display; this is purely
# the on-disk/S3 key component, which must be stable, case-safe, and free of
# anything that could confuse intermediate systems (log pipelines, URL
# libraries, CDN key-validation rules).
_SAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]")
_MULTIDOT = re.compile(r"\.{2,}")


def _sanitize_filename_component(filename: str) -> str:
    """Reduce the user-supplied filename to an S3-safe token.

    Belt-and-suspenders: the API validation layer already rejects filenames
    with traversal segments and control characters, so this function should
    in practice only see already-safe input. The reason we still sanitize:
    if a future code path ever constructs a storage key with a less-strict
    validator (e.g., an admin backfill tool), the key must stay safe.
    """
    if not filename:
        return "upload"
    cleaned = _SAFE_FILENAME_CHARS.sub("_", filename)
    # "..", "....", ". ." etc. are traversal markers in most filesystems —
    # replace any dot-run ≥ 2 so the sanitized key can never resemble one.
    cleaned = _MULTIDOT.sub("__", cleaned)
    # Strip leading/trailing dots and underscores so we don't create hidden
    # files (`.foo`) or awkward double-delimiter keys.
    cleaned = cleaned.strip("._") or "upload"
    return cleaned[:200]


def storage_key_for_video(*, team_id: str, video_id: str, filename: str) -> str:
    """Deterministic raw-bucket key. Including team_id keeps cross-tenant
    enumeration impossible even if a key leaks; including the video_id
    guarantees per-video uniqueness."""
    safe_name = _sanitize_filename_component(filename)
    return f"raw/{team_id}/{video_id}/{safe_name}"


def storage_key_for_mezzanine(*, team_id: str, video_id: str) -> str:
    return f"mezzanine/{team_id}/{video_id}/video.mp4"


def storage_key_for_hls(*, team_id: str, video_id: str) -> str:
    return f"hls/{team_id}/{video_id}/manifest.m3u8"


def storage_key_for_thumbnail(*, team_id: str, video_id: str) -> str:
    return f"thumbnails/{team_id}/{video_id}/preview.jpg"


def storage_key_for_demo_preview(*, team_id: str, video_id: str) -> str:
    return f"artifacts/{team_id}/{video_id}/demo-preview.annotated.mp4"


# --- CV-stage artifact layout ---------------------------------------------
#
# Each downstream CV stage deposits its output at a deterministic key under
# `artifacts/{team}/{video}/`. Co-locating them means the retention/cleanup
# code can prune per-video artifacts with a single bucket-list + prefix
# delete, and keeps cross-stage joins (e.g. events referencing tracking IDs)
# addressable by convention alone. The `.json` suffix is load-bearing for
# downstream CDN/content-type routing and for admin tooling that inspects
# artifacts without having to query the DB for content type.


def storage_key_for_detections(*, team_id: str, video_id: str) -> str:
    return f"artifacts/{team_id}/{video_id}/detections.json"


def storage_key_for_tracking(*, team_id: str, video_id: str) -> str:
    return f"artifacts/{team_id}/{video_id}/tracking.json"


def storage_key_for_court_mapping(*, team_id: str, video_id: str) -> str:
    return f"artifacts/{team_id}/{video_id}/court.json"


def storage_key_for_events(*, team_id: str, video_id: str) -> str:
    return f"artifacts/{team_id}/{video_id}/events.json"


def storage_key_for_metrics(*, team_id: str, video_id: str) -> str:
    return f"artifacts/{team_id}/{video_id}/metrics.json"


def normalize_etag(etag: str | None) -> str | None:
    """Strip the surrounding quotes S3 ships with ETag values so we get a
    storage-canonical hex string. Multipart ETags retain a `-N` suffix that
    we keep — it's how we detect "this came from multipart, MD5 isn't valid
    over the whole object"."""
    if etag is None:
        return None
    return etag.strip().strip('"') or None
