# Complete local demo accounts

`seed_demo_accounts` creates **100 total users paired into 50 established couples**.
The 100 includes every partner. It uses the current schema and needs no new
migrations, API changes, dependency changes, or frontend deployment. Apply the
branch's existing migrations through the normal local setup first.

## Run

In the backend checkout, with the normal development Compose stack running:

```sh
docker compose exec web python manage.py seed_demo_accounts --dry-run
docker compose exec web python manage.py seed_demo_accounts --confirm-local --manifest .local/demo-accounts.json
```

The source checkout is mounted at `/app`, so the roster appears in the backend's
ignored `.local/demo-accounts.json` file on the host. No rebuild is needed
for a running development container with that mount. Start the existing local
stack using your normal workflow if `web` is stopped.

For a separately configured local Python backend on Windows:

```powershell
.venv\Scripts\python.exe manage.py seed_demo_accounts --dry-run
.venv\Scripts\python.exe manage.py seed_demo_accounts --confirm-local --manifest .local/demo-accounts.json
```

Use `.venv/bin/python` on macOS/Linux. These commands use **your configured local
database**, not workspace test settings. The command requires `DJANGO_DEBUG=true`,
`OTP_MOCK_DELIVERY=true`, a valid configured `OTP_MOCK_CODE`, local filesystem
media storage, and `--confirm-local` for writes. It never changes those settings
or reads a production database for you. Dry-run validates sources, collisions,
and existing relationships without writing database rows, media, or a roster.

## Sign in

Use the normal app OTP flow with the configured development mock code. No SMS
is sent in that environment. The command never prints the code or creates tokens,
sessions, passwords, staff users, or superusers.

The first pair is Asha Thapa (`+447700900000`) and Aarav Raut
(`+447700900001`). Choose country code **+44** and enter **7700900000** or
**7700900001** if the app separates country and national numbers. The roster
lists all 100 database IDs, names, usernames, phone numbers, couple IDs, and
partner identities. Database IDs are assigned normally, not forced to 1–100.

