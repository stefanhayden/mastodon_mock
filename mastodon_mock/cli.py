"""Command-line entry point for mastodon_mock."""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
from pathlib import Path

import orjson
import uvicorn

from mastodon_mock.__about__ import __version__
from mastodon_mock.app import create_app
from mastodon_mock.config import PRESETS, MastodonMockConfig, SampleDataConfig, demo_config


def _silence_proactor_connection_reset() -> None:
    """Swallow the benign ``ConnectionResetError`` logged by asyncio's Windows Proactor loop.

    When a client (browser/Electron app) aborts a connection instead of closing it
    cleanly — routine for SSE/streaming clients with short read timeouts —
    ``ProactorBasePipeTransport._call_connection_lost`` logs a scary-looking
    "Exception in callback" traceback for WinError 10054. It's not a server bug; Linux's
    selector loop already ignores the equivalent. Only patched on win32.
    """
    if sys.platform != "win32":
        return

    from asyncio.proactor_events import _ProactorBasePipeTransport

    # pylint: disable=protected-access
    original = getattr(_ProactorBasePipeTransport, "_call_connection_lost")  # noqa: B009

    def _quiet_call_connection_lost(self: object, exc: BaseException | None) -> None:
        with contextlib.suppress(ConnectionResetError):
            original(self, exc)

    setattr(_ProactorBasePipeTransport, "_call_connection_lost", _quiet_call_connection_lost)  # noqa: B010


def main(argv: list[str] | None = None) -> None:
    """Run the mastodon_mock CLI."""
    parser = argparse.ArgumentParser(
        prog="mastodon_mock",
        description="Stateful Mastodon mock server.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="Run the mock HTTP server")
    serve.add_argument("--config", default=None, help="Path to a .mastodon_mock.toml config file")
    serve.add_argument("--host", default=None, help="Host to bind (overrides $HOST and config)")
    serve.add_argument("--port", type=int, default=None, help="Port to bind (overrides $PORT and config)")
    serve.add_argument(
        "--domain",
        default=None,
        help="Public domain used to build avatar/header/status URLs (overrides config and auto-derivation)",
    )
    serve.add_argument("--in-memory", action="store_true", help="Force in-memory SQLite")
    serve.add_argument(
        "--demo",
        action="store_true",
        help="Seed a rich demo community (accounts, follows, quotes, announcements, rules, ToS)",
    )
    serve.add_argument("--ssl-keyfile", default=None, help="Path to a TLS private key (enables HTTPS)")
    serve.add_argument("--ssl-certfile", default=None, help="Path to a TLS certificate (enables HTTPS)")
    serve.add_argument("--ssl-keyfile-password", default=None, help="Password for an encrypted --ssl-keyfile")
    serve.add_argument(
        "--log-level",
        default=None,
        choices=["critical", "error", "warning", "info", "debug", "trace"],
        help=(
            "Uvicorn log verbosity (default: info). "
            "Use 'debug' or 'trace' to see headers/bodies while debugging a client."
        ),
    )

    upgrade = sub.add_parser("db", help="Database commands")
    upgrade.add_argument("db_command", choices=["upgrade"], help="Database subcommand")
    upgrade.add_argument("--config", default=None)

    pw = sub.add_parser("set-password", help="Set a local account's password (for [auth] require_password)")
    pw.add_argument("username", help="Local account username")
    pw.add_argument("--config", default=None, help="Path to a .mastodon_mock.toml config file")
    pw.add_argument("--database", default=None, help="SQLite path to write into (overrides config)")
    pw.add_argument("--create", action="store_true", help="Create the account if it doesn't exist")
    pw.add_argument(
        "--role", choices=["user", "moderator", "admin", "owner"], default=None, help="Also set the account's role"
    )
    pw.add_argument("--email", default=None, help="Also set the account's email")
    pw.add_argument("--password-stdin", action="store_true", help="Read the password from stdin instead of prompting")

    gen = sub.add_parser("gen-data", help="Bulk-generate throwaway sample data")
    gen.add_argument("--config", default=None, help="Path to a .mastodon_mock.toml config file")
    gen.add_argument("--preset", choices=sorted(PRESETS), default=None, help="Named size preset")
    gen.add_argument("--accounts", type=int, default=None)
    gen.add_argument("--statuses-per-account", type=int, default=None)
    gen.add_argument("--followers-per-account", type=int, default=None)
    gen.add_argument("--favourites-per-account", type=int, default=None)
    gen.add_argument("--seed", type=int, default=None, help="RNG seed for a reproducible cohort")
    gen.add_argument("--database", default=None, help="SQLite path to write into (overrides config)")
    gen.add_argument("--in-memory", action="store_true", help="Use an in-memory DB (only useful for a quick benchmark)")
    gen.add_argument(
        "--api",
        metavar="URL",
        default=None,
        help="Generate through a running server's HTTP API instead of writing directly to SQLite",
    )
    gen.add_argument("--yes", action="store_true", help="Skip the confirmation prompt for large shapes")
    gen.add_argument("--json", action="store_true", help="Emit the generation report as JSON")

    cmp = sub.add_parser(
        "compare-openapi",
        help="Diff the mock's OpenAPI contract against the upstream Mastodon schema",
    )
    cmp.add_argument(
        "--truth",
        default="mastodon-openapi/dist/schema.json",
        help="Path to the ground-truth Mastodon OpenAPI schema",
    )
    cmp.add_argument(
        "--mock",
        default=None,
        help="Path to a mock OpenAPI schema (default: generate from the live app in-process)",
    )
    cmp.add_argument("--format", choices=["text", "json", "markdown"], default="text")
    cmp.add_argument("--out", default=None, help="Write the report to this path instead of stdout")
    cmp.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when unexpected drift is found (mock-only/truth-only/param mismatch)",
    )

    args = parser.parse_args(argv)

    if args.command == "serve":
        _serve(args)
    elif args.command == "db":
        _db(args)
    elif args.command == "set-password":
        _set_password(args)
    elif args.command == "gen-data":
        _gen_data(args)
    elif args.command == "compare-openapi":
        _compare_openapi(args)
    else:
        parser.print_help()


