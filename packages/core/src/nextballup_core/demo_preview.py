from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from inspect import isawaitable
from pathlib import Path
from typing import Any, Literal

from anyio import to_thread

from nextballup_core.constants import ErrorCode
from nextballup_core.enums import VideoStatus
from nextballup_core.errors import ConflictError, ServiceUnavailableError
from nextballup_core.settings import Settings

DemoPreviewStatus = Literal["idle", "queued", "running", "completed", "failed"]
DownloadArtifactFile = Callable[[str, str], Awaitable[None]]
PreviewStartedHook = Callable[[], None | Awaitable[None]]
_CONTROL_CHARS = re.compile(r"[\x00-\x1F\x7F-\x9F]+")


@dataclass(frozen=True)
class DemoPreviewArtifact:
    output_path: Path
    url_path: str
    generated_at: datetime


@dataclass(frozen=True)
class DemoPreviewState:
    status: DemoPreviewStatus
    requested_at: datetime | None = None
    started_at: datetime | None = None
    generated_at: datetime | None = None
    task_id: str | None = None
    error_message: str | None = None


def _runtime_error(message: str, *, startup: bool) -> RuntimeError | ServiceUnavailableError:
    if startup:
        return RuntimeError(message)
    return ServiceUnavailableError(message, code=ErrorCode.DEMO_PREVIEW_FAILED)


def _normalized_error_message(message: str) -> str:
    cleaned = _CONTROL_CHARS.sub(" ", message).strip()
    collapsed = " ".join(cleaned.split())
    if collapsed:
        return collapsed[:1000]
    return "Local demo preview failed"


def _resolved_demo_root(settings: Settings) -> Path:
    return settings.resolve_repo_relative_path(settings.cv_demo_preview_root)


def _resolved_demo_temp_parent(settings: Settings) -> str | None:
    if settings.worker_media_temp_dir is None:
        return None
    temp_parent = settings.resolve_repo_relative_path(settings.worker_media_temp_dir)
    try:
        temp_parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ServiceUnavailableError(
            "Alpha detector preview scratch directory is not writable",
            code=ErrorCode.DEMO_PREVIEW_FAILED,
        ) from exc
    if not temp_parent.is_dir():
        raise ServiceUnavailableError(
            "Alpha detector preview scratch path is not a directory",
            code=ErrorCode.DEMO_PREVIEW_FAILED,
        )
    return str(temp_parent)


def _resolved_training_root(settings: Settings) -> Path:
    return settings.resolve_repo_relative_path(settings.cv_demo_training_repo_root)


def _resolved_demo_dir(settings: Settings, video_id: uuid.UUID) -> Path:
    return _resolved_demo_root(settings) / str(video_id)


def _resolved_demo_path(settings: Settings, video_id: uuid.UUID) -> Path:
    return _resolved_demo_dir(settings, video_id) / "demo-preview.annotated.mp4"


def _resolved_demo_state_path(settings: Settings, video_id: uuid.UUID) -> Path:
    return _resolved_demo_dir(settings, video_id) / "demo-preview.state.json"


def _resolved_demo_run_lock_path(settings: Settings, video_id: uuid.UUID) -> Path:
    return _resolved_demo_dir(settings, video_id) / ".demo-preview.run.lock"


def _resolved_demo_machine_lock_path(settings: Settings) -> Path:
    return _resolved_demo_root(settings) / ".demo-preview.machine.lock"


def _resolved_demo_queue_lock_path(settings: Settings, video_id: uuid.UUID) -> Path:
    return _resolved_demo_dir(settings, video_id) / ".demo-preview.queue.lock"


def demo_preview_url_path(video_id: uuid.UUID) -> str:
    return f"/api/v1/videos/{video_id}/demo-preview/artifact"


def _ensure_path_within_root(*, root: Path, path: Path, label: str, startup: bool) -> Path:
    resolved = path.expanduser()
    if not resolved.is_absolute():
        resolved = root / resolved
    resolved = resolved.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise _runtime_error(
            f"Local demo preview {label} must stay within the training repo root",
            startup=startup,
        ) from exc
    return resolved


