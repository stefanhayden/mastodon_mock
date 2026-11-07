# Changelog
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]
### Added
- `[auth] require_password` password mode for running the mock on a public host: password sign-in with single-use codes, client secret checks, staff-only admin API, and no `/api/v1/_mock/*` or `/_ui/`.
- `mastodon_mock set-password` CLI command to create accounts and set passwords.

### Changed
- Requires Python 3.11 or newer; Python 3.10 is no longer supported.
- Signup stores the email, IP, reason and password hash, and honours `registration_approval_required`.

### Fixed
- `grant_type=refresh_token` without a `refresh_token` no longer re-keys and returns an arbitrary existing token.
- The authorize page escapes its query parameters and account names.

## [0.6.1] - 2026-09-11
### Fixed
- Database upgrades resolve migrations from the installed package and honor the configured SQLite path.
- Generated asset and API URLs use the CLI listener's HTTP/HTTPS scheme, with an explicit proxy override.
- Corrected UI and deployment documentation, synchronized release metadata, and made local audit and documentation checks portable to Windows.
- OAuth `authorization_code` token exchange now rejects disabled/suspended accounts with 403 (previously only `/api/v1/_mock/login` enforced this).
- Test website surfaces explore page
- Test website retweets fixed
- Swagger docs page now groups endpoints by category
- Errors return correct schema now.

### Changed
- Restored the bundled lite UI to its pre-Mawkingbird scope; standalone product builds and publishing belong to the Mawkingbird repository.
- Refreshed npm dependencies within their existing version ranges and narrowed source-distribution UI inputs to exclude dependency trees and scratch files.

## [0.6.0] - 2026-06-20
### Added
- Seed data for more data types

### Fixed
- More endpoints have behavior
- 404 on colossal IDs instead of 500s.
- Docs match code again

## [0.5.0] - 2026-06-20
### Added
- OpenAPI contract comparison: `mastodon_mock compare-openapi` (and `make compare-openapi`) diffs the mock's published OpenAPI against the upstream Mastodon schema (`mastodon-openapi/dist/schema.json`), writing `spec/openapi_compare_report.md`. A reviewed allow-list (`tests/openapi/allowlist.py`) plus `tests/test_openapi_contract.py` turns this into a CI guard rail against contract drift. An "API Docs" link in the UI's More menu surfaces the always-served Swagger UI at `/docs`. See `spec/openapi_support.md` (Phases 1 & 2).
- OpenAPI contract fuzzing (opt-in): `make openapi-fuzz` (or `uv run pytest -m contract`) drives [Schemathesis](https://schemathesis.readthedocs.io/) at the running mock using the upstream schema as the oracle. Default mode guarantees the mock never 500s on the shared read-only surface; `CONTRACT_STRICT=1` additionally checks full response-shape conformance (the gap finder for Phase 4). Lives behind a `contract` extra/marker so the default suite stays fast. See `spec/openapi_support.md` (Phase 3).
- Automated drift detection: a weekly + per-PR `openapi-drift` GitHub Actions workflow clones the (untracked) upstream schema generator, runs the comparison, enforces the contract tests, and uploads the report. New contributor guide [`docs/extending/openapi-sync.md`](https://github.com/matthewdeanmartin/mastodon_mock/blob/main/docs/extending/openapi-sync.md) documents the whole OpenAPI sync workflow. See `spec/openapi_support.md` (Phase 5).

### Fixed
- Hardened pagination/limit handling against malformed query params (found by the new OpenAPI fuzzing): a non-numeric `max_id`/`min_id`/`since_id` no longer raises `ValueError` → HTTP 500 (the cursor is ignored, matching Mastodon), and an out-of-range `limit`/`offset` no longer overflows SQLite's 64-bit INTEGER → HTTP 500 (it's clamped). Affects timelines, search, trends, suggestions, conversations, account search, and the directory. New shared helpers `clamp_limit` / `clamp_offset` / `coerce_cursor` in `pagination.py`.
- Fixed layout of UI.
- Small perf improvements.

## [0.4.0] - 2026-06-19
### Fixed
- Conversation IDs were a hyphen-joined composite of participant account IDs instead of a real numeric status ID, breaking clients (e.g. mastui) that parse conversation IDs as integers.

### Added
- UI supports showing more features.

## [0.3.0] - 2026-06-18
### Added
- Support for python 3.10, 3.11, 3.12
- Websockets support
- More oauth login machinery

### Fixed
- Missing paging for some end points
- More missing endpoints
- Missing v1/v2 endpoints

## [0.2.0] - 2026-06-17
### Added
- More endpoints, SSE style streaming.
- More features visible via UI

## [0.1.0] - 2026-06-16
### Added
- Added UI
- Added bulk sample data generation

### Fixed
- Improved performance

## [0.0.1] - 2026-06-15
### Added
- Initial release of `mastodon_mock`, a stateful FastAPI + SQLite mock of the Mastodon REST API, driveable by real clients including Mastodon.py.
- Auth & apps: app registration, `client_credentials` and `refresh_token` OAuth grants, token revocation, self-service account creation, and OAuth server metadata.
- Accounts: profiles, `verify_credentials`, relationships, follow/unfollow, mute/block, and account search.
- Statuses: posting, editing, deleting, replies, context, reblogs, favourites, bookmarks, polls, scheduled statuses, and quotes.
- Timelines: home, public (with `local`/`remote` filters), hashtag, and list timelines, with cursor pagination and `Link` headers.
- Lists, content filters (v1 + v2), notifications (including grouped notifications), conversations, media upload/update, and instance metadata.
- `mastodon_mock serve` and `mastodon_mock db upgrade` CLI commands with TOML-based configuration and Alembic migrations.
- Admin / moderation API (`mastodon/admin.py`): account listing & moderation actions (enable/approve/reject/silence/suspend/sensitive/delete), reports (create + admin queue/assign/resolve/reopen), domain blocks/allows, email-domain & canonical-email blocks, IP blocks, and shaped stubs for admin trends/measures/dimensions/retention.
- Auth remains faked (no role enforcement), consistent with the project's non-goals.
- Hashtag follow/unfollow (`tag_follow`/`tag_unfollow`), `tag()` fetch, and a real `followed_tags` listing backed by a new `followed_tags` table.
- Featured hashtags: `featured_tag_create`/`featured_tag_delete`, the newer `tag_feature`/`tag_unfeature` aliases, `featured_tag_suggestions`, and `featured_tags` (own + per-account), backed by a new `featured_tags` table. `tag()` now reports `featuring`, and usage counts are derived from the account's statuses.
- Quote moderation (Mastodon 4.5+): `status_quote_revoke` (sets a quote's `state` to `revoked` and hides the quoted status) and `status_update_quote_approval_policy` (`public`/`followers`/`nobody`, forced to `nobody` for private/direct statuses). Statuses now expose `quote.state` and `quote_approval_policy`.
- Discovery surfaces now return realistic, data-derived content instead of empty stubs: instance `activity`/`peers`/`domain_blocks`, trending tags & statuses, follow suggestions, endorsements, featured tags (own + per-account) and followed tags. Custom emojis and translation languages return correctly-shaped static data, the notification policy includes `for_bots`, and the notification-requests family is fully wired. Shapes were validated against a live `mastodon.social`.
