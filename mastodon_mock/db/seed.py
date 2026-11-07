"""Idempotent seed application. See spec/07-seeding-and-fixtures.md."""

from __future__ import annotations

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from mastodon_mock.config import SeedAccount, SeedAnnouncement, SeedConfig, SeedStatus
from mastodon_mock.content_format import render_status_html
from mastodon_mock.db.models import Account, Announcement, OAuthToken, Status, utcnow
from mastodon_mock.services import attach_mentions_and_tags, do_follow

_DEFAULT_SCOPES = ["read", "write", "follow", "push"]


def apply_seed_data(engine: Engine, seed: SeedConfig, *, with_tokens: bool = True) -> None:
    """Apply seed accounts, follows, and statuses idempotently.

    ``with_tokens=False`` skips seed access tokens, which are well-known strings
    (e.g. ``mock_token``) that must not work on a password-protected server.
    """
    with Session(engine) as session:
        username_to_account: dict[str, Account] = {}

        for spec in seed.accounts:
            account = _ensure_account(session, spec)
            username_to_account[spec.username] = account
            if spec.access_token and with_tokens:
                _ensure_token(session, spec.access_token, account.id)

        session.flush()

        for follow in seed.follows:
            follower = username_to_account.get(follow.follower)
            target = username_to_account.get(follow.following)
            if follower is not None and target is not None:
                do_follow(session, follower, target)

        # ``ref`` -> created Status, so a later seed status can quote an earlier one.
        ref_to_status: dict[str, Status] = {}
        for status_spec in seed.statuses:
            author = username_to_account.get(status_spec.account)
            if author is not None:
                status = _ensure_status(session, author, status_spec, ref_to_status)
                if status_spec.ref is not None:
                    ref_to_status[status_spec.ref] = status

        for announcement_spec in seed.announcements:
            _ensure_announcement(session, announcement_spec)

        session.commit()


def _ensure_account(session: Session, spec: SeedAccount) -> Account:
    """Find-or-create an account matched on (username, domain)."""
    existing = session.scalar(select(Account).where(Account.username == spec.username, Account.domain == spec.domain))
    if existing is not None:
        return existing
    # Local accounts get a synthetic email for the admin API; remote accounts don't.
    email = spec.email or (f"{spec.username}@local" if spec.domain is None else None)
    account = Account(
        username=spec.username,
        domain=spec.domain,
        display_name=spec.display_name or spec.username,
        note=spec.note or "",
        locked=spec.locked,
        bot=spec.bot,
        created_at=utcnow(),
        fields=[],
        email=email,
        role=spec.role,
    )
    session.add(account)
    session.flush()
    return account


def _ensure_token(session: Session, access_token: str, account_id: int) -> None:
    """Find-or-create an oauth token matched on the token string."""
    existing = session.scalar(select(OAuthToken).where(OAuthToken.access_token == access_token))
    if existing is None:
        session.add(
            OAuthToken(
                access_token=access_token,
                account_id=account_id,
                scopes=list(_DEFAULT_SCOPES),
                created_at=utcnow(),
            )
        )


def _ensure_status(
    session: Session,
    account: Account,
    spec: SeedStatus,
    ref_to_status: dict[str, Status],
) -> Status:
    """Find-or-create a seed status matched on (account_id, text).

    When ``spec.quotes`` names a known ref, the new status quotes that status;
    when ``spec.reply_to`` names a known ref, it replies to that status.
    """
    existing = session.scalar(select(Status).where(Status.account_id == account.id, Status.text == spec.text))
    if existing is not None:
        return existing
    quoted = ref_to_status.get(spec.quotes) if spec.quotes is not None else None
    parent = ref_to_status.get(spec.reply_to) if spec.reply_to is not None else None
    status = Status(
        account_id=account.id,
        content=render_status_html(spec.text),
        text=spec.text,
        visibility=spec.visibility,
        created_at=utcnow(),
        edit_history=[],
        quoted_status_id=quoted.id if quoted is not None else None,
        in_reply_to_id=parent.id if parent is not None else None,
    )
    session.add(status)
    session.flush()
    attach_mentions_and_tags(session, status.id, account.id, spec.text)
    return status


def _ensure_announcement(session: Session, spec: SeedAnnouncement) -> None:
    """Find-or-create a seed announcement matched on its content."""
    existing = session.scalar(select(Announcement).where(Announcement.content == spec.content))
    if existing is not None:
        return
    now = utcnow()
    session.add(
        Announcement(
            content=spec.content,
            starts_at=spec.starts_at,
            ends_at=spec.ends_at,
            all_day=spec.all_day,
            published=spec.published,
            published_at=now,
            updated_at=now,
        )
    )
