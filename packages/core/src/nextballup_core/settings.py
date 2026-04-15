from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    The .env file is read when present; explicit env vars always win.
    Only Phase 1 fields are required. Other env vars (S3, CV, etc.) are
    intentionally ignored here and will be added by their own modules.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- App ----
    app_env: Literal["development", "test", "staging", "production"] = "development"
    app_debug: bool = True
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    frontend_app_url: str = "http://localhost:3000"
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )

    # ---- Database ----
    database_url: str = "postgresql+asyncpg://nextballup:nextballup_dev@localhost:5432/nextballup"
    database_url_sync: str = "postgresql://nextballup:nextballup_dev@localhost:5432/nextballup"

    # ---- Distributed Dependencies ----
    redis_url: str | None = None
    s3_endpoint_url: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_region: str = "us-east-1"
    s3_bucket_raw: str | None = None

    # ---- Auth / JWT ----
    jwt_algorithm: Literal["RS256"] = "RS256"
    jwt_private_key_path: Path = Path("./keys/jwt-private.pem")
    jwt_public_key_path: Path = Path("./keys/jwt-public.pem")
    # In-memory key overrides; populated in tests so we never touch disk.
    jwt_private_key: str | None = None
    jwt_public_key: str | None = None
    jwt_issuer: str = "nextballup"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7
    # Playback tokens are deliberately short-lived; the presigned URL TTL is
    # the real access boundary, the JWT is the forward-compat hook for a
    # future signed-token verification endpoint.
    playback_token_expire_seconds: int = 3600
    playback_url_expires_seconds: int = 3600
    playback_token_audience: str = "video:playback"
    auth_rate_limit_attempts: int = 5
    auth_rate_limit_window_seconds: int = 60
    team_join_rate_limit_attempts: int = 20
    team_join_rate_limit_window_seconds: int = 60
    video_upload_rate_limit_attempts: int = 30
    video_upload_rate_limit_window_seconds: int = 3600

    # ---- Uploads ----
    max_upload_size_bytes: int = 10_737_418_240  # 10 GB ceiling per API_SPEC.
    upload_multipart_threshold_bytes: int = 1_073_741_824  # 1 GB switchpoint.
    upload_multipart_part_size_bytes: int = 100 * 1024 * 1024  # 100 MB parts.
    upload_url_expires_seconds: int = 3600
    allowed_video_content_types: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["video/mp4", "video/quicktime", "video/x-matroska"]
    )

    # ---- Worker / Celery ----
    # Broker/backend are optional so tests and the API process don't require a
    # running Redis. When unset, the worker refuses to start and beat is a no-op.
    celery_broker_url: str | None = None
    celery_result_backend: str | None = None
    celery_task_default_queue: str = "nextballup.default"
    celery_transcode_queue: str = "nextballup.transcode"
    celery_maintenance_queue: str = "nextballup.maintenance"
    # Seconds between in-task heartbeat writes while a stage is running.
    worker_heartbeat_interval_seconds: int = 15
    # Jobs whose heartbeat is older than this are considered abandoned and swept.
    worker_stale_heartbeat_seconds: int = 120
    # Max Celery autoretries for transient processing errors.
    worker_job_max_retries: int = 3
    # Backoff base seconds (exponentially multiplied by retry count).
    worker_job_retry_backoff_seconds: int = 30
    # Grace window beyond upload_expires_at before an upload is abandoned.
    worker_abandoned_upload_grace_seconds: int = 300
    # Beat tick intervals.
    worker_dispatch_interval_seconds: int = 10
    worker_cleanup_interval_seconds: int = 300

    # ---- Cookies ----
    cookie_access_name: str = "nbu_access_token"
    cookie_refresh_name: str = "nbu_refresh_token"
    cookie_secure: bool = False  # True in staging/production
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    cookie_domain: str | None = None
    trusted_proxy_ips: Annotated[list[str], NoDecode] = Field(default_factory=list)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("trusted_proxy_ips", mode="before")
    @classmethod
    def _split_proxy_ips(cls, value: object) -> object:
        if isinstance(value, str):
            return [ip.strip() for ip in value.split(",") if ip.strip()]
        return value

    @field_validator("allowed_video_content_types", mode="before")
    @classmethod
    def _split_content_types(cls, value: object) -> object:
        if isinstance(value, str):
            return [ct.strip().lower() for ct in value.split(",") if ct.strip()]
        return value

    def storage_configured(self) -> bool:
        return all(
            (
                self.s3_endpoint_url,
                self.s3_access_key,
                self.s3_secret_key,
                self.s3_bucket_raw,
            )
        )

    def load_jwt_private_key(self) -> str:
        if self.jwt_private_key:
            return self.jwt_private_key
        return self.jwt_private_key_path.read_text(encoding="utf-8")

    def load_jwt_public_key(self) -> str:
        if self.jwt_public_key:
            return self.jwt_public_key
        return self.jwt_public_key_path.read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reload_settings() -> Settings:
    """Drop the cached settings instance — tests use this after monkeypatching env."""
    get_settings.cache_clear()
    return get_settings()
