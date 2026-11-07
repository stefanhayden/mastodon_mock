"""Apps & auth endpoints. See spec/04-auth.md.

Security is faked: tokens are random strings mapped 1:1 to accounts. The
``client_credentials`` grant always succeeds; ``password`` is rejected (matching
current Mastodon). ``authorization_code`` is supported via a bare-bones account
picker at ``/oauth/authorize`` (see "Optional: permissive code flow" in spec/04-auth.md)
for GUI clients (Whalebird, Fedistar) that do the full browser-redirect login.

With ``[auth] require_password`` the code flow is real instead: a password login
form, single-use random codes bound to the client and redirect URI, and client
secret checks on exchange (see spec/04-auth.md "Password mode").
"""

from __future__ import annotations

import base64
import binascii
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from html import escape
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlencode

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import or_

from mastodon_mock.db.models import Account, OAuthApp, OAuthToken, utcnow
from mastodon_mock.deps import Config, CurrentToken, DbSession, RequiredAccount
from mastodon_mock.passwords import hash_password, verify_password
from mastodon_mock.routers.helpers import read_body
from mastodon_mock.serializers.accounts import serialize_account
from mastodon_mock.serializers.common import account_acct, sid

if TYPE_CHECKING:
    from mastodon_mock.faults import FaultStore

router = APIRouter(tags=["oauth"])

_DEFAULT_SCOPES = ["read", "write", "follow", "push"]


def _token() -> str:
    """Generate a random opaque token."""
    return secrets.token_urlsafe(32)


_AUTHORIZE_CODE_PREFIX = "mockcode_"


def _encode_authorize_code(username: str) -> str:
    """Build an opaque authorization code that round-trips the chosen username.

    There's no server-side "pending authorization" table, so the code is
    self-describing (mock-only shortcut — see spec/04-auth.md's "Optional: permissive
    code flow" section).
    """
    return f"{_AUTHORIZE_CODE_PREFIX}{username}"


def _decode_authorize_code(code: str) -> str | None:
    """Recover the username from a code minted by ``_encode_authorize_code``."""
    if not code.startswith(_AUTHORIZE_CODE_PREFIX):
        return None
    return code[len(_AUTHORIZE_CODE_PREFIX) :] or None


_OOB_REDIRECT = "urn:ietf:wg:oauth:2.0:oob"
_CODE_TTL = timedelta(minutes=10)


@dataclass(frozen=True)
class PendingCode:
    """A password-mode authorization code awaiting exchange."""

    account_id: int
    client_id: str
    redirect_uri: str
    scopes: list[str]
    expires_at: datetime


@router.post("/api/v1/apps")
async def create_app(request: Request, db: DbSession) -> dict[str, Any]:
    """Register an OAuth application.

    Accepts form or JSON bodies (see ``read_body``) — some clients (e.g. Whalebird)
    POST this as JSON rather than the form-encoded body Mastodon.py sends, and
    ``redirect_uris``/``scopes`` may arrive as either a string or a JSON array.
    """
    body = await read_body(request)
    client_name = body.get("client_name")
    if not client_name:
        raise HTTPException(status_code=422, detail="Validation failed: Client name can't be blank")
    redirect_uris = body.get("redirect_uris", "urn:ietf:wg:oauth:2.0:oob")
    scopes = body.get("scopes", "read")
    website = body.get("website")

    app = OAuthApp(
        client_id=_token(),
        client_secret=_token(),
        name=client_name,
        website=website,
        redirect_uris=redirect_uris if isinstance(redirect_uris, list) else str(redirect_uris).split("\n"),
        scopes=scopes if isinstance(scopes, list) else str(scopes).split(" "),
    )
    db.add(app)
    db.commit()
    return {
        "id": sid(app.id),
        "name": app.name,
        "website": app.website,
        "redirect_uri": app.redirect_uris[0] if app.redirect_uris else "urn:ietf:wg:oauth:2.0:oob",
        "redirect_uris": app.redirect_uris,
        "client_id": app.client_id,
        "client_secret": app.client_secret,
        "vapid_key": "mock-vapid-key",
        "scopes": app.scopes,
    }


