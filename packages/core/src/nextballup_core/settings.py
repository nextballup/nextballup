from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    The .env file is read when present; explicit env vars always win.
    The settings model intentionally stays focused on the currently runnable
    platform surface; future CV-heavy env vars live in their own modules so a
    plain backend setup does not pull in those dependencies or assumptions.
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
    # Localhost by default (CLAUDE.md: "API/frontend should bind to 127.0.0.1").
    # Deployments that need LAN binding override via APP_HOST.
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    frontend_app_url: str = "http://localhost:3000"
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )

    # ---- Database ----
    # `database_url` is the *owner* connection used for migrations and startup.
    # `database_url_runtime` is the application's CRUD-only role used for
    # request handling; FORCE ROW LEVEL SECURITY is bypassed by the table
    # owner, so the runtime role must be a non-owner for RLS to actually
    # filter rows. When unset the owner URL is used (dev convenience).
    database_url: str = "postgresql+asyncpg://nextballup:nextballup_dev@localhost:5432/nextballup"
    database_url_sync: str = "postgresql://nextballup:nextballup_dev@localhost:5432/nextballup"
    database_url_runtime: str | None = None

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
    # Playback tokens carry `sv` (session version) + `role`; the verify
    # endpoint ties them to the live user record so a logout (which bumps
    # session_version) invalidates any playback credential still in flight.
    # The default TTL is short because the intended flow is: the client mints
    # fresh tokens via the video detail endpoint whenever the previous one
    # expires. Presigned URLs are still the object-level boundary; this token
    # is the *session-aware* boundary.
    playback_token_expire_seconds: int = 120
    playback_url_expires_seconds: int = 600
    playback_token_audience: str = "video:playback"
    auth_rate_limit_attempts: int = 5
    auth_rate_limit_window_seconds: int = 60
    team_join_rate_limit_attempts: int = 20
    team_join_rate_limit_window_seconds: int = 60
    video_upload_rate_limit_attempts: int = 30
    video_upload_rate_limit_window_seconds: int = 3600

    # ---- Uploads ----
    max_upload_size_bytes: int = 10_737_418_240  # 10 GB ceiling per API_SPEC.
    # Floor that's well below a plausible game clip but far above a 1-byte
    # probe. Blocks the obvious abuse pattern of "mint thousands of presigned
    # URLs for tiny files to churn storage" without affecting real uploads.
    min_upload_size_bytes: int = 1_048_576  # 1 MB
    upload_multipart_threshold_bytes: int = 1_073_741_824  # 1 GB switchpoint.
    upload_multipart_part_size_bytes: int = 100 * 1024 * 1024  # 100 MB parts.
    upload_url_expires_seconds: int = 3600
    allowed_video_content_types: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["video/mp4", "video/quicktime", "video/x-matroska"]
    )
    # Declared content-type → allowed filename extensions. Blocks trivially
    # mismatched uploads (a `.exe` declaring `video/mp4`). Not a content
    # oracle — the real file still gets transcoded — but raises the bar
    # against casual content-type smuggling.
    upload_content_type_extensions: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "video/mp4": [".mp4"],
            "video/quicktime": [".mov"],
            "video/x-matroska": [".mkv"],
        }
    )

    # ---- Worker / Celery ----
    # Broker/backend are optional so tests and the API process don't require a
    # running Redis. When unset, the worker refuses to start and beat is a no-op.
    celery_broker_url: str | None = None
    celery_result_backend: str | None = None
    celery_task_default_queue: str = "nextballup.default"
    celery_transcode_queue: str = "nextballup.transcode"
    celery_maintenance_queue: str = "nextballup.maintenance"
    # CV stages split onto GPU vs. CPU queues so a congested GPU pool never
    # starves a CPU-only stage (court mapping, metrics) that could otherwise
    # finish immediately. Stage → queue routing lives in the worker module.
    celery_gpu_queue: str = "nextballup.gpu"
    celery_cpu_queue: str = "nextballup.cpu"
    # Seconds between in-task heartbeat writes while a stage is running.
    worker_heartbeat_interval_seconds: int = 15
    # Jobs whose heartbeat is older than this are considered abandoned and swept.
    worker_stale_heartbeat_seconds: int = 120
    # Max Celery autoretries for transient processing errors.
    worker_job_max_retries: int = 3
    # Backoff base seconds (exponentially multiplied by retry count).
    worker_job_retry_backoff_seconds: int = 30
    # Media toolchain for browser-safe mezzanine creation. FFmpeg is the
    # production/default path; macOS can fall back to avconvert for local dev.
    worker_ffmpeg_binary: str = "ffmpeg"
    worker_ffprobe_binary: str = "ffprobe"
    worker_avconvert_binary: str = "avconvert"
    worker_transcode_timeout_seconds: int = 7_200
    # Grace window beyond upload_expires_at before an upload is abandoned.
    worker_abandoned_upload_grace_seconds: int = 300
    # Beat tick intervals.
    worker_dispatch_interval_seconds: int = 10
    worker_cleanup_interval_seconds: int = 300

    # ---- Cookies ----
    cookie_access_name: str = "nbu_access_token"
    cookie_refresh_name: str = "nbu_refresh_token"
    cookie_csrf_name: str = "nbu_csrf_token"
    # Refresh is narrower than access: the browser only needs to send the
    # refresh cookie back to the refresh endpoint, so we scope it there.
    # This intentionally cannot use `__Host-` because that prefix requires
    # Path=/ for the cookie.
    cookie_refresh_path: str = "/api/v1/auth/refresh"
    cookie_secure: bool = False  # True in staging/production
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    cookie_domain: str | None = None
    # `__Host-` prefix binds a cookie to exact-origin and requires Secure +
    # Path=/ + no Domain. We only enable it when the deployment is secure
    # enough to satisfy those constraints (secure cookies, no custom domain).
    cookie_host_prefix: bool = False
    trusted_proxy_ips: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # ---- CSRF ----
    # Stateless HMAC double-submit token. The `csrf_secret` is the HMAC key;
    # when unset a dev-only fallback is used. Staging/production fail closed
    # if this is missing (see main.py startup validation).
    csrf_secret: str | None = None
    csrf_token_ttl_seconds: int = 86400  # 24h — one browsing session
    # Request methods that require CSRF verification when cookie-authenticated.
    csrf_protected_methods: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["POST", "PUT", "PATCH", "DELETE"]
    )
    # Path prefixes that are exempt from CSRF (bootstrap auth flows). The
    # refresh endpoint is exempt because pre-session users don't have a CSRF
    # cookie yet; the refresh token itself is the credential and is rotated.
    csrf_exempt_paths: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "/api/v1/auth/login",
            "/api/v1/auth/register",
            "/api/v1/auth/refresh",
        ]
    )

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

    @field_validator("csrf_protected_methods", mode="before")
    @classmethod
    def _split_csrf_methods(cls, value: object) -> object:
        if isinstance(value, str):
            return [m.strip().upper() for m in value.split(",") if m.strip()]
        return value

    @field_validator("csrf_exempt_paths", mode="before")
    @classmethod
    def _split_csrf_exempt(cls, value: object) -> object:
        if isinstance(value, str):
            return [p.strip() for p in value.split(",") if p.strip()]
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

    def effective_csrf_secret(self) -> str:
        """Resolve the CSRF HMAC key.

        Production/staging deployments must set `CSRF_SECRET` explicitly —
        the startup validator refuses to boot otherwise. In dev/test we fall
        back to a deterministic value derived from the JWT private key so
        restarts don't invalidate every outstanding CSRF cookie.
        """
        if self.csrf_secret:
            return self.csrf_secret
        return f"csrf-dev-fallback::{self.load_jwt_private_key()[:64]}"

    def runtime_database_url(self) -> str:
        """URL used for request-time sessions. Falls back to the owner URL
        when no separate runtime role is configured (dev convenience)."""
        if self.database_url_runtime:
            return self.database_url_runtime
        if self.app_env in ("staging", "production"):
            raise RuntimeError(
                "DATABASE_URL_RUNTIME must be configured in staging/production "
                "(fail-closed; API/worker must use the non-owner runtime role)"
            )
        return self.database_url

    def cookie_name(self, base: str) -> str:
        """Resolve the wire name for a cookie, applying `__Host-` when enabled."""
        if self.cookie_host_prefix:
            # __Host- requires Secure + Path=/ + no Domain attribute. The
            # caller is responsible for honoring those constraints.
            return f"__Host-{base}"
        return base


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reload_settings() -> Settings:
    """Drop the cached settings instance — tests use this after monkeypatching env."""
    get_settings.cache_clear()
    return get_settings()