def _preview_config_path(settings: Settings) -> Path:
    if settings.alpha_detector_preview_enabled():
        return settings.cv_alpha_detector_config_path
    return settings.cv_demo_config_path


def _preview_checkpoint_path(settings: Settings) -> Path:
    if settings.alpha_detector_preview_enabled():
        return settings.cv_alpha_detector_checkpoint_path
    return settings.cv_demo_checkpoint_path


def _validate_alpha_detector_preview_report(
    settings: Settings,
    *,
    training_root: Path,
    startup: bool,
) -> None:
    if not settings.alpha_detector_preview_enabled():
        return
    report_path = _ensure_path_within_root(
        root=training_root,
        path=settings.resolve_repo_relative_path(settings.cv_alpha_detector_eval_report_path),
        label="alpha detector eval report",
        startup=startup,
    )
    if not report_path.is_file():
        raise _runtime_error(
            "Alpha detector preview eval report is not available",
            startup=startup,
        )
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise _runtime_error(
            "Alpha detector preview eval report could not be read",
            startup=startup,
        ) from exc
    known_failure_modes = report.get("known_failure_modes")
    if not isinstance(known_failure_modes, list):
        raise _runtime_error(
            "Alpha detector preview eval report must declare known_failure_modes",
            startup=startup,
        )
    failure_modes = {str(mode) for mode in known_failure_modes}
    required_modes = {"internal_alpha_poc_only", "not_commercial_lineage"}
    if not required_modes.issubset(failure_modes):
        raise _runtime_error(
            "Alpha detector preview artifact must be marked internal_alpha_poc_only "
            "and not_commercial_lineage",
            startup=startup,
        )
    if report.get("stage") != "detect" or report.get("sport") != "basketball":
        raise _runtime_error(
            "Alpha detector preview eval report must describe a basketball detect artifact",
            startup=startup,
        )


def _validate_demo_preview_inputs(
    settings: Settings,
    *,
    startup: bool,
) -> tuple[Path, Path, Path, Path, Path]:
    training_root = _resolved_training_root(settings)
    if not training_root.is_dir():
        raise _runtime_error(
            "CV_DEMO_TRAINING_REPO_ROOT must point to the sibling training repo",
            startup=startup,
        )
    preview_root = _resolved_demo_root(settings)
    try:
        preview_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise _runtime_error(
            "CV_DEMO_PREVIEW_ROOT must be writable before enabling local demo previews",
            startup=startup,
        ) from exc

    script_path = _ensure_path_within_root(
        root=training_root,
        path=training_root / "scripts" / "local_demo_infer.py",
        label="script",
        startup=startup,
    )
    config_path = _ensure_path_within_root(
        root=training_root,
        path=settings.resolve_repo_relative_path(_preview_config_path(settings)),
        label="config",
        startup=startup,
    )
    checkpoint_path = _ensure_path_within_root(
        root=training_root,
        path=settings.resolve_repo_relative_path(_preview_checkpoint_path(settings)),
        label="checkpoint",
        startup=startup,
    )
    _validate_alpha_detector_preview_report(
        settings,
        training_root=training_root,
        startup=startup,
    )
    required_files = {
        "script": script_path,
        "config": config_path,
        "checkpoint": checkpoint_path,
    }
    missing = [name for name, path in required_files.items() if not path.is_file()]
    if missing:
        raise _runtime_error(
            "Local demo preview dependencies are not available: " + ", ".join(sorted(missing)),
            startup=startup,
        )
    return preview_root, training_root, script_path, config_path, checkpoint_path


def _chmod_best_effort(path: Path, mode: int) -> None:
    try:
        if path.is_symlink():
            return
        path.chmod(mode)
    except OSError:
        return