@router.post("/oauth/token")
async def oauth_token(request: Request, db: DbSession, config: Config) -> dict[str, Any]:
    """Issue tokens. Only ``client_credentials``, ``authorization_code``, and ``refresh_token`` succeed."""
    form = await read_body(request)
    grant_type = form.get("grant_type")

    if grant_type == "authorization_code" and config.auth.require_password:
        return _exchange_pending_code(request, db, form)

    if grant_type == "client_credentials":
        app = _resolve_app(db, form.get("client_id"))
        token = OAuthToken(
            access_token=_token(),
            app_id=app.id if app else None,
            account_id=None,
            scopes=str(form.get("scope", "read")).split(" "),
            created_at=utcnow(),
        )
        db.add(token)
        db.commit()
        return _token_response(token)

    if grant_type == "refresh_token":
        refresh_token = form.get("refresh_token")
        # Guard the empty case: `== None` compiles to IS NULL and would match any token.
        existing = (
            db.query(OAuthToken).filter(OAuthToken.refresh_token == refresh_token).first() if refresh_token else None
        )
        if existing is None:
            raise HTTPException(status_code=400, detail="invalid_grant")
        existing.access_token = _token()
        db.commit()
        return _token_response(existing)

    if grant_type == "authorization_code":
        username = _decode_authorize_code(str(form.get("code", "")))
        account = db.query(Account).filter(Account.username == username, Account.domain.is_(None)).first()
        if username is None or account is None:
            raise HTTPException(status_code=400, detail="invalid_grant")
        from mastodon_mock.moderation import account_is_active

        if not account_is_active(account):
            raise HTTPException(status_code=403, detail="Your login is currently disabled")
        app = _resolve_app(db, form.get("client_id"))
        token = OAuthToken(
            access_token=_token(),
            app_id=app.id if app else None,
            account_id=account.id,
            scopes=app.scopes if app else list(_DEFAULT_SCOPES),
            created_at=utcnow(),
        )
        db.add(token)
        db.commit()
        return _token_response(token)

    raise HTTPException(status_code=400, detail="unsupported_grant_type")


@router.get("/oauth/authorize")
def oauth_authorize_picker(
    request: Request,
    db: DbSession,
    config: Config,
    client_id: str,
    redirect_uri: str,
    response_type: str,
    scope: str = "read",
    state: str = "",
) -> Response:
    """Render a bare-bones account picker for the authorization-code flow.

    Real clients with a browser-redirect login (Whalebird, Fedistar) hit this with
    ``response_type=code``. There's no session/login concept in the mock, so instead
    of guessing an account, this renders a tiny HTML page listing local accounts;
    picking one POSTs back here and issues the redirect with a code.

    In password mode it renders a username/password form instead, and never lists accounts.
    """
    del response_type
    if config.auth.require_password:
        app = _require_registered_client(db, client_id, redirect_uri)
        _requested_scopes(app, scope)
        return _login_form(client_id, redirect_uri, scope, state)

    accounts = db.query(Account).filter(Account.domain.is_(None)).order_by(Account.id).all()

    options = "\n".join(
        f'<li><button type="submit" name="username" value="{escape(a.username)}">'
        f"{escape(a.display_name or a.username)} (@{escape(a.username)})</button></li>"
        for a in accounts
    )
    html = f"""<!doctype html>
<html><head><title>mastodon_mock: choose an account</title></head>
<body>
<h1>mastodon_mock</h1>
<p>Authorize this app as:</p>
<form method="post" action="/oauth/authorize">
  {_hidden_fields(client_id, redirect_uri, scope, state)}
  <ul>{options}</ul>
</form>
</body></html>"""
    return Response(content=html, media_type="text/html")


