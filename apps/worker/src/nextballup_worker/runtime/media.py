from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
from hashlib import sha256
from pathlib import Path
from typing import Any

import filetype  # type: ignore[import-untyped]
from anyio import to_thread
from nextballup_api.storage import (
    StorageFailureError,
    StoragePresigner,
    normalize_etag,
    storage_download_file,
    storage_head_object,
    storage_key_for_mezzanine,
    storage_upload_file,
)

from nextballup_core.constants import ErrorCode
from nextballup_core.settings import Settings
from nextballup_db.models.video import Video
from nextballup_worker.errors import PermanentProcessingError, TransientProcessingError


@dataclass(frozen=True)
class BrowserMezzanineArtifact:
    mezzanine_key: str
    storage_etag: str | None
    output_sha256: str
    output_size_bytes: int
    duration_seconds: float | None
    width: int | None
    height: int | None
    fps: float | None
    codec: str | None
    transcoder: str


@dataclass(frozen=True)
class _ProbeResult:
    duration_seconds: float | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    codec: str | None = None


_MEDIA_ENV_ALLOWLIST = ("PATH", "HOME", "TMPDIR", "TEMP", "TMP", "LANG", "LC_ALL")
_MEDIA_ENV_DENY_PREFIXES = (
    "AWS_",
    "DATABASE_",
    "JWT_",
    "OPENAI_",
    "REDIS_",
    "S3_",
    "SECRET",
)
_MAGIC_PROBE_BYTES = 8 * 1024


def _build_ffmpeg_command(
    *,
    binary: str,
    input_path: Path,
    output_path: Path,
    threads: int | None = None,
    max_width: int = 1280,
    max_fps: int = 30,
    crf: int = 26,
    preset: str = "veryfast",
) -> list[str]:
    thread_args: list[str] = []
    if threads is not None:
        thread_args = ["-threads", str(threads)]
    filter_parts: list[str] = []
    if max_width > 0:
        filter_parts.append(f"scale='min({max_width},iw)':-2:force_original_aspect_ratio=decrease")
    if max_fps > 0:
        filter_parts.append(f"fps=fps='min({max_fps},source_fps)'")
    filter_args = ["-vf", ",".join(filter_parts)] if filter_parts else []
    return [
        binary,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(input_path),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        # Strip source metadata, chapters, subtitle, and data streams so the
        # playback artifact doesn't leak device/location or unrelated tracks.
        "-map_metadata",
        "-1",
        "-map_chapters",
        "-1",
        "-sn",
        "-dn",
        "-c:v",
        "libx264",
        *thread_args,
        *filter_args,
        "-preset",
        preset,
        "-crf",
        str(crf),
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        str(output_path),
    ]


def _build_avconvert_command(*, binary: str, input_path: Path, output_path: Path) -> list[str]:
    return [
        binary,
        "--source",
        str(input_path),
        "--output",
        str(output_path),
        "--preset",
        "PresetHighestQuality",
        "--replace",
    ]


def _media_subprocess_env() -> dict[str, str]:
    """Return a minimal environment for untrusted media tooling."""
    clean: dict[str, str] = {}
    for key in _MEDIA_ENV_ALLOWLIST:
        value = os.environ.get(key)
        if value:
            clean[key] = value
    for key in list(clean):
        if key.upper().startswith(_MEDIA_ENV_DENY_PREFIXES):
            clean.pop(key, None)
    return clean


def _media_preexec_fn(settings: Settings) -> Callable[[], None] | None:
    if not settings.worker_media_subprocess_sandbox or os.name != "posix":
        return None

    def _apply_limits() -> None:
        os.setsid()
        try:
            import resource
        except ImportError:  # pragma: no cover - resource is POSIX-only.
            return

        if settings.worker_media_max_cpu_seconds > 0:
            resource.setrlimit(
                resource.RLIMIT_CPU,
                (
                    settings.worker_media_max_cpu_seconds,
                    settings.worker_media_max_cpu_seconds,
                ),
            )
        if settings.worker_media_max_output_bytes > 0:
            resource.setrlimit(
                resource.RLIMIT_FSIZE,
                (
                    settings.worker_media_max_output_bytes,
                    settings.worker_media_max_output_bytes,
                ),
            )
        resource.setrlimit(
            resource.RLIMIT_NOFILE,
            (
                settings.worker_media_max_open_files,
                settings.worker_media_max_open_files,
            ),
        )

    return _apply_limits