def _repair_demo_preview_permissions(
    settings: Settings,
    *,
    video_id: uuid.UUID | None = None,
) -> None:
    root = _resolved_demo_root(settings)
    if not root.exists():
        return

    _chmod_best_effort(root, 0o700)

    entries: list[Path]
    if video_id is None:
        try:
            entries = list(root.iterdir())
        except OSError:
            return
    else:
        entries = [_resolved_demo_dir(settings, video_id)]

    for entry in entries:
        if not entry.exists():
            continue
        if entry.is_dir():
            _chmod_best_effort(entry, 0o700)
            try:
                children = list(entry.iterdir())
            except OSError:
                continue
            for child in children:
                if child.is_dir():
                    _chmod_best_effort(child, 0o700)
                else:
                    _chmod_best_effort(child, 0o600)
            continue
        _chmod_best_effort(entry, 0o600)


def validate_demo_preview_runtime(
    settings: Settings,
    *,
    startup: bool,
    require_inference_runtime: bool = True,
) -> None:
    if not settings.local_demo_preview_enabled():
        return
    if settings.app_env != "test" and not settings.celery_broker_url:
        raise _runtime_error(
            "CV_DEMO_PREVIEW_ENABLED requires CELERY_BROKER_URL to be configured",
            startup=startup,
        )
    if not settings.celery_demo_preview_queue.strip():
        raise _runtime_error(
            "CELERY_DEMO_PREVIEW_QUEUE must be configured before enabling local demo previews",
            startup=startup,
        )
    if require_inference_runtime:
        _validate_demo_preview_inputs(settings, startup=startup)
    if startup and require_inference_runtime:
        _repair_demo_preview_permissions(settings)