@router.post("/oauth/authorize")
async def oauth_authorize_submit(request: Request, db: DbSession, config: Config) -> Response:
    """Issue a code for the chosen account and redirect (or display it for ``oob``)."""
    form = await request.form()
    if config.auth.require_password:
        return _password_authorize(request, db, {k: str(v) for k, v in form.items()})

    username = str(form.get("username", ""))
    redirect_uri = str(form.get("redirect_uri") or _OOB_REDIRECT)
    return _code_redirect(redirect_uri, _encode_authorize_code(username), str(form.get("state") or ""))


def _password_authorize(request: Request, db: DbSession, form: dict[str, str]) -> Response:
    """Password mode: check credentials, then mint a single-use code bound to client + redirect."""
    from mastodon_mock.moderation import account_is_active

    client_id = form.get("client_id", "")
    redirect_uri = form.get("redirect_uri", "")
    scope = form.get("scope", "read")
    state = form.get("state", "")
    app = _require_registered_client(db, client_id, redirect_uri)
    scopes = _requested_scopes(app, scope)

    login = form.get("username", "").strip()
    password = form.get("password", "")
    account = (
        db.query(Account)
        .filter(Account.domain.is_(None), or_(Account.username == login, Account.email == login))
        .first()
        if login
        else None
    )
    if account is None:
        hash_password(password)  # same work as a real check, so timing doesn't reveal usernames
    if account is None or not verify_password(password, account.password_hash):
        return _login_form(client_id, redirect_uri, scope, state, error="Invalid username or password.", status=401)
    if not account_is_active(account):
        return _login_form(client_id, redirect_uri, scope, state, error="Your login is currently disabled.", status=403)

    codes: dict[str, PendingCode] = request.app.state.authorize_codes
    now = utcnow()
    for stale in [c for c, p in codes.items() if p.expires_at <= now]:
        del codes[stale]
    code = secrets.token_urlsafe(32)
    codes[code] = PendingCode(
        account_id=account.id,
        client_id=client_id,
        redirect_uri=redirect_uri,
        scopes=scopes,
        expires_at=now + _CODE_TTL,
    )
    return _code_redirect(redirect_uri, code, state)


def _exchange_pending_code(request: Request, db: DbSession, form: dict[str, Any]) -> dict[str, Any]:
    """Password mode: redeem a code once, for the client and redirect URI it was issued to."""
    from mastodon_mock.moderation import account_is_active

    codes: dict[str, PendingCode] = request.app.state.authorize_codes
    pending = codes.pop(str(form.get("code", "")), None)
    if pending is None or pending.expires_at <= utcnow():
        raise HTTPException(status_code=400, detail="invalid_grant")
    app = _authenticate_client(request, db, form)
    if app is None:
        raise HTTPException(status_code=401, detail="invalid_client")
    if app.client_id != pending.client_id or str(form.get("redirect_uri", "")) != pending.redirect_uri:
        raise HTTPException(status_code=400, detail="invalid_grant")
    account = db.get(Account, pending.account_id)
    if account is None:
        raise HTTPException(status_code=400, detail="invalid_grant")
    if not account_is_active(account):
        raise HTTPException(status_code=403, detail="Your login is currently disabled")
    token = OAuthToken(
        access_token=_token(),
        app_id=app.id,
        account_id=account.id,
        scopes=pending.scopes,
        created_at=utcnow(),
    )
    db.add(token)
    db.commit()
    return _token_response(token)


def _require_registered_client(db: DbSession, client_id: str, redirect_uri: str) -> OAuthApp:
    """The app for ``client_id``, provided ``redirect_uri`` is one it registered."""
    app = _resolve_app(db, client_id)
    if app is None:
        raise HTTPException(status_code=401, detail="Client authentication failed due to unknown client")
    if redirect_uri not in app.redirect_uris:
        raise HTTPException(status_code=400, detail="The redirect uri included is not valid.")
    return app


