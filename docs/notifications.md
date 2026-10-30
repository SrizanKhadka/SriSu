# Notifications V1

## Consolidated core integration — 29 October 2026

`codex/consolidate-core-20261029` combines backend Matrix/shared activities
`34d47b4`, notifications `065340f`, and the local Explore/Faves/development-account
snapshot `0ea972d`, targeting `dev-core-architecture`. The paired frontend starts
from `2c7384b` and includes notifications `0898bef`.

The runtime uses Matrix only. Activity notification producers run in current
relationship, Plan and Spark transactions; deliveries capture current couple and
membership IDs. Every dispatch/tap rechecks current membership, blocking and resource
state. Plan cancellation, superseded responses, changed sharing consent/content,
replaced relationships and rotated Matrix Spark epochs make stale alerts unavailable.
No private content is added to provider payloads. Existing idempotency and worker
lease/retry/binding rules remain in force.

Matrix chat push is unavailable pending a separate Matrix-specific integration.
Legacy chat events are never produced or resolved, and the old Plan-card correlation
capability is not advertised. The client exposes current activity controls and a
clear chat-push availability message. The new `spark` typed destination is additive;
older clients cannot open it and should be upgraded with this backend.

Historical `couple_chat` **models and migrations only** remain installed so hosts
with applied notification migrations keep a valid migration graph. Its API, sockets,
workers, signal handlers and runtime implementation are removed. Existing archival
tables and ciphertext are retained; no new archive cleanup/destruction is authorized
by this consolidation. They are not a fallback transport or a source for new alerts.

New merge migrations:

- `social.0022_merge_notification_compatibility`: joins the independent applied
  Windows compatibility migration and current lifecycle/Explore migration chain.
- `couple_chat.0006_archive_reverse_names`: disambiguates an archival ORM reverse
  accessor from the current Spark model; preserves the stored data.
- `notifications.0003_current_couple_audience`: adds nullable current-couple and
  membership-snapshot fields. Existing historical delivery references are retained
  but are not rewritten into a new audience.

**Deployment order:** use the existing Matrix cutover instructions in `chat-v2.md`
and a verified host-local backup/readiness attestation before its destructive legacy
`chat.0006` migration. Keep notifications disabled during migration; apply the merge
migrations, deploy both current clients, then enable the optional notification worker.
Do not reuse the October 14 notification-only image/Compose override as a Matrix
rollout recipe. This merge does not deploy, run migrations against an application
database, or change a running host. The evidence below is historical unless dated
October 29.

### Consolidation verification — 29 October 2026

- Paired `tools/workspace.py verify all` passed: contracts, Django checks, theme,
  Android unit tests and Android/shared iOS compilation. The SQLite suite passed
  327 tests with 27 expected PostgreSQL-only skips before the final Plan regression
  was added.
- The final disposable PostgreSQL 17 run passed **328 tests with no skips**,
  including the additional identical-Plan-proposal regression, concurrent workers,
  membership isolation, source expiry and migration compatibility.
- Real KMP-to-Django loopback HTTP integration passed using a temporary server,
  synthetic accounts and an isolated database. Fixtures and server were cleaned up.
- `pip check`, migration drift checks and Compose configuration validation passed.
  Compose was validated with a temporary synthetic environment; no services started.
- The paired native iOS simulator build passed without device signing. Real FCM,
  physical-device behavior, APNs and cloud Crashlytics were not revalidated for this
  consolidation. Historical deployment evidence below does not validate this head.

Implemented on `codex/notifications-crashlytics-v1`, based on backend
`dev-core-architecture` at `2c4f312b3cb0327d7d298615028370514db2feb7` and paired with
SriZan12/SriSu frontend `9f1a7708f1aa9785fb6e046227e134f23e1ec9f1` plus its notification
branch. These are implementation baselines, not deployment claims.

### Merged Windows rollout — 29 October 2026

The merged runtime `80a1330` is deployed on the authorized Windows Docker Desktop
host `192.168.1.69`. The Windows shell entrypoint fix (`8fdfccc`, merged as
`06b004f`) pins `*.sh` to LF so Synapse starts after a normal Windows Git checkout.
The paired Android runtime is `5ad8a97`, with additional opt-in deployment/native
Matrix instrumentation on `codex/windows-core-deployment-20261029`. Follow-up
documentation and tests are merged into both `dev-core-architecture` branches.

