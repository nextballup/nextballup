"""Email delivery provider abstraction.

The platform never embeds real provider credentials. Three providers ship in
the repo:

  * `logging` (default for dev/test): records each message as a JSON line
    against a configurable path, or to the `nextballup_api.email` logger
    when the path is unset. Useful for local development and CI.
  * `noop`: drops messages on the floor. Useful for unit tests that exercise
    code paths without recording anything.
  * `postmark`: sends transactional email through Postmark. Credentials are
    supplied by deployment secrets, never by source control.

Deployments can still register alternate providers (SES, SendGrid, …) behind
the same narrow interface. Registration intentionally uses explicit provider
ids so a misconfigured environment cannot instantiate arbitrary callables.

`EmailMessage` is intentionally narrow: subject + plaintext body + a single
known link. We do not render arbitrary HTML in the platform-side template
because the repo cannot store templates that have been reviewed by counsel
or designers; a richer body is the provider's job in production.
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from http import HTTPStatus
from pathlib import Path
from typing import Protocol

from nextballup_core.settings import Settings

logger = logging.getLogger("nextballup_api.email")
_LOG_LOCK = threading.Lock()


@dataclass(frozen=True)
class EmailMessage:
    """A single transactional email. Intentionally minimal — link-only."""

    to_address: str
    subject: str
    body_plaintext: str
    link_url: str
    template_id: str
    metadata: dict[str, str]


class EmailDeliveryProvider(Protocol):
    """Protocol every email delivery backend implements."""

    name: str

    def send(self, message: EmailMessage) -> None: ...


class EmailDeliveryError(RuntimeError):
    """Provider failure that is safe to surface internally without secrets."""


class NoopDeliveryProvider:
    """Drops messages silently. Used by tests that don't care about delivery."""

    name = "noop"

    def send(self, message: EmailMessage) -> None:
        return None


class LoggingDeliveryProvider:
    """Records each message as a JSON line — to a path or to the logger.

    Path-based logging is convenient for local dev: the operator can `tail`
    the file to inspect verification links. When `log_path` is None we fall
    back to the standard logger so the same provider works in container
    environments without writable disk.

    The provider is thread-safe via a module-level lock; the throughput is
    so low (transactional emails per minute) that lock contention is a
    non-issue and the simplicity beats per-instance locks.
    """

    name = "logging"

    def __init__(self, log_path: Path | None = None) -> None:
        self._log_path = log_path

    def send(self, message: EmailMessage) -> None:
        payload = {
            "ts": datetime.now(tz=UTC).isoformat(),
            "to": message.to_address,
            "subject": message.subject,
            "template_id": message.template_id,
            "link_url": message.link_url,
            "body": message.body_plaintext,
            "metadata": message.metadata,
        }
        rendered = json.dumps(payload, sort_keys=True, default=str)
        if self._log_path is None:
            logger.info("email.send %s", rendered)
            return
        with _LOG_LOCK:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._log_path.open("a", encoding="utf-8") as handle:
                handle.write(rendered)
                handle.write("\n")


PostmarkOpener = Callable[[urllib.request.Request, float], bytes]


def _default_postmark_opener(request: urllib.request.Request, timeout: float) -> bytes:
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return bytes(response.read())


def _postmark_error_message(exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8", errors="replace")
    except Exception:
        body = ""
    if body:
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            message = payload.get("Message")
            code = payload.get("ErrorCode")
            if isinstance(message, str) and message:
                if isinstance(code, int):
                    return f"{message} (Postmark error {code})"
                return message
    try:
        phrase = HTTPStatus(exc.code).phrase
    except ValueError:
        phrase = "HTTP error"
    return f"{phrase} ({exc.code})"


class PostmarkDeliveryProvider:
    """Transactional email provider for staging/production deployments.

    The adapter sends plaintext-only messages because platform templates are
    deliberately narrow and link-only. Postmark may render richer templates in
    the future, but this keeps account recovery usable without committing
    provider-specific template state to the repo.
    """

    name = "postmark"
    _API_URL = "https://api.postmarkapp.com/email"

    def __init__(
        self,
        *,
        server_token: str | None,
        from_address: str,
        message_stream: str,
        timeout_seconds: float,
        opener: PostmarkOpener | None = None,
    ) -> None:
        token = (server_token or "").strip()
        sender = from_address.strip()
        stream = message_stream.strip()
        if not token:
            raise RuntimeError("POSTMARK_SERVER_TOKEN must be configured for Postmark delivery")
        if not sender or sender.endswith(".invalid"):
            raise RuntimeError(
                "EMAIL_VERIFICATION_FROM_ADDRESS must be a verified sender for Postmark delivery"
            )
        if not stream:
            raise RuntimeError("POSTMARK_MESSAGE_STREAM must be configured for Postmark delivery")
        self._server_token = token
        self._from_address = sender
        self._message_stream = stream
        self._timeout_seconds = timeout_seconds
        self._opener = opener or _default_postmark_opener

    def send(self, message: EmailMessage) -> None:
        payload = {
            "From": self._from_address,
            "To": message.to_address,
            "Subject": message.subject,
            "TextBody": message.body_plaintext,
            "MessageStream": self._message_stream,
            "Metadata": {
                **message.metadata,
                "template_id": message.template_id,
            },
        }
        request = urllib.request.Request(
            self._API_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-Postmark-Server-Token": self._server_token,
            },
            method="POST",
        )
        try:
            self._opener(request, self._timeout_seconds)
        except urllib.error.HTTPError as exc:
            raise EmailDeliveryError(
                f"Postmark rejected email delivery: {_postmark_error_message(exc)}"
            ) from exc
        except urllib.error.URLError as exc:
            raise EmailDeliveryError("Postmark email delivery failed: network error") from exc


_PROVIDER_FACTORY: dict[str, Callable[[Settings], EmailDeliveryProvider]] = {
    "logging": lambda s: LoggingDeliveryProvider(log_path=s.email_delivery_log_path),
    "noop": lambda _s: NoopDeliveryProvider(),
    "postmark": lambda s: PostmarkDeliveryProvider(
        server_token=s.postmark_server_token,
        from_address=s.email_verification_from_address,
        message_stream=s.postmark_message_stream,
        timeout_seconds=s.postmark_send_timeout_seconds,
    ),
}


def register_email_provider(
    name: str, factory: Callable[[Settings], EmailDeliveryProvider]
) -> None:
    """Hook for production deployments to register a real provider.

    Kept as a registration call rather than an env-driven import path so a
    misconfigured deploy can't be talked into instantiating an arbitrary
    callable. Production wiring lives outside this repo.
    """
    _PROVIDER_FACTORY[name] = factory


def get_email_provider(settings: Settings) -> EmailDeliveryProvider:
    """Construct (or look up) the configured provider.

    `settings.email_delivery_provider` is constrained at the schema layer so
    only registered ids reach this function. Unregistered ids raise a clear
    runtime error rather than silently dropping mail.
    """
    factory = _PROVIDER_FACTORY.get(settings.email_delivery_provider)
    if factory is None:
        raise RuntimeError(
            f"No email delivery provider registered for "
            f"`{settings.email_delivery_provider}`; register one before boot."
        )
    return factory(settings)