def _requested_scopes(app: OAuthApp, scope: str) -> list[str]:
    """The requested scopes, which must all have been registered by the app."""
    scopes = scope.split() or list(app.scopes)
    if not set(scopes) <= set(app.scopes):
        raise HTTPException(status_code=400, detail="The requested scope is invalid, unknown, or malformed.")
    return scopes


def _authenticate_client(request: Request, db: DbSession, form: dict[str, Any]) -> OAuthApp | None:
    """Check the client secret, sent in the body or as HTTP Basic auth."""
    client_id = form.get("client_id")
    client_secret = form.get("client_secret")
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("basic "):
        try:
            decoded = base64.b64decode(auth[6:], validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            return None
        raw_id, _, raw_secret = decoded.partition(":")
        client_id, client_secret = unquote(raw_id), unquote(raw_secret)
    app = _resolve_app(db, client_id)
    if app is None or not client_secret or not hmac.compare_digest(app.client_secret, str(client_secret)):
        return None
    return app


def _hidden_fields(client_id: str, redirect_uri: str, scope: str, state: str) -> str:
    """The OAuth parameters carried through the authorize form, HTML-escaped."""
    fields = {"client_id": client_id, "redirect_uri": redirect_uri, "scope": scope, "state": state}
    return "\n  ".join(f'<input type="hidden" name="{name}" value="{escape(value)}">' for name, value in fields.items())


def _login_form(
    client_id: str, redirect_uri: str, scope: str, state: str, *, error: str = "", status: int = 200
) -> Response:
    """Password mode's sign-in page."""
    message = f'<p role="alert">{escape(error)}</p>' if error else ""
    html = f"""<!doctype html>
<html><head><title>mastodon_mock: sign in</title></head>
<body>
<h1>mastodon_mock</h1>
<p>Sign in to authorize this app.</p>
{message}
<form method="post" action="/oauth/authorize">
  {_hidden_fields(client_id, redirect_uri, scope, state)}
  <label>Username or email <input name="username" autocomplete="username" required></label>
  <label>Password <input name="password" type="password" autocomplete="current-password" required></label>
  <button type="submit">Authorize</button>
</form>
</body></html>"""
    return HTMLResponse(content=html, status_code=status)


def _code_redirect(redirect_uri: str, code: str, state: str) -> Response:
    """Send the code back to the client, or show it for the ``oob`` redirect."""
    if redirect_uri == _OOB_REDIRECT:
        return HTMLResponse(f"<!doctype html><html><body><p>Authorization code: {escape(code)}</p></body></html>")
    params = {"code": code, **({"state": state} if state else {})}
    separator = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(url=f"{redirect_uri}{separator}{urlencode(params)}", status_code=302)


@router.post("/oauth/revoke")
async def oauth_revoke(request: Request, db: DbSession) -> dict[str, Any]:
    """Revoke (delete) a token."""
    form = await request.form()
    token_value = form.get("token")
    if token_value:
        existing = db.query(OAuthToken).filter(OAuthToken.access_token == token_value).first()
        if existing is not None:
            db.delete(existing)
            db.commit()
    return {}


@router.get("/.well-known/oauth-authorization-server")
def oauth_server_info(request: Request) -> dict[str, Any]:
    """Advertise the OAuth server config (no password grant; matches 4.4+)."""
    base = f"{request.url.scheme}://{request.url.netloc}"
    return {
        "issuer": f"{base}/",
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/oauth/token",
        "revocation_endpoint": f"{base}/oauth/revoke",
        "userinfo_endpoint": f"{base}/oauth/userinfo",
        "app_registration_endpoint": f"{base}/api/v1/apps",
        "service_documentation": "https://docs.joinmastodon.org/",
        "scopes_supported": _DEFAULT_SCOPES,
        "response_types_supported": ["code"],
        "response_modes_supported": ["query", "fragment", "form_post"],
        "grant_types_supported": ["authorization_code", "client_credentials"],
        "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post"],
        "code_challenge_methods_supported": ["S256"],
    }


