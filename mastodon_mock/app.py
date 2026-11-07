"""FastAPI application factory."""

from __future__ import annotations

import base64
import tempfile
import weakref
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from mastodon_mock.config import MastodonMockConfig
from mastodon_mock.db.base import Base, init_engine, make_session_factory
from mastodon_mock.db.seed import apply_seed_data
from mastodon_mock.errors import install_error_handlers
from mastodon_mock.faults import FaultStore, add_fault_middleware
from mastodon_mock.identicon import avatar_svg, header_svg
from mastodon_mock.middleware import add_middleware
from mastodon_mock.routers import (
    accounts,
    admin,
    conversations,
    favourites_bookmarks,
    filters,
    instance,
    lists,
    media,
    misc,
    notifications,
    oauth,
    polls,
    preferences,
    push,
    relationships,
    search,
    statuses,
    streaming,
    tags,
    timelines,
    user_settings,
)
from mastodon_mock.streaming import EventBus
from mastodon_mock.ui import mount_ui

# A 1x1 transparent PNG, served at the avatar/header placeholder URLs that
# serializers/common.py builds for accounts with no custom image.
_MISSING_IMAGE_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


# Tag order + descriptions for the Swagger/ReDoc docs. Order mirrors router
# registration in create_app(); FastAPI renders tags in this order and lists any
# untagged operations (the app-level routes below) under "default" at the end.
OPENAPI_TAGS = [
    {"name": "oauth", "description": "App registration, authorization, and token endpoints."},
    {"name": "instance", "description": "Instance metadata, rules, and node info."},
    {"name": "accounts", "description": "Accounts, profiles, and the current user (`verify_credentials`)."},
    {"name": "statuses", "description": "Create, read, edit, and delete statuses; favourite/boost/pin."},
    {"name": "timelines", "description": "Home, public, hashtag, and list timelines."},
    {"name": "relationships", "description": "Follow, block, mute, and follow-request management."},
    {"name": "notifications", "description": "Notification list, dismissal, and clearing."},
    {"name": "media", "description": "Media attachment upload and metadata."},
    {"name": "search", "description": "Search across accounts, statuses, and hashtags."},
    {"name": "lists", "description": "User-defined lists and their membership."},
    {"name": "favourites & bookmarks", "description": "Favourited and bookmarked statuses."},
    {"name": "filters", "description": "Keyword/status filters."},
    {"name": "polls", "description": "Poll details and voting."},
    {"name": "preferences", "description": "User preferences."},
    {"name": "push", "description": "Web Push subscription management."},
    {"name": "conversations", "description": "Direct-message conversations."},
    {"name": "admin", "description": "Admin/moderation endpoints."},
    {"name": "tags", "description": "Hashtag follow/feature endpoints."},
    {"name": "streaming", "description": "Streaming API (server-sent events / websocket)."},
    {"name": "misc", "description": "Static-shape stubs for rarely-used upstream endpoints."},
    {"name": "mock settings", "description": "Mock-only web-settings endpoints (no upstream API equivalent)."},
]


