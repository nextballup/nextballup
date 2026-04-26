from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from nextballup_worker.errors import PermanentProcessingError
from nextballup_worker.runtime import media as media_module
from nextballup_worker.runtime.media import (
    _build_containerized_ffmpeg_command,
    _build_ffmpeg_command,
    _media_subprocess_env,
    _media_subprocess_kwargs,
    _select_transcoder,
    create_browser_mezzanine,
)

from nextballup_core.constants import ErrorCode
from nextballup_core.enums import UploadMethod
from nextballup_core.settings import get_settings
from nextballup_db.models.video import Video

_MP4_PAYLOAD = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
_PNG_PAYLOAD = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"


class _DownloadedObjectStorage:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.uploaded_payloads: dict[str, bytes] = {}
        self.uploaded_metadata: dict[str, dict[str, str]] = {}

    def is_configured(self) -> bool:
        return True

    def presign_upload(
        self,
        *,
        key: str,
        content_type: str,
        file_size_bytes: int,
        checksum_sha256: str | None = None,
    ) -> Any:
        return {"method": UploadMethod.PUT}

    def complete_multipart(self, *, key: str, upload_id: str, parts: list[dict[str, Any]]) -> None:
        return None

    def abort_multipart(self, *, key: str, upload_id: str) -> None:
        return None

    def head_object(self, *, key: str) -> dict[str, Any] | None:
        if key in self.uploaded_payloads:
            return {
                "ContentLength": len(self.uploaded_payloads[key]),
                "ETag": '"0123456789abcdef0123456789abcdef"',
                "Metadata": self.uploaded_metadata.get(key, {}),
            }
        return {"ContentLength": len(self.payload)}

    def presign_get(
        self, *, key: str, expires_in: int, response_content_type: str | None = None
    ) -> str:
        return "https://storage.test/object"

    def download_file(self, *, key: str, destination: str) -> None:
        Path(destination).write_bytes(self.payload)

    def upload_file(
        self,
        *,
        key: str,
        source: str,
        content_type: str,
        metadata: dict[str, str] | None = None,
    ) -> None:
        self.uploaded_payloads[key] = Path(source).read_bytes()
        self.uploaded_metadata[key] = dict(metadata or {})

    def delete_object(self, *, key: str) -> None:
        return None


def test_build_ffmpeg_command_strips_sensitive_metadata_and_targets_mp4() -> None:
    command = _build_ffmpeg_command(
        binary="ffmpeg",
        input_path=Path("/tmp/input.mov"),
        output_path=Path("/tmp/output.mp4"),
        threads=2,
    )

    assert "-map_metadata" in command
    assert "-1" in command
    assert "-map_chapters" in command
    assert "-sn" in command
    assert "-dn" in command
    assert "-movflags" in command
    assert "+faststart" in command
    assert command[command.index("-threads") + 1] == "2"
    assert command[-1] == "/tmp/output.mp4"


def test_media_subprocess_env_drops_application_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("S3_SECRET_KEY", "secret")
    monkeypatch.setenv("DATABASE_URL", "postgresql://example")

    env = _media_subprocess_env()

    assert env["PATH"] == "/usr/bin"
    assert "S3_SECRET_KEY" not in env
    assert "DATABASE_URL" not in env


def test_media_subprocess_kwargs_uses_tempdir_cwd_and_sanitized_env(tmp_path: Path) -> None:
    settings = get_settings().model_copy(update={"worker_media_subprocess_sandbox": False})

    kwargs = _media_subprocess_kwargs(settings, cwd=tmp_path)

    assert kwargs["cwd"] == str(tmp_path)
    assert "env" in kwargs
    assert "preexec_fn" not in kwargs


def test_build_containerized_ffmpeg_command_is_networkless_and_hardened(tmp_path: Path) -> None:
    settings = get_settings().model_copy(
        update={
            "worker_media_container_image": "nextballup-ffmpeg-sandbox:6.1",
            "worker_media_container_binary": "ffmpeg",
        }
    )
    input_path = tmp_path / "input.mov"
    output_path = tmp_path / "video.mp4"
    command = _build_ffmpeg_command(
        binary="/usr/local/bin/ffmpeg",
        input_path=input_path,
        output_path=output_path,
        threads=2,
    )

    container = _build_containerized_ffmpeg_command(
        cmd=command,
        settings=settings,
        cwd=tmp_path,
    )

    assert container[:3] == ["docker", "run", "--rm"]
    assert "--network" in container
    assert container[container.index("--network") + 1] == "none"
    assert "--read-only" in container
    assert "ALL" in container
    assert "no-new-privileges" in container
    assert f"{tmp_path.resolve()}:/work:rw" in container
    assert "/work/input.mov" in container
    assert "/work/video.mp4" in container


