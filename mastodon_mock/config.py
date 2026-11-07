"""Configuration loading for mastodon_mock.

Loads from ``.mastodon_mock.toml`` (whole document) or the
``[tool.mastodon_mock]`` table of ``pyproject.toml``, falling back to built-in
defaults. See spec/01-architecture.md.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import tomllib
from pydantic import BaseModel, Field

from mastodon_mock.versioning import CURRENT_VERSION


class DatabaseConfig(BaseModel):
    """SQLite database settings."""

    driver: str = "sqlite"
    path: str = ":memory:"
    echo: bool = False


class ServerConfig(BaseModel):
    """uvicorn host/port settings for ``mastodon_mock serve``."""

    host: str = "127.0.0.1"
    port: int = 3000


class AuthConfig(BaseModel):
    """Auth behaviour knobs."""

    # If true, any/no bearer token maps to the first seeded account.
    permissive: bool = False
    # If true, enforce coarse OAuth scopes (read/write/follow) on the token.
    # Off by default — the mock stores+echoes scopes but does not check them.
    enforce_scopes: bool = False
    # Lock the mock down for a publicly reachable host (see spec/04-auth.md "Password
    # mode"): /oauth/authorize asks for a password and mints single-use random codes,
    # /api/v1/_mock/* and /_ui/ are removed, admin endpoints require a staff role, seed
    # access tokens are not created, and signups honour registrations_enabled.
    require_password: bool = False


class RateLimitConfig(BaseModel):
    """Opt-in rate limiting (off by default).

    When ``enabled``, the mock returns ``429`` + ``X-RateLimit-*`` headers after
    ``limit`` requests within ``window_seconds``, so consuming suites can exercise
    Mastodon.py's ``ratelimit_method`` handling. A documented stretch goal.
    """

    enabled: bool = False
    limit: int = 300
    window_seconds: int = 300


class StreamingConfig(BaseModel):
    """Server-Sent-Events streaming behaviour. See spec/streaming.md.

    Streaming is on by default. When ``enabled`` is false the
    ``/api/v1/streaming/*`` routes (except ``health``) return ``404``, matching an
    instance with streaming switched off.
    """

    enabled: bool = True
    heartbeat_seconds: float = 15.0
    queue_maxsize: int = 1000


class FaultConfig(BaseModel):
    """Mock-only fault-injection control plane. See spec/fault_injection.md.

    The control plane is available by default but inert until a rule is added.
    Set ``enabled = false`` to remove the ``/_mock/faults`` routes and the
    middleware entirely.
    """

    enabled: bool = True


class ModerationConfig(BaseModel):
    """How strongly persisted moderation state affects the public fake."""

    # Current Mastodon behavior is the default target. Disable this only for
    # older test suites that treated moderation flags as admin-display metadata.
    enforce_actions: bool = True
    # Mastodon's domain allow list matters only in allow-list federation mode.
    # The mock has no federation mode, so this remains an explicit opt-in.
    enforce_domain_allows: bool = False


class SeedAccount(BaseModel):
    """A single seeded account."""

    username: str
    domain: str | None = None
    display_name: str | None = None
    note: str | None = None
    locked: bool = False
    bot: bool = False
    access_token: str | None = None
    # Admin / moderation seed fields (optional).
    email: str | None = None
    role: str = "user"


class SeedFollow(BaseModel):
    """A seeded follow edge by username."""

    follower: str
    following: str


class SeedStatus(BaseModel):
    """A seeded status by account username.

    ``ref`` is an optional stable handle so other seed rows can refer to this
    status (e.g. as a quote or reply target); it is not persisted. ``quotes``
    names the ``ref`` of an earlier seed status this one quotes; ``reply_to``
    names the ``ref`` of an earlier seed status this one replies to (a
    self-reply chain by one author is a "storm").
    """

    account: str
    text: str
    visibility: str = "public"
    ref: str | None = None
    quotes: str | None = None
    reply_to: str | None = None


class SeedAnnouncement(BaseModel):
    """A seeded instance announcement.

    ``content`` is the announcement body (rendered to HTML by wrapping in a
    ``<p>`` if it isn't already markup). The optional time fields and ``all_day``
    map straight onto the ``Announcement`` entity.
    """

    content: str
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    all_day: bool = False
    published: bool = True


class SeedConfig(BaseModel):
    """The collection of seed data."""

    accounts: list[SeedAccount] = Field(default_factory=list)
    follows: list[SeedFollow] = Field(default_factory=list)
    statuses: list[SeedStatus] = Field(default_factory=list)
    announcements: list[SeedAnnouncement] = Field(default_factory=list)


class SampleDataConfig(BaseModel):
    """Shape of a bulk-generated, throwaway sample cohort.

    Unlike :class:`SeedConfig` this is *not* applied at startup; it is the default
    profile used by the ``gen-data`` CLI command and the ``/_mock/sample_data``
    endpoint. See spec/09-sample-data-and-perf.md.
    """

    accounts: int = 100
    followers_per_account: int = 20
    statuses_per_account: int = 50
    reply_ratio: float = 0.2
    favourites_per_account: int = 10
    bookmarks_per_account: int = 0
    with_notifications: bool = False
    seed: int | None = None
    chunk_size: int = 5000


# Named presets that scale the whole shape together. See spec/09-sample-data-and-perf.md.
PRESETS: dict[str, SampleDataConfig] = {
    "tiny": SampleDataConfig(accounts=10, followers_per_account=5, statuses_per_account=10),
    "small": SampleDataConfig(accounts=100, followers_per_account=20, statuses_per_account=50),
    "medium": SampleDataConfig(accounts=1000, followers_per_account=100, statuses_per_account=100),
    "large": SampleDataConfig(
        accounts=5000, followers_per_account=1000, statuses_per_account=1000, favourites_per_account=50
    ),
    "huge": SampleDataConfig(
        accounts=10000, followers_per_account=1000, statuses_per_account=1000, favourites_per_account=50
    ),
}


# mock seed token below is not a real credential
DEFAULT_SEED = SeedConfig(
    accounts=[SeedAccount(username="testuser", display_name="Test User", access_token="mock_token")],  # nosec
)


# A richer, demo-only seed. Unlike DEFAULT_SEED (deliberately minimal so the test
# suite starts from a clean, predictable slate) this populates a small community —
# multiple accounts, follows, a thread, a quote post, and instance announcements —
# so that every surfaced UI feature has something to show. Applied by
# ``serve --demo`` via DEMO_CONFIG; not used by the test fixtures.
# The tokens below are mock credentials, not real secrets.
DEMO_SEED = SeedConfig(
    accounts=[
        SeedAccount(
            username="ada",
            display_name="Ada Lovelace",
            note="First programmer. Posting about analytical engines.",
            access_token="ada_token",  # nosec
            email="ada@mock.local",
            role="admin",
        ),
        SeedAccount(
            username="grace",
            display_name="Grace Hopper",
            note="Compilers, nanoseconds, and debugging actual bugs.",
            access_token="grace_token",  # nosec
            email="grace@mock.local",
        ),
        SeedAccount(
            username="alan",
            display_name="Alan Turing",
            note="Thinking about whether machines can think.",
            access_token="alan_token",  # nosec
            email="alan@mock.local",
        ),
        SeedAccount(
            username="katherine",
            display_name="Katherine Johnson",
            note="Doing the math that gets us to orbit.",
            access_token="katherine_token",  # nosec
            email="katherine@mock.local",
        ),
    ],
    follows=[
        SeedFollow(follower="grace", following="ada"),
        SeedFollow(follower="alan", following="ada"),
        SeedFollow(follower="katherine", following="ada"),
        SeedFollow(follower="ada", following="grace"),
        SeedFollow(follower="alan", following="grace"),
        SeedFollow(follower="ada", following="alan"),
    ],
    statuses=[
        SeedStatus(
            account="ada",
            text="Just finished a new set of notes on the Analytical Engine. #computing",
            ref="ada_notes",
        ),
        # A storm-shaped thread by @ada (root + self-replies): the demo tenant
        # connects @ada's identity, so this is what makes the demo blog actually
        # render a storm (self-reply chain by one author). See remaining_work §3.4.
        SeedStatus(
            account="ada",
            text=(
                "A thread on why the Analytical Engine matters — not as a "
                "calculator, but as a machine that manipulates symbols. 🧵 #computing"
            ),
            ref="ada_storm_root",
        ),
        SeedStatus(
            account="ada",
            text=(
                "1/ Babbage's Difference Engine tabulated polynomials. The "
                "Analytical Engine is different in kind: it has a 'store' (memory) "
                "and a 'mill' (a processor). Sound familiar?"
            ),
            ref="ada_storm_1",
            reply_to="ada_storm_root",
        ),
        SeedStatus(
            account="ada",
            text=(
                "2/ The leap is that its punched cards can encode *operations*, "
                "not just numbers. It can branch and loop. That means it can do "
                "more than arithmetic — it can reason over symbols of any kind."
            ),
            ref="ada_storm_2",
            reply_to="ada_storm_1",
        ),
        SeedStatus(
            account="ada",
            text=(
                "3/ So it could, in principle, compose music, or make graphics, "
                "if we could only express the rules as operations. The Engine "
                "weaves algebraic patterns just as the Jacquard loom weaves flowers."
            ),
            ref="ada_storm_3",
            reply_to="ada_storm_2",
        ),
        SeedStatus(
            account="ada",
            text=(
                "4/ It will not *originate* anything — it does whatever we know "
                "how to order it to perform. But that is exactly the point: the "
                "limits are our imagination, not the machine's. /end #computing"
            ),
            ref="ada_storm_4",
            reply_to="ada_storm_3",
        ),
        SeedStatus(
            account="grace",
            text="Reminder: it is easier to ask forgiveness than permission. #debugging",
            ref="grace_quip",
        ),
        SeedStatus(
            account="alan",
            text="Brilliant work here — this is the foundation everything else builds on.",
            quotes="ada_notes",
        ),
        SeedStatus(
            account="katherine",
            text="Running the numbers one more time before launch. #spaceflight",
        ),
        SeedStatus(
            account="ada",
            text="Couldn't agree more, Grace. Shipping beats perfect.",
            quotes="grace_quip",
        ),
        SeedStatus(
            account="grace",
            text="@ada quick one — got a minute to look over the punch-card notes before the demo?",
            visibility="direct",
        ),
        SeedStatus(
            account="ada",
            text="@grace yes, sending them over now. Thanks for the nudge!",
            visibility="direct",
        ),
        SeedStatus(
            account="alan",
            text="@ada @katherine pulling together a quick standup doc for the three of us, one sec.",
            visibility="direct",
        ),
    ],
    announcements=[
        SeedAnnouncement(
            content="<p>Welcome to the <strong>Mastodon Mock</strong> demo instance! "
            "Explore timelines, quotes, lists, and the admin panel.</p>",
        ),
        SeedAnnouncement(
            content="Scheduled maintenance window this weekend — expect brief downtime.",
            all_day=True,
        ),
    ],
)


# Sensible instance rules + terms of service for the demo (the About page reads these).
DEMO_RULES = [
    "Be excellent to each other.",
    "No harassment, hate speech, or spam.",
    "Mark sensitive media as sensitive.",
    "Credit original creators when you boost or quote.",
]

DEMO_TERMS_OF_SERVICE = (
    "<p>This is a <strong>mock</strong> Mastodon instance for testing and demos. "
    "No real data is stored and the server may be reset at any time.</p>"
    "<p>By using it you agree to be reasonable, as one does.</p>"
)


class MastodonMockConfig(BaseModel):
    """Top-level configuration object."""

    mocked_version: str = CURRENT_VERSION
    domain: str = "mock.local"
    # None preserves HTTPS for direct app construction; the CLI infers its listener's scheme.
    url_scheme: Literal["http", "https"] | None = None
    title: str = "Mastodon Mock"
    email: str = "admin@mock.local"
    description: str = "A local mock Mastodon instance for testing."
    # Instance registration policy, reported in instance metadata (v1 + v2).
    registrations_enabled: bool = True
    registration_approval_required: bool = False
    # Whether the instance advertises a translation service. Off by default
    # (the mock has no translator); set true to report translation as available.
    translation_enabled: bool = False
    media_storage_path: str | None = None
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    ratelimit: RateLimitConfig = Field(default_factory=RateLimitConfig)
    streaming: StreamingConfig = Field(default_factory=StreamingConfig)
    faults: FaultConfig = Field(default_factory=FaultConfig)
    moderation: ModerationConfig = Field(default_factory=ModerationConfig)
    seed: SeedConfig = Field(default_factory=lambda: DEFAULT_SEED)
    sample_data: SampleDataConfig = Field(default_factory=SampleDataConfig)
    rules: list[str] = Field(default_factory=list)
    # Instance terms of service (HTML or plain text). Empty → the ToS endpoint
    # 404s, matching an instance with none configured.
    terms_of_service: str = ""
    # Instance privacy policy (HTML or plain text). Empty → the endpoint 404s.
    privacy_policy: str = ""

    @property
    def base_url(self) -> str:
        """Public origin for generated API links and assets."""
        return f"{self.url_scheme or 'https'}://{self.domain}"

    @property
    def streaming_url(self) -> str:
        """Public WebSocket origin using the same transport as generated HTTP links."""
        return self.base_url.replace("http", "ws", 1)

    @classmethod
    def load(cls, config_path: str | Path | None = None) -> MastodonMockConfig:
        """Load configuration following the precedence in spec/01-architecture.md.

        1. An explicit ``config_path`` (or ``./.mastodon_mock.toml``) → whole document.
        2. ``[tool.mastodon_mock]`` in ``./pyproject.toml``.
        3. Built-in defaults.
        """
        data: dict[str, Any] | None = None

        if config_path is not None:
            data = _read_toml(Path(config_path))
        else:
            local = Path.cwd() / ".mastodon_mock.toml"
            if local.is_file():
                data = _read_toml(local)
            else:
                pyproject = Path.cwd() / "pyproject.toml"
                if pyproject.is_file():
                    doc = _read_toml(pyproject)
                    data = doc.get("tool", {}).get("mastodon_mock")

        if not data:
            return cls()
        return cls.model_validate(data)


def demo_config(base: MastodonMockConfig | None = None) -> MastodonMockConfig:
    """Return a config wired for a rich demo (seed, rules, terms of service).

    Starts from ``base`` (or fresh defaults) and overlays the demo seed plus
    instance rules and terms of service, so every surfaced UI feature has content
    to show. Used by ``serve --demo``.
    """
    config = base.model_copy(deep=True) if base is not None else MastodonMockConfig()
    config.seed = DEMO_SEED
    config.rules = list(DEMO_RULES)
    config.terms_of_service = DEMO_TERMS_OF_SERVICE
    return config


def _read_toml(path: Path) -> dict[str, Any]:
    """Read and parse a TOML file."""
    with path.open("rb") as fh:
        return tomllib.load(fh)
