from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

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


def _build_ffmpeg_command(
    *, binary: str, input_path: Path, output_path: Path
) -> list[str]:
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
        "-preset",
        "veryfast",
        "-crf",
        "23",
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


def _build_avconvert_command(
    *, binary: str, input_path: Path, output_path: Path
) -> list[str]:
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


def _run_subprocess(
    *, cmd: list[str], timeout_seconds: int, unavailable_code: str
) -> None:
    try:
        subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
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

    with tempfile.TemporaryDirectory(prefix="nbu-transcode-") as tempdir:
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

        transcoder, binaries = _select_transcoder(settings)
        if transcoder == "ffmpeg":
            await to_thread.run_sync(
                lambda: _run_subprocess(
                    cmd=_build_ffmpeg_command(
                        binary=binaries[0],
                        input_path=input_path,
                        output_path=output_path,
                    ),
                    timeout_seconds=settings.worker_transcode_timeout_seconds,
                    unavailable_code=ErrorCode.PROCESSING_TRANSCODER_UNAVAILABLE,
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

        probe = await to_thread.run_sync(lambda: _probe_output_sync(output_path, settings))

        try:
            await storage_upload_file(
                presigner,
                key=mezzanine_key,
                source=str(output_path),
                content_type="video/mp4",
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
            output_size_bytes=stored_size,
            duration_seconds=probe.duration_seconds,
            width=probe.width,
            height=probe.height,
            fps=probe.fps,
            codec=probe.codec,
            transcoder=transcoder,
        )
