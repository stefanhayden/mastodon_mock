"""SQLAlchemy 2.0 declarative models. See spec/02-data-model.md.

All primary keys / FKs are ``BigInteger`` and are serialized as strings in API
responses (Mastodon ``IdType``). ID values come from ``mastodon_mock.ids.next_id``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from mastodon_mock.db.base import Base
from mastodon_mock.ids import next_id


def utcnow() -> datetime:
    """Return the current UTC time (timezone-aware)."""
    return datetime.now(UTC)


def _id() -> int:
    """Default factory for primary keys."""
    return next_id()


class Account(Base):
    """A local account (possibly marked "remote" for seeding)."""

    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, default=_id)
    username: Mapped[str] = mapped_column(String, index=True)
    domain: Mapped[str | None] = mapped_column(String, nullable=True)
    display_name: Mapped[str] = mapped_column(String, default="")
    note: Mapped[str] = mapped_column(Text, default="")
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    bot: Mapped[bool] = mapped_column(Boolean, default=False)
    discoverable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    group: Mapped[bool] = mapped_column(Boolean, default=False)
    indexable: Mapped[bool] = mapped_column(Boolean, default=False)
    hide_collections: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String, nullable=True)
    header_url: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    fields: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    default_privacy: Mapped[str] = mapped_column(String, default="public")
    default_sensitive: Mapped[bool] = mapped_column(Boolean, default=False)
    default_language: Mapped[str | None] = mapped_column(String, nullable=True)

    # Admin / moderation state (see spec/03-api-coverage.md "admin"). These back
    # the AdminAccount entity and the moderation actions; they have no effect on
    # the public API surface beyond being reported.
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    ip: Mapped[str | None] = mapped_column(String, nullable=True)
    role: Mapped[str] = mapped_column(String, default="user")
    locale: Mapped[str] = mapped_column(String, default="en")
    confirmed: Mapped[bool] = mapped_column(Boolean, default=True)
    approved: Mapped[bool] = mapped_column(Boolean, default=True)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    silenced: Mapped[bool] = mapped_column(Boolean, default=False)
    suspended: Mapped[bool] = mapped_column(Boolean, default=False)
    sensitized: Mapped[bool] = mapped_column(Boolean, default=False)
    invite_request: Mapped[str | None] = mapped_column(String, nullable=True)
    # scrypt hash (see mastodon_mock.passwords); only checked when [auth] require_password is on.
    password_hash: Mapped[str | None] = mapped_column(String, nullable=True)

    __table_args__ = (UniqueConstraint("username", "domain", name="uq_account_username_domain"),)


class OAuthApp(Base):
    """A registered OAuth application (``/api/v1/apps``)."""

    __tablename__ = "oauth_apps"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, default=_id)
    client_id: Mapped[str] = mapped_column(String, unique=True)
    client_secret: Mapped[str] = mapped_column(String)
    name: Mapped[str] = mapped_column(String)
    website: Mapped[str | None] = mapped_column(String, nullable=True)
    redirect_uris: Mapped[list[str]] = mapped_column(JSON, default=list)
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list)


class OAuthToken(Base):
    """A bearer token bound to an account (+ app + scopes)."""

    __tablename__ = "oauth_tokens"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, default=_id)
    access_token: Mapped[str] = mapped_column(String, unique=True)
    refresh_token: Mapped[str | None] = mapped_column(String, nullable=True)
    app_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("oauth_apps.id"), nullable=True)
    account_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("accounts.id"), nullable=True)
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Status(Base):
    """A single status / toot."""

    __tablename__ = "statuses"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, default=_id)
    account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.id"), index=True)
    content: Mapped[str] = mapped_column(Text, default="")
    text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    in_reply_to_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("statuses.id"), nullable=True)
    in_reply_to_account_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("accounts.id"), nullable=True)
    reblog_of_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("statuses.id"), nullable=True)
    sensitive: Mapped[bool] = mapped_column(Boolean, default=False)
    spoiler_text: Mapped[str] = mapped_column(String, default="")
    visibility: Mapped[str] = mapped_column(String, default="public")
    language: Mapped[str | None] = mapped_column(String, nullable=True)
    poll_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("polls.id"), nullable=True)
    url: Mapped[str | None] = mapped_column(String, nullable=True)
    application_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("oauth_apps.id"), nullable=True)
    # The status this one quotes (Mastodon 4.5+ quote posts), if any.
    quoted_status_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("statuses.id"), nullable=True)
    # Quote state of *this* status's quote ("accepted"/"revoked"/"pending"); only
    # meaningful when quoted_status_id is set.
    quote_state: Mapped[str] = mapped_column(String, default="accepted")
    # Who may quote *this* status: "public" | "followers" | "nobody" (4.5+).
    quote_approval_policy: Mapped[str] = mapped_column(String, default="public")
    # Snapshots of past versions for status_history(); list of dicts.
    edit_history: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)

    account: Mapped[Account] = relationship(foreign_keys=[account_id], lazy="joined")


class StatusMention(Base):
    """A status → mentioned account edge."""

    __tablename__ = "status_mentions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, default=_id)
    status_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("statuses.id"), index=True)
    account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.id"))


class StatusTag(Base):
    """A hashtag attached to a status."""

    __tablename__ = "status_tags"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, default=_id)
    status_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("statuses.id"), index=True)
    name: Mapped[str] = mapped_column(String, index=True)


class FollowedTag(Base):
    """A hashtag followed by an account (``tag_follow``/``tag_unfollow``)."""

    __tablename__ = "followed_tags"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, default=_id)
    account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.id"), index=True)
    name: Mapped[str] = mapped_column(String, index=True)

    __table_args__ = (UniqueConstraint("account_id", "name", name="uq_followed_tag"),)


class FeaturedTag(Base):
    """A hashtag an account features on its profile.

    Backs ``featured_tag_create``/``featured_tag_delete`` and the newer
    ``tag_feature``/``tag_unfeature``. ``statuses_count``/``last_status_at`` are
    derived at read time from the account's statuses using the tag.
    """

    __tablename__ = "featured_tags"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, default=_id)
    account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.id"), index=True)
    name: Mapped[str] = mapped_column(String, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (UniqueConstraint("account_id", "name", name="uq_featured_tag"),)


class MediaAttachment(Base):
    """An uploaded media attachment."""

    __tablename__ = "media_attachments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, default=_id)
    account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.id"))
    status_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("statuses.id"), nullable=True)
    type: Mapped[str] = mapped_column(String, default="unknown")
    url: Mapped[str] = mapped_column(String, default="")
    preview_url: Mapped[str] = mapped_column(String, default="")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    blurhash: Mapped[str | None] = mapped_column(String, nullable=True)
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    filename: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Poll(Base):
    """A poll attached to a status."""

    __tablename__ = "polls"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, default=_id)
    status_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    multiple: Mapped[bool] = mapped_column(Boolean, default=False)
    hide_totals: Mapped[bool] = mapped_column(Boolean, default=False)
    expired: Mapped[bool] = mapped_column(Boolean, default=False)

    options: Mapped[list[PollOption]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="PollOption.position"
    )


class PollOption(Base):
    """An option of a poll."""

    __tablename__ = "poll_options"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, default=_id)
    poll_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("polls.id"))
    position: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String)


class PollVote(Base):
    """A single vote on a poll option by an account."""

    __tablename__ = "poll_votes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, default=_id)
    poll_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("polls.id"), index=True)
    account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.id"))
    option_position: Mapped[int] = mapped_column(Integer)


class Relationship(Base):
    """A directed (source → target) relationship edge."""

    __tablename__ = "relationships"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, default=_id)
    source_account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.id"), index=True)
    target_account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.id"), index=True)
    following: Mapped[bool] = mapped_column(Boolean, default=False)
    showing_reblogs: Mapped[bool] = mapped_column(Boolean, default=True)
    notifying: Mapped[bool] = mapped_column(Boolean, default=False)
    languages: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    followed_by: Mapped[bool] = mapped_column(Boolean, default=False)
    blocking: Mapped[bool] = mapped_column(Boolean, default=False)
    blocked_by: Mapped[bool] = mapped_column(Boolean, default=False)
    muting: Mapped[bool] = mapped_column(Boolean, default=False)
    muting_notifications: Mapped[bool] = mapped_column(Boolean, default=True)
    muting_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    endorsed: Mapped[bool] = mapped_column(Boolean, default=False)
    requested: Mapped[bool] = mapped_column(Boolean, default=False)
    requested_by: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str] = mapped_column(Text, default="")

    __table_args__ = (UniqueConstraint("source_account_id", "target_account_id", name="uq_relationship_source_target"),)


class DomainBlock(Base):
    """A domain block owned by an account."""

    __tablename__ = "domain_blocks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, default=_id)
    account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.id"), index=True)
    domain: Mapped[str] = mapped_column(String)


class Favourite(Base):
    """A favourite of a status by an account."""

    __tablename__ = "favourites"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, default=_id)
    account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.id"), index=True)
    status_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("statuses.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (UniqueConstraint("account_id", "status_id", name="uq_favourite"),)


class Bookmark(Base):
    """A bookmark of a status by an account."""

    __tablename__ = "bookmarks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, default=_id)
    account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.id"), index=True)
    status_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("statuses.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (UniqueConstraint("account_id", "status_id", name="uq_bookmark"),)


class Pin(Base):
    """A pinned status for an account."""

    __tablename__ = "pins"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, default=_id)
    account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.id"), index=True)
    status_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("statuses.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (UniqueConstraint("account_id", "status_id", name="uq_pin"),)


class StatusMute(Base):
    """A status (conversation) mute by an account."""

    __tablename__ = "status_mutes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, default=_id)
    account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.id"), index=True)
    status_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("statuses.id"), index=True)

    __table_args__ = (UniqueConstraint("account_id", "status_id", name="uq_status_mute"),)


class Notification(Base):
    """A notification to a recipient account."""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, default=_id)
    account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.id"), index=True)
    type: Mapped[str] = mapped_column(String)
    from_account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.id"))
    status_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("statuses.id"), nullable=True)
    request_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("notification_requests.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    read: Mapped[bool] = mapped_column(Boolean, default=False)


class NotificationPolicy(Base):
    """Per-account filtering policy for incoming notifications."""

    __tablename__ = "notification_policies"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, default=_id)
    account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.id"), unique=True, index=True)
    for_not_following: Mapped[str] = mapped_column(String, default="accept")
    for_not_followers: Mapped[str] = mapped_column(String, default="accept")
    for_new_accounts: Mapped[str] = mapped_column(String, default="accept")
    for_private_mentions: Mapped[str] = mapped_column(String, default="accept")
    for_limited_accounts: Mapped[str] = mapped_column(String, default="accept")


class NotificationRequest(Base):
    """Filtered notifications grouped by recipient and actor."""

    __tablename__ = "notification_requests"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, default=_id)
    account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.id"), index=True)
    from_account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (UniqueConstraint("account_id", "from_account_id", name="uq_notification_request_accounts"),)


class NotificationPolicyOverride(Base):
    """Actor explicitly accepted from the notification-request queue."""

    __tablename__ = "notification_policy_overrides"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, default=_id)
    account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.id"), index=True)
    from_account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.id"), index=True)

    __table_args__ = (
        UniqueConstraint("account_id", "from_account_id", name="uq_notification_policy_override_accounts"),
    )


class UserList(Base):
    """A user-defined list."""

    __tablename__ = "user_lists"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, default=_id)
    account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.id"), index=True)
    title: Mapped[str] = mapped_column(String)
    replies_policy: Mapped[str] = mapped_column(String, default="list")
    exclusive: Mapped[bool] = mapped_column(Boolean, default=False)


class UserListAccount(Base):
    """Membership of an account in a list."""

    __tablename__ = "user_list_accounts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, default=_id)
    list_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("user_lists.id"), index=True)
    account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.id"))


class Filter(Base):
    """A content filter (v2; v1 shape derived in serializer)."""

    __tablename__ = "filters"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, default=_id)
    account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.id"), index=True)
    title: Mapped[str] = mapped_column(String, default="")
    context: Mapped[list[str]] = mapped_column(JSON, default=list)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    filter_action: Mapped[str] = mapped_column(String, default="warn")

    keywords: Mapped[list[FilterKeyword]] = relationship(cascade="all, delete-orphan", lazy="selectin")
    status_filters: Mapped[list[FilterStatus]] = relationship(cascade="all, delete-orphan", lazy="selectin")


class FilterKeyword(Base):
    """A keyword belonging to a filter."""

    __tablename__ = "filter_keywords"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, default=_id)
    filter_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("filters.id"), index=True)
    keyword: Mapped[str] = mapped_column(String, default="")
    whole_word: Mapped[bool] = mapped_column(Boolean, default=True)


class FilterStatus(Base):
    """A specific status attached to a filter (``FilterStatus``)."""

    __tablename__ = "filter_statuses"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, default=_id)
    filter_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("filters.id"), index=True)
    status_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("statuses.id"), index=True)


class Announcement(Base):
    """An instance announcement posted by staff.

    Announcement bodies are seeded from config; per-user read (dismissed) state
    and reactions live in their own tables so they can be set at runtime.
    """

    __tablename__ = "announcements"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, default=_id)
    content: Mapped[str] = mapped_column(Text, default="")
    starts_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    all_day: Mapped[bool] = mapped_column(Boolean, default=False)
    published: Mapped[bool] = mapped_column(Boolean, default=True)
    published_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    dismissals: Mapped[list[AnnouncementDismissal]] = relationship(cascade="all, delete-orphan", lazy="selectin")
    reactions: Mapped[list[AnnouncementReaction]] = relationship(cascade="all, delete-orphan", lazy="selectin")


class AnnouncementDismissal(Base):
    """Records that an account has dismissed (read) an announcement."""

    __tablename__ = "announcement_dismissals"
    __table_args__ = (UniqueConstraint("announcement_id", "account_id", name="uq_announcement_dismissal"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, default=_id)
    announcement_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("announcements.id"), index=True)
    account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.id"), index=True)


class AnnouncementReaction(Base):
    """A single account's reaction (one emoji ``name``) to an announcement."""

    __tablename__ = "announcement_reactions"
    __table_args__ = (UniqueConstraint("announcement_id", "account_id", "name", name="uq_announcement_reaction"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, default=_id)
    announcement_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("announcements.id"), index=True)
    account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.id"), index=True)
    name: Mapped[str] = mapped_column(String, default="")


