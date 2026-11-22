# SriSu local backend startup

Use the repository launcher instead of manually remembering the Docker sequence.
It supports Windows PowerShell, macOS, Linux, and WSL while preserving existing
credentials, database volumes, Matrix identity, media, and source changes.

## Quick start

From the root of any compatible SriSu backend branch:

### Windows PowerShell

```powershell
.\start-local-server.ps1
```

### macOS, Linux, or WSL

```sh
./start-local-server.sh
```

The launcher detects the laptop's private LAN IPv4. Override detection when the
laptop has several network adapters:

```powershell
.\start-local-server.ps1 --lan-ip 192.168.1.50
```

```sh
./start-local-server.sh --lan-ip 192.168.1.50
```

WSL users must pass the Windows host Wi-Fi/Ethernet IPv4 explicitly; the WSL
`172.x` NAT address is normally not reachable from a physical phone.

Use `--pull` to refresh pinned external images. Use `--skip-build` only when the
local `srisu-backend-app:local` image already matches the checkout. Preview the
sequence without changing files, containers, or databases with `--dry-run`.

## What it starts

The normal stack contains six Compose services:

| Service | Responsibility | Host port |
| --- | --- | --- |
| `web` | Django/Daphne HTTP API | `8000` |
| `matrix_reconciler` | Durable Matrix room provisioning/revocation | internal |
| `srisu_db` | Django PostgreSQL 17 | `5433` |
| `redis` | Couple-feed cache | `6379` |
| `synapse` | Private Matrix homeserver | `8008` |
| `synapse_db` | Synapse PostgreSQL 17 | internal |

`notification_worker` is intentionally optional. Start it only with
`--with-notifications` after setting `NOTIFICATIONS_ENABLED=true` and configuring
a readable `SRISU_FIREBASE_ADC_PATH`. Firebase credentials are not required for
the normal backend and chat stack.

## First run behavior

If `.env` is absent and no retained SriSu PostgreSQL container/volume exists, the
launcher copies `.env.example`, creates unique local
Django and PostgreSQL secrets, sets development-only mock OTP delivery, and
writes the ignored file with owner-only permissions. If `.env` already exists,
the launcher preserves its secrets and updates only the current development LAN
host settings. It refuses to rotate missing/placeholder credentials in an
existing environment because an initialized database may depend on them.

The existing `tools/configure_matrix_dev.py` helper enables Matrix, creates or
preserves private Matrix secrets, keeps `MATRIX_SERVER_NAME` stable, and sets the
device-reachable Synapse origin.

Before touching Docker or `.env`, the launcher verifies that the checkout has
the expected Compose, Matrix configuration, Synapse startup, and guarded chat
cutover source files. It then validates Compose, optionally refreshes external
images, builds the shared Django image, and starts both databases plus Redis and
Synapse. Before stopping application services or applying migrations, it runs
the built image's Django checks, verifies both Matrix management commands, and
confirms that the guarded `chat.0006` migration is present. This catches a stale
image used with `--skip-build` while the existing application processes are
still untouched. Finally, it quiesces application processes, applies ordinary
pending migrations, starts `web` and `matrix_reconciler`, and verifies Django,
migrations, Matrix policy/readiness, and HTTP reachability.

A genuinely new database still reaches the reviewed irreversible `chat.0006`
cutover gate. The launcher deliberately stops there; complete the documented
one-time readiness and backup sequence in `docs/chat-v2.md`, then rerun it. This
gate is never converted into an unattended destructive first-run shortcut.

## Mobile configuration

After successful startup, copy the printed values into the frontend's ignored
`local.properties`, rebuild, and reinstall the app:

```properties
srisu.apiBaseUrl=http://YOUR_BACKEND_LAN_IP:8000/
srisu.environment=development
```

The mobile client obtains the Matrix origin from the authorized Django session
response. Do not point a physical device at its own `localhost`.

## Branch portability

The launcher does not require, select, or switch a particular Git branch. It
prints the current branch and commit for diagnostics, preserves local changes,
and validates capabilities instead: required source files, the six required
Compose services, Django's checks, the guarded chat migration, both Matrix
management commands, and Matrix health. A branch such as
`dev-core-architecture`, `codex/home-couple-space`, or another feature branch
can run when it contains those compatible backend facilities.

An incompatible branch fails on the missing capability rather than on its name.
The legacy `--allow-other-branch` argument remains accepted as a no-op so older
commands do not break.

## Safety behavior

The launcher never:

- switches, resets, merges, rebases, pulls, or stashes Git work;
- changes the Compose project name;
- runs `docker compose down -v`, deletes volumes, or prunes Docker state;
- changes an existing Django/PostgreSQL secret or Matrix server name;
- sets `SRISU_CONFIRM_DESTROY_LEGACY_CHAT`;
- purges legacy media or retries the destructive migration gate.

If migration output mentions `DESTROY_LEGACY_CHAT_TRANSPORT_V1`, startup stops.
That database has not completed the reviewed Matrix cutover. Follow the
readiness, backup, migration, and purge sequence in `docs/chat-v2.md`; do not
bypass the gate from a startup script.

## Troubleshooting

Inspect the current state and recent logs:

```sh
docker compose ps
docker compose logs --tail 100 web matrix_reconciler synapse
```

Common causes:

- **Retained Synapse state exists, but `.env` is missing stable Matrix values:**
  the local environment file is incomplete, while the Compose project's old
  Synapse containers or volumes still exist. The PowerShell wrapper delegates
  this check to `tools/start_local_server.py`. Restore the original Matrix
  server name, JWT secret, PostgreSQL password, macaroon secret, and form secret
  into the ignored `.env`; also preserve the Matrix database name and user.
  Stopped containers retain their environment, so these values may be recoverable
  even when the original `.env` is unavailable. Before restoring them, verify
  the Compose project and compare the Synapse/reconciler environments, Synapse's
  saved `/data/homeserver.yaml`, and the Synapse PostgreSQL container's settings.
  Keep secret values out of terminal output, logs, and commits. Do not delete
  volumes or run `configure_matrix_dev.py` to generate replacements for retained
  state. Rerun the launcher after recovery; it updates the LAN URL while keeping
  the original Matrix identity and credentials.
- **Docker unavailable:** start Docker Desktop/Engine. The launcher can start an
  already-selected Colima context and can open installed Docker Desktop.
- **HTTP 400 from Django:** `SRISU_DEV_LAN_HOST` does not match the requested host.
- **Admin loads without CSS:** direct Daphne startup needs the development
  static-file routes in `srisu/urls.py`. With `DJANGO_DEBUG=true`, they serve
  admin CSS/JavaScript from Django's installed static assets. Restart `web`
  after changing server code; Daphne does not automatically reload it. With
  debug disabled, static assets require a separate production static server.
- **Mobile timeout:** the app still embeds an old IP, or the host firewall blocks
  private-network access to ports `8000`/`8008`; rebuild and reinstall the app.
- **Home endpoints return 404:** the wrong backend checkout is mounted or additive
  migration `social.0023_home_couple_space` is missing.
- **Matrix is not ready:** inspect `matrix_status` plus Synapse/reconciler logs;
  do not create a second Matrix identity or change `MATRIX_SERVER_NAME`.

Stop containers without deleting data:

```sh
docker compose stop
```
