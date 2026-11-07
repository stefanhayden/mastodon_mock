# Mastodon Mock

[![PyPI version](https://badge.fury.io/py/mastodon_mock.svg)](https://badge.fury.io/py/mastodon_mock)
[![CI](https://github.com/matthewdeanmartin/mastodon_mock/actions/workflows/build.yml/badge.svg)](https://github.com/matthewdeanmartin/mastodon_mock/actions/workflows/build.yml)
[![Python versions](https://img.shields.io/pypi/pyversions/mastodon_mock.svg)](https://pypi.org/project/mastodon_mock/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://github.com/matthewdeanmartin/mastodon_mock/blob/main/LICENSE)

`mastodon_mock` is a stateful, local Mastodon REST API for tests and development. It runs a
real FastAPI server backed by in-memory or file-backed SQLite, so client code can post
statuses, follow accounts, build timelines, manage lists and filters, file reports, and
exercise OAuth/admin flows without touching a live instance.

Use it when you want the confidence of real HTTP and persisted state, without flaky
network tests, rate limits, public test posts, or hand-written response mocks.

Highlights:

- Real Mastodon-shaped HTTP API, tested against [Mastodon.py](https://github.com/halcy/Mastodon.py).
- Fast disposable SQLite state, with deterministic seed data and reset support.
- Pytest fixtures, a context manager, and decorator sugar for zero-boilerplate tests.
- Bulk sample-data generation for demos, UI work, and performance checks.
- Bundled web client and admin UI at `/_ui/` when the package includes the built frontend.
- Admin/moderation endpoints for account actions, reports, domain blocks, email blocks, and IP blocks.

## Installation

```bash
pipx install mastodon_mock
```

Or with pip:

```bash
pip install mastodon_mock
```

## Usage

Run the mock server:

```bash
mastodon_mock serve --in-memory
```

Point a client at it:

```python
from mastodon import Mastodon

client = Mastodon(access_token="mock_token", api_base_url="http://127.0.0.1:3000")
client.status_post("hello from a mock!")
```

Open `http://127.0.0.1:3000/_ui/` for the bundled browser UI when it is available. The UI
covers timelines, compose/reply, boost/favourite/bookmark, **quote posts**, threads,
profiles, search, lists, an **About this server** page (instance rules, terms of service,
custom emojis), an **announcements banner** (dismiss + emoji reactions), and an admin
panel (accounts, reports, domains, **announcements**, **trends**).

For a quick, good-looking demo with a small community already populated — multiple
accounts, follows, a quote post, announcements, rules, and terms of service — run:

```bash
mastodon_mock serve --in-memory --demo
```

For a populated local instance, generate a throwaway cohort into a SQLite file and point
your config's `[database].path` at it:

```bash
mastodon_mock gen-data --preset small --database ./mastodon_mock.sqlite --yes
```

### HTTPS (required for most Mastodon clients, e.g. Whalebird, Fedistar)

Desktop/mobile Mastodon clients connect to the entered domain over HTTPS regardless of
any scheme you type, so pointing one at a plain-HTTP `serve` instance fails with
`Invalid HTTP request received` in the server log (the client's TLS handshake hitting an
HTTP parser).

A plain openssl self-signed cert is often not enough — Electron-based clients
(Whalebird, Fedistar) commonly refuse to connect at all rather than warn-and-allow like a
browser does. Use [mkcert](https://github.com/FiloSottile/mkcert) instead: it installs a
local CA into your OS trust store and issues certs signed by it, so clients trust the
connection outright.

```bash
make dev-cert      # generates .dev_certs/ via mkcert if installed, else falls back to openssl
make serve-https    # dev-cert + serve --in-memory --demo --port 3443 over HTTPS
```

Or run the steps by hand:

```bash
mkcert -install   # one-time: adds a local CA to the OS/browser trust stores
mkcert -key-file .dev_certs/localhost-key.pem -cert-file .dev_certs/localhost-cert.pem \
  localhost 127.0.0.1 ::1

mastodon_mock serve --in-memory --demo --port 3443 \
  --ssl-keyfile .dev_certs/localhost-key.pem --ssl-certfile .dev_certs/localhost-cert.pem
```

Point the client at `https://127.0.0.1:3443` (or `localhost`). Without mkcert installed,
`gen-dev-cert.sh` falls back to an openssl self-signed cert, but expect Electron clients
to refuse it outright rather than just warn.

See [HTTPS and Real Mastodon Clients](https://github.com/matthewdeanmartin/mastodon_mock/blob/main/docs/usage/https-and-real-clients.md) for the full
walkthrough (mkcert setup, troubleshooting, and how `serve` derives a reachable
domain for avatar/header/status URLs).

### Running on a public host (password mode)

By default anyone who can reach the server can sign in as any account. To expose it on
the internet, turn on password mode and give accounts passwords:

```toml
# .mastodon_mock.toml
domain = "mock.example.com"
url_scheme = "https"
registration_approval_required = true

[database]
path = "./mock.sqlite"

[auth]
require_password = true

[seed]
accounts = []
```

```bash
mastodon_mock set-password admin --create --role admin --email you@example.com --config .mastodon_mock.toml
mastodon_mock serve --config .mastodon_mock.toml
```

Sign-in then needs a password, the `/api/v1/_mock/*` helpers and `/_ui/` are switched off,
and the admin API requires a staff role. See "Password mode" in `spec/04-auth.md`.
Run it behind a TLS-terminating proxy, as a single worker.

## Why I think this works

- 100s of unit tests
  - Ordinary pytests
  - Integration tests via live server
  - Integration tests via Angular client
- Unit tests using mastodon-py
- Hand tested with half a dozen clients
- Bundled with an Angular client written specifically to test the mock server
- Schema compared with the reverse engineered OpenAPI schema
- Tested against five of my own Mastodon projects
- Test data generation uses mastodon-mock

## Tested on...

- Tuba - Works, but had to use port 443 and edit hosts file so cert matched domain (mock.local in my case)
- Whalebird- Works!
- Fedistar - couldn't get it to trust local certificate
- Dowstodon - Works!
- Various web - They need to make a serverside request to my machine. Can't do it, without putting a mock server on public web.
- toot - can't test on Windows
- Sengi - couldn't get this to work? But not sure the Sengi app is finished either (as of 6/2026)?

## Documentation

See the [documentation](https://mastodon-mock.readthedocs.io/en/latest/)
for configuration, fixtures, endpoint coverage, data generation, and admin UI/API details.

## Contributing

See [CONTRIBUTING.md](https://github.com/matthewdeanmartin/mastodon_mock/blob/main/docs/extending/CONTRIBUTING.md).

## License

MIT. See [LICENSE](https://github.com/matthewdeanmartin/mastodon_mock/blob/main/LICENSE).

## Changelog

See [CHANGELOG.md](https://github.com/matthewdeanmartin/mastodon_mock/blob/main/CHANGELOG.md).

## Python 3.15 wheels-only verification — 2026-10-04

Windows Python 3.15.0rc3 passes 434 configured test cases with two skip records using wheel-installed dependency prereleases, including Pydantic 2.14.0b2. Python 3.14 matches; stable Pydantic core is still wheel-blocked on RC3. The plain-Uvicorn fallback now explicitly retains WebSocket streaming through websockets>=13.0. Both live-wire WebSocket tests also pass with the portable py3-none-any websockets 17.2 wheel and no native speedups. Wheels reused the existing bundled UI; no fresh Angular build/test certification is claimed. No real Mastodon backend or GitHub job was invoked.