@router.get("/oauth/userinfo")
def oauth_userinfo(account: RequiredAccount, config: Config) -> dict[str, Any]:
    """Minimal OIDC-ish claims derived from the authed account."""
    acct = account_acct(account.username, account.domain)
    return {
        "sub": sid(account.id),
        "preferred_username": account.username,
        "name": account.display_name,
        "profile": f"{config.base_url}/@{acct}",
    }


@router.get("/api/v1/apps/verify_credentials")
def app_verify_credentials(db: DbSession, token: CurrentToken) -> dict[str, Any]:
    """Return info about the app for the bearer token."""
    if token is None or token.app_id is None:
        raise HTTPException(status_code=401, detail="The access token is invalid")
    app = db.get(OAuthApp, token.app_id)
    if app is None:
        raise HTTPException(status_code=401, detail="The access token is invalid")
    return {
        "name": app.name,
        "website": app.website,
        "scopes": app.scopes,
        "vapid_key": "mock-vapid-key",
    }


@router.post("/api/v1/accounts", status_code=200)
async def create_account(request: Request, db: DbSession, token: CurrentToken, config: Config) -> dict[str, Any]:
    """Self-service signup: create an account + user token.

    Honours ``registration_approval_required`` (the account starts pending) and, in
    password mode, ``registrations_enabled``.
    """
    if config.auth.require_password and not config.registrations_enabled:
        raise HTTPException(status_code=403, detail="Registrations are not open on this server")
    form = await request.form()
    username = form.get("username")
    email = form.get("email")
    password = form.get("password")
    agreement = str(form.get("agreement", "")).lower() in ("true", "1", "on")

    if not username or not email or not password:
        raise HTTPException(status_code=422, detail="Validation failed")
    if not agreement:
        raise HTTPException(status_code=422, detail="agreement must be accepted")
    from mastodon_mock.moderation import signup_block_reason

    blocked = signup_block_reason(db, str(email), request.client.host if request.client else None)
    if blocked is not None:
        raise HTTPException(status_code=422, detail=blocked)

    existing = db.query(Account).filter(Account.username == username, Account.domain.is_(None)).first()
    if existing is not None:
        raise HTTPException(
            status_code=422, detail={"error": "Validation failed", "details": {"username": [{"error": "ERR_TAKEN"}]}}
        )

    account = Account(
        username=username,
        display_name=username,
        created_at=utcnow(),
        fields=[],
        email=str(email),
        ip=request.client.host if request.client else None,
        approved=not config.registration_approval_required,
        invite_request=str(form.get("reason") or "") or None,
        password_hash=hash_password(str(password)),
    )
    db.add(account)
    db.flush()
    new_token = OAuthToken(
        access_token=_token(),
        app_id=token.app_id if token else None,
        account_id=account.id,
        scopes=list(_DEFAULT_SCOPES),
        created_at=utcnow(),
    )
    db.add(new_token)
    db.commit()
    return _token_response(new_token)


@router.post("/api/v1/emails/confirmations", status_code=200)
def email_resend_confirmation() -> dict[str, Any]:
    """Stub: accept and do nothing."""
    return {}


@router.post("/api/v1/_mock/login")
async def mock_login(request: Request, db: DbSession) -> dict[str, Any]:
    """Mock-only shortcut: issue a user token for a username."""
    body = await request.json()
    username = body.get("username")
    account = db.query(Account).filter(Account.username == username, Account.domain.is_(None)).first()
    if account is None:
        raise HTTPException(status_code=404, detail="Unknown account")
    from mastodon_mock.moderation import account_is_active

    if not account_is_active(account):
        raise HTTPException(status_code=403, detail="Your login is currently disabled")
    token = OAuthToken(
        access_token=_token(),
        account_id=account.id,
        scopes=list(_DEFAULT_SCOPES),
        created_at=utcnow(),
    )
    db.add(token)
    db.commit()
    return _token_response(token)