def _media_subprocess_kwargs(settings: Settings, *, cwd: Path) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "cwd": str(cwd),
        "env": _media_subprocess_env(),
    }
    preexec_fn = _media_preexec_fn(settings)
    if preexec_fn is not None:
        kwargs["preexec_fn"] = preexec_fn
    return kwargs


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _detect_file_mime(path: Path) -> str | None:
    with path.open("rb") as handle:
        kind = filetype.guess(handle.read(_MAGIC_PROBE_BYTES))
    mime = getattr(kind, "mime", None)
    return mime.lower() if isinstance(mime, str) else None


def _declared_mime(content_type: str | None) -> str | None:
    if content_type is None:
        return None
    value = content_type.split(";", 1)[0].strip().lower()
    return value or None


def _validate_downloaded_content_type(path: Path, *, declared_content_type: str | None) -> None:
    declared = _declared_mime(declared_content_type)
    if declared is None:
        return
    detected = _detect_file_mime(path)
    if detected != declared:
        raise PermanentProcessingError(
            "Uploaded object MIME type does not match the declared content type",
            code=ErrorCode.PROCESSING_CONTENT_TYPE_MISMATCH,
            details={
                "declared_content_type": declared,
                "detected_content_type": detected,
            },
        )


def _is_ffmpeg_command(cmd: list[str], settings: Settings) -> bool:
    binary_name = Path(cmd[0]).name if cmd else ""
    return binary_name == Path(settings.worker_ffmpeg_binary).name or binary_name == "ffmpeg"


def _container_path_arg(arg: str, *, cwd: Path) -> str:
    try:
        path = Path(arg)
        if not path.is_absolute():
            return arg
        relative = path.resolve().relative_to(cwd.resolve())
    except (OSError, ValueError):
        return arg
    return str(Path("/work") / relative)


def _build_containerized_ffmpeg_command(
    *,
    cmd: list[str],
    settings: Settings,
    cwd: Path,
) -> list[str]:
    translated_args = [_container_path_arg(arg, cwd=cwd) for arg in cmd[1:]]
    return [
        settings.worker_media_container_runtime,
        "run",
        "--rm",
        "--network",
        "none",
        "--cpus",
        str(settings.worker_media_container_cpus),
        "--memory",
        settings.worker_media_container_memory,
        "--pids-limit",
        str(settings.worker_media_container_pids_limit),
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--user",
        settings.worker_media_container_user,
        "--tmpfs",
        f"/tmp:rw,nosuid,nodev,noexec,size={settings.worker_media_container_tmpfs_size}",
        "-v",
        f"{cwd.resolve()}:/work:rw",
        "--workdir",
        "/work",
        settings.worker_media_container_image,
        settings.worker_media_container_binary,
        *translated_args,
    ]


