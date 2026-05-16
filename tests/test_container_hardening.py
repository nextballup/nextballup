from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_backend_image_defines_unprivileged_runtime_user() -> None:
    dockerfile = _read("Dockerfile.backend")

    assert "gosu" in dockerfile
    assert "groupadd --gid 10001 nextballup" in dockerfile
    assert "useradd --uid 10001 --gid nextballup" in dockerfile
    assert "--no-create-home nextballup" in dockerfile
    assert "chown -R nextballup:nextballup /app /var/data /tmp/nextballup" in dockerfile
    assert 'CMD ["sh", "scripts/render_start_api.sh"]' in dockerfile


def test_render_start_commands_drop_root_privileges() -> None:
    runtime_helper = _read("scripts/render_runtime.sh")

    assert 'APP_RUN_USER="nextballup"' in runtime_helper
    assert 'APP_RUN_GROUP="nextballup"' in runtime_helper
    assert "render_assert_safe_runtime_dir" in runtime_helper
    assert "/var/data/nextballup-transcode" in runtime_helper
    assert "/tmp/nextballup" in runtime_helper

    for script in (
        "scripts/render_start_api.sh",
        "scripts/render_start_worker.sh",
        "scripts/render_start_beat.sh",
    ):
        body = _read(script)
        assert ". scripts/render_runtime.sh" in body
        assert "render_prepare_runtime_dirs" in body
        assert "render_drop_exec" in body


def test_render_runtime_refuses_unsafe_writable_dirs() -> None:
    env = {
        **os.environ,
        "TMPDIR": "/tmp/nextballup",
        "WORKER_MEDIA_TEMP_DIR": "/",
    }

    result = subprocess.run(
        ["sh", "-c", ". scripts/render_runtime.sh; render_prepare_runtime_dirs"],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 70
    assert "Refusing to prepare unsafe runtime directory: /" in result.stderr


def test_worker_start_prepares_render_scratch_before_drop() -> None:
    body = _read("scripts/render_start_worker.sh")

    assert body.index("render_prepare_runtime_dirs") < body.index("render_drop_exec celery")
    assert (
        "--queues=nextballup.default,nextballup.transcode,nextballup.maintenance,nextballup.cpu"
        in body
    )


def test_predeploy_runs_without_root_runtime_privileges() -> None:
    body = _read("scripts/render_predeploy.sh")

    assert ". scripts/render_runtime.sh" in body
    assert 'gosu "${APP_RUN_USER}:${APP_RUN_GROUP}"' in body
    assert "alembic upgrade head && python scripts/configure_runtime_db_role.py" in body
