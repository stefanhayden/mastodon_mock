"""``[auth] require_password``: the lock-down for running the mock on a public host."""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx2 as httpx
import pytest

from mastodon_mock.app import create_app
from mastodon_mock.cli import main
from mastodon_mock.config import AuthConfig, DatabaseConfig, MastodonMockConfig, SeedAccount, SeedConfig
from mastodon_mock.passwords import hash_password, verify_password
from mastodon_mock.testing import MockServer

REDIRECT = "https://client.example/callback"
ADMIN_PASSWORD = "correct horse battery staple"  # nosec B105
USER_PASSWORD = "ordinary user password"  # nosec B105


def _set_password(db_path: Path, monkeypatch: pytest.MonkeyPatch, username: str, password: str, *flags: str) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(f"{password}\n"))
    main(["set-password", username, "--database", str(db_path), "--password-stdin", "--create", *flags])


def _config(db_path: Path, **overrides: object) -> MastodonMockConfig:
    return MastodonMockConfig(
        database=DatabaseConfig(path=str(db_path)),
        auth=AuthConfig(require_password=True),
        seed=SeedConfig(accounts=[SeedAccount(username="testuser", access_token="mock_token")]),  # nosec B106
        **overrides,  # type: ignore[arg-type]
    )


@pytest.fixture()
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "locked.sqlite"
    _set_password(path, monkeypatch, "boss", ADMIN_PASSWORD, "--role", "admin", "--email", "boss@example.test")
    _set_password(path, monkeypatch, "pleb", USER_PASSWORD)
    return path


@pytest.fixture()
def locked(db_path: Path) -> Iterator[str]:
    with MockServer(config=_config(db_path)) as server:
        yield server.base_url


def _register_app(base: str) -> tuple[str, str]:
    resp = httpx.post(
        f"{base}/api/v1/apps",
        data={"client_name": "fediponics", "redirect_uris": REDIRECT, "scopes": "read write admin:read"},
    )
    resp.raise_for_status()
    body = resp.json()
    return body["client_id"], body["client_secret"]


def _authorize(
    base: str, client_id: str, username: str, password: str, scope: str = "read admin:read"
) -> httpx.Response:
    return httpx.post(
        f"{base}/oauth/authorize",
        data={
            "client_id": client_id,
            "redirect_uri": REDIRECT,
            "scope": scope,
            "state": "s&t",
            "username": username,
            "password": password,
        },
    )


def _code(resp: httpx.Response) -> str:
    assert resp.status_code == 302, resp.text
    query = parse_qs(urlparse(resp.headers["location"]).query)
    assert query["state"] == ["s&t"]
    return query["code"][0]


def _exchange(base: str, client_id: str, client_secret: str, code: str, redirect_uri: str = REDIRECT) -> httpx.Response:
    return httpx.post(
        f"{base}/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        },
    )


def _login(base: str, username: str, password: str) -> str:
    client_id, client_secret = _register_app(base)
    resp = _exchange(base, client_id, client_secret, _code(_authorize(base, client_id, username, password)))
    resp.raise_for_status()
    return str(resp.json()["access_token"])


def test_password_hash_round_trip() -> None:
    stored = hash_password("hunter2")
    assert verify_password("hunter2", stored)
    assert not verify_password("hunter3", stored)
    assert not verify_password("hunter2", None)
    assert not verify_password("hunter2", "garbage")


def test_authorize_page_asks_for_a_password_and_lists_no_accounts(locked: str) -> None:
    client_id, _ = _register_app(locked)
    resp = httpx.get(
        f"{locked}/oauth/authorize",
        params={"client_id": client_id, "redirect_uri": REDIRECT, "response_type": "code", "state": '"><script>'},
    )
    assert resp.status_code == 200
    assert 'type="password"' in resp.text
    assert "boss" not in resp.text
    assert "<script>" not in resp.text


def test_authorize_rejects_unknown_client_and_unregistered_redirect(locked: str) -> None:
    client_id, _ = _register_app(locked)
    params = {"client_id": client_id, "redirect_uri": "https://evil.example/", "response_type": "code"}
    assert httpx.get(f"{locked}/oauth/authorize", params=params).status_code == 400
    params = {"client_id": "nope", "redirect_uri": REDIRECT, "response_type": "code"}
    assert httpx.get(f"{locked}/oauth/authorize", params=params).status_code == 401


def test_authorize_rejects_scopes_the_app_did_not_register(locked: str) -> None:
    client_id, _ = _register_app(locked)
    assert _authorize(locked, client_id, "boss", ADMIN_PASSWORD, scope="admin:write").status_code == 400


def test_wrong_password_or_unknown_user_gets_no_code(locked: str) -> None:
    client_id, _ = _register_app(locked)
    for username, password in (("boss", "wrong password"), ("nobody", ADMIN_PASSWORD)):
        resp = _authorize(locked, client_id, username, password)
        assert resp.status_code == 401
        assert "location" not in resp.headers
        assert "Invalid username or password." in resp.text


def test_code_flow_issues_a_token_with_the_requested_scopes(locked: str) -> None:
    client_id, client_secret = _register_app(locked)
    resp = _exchange(locked, client_id, client_secret, _code(_authorize(locked, client_id, "boss", ADMIN_PASSWORD)))
    assert resp.status_code == 200
    assert resp.json()["scope"] == "read admin:read"
    me = httpx.get(
        f"{locked}/api/v1/accounts/verify_credentials",
        headers={"Authorization": f"Bearer {resp.json()['access_token']}"},
    ).json()
    assert me["username"] == "boss"