def dispose_app_resources(app: FastAPI) -> None:
    """Dispose resources owned by a ``mastodon_mock`` app instance."""
    if hasattr(app.state, "engine"):
        app.state.engine.dispose()
    finalizer = getattr(app.state, "resource_finalizer", None)
    if finalizer is not None:
        finalizer.detach()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Dispose of the engine on shutdown to clear connection pool."""
    try:
        yield
    finally:
        dispose_app_resources(app)


def create_app(config: MastodonMockConfig | None = None) -> FastAPI:
    """Create and configure the FastAPI app for the given config."""
    config = config or MastodonMockConfig.load()
    if config.auth.require_password and config.auth.permissive:
        raise ValueError("[auth] permissive cannot be combined with require_password")

    engine = init_engine(config.database)
    Base.metadata.create_all(engine, checkfirst=True)
    apply_seed_data(engine, config.seed, with_tokens=not config.auth.require_password)

    media_path = config.media_storage_path or tempfile.mkdtemp(prefix="mastodon_mock_media_")
    Path(media_path).mkdir(parents=True, exist_ok=True)

    app = FastAPI(
        title="mastodon_mock",
        version=config.mocked_version,
        summary="Stateful mock of the Mastodon REST API.",
        description=(
            "A stateful in-memory/SQLite mock of the [Mastodon](https://docs.joinmastodon.org/api/) "
            "REST API, for testing Mastodon clients (e.g. Mastodon.py) without a real instance.\n\n"
            "Endpoints are grouped by Mastodon API area. The reported instance version is "
            f"`{config.mocked_version}`."
        ),
        contact={
            "name": "Matthew Martin",
            "url": "https://github.com/matthewdeanmartin/mastodon_mock",
            "email": "matthewdeanmartin@gmail.com",
        },
        license_info={"name": "MIT", "url": "https://opensource.org/licenses/MIT"},
        openapi_tags=OPENAPI_TAGS,
        lifespan=lifespan,
    )
    install_error_handlers(app)
    app.state.config = config
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.state.media_path = media_path
    app.state.resource_finalizer = weakref.finalize(app, engine.dispose)
    # Password mode's pending authorization codes: code -> PendingCode (see routers/oauth.py).
    app.state.authorize_codes = {}

    if config.streaming.enabled:
        app.state.event_bus = EventBus(queue_maxsize=config.streaming.queue_maxsize)

    # Real Mastodon allows any origin (web clients like elk.zone/mastodeck run in the
    # browser and call whatever instance the user points them at, so the API must be
    # reachable cross-origin). Mirrors real instances' wide-open CORS, including the
    # custom headers pagination/rate-limiting rely on (Link, X-RateLimit-*).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=[
            "Link",
            "Mastodon-Async-Refresh",
            "X-RateLimit-Reset",
            "X-RateLimit-Limit",
            "X-RateLimit-Remaining",
            "Retry-After",
            "X-Request-Id",
        ],
    )

    @app.middleware("http")
    async def _set_server_header(request: Request, call_next: Any) -> Response:
        """Set ``Server: Mastodon``, matching real instances — some clients use it
        as a reachability/identity signal (alongside nodeinfo's ``software.name``).
        """
        response: Response = await call_next(request)
        response.headers["Server"] = "Mastodon"
        return response

    add_middleware(app, config)

    # Added last so it wraps outermost: a fault short-circuits before scope/rate
    # checks, and the request never reaches the router.
    if config.faults.enabled:
        app.state.fault_store = FaultStore()
        add_fault_middleware(app)

    for module in (
        oauth,
        instance,
        accounts,
        statuses,
        timelines,
        relationships,
        notifications,
        media,
        search,
        lists,
        favourites_bookmarks,
        filters,
        polls,
        preferences,
        push,
        conversations,
        admin,
        tags,
        streaming,
        misc,
        user_settings,
    ):
        app.include_router(module.router)

    app.mount("/media", StaticFiles(directory=media_path), name="media")

    @app.get("/avatars/original/missing.png")
    @app.get("/headers/original/missing.png")
    def missing_placeholder() -> Response:
        """Serve the 1x1 placeholder image, kept for any URL built before this account existed.

        Real Mastodon ships actual stock images at these paths; older serializer output or
        cached clients may still reference this path, so it stays available as a fallback.
        """
        return Response(content=_MISSING_IMAGE_PNG, media_type="image/png")

    @app.get("/avatars/generated/{seed}.svg")
    def generated_avatar(seed: str) -> Response:
        """Serve a deterministic per-account SVG identicon (see serializers/common.py)."""
        return Response(content=avatar_svg(seed), media_type="image/svg+xml")

    @app.get("/headers/generated/{seed}.svg")
    def generated_header(seed: str) -> Response:
        """Serve a deterministic per-account SVG header banner (see serializers/common.py)."""
        return Response(content=header_svg(seed), media_type="image/svg+xml")

    # The admin UI logs in through the /api/v1/_mock routes, which password mode removes.
    ui_available = False if config.auth.require_password else mount_ui(app)

    @app.get("/health")
    def health() -> Response:
        """Liveness probe, matching real Mastodon's bare-text ``/health``."""
        return Response(content="OK", media_type="text/plain")

    @app.get("/")
    def root() -> Response:
        """Root: serve HTML, same as real Mastodon instances (e.g. mastodon.social).

        The JSON identity blob previously served here isn't part of the real
        Mastodon API contract — API clients hit ``/api/...`` and never depend on
        what ``/`` returns. So ``/`` always behaves like a browser landing page:
        redirect into the SPA at ``/_ui/`` when it's built, otherwise serve a
        minimal HTML stub.
        """
        if ui_available:
            return RedirectResponse(url="/_ui/")
        return Response(content=_NO_UI_HTML, media_type="text/html")

    return app


_NO_UI_HTML = (
    "<!doctype html><html><head><title>mastodon_mock</title></head>"
    "<body><h1>mastodon_mock</h1><p>Admin UI not built. Run <code>make ui</code>.</p>"
    "</body></html>"
)