- Checkout: `D:\Djangos\SriSu-core-20261029`, detached at the deployed merge.
  The original dirty checkout and previous notification checkout are preserved.
- Compose project: `srisu`, with the checkout's `docker-compose.yaml` and
  `D:\SriSu-tools\deployment\compose.core-20261029.yaml`. The override pins the
  built application image, retained external database/Matrix volumes, and original
  media mount `D:/Djangos/SriSu/media:/app/media` for every application service.
- Django: `http://192.168.1.69:8000/`; private Synapse:
  `http://192.168.1.69:8008/`, stable identity `srisu.local`. This is the requested
  HTTP development deployment with mock OTP delivery, not a production release.
- Web, Matrix reconciler, PostgreSQL, Redis, Synapse and the notification worker
  run with persistent restart policies. Notifications were disabled for migration
  and enabled after the host-local ADC passed an FCM validation-only request.
  Credentials remain on Windows, mounted read-only into the notification worker.

The old worker was quiesced before cold backups. PostgreSQL volumes, media and
old configuration were archived and verified under
`D:\SriSu-secrets\backups\core-20261029T131044Z`. Both Matrix volumes were initially
empty. The existing relationship preflight passed; additive migrations through
`chat.0005a` were applied; the existing eligible relationship was reconciled and
live-verified with no outstanding work. The application and initialized Matrix
databases were then dumped and **successfully restored into disposable isolated
PostgreSQL containers**, with hashes retained on Windows. A fresh readiness
attestation preceded the one-off gated `chat.0006` migration. All later merged
migrations passed, and `migrate --check` reports no outstanding migration.
Original media and archival `couple_chat` data are preserved; no media purge ran.
Rollback across `chat.0006` requires the verified backup and compatible old code,
not a reverse migration or an old image against the upgraded database.

The exact merged image also ran in a separate `srisu-core-e2e-20261029` project with
synthetic users and its own PostgreSQL/Synapse volumes. Actual API 35 Android
instrumentation passed the full foreground/background FCM, tap authorization,
preference, account-switch and logout test (49.975 seconds). It reported two
accepted deliveries and no active installations after logout. A second native
test passed two-participant encrypted messaging/decryption, typing start/stop and
unrelated-user denial (47.286 seconds); raw Matrix history contained encrypted
events and no synthetic plaintext. HTTP tests passed Plans create/duplicate
retry/accept/stale-write denial, Sparks creation/plaintext rejection/skip,
Challenges/Home, profile/feed and cross-user access checks. The real application
database received no synthetic test accounts or messages.
The normal LAN APK separately passed a read-only Android smoke test against the
deployed application services (11.09 seconds): correct configured origin, catalogue
200, protected preferences 401, Matrix versions/policy 200 and MainActivity launch.

Use both Compose files for subsequent operations, for example in PowerShell:

```powershell
Set-Location D:\Djangos\SriSu-core-20261029
docker compose -p srisu -f docker-compose.yaml `
  -f D:\SriSu-tools\deployment\compose.core-20261029.yaml `
  --profile notifications up -d
```

The normal LAN APK is `D:\SriSu-tools\SriSu-core-debug.apk`; the frontend flow
record has its hash and device-test details. Apple enrollment/APNs remain skipped.
No physical-phone app data was changed. Matrix message push is still unavailable;
activity notifications are the verified V1 scope. Full activity-screen UI coverage,
signed releases and renewed Crashlytics console verification are separate work.

## Contract and privacy

`contracts/notifications-1/` owns registration, preference, resolution and provider
payload schemas. Its pinned copy and current activity/Spark capabilities live in
the frontend. `core-1/routes.json` includes the new client calls.
All routes below require an authenticated user and a live auth-1 DeviceSession. All responses use the existing `{data: ...}` envelope and private/no-store.