Numbers run through `+447700900099`, within Ofcom's
[fictional mobile range](https://www.ofcom.org.uk/phones-and-broadband/phone-numbers/numbers-for-drama).
Emails use `demo.srisu.test`. All identities and relationship stories are fictional.

## What is populated

| Data | Fresh dataset |
| --- | --- |
| Complete, verified, active adult accounts | 100 |
| Distinct synthetic people photos used as avatars | 100 |
| Personal album entries | 200 (portrait plus landscape per user) |
| Personal interests | 500 (five per account, three shared with partner) |
| Account preferences and notification preferences | 100 each |
| Accepted relationships and complete couple profiles | 50 |
| Current memberships with nicknames | 100 |
| Private couple covers, songs, anniversaries, shared dreams, journey stories | 50 each |
| Story answers | 300 (three from each partner) |
| Plans | 150 (50 completed, 50 agreed upcoming, 50 invitations) |
| Moments and moment photos | 150 each (50 recent public, 50 recent private, 50 expired archived) |
| Private appreciation notes | 150 |
| Personal favourites | 100 |
| Reactions and views | 250 each |
| Couple statuses and date ideas | 100 statuses, 50 ideas |
| Chat rooms / Matrix user and room mappings | 50 rooms, 100 users, 50 room mappings |

Birth dates, zodiac signs, adult relationship timelines, locations, and plan time
zones are consistent. Names reflect Nepal and its diaspora; photos show a diverse
synthetic cast. Album and moment landscape images intentionally reuse a small
licensed scenery collection. Each upload gets its own storage name so deleting
one user's media does not break another's. There are 500 uploaded files.

Forty couples have both partners' valid, content-bound consent for public
identity, date, interests, and cover. Ten keep all profile sections private.
Stories, songs, plans, and private moments remain subject to existing member
authorization. Covers and moments use the existing protected media directories
and authenticated endpoints, never a new public media route.

## Reruns and activity freshness

The default content seed is `42`; `--seed` can select another value on the first
run. Reuse it thereafter. Accounts are owned by a dedicated, permission-free
Django group; the phone, username, and email also have to match. The command
refuses to claim existing accounts or reconnect edited/broken relationships.
It leaves unrelated users and the older six-account `seed_local_test_accounts`
dataset alone. Ordinary reruns preserve profile edits, existing records, and
files, and report zero new accounts/couples.

Moments retain the product's normal 24-hour expiry. Before a later demonstration:

```sh
docker compose exec web python manage.py seed_demo_accounts --confirm-local --refresh-activity --manifest .local/demo-accounts.json
```

This refreshes the two non-archived seeded moments per couple and moves unchanged
open seeded plans into the future. It does not revive archived moments, cancelled
or completed plans, or manually rescheduled plans. It does not extend status
expiry or overwrite user-authored content. To try a different seed, use a fresh
disposable database; there is deliberately no destructive reset switch.

Missing accounts, renamed reserved identities, changed partners, missing required
profile data, or missing media fail with an explanation. Restore that data or use
a fresh demo database. A caught failure rolls back the database transaction and
removes only files created during that attempt. A process kill/power failure can
leave orphan files; a retry reuses matching checksummed names and refuses to
overwrite a different file. An existing unrelated roster file is also protected.

## Architecture and limits

The command hydrates historical fixtures directly and reuses the existing couple
membership, chat-room, Matrix-mapping, profile-consent, and publication-fingerprint
helpers. It intentionally does not replay live invitation/notification APIs.
No network calls, emails, SMS, push delivery jobs, or cache writes run during
seeding. Notification preferences start with push disabled. The photo pack is
bundled, validated by SHA-256 and image decoding, and never fetched at runtime.

A transaction and a common dataset row lock serialize PostgreSQL seed runs.
On SQLite, contention can produce a locked-database error; retry after the other
writer finishes. Existing application services, routes, serializers, authorization,
and dependencies are unchanged. Source assets are versioned under
`authentication/demo_assets`; generated uploads and rosters remain ignored.

Matrix room mappings are created as **pending**. The existing Matrix reconciler
provisions them after commit when Synapse is available. Check `matrix_status`
for actual readiness. The command reports active mappings; it never marks a
room active without provisioning. Encrypted chat messages, media keys, Spark
answers, device sessions, and push installations require real client flows and
are not fabricated. Existing home/challenge catalogues remain unchanged.

## Verification

Run the locked, socket-blocked workspace checks:

```powershell
.venv\Scripts\python.exe tools/workspace.py check
.venv\Scripts\python.exe tools/workspace.py test
.venv\Scripts\python.exe tools/workspace.py migrations
```

The explicit allowlist includes `authentication.test_demo_accounts`, covering the
full dataset, private/cross-couple access, expiry, OTP login, collisions, dry runs,
reruns, file-write rollback, assets, and refresh behavior. PostgreSQL concurrency
requires a separate disposable database; native device and live Matrix behavior
remain separate integration checks.

### Validation on 2026-11-22

The existing Windows `.venv` has Python 3.13.5 / Django 5.1.3, whereas the lock
and existing `srisu-backend-app:local` image use Python 3.13.5 / Django 6.0.3.
No packages or dependency files were upgraded. The image was used for the
authoritative locked-environment checks with the current source mounted read-only.

Commands actually run from the backend checkout:

```powershell
.venv\Scripts\python.exe tools/workspace.py check
.venv\Scripts\python.exe tools/workspace.py test
.venv\Scripts\python.exe tools/workspace.py migrations
docker run --rm --network none --mount 'type=bind,source=D:\Djangos\SriSu,target=/app,readonly' --workdir /app srisu-backend-app:local python tools/workspace.py check
docker run --rm --network none --mount 'type=bind,source=D:\Djangos\SriSu,target=/app,readonly' --workdir /app srisu-backend-app:local python tools/workspace.py test
docker run --rm --network none --mount 'type=bind,source=D:\Djangos\SriSu,target=/app,readonly' --workdir /app srisu-backend-app:local python tools/workspace.py migrations
```

- Pinned image: system checks passed, no migration changes, and the complete
  380-test allowlist passed with 33 skips (PostgreSQL/live-environment coverage).
- PostgreSQL 17 in the task-owned `srisu-demo-verification-20261122` container,
  with a tmpfs database and no application volumes: all 13 demo tests passed
  without skips in the pinned app image, including two simultaneous seeds.
  The corrected tests also passed in the older Windows environment. The
  one-off command was `docker run --rm --network container:srisu-demo-verification-20261122 --mount 'type=bind,source=D:\Djangos\SriSu,target=/app,readonly' --workdir /app srisu-backend-app:local python .local/demo-preparation/verify_locked_postgres.py`.
  That ignored verification helper used `srisu.test_postgres_settings`, the
  workspace socket blocker, and only `authentication.test_demo_accounts`.
  The temporary container/database was removed afterwards. For another machine,
  the existing disposable PostgreSQL runner in `docs/workspace.md` includes this
  suite through the maintained allowlist.
- Existing Windows environment: checks/migration consistency passed; the full
  suite had one failure and seven errors: three missing `firebase_admin` imports,
  three Windows asyncio socket-pair conflicts with the network blocker, one
  POSIX permission expectation, and one existing photo-test file-handle cleanup
  error. The new ordinary demo tests passed. Those unrelated tests and the local
  virtualenv were not modified.
- The initial new PostgreSQL media test exposed a test-harness cleanup issue:
  manually closing the streamed response also closed the test transaction's
  connection. The test now consumes Django's closing iterator correctly.
- Both repositories passed `git diff --check`. Backend code/docs/assets and only
  the frontend's shared project-map documentation changed. No API/client changes
  or new migrations, and nothing was pushed.
- The local Compose app stack was stopped. The application database was not
  populated during implementation; use the command above when it is running.
  Native devices and live Matrix provisioning/conversation flows were not run.

Photo provenance, source URLs, and individual photographer credits are recorded
in [the asset manifest](../authentication/demo_assets/manifest.json) and
[photo licence notes](../authentication/demo_assets/README.md).
