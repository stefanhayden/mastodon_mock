"""Opt-in middleware: coarse OAuth scope enforcement and rate limiting.

Both are **off by default** (the mock stores+echoes scopes but never checks them,
and imposes no rate limit). They exist so a consuming suite can exercise
Mastodon.py's scope-error and ``ratelimit_method`` handling. Enable via config:

    [tool.mastodon_mock.auth]
    enforce_scopes = true

    [tool.mastodon_mock.ratelimit]
    enabled = true
    limit = 5
    window_seconds = 300
"""

from __future__ import annotations

import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from mastodon_mock.config import MastodonMockConfig
from mastodon_mock.db.models import OAuthToken

# HTTP methods that mutate state require the ``write`` scope; everything else
# requires ``read``. Follows are a special case handled below.
_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Paths exempt from scope checks: auth/oauth bootstrap, instance metadata, and the
# mock-only helpers. These are either unauthenticated or pre-token.
_SCOPE_EXEMPT_PREFIXES = (
    "/oauth",
    "/api/v1/apps",
    "/api/v1/_mock",
    "/api/v1/instance",
    "/api/v2/instance",
    "/api/v1/custom_emojis",
    "/api/v1/announcements",
    "/media",
)


def _bearer_token(request: Request) -> str | None:
    """Extract a bearer token from the Authorization header, if present."""
    auth = request.headers.get("authorization")
    if not auth:
        return None
    scheme, _, token = auth.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token


def _scopes_satisfy(granted: list[str], required: str) -> bool:
    """Whether ``granted`` scopes cover ``required`` (e.g. ``write`` covers ``write:statuses``)."""
    if required in granted:
        return True
    # A broad scope (``read``/``write``) covers its granular children.
    top = required.split(":", 1)[0]
    return top in granted


def _required_scope(request: Request) -> str:
    """The coarse scope a request needs, by method."""
    # Follow/unfollow live under accounts/*; Mastodon files them under the
    # ``follow`` scope, but ``write`` is also accepted. Treat as write for simplicity.
    return "write" if request.method.upper() in _WRITE_METHODS else "read"


def add_middleware(app: FastAPI, config: MastodonMockConfig) -> None:
    """Wire scope/rate-limit middleware onto ``app`` only if the config enables them."""
    if config.auth.enforce_scopes:
        _add_scope_enforcement(app)
    if config.ratelimit.enabled:
        _add_rate_limiting(app, config)
    if config.auth.require_password:
        _add_password_mode_guards(app)


_MOCK_ONLY_PREFIX = "/api/v1/_mock"
_ADMIN_PREFIXES = ("/api/v1/admin/", "/api/v2/admin/")
_STAFF_ROLES = {"moderator", "admin", "owner"}


def _add_password_mode_guards(app: FastAPI) -> None:
    """Hide the unauthenticated mock-only routes and require a staff role for the admin API."""
    from mastodon_mock.db.models import Account

    @app.middleware("http")
    async def _guard(request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if path == _MOCK_ONLY_PREFIX or path.startswith(f"{_MOCK_ONLY_PREFIX}/"):
            return JSONResponse(status_code=404, content={"error": "Record not found"})
        token_str = _bearer_token(request)
        # No token falls through so the route returns its usual 401.
        if token_str is not None and path.startswith(_ADMIN_PREFIXES):
            factory = request.app.state.session_factory
            with factory() as session:
                token = session.scalar(select(OAuthToken).where(OAuthToken.access_token == token_str))
                account = session.get(Account, token.account_id) if token and token.account_id else None
                role = account.role if account is not None else None
            if role not in _STAFF_ROLES:
                return JSONResponse(status_code=403, content={"error": "This action is not allowed"})
        return await call_next(request)


def _add_scope_enforcement(app: FastAPI) -> None:
    @app.middleware("http")
    async def _enforce_scopes(request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        token_str = _bearer_token(request)
        if token_str is not None and not any(path.startswith(p) for p in _SCOPE_EXEMPT_PREFIXES):
            factory = request.app.state.session_factory
            with factory() as session:
                token = session.scalar(select(OAuthToken).where(OAuthToken.access_token == token_str))
            if token is not None and not _scopes_satisfy(list(token.scopes or []), _required_scope(request)):
                return JSONResponse(
                    status_code=403,
                    content={"error": "This action is outside the authorized scopes"},
                )
        return await call_next(request)


def _add_rate_limiting(app: FastAPI, config: MastodonMockConfig) -> None:
    limit = config.ratelimit.limit
    window = config.ratelimit.window_seconds
    # Per-token fixed-window counters: token -> (window_start_epoch, count).
    buckets: dict[str, tuple[float, int]] = {}

    @app.middleware("http")
    async def _rate_limit(request: Request, call_next: RequestResponseEndpoint) -> Response:
        key = _bearer_token(request) or (request.client.host if request.client else "anon")
        now = time.time()
        start, count = buckets.get(key, (now, 0))
        if now - start >= window:
            start, count = now, 0
        count += 1
        buckets[key] = (start, count)

        reset_epoch = int(start + window)
        remaining = max(0, limit - count)

        if count > limit:
            resp: Response = JSONResponse(
                status_code=429,
                content={"error": "Too many requests"},
            )
            remaining = 0
        else:
            resp = await call_next(request)

        resp.headers["X-RateLimit-Limit"] = str(limit)
        resp.headers["X-RateLimit-Remaining"] = str(remaining)
        resp.headers["X-RateLimit-Reset"] = str(reset_epoch)
        return resp
