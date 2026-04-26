"""Email delivery provider abstraction.

The platform never embeds real provider credentials. Two providers ship in
the repo:

  * `logging` (default for dev/test): records each message as a JSON line
    against a configurable path, or to the `nextballup_api.email` logger
    when the path is unset. Useful for local development and CI.
  * `noop`: drops messages on the floor. Useful for unit tests that exercise
    code paths without recording anything.

A production deployment is expected to register a real provider (SES, SendGrid,
Postmark, …) by registering an `EmailDeliveryProvider` instance under the
`production` provider id. That registration intentionally lives outside the
default codebase so credentials are never committed.

`EmailMessage` is intentionally narrow: subject + plaintext body + a single
known link. We do not render arbitrary HTML in the platform-side template
because the repo cannot store templates that have been reviewed by counsel
or designers; a richer body is the provider's job in production.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
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


_PROVIDER_FACTORY: dict[str, Callable[[Settings], EmailDeliveryProvider]] = {
    "logging": lambda s: LoggingDeliveryProvider(log_path=s.email_delivery_log_path),
    "noop": lambda _s: NoopDeliveryProvider(),
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