@router.post("/api/v1/_mock/dev_user", status_code=200)
async def mock_create_dev_user(request: Request, db: DbSession) -> dict[str, Any]:
    """Mock-only: create a fresh local account (+ token) for the dev login UI.

    Body (all optional): ``{"username": str, "display_name": str, "admin": bool}``.
    When ``username`` is omitted a unique one is generated. ``admin`` sets the account
    ``role`` to ``admin`` (the mock does not enforce roles, but the admin panel uses it
    to decide what to show).
    """
    try:
        body = await request.json()
    except Exception:
        body = {}

    admin = bool(body.get("admin"))
    prefix = "admin" if admin else "user"
    username = (body.get("username") or "").strip() or f"{prefix}_{secrets.token_hex(3)}"

    existing = db.query(Account).filter(Account.username == username, Account.domain.is_(None)).first()
    if existing is not None:
        raise HTTPException(status_code=422, detail=f"Username {username!r} already taken")

    account = Account(
        username=username,
        display_name=(body.get("display_name") or "").strip() or username,
        created_at=utcnow(),
        fields=[],
        email=f"{username}@local",
        role="admin" if admin else "user",
    )
    db.add(account)
    db.flush()
    token = OAuthToken(
        access_token=_token(),
        account_id=account.id,
        scopes=list(_DEFAULT_SCOPES),
        created_at=utcnow(),
    )
    db.add(token)
    db.commit()
    return {
        "id": sid(account.id),
        "username": account.username,
        "display_name": account.display_name,
        "role": account.role,
        "access_token": token.access_token,
    }


@router.get("/api/v1/_mock/dev_users")
def mock_list_dev_users(db: DbSession) -> list[dict[str, Any]]:
    """Mock-only: list local accounts that have a usable token, for the dev login UI.

    Returns the most-recent token per account so a tester can click to autofill it.
    """
    rows = (
        db.query(Account, OAuthToken)
        .join(OAuthToken, OAuthToken.account_id == Account.id)
        .filter(Account.domain.is_(None))
        .order_by(OAuthToken.created_at.desc())
        .all()
    )
    seen: set[int] = set()
    out: list[dict[str, Any]] = []
    for account, token in rows:
        if account.id in seen:
            continue
        seen.add(account.id)
        out.append(
            {
                "id": sid(account.id),
                "username": account.username,
                "display_name": account.display_name,
                "role": account.role,
                "access_token": token.access_token,
            }
        )
    return out


@router.post("/api/v1/_mock/reset", status_code=200)
def mock_reset(request: Request) -> dict[str, Any]:
    """Mock-only: drop+recreate all tables and re-apply seed data."""
    from mastodon_mock.db.base import Base
    from mastodon_mock.db.seed import apply_seed_data

    engine = request.app.state.engine
    config = request.app.state.config
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    apply_seed_data(engine, config.seed)
    store = getattr(request.app.state, "fault_store", None)
    if store is not None:
        store.clear()
    return {"ok": True}


# Server-side caps so a browser can't request a "large"/"huge" cohort that wedges the
# single shared SQLite connection. The "medium" preset (~300k rows, ~3s) is allowed; the
# CLI is uncapped. See spec/09-sample-data-and-perf.md.
_SAMPLE_MAX_ACCOUNTS = 2000
_SAMPLE_MAX_ROWS = 750_000