| Method | Path relative to `/api/notifications/v1/` | Behavior |
| --- | --- | --- |
| PUT | `installations/` | Register current authenticated session, installation UUID, random proof, monotonically increasing version, platform, tagged FCM target and OS permission. |
| DELETE | `installations/` | Retire this session's bindings only. Cannot revoke a later account's replacement binding. |
| GET / PUT | `preferences/` | Owner-only global push, chat, shared activity and chat mute; optimistic expected_revision protects saves. |
| GET | `deliveries/{uuid}/` | Reauthorize recipient, exact binding/session, current membership, device, content and expiry. Return an allowlisted typed destination or neutral 404. No mutation. |

FCM receives only the fixed title `SriSu`, fixed body `You have an update. Open SriSu
to view it.`, schema `1`, opaque delivery UUID and expiry epoch. No account IDs,
relationship IDs, names, phone numbers, previews, ciphertext, keys, media URLs or
credentials are included. Collapse/thread IDs are opaque HMACs. Provider acceptance
is not evidence of device delivery. Never log targets or raw provider exceptions.
The installation table contains sensitive routing credentials; do not expose it in
Django admin, logs or analytical exports. Proofs are SHA-256 digests of 256-bit
random values. TLS is required outside explicitly local development.

Native clients prefer **registered FCM FIDs**, not raw Firebase Installations IDs.
The tagged `token` form remains supported for compatibility. Firebase Admin 7.7.0
and its additive dependency versions are pinned in `requirements-notifications.txt`;
existing Pipfile.lock packages are unchanged. Firestore/Storage packages are SDK
transitive dependencies; no Firebase database/storage/auth service is configured
or used.

## Events and current authorization

| Event | Source transition | Recipient / route | TTL |
| --- | --- | --- | --- |
| Matrix chat | No producer in this release | Unavailable pending Matrix push integration | — |
| invite.received | New pending invitation | Existing recipient; invitation list | 24 hours |
| invite.accepted | Pending → accepted, once | Original sender; current couple space | 6 hours |
| plan.created | Idempotent Plan creation | Other current member; Plan | Earlier of start / 6 hours |
| plan.responded | Meaningful response/note change | Plan proposer; Plan | Earlier of start / 6 hours |
| spark.revealed | Second answer, once | First waiting participant; current Spark and Matrix epoch | 6 hours |
| sharing.proposed | Changed nonempty proposal | Other member; sharing review | 24 hours |

No reactions, receipts, typing, presence, deletes, engagement reminders or historical
backfills generate push. No new scheduler/reminder product or notification center.
Current Plans support the operations defined in `social/services/plans.py`; only
creation and meaningful partner responses emit notifications. Matrix card sharing
has no notification producer and the retired `notification-plan-1` correlation
capability is not advertised.

Activity reaches each eligible logged-in installation captured at event creation.
There is exactly one active notification installation per DeviceSession. Spark
resolution reuses current membership and Matrix-epoch authorization; it never
translates old encrypted-device deliveries into the new audience.

## Transactions, retries and lifecycle

Domain services create Event and Delivery rows in the **same database transaction**
as the authorized mutation, after the operation exists. No Redis dependency and no
network call in that transaction. Unique source+recipient keys and delivery binding
keys make retried commands idempotent. Only installations present at event creation
are eligible; new installations never get an old backlog.

`dispatch_notifications` polls PostgreSQL, leases with SELECT FOR UPDATE SKIP LOCKED,
reclaims leases after two minutes and sends outside locks. It rechecks authorization,
expiry, preference, OS permission, binding version, session expiry/revocation,
current couple membership and source state. Deliveries wait two seconds before
the first attempt; the worker reserves at most one audible alert per installation per
30 seconds. Provider transport is at least once: a crash after acceptance and before
commit can cause a duplicate. Stable OS replacement IDs and client delivery-ID
checks reduce this; exactly-once presentation is not promised.

Unregistered retires only the attempted binding version. A concurrent FID/token
rotation survives a late failure. Quota/unavailable/timeouts use bounded exponential
retry plus jitter and Retry-After (seconds or date). Configuration failures retry
slowly without erasing registrations; payload-invalid is never treated as evidence
of a dead target. Retries stop at expiry or 20 attempts. Event/delivery metadata is
removed after 30 days. Revoked/expired session target values are erased, while the
proof/version tombstone fences old requests. Workers may be replicated on PostgreSQL.

Logout clears OS notifications, disables native auto-init, unregisters from FCM and
uses the existing server session-revocation workflow. An in-flight/previously queued
OS alert can still appear, with generic copy only. Offline logout cannot atomically
recall APNs/FCM queues. Taps always reauthorize and cannot open another account.
Native registration versions persist separately from account-session storage.

