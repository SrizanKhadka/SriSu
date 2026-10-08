# SriSu shared workspace

The published setup branch is `codex/workspace-integration`. The frontend's
[portable workflow guide](https://github.com/SriZan12/SriSu/blob/codex/workspace-integration/docs/integration/other-laptops.md)
explains access from another laptop and the GitHub-hosted checks.

The backend checkout belongs beside the frontend:

```text
studioprojects/
  SriSu/          # SriZan12/SriSu; integration branch dev-new-theme
  SriSu-backend/  # SrizanKhadka/SriSu; integration branch dev
```

The shared guide is `../SriSu/docs/project-context.md`; the structured map is
`../SriSu/docs/integration/project-map.json`. Figma file:
[Srisu](https://www.figma.com/design/LztysD1YvINX7RwZpnhhTt/Srisu?node-id=0-1).
Read the frontend's instructions explicitly before changing it.

## Environment

Use Python **3.13.5**, as declared by `Pipfile`/`Pipfile.lock`. The lockfile pins
Django 6.0.3 and the current Channels dependencies. The older UTF-16
`requirements.txt` differs and is not used for this workspace environment.

With [uv installed](https://docs.astral.sh/uv/getting-started/installation/),
bootstrap a fresh local environment from this directory:

```sh
uv venv --python 3.13.5 --seed .venv
.venv/bin/python tools/export_workspace_requirements.py > .venv/workspace-requirements.txt
.venv/bin/python -m pip install -r .venv/workspace-requirements.txt
.venv/bin/python -m pip check
```

The exporter uses existing locked versions and markers, replacing `psycopg2`
with the same-version `psycopg2-binary` wheel for local checks. It pins versions
but does not preserve artifact hashes. Production dependency files are unchanged.
The generated requirements and environment stay inside ignored `.venv`.

## Isolated verification

```sh
.venv/bin/python tools/workspace.py check
.venv/bin/python tools/workspace.py test
.venv/bin/python tools/workspace.py migrations
```

The runner uses `srisu.workspace_test_settings`: synthetic credentials, an
in-memory SQLite test database, in-memory cache/channel layers, and a temporary
media directory cleaned on exit. Socket connections and datagram sends are
blocked for this process. No application server is started.

Tests explicitly select the four existing social suites and `srisu.test_core` plus `authentication.test_auth`.
The selection also includes `chat.test_v2`; see [chat v2 rollout and contract](chat-v2.md)
before applying its additive migrations or enabling the fail-closed pilot gates.
The exporter now also includes `requirements-core-tests.txt` for JSON Schema tests. PostgreSQL-only concurrency
tests may be skipped on SQLite; run them separately against a disposable local
PostgreSQL instance using the existing PostgreSQL test settings after inspecting
that environment. This local runner does not verify production configuration,
OTP delivery, Android/iOS-to-server requests, or native-device behavior. `tools/core_integration.py` in the frontend separately
starts a disposable loopback backend for real KMP HTTP/WebSocket traffic; see the
core validation record.

Do not run bare `manage.py test`: legacy `chat/tests.py` invokes `asyncio.run`
at import time and opens a live WebSocket. It needs conversion into a proper
isolated test suite before unrestricted discovery is suitable for CI.

## Cross-repository work

From the frontend, run `python3 tools/workspace.py status --remote` to identify
both repositories and current integration refs. Run
`python3 tools/workspace.py verify all` to check frontend and backend in sequence.
Open `../SriSu/SriSu.code-workspace` for a multi-folder editor workspace, or run
`bash ../SriSu/tools/codex-workspace.sh` to launch Codex CLI with both directories.

Separate checkouts and explicit working directories allow one task to edit
both repositories. Concurrent tasks should use their own branches/worktrees;
the frontend's worktree does not isolate this backend automatically.

GitHub Actions runs the isolated checks and migration consistency checks from
`.github/workflows/workspace.yml` on pushes/PRs. Check the exact commit's result
under Actions; configuring CI does not itself establish a passing run. Native
PostgreSQL concurrency and live transport still require additional coverage.

### Disposable PostgreSQL verification

With PostgreSQL 17 binaries installed, run the same explicitly selected suites:

```sh
.venv/bin/python tools/auth_postgres_tests.py --postgres-bin /opt/homebrew/opt/postgresql@17/bin
```

The runner creates a private temporary cluster, binds a dynamically selected
loopback port, uses synthetic workspace settings and temporary media, runs the
suites, stops the server and deletes the temporary directory. It does not use an
application database or register a background service. Python socket egress is
blocked; libpq connects only to the configured temporary loopback database. All
121 tests passed on PostgreSQL 17.11 on 2026-09-28; this is separate from SQLite and
the mobile loopback transport check.

Configure an authorized GitHub login on each laptop when pushing changes; never
put credentials in the workspace files. Workflow publication uses the connected
GitHub account and does not copy a local Git login to other machines.

For the chat-v2 feature branch's exact Docker migration, backfill, worker, and
diagnostic sequence, read [Development Compose and another-laptop
setup](chat-v2.md#development-compose-and-another-laptop-setup).

## Core contract checks

```sh
.venv/bin/python tools/check_core_contracts.py
```

The normal server now requires environment-backed `DJANGO_SECRET_KEY`,
`DJANGO_ALLOWED_HOSTS` and optional browser `WEBSOCKET_ALLOWED_ORIGINS`. Use
`.env.example` only for fresh local values; plan any existing signing-key rotation
with the Authentication rollout. Never use workspace test settings to serve users.

Authentication phase 1 adds isolated auth tests and migrations; see
[the endpoint/rollout notes](authentication.md). No unrestricted chat discovery or
provider SMS is used by these tests.

Development Compose currently mocks OTP SMS delivery so the expired Twilio trial
does not block testing `SendOTPAPIView`. See [mock delivery behavior and restoring
real SMS](authentication.md#development-mock-for-sendotpapiview). Pull/recreate is
sufficient; request a new OTP and use the shared synthetic code **123456** for
development users. The response explicitly says no SMS was sent. Verification
still enforces the challenge, expiry and attempt limits. Normal non-Compose
settings retain real Twilio delivery with random OTPs by default.

## Mobile clients connecting to Docker over the LAN

Binding `0.0.0.0:8000` and publishing port 8000 makes the server reachable, but
does not add the client's requested hostname to Django's allowlist. Django rejects
an unlisted Host before API routing, returning **400 HTML**, including for a valid
`POST /api/auth/send-otp/`. This is not a missing endpoint or OTP validation error.

The checked-in development Compose configuration now appends `192.168.1.73` to
`DJANGO_ALLOWED_HOSTS`, including when an existing `.env` sets only localhost.
For the current backend laptop, no `.env` edit is needed: pull the backend branch
and recreate the application services. From the `codex/couple-chat-rebuild`
checkout:

```sh
git pull --ff-only origin codex/couple-chat-rebuild
docker compose up -d --force-recreate web chat_outbox chat_maintenance
curl -i http://192.168.1.73:8000/api/auth/interests/
```

For another address, set `SRISU_DEV_LAN_HOST` to the backend laptop's LAN IP in
the local `.env`; an explicitly empty value disables the Compose addition.
Omitting it uses `192.168.1.73`. Existing `DJANGO_ALLOWED_HOSTS` entries are retained.
This addition is scoped to this development Compose server; `srisu/settings.py`
and non-Docker deployments still use their own explicit `DJANGO_ALLOWED_HOSTS`.
Use only hostnames/IPs: no scheme, path, port or wildcard. Compose also passes
`DJANGO_DEBUG`; the old `DEBUG=1` name was ignored by `srisu/settings.py`.

Modern Compose also accepts `docker compose` with the same arguments. A container
recreation reloads environment values; an application autoreload is insufficient
when the old value was injected into the container. The catalogue request must
return 200 JSON. A read-only GET to `send-otp/` returns 405 JSON (it requires POST),
and unauthenticated `chat/rooms/` returns 401 JSON. Do not send a real OTP simply
to test connectivity. If the laptop's DHCP address changes, update `SRISU_DEV_LAN_HOST`
and rebuild the client with the new `srisu.apiBaseUrl` origin.

On 2026-09-29 the reachable server at that example LAN address rejected its LAN
Host with 400 HTML but accepted localhost, yielding 200/405/401 as above. GET on
`auth/refresh/` also returned 405, confirming the new route was present on that
server. The remote environment was not editable from the frontend laptop, so the
follow-up Compose default makes pull/recreate sufficient for that LAN address.

## Full mobile HTTP route inventory

`contracts/core-1/routes.json` lists all 26 first-party HTTP method/path/query
combinations currently called by KMP. `srisu.test_api_routes` resolves each against
the actual Django URLconf and view methods, and verifies the LAN-host rejection
and explicit-allowlist fix with mocked SMS. The frontend pins this file and tests
actual service requests against it. This extends the earlier core fixture subset;
it is not a complete schema for all legacy response bodies. The external city
catalogue and WebSocket protocol remain separate.

## Navigation-era client contract (2026-10-01)

[Dating retirement and compatibility](navigation-retirement.md) supersedes the older
all-26-calls count and active dating expectations above. Current KMP calls, retired
paths and preserved personal-preference APIs are separated in routes.json. No database
migrations or production changes are required by this retirement.
