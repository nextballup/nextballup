from __future__ import annotations

from functools import lru_cache
from ipaddress import ip_address, ip_network
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import quote, urlparse, urlsplit, urlunsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _normalize_async_postgres_url(value: str | None) -> str | None:
    if value is None:
        return None
    if value.startswith("postgres://"):
        return f"postgresql+asyncpg://{value.removeprefix('postgres://')}"
    if value.startswith("postgresql://"):
        return f"postgresql+asyncpg://{value.removeprefix('postgresql://')}"
    return value


def _database_url_with_credentials(database_url: str, *, username: str, password: str) -> str:
    parsed = urlsplit(_normalize_async_postgres_url(database_url) or database_url)
    if parsed.hostname is None:
        raise ValueError("DATABASE_URL must include a hostname")
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    netloc = f"{quote(username, safe='')}:{quote(password, safe='')}@{host}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


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
    database_runtime_username: str = "nextballup_app"
    database_runtime_password: str | None = None

    # ---- Distributed Dependencies ----
    redis_url: str | None = None
    s3_endpoint_url: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_region: str = "us-east-1"
    s3_bucket_raw: str | None = None

    # ---- Observability ----
    # Disabled by default. When enabled, the Prometheus text endpoint exposes
    # aggregate operational counters only and requires this shared secret.
    observability_metrics_enabled: bool = False
    observability_metrics_token: str | None = None
    observability_worker_metrics_enabled: bool = False
    observability_worker_metrics_host: str = "127.0.0.1"
    observability_worker_metrics_port: int = Field(default=9108, ge=1024, le=65535)
    observability_worker_metrics_port_span: int = Field(default=16, ge=1, le=256)

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
    demo_preview_url_expires_seconds: int = Field(default=7200, ge=60, le=86400)
    playback_token_audience: str = "video:playback"
    auth_rate_limit_attempts: int = 5
    auth_rate_limit_window_seconds: int = 60

    # ---- Registration gate ----
    # Public/root domain stays marketing/waitlist by default; the app
    # registration endpoint must not expose open signup outside an explicit
    # operator decision. Modes:
    #   * open         — anyone may register (development default)
    #   * invite_only  — caller must present a code from
    #                    `registration_invite_codes`
    #   * allowlist    — caller's email must appear in
    #                    `registration_email_allowlist`
    #   * disabled     — registration is rejected outright (public/marketing)
    # See docs/soc2/DEPLOYMENT_CHANNELS.md and
    # docs/soc2/PRODUCTION_READINESS.md.
    registration_mode: Literal["open", "invite_only", "allowlist", "disabled"] = "open"
    # Plaintext invite codes the operator hands out for invite_only beta. Each
    # code is shared (no per-user binding); rotate by replacing the env value.
    # Codes are constant-time-compared so the failed-match path can't be used
    # as a timing oracle.
    registration_invite_codes: Annotated[list[str], NoDecode] = Field(default_factory=list)
    # Lowercased email addresses allowed to register when mode=allowlist.
    registration_email_allowlist: Annotated[list[str], NoDecode] = Field(default_factory=list)
    rate_limit_fail_closed: bool | None = None
    team_join_rate_limit_attempts: int = 20
    team_join_rate_limit_window_seconds: int = 60
    video_upload_rate_limit_attempts: int = 30
    video_upload_rate_limit_window_seconds: int = 3600
    video_demo_preview_rate_limit_attempts: int = 6
    video_demo_preview_rate_limit_window_seconds: int = 600
    require_verified_email_for_sensitive_actions: bool | None = None

    # ---- Email verification ----
    # Token TTL — long enough to survive a busy mailbox, short enough that a
    # leaked link cannot be replayed across days. One-time use enforced by
    # the `used_at` column.
    email_verification_token_ttl_minutes: int = Field(default=60, ge=5, le=1440)
    # Per-user request rate limit (Redis-backed when configured).
    email_verification_request_rate_attempts: int = Field(default=5, ge=1, le=50)
    email_verification_request_rate_window_seconds: int = Field(default=900, ge=60, le=86400)
    # Provider id for the email delivery layer. `logging` is the safe default
    # for dev/test (writes a JSON line to a configured log path); `noop` drops
    # messages silently for tests; staging/production must use a real provider
    # such as the built-in Postmark adapter or an explicitly registered
    # deployment provider.
    email_delivery_provider: str = "logging"
    email_delivery_log_path: Path | None = None
    # Sender shown in the rendered link payload (cosmetic; the real `From:`
    # comes from the delivery provider in production).
    email_verification_from_address: str = "no-reply@nextballup.invalid"
    # Verification link target — defaults to the frontend `/verify-email`
    # route. The token rides as a URL query param.
    email_verification_redirect_path: str = "/verify-email"
    postmark_server_token: str | None = None
    postmark_message_stream: str = "outbound"
    postmark_send_timeout_seconds: float = Field(default=10.0, gt=0.0, le=60.0)

    # ---- Password reset ----
    password_reset_token_ttl_minutes: int = Field(default=30, ge=5, le=240)
    password_reset_request_rate_attempts: int = Field(default=5, ge=1, le=50)
    password_reset_request_rate_window_seconds: int = Field(default=900, ge=60, le=86400)
    password_reset_confirm_rate_attempts: int = Field(default=10, ge=1, le=100)
    password_reset_confirm_rate_window_seconds: int = Field(default=900, ge=60, le=86400)
    password_reset_redirect_path: str = "/reset-password"

    # ---- MFA / TOTP ----
    # Secret used to derive the AES-GCM key that encrypts TOTP shared secrets
    # at rest. Must be set in staging/production; dev/test fall back to a
    # deterministic local value derived from the JWT private key.
    mfa_secret_key: str | None = None
    mfa_totp_issuer: str = "NextBallUp"
    mfa_totp_step_seconds: int = Field(default=30, ge=15, le=120)
    mfa_totp_digits: int = Field(default=6, ge=6, le=8)
    # Number of recovery codes minted at confirm time.
    mfa_recovery_code_count: int = Field(default=10, ge=4, le=20)

    # ---- Billing ----
    # Stripe-style provider hook. `stub` is local/dev only. Alpha/staging may
    # use `billing_disabled` to keep checkout fail-closed while the private CV
    # POC is not charging users. Production/beta must register a real provider.
    billing_provider: str = "stub"
    # Default plan code assigned to a freshly-provisioned billing account when
    # no subscription has been chosen yet.
    billing_default_plan_code: str = "free"

    # ---- Uploads ----
    max_upload_size_bytes: int = 10_737_418_240  # 10 GB ceiling per API_SPEC.
    # Floor that's well below a plausible game clip but far above a 1-byte
    # probe. Blocks the obvious abuse pattern of "mint thousands of presigned
    # URLs for tiny files to churn storage" without affecting real uploads.
    min_upload_size_bytes: int = 1_048_576  # 1 MB
    upload_multipart_threshold_bytes: int = 1_073_741_824  # 1 GB switchpoint.
    upload_multipart_part_size_bytes: int = 100 * 1024 * 1024  # 100 MB parts.
    upload_url_expires_seconds: int = 3600
    upload_presigned_put_checksum_header: bool = True
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
    require_privacy_consent_for_sensitive_uploads: bool | None = None
    raw_video_retention_days: int = Field(default=365, ge=1, le=3650)
    raw_video_retention_cleanup_batch_size: int = Field(default=100, ge=1, le=1000)
    csp_report_retention_days: int = Field(default=90, ge=1, le=3650)

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
    celery_demo_preview_queue: str = "nextballup.demo_preview"
    cv_pipeline_enabled: bool = False
    cv_require_model_artifacts: bool = True
    cv_model_artifact_required_stages: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["detection", "tracking", "court_mapping", "events"]
    )
    # Seconds between in-task heartbeat writes while a stage is running.
    worker_heartbeat_interval_seconds: int = 15
    # Jobs whose heartbeat is older than this are considered abandoned and swept.
    worker_stale_heartbeat_seconds: int = 300
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
    worker_ffmpeg_threads: int = Field(default=2, ge=1, le=16)
    worker_media_temp_dir: Path | None = None
    worker_playback_max_width: int = Field(default=1280, ge=0, le=7680)
    worker_playback_max_fps: int = Field(default=30, ge=0, le=240)
    worker_playback_crf: int = Field(default=26, ge=0, le=51)
    worker_playback_preset: str = "veryfast"
    worker_playback_remux_enabled: bool = True
    worker_playback_remux_max_width: int = Field(default=1920, ge=0, le=7680)
    worker_playback_remux_max_fps: int = Field(default=60, ge=0, le=240)
    worker_media_subprocess_sandbox: bool = True
    worker_media_max_cpu_seconds: int = Field(default=7_200, ge=0)
    worker_media_max_output_bytes: int = Field(default=100 * 1024 * 1024 * 1024, ge=0)
    worker_media_max_open_files: int = Field(default=64, ge=16, le=1024)
    worker_media_container_sandbox_enabled: bool = False
    worker_media_container_image: str = "jrottenberg/ffmpeg:6.1-alpine"
    worker_media_container_binary: str = "ffmpeg"
    worker_media_container_runtime: str = "docker"
    worker_media_container_user: str = "65532:65532"
    worker_media_container_cpus: float = Field(default=2.0, gt=0.0, le=32.0)
    worker_media_container_memory: str = "8g"
    worker_media_container_pids_limit: int = Field(default=128, ge=16, le=4096)
    worker_media_container_tmpfs_size: str = "512m"
    # Grace window beyond upload_expires_at before an upload is abandoned.
    worker_abandoned_upload_grace_seconds: int = 300
    # Beat tick intervals.
    worker_dispatch_interval_seconds: int = 10
    worker_cleanup_interval_seconds: int = 300

    # ---- Local CV demo preview bridge ----
    # Dev/test-only bridge into the sibling training repo. This is explicitly
    # not a production inference path; it shells out to the local training
    # workspace to render an annotated preview for internal evaluation.
    cv_demo_preview_enabled: bool = False
    cv_demo_preview_root: Path = Path("./local_artifacts/demo_previews")
    cv_demo_training_repo_root: Path = Path("../nextballup-vision-training")
    cv_demo_config_path: Path = Path(
        "../nextballup-vision-training/configs/experiments/basketball/detect/"
        "rfdetr_demo_local_overfit_v1.yaml"
    )
    cv_demo_checkpoint_path: Path = Path(
        "../nextballup-vision-training/runs/bb_detect_rfdetr_demo_local_overfit_v1/"
        "demo-01/checkpoints/checkpoint_best_total.pth"
    )
    cv_alpha_detector_preview_enabled: bool = False
    cv_alpha_detector_config_path: Path = Path(
        "../nextballup-vision-training/configs/experiments/basketball/detect/"
        "rfdetr_ebard_alpha_demo_v1.yaml"
    )
    cv_alpha_detector_checkpoint_path: Path = Path(
        "../nextballup-vision-training/local_artifacts/runs_alpha/runs/"
        "bb_detect_rfdetr_ebard_alpha_demo_v1/"
        "bb_detect_rfdetr_ebard_alpha_demo_v1-20260505T190931Z/"
        "checkpoints/checkpoint_best_total.pth"
    )
    cv_alpha_detector_eval_report_path: Path = Path(
        "../nextballup-vision-training/local_artifacts/runs_alpha/runs/"
        "bb_detect_rfdetr_ebard_alpha_demo_v1/"
        "bb_detect_rfdetr_ebard_alpha_demo_v1-20260505T190931Z/eval_report.json"
    )
    cv_demo_sample_fps: float = Field(default=2.0, gt=0.0, le=30.0)
    cv_demo_max_sample_fps: float = Field(default=4.0, gt=0.0, le=24.0)
    cv_demo_timeout_seconds: int = Field(default=1800, ge=30, le=7200)
    cv_demo_retention_seconds: int = Field(default=259200, ge=3600, le=2_592_000)
    cv_demo_subprocess_sandbox: bool = True
    cv_demo_max_cpu_seconds: int = Field(default=0, ge=0)
    cv_demo_max_output_bytes: int = Field(default=0, ge=0)
    cv_demo_max_open_files: int = Field(default=256, ge=64, le=4096)
    cv_alpha_candidate_tags_enabled: bool = False
    cv_alpha_candidate_script_path: Path = Path(
        "../nextballup-vision-training/scripts/local_bard_candidate_infer.py"
    )
    cv_alpha_candidate_trainer_sidecar_path: Path = Path(
        "../nextballup-vision-training/local_artifacts/trainer/"
        "bard_qwen25vl3b_train_steps75_limit120_ga1_skip10_4fps8f112px_balanced_v1/"
        "trainer_metadata.json"
    )
    cv_alpha_candidate_adapter_path: Path = Path(
        "../nextballup-vision-training/local_artifacts/trainer/"
        "bard_qwen25vl3b_train_steps75_limit120_ga1_skip10_4fps8f112px_balanced_v1/"
        "lora_adapter"
    )
    cv_alpha_candidate_window_seconds: float = Field(default=8.0, gt=0.0, le=30.0)
    cv_alpha_candidate_stride_seconds: float = Field(default=16.0, gt=0.0, le=120.0)
    cv_alpha_candidate_max_windows: int = Field(default=40, ge=1, le=400)
    cv_alpha_candidate_timeout_seconds: int = Field(default=7200, ge=30, le=21600)

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
            "/api/v1/auth/password/forgot",
            "/api/v1/auth/password/reset",
            "/api/v1/_csp-report",
            # Marketing pilot-interest is intentionally unauthenticated and
            # rate-limited per IP; there is no logged-in session to bind a
            # CSRF token to from the public marketing site.
            "/api/v1/pilot-interest",
        ]
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("cors_origins")
    @classmethod
    def _validate_cors_origins(cls, value: list[str]) -> list[str]:
        for origin in value:
            parsed = urlparse(origin)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("CORS_ORIGINS entries must be absolute http/https origins")
        return value

    @field_validator("trusted_proxy_ips", mode="before")
    @classmethod
    def _split_proxy_ips(cls, value: object) -> object:
        if isinstance(value, str):
            if value.strip() in {"", "[]"}:
                return []
            return [ip.strip() for ip in value.split(",") if ip.strip()]
        return value

    @field_validator("trusted_proxy_ips")
    @classmethod
    def _validate_proxy_ips(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for entry in value:
            try:
                normalized.append(str(ip_address(entry)))
                continue
            except ValueError:
                pass
            try:
                normalized.append(str(ip_network(entry, strict=False)))
            except ValueError as exc:
                raise ValueError(
                    "TRUSTED_PROXY_IPS entries must be IP addresses or networks"
                ) from exc
        return normalized

    @field_validator("observability_worker_metrics_host")
    @classmethod
    def _validate_worker_metrics_host(cls, value: str) -> str:
        try:
            parsed = ip_address(value)
        except ValueError as exc:
            raise ValueError("WORKER_METRICS_HOST must be an IP address") from exc
        if not parsed.is_loopback:
            raise ValueError("WORKER_METRICS_HOST must be loopback-only")
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

    @field_validator("registration_invite_codes", mode="before")
    @classmethod
    def _split_registration_invite_codes(cls, value: object) -> object:
        if isinstance(value, str):
            return [c.strip() for c in value.split(",") if c.strip()]
        return value

    @field_validator("registration_invite_codes")
    @classmethod
    def _validate_registration_invite_codes(cls, value: list[str]) -> list[str]:
        for code in value:
            if len(code) < 8:
                raise ValueError("REGISTRATION_INVITE_CODES entries must be at least 8 characters")
        return value

    @field_validator("registration_email_allowlist", mode="before")
    @classmethod
    def _split_registration_email_allowlist(cls, value: object) -> object:
        if isinstance(value, str):
            return [e.strip().lower() for e in value.split(",") if e.strip()]
        if isinstance(value, list):
            return [str(e).strip().lower() for e in value if str(e).strip()]
        return value

    @field_validator("database_url", "database_url_runtime", mode="before")
    @classmethod
    def _normalize_database_urls(cls, value: object) -> object:
        if isinstance(value, str):
            return _normalize_async_postgres_url(value)
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

    def local_demo_preview_enabled(self) -> bool:
        """Whether the internal demo-preview bridge may be used at runtime."""
        dev_demo_enabled = self.cv_demo_preview_enabled and self.app_env in (
            "development",
            "test",
        )
        return dev_demo_enabled or self.alpha_detector_preview_enabled()

    def alpha_detector_preview_enabled(self) -> bool:
        """Whether the restricted alpha detector preview may be used."""
        return self.cv_alpha_detector_preview_enabled and self.app_env in (
            "development",
            "test",
            "staging",
        )

    def alpha_candidate_tags_enabled(self) -> bool:
        """Whether restricted alpha candidate tagging may run locally."""
        return self.cv_alpha_candidate_tags_enabled and self.alpha_detector_preview_enabled()

    def repo_root(self) -> Path:
        """Resolve the repository root independent of the process CWD."""
        return Path(__file__).resolve().parents[4]

    def resolve_repo_relative_path(self, path: Path) -> Path:
        """Resolve a path against the repo root when it is not absolute.

        This keeps the API and worker aligned even if they are launched from
        different working directories.
        """
        expanded = path.expanduser()
        if expanded.is_absolute():
            return expanded.resolve()
        return (self.repo_root() / expanded).resolve()

    def runtime_database_url(self) -> str:
        """URL used for request-time sessions. Falls back to the owner URL
        when no separate runtime role is configured (dev convenience)."""
        if self.database_url_runtime:
            return self.database_url_runtime
        if self.database_runtime_password:
            return _database_url_with_credentials(
                self.database_url,
                username=self.database_runtime_username,
                password=self.database_runtime_password,
            )
        if self.app_env in ("staging", "production"):
            raise RuntimeError(
                "DATABASE_URL_RUNTIME or DATABASE_RUNTIME_PASSWORD must be configured "
                "in staging/production (fail-closed; API/worker must use the "
                "non-owner runtime role)"
            )
        return self.database_url

    def should_rate_limit_fail_closed(self) -> bool:
        """Whether abuse controls should block when Redis is unavailable."""
        if self.rate_limit_fail_closed is not None:
            return self.rate_limit_fail_closed
        return self.app_env in ("staging", "production")

    def should_require_sensitive_upload_consent(self) -> bool:
        """Whether youth/K-12 uploads require a ledger-backed consent record."""
        if self.require_privacy_consent_for_sensitive_uploads is not None:
            return self.require_privacy_consent_for_sensitive_uploads
        return self.app_env != "test"

    def is_registration_invite_required(self) -> bool:
        return self.registration_mode == "invite_only"

    def is_registration_disabled(self) -> bool:
        return self.registration_mode == "disabled"

    def is_registration_email_allowlisted(self, email: str) -> bool:
        if self.registration_mode != "allowlist":
            return True
        return email.strip().lower() in set(self.registration_email_allowlist)

    def is_valid_registration_invite_code(self, code: str | None) -> bool:
        if not self.is_registration_invite_required():
            return True
        if not code:
            return False
        submitted = code.strip()
        # Constant-time compare against every configured code so the failed
        # path's wall time does not vary with the submitted prefix.
        import hmac

        match = False
        for known in self.registration_invite_codes:
            if hmac.compare_digest(submitted, known):
                match = True
        return match

    def should_require_verified_email_for_sensitive_actions(self) -> bool:
        """Whether coach/admin state-changing actions require verified email."""
        if self.require_verified_email_for_sensitive_actions is not None:
            return self.require_verified_email_for_sensitive_actions
        return self.app_env in ("staging", "production")

    def effective_cv_demo_sample_fps(self) -> float:
        """Bound the local demo bridge to a practical preview sample rate."""
        return min(self.cv_demo_sample_fps, self.cv_demo_max_sample_fps)

    @field_validator("cv_model_artifact_required_stages", mode="before")
    @classmethod
    def _split_cv_model_artifact_required_stages(cls, value: object) -> object:
        if isinstance(value, str):
            return [stage.strip() for stage in value.split(",") if stage.strip()]
        return value

    def cookie_name(self, base: str) -> str:
        """Resolve the wire name for a cookie, applying `__Host-` when enabled."""
        if self.cookie_host_prefix:
            # __Host- requires Secure + Path=/ + no Domain attribute. The
            # caller is responsible for honoring those constraints.
            return f"__Host-{base}"
        return base

    def effective_mfa_secret_key(self) -> str:
        """Resolve the MFA-cipher master key.

        Production / staging require an explicit `MFA_SECRET_KEY`. The
        startup validator refuses to boot otherwise (see main.py). In
        dev/test we derive a stable per-process value from the JWT private
        key so re-runs don't break already-enrolled fixtures.
        """
        if self.mfa_secret_key:
            return self.mfa_secret_key
        return f"mfa-dev-fallback::{self.load_jwt_private_key()[:64]}"

    def email_verification_link(self, token: str) -> str:
        """Build the verification URL the user clicks from email.

        Same-origin frontend (`apps/web`) consumes the token from the query
        param and POSTs it to `/api/v1/auth/email/verify/confirm`.
        """
        base = self.frontend_app_url.rstrip("/")
        path = self.email_verification_redirect_path or "/verify-email"
        if not path.startswith("/"):
            path = f"/{path}"
        return f"{base}{path}?token={token}"

    def password_reset_link(self, token: str) -> str:
        base = self.frontend_app_url.rstrip("/")
        path = self.password_reset_redirect_path or "/reset-password"
        if not path.startswith("/"):
            path = f"/{path}"
        return f"{base}{path}?token={token}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reload_settings() -> Settings:
    """Drop the cached settings instance — tests use this after monkeypatching env."""
    get_settings.cache_clear()
    return get_settings()