## Docker deployment

The existing Docker image and PostgreSQL are retained. A supervised
`notification_worker` service is behind the `notifications` Compose profile. No
Celery, Firebase Functions, new queue service or new database is required.

1. Deploy this backend branch and install both dependency locks. Docker exports
   hashed requirements from the existing Pipfile.lock using an isolated, pinned
   Pipenv build tool, then installs the additive notification requirements. Build
   tools do not share runtime dependencies; the existing lockfile stays unchanged.
2. Keep `NOTIFICATIONS_ENABLED=false` while running `python manage.py migrate`.
   Apply the notification chain through `0003_current_couple_audience`, the social
   merge migration and archive accessor migration listed above. Follow the Matrix
   cutover prerequisites before migrating a pre-Matrix host. No notification backfill.
3. Configure project `srisu-ef431` and read-only ADC credentials for the worker.
   Prefer a dedicated sender service account with `cloudmessaging.messages.create`
   (the predefined `roles/firebasecloudmessaging.admin` is scoped to this project)
   and short-lived workload identity or local service-account impersonation. Do not
   grant Owner/Editor, create downloadable keys by default, or use Firebase CLI
   credentials as a production worker credential. Google CLI login and ADC are
   separate from Firebase CLI login.
4. Set `SRISU_FIREBASE_ADC_PATH` to the local ADC/WIF file (outside Git), mounted as
   `/run/secrets/firebase-adc.json`. The template default path is deliberately absent;
   do not enable the profile without a real readable file.
5. Set `SRISU_DEV_LAN_HOST` to the actual development host (observed in this task:
   `192.168.1.69`; the Compose fallback is the older `.73` address). Enable
   `NOTIFICATIONS_ENABLED=true` in both web and worker, then recreate:

   ```sh
   docker compose build web notification_worker
   docker compose run --rm web python manage.py migrate
   docker compose --profile notifications up -d --force-recreate web notification_worker
   ```

6. Ship the mobile clients after server deployment. Confirm registration/preferences
   and only then run controlled synthetic delivery checks. The worker needs outbound
   HTTPS to Google, not inbound public access. Existing LAN/API security still applies.

Rollback: disable the flag in web and worker and recreate them. Retain additive
migrations/data while old and new mobile clients coexist; do not drop the tables.
No live database migration or Docker deployment is claimed by a local build/test.

### Verified development deployment — 2026-10-14

Backend implementation `784c5aface38e862bb5dae9e7379c515c761a73f` is deployed on the
user's Windows Docker Desktop laptop at `192.168.1.69:8000`. Frontend implementation
baseline is `0d65527418c9912d21f205a83c637744f6fd5fd6`, followed by the verified
preference-write fix `58d6322`, on the matching feature branch.
The new checkout is `D:\Djangos\SriSu-notifications`; the original
`D:\Djangos\SriSu` checkout and its uncommitted files were preserved.

- Image `srisu-web:notifications-784c5af` built successfully, including `pip check`.
  Image ID: `sha256:940512a7cf88d6894c0158b13a3fc060341d0cc4f6a6d0e0dd5a6bb474854ccd`.
- Django system checks passed. The migration plan contained only the two reviewed
  notification migrations; both were applied successfully with sending disabled.
- PostgreSQL custom-format backup was verified with `pg_restore --list`, copied to
  `D:\SriSu-secrets\backups\srisu-pre-notifications-20261014T162009Z.dump`, and hashed
  locally. It remains on the host, outside Git; no database contents left the host.
- Compose project `srisu` retains PostgreSQL volume `srisu_postgres_data` and media
  mount `D:/Djangos/SriSu/media:/app/media`. Database and Redis were not recreated.
  Web now binds the new checkout; a separate worker is running and healthy.
- `NOTIFICATIONS_ENABLED=true` in web and worker. Initial aggregate status showed
  zero installations and deliveries. LAN interests returned HTTP 200; unauthenticated
  notification preferences returned HTTP 401.
- The Windows-local impersonated ADC, mounted read-only into the worker, successfully
  completed an FCM validation-only request. This did not send a device notification.