@router.post("/api/v1/_mock/sample_data", status_code=200)
async def mock_sample_data(request: Request) -> dict[str, Any]:
    """Mock-only: bulk-generate a throwaway sample cohort into the running DB.

    Body (all optional): a ``preset`` name and/or individual ``SampleDataConfig``
    fields, merged over the server's configured default profile. Capped so a browser
    can't request a runaway shape.
    """
    from mastodon_mock.config import PRESETS, SampleDataConfig
    from mastodon_mock.db.sample_data import estimate_rows, generate_sample_data

    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}

    config = request.app.state.config
    base = config.sample_data
    preset = body.get("preset")
    if preset is not None:
        if preset not in PRESETS:
            raise HTTPException(status_code=422, detail=f"Unknown preset {preset!r}")
        base = PRESETS[preset]

    data = base.model_dump()
    fields = set(SampleDataConfig.model_fields)
    data.update({k: v for k, v in body.items() if k in fields})
    cfg = SampleDataConfig.model_validate(data)

    if cfg.accounts > _SAMPLE_MAX_ACCOUNTS or estimate_rows(cfg) > _SAMPLE_MAX_ROWS:
        raise HTTPException(
            status_code=422,
            detail=f"Shape too large for the browser endpoint (max {_SAMPLE_MAX_ACCOUNTS} accounts / "
            f"~{_SAMPLE_MAX_ROWS:,} rows). Use the gen-data CLI for larger cohorts.",
        )

    report = generate_sample_data(request.app.state.engine, cfg)
    return {"report": report.to_dict()}


@router.post("/api/v1/_mock/faults", status_code=200)
async def mock_add_fault(request: Request) -> dict[str, Any]:
    """Mock-only: register a fault-injection rule. See spec/fault_injection.md."""
    store: FaultStore | None = getattr(request.app.state, "fault_store", None)
    if store is None:
        raise HTTPException(status_code=404, detail="Fault injection is not enabled")
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="Rule body must be an object")
    try:
        rule = store.add(body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return rule.to_dict()


@router.get("/api/v1/_mock/faults")
def mock_list_faults(request: Request) -> list[dict[str, Any]]:
    """Mock-only: list active fault rules with their remaining budgets."""
    store: FaultStore | None = getattr(request.app.state, "fault_store", None)
    if store is None:
        raise HTTPException(status_code=404, detail="Fault injection is not enabled")
    return [rule.to_dict() for rule in store.list()]


@router.delete("/api/v1/_mock/faults/{rule_id}", status_code=200)
def mock_delete_fault(rule_id: str, request: Request) -> dict[str, Any]:
    """Mock-only: remove one fault rule by id."""
    store = getattr(request.app.state, "fault_store", None)
    if store is None:
        raise HTTPException(status_code=404, detail="Fault injection is not enabled")
    removed = store.remove(rule_id)
    if not removed:
        raise HTTPException(status_code=404, detail=f"No fault rule {rule_id!r}")
    return {"ok": True}


@router.delete("/api/v1/_mock/faults", status_code=200)
def mock_clear_faults(request: Request) -> dict[str, Any]:
    """Mock-only: clear all fault rules."""
    store = getattr(request.app.state, "fault_store", None)
    if store is None:
        raise HTTPException(status_code=404, detail="Fault injection is not enabled")
    store.clear()
    return {"ok": True}


def _resolve_app(db: DbSession, client_id: Any) -> OAuthApp | None:
    """Look up an app by client_id, if provided."""
    if not client_id:
        return None
    return db.query(OAuthApp).filter(OAuthApp.client_id == client_id).first()


def _token_response(token: OAuthToken) -> dict[str, Any]:
    """Shape an oauth token response."""
    return {
        "access_token": token.access_token,
        # "Bearer" is the OAuth token_type label, not a secret.
        "token_type": "Bearer",  # nosec B105
        "scope": " ".join(token.scopes),
        "created_at": int(token.created_at.timestamp()),
    }


def serialize_authed_account(db: DbSession, account: Account, config: Config) -> dict[str, Any]:
    """Convenience used by tests/other routers (kept here for cohesion)."""
    return serialize_account(db, account, config, with_source=True)