def _datetime_to_json(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _datetime_from_json(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _write_demo_preview_state(
    *,
    settings: Settings,
    video_id: uuid.UUID,
    state: DemoPreviewState,
) -> DemoPreviewState:
    state_path = _resolved_demo_state_path(settings, video_id)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    _repair_demo_preview_permissions(settings, video_id=video_id)
    payload = {
        **asdict(state),
        "requested_at": _datetime_to_json(state.requested_at),
        "started_at": _datetime_to_json(state.started_at),
        "generated_at": _datetime_to_json(state.generated_at),
    }
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=state_path.parent,
        prefix=".demo-preview-state.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        json.dump(payload, handle, sort_keys=True)
        handle.write("\n")
        temp_path = Path(handle.name)
    temp_path.replace(state_path)
    _chmod_best_effort(state_path, 0o600)
    return state


def _read_demo_preview_state(*, settings: Settings, video_id: uuid.UUID) -> DemoPreviewState | None:
    state_path = _resolved_demo_state_path(settings, video_id)
    if not state_path.is_file():
        return None
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    status = payload.get("status")
    if status not in {"idle", "queued", "running", "completed", "failed"}:
        return None
    return DemoPreviewState(
        status=status,
        requested_at=_datetime_from_json(payload.get("requested_at")),
        started_at=_datetime_from_json(payload.get("started_at")),
        generated_at=_datetime_from_json(payload.get("generated_at")),
        task_id=payload.get("task_id") if isinstance(payload.get("task_id"), str) else None,
        error_message=(
            payload.get("error_message") if isinstance(payload.get("error_message"), str) else None
        ),
    )


def _state_is_stale(state: DemoPreviewState, *, settings: Settings) -> bool:
    if state.status not in {"queued", "running"}:
        return False
    reference = state.started_at or state.requested_at
    if reference is None:
        return True
    age_seconds = (datetime.now(tz=UTC) - reference).total_seconds()
    return age_seconds > settings.cv_demo_timeout_seconds + 60


def _lock_is_stale(lock_path: Path, *, stale_after_seconds: int) -> bool:
    try:
        age_seconds = datetime.now(tz=UTC).timestamp() - lock_path.stat().st_mtime
    except FileNotFoundError:
        return False
    return age_seconds > stale_after_seconds


def _acquire_lock(
    *,
    lock_path: Path,
    stale_after_seconds: int,
    conflict_message: str,
    conflict_code: str,
    details: dict[str, str] | None = None,
) -> Path:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as err:
            if _lock_is_stale(lock_path, stale_after_seconds=stale_after_seconds):
                lock_path.unlink(missing_ok=True)
                continue
            raise ConflictError(
                conflict_message,
                code=conflict_code,
                details=details or {},
            ) from err
        with os.fdopen(fd, "w", encoding="utf-8") as lock_file:
            lock_file.write(f"{datetime.now(tz=UTC).isoformat()}\n")
        return lock_path
    raise ConflictError(
        conflict_message,
        code=conflict_code,
        details=details or {},
    )


def acquire_demo_preview_queue_lock(
    *,
    settings: Settings,
    video_id: uuid.UUID,
    wait_timeout_seconds: float = 1.0,
) -> Path:
    deadline = time.monotonic() + wait_timeout_seconds
    while True:
        try:
            return _acquire_lock(
                lock_path=_resolved_demo_queue_lock_path(settings, video_id),
                stale_after_seconds=max(15, settings.cv_demo_timeout_seconds + 60),
                conflict_message="Another local demo preview request is already being queued",
                conflict_code=ErrorCode.DEMO_PREVIEW_IN_PROGRESS,
                details={"video_id": str(video_id)},
            )
        except ConflictError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.02)


def release_demo_preview_lock(lock_path: Path | None) -> None:
    if lock_path is None:
        return
    lock_path.unlink(missing_ok=True)


def resolve_demo_preview(*, settings: Settings, video_id: uuid.UUID) -> DemoPreviewArtifact | None:
    if not settings.local_demo_preview_enabled():
        return None
    _repair_demo_preview_permissions(settings, video_id=video_id)
    output_path = _resolved_demo_path(settings, video_id)
    if not output_path.is_file():
        return None
    generated_at = datetime.fromtimestamp(output_path.stat().st_mtime, tz=UTC)
    return DemoPreviewArtifact(
        output_path=output_path,
        url_path=demo_preview_url_path(video_id),
        generated_at=generated_at,
    )


def resolve_demo_preview_state(
    *,
    settings: Settings,
    video_id: uuid.UUID,
) -> DemoPreviewState:
    if not settings.local_demo_preview_enabled():
        return DemoPreviewState(status="idle")
    artifact = resolve_demo_preview(settings=settings, video_id=video_id)
    state = _read_demo_preview_state(settings=settings, video_id=video_id)
    if state is None:
        if artifact is None:
            return DemoPreviewState(status="idle")
        completed = DemoPreviewState(status="completed", generated_at=artifact.generated_at)
        return _write_demo_preview_state(settings=settings, video_id=video_id, state=completed)
    if _state_is_stale(state, settings=settings):
        stale = replace(
            state,
            status="failed",
            error_message="Local demo preview job became stale before finishing",
        )
        state = _write_demo_preview_state(settings=settings, video_id=video_id, state=stale)
    if state.status == "completed":
        if artifact is None:
            missing = replace(
                state,
                status="failed",
                generated_at=None,
                error_message="Local demo preview artifact is missing after completion",
            )
            return _write_demo_preview_state(settings=settings, video_id=video_id, state=missing)
        if state.generated_at != artifact.generated_at:
            state = _write_demo_preview_state(
                settings=settings,
                video_id=video_id,
                state=replace(state, generated_at=artifact.generated_at, error_message=None),
            )
        return state
    if state.status == "failed" and artifact is not None:
        restored = replace(
            state,
            status="completed",
            generated_at=artifact.generated_at,
            error_message=None,
        )
        return _write_demo_preview_state(settings=settings, video_id=video_id, state=restored)
    if artifact is not None and state.generated_at != artifact.generated_at:
        state = replace(state, generated_at=artifact.generated_at)
    return state


def mark_demo_preview_running(
    *,
    settings: Settings,
    video_id: uuid.UUID,
    task_id: str,
) -> DemoPreviewState:
    current = resolve_demo_preview_state(settings=settings, video_id=video_id)
    return _write_demo_preview_state(
        settings=settings,
        video_id=video_id,
        state=DemoPreviewState(
            status="running",
            requested_at=current.requested_at or datetime.now(tz=UTC),
            started_at=datetime.now(tz=UTC),
            generated_at=current.generated_at,
            task_id=task_id,
            error_message=None,
        ),
    )


def mark_demo_preview_queued(
    *,
    settings: Settings,
    video_id: uuid.UUID,
    task_id: str | None,
    generated_at: datetime | None,
    requested_at: datetime | None = None,
) -> DemoPreviewState:
    current = resolve_demo_preview_state(settings=settings, video_id=video_id)
    return _write_demo_preview_state(
        settings=settings,
        video_id=video_id,
        state=DemoPreviewState(
            status="queued",
            requested_at=requested_at or current.requested_at or datetime.now(tz=UTC),
            started_at=None,
            generated_at=generated_at if generated_at is not None else current.generated_at,
            task_id=task_id or current.task_id,
            error_message=None,
        ),
    )


def mark_demo_preview_failed(
    *,
    settings: Settings,
    video_id: uuid.UUID,
    task_id: str | None,
    error_message: str,
) -> DemoPreviewState:
    current = resolve_demo_preview_state(settings=settings, video_id=video_id)
    return _write_demo_preview_state(
        settings=settings,
        video_id=video_id,
        state=DemoPreviewState(
            status="failed",
            requested_at=current.requested_at,
            started_at=current.started_at,
            generated_at=current.generated_at,
            task_id=task_id or current.task_id,
            error_message=_normalized_error_message(error_message),
        ),
    )


def cleanup_expired_demo_previews(*, settings: Settings) -> list[str]:
    root = _resolved_demo_root(settings)
    if not root.is_dir():
        return []
    cutoff = datetime.now(tz=UTC).timestamp() - settings.cv_demo_retention_seconds
    cleaned: list[str] = []
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        try:
            video_id = uuid.UUID(entry.name)
        except ValueError:
            continue
        state = _read_demo_preview_state(settings=settings, video_id=video_id)
        if (
            state is not None
            and state.status in {"queued", "running"}
            and not _state_is_stale(
                state,
                settings=settings,
            )
        ):
            continue
        try:
            latest_mtime = max(
                (path.stat().st_mtime for path in entry.iterdir()),
                default=entry.stat().st_mtime,
            )
        except OSError:
            continue
        if latest_mtime >= cutoff:
            continue
        try:
            shutil.rmtree(entry)
        except OSError:
            continue
        cleaned.append(str(video_id))
    return cleaned


def mark_demo_preview_completed(
    *,
    settings: Settings,
    video_id: uuid.UUID,
    task_id: str | None,
    generated_at: datetime,
) -> DemoPreviewState:
    current = resolve_demo_preview_state(settings=settings, video_id=video_id)
    return _write_demo_preview_state(
        settings=settings,
        video_id=video_id,
        state=DemoPreviewState(
            status="completed",
            requested_at=current.requested_at,
            started_at=current.started_at or datetime.now(tz=UTC),
            generated_at=generated_at,
            task_id=task_id or current.task_id,
            error_message=None,
        ),
    )


def _require_demo_preview_enabled(settings: Settings) -> None:
    if settings.local_demo_preview_enabled():
        return
    raise ServiceUnavailableError(
        "Local demo preview is not enabled for this environment",
        code=ErrorCode.DEMO_PREVIEW_NOT_ENABLED,
    )


def _demo_preview_extra_path_entries() -> list[str]:
    entries: list[str] = []
    home = os.environ.get("HOME")
    if home:
        entries.append(str(Path(home).expanduser() / ".local" / "bin"))
    configured_uv = os.environ.get("UV_BIN")
    if configured_uv:
        configured_uv_path = Path(configured_uv).expanduser()
        if configured_uv_path.is_absolute():
            entries.append(str(configured_uv_path.parent))
    for binary in ("uv", "ffmpeg"):
        discovered = shutil.which(binary)
        if discovered:
            entries.append(str(Path(discovered).parent))
    entries.extend(("/opt/homebrew/bin", "/usr/local/bin"))
    return entries


def _build_demo_preview_path(path: str) -> str:
    entries = [entry for entry in path.split(os.pathsep) if entry]
    seen = set(entries)
    for entry in _demo_preview_extra_path_entries():
        if entry and entry not in seen:
            entries.append(entry)
            seen.add(entry)
    if not entries:
        return os.defpath
    return os.pathsep.join(entries)


def _build_demo_preview_env() -> dict[str, str]:
    allowed_keys = {
        "CUDA_VISIBLE_DEVICES",
        "DYLD_LIBRARY_PATH",
        "HF_HOME",
        "HOME",
        "LANG",
        "LC_ALL",
        "LD_LIBRARY_PATH",
        "PATH",
        "PYTORCH_ENABLE_MPS_FALLBACK",
        "PYTORCH_MPS_FALLBACK_POLICY",
        "PYTORCH_MPS_HIGH_WATERMARK_RATIO",
        "PYTHONUNBUFFERED",
        "TEMP",
        "TERM",
        "TMP",
        "TMPDIR",
        "TORCH_HOME",
        "TRANSFORMERS_CACHE",
        "UV_CACHE_DIR",
        "UV_BIN",
        "XDG_CACHE_HOME",
    }
    env = {key: value for key, value in os.environ.items() if key in allowed_keys and value}
    env["PATH"] = _build_demo_preview_path(env.get("PATH", os.defpath))
    env.setdefault("PYTHONUNBUFFERED", "1")
    return env


def _demo_preview_preexec_fn(settings: Settings) -> Callable[[], None] | None:
    if not settings.cv_demo_subprocess_sandbox or os.name != "posix":
        return None

    def _apply_limits() -> None:
        os.setsid()
        try:
            import resource
        except ImportError:  # pragma: no cover - resource is POSIX-only.
            return

        if settings.cv_demo_max_cpu_seconds > 0:
            resource.setrlimit(
                resource.RLIMIT_CPU,
                (
                    settings.cv_demo_max_cpu_seconds,
                    settings.cv_demo_max_cpu_seconds,
                ),
            )
        if settings.cv_demo_max_output_bytes > 0:
            resource.setrlimit(
                resource.RLIMIT_FSIZE,
                (
                    settings.cv_demo_max_output_bytes,
                    settings.cv_demo_max_output_bytes,
                ),
            )
        resource.setrlimit(
            resource.RLIMIT_NOFILE,
            (
                settings.cv_demo_max_open_files,
                settings.cv_demo_max_open_files,
            ),
        )

    return _apply_limits


def _resolve_uv_command() -> str:
    configured = os.environ.get("UV_BIN")
    if configured and Path(configured).expanduser().is_file():
        return str(Path(configured).expanduser())
    discovered = shutil.which("uv")
    if discovered:
        return discovered
    home = Path.home()
    candidates = (
        home / ".local/bin/uv",
        Path("/opt/homebrew/bin/uv"),
        Path("/usr/local/bin/uv"),
    )
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return "uv"


def _run_demo_preview_inference(
    *,
    settings: Settings,
    input_path: Path,
    output_path: Path,
) -> None:
    _, training_root, script_path, config_path, checkpoint_path = _validate_demo_preview_inputs(
        settings,
        startup=False,
    )
    command = [
        _resolve_uv_command(),
        "run",
        "--no-sync",
        "python",
        str(script_path.relative_to(training_root)),
        "--config",
        str(config_path),
        "--checkpoint",
        str(checkpoint_path),
        "--input",
        str(input_path),
        "--output",
        str(output_path),
        "--sample-fps",
        str(settings.effective_cv_demo_sample_fps()),
    ]
    subprocess_kwargs: dict[str, Any] = {
        "cwd": training_root,
        "env": _build_demo_preview_env(),
        "capture_output": True,
        "text": True,
        "check": False,
        "timeout": settings.cv_demo_timeout_seconds,
    }
    preexec_fn = _demo_preview_preexec_fn(settings)
    if preexec_fn is not None:
        subprocess_kwargs["preexec_fn"] = preexec_fn
    try:
        completed = subprocess.run(
            command,
            **subprocess_kwargs,
        )
    except FileNotFoundError as exc:
        raise ServiceUnavailableError(
            "uv is not available; cannot run local demo preview",
            code=ErrorCode.DEMO_PREVIEW_FAILED,
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ServiceUnavailableError(
            "Local demo preview timed out",
            code=ErrorCode.DEMO_PREVIEW_FAILED,
            details={"timeout_seconds": settings.cv_demo_timeout_seconds},
        ) from exc
    if completed.returncode != 0:
        raise ServiceUnavailableError(
            "Local demo preview inference failed",
            code=ErrorCode.DEMO_PREVIEW_FAILED,
            details={"returncode": completed.returncode},
        )


async def render_demo_preview_artifact(
    *,
    video_id: uuid.UUID,
    video_status: VideoStatus,
    storage_key_mezzanine: str | None,
    download_file: DownloadArtifactFile,
    settings: Settings,
    on_started: PreviewStartedHook | None = None,
) -> DemoPreviewArtifact:
    _require_demo_preview_enabled(settings)
    validate_demo_preview_runtime(settings, startup=False)
    if video_status is not VideoStatus.PROCESSED:
        raise ConflictError(
            "Video must finish processing before a demo preview can be generated",
            code=ErrorCode.INVALID_VIDEO_STATE,
            details={"current_status": video_status.value},
        )
    if not storage_key_mezzanine:
        raise ConflictError(
            "Processed video is missing its mezzanine artifact",
            code=ErrorCode.INVALID_VIDEO_STATE,
        )
    final_output = _resolved_demo_path(settings, video_id)
    final_output.parent.mkdir(parents=True, exist_ok=True)
    _repair_demo_preview_permissions(settings, video_id=video_id)
    machine_lock = _acquire_lock(
        lock_path=_resolved_demo_machine_lock_path(settings),
        stale_after_seconds=settings.cv_demo_timeout_seconds + 60,
        conflict_message="Another local demo preview is already running on this machine",
        conflict_code=ErrorCode.DEMO_PREVIEW_MACHINE_BUSY,
        details={"scope": "machine"},
    )
    run_lock = None
    try:
        run_lock = _acquire_lock(
            lock_path=_resolved_demo_run_lock_path(settings, video_id),
            stale_after_seconds=settings.cv_demo_timeout_seconds + 60,
            conflict_message="A local demo preview is already being generated for this video",
            conflict_code=ErrorCode.DEMO_PREVIEW_IN_PROGRESS,
            details={"video_id": str(video_id)},
        )
        if on_started is not None:
            started = on_started()
            if isawaitable(started):
                await started
        with tempfile.TemporaryDirectory(
            prefix=f"nbu-demo-preview-{video_id}-",
            dir=_resolved_demo_temp_parent(settings),
        ) as temp_dir_raw:
            temp_dir = Path(temp_dir_raw)
            input_path = temp_dir / "input.mp4"
            staged_output = temp_dir / "demo-preview.annotated.mp4"
            await download_file(storage_key_mezzanine, str(input_path))
            await to_thread.run_sync(
                lambda: _run_demo_preview_inference(
                    settings=settings,
                    input_path=input_path,
                    output_path=staged_output,
                )
            )
            if not staged_output.is_file() or staged_output.stat().st_size <= 0:
                raise ServiceUnavailableError(
                    "Local demo preview did not produce an output artifact",
                    code=ErrorCode.DEMO_PREVIEW_FAILED,
                )
            staged_output.replace(final_output)
            _chmod_best_effort(final_output.parent, 0o700)
            final_output.chmod(0o600)
    finally:
        release_demo_preview_lock(run_lock)
        release_demo_preview_lock(machine_lock)
    artifact = resolve_demo_preview(settings=settings, video_id=video_id)
    if artifact is None:
        raise ServiceUnavailableError(
            "Local demo preview artifact could not be resolved after generation",
            code=ErrorCode.DEMO_PREVIEW_FAILED,
        )
    return artifact