Host-specific Compose overrides are in `D:\SriSu-tools\deployment\compose.host.yaml`.
Use both Compose files and `-p srisu` for subsequent operations. The old image is
retained as `srisu-web:pre-notifications-20261013`. For rollback, stop the new worker
and start web from the original checkout with that old image, keeping the explicit
project name and existing database volume. Retain the additive notification tables.

Docker's desktop credential helper cannot access its interactive logon session over
SSH. Build commands use a separate task-only Docker client configuration at
`D:\SriSu-tools\docker-client`, the Docker Desktop Linux named pipe, and an anonymous
Docker Hub auth-map entry. No registry credentials or user Docker configuration were
changed. The C: space blocker was resolved before deployment; do not prune user
volumes or unrelated caches to accommodate a build.

Backend regression evidence before rollout: **245 isolated PostgreSQL tests passed**,
including three compatibility tests for the host's existing legacy-schema repair.
Device delivery tests use the same image in a separate `srisu-notifications-e2e`
Compose project with a disposable PostgreSQL database and synthetic accounts; they
never write synthetic users/messages into the deployed application database.
The live API 35 Android emulator test passed: registered FCM FID, foreground signal,
background OS notification, real PendingIntent/MainActivity launch, authorized typed
routing, unrelated-user denial, stale invitation rejection, push preference
suppression, account switching and online logout. Aggregate results were two
accepted deliveries and zero active installations after logout. Android controlled
fatal/nonfatal reports were also verified in Firebase; details and coverage limits
are recorded in the frontend's `docs/flows/notifications.md`.

The live test exposed a frontend default-value serialization defect: ordinary
preference writes omitted the required `mute` field. Frontend `58d6322` includes it
and preserves existing mute state. This did not require a backend contract change.
The normal LAN debug APK is `D:\SriSu-tools\SriSu-notifications-debug.apk`.
The isolated test project is torn down after verification; real project `srisu`
remains running. Apple/APNs, physical-device and signed-release checks remain deferred.

### Windows Docker Desktop host

Inspection of the actual host found an existing, already-applied local repair,
`social.0014_partner_insert_compatibility`. It preserves INSERT compatibility with
required columns retained from the old chat-v2 schema by supplying absent database
defaults; it never rewrites existing rows. The deployment branch carries forward
that exact host migration and its three PostgreSQL regression tests, together with
the existing PostgreSQL/Redis health checks. Both web and notification worker wait
for those dependencies. The original Windows checkout and its uncommitted files
are preserved; deploy the feature from a separate worktree with the same explicit
Compose project and mounts for the existing media and environment.

Apple enrollment/APNs is deferred at the user's request (2026-10-13). Deploy and
validate Android first; this does not require Apple membership.

The existing manual-development checkout was recorded at `D:\Djangos\SriSu` on
Windows, with Docker Desktop/WSL and LAN address `192.168.1.69`. Recheck these values
on that laptop before using them. Remote access should use Windows OpenSSH on the
private LAN, using the same Windows account that runs Docker Desktop. Keep Docker
Desktop running and the laptop awake. Run `docker` on Windows through SSH; no raw
Docker TCP socket, router forwarding, WSL SSH server or copied Mac credentials are
required. If the checkout lives in WSL instead, inspect its distribution/path first
and execute through `wsl.exe` under that same Windows account.

Install Windows' built-in OpenSSH Server through an elevated PowerShell session;
authorize only a dedicated public key whose private key stays on the Mac. Restrict
that key and the new firewall rule to the Mac's current LAN address. Administrative
accounts use `%ProgramData%\ssh\administrators_authorized_keys`, with access only
for SYSTEM and Administrators; standard accounts use their own `.ssh\authorized_keys`.
Do not replace existing authorized keys. Compare the Windows Ed25519 host-key
fingerprint locally before trusting the first SSH connection. Passwords, private
keys, ADC files and `.env` contents must not be pasted into chat.

Official instructions: [Windows OpenSSH installation](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_install_firstuse)
and [key authorization](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_keymanagement).

After remote access is established, deploy in this order:

