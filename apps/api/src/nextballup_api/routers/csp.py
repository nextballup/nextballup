from __future__ import annotations

import json
from collections.abc import Mapping
from json import JSONDecodeError

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from nextballup_api.deps import get_app_settings, get_db, get_optional_current_user
from nextballup_api.request_meta import client_ip
from nextballup_api.security.rate_limit import enforce_rate_limit
from nextballup_core.observability import API_CSP_REPORTS_TOTAL
from nextballup_core.settings import Settings
from nextballup_db.models.csp import CspReport

router = APIRouter(tags=["csp"])

MAX_CSP_REPORT_BYTES = 8 * 1024
CSP_REPORT_RATE_LIMIT_ATTEMPTS = 60
CSP_REPORT_RATE_LIMIT_WINDOW_SECONDS = 60
_ALLOWED_CONTENT_TYPES = {"application/csp-report", "application/json"}
_KNOWN_DIRECTIVES = {
    "base-uri",
    "child-src",
    "connect-src",
    "default-src",
    "font-src",
    "form-action",
    "frame-ancestors",
    "frame-src",
    "img-src",
    "manifest-src",
    "media-src",
    "object-src",
    "report-to",
    "report-uri",
    "script-src",
    "script-src-attr",
    "script-src-elem",
    "style-src",
    "style-src-attr",
    "style-src-elem",
    "worker-src",
    "unknown",
}


@router.post("/_csp-report", status_code=status.HTTP_204_NO_CONTENT)
async def create_csp_report(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> Response:
    _validate_content_type(request)
    await enforce_rate_limit(
        request=request,
        settings=settings,
        scope="csp_report",
        subject="anonymous",
        max_attempts=CSP_REPORT_RATE_LIMIT_ATTEMPTS,
        window_seconds=CSP_REPORT_RATE_LIMIT_WINDOW_SECONDS,
    )
    payload = _decode_report(await _read_capped_body(request))
    report = _extract_report(payload)
    directive = (
        _text(report, "violated-directive", 256)
        or _text(report, "effective-directive", 256)
        or "unknown"
    )
    current_user = await get_optional_current_user(request, session, settings)
    session.add(
        CspReport(
            user_id=current_user.id if current_user is not None else None,
            document_uri=_text(report, "document-uri", 1024),
            violated_directive=directive,
            blocked_uri=_text(report, "blocked-uri", 512),
            source_file=_text(report, "source-file", 512),
            line_number=_int(report, "line-number"),
            column_number=_int(report, "column-number"),
            user_agent=_truncate(request.headers.get("user-agent"), 512),
            reporter_ip=_truncate(client_ip(request, settings=settings), 64),
        )
    )
    API_CSP_REPORTS_TOTAL.labels(directive=_metric_directive(directive)).inc()
    await session.commit()
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


def _validate_content_type(request: Request) -> None:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type not in _ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Unsupported CSP report content type",
        )


async def _read_capped_body(request: Request) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_CSP_REPORT_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail="CSP report payload too large",
                )
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid Content-Length",
            ) from None

    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_CSP_REPORT_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="CSP report payload too large",
            )
    return bytes(body)


def _decode_report(raw: bytes) -> Mapping[str, object]:
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, JSONDecodeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Malformed CSP report payload",
        ) from None
    if not isinstance(decoded, Mapping):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Malformed CSP report payload",
        )
    return decoded


def _extract_report(payload: Mapping[str, object]) -> Mapping[str, object]:
    legacy = payload.get("csp-report")
    if isinstance(legacy, Mapping):
        return legacy
    body = payload.get("body")
    if isinstance(body, Mapping):
        return body
    return payload


def _text(report: Mapping[str, object], key: str, max_length: int) -> str | None:
    value = report.get(key)
    if not isinstance(value, str):
        return None
    return _truncate(value, max_length)


def _truncate(value: str | None, max_length: int) -> str | None:
    if value is None:
        return None
    return value[:max_length]


def _metric_directive(directive: str) -> str:
    return directive if directive in _KNOWN_DIRECTIVES else "other"


def _int(report: Mapping[str, object], key: str) -> int | None:
    value = report.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None
