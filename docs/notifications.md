# Notifications V1

Implemented on `codex/notifications-crashlytics-v1`, based on backend
`dev-core-architecture` at `2c4f312b3cb0327d7d298615028370514db2feb7` and paired with
SriZan12/SriSu frontend `9f1a7708f1aa9785fb6e046227e134f23e1ec9f1` plus its notification
branch. These are implementation baselines, not deployment claims.

## Contract and privacy

`contracts/notifications-1/` owns registration, preference, resolution and provider
payload schemas. Its pinned copy and the additive `couple-chat-1` capability/schema
live in the frontend. `core-1/routes.json` includes the new client calls.
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
| chat.message | New committed encrypted message operation | Partner's current encrypted-device session; exact conversation/message | 1 hour |
| invite.received | New pending invitation | Existing recipient; invitation list | 24 hours |
| invite.accepted | Pending → accepted, once | Original sender; current couple space | 6 hours |
| plan.created | Idempotent Plan creation | Other current member; Plan | Earlier of start / 6 hours |
| plan.responded | Meaningful response/note change | Plan proposer; Plan | Earlier of start / 6 hours |
| spark.revealed | Second original-device answer, once | First waiting participant's original device; Spark message | 6 hours |
| sharing.proposed | Changed nonempty proposal | Other member; sharing review | 24 hours |

No reactions, receipts, typing, presence, deletes, engagement reminders or historical
backfills generate push. No new scheduler/reminder product or notification center.
Plan title/time editing is not an existing server capability and was not invented.

New clients negotiate `notification-plan-1` via chat state. An optional
`notification_plan_id` on a message is checked against the current couple
and membership audience; creation deduplication applies to the Plan creator. The first correlated encrypted card does not produce a
second alert beside Plan creation. A later deliberate card share can alert normally.
The server never examines/decrypts card contents. Old clients omit this field and
keep working; older servers must be deployed before new capability-dependent use.

General activity reaches each eligible logged-in installation. There is exactly one
active notification installation per DeviceSession. Chat/Spark events additionally
pin the encrypted device/session; replacement does not redirect old ciphertext.
No encryption/multidevice feature was added.

## Transactions, retries and lifecycle

Domain services create Event and Delivery rows in the **same database transaction**
as the authorized mutation, after the operation exists. No Redis dependency and no
network call in that transaction. Unique source+recipient keys and delivery binding
keys make retried commands idempotent. Only installations present at event creation
are eligible; new installations never get an old backlog.

`dispatch_notifications` polls PostgreSQL, leases with SELECT FOR UPDATE SKIP LOCKED,
reclaims leases after two minutes and sends outside locks. It rechecks authorization,
expiry, preference, OS permission, binding version, session expiry/revocation,
current encrypted device and read receipts. It coalesces pending chat deliveries in
a two-second window and reserves at most one audible alert per installation per
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

1. Deploy this backend branch and install both dependency locks. Docker does this
   through the existing Pipfile sync plus the additive requirements file.
2. Keep `NOTIFICATIONS_ENABLED=false` while running `python manage.py migrate`.
   New additive migrations are `notifications/0001_initial` and
   `0002_installation_notification_active_session`. No historical source backfill.
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

### Windows Docker Desktop host

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
rollback, duplicate producer, worker leases, coalescing/read suppression, stale taps,
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
No current remote Docker deployment or live migration has been performed.

`NOTIFICATION_INSTALLATION_MAX_AGE_DAYS` defaults to 30. Foreground reconciliation
refreshes this timestamp; stale bindings are excluded from fanout/sending and their
target is erased during cleanup. A new binding version can reactivate one without
backfilling old deliveries. The worker updates a content-free heartbeat file and
Compose marks it unhealthy after stale heartbeats. An unhealthy label alone does
not restart Docker; the restart policy handles process exit. Check aggregate queue
age and outcomes with `python manage.py notification_status`; investigate repeated
configuration failures without logging SDK exceptions or target values.