def test_email_works_as_the_login(locked: str) -> None:
    client_id, _ = _register_app(locked)
    assert _authorize(locked, client_id, "boss@example.test", ADMIN_PASSWORD).status_code == 302


def test_codes_are_single_use_and_bound_to_client_and_redirect(locked: str) -> None:
    client_id, client_secret = _register_app(locked)
    other_id, other_secret = _register_app(locked)

    code = _code(_authorize(locked, client_id, "boss", ADMIN_PASSWORD))
    assert _exchange(locked, client_id, client_secret, code).status_code == 200
    assert _exchange(locked, client_id, client_secret, code).status_code == 400

    code = _code(_authorize(locked, client_id, "boss", ADMIN_PASSWORD))
    assert _exchange(locked, other_id, other_secret, code).status_code == 400

    code = _code(_authorize(locked, client_id, "boss", ADMIN_PASSWORD))
    assert _exchange(locked, client_id, "wrong-secret", code).status_code == 401

    code = _code(_authorize(locked, client_id, "boss", ADMIN_PASSWORD))
    assert _exchange(locked, client_id, client_secret, code, redirect_uri="https://evil.example/").status_code == 400


def test_client_secret_accepted_as_basic_auth(locked: str) -> None:
    client_id, client_secret = _register_app(locked)
    code = _code(_authorize(locked, client_id, "boss", ADMIN_PASSWORD))
    resp = httpx.post(
        f"{locked}/oauth/token",
        data={"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT},
        auth=(client_id, client_secret),
    )
    assert resp.status_code == 200


def test_self_describing_mock_codes_are_refused(locked: str) -> None:
    client_id, client_secret = _register_app(locked)
    assert _exchange(locked, client_id, client_secret, "mockcode_boss").status_code == 400


def test_seed_tokens_are_not_created(locked: str) -> None:
    resp = httpx.get(f"{locked}/api/v1/accounts/verify_credentials", headers={"Authorization": "Bearer mock_token"})
    assert resp.status_code == 401


def test_mock_only_routes_are_gone(locked: str) -> None:
    assert httpx.post(f"{locked}/api/v1/_mock/login", json={"username": "boss"}).status_code == 404
    assert httpx.get(f"{locked}/api/v1/_mock/dev_users").status_code == 404
    assert httpx.post(f"{locked}/api/v1/_mock/reset").status_code == 404
    assert httpx.get(f"{locked}/_ui/").status_code == 404


def test_admin_api_requires_a_staff_role(locked: str) -> None:
    admin = {"Authorization": f"Bearer {_login(locked, 'boss', ADMIN_PASSWORD)}"}
    user = {"Authorization": f"Bearer {_login(locked, 'pleb', USER_PASSWORD)}"}
    assert httpx.get(f"{locked}/api/v1/admin/accounts", headers=admin).status_code == 200
    assert httpx.get(f"{locked}/api/v1/admin/accounts", headers=user).status_code == 403
    assert httpx.get(f"{locked}/api/v2/admin/accounts", headers=user).status_code == 403
    assert httpx.get(f"{locked}/api/v1/admin/accounts").status_code == 401


def test_signup_stores_the_password_and_honours_approval(db_path: Path) -> None:
    with MockServer(config=_config(db_path, registration_approval_required=True)) as server:
        base = server.base_url
        resp = httpx.post(
            f"{base}/api/v1/accounts",
            data={
                "username": "newbie",
                "email": "newbie@example.test",
                "password": "a new user password",
                "agreement": "true",
                "reason": "I grow tomatoes",
            },
        )
        assert resp.status_code == 200
        admin = {"Authorization": f"Bearer {_login(base, 'boss', ADMIN_PASSWORD)}"}
        pending = httpx.get(f"{base}/api/v1/admin/accounts", params={"pending": "true"}, headers=admin).json()
        assert [(a["username"], a["email"], a["invite_request"]) for a in pending] == [
            ("newbie", "newbie@example.test", "I grow tomatoes")
        ]
        # Pending accounts can't sign in until approved, like real Mastodon.
        client_id, _ = _register_app(base)
        assert _authorize(base, client_id, "newbie", "a new user password").status_code == 403


def test_signup_refused_when_registrations_are_closed(db_path: Path) -> None:
    with MockServer(config=_config(db_path, registrations_enabled=False)) as server:
        resp = httpx.post(
            f"{server.base_url}/api/v1/accounts",
            data={"username": "x", "email": "x@example.test", "password": "whatever pw", "agreement": "true"},
        )
        assert resp.status_code == 403


def test_permissive_cannot_be_combined_with_password_mode(tmp_path: Path) -> None:
    config = _config(tmp_path / "x.sqlite")
    config.auth.permissive = True
    with pytest.raises(ValueError, match="permissive"):
        create_app(config)


def test_set_password_requires_existing_account_without_create(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("long enough password\n"))
    with pytest.raises(SystemExit, match="--create"):
        main(["set-password", "ghost", "--database", str(tmp_path / "x.sqlite"), "--password-stdin"])


def test_set_password_rejects_short_passwords(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("short\n"))
    with pytest.raises(SystemExit, match="at least"):
        main(["set-password", "x", "--database", str(tmp_path / "x.sqlite"), "--password-stdin", "--create"])


def test_refresh_grant_without_a_refresh_token_is_refused(live_server: str) -> None:
    # Before the fix, a missing refresh_token matched (and re-keyed) the first token in the table.
    resp = httpx.post(f"{live_server}/oauth/token", data={"grant_type": "refresh_token"})
    assert resp.status_code == 400
