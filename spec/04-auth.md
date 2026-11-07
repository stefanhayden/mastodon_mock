# Fake Auth & Multi-Account Model

## Principle

Security is **faked, not enforced**. The goal is that Mastodon.py's client-side code
paths (`create_app`, `log_in`, `Mastodon(access_token=...)`, sending
`Authorization: Bearer <token>` on every request) all work *mechanically*, and that a
test can have several `Mastodon` client instances, each "logged in" as a different
seeded account, interacting with shared state.

There is **no password checking, no real client secret validation, no scope
enforcement** beyond the bare minimum needed for Mastodon.py to not raise
`MastodonAPIError`/`MastodonIllegalArgumentError` on its own client-side checks (e.g.
`log_in()` checks that `oauth_authorization_server_info()["grant_types_supported"]`
contains `authorization_code` before attempting a code-flow login).

## Flow 1: App registration (`create_app`)

```python
client_id, client_secret = Mastodon.create_app(
    "my_test_app",
    api_base_url="http://127.0.0.1:3000",
)
```

→ `POST /api/v1/apps` with `client_name`, `scopes`, `redirect_uris`, `website`.

Mock behavior:

- Insert a row into `oauth_apps` with random `client_id`/`client_secret` (e.g.
  `secrets.token_urlsafe(32)`).
- Return `{"id": ..., "name": ..., "client_id": ..., "client_secret": ..., "redirect_uri": ..., "redirect_uris": [...], "vapid_key": "mock-vapid-key", "scopes": [...]}`.

## Flow 2: Pre-seeded accounts + direct token construction (recommended for tests)

Because the config file ([01-architecture.md](01-architecture.md)) lets you declare:

```toml
[[tool.mastodon_mock.seed.accounts]]
username = "alice"
access_token = "alice_token"

[[tool.mastodon_mock.seed.accounts]]
username = "bob"
access_token = "bob_token"
```

...a consuming test suite can **skip the OAuth dance entirely**:

```python
alice = Mastodon(access_token="alice_token", api_base_url="http://127.0.0.1:3000")
bob = Mastodon(access_token="bob_token", api_base_url="http://127.0.0.1:3000")

alice.account_follow(bob.account_verify_credentials().id)
```

Mock behavior: on server startup, `apply_seed_data()` inserts one `accounts` row and one
`oauth_tokens` row (with `access_token` = the configured string, `account_id` = the new
account, `app_id = NULL`, `scopes = ["read", "write", "follow", "push"]`) per seeded
account. This is the **primary supported workflow** for multi-account simulation.

## Flow 3: OAuth code/client-credentials flow (`log_in`)

For tests that specifically want to exercise Mastodon.py's `log_in()` /
`create_account()` (the actual library functions under test, e.g. if the consuming
project is testing *its own* login wrapper around Mastodon.py):

```python
m = Mastodon(client_id=client_id, client_secret=client_secret, api_base_url="http://127.0.0.1:3000")
# username/password flow is REMOVED in real Mastodon 4.4+, so mock does NOT support it either,
# per the "if mastodon.py doesn't support it, neither do we" rule for *current* version.
```

Since password-flow login is gone in current Mastodon, and the code-flow requires a
browser redirect (out of scope for a headless mock), **`log_in()` is supported only via
a mock-specific shortcut**: `POST /oauth/token` with `grant_type=client_credentials`
always succeeds (per real Mastodon behavior — this grant doesn't authenticate a *user*,
just the app) and returns an app-only token (`account_id = NULL` in `oauth_tokens`).

For *user*-scoped tokens, the supported pattern is Flow 2 (pre-seeded
`access_token`), or a mock-only convenience endpoint:

```
POST /api/v1/_mock/login
  { "username": "alice" }
  -> { "access_token": "<random>", "token_type": "Bearer", "scope": "read write follow push", "created_at": ... }
```

This mirrors what `/oauth/token` returns shape-wise (so it could even be wired up if a
future need arises for `grant_type=password` to "just work" against the mock — see
"Optional: permissive password grant" below) but is exposed as a clearly-named
mock-only endpoint so it's obvious in test code that this is a mock-specific shortcut,
not something that would work against a real server.

### `/oauth/token` behavior summary