def _run_subprocess(
    *,
    cmd: list[str],
    timeout_seconds: int,
    unavailable_code: str,
    settings: Settings,
    cwd: Path,
) -> None:
    subprocess_cmd = cmd
    subprocess_kwargs = _media_subprocess_kwargs(settings, cwd=cwd)
    if settings.worker_media_container_sandbox_enabled and _is_ffmpeg_command(cmd, settings):
        subprocess_cmd = _build_containerized_ffmpeg_command(cmd=cmd, settings=settings, cwd=cwd)
        subprocess_kwargs = {"cwd": str(cwd), "env": _media_subprocess_env()}
    try:
        subprocess.run(
            subprocess_cmd,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            **subprocess_kwargs,
        )
    except FileNotFoundError as exc:
        raise PermanentProcessingError(
            "No supported video transcoder is installed",
            code=unavailable_code,
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise PermanentProcessingError(
            "Video transcoding exceeded the worker timeout",
            code=ErrorCode.PROCESSING_TRANSCODE_FAILED,
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise PermanentProcessingError(
            "Video transcoding failed",
            code=ErrorCode.PROCESSING_TRANSCODE_FAILED,
        ) from exc


def _select_transcoder(settings: Settings) -> tuple[str, list[str]]:
    ffmpeg = shutil.which(settings.worker_ffmpeg_binary)
    if ffmpeg:
        return "ffmpeg", [ffmpeg]

    if sys.platform == "darwin":
        avconvert = shutil.which(settings.worker_avconvert_binary)
        if avconvert:
            return "avconvert", [avconvert]

    raise PermanentProcessingError(
        "No supported video transcoder is installed",
        code=ErrorCode.PROCESSING_TRANSCODER_UNAVAILABLE,
    )


def _probe_output_sync(output_path: Path, settings: Settings) -> _ProbeResult:
    ffprobe = shutil.which(settings.worker_ffprobe_binary)
    if not ffprobe:
        return _ProbeResult(codec="h264")
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=codec_name,width,height,r_frame_rate",
                "-of",
                "json",
                str(output_path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=min(settings.worker_transcode_timeout_seconds, 120),
            **_media_subprocess_kwargs(settings, cwd=output_path.parent),
        )
        payload = json.loads(result.stdout or "{}")
    except (FileNotFoundError, subprocess.SubprocessError, json.JSONDecodeError):
        return _ProbeResult(codec="h264")

    streams = payload.get("streams")
    stream = streams[0] if isinstance(streams, list) and streams else {}
    fmt = payload.get("format") if isinstance(payload.get("format"), dict) else {}
    duration_seconds = None
    raw_duration = fmt.get("duration")
    if isinstance(raw_duration, str):
        try:
            duration_seconds = float(raw_duration)
        except ValueError:
            duration_seconds = None

    fps = None
    raw_fps = stream.get("r_frame_rate")
    if isinstance(raw_fps, str) and raw_fps not in {"0/0", "N/A"}:
        try:
            fps = float(Fraction(raw_fps))
        except (ValueError, ZeroDivisionError):
            fps = None

    width = stream.get("width")
    height = stream.get("height")
    codec = stream.get("codec_name")
    return _ProbeResult(
        duration_seconds=duration_seconds,
        width=width if isinstance(width, int) else None,
        height=height if isinstance(height, int) else None,
        fps=fps,
        codec=codec if isinstance(codec, str) else "h264",
    )


def _media_temp_parent(settings: Settings) -> str | None:
    if settings.worker_media_temp_dir is None:
        return None
    temp_parent = settings.worker_media_temp_dir.expanduser()
    try:
        temp_parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise PermanentProcessingError(
            "Worker media scratch directory is not writable",
            code=ErrorCode.PROCESSING_TRANSCODE_FAILED,
        ) from exc
    if not temp_parent.is_dir():
        raise PermanentProcessingError(
            "Worker media scratch path is not a directory",
            code=ErrorCode.PROCESSING_TRANSCODE_FAILED,
        )
    return str(temp_parent)


async def create_browser_mezzanine(
    *,
    video: Video,
    presigner: StoragePresigner,
    settings: Settings,
) -> BrowserMezzanineArtifact:
    if not video.storage_key_raw:
        raise PermanentProcessingError(
            "Video has no raw storage key",
            code=ErrorCode.PROCESSING_OBJECT_MISSING,
        )

    mezzanine_key = storage_key_for_mezzanine(
        team_id=str(video.team_id),
        video_id=str(video.id),
    )
    source_suffix = Path(video.filename or "upload").suffix.lower() or ".bin"

    with tempfile.TemporaryDirectory(
        prefix="nbu-transcode-",
        dir=_media_temp_parent(settings),
    ) as tempdir:
        tempdir_path = Path(tempdir)
        input_path = tempdir_path / f"input{source_suffix}"
        output_path = tempdir_path / "video.mp4"

        try:
            await storage_download_file(
                presigner,
                key=video.storage_key_raw,
                destination=str(input_path),
            )
        except StorageFailureError as exc:
            raise TransientProcessingError(
                "Failed to download uploaded object for transcoding",
                code=ErrorCode.PROCESSING_STORAGE_FAILURE,
            ) from exc
        if video.checksum_sha256:
            actual_sha256 = await to_thread.run_sync(lambda: _sha256_file(input_path))
            if actual_sha256 != video.checksum_sha256.lower():
                raise PermanentProcessingError(
                    "Downloaded uploaded object SHA-256 does not match the recorded digest",
                    code=ErrorCode.PROCESSING_CHECKSUM_MISMATCH,
                )
        await to_thread.run_sync(
            lambda: _validate_downloaded_content_type(
                input_path,
                declared_content_type=video.content_type,
            )
        )

        transcoder, binaries = _select_transcoder(settings)
        if transcoder == "ffmpeg":
            await to_thread.run_sync(
                lambda: _run_subprocess(
                    cmd=_build_ffmpeg_command(
                        binary=binaries[0],
                        input_path=input_path,
                        output_path=output_path,
                        threads=settings.worker_ffmpeg_threads,
                        max_width=settings.worker_playback_max_width,
                        max_fps=settings.worker_playback_max_fps,
                        crf=settings.worker_playback_crf,
                        preset=settings.worker_playback_preset,
                    ),
                    timeout_seconds=settings.worker_transcode_timeout_seconds,
                    unavailable_code=ErrorCode.PROCESSING_TRANSCODER_UNAVAILABLE,
                    settings=settings,
                    cwd=tempdir_path,
                )
            )
        else:
            await to_thread.run_sync(
                lambda: _run_subprocess(
                    cmd=_build_avconvert_command(
                        binary=binaries[0],
                        input_path=input_path,
                        output_path=output_path,
                    ),
                    timeout_seconds=settings.worker_transcode_timeout_seconds,
                    unavailable_code=ErrorCode.PROCESSING_TRANSCODER_UNAVAILABLE,
                    settings=settings,
                    cwd=tempdir_path,
                )
            )

        try:
            output_size_bytes = output_path.stat().st_size
        except FileNotFoundError as exc:
            raise PermanentProcessingError(
                "Video transcoding did not produce an output artifact",
                code=ErrorCode.PROCESSING_TRANSCODE_FAILED,
            ) from exc
        if output_size_bytes <= 0:
            raise PermanentProcessingError(
                "Video transcoding produced an empty output artifact",
                code=ErrorCode.PROCESSING_TRANSCODE_FAILED,
            )

        output_sha256 = await to_thread.run_sync(lambda: _sha256_file(output_path))
        probe = await to_thread.run_sync(lambda: _probe_output_sync(output_path, settings))

        try:
            await storage_upload_file(
                presigner,
                key=mezzanine_key,
                source=str(output_path),
                content_type="video/mp4",
                metadata={"nbu-output-sha256": output_sha256},
            )
            metadata = await storage_head_object(presigner, key=mezzanine_key)
        except StorageFailureError as exc:
            raise TransientProcessingError(
                "Failed to upload browser playback artifact",
                code=ErrorCode.PROCESSING_STORAGE_FAILURE,
            ) from exc

        if metadata is None:
            raise TransientProcessingError(
                "Browser playback artifact was not found after upload",
                code=ErrorCode.PROCESSING_STORAGE_FAILURE,
            )
        stored_size_value = metadata.get("ContentLength")
        stored_size = stored_size_value if isinstance(stored_size_value, int) else output_size_bytes
        if stored_size <= 0:
            raise TransientProcessingError(
                "Browser playback artifact is empty in storage",
                code=ErrorCode.PROCESSING_STORAGE_FAILURE,
            )

        raw_etag = metadata.get("ETag")
        normalized_etag = normalize_etag(raw_etag if isinstance(raw_etag, str) else None)

        return BrowserMezzanineArtifact(
            mezzanine_key=mezzanine_key,
            storage_etag=normalized_etag,
            output_sha256=output_sha256,
            output_size_bytes=stored_size,
            duration_seconds=probe.duration_seconds,
            width=probe.width,
            height=probe.height,
            fps=probe.fps,
            codec=probe.codec,
            transcoder=transcoder,
        )