def test_select_transcoder_prefers_ffmpeg(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings().model_copy(
        update={"worker_ffmpeg_binary": "ffmpeg", "worker_avconvert_binary": "avconvert"}
    )

    def _fake_which(binary: str) -> str | None:
        if binary == "ffmpeg":
            return "/usr/local/bin/ffmpeg"
        if binary == "avconvert":
            return "/usr/bin/avconvert"
        return None

    monkeypatch.setattr("nextballup_worker.runtime.media.shutil.which", _fake_which)
    monkeypatch.setattr("nextballup_worker.runtime.media.sys.platform", "darwin")

    transcoder, binaries = _select_transcoder(settings)
    assert transcoder == "ffmpeg"
    assert binaries == ["/usr/local/bin/ffmpeg"]


def test_select_transcoder_uses_mac_fallback_when_ffmpeg_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings().model_copy(
        update={"worker_ffmpeg_binary": "ffmpeg", "worker_avconvert_binary": "avconvert"}
    )

    def _fake_which(binary: str) -> str | None:
        if binary == "avconvert":
            return "/usr/bin/avconvert"
        return None

    monkeypatch.setattr("nextballup_worker.runtime.media.shutil.which", _fake_which)
    monkeypatch.setattr("nextballup_worker.runtime.media.sys.platform", "darwin")

    transcoder, binaries = _select_transcoder(settings)
    assert transcoder == "avconvert"
    assert binaries == ["/usr/bin/avconvert"]


def test_select_transcoder_fails_closed_when_none_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings().model_copy(
        update={"worker_ffmpeg_binary": "ffmpeg", "worker_avconvert_binary": "avconvert"}
    )

    monkeypatch.setattr("nextballup_worker.runtime.media.shutil.which", lambda _: None)
    monkeypatch.setattr("nextballup_worker.runtime.media.sys.platform", "linux")

    with pytest.raises(PermanentProcessingError) as exc:
        _select_transcoder(settings)

    assert exc.value.code == ErrorCode.PROCESSING_TRANSCODER_UNAVAILABLE


@pytest.mark.asyncio(loop_scope="session")
async def test_create_browser_mezzanine_rehashes_download_before_transcode() -> None:
    video = Video(
        id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        storage_key_raw="raw/team/video/input.mp4",
        filename="input.mp4",
        checksum_sha256="a" * 64,
    )

    with pytest.raises(PermanentProcessingError) as exc:
        await create_browser_mezzanine(
            video=video,
            presigner=_DownloadedObjectStorage(b"not-the-declared-object"),
            settings=get_settings(),
        )

    assert exc.value.code == ErrorCode.PROCESSING_CHECKSUM_MISMATCH


@pytest.mark.asyncio(loop_scope="session")
async def test_create_browser_mezzanine_hashes_and_uploads_output_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _MP4_PAYLOAD + b"declared-object"
    output_payload = b"browser-safe-mp4"
    video = Video(
        id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        storage_key_raw="raw/team/video/input.mp4",
        filename="input.mp4",
        content_type="video/mp4",
        checksum_sha256=hashlib.sha256(payload).hexdigest(),
    )
    storage = _DownloadedObjectStorage(payload)

    def _fake_run_subprocess(**kwargs: Any) -> None:
        cmd = kwargs["cmd"]
        Path(cmd[-1]).write_bytes(output_payload)

    monkeypatch.setattr(
        media_module, "_select_transcoder", lambda _settings: ("ffmpeg", ["ffmpeg"])
    )
    monkeypatch.setattr(media_module, "_run_subprocess", _fake_run_subprocess)
    monkeypatch.setattr(
        media_module,
        "_probe_output_sync",
        lambda _path, _settings: SimpleNamespace(
            duration_seconds=1.0,
            width=640,
            height=360,
            fps=30.0,
            codec="h264",
        ),
    )

    artifact = await create_browser_mezzanine(
        video=video,
        presigner=storage,
        settings=get_settings(),
    )

    expected_sha = hashlib.sha256(output_payload).hexdigest()
    assert artifact.output_sha256 == expected_sha
    assert artifact.output_size_bytes == len(output_payload)
    assert storage.uploaded_metadata[artifact.mezzanine_key] == {"nbu-output-sha256": expected_sha}


@pytest.mark.asyncio(loop_scope="session")
async def test_create_browser_mezzanine_rejects_magic_type_mismatch() -> None:
    video = Video(
        id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        storage_key_raw="raw/team/video/input.mp4",
        filename="input.mp4",
        content_type="video/mp4",
    )

    with pytest.raises(PermanentProcessingError) as exc:
        await create_browser_mezzanine(
            video=video,
            presigner=_DownloadedObjectStorage(_PNG_PAYLOAD),
            settings=get_settings(),
        )

    assert exc.value.code == ErrorCode.PROCESSING_CONTENT_TYPE_MISMATCH
    assert exc.value.details == {
        "declared_content_type": "video/mp4",
        "detected_content_type": "image/png",
    }
