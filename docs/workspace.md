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

Tests explicitly select the four existing social suites and `srisu.test_core`.
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

Configure an authorized GitHub login on each laptop when pushing changes; never
put credentials in the workspace files. Workflow publication uses the connected
GitHub account and does not copy a local Git login to other machines.

## Core contract checks

```sh
.venv/bin/python tools/check_core_contracts.py
```

The normal server now requires environment-backed `DJANGO_SECRET_KEY`,
`DJANGO_ALLOWED_HOSTS` and optional browser `WEBSOCKET_ALLOWED_ORIGINS`. Use
`.env.example` only for fresh local values; plan any existing signing-key rotation
with the Authentication rollout. Never use workspace test settings to serve users.
