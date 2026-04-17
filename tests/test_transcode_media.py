from __future__ import annotations

from pathlib import Path

import pytest
from nextballup_worker.errors import PermanentProcessingError
from nextballup_worker.runtime.media import _build_ffmpeg_command, _select_transcoder

from nextballup_core.constants import ErrorCode
from nextballup_core.settings import get_settings


def test_build_ffmpeg_command_strips_sensitive_metadata_and_targets_mp4() -> None:
    command = _build_ffmpeg_command(
        binary="ffmpeg",
        input_path=Path("/tmp/input.mov"),
        output_path=Path("/tmp/output.mp4"),
    )

    assert "-map_metadata" in command
    assert "-1" in command
    assert "-map_chapters" in command
    assert "-sn" in command
    assert "-dn" in command
    assert "-movflags" in command
    assert "+faststart" in command
    assert command[-1] == "/tmp/output.mp4"


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
