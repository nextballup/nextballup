from __future__ import annotations


class ProcessingError(Exception):
    """Base for worker-side processing errors.

    Carries a stable `code` that is persisted to `processing_jobs.error_message`
    prefix + `extra` so the API status endpoints can surface it without leaking
    internal stack traces.
    """

    code: str = "processing_error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code


class TransientProcessingError(ProcessingError):
    """Retryable error. Celery's autoretry_for rebrands these as retries until
    the task's `max_retries` is exhausted, at which point the `on_failure` hook
    marks the job terminally FAILED."""

    code = "transient_processing_error"


class PermanentProcessingError(ProcessingError):
    """Non-retryable error. The task marks the job FAILED immediately and
    re-raises so Celery records the task as failed without further retries."""

    code = "permanent_processing_error"
