"""Optional shared-token gate for deployments that are reachable from the internet.

Set ``CELLTRACK_ACCESS_TOKEN`` to enable it. Opening any page with
``?token=<value>`` stores a cookie and redirects to the same page without the
token; afterwards the cookie is enough. Every other request is refused.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import hashlib
import hmac
import os
from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import PlainTextResponse, RedirectResponse, Response


TOKEN_ENV = "CELLTRACK_ACCESS_TOKEN"
TOKEN_PARAM = "token"
COOKIE_NAME = "celltrack_access"
COOKIE_MAX_AGE = 30 * 24 * 60 * 60


def configured_token() -> str | None:
    return os.environ.get(TOKEN_ENV) or None


def cookie_value(token: str) -> str:
    return hashlib.sha256(f"celltrack-access:{token}".encode()).hexdigest()


def _matches(candidate: str | None, expected: str) -> bool:
    return candidate is not None and hmac.compare_digest(
        candidate.encode(), expected.encode()
    )


async def require_access_token(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    token = configured_token()
    if token is None:
        return await call_next(request)

    if _matches(request.query_params.get(TOKEN_PARAM), token):
        remaining = urlencode([
            (key, value)
            for key, value in request.query_params.multi_items()
            if key != TOKEN_PARAM
        ])
        # A leading "//" would turn the redirect into a different host.
        target = "/" + request.url.path.lstrip("/") + (f"?{remaining}" if remaining else "")
        response = RedirectResponse(target, status_code=303)
        response.set_cookie(
            COOKIE_NAME,
            cookie_value(token),
            max_age=COOKIE_MAX_AGE,
            httponly=True,
            secure=_is_https(request),
            samesite="lax",
        )
        return response

    if _matches(request.cookies.get(COOKIE_NAME), cookie_value(token)):
        return await call_next(request)

    return PlainTextResponse("Forbidden", status_code=403)


def _is_https(request: Request) -> bool:
    forwarded = request.headers.get("x-forwarded-proto", "")
    return request.url.scheme == "https" or forwarded.split(",")[0].strip() == "https"