| `grant_type` | Mock behavior |
|--------------|----------------|
| `client_credentials` | Always succeeds. Creates/returns an app-only `oauth_tokens` row (`account_id=NULL`). Used by `create_account()`'s step 1 and by any code that just wants an app token. |
| `authorization_code` | **Not supported** — returns 400/`invalid_grant`. (Real Mastodon requires the browser redirect step; mocking a fake "code" with no corresponding auth-server-side state isn't worth it. If a consuming suite needs this, add a `/api/v1/_mock/login`-issued opaque code that this grant type will accept 1:1 — see "Optional" below.) |
| `password` | **Not supported** — matches Mastodon 4.4+ (`invalid_grant`), consistent with "if mastodon.py doesn't support it, we don't either" since Mastodon.py's `log_in()` itself raises before sending the request if the (current-version) server doesn't advertise `password` in `grant_types_supported`. |
| `refresh_token` | **Supported** — looks up the `oauth_tokens` row by a stored `refresh_token` value (add `refresh_token` column to `oauth_tokens`), issues a new `access_token`, same `account_id`/`scopes`. |

### Optional: permissive code flow for completeness

If a later phase wants `log_in(code=...)` to fully work end-to-end (e.g. to test an
app's OAuth callback handler against the mock):

- `GET /oauth/authorize?client_id=&redirect_uri=&scope=&response_type=code&...` — mock
  renders/returns a trivial response containing a `code` query param appended to
  `redirect_uri` (for `redirect_uri != urn:ietf:wg:oauth:2.0:oob`) or just displays the
  code (for `oob`). The "code" is just `f"mockcode_{account.username}"` for whichever
  account the request is "logged in as" — which itself requires some session concept.
- This is **deferred**; flag as a stretch goal, not required for the primary use case
  (multi-account write/read simulation via pre-seeded tokens).

### Password mode (`[auth] require_password = true`)

For running the mock on a publicly reachable host (e.g. as a live test instance for
a third-party admin tool). Off by default; every other section describes the
default mode. When on:

- `GET /oauth/authorize` checks `client_id` and that `redirect_uri` is one the app
  registered, then renders a username-or-email + password form. It never lists accounts.
- `POST /oauth/authorize` verifies the password (scrypt hash in `accounts.password_hash`)
  and mints a random code, held in memory for 10 minutes and redeemable once. Requested
  scopes must be a subset of the app's registered scopes.
- `/oauth/token` with `authorization_code` accepts only those codes, and only with the
  matching `client_id`, `client_secret` (body or HTTP Basic) and `redirect_uri`.
  `mockcode_*` codes are refused.
- `/api/v1/_mock/*` returns 404 and `/_ui/` isn't mounted.
- `/api/v1/admin/*` and `/api/v2/admin/*` return 403 unless the token's account has the
  `moderator`, `admin` or `owner` role.
- Seed `access_token`s are not created (they're well-known strings like `mock_token`).
- `POST /api/v1/accounts` returns 403 when `registrations_enabled = false`.
- `[auth] permissive = true` is refused at startup.

Passwords are set with the CLI against a file-backed database:

```bash
mastodon_mock set-password admin --create --role admin --database ./mock.sqlite
```

Pending authorization codes live in process memory, so run a single worker.

## Bearer token resolution (every authenticated request)

`mastodon_mock/deps.py`:

```python
def get_current_token(authorization: str | None = Header(default=None), db: Session = Depends(get_db)) -> OAuthToken | None:
    if authorization is None:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return db.scalar(select(OAuthToken).where(OAuthToken.access_token == token))

def get_current_account(token: OAuthToken | None = Depends(get_current_token), db: Session = Depends(get_db)) -> Account | None:
    if token is None or token.account_id is None:
        if config.auth.permissive:
            return db.scalars(select(Account)).first()
        return None
    return db.get(Account, token.account_id)
```

Endpoints that **require** auth (per Mastodon.py docstrings, e.g.
`account_verify_credentials`, `status_post`, `account_follow`, ...) raise
`HTTPException(401)` if `get_current_account()` returns `None`. Mastodon.py surfaces
this as `MastodonAPIError`/`MastodonUnauthorizedError` — matches real-server behavior
for an unauthenticated write.

Endpoints that work with **or without** auth (e.g. `status()`, `timeline_public()`)
just treat `account=None` as "no relationship/ownership context" (e.g.
`status.favourited`/`status.reblogged` are omitted or `False`).

## Scopes

`oauth_tokens.scopes` is stored but **not enforced by default**. Mastodon.py sends
`scope = " ".join(scopes)` and reads back `response["scope"]`; the mock just echoes
whatever scopes the seed/app config declares (default: `["read", "write", "follow", "push"]`, i.e. `_DEFAULT_SCOPES`-equivalent).

To test scope-restricted behavior (e.g. a `read`-only token getting 403 on writes),
set `config.auth.enforce_scopes = true` (`[tool.mastodon_mock.auth] enforce_scopes`).
When enabled, `mastodon_mock/middleware.py` enforces a **coarse** mapping: write
methods (`POST`/`PUT`/`PATCH`/`DELETE`) require the `write` scope, everything else
requires `read` (a broad scope covers its `scope:subscope` children). On mismatch the
mock returns `403 {"error": "This action is outside the authorized scopes"}`.
Auth/oauth bootstrap, instance metadata, and `/api/v1/_mock/*` paths are exempt.

## Rate limiting

Off by default. Set `[tool.mastodon_mock.ratelimit] enabled = true` (with optional
`limit`, `window_seconds`) to make the mock return `429` + `X-RateLimit-Limit`/
`-Remaining`/`-Reset` headers after `limit` requests per token per fixed window —
enough to exercise Mastodon.py's `ratelimit_method` (`throw`/`wait`/`pace`).

## `account_verify_credentials` / `me()`

`GET /api/v1/accounts/verify_credentials` returns the `Account` for
`get_current_account()`, with the extra `source` block (`CredentialAccountSource`:
`privacy`, `sensitive`, `language`, `note`, `fields`, `follow_requests_count`).
401 if unauthenticated.

## Self-service signup (`create_account`)

`POST /api/v1/accounts` (after the client-credentials `/oauth/token` step):

- Validates `username`/`email`/`password` are present and `agreement=true` (mirrors
  `AccountCreationError` shape for `return_detailed_error=True` callers, but the mock
  doesn't implement the full `ERR_*` taxonomy beyond `ERR_TAKEN` for duplicate
  `username`).
- On success: creates an `accounts` row, an `oauth_tokens` row (bound to the
  client-credentials token's `app_id`, `account_id` = new account), and returns
  `{"access_token": ..., "token_type": "Bearer", "scope": "...", "created_at": ...}`.
- Stores the email, client IP, `reason` (as the admin API's `invite_request`) and a hash
  of the password. With `registration_approval_required = true` the account starts
  pending (`approved=false`), so it shows under `pending=true` in the admin API and can't
  sign in until approved.
- No email confirmation step is modeled — the returned token is immediately usable
  (real Mastodon would require email confirmation before the token works for most
  scopes; mock skips this for simplicity, since `email_resend_confirmation` is a stub
  anyway).