def _env_port() -> int | None:
    """Read a port from ``$PORT`` (set by most PaaS hosts). Ignore a blank/invalid value."""
    raw = os.environ.get("PORT", "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        print(f"Ignoring non-integer $PORT={raw!r}.", file=sys.stderr)
        return None


def _serve(args: argparse.Namespace) -> None:
    """Run the uvicorn server with the resolved config."""
    config = MastodonMockConfig.load(args.config)
    if getattr(args, "demo", False):
        config = demo_config(config)
    if args.in_memory:
        config.database.path = ":memory:"
    # Bind precedence: explicit --host/--port flag > $HOST/$PORT env > config default.
    # The env fallback lets PaaS hosts (Render/Railway/Koyeb/...) that inject $PORT run
    # `mastodon_mock serve` with no extra flags. See spec/publish.md, Track 2.
    host = args.host or os.environ.get("HOST") or config.server.host
    port = args.port or _env_port() or config.server.port
    if getattr(args, "domain", None):
        # Explicit --domain always wins, e.g. a named cert + hosts-file entry
        # (see scripts/gen_dev_cert.sh) for clients that reject bare IPs/localhost.
        config.domain = args.domain
    elif config.domain == MastodonMockConfig.model_fields["domain"].default:  # pylint: disable=unsubscriptable-object
        # Unset by the user: derive a reachable domain from the actual bind address
        # instead of the unresolvable "mock.local" default, so avatar/header
        # placeholders and status permalinks (built from config.domain) are
        # clickable/loadable rather than 404ing against a host that doesn't resolve.
        # 0.0.0.0 is a valid intentional bind address for container deployments;
        # use a browser-reachable hostname when deriving links from it.
        display_host = "localhost" if host in ("127.0.0.1", "0.0.0.0") else host  # nosec B104
        config.domain = f"{display_host}:{port}"
    if config.url_scheme is None:
        config.url_scheme = "https" if getattr(args, "ssl_certfile", None) else "http"
    app = create_app(config)
    _silence_proactor_connection_reset()
    uvicorn.run(
        app,
        host=host,
        port=port,
        # The app's own middleware sets "Server: Mastodon" to match a real instance;
        # disable uvicorn's default "Server: uvicorn" so responses don't carry both.
        server_header=False,
        ssl_keyfile=getattr(args, "ssl_keyfile", None),
        ssl_certfile=getattr(args, "ssl_certfile", None),
        ssl_keyfile_password=getattr(args, "ssl_keyfile_password", None),
        log_level=getattr(args, "log_level", None),
    )


_MIN_PASSWORD_LENGTH = 12


def _set_password(args: argparse.Namespace) -> None:
    """Set (and optionally create) a local account's password in a file-backed database."""
    import getpass

    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from mastodon_mock.db.base import Base, init_engine
    from mastodon_mock.db.models import Account, utcnow
    from mastodon_mock.passwords import hash_password

    config = MastodonMockConfig.load(args.config)
    if args.database is not None:
        config.database.path = args.database
    if config.database.path == ":memory:":
        sys.exit("set-password needs a file-backed database: pass --database or set [database].path.")

    if args.password_stdin:
        password = sys.stdin.readline().rstrip("\r\n")
    else:
        password = getpass.getpass("New password: ")
        if getpass.getpass("Repeat password: ") != password:
            sys.exit("Passwords don't match.")
    if len(password) < _MIN_PASSWORD_LENGTH:
        sys.exit(f"Password must be at least {_MIN_PASSWORD_LENGTH} characters.")

    engine = init_engine(config.database)
    Base.metadata.create_all(engine, checkfirst=True)
    with Session(engine) as session:
        account = session.scalar(select(Account).where(Account.username == args.username, Account.domain.is_(None)))
        if account is None:
            if not args.create:
                sys.exit(f"No local account {args.username!r}. Pass --create to make one.")
            account = Account(
                username=args.username,
                display_name=args.username,
                created_at=utcnow(),
                fields=[],
                email=f"{args.username}@local",
            )
            session.add(account)
        account.password_hash = hash_password(password)
        if args.role is not None:
            account.role = args.role
        if args.email is not None:
            account.email = args.email
        session.commit()
        print(f"Password set for @{account.username} (role: {account.role}).")
    engine.dispose()


def _gen_data(args: argparse.Namespace) -> None:
    """Generate a sample cohort into the configured database."""
    from mastodon_mock.db.sample_data import estimate_rows, generate_sample_data

    config = MastodonMockConfig.load(args.config)
    cfg = _build_sample_config(args, config.sample_data)

    if args.api is not None and (args.in_memory or args.database is not None):
        print("--api cannot be combined with --database or --in-memory.")
        sys.exit(2)

    if args.in_memory:
        config.database.path = ":memory:"
    elif args.database is not None:
        config.database.path = args.database

    if args.api is None and config.database.path == ":memory:" and not args.in_memory:
        print("Refusing to write into an in-memory DB (it vanishes on exit). Use --in-memory or --database.")
        sys.exit(2)

    rows = estimate_rows(cfg)
    target = f"HTTP API at {args.api}" if args.api is not None else config.database.path
    print(f"Target: {cfg.accounts} accounts, ~{rows:,} total rows -> {target}")
    if rows > 250_000 and not args.yes:
        reply = input("This is a large shape and may be slow. Continue? [y/N] ").strip().lower()
        if reply not in ("y", "yes"):
            print("Aborted.")
            return

    if args.api is not None:
        from mastodon_mock.api_sample_data import generate_sample_data_via_api

        report = generate_sample_data_via_api(args.api, cfg)
    else:
        from mastodon_mock.db.base import Base, init_engine

        engine = init_engine(config.database)
        Base.metadata.create_all(engine, checkfirst=True)
        report = generate_sample_data(engine, cfg)

    if args.json:
        # pylint: disable=no-member
        print(orjson.dumps(report.to_dict(), option=orjson.OPT_INDENT_2).decode())
        return
    print(f"\nGenerated in {report.total_seconds:.2f}s ({report.rows_per_second:,.0f} rows/s):")
    print(f"  accounts:      {report.accounts:>10,}")
    print(f"  relationships: {report.relationships:>10,}")
    print(f"  statuses:      {report.statuses:>10,}")
    print(f"  favourites:    {report.favourites:>10,}")
    print(f"  bookmarks:     {report.bookmarks:>10,}")
    print(f"  notifications: {report.notifications:>10,}")
    print(f"  total rows:    {report.total_rows:>10,}")


def _build_sample_config(args: argparse.Namespace, default: SampleDataConfig) -> SampleDataConfig:
    """Merge CLI flags over a preset (or the configured default)."""
    base = PRESETS[args.preset] if args.preset else default
    overrides = {
        "accounts": args.accounts,
        "statuses_per_account": args.statuses_per_account,
        "followers_per_account": args.followers_per_account,
        "favourites_per_account": args.favourites_per_account,
        "seed": args.seed,
    }
    data = base.model_dump()
    data.update({k: v for k, v in overrides.items() if v is not None})
    return SampleDataConfig.model_validate(data)


def _compare_openapi(args: argparse.Namespace) -> None:
    """Diff the mock's OpenAPI contract against the upstream Mastodon schema."""
    from mastodon_mock import openapi_compare as oc

    truth = oc.load_spec(args.truth)
    # When --mock isn't given, generate from the live app so the report reflects
    # exactly what's served rather than a possibly-stale on-disk snapshot.
    mock = oc.load_spec(args.mock) if args.mock is not None else create_app().openapi()

    report = oc.compare_specs(truth, mock)
    renderer = {"text": oc.render_text, "json": oc.render_json, "markdown": oc.render_markdown}[args.format]
    output = renderer(report)

    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
        print(f"Wrote {args.format} report to {args.out}")
    else:
        print(output, end="")

    if args.strict and report.has_unexpected_drift:
        sys.exit(1)


def _db(args: argparse.Namespace) -> None:
    """Run a database management command (alembic upgrade head)."""
    if args.db_command == "upgrade":
        from mastodon_mock.db.migrations import upgrade_database

        config = MastodonMockConfig.load(args.config)
        try:
            upgrade_database(config.database)
        except ValueError as exc:
            sys.exit(str(exc))
        print("Database upgraded to head.")


if __name__ == "__main__":
    main()