1. Inspect `git status`, current branch/commit, `docker compose ls`, the existing
   Compose project name, running services and PostgreSQL volume. Preserve local
   changes and the original Compose project/volume; a different checkout name must
   not silently create another database. The development source bind mount means
   switching the active checkout can immediately reload the running web service.
   Schedule the brief web/worker stop before switching that checkout, leaving the
   database and Redis running.
2. Fetch `origin codex/notifications-crashlytics-v1`; verify the published commit
   before switching/fast-forwarding. Keep the existing `.env`, signing key, media
   and PostgreSQL credentials. Take a host-local database backup before migrations;
   never commit or upload that backup. Do not use `down -v`, volume pruning or reset.
3. Build the new image with sending disabled. Run Django checks and migration-plan
   inspection, then apply migrations against the verified existing database.
4. Authenticate Google Cloud **on the Windows host** with the dedicated sender's
   impersonation ADC. This requires the account holder's interactive sign-in:

   ```powershell
   gcloud auth application-default login --impersonate-service-account=srisu-notification-sender@srisu-ef431.iam.gserviceaccount.com
   ```

   For this personal development host only, set `SRISU_FIREBASE_ADC_PATH` in its
   ignored `.env` to the actual ADC path under `%APPDATA%\gcloud`, expressed with
   forward slashes for Compose. Expand `%APPDATA%` locally; do not put that literal
   expression in `.env`. The file contains a developer refresh credential and must
   remain private to the authorized operator. Shared/production hosts need an
   appropriate workload identity instead. The Docker context excludes local ADC
   paths and key files as well as `.env`.
5. Perform an FCM validation-only request from the worker context, enable sending
   in web/worker, and start both with the `notifications` profile. Verify aggregate
   `notification_status`, heartbeat/health, and public read-only API responses.
6. Install the configured Android debug build on a Play-enabled device using the
   confirmed Windows LAN origin. Use synthetic partner accounts for permission,
   registration, background delivery/tap, mute, logout and account-switch checks.
   Opt in to diagnostics on the test device before controlled Crashlytics probes;
   confirm reports in Firebase. A server `sent` outcome alone is not device proof.

The repositories carry code, not Firebase app configs or credentials. Download the
Android app config into its ignored location if building on the second laptop.
Remove the task's public-key entry and task firewall rule when remote work is done;
stop SSH only if it was enabled exclusively for this task.

## Verification

Use `tools/workspace.py test` (explicit allowlist, offline SQLite) and
`tools/auth_postgres_tests.py --postgres-bin ...` (disposable local PostgreSQL).
Never use unrestricted discovery. `notifications.tests` covers payload fields,
recipient isolation, session expiry, binding proof/version, account transfer,
rollback, duplicate producer, worker leases, stale-source suppression, stale taps,
preferences, retry/expiry, token rotation, Plan correlation, invitation transitions,
Spark reveal and sharing approval. FCM is replaced with a synthetic provider in tests.
The frontend loopback integration additionally exercises real KMP registration and
preference HTTP calls against the disposable Django server.

Operational checks use aggregate states/outcomes, e.g. counts grouped by
`Delivery.state`/`outcome`. Do not export row-level routing data. Real FCM/APNs,
physical-device force-quit behavior and cloud Crashlytics reports require separate
verification recorded in the paired frontend handoff.

## Current setup evidence (2026-10-13)

The dedicated `srisu-notification-sender@srisu-ef431.iam.gserviceaccount.com` identity
has custom role `projects/srisu-ef431/roles/srisuNotificationSender` with only
`cloudmessaging.messages.create`. IAM Credentials API is enabled; the signed-in
operator has Token Creator on that service account only. A short-lived impersonated
credential successfully made an FCM validation-only request. No device message,
service-account key, billing change or broader role grant was involved.

Local development ADC is outside Git at
`~/.config/srisu/notification-sender-adc.json` (0600). Its source is a developer
refresh credential, so do not copy it to an untrusted/shared host; the narrow
impersonated token does not restrict what the source user credential can do. Use
workload identity or the intended Docker host's authorized development credentials.
This was the pre-deployment checkpoint. The completed 2026-10-14 remote deployment
and migration evidence appears above.