class ScheduledStatus(Base):
    """A scheduled (future-dated) status."""

    __tablename__ = "scheduled_statuses"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, default=_id)
    account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.id"), index=True)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Marker(Base):
    """A read marker for a timeline."""

    __tablename__ = "markers"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, default=_id)
    account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.id"), index=True)
    timeline: Mapped[str] = mapped_column(String)
    last_read_id: Mapped[int] = mapped_column(BigInteger)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (UniqueConstraint("account_id", "timeline", name="uq_marker"),)


class Idempotency(Base):
    """Dedup table for ``Idempotency-Key`` on status posting."""

    __tablename__ = "idempotency"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, default=_id)
    account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.id"), index=True)
    key: Mapped[str] = mapped_column(String, index=True)
    status_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("statuses.id"))

    __table_args__ = (UniqueConstraint("account_id", "key", name="uq_idempotency"),)


class ConversationRead(Base):
    """Marks a direct-message conversation as read for an account."""

    __tablename__ = "conversation_reads"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, default=_id)
    account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.id"), index=True)
    conversation_id: Mapped[str] = mapped_column(String, index=True)

    __table_args__ = (UniqueConstraint("account_id", "conversation_id", name="uq_conversation_read"),)


class SuggestionDismissal(Base):
    """A follow suggestion hidden by an account."""

    __tablename__ = "suggestion_dismissals"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, default=_id)
    account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.id"), index=True)
    target_account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.id"), index=True)

    __table_args__ = (UniqueConstraint("account_id", "target_account_id", name="uq_suggestion_dismissal_accounts"),)


class PushSubscription(Base):
    """A Web Push subscription for an OAuth token (one per token, per Mastodon semantics)."""

    __tablename__ = "push_subscriptions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, default=_id)
    token_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("oauth_tokens.id"), unique=True, index=True)
    endpoint: Mapped[str] = mapped_column(String)
    server_key: Mapped[str] = mapped_column(String, default="")
    alerts: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    policy: Mapped[str] = mapped_column(String, default="all")


# --- Admin / moderation (see spec/03-api-coverage.md "admin") -----------------


class Report(Base):
    """A moderation report filed against an account.

    Backs both the (consumer-facing) ``/api/v1/reports`` create endpoint and the
    admin report API. ``status_ids`` holds the reported status ids as JSON.
    """

    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, default=_id)
    account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.id"), index=True)
    target_account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.id"), index=True)
    comment: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String, default="other")
    forwarded: Mapped[bool] = mapped_column(Boolean, default=False)
    status_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    rule_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    action_taken: Mapped[bool] = mapped_column(Boolean, default=False)
    action_taken_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    assigned_account_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("accounts.id"), nullable=True)
    action_taken_by_account_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("accounts.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AdminDomainBlock(Base):
    """An instance-wide domain block (admin API)."""

    __tablename__ = "admin_domain_blocks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, default=_id)
    domain: Mapped[str] = mapped_column(String, index=True)
    severity: Mapped[str] = mapped_column(String, default="silence")
    reject_media: Mapped[bool] = mapped_column(Boolean, default=False)
    reject_reports: Mapped[bool] = mapped_column(Boolean, default=False)
    private_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    public_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    obfuscate: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class AdminDomainAllow(Base):
    """An instance-wide domain allow (allowlist mode; admin API)."""

    __tablename__ = "admin_domain_allows"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, default=_id)
    domain: Mapped[str] = mapped_column(String, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class AdminEmailDomainBlock(Base):
    """A blocked email domain for signups (admin API)."""

    __tablename__ = "admin_email_domain_blocks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, default=_id)
    domain: Mapped[str] = mapped_column(String, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class AdminCanonicalEmailBlock(Base):
    """A canonical (hashed) email block (admin API)."""

    __tablename__ = "admin_canonical_email_blocks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, default=_id)
    canonical_email_hash: Mapped[str] = mapped_column(String, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class AdminIpBlock(Base):
    """A blocked IP address or CIDR range (admin API)."""

    __tablename__ = "admin_ip_blocks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, default=_id)
    ip: Mapped[str] = mapped_column(String, index=True)
    severity: Mapped[str] = mapped_column(String, default="no_access")
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class TrendReview(Base):
    """Persisted moderator decision for a trend candidate."""

    __tablename__ = "trend_reviews"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, default=_id)
    kind: Mapped[str] = mapped_column(String, index=True)
    key: Mapped[str] = mapped_column(String, index=True)
    approved: Mapped[bool] = mapped_column(Boolean)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_by_account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.id"))

    __table_args__ = (UniqueConstraint("kind", "key", name="uq_trend_review_kind_key"),)


class AccountSettings(Base):
    """Per-account web-settings blob (mock-only; backs ``/api/v1/_mock/settings``).

    Real Mastodon keeps these web-UI settings (appearance, email notifications,
    automated post deletion, ...) server-side but exposes no public API for them,
    so the mock stores them as one JSON document per account.
    """

    __tablename__ = "account_settings"

    account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.id"), primary_key=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Invite(Base):
    """An invite link owned by an account (mock-only; backs ``/api/v1/_mock/invites``)."""

    __tablename__ = "invites"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, default=_id)
    account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.id"), index=True)
    code: Mapped[str] = mapped_column(String, unique=True)
    max_uses: Mapped[int | None] = mapped_column(Integer, nullable=True)
    uses: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