`NOTIFICATION_INSTALLATION_MAX_AGE_DAYS` defaults to 30. Foreground reconciliation
refreshes this timestamp; stale bindings are excluded from fanout/sending and their
target is erased during cleanup. A new binding version can reactivate one without
backfilling old deliveries. The worker updates a content-free heartbeat file and
Compose marks it unhealthy after stale heartbeats. An unhealthy label alone does
not restart Docker; the restart policy handles process exit. Check aggregate queue
age and outcomes with `python manage.py notification_status`; investigate repeated
configuration failures without logging SDK exceptions or target values.

## Matrix chat push (2026-10-30)

The `matrix-chat-push-1` capability adds Android-only `matrix.message` events and
`matrix_conversation` destinations without reviving the retired chat transport.
Old installation requests can omit capabilities; only opted-in Android bindings
receive this kind. Upgrade bumps the binding version and never backfills events.
Deploy migration `notifications.0004_matrix_chat_push` before the new Android app.

Synapse sends standard [application-service transactions](https://spec.matrix.org/latest/application-service-api/)
to `/_matrix/app/v1/transactions/{id}` using a dedicated Bearer `hs_token`.
Authentication precedes JSON parsing; user JWTs and query-string credentials are
rejected. The namespace matches only SriSu's generated couple-room aliases,
non-exclusively. User/room-ID namespaces are empty, so the AS has no human-user
impersonation rights. Query endpoints return authenticated `M_UNRECOGNIZED`.
Ephemeral receipts are enabled. The renderer disables legacy URL authentication.

Only encrypted message metadata is examined. Neither plaintext nor ciphertext is
stored, logged or sent to FCM. Events must be fresh (10-minute TTL from the original
Matrix timestamp), after the rollout cutoff, and from a current authorized member
of the active provisioned room/epoch. Pending replacement, remote revocation,
blocked relationships, stale members and unrelated senders are excluded. The
existing sender rechecks session, binding, preferences, relationship and mapping.
Taps reauthorize the same Django conversation; deleted/replaced relationships and
redactions become unavailable. FCM carries generic copy and an opaque delivery ID.

Transactions and source events are deduplicated atomically; failures roll back the
batch for Synapse retry. The receiver locks affected mappings in stable order.
Edits/reactions with visible relation metadata are excluded. Read receipts suppress
pending events through the known received event's timestamp; an early receipt also
leaves an exact-event tombstone. Redactions suppress pending deliveries and taps.
Already accepted notifications cannot be recalled, and cross-transaction receipt
ordering is not a full Matrix read-cursor implementation. Transaction hashes expire
after 30 days, suppression hashes after a day, beyond the 10-minute delivery TTL.

### Docker deployment and rollback

1. Back up the existing PostgreSQL databases and Synapse data. Keep existing Matrix
   server identity, JWT secrets, media and volumes. Apply only the additive
   notification migration; do not rerun the historical destructive chat cutover.
2. Run `python tools/configure_matrix_push.py --env .env` in the selected checkout
   with Matrix and notifications already enabled. It writes a random `hs_token`
   only to the ignored environment and establishes a no-backfill cutoff. Re-running
   preserves an enabled rollout's token/cutoff; enabling after a disable resets the
   cutoff. Never print `.env` or resolved Compose configuration.
3. Recreate web, notification_worker, matrix_reconciler and Synapse using the updated
   Compose file. The AS callback origin defaults to internal `http://web:8000`;
   Django allows that internal hostname. The Synapse renderer writes a mode-0600
   registration in its private data volume and preserves its independent `as_token`
   across restarts. Django never receives that AS client credential. Use a protected
   network or TLS if these services are separated across hosts.
4. Verify Synapse health, the worker heartbeat, Matrix status and one synthetic
   encrypted-message delivery before updating Android. Ship the backend first;
   the Android installation advertises support when it re-registers.
5. To stop chat alerts set `MATRIX_CHAT_PUSH_ENABLED=false` and recreate the same
   services. The AS receiver acknowledges disabled batches without later replay.
   Leave the additive schema in place for a safe code rollback. Other notification
   kinds remain controlled by their existing settings.

Verification: `tools/workspace.py test` includes `notifications.test_matrix_push`;
`tools/auth_postgres_tests.py` also runs its concurrent transaction test. The frontend
opt-in `MatrixPushDeviceIntegrationTest` uses a separate synthetic backend and real
Android FCM delivery. Apple/APNs enrollment and delivery remain explicitly skipped.
