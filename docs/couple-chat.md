# Couple chat implementation and verification

Updated 2026-10-10. The chat implementation is available for local testing and QA
in both repositories. Encrypted transport, protected offline storage and native
media interoperability have executable Android/iOS evidence. The remaining device,
visual, performance and independent security checks below prevent a release claim.

## Repositories and compatibility

Backend: `SrizanKhadka/SriSu`, `dev-core-architecture-chat-system`, baseline
`c68e7b068229cb57d37064bea903108f237b1251`. Frontend: `SriZan12/SriSu`, same feature
branch, baseline `fa7e4fa20a90d2e1289c80fc5552d2bf69e00dd5`. Local frontend work is
in `D:/Djangos/SriSu/.cache/frontend-couple-chat`; the original StudioProjects
checkout and the user's untracked private media are preserved. Publication is
through paired draft PRs against backend `dev` and frontend `dev-new-theme`;
no deployment is included. These branches retain the approved architecture
baseline, including earlier changes not yet merged into the integration branches.

`couple_chat` is independent of legacy chat. Existing plaintext history is neither
imported nor used as a fallback. Acceptance retains the legacy room for released
clients and adds the new authoritative room ID. Invitation writes now pass through
the locked relationship service. The new client uses existing typed Navigation,
Ktor/session refresh, Koin, lifecycle and theme components.

Plans remain authoritative in `social.CouplePlanModel`. New clients pass
`share_to_legacy_chat=false` when creating a Plan, then send an encrypted reference.
The default remains true for released clients. Plan titles/times/responses remain
server-readable domain data; an encrypted reference does not change that fact.
Moments keep their original audience and 24-hour expiry. Chat stores only an
identifier and expiry, not permanent previews or copies of source content.
Challenges have no current domain implementation. Per the user's decision,
versioned Challenge references remain explicitly unavailable. Future work should
resolve their source IDs through the authoritative Challenge API and add exact
source navigation; transport, encryption and deletion semantics need not change.

Major backend files are `couple_chat/{models,services,api,consumers}.py`, its
migrations 0001–0005, `social/services/relationship_service.py`, the additive Plan
serializers/views, and `contracts/couple-chat-1/`. On the frontend, the feature is
in `composeApp/src/commonMain/kotlin/com/srisu/srisu/features/couplechat/`, with
`secureChatNative/` and `iosApp/SecureChat/` implementing protected native crypto
and storage. Platform media drivers, navigation/Koin wiring, Gradle/CocoaPods
configuration and matching contracts complete the integration. Test-only native
relays and the opt-in iOS evaluation harness are separate from normal app builds.

## Implemented boundaries

* One UUID room per accepted relationship instance, with immutable membership
  row IDs and two participants. Account/session/room locks serialize acceptance,
  device changes and durable commands. Removed/re-added members cannot inherit
  the previous room. Blocking, inactive users and revoked sessions deny access.
* HTTP owns durable commands, public-key registration/claims, ordered catch-up,
  private uploads and Spark release. Operation IDs and immutable digests make
  uncertain acknowledgements retryable without duplicate logical messages.
* Changes commit in the mutation transaction. WebSockets carry invalidation hints
  and disposable typing/presence only. Retryable dispatch and cursor catch-up
  recover commit-before-publication crashes. Delivery/read/played are distinct
  encrypted controls; fetching a page is not a delivery receipt.
* Native protected history, drafts, reply selection, queue and desired mutation
  versions. Text/replies/reactions/deletion; private nickname; identity-change
  confirmation; explicit device replacement; queued sends and retry/cancel.
  Ask/Spark answer drafts, Planner request IDs and reading position also survive
  restarts in protected storage. Planner retries preserve the exact consented
  request after a lost response; the UI reloads authoritative Plans on foreground.
* Native photo/video preparation and voice recording/playback, encrypted
  attachment storage, private uploads, capability downloads, captions, previews,
  explicit export, waveform/seek/speed and interruption handling.
* Typed Ask questions/polls and responses; per-Spark mutual reveal; existing Plans
  proposals/responses/source navigation; permission-checked Moment source views.
* Home/Profile chat entry and foreground unread/resume work. No notifications,
  calls, reminders, cloud backup, groups, AI, or new multi-device synchronization.

## Encryption and lifecycle

Native adapters pin official libsignal 0.105.0 Java/Android and Swift bindings.
The common layer does not implement a ratchet. Libsignal is AGPLv3 and explicitly
unsupported for external integration; independent licensing/security review and
an owned update process are release prerequisites. The app has not been relicensed.

Initial contact uses trust on first use. Safety numbers are library generated;
QR comparisons use the library's native fingerprint comparison and never silently
trust a changed identity. Changed identities require explicit confirmation. One active chat installation is
bound to a current authenticated login session. Replacement revokes future access
for the old device and never redirects old ciphertext to a new identity. Old
server ciphertext cannot recover history without the original local keys. Missing
storage keys fail visibly; reset requires explicit confirmation of history loss.

Android uses a Keystore AES-256-GCM wrapping key and encrypted records in SQLite
WAL/FULL under no-backup storage. iOS uses a Keychain WhenUnlockedThisDeviceOnly
key, AES-GCM encrypted records and backup-excluded protected SQLite. Ratchet changes
and immutable outbox/inbox records commit in one native transaction. Common
projection/cursor commits are replay-safe; successful projection also erases the
redundant decoded native inbox payload. No private body/key goes in a socket,
server log, URL, admin preview or notification hook.

Attachments use native AES-256-GCM with fresh 32-byte file keys and random native
96-bit nonces. Layout: nonce | ciphertext | tag. AAD binds protocol version, room,
message and attachment IDs. Descriptors/keys/thumbnails stay inside Signal payloads.
Retransmission uses immutable ciphertext. Integrity is verified before decoding.
Voice PCM and Android video preparation stay in bounded memory. iOS video uses a
seekable MP4 scratch file inheriting Complete file protection in a backup-excluded
directory, removes it before returning bytes for encryption, and cleans interrupted
files on startup. This replaces incompatible fragmented MP4 output. Explicit
exports may create user-authorized copies outside protected chat storage.

Spark answers use separate Signal addresses scoped by Spark UUID, never the normal
chat address. Server rows contain only routing identifiers and encrypted envelopes.
Neither status reads, changes nor sockets expose a partner's answer before both
immutable submissions. A locked second submission releases both. This trusts an
honest server to enforce release; it is not cryptographic fair exchange. Device
replacement requires a new Spark. Two unresolved Sparks are allowed per room;
new key-bundle claims are limited to 20 per device pair per rolling day. Retries
return the original claim. Deleted Spark cards erase their held server envelopes.

## Limits, retention and operations

Messages: 4,000 characters; encrypted envelopes: 64 KiB decoded; request JSON:
128 KiB; changes: default 50, maximum 100; local render window: 300; queued
operations: 100, with receipt reserve; server undelivered message cap: 1,000.
Attachments: three/message, each 16 MiB plaintext plus 28 encryption bytes; room
quota 512 MiB configurable; eight unfinished reservations; unbound uploads expire
in 24 hours. Videos: H.264/AAC, at most 1080p and two minutes. Photos are resized
and re-encoded without location tags. Voice: mono PCM16 WAV, up to two minutes.
Local encrypted media cache: 256 MiB/200 files, pending uploads pinned.
Acknowledgement/permanent failure atomically records an unpin job; local deletion
records a delete job. A restart retries these idempotent jobs. Uncertain sends keep
their ciphertext until the accepted result is recovered. Archived drafts are
eligible for eviction. Cache cleanup failure preserves its job and does not prevent
opening text history; loss of the protected master key still fails closed.

All Compose selection/copy paths are wrapped by the native private clipboard:
Android marks content sensitive and clears it after 60 seconds; iOS uses local-only
pasteboard entries with a 60-second expiry. Explicit copying/export remains a user
action that cannot revoke copies already taken by another application.

Android camera capture uses CameraX 1.5.3 into memory. QR pixels use ZXing core
3.5.4 on Android and Core Image on iOS. These supplement native libsignal's identity
comparison, not its cryptography. CameraX/ZXing use Apache-2.0; see the pinned
[CameraX release notes](https://developer.android.com/jetpack/androidx/releases/camera)
and [ZXing source/license](https://github.com/zxing/zxing/tree/zxing-3.5.4).

Active history/ciphertext has no silent time expiry. Tombstones and ordering records
are retained for reconciliation. Deleted ordinary message ciphertext is retained
to advance the recipient's ratchet; clients discard its body. Local deletion does
not promise forensic erasure or removal of previously exported/screenshot copies.
Capability downloads require Bearer authentication and a header-held, 60-second,
user/session/device/file-bound token. Every read rechecks membership and deletion;
revocation cannot retract bytes already delivered. Files are private even in Debug
MEDIA serving. Canonical storage paths are reserved before upload to make cleanup
recoverable after a file-write/database-commit crash.

Socket frames are limited to 512 UTF-8 bytes, four-frame burst and one/second.
Typing expires within five seconds, activity within 25 seconds; heartbeats are
foreground only. Disconnects clear state. No last-seen history is stored.
HTTP read/write/key rates: 240/120/12 per minute per user.

Apply backend migrations 0001–0005 before the new client. Backfill existing accepted
couples using `backfill_couple_chat --dry-run`, inspect, then run without dry-run.
`COUPLE_CHAT_PREVIEW_ENABLED` retains its early configuration name but now defaults
true; false remains an administrative off switch. The client no longer hides chat
behind a development-only button. Inspect existing environment overrides on rollout.
Run `dispatch_couple_chat` and `cleanup_couple_chat_media` regularly via the existing
worker/scheduler. Each invocation is bounded. Production needs TLS, private storage,
current auth session support and a shared Redis channel layer/cache for multiple
ASGI workers. An in-memory channel layer only spans one process. No new broker or
notification delivery stack is added. The notification hook is deliberately a no-op.

## Verified evidence

* Locked Python 3.13.5/Django 6.0.3 disposable PostgreSQL: **220 tests passed** with
  `python tools/couple_chat_checks.py --postgres --all` (168.434 s), including existing
  social suites, acceptance/concurrency, auth, media, activity, Sparks and the
  additive Plan compatibility behavior. Never use unrestricted Django discovery:
  legacy `chat/tests.py` opens a live socket on import.
  Two added Spark race cases also passed in the focused **58-test** suite
  (31.541 s): concurrent duplicate submissions and simultaneous partner answers.
* Full mobile unit/render run: **142 discovered, 141 passed, one existing live-
  transport skip**. Large-font dark Unicode/reply layout and light deletion
  screenshots were rendered and inspected; deleted body/quote accessibility and
  both Compose clipboard interfaces are covered. This is not Figma parity proof.
* Native production crypto/storage suites passed **four Android and four iOS**
  tests, including quiet 30-day signed-prekey rotation with full pools, immutable
  retries, fingerprint comparison and authenticated attachment/cache operations.
* Real native adapters + disposable Django HTTP passed bidirectional text,
  restart/retry/tamper/replay, reaction/read/deletion, third-user denial and encrypted
  attachment upload/key delivery/download/decryption. Evidence run
  `26afaf6c-adbe-4213-a91e-69057dad910c`. Its attachment input was synthetic bytes,
  not a codec fixture. Expanded run `58e02b37-3507-4800-b401-bbc6ee0f4b8c` passed
  Ask voting and Spark mutual reveal after **2,100 ordinary messages** advanced the
  regular ratchet while both Spark envelopes remained held. Run
  `a573a0ea-d6d1-44e6-92c0-38988721ae96` additionally passed native Android/iOS
  safety-number equality and QR comparison in both directions.
* Actual KMP Android repositories against disposable PostgreSQL and real HTTP/
  WebSockets passed in **53.198 s**, run `cc_app_895ccf227eed42aba3da6051827c88fe`:
  two users, protected offline queue/restart, Unicode, exact logical retry,
  nickname privacy, disabled read receipts, reply/reaction, Ask vote, Spark held
  answer/mutual reveal, deletion and no resurrection after catch-up. This test
  does not render the conversation UI.
  The final APK passed this flow again in **14.562 s**, run
  `cc_app_d450b60667e746f383bb83b05175e562`, after the readiness and route fixes.
* Actual KMP iOS repositories passed the corresponding two-user PostgreSQL/HTTP/
  WebSocket flow in **30.605 s**, run `cc_app_1fa9626213284884badae38f488b5aec`.
  The test exposed and reproduced a real race: catch-up temporarily changed READY
  to SETTING_UP, allowing a reaction to be ignored. Routine catch-up now preserves
  readiness only for the same verified account/room/device/identity context;
  changed identities/devices still require setup. The complete iOS test then passed.
* Visible Android Compose conversation passed against the same real repositories
  and disposable backend in **32.836 s**, run
  `cc_app_abe0f78cf3134729a0e52dd7de8e540c`: send, partner reply, reaction,
  personal nickname isolation, Ask creation/vote, unavailable Challenge and delete
  for everyone. It also asserts the secure-window flag. The test selects the
  actionable message separately from its repeated text in a reply preview.
  An earlier emulator run crashed before assertions; a fresh run reached the
  deletion step, exposed that selector ambiguity, and passed after the test fix.
* Full Android APK and instrumentation APK build passed after enabling Java 17
  application bytecode/record desugaring (minSdk unchanged). compileSdk is 36 for
  Media3 1.10.1; secure native library remains Java 21 compatible.
  Lint identified the old 1.1.6 core desugaring runtime as incompatible with this
  compileSdk. It is explicitly updated to 2.1.5; see the
  [upstream desugaring changelog](https://github.com/google/desugar_jdk_libs/blob/master/CHANGELOG.md).
  The final combined unit/APK/instrumentation/lint run passed in **8 min 15 s**;
  lint reports **zero errors and 121 warnings**, not a warning-free build.
* Native codec tests: two Android tests passed (3.295 s), including iOS-produced
  MP4/WAV decoding and photo rotation/location stripping; four iOS tests passed,
  including Android-produced MP4 decoding, corrupt media rejection and WAV/JPEG.
  Latest iOS main-app suite passed **five tests** including native QR encoding/
  decoding (0.370 s). The camera permission/cancellation fixes compiled in that app.
  Android: dedicated API35 emulator; iOS: iPhone17 simulator/iOS26.5, Xcode27.0.
  Updated Android native media/QR tests and the protected-history benchmark all
  passed together: **four tests, 121.312 s**.
  After the final app build/desugaring change, Android native media/QR tests passed
  again: **three tests, 2.104 s**.
* Initial maintained-library Android/iOS persistence/interoperability spike passed
  ten phases; run `8be8d419-e1b8-42dc-a6e7-c60f9c041f39`. This does not prove physical
  power-loss behavior or constitute an independent security audit.

Debug-only observational benchmark on emulator-5580 (API35 x86_64, two vCPUs,
2 GiB RAM), during concurrent development builds: 10,000 synthetic encrypted
messages; 50-row pages; cold native page 1,027.43 ms; warm median 750.47 ms/p95
1,135.54 ms; protected enqueue 160.37 ms; process PSS 220,930 KiB. Query/render
windows stayed bounded and history was correct. These are native repository
measurements, not first-frame, scrolling, network latency or Release/device
measurements. They do **not** establish the proposed 500/100 ms product targets.
Physical Release profiling remains blocked; performance tuning is still a QA item.

## Remaining acceptance work

Complete the full-UI device matrix, especially iOS and Android–iOS full-app flows,
including all cards and offline/error paths. Actual hardware camera/QR scanning, microphone permission and
audio-route interruption checks remain separate from native fixture tests.
Migration check reported no changes; core-1 schema/15 fixtures and theme audit
(96 swatches, 24 role pairs, minimum 4.75:1) passed. Final Android unit/build/lint
and normal iOS main-app tests passed after cache cleanup and route-resume changes.
Measure actual release-build UI opening/scroll/send/memory on named devices; no
performance targets are claimed as achieved. Physical-device tests are blocked by
lack of supplied physical devices, not replaced by simulator claims.

Figma Message page 1:5 and sections 184:2, 184:1121, 184:1634, 185:2, 185:781,
185:1624 remain the intended reference. Context/screenshots were quota-blocked;
no pixel-level comparison is claimed. Perform small/large-font/theme/screen-reader
and safe-area/back-navigation checks. Independent security/dependency review is
required before release. Challenges remain unavailable by explicit user choice.

## Security review checklist

This maps implemented checks and remaining work to relevant
[OWASP MASVS](https://mas.owasp.org/MASVS/) and
[OWASP API Security 2023](https://api-security.owasp.org/editions/2023/en/0x11-t10/)
areas. It is a review aid, not certification or a complete MASVS assessment.

| Area | Evidence and remaining verification |
| --- | --- |
| Storage/cryptography | Native encrypted records, protected keys, rollback/retry, tamper and missing-key tests pass. Physical power-loss, backup extraction and forensic review remain. |
| Authentication/object authorization | Server tests cover unrelated/former partners, revoked sessions/devices and private-media access; clients never choose the acting user. Review deployment identity/session settings. |
| Network/configuration | E2EE protects payloads independently of transport. Production still requires TLS and shared Redis/private storage; this task used isolated HTTP fixtures and did not test a production TLS deployment. |
| Resource consumption/business flows | Bounded frames, payloads, key claims, uploads, quotas and duplicate/concurrent writes are tested. Production capacity/load testing remains. |
| Platform/privacy | Clipboard tests, deleted-content semantics, secure Android window and backup-excluded stores are covered. Physical app-switcher, recording permissions and screen-reader checks remain. |
| Code/dependencies | Pinned protocol bindings and passing builds/lint provide integration evidence. AGPL obligations, unsupported external-use maintenance, dependency audit and independent security review remain release prerequisites. |

## Reproducing the synthetic two-account checks

The checks actually run use the following commands (substitute the documented
local executable paths and isolated environment, not a production configuration):

```sh
python tools/couple_chat_checks.py --postgres --all
python tools/couple_chat_checks.py --postgres
python tools/workspace.py migrations
python tools/check_core_contracts.py
# From the frontend checkout:
python tools/theme/check_theme.py
./gradlew :composeApp:testDebugUnitTest :composeApp:assembleDebug :composeApp:assembleDebugAndroidTest :composeApp:lintDebug --no-daemon --max-workers=2
adb -s emulator-5580 shell am instrument -w -r -e class com.srisu.srisu.features.couplechat.presentation.ChatMediaNativeTest com.srisu.srisu.test/androidx.test.runner.AndroidJUnitRunner
```

Windows uses `gradlew.bat`, JDK21 and the ignored D:-based Gradle/temp caches. Its
Robolectric init script redirects test `user.home` to D: because C: is full; this
does not alter the tests. The benchmark used the same instrumentation command
with `ChatHistoryBenchmarkTest` added to the comma-separated class argument.
Native production engine suites use `EngineTests` in `tools/e2ee-spike` on each
platform; initial spike checks/commands are recorded in that directory's README.
The expanded backend-native transport command was:

```sh
python tools/native_chat_e2e.py --frontend <frontend-path> --adb <adb-path> --mac-host <user@mac> --identity <existing-key-path> --known-hosts <verified-hosts-path> --mac-root <isolated-mac-evaluation-root> --spark-stress-messages 2100
```

The later bidirectional safety-number/QR run used the same command with stress
count zero. The normal iOS build/test command, from `iosApp/`, was:

```sh
xcodebuild test -workspace iosApp.xcworkspace -scheme SriSuChatTests -configuration Debug -destination 'platform=iOS Simulator,id=0055683D-7DBA-44C7-855D-4AA64123C522' -derivedDataPath build/chat-media -jobs 2 CODE_SIGNING_ALLOWED=YES CODE_SIGN_IDENTITY=-
```

Ad-hoc signing is needed for the simulator's Keychain entitlement. The ordinary
build excludes `ChatAppEvaluation` (also checked in the generated framework header).

Use the isolated locked environment from `docs/workspace.md`, never a production
database or login. Start the disposable PostgreSQL container with
`docker compose -p srisu-couple-chat-test -f compose.couple-chat-test.yaml up -d`.
It binds only `127.0.0.1:55449`; its database files are disposable tmpfs.

Build the frontend with JDK21 and the existing lock/version catalogue:

```sh
./gradlew :composeApp:testDebugUnitTest :composeApp:assembleDebug :composeApp:assembleDebugAndroidTest --max-workers=2
adb -s emulator-5580 install -r composeApp/build/outputs/apk/debug/composeApp-debug.apk
adb -s emulator-5580 install -r composeApp/build/outputs/apk/androidTest/debug/composeApp-debug-androidTest.apk
```

From the backend, run `python tools/chat_app_integration.py --adb <adb-path>`.
The runner creates a uniquely named database, two accepted synthetic partners and
an unrelated user; starts an isolated ASGI server; reverses one emulator port;
supplies ephemeral credentials only to the test APK's private fixture; and removes
the fixture, server, port mapping and database on exit. Add `--ui` for the visible
Compose test. There is no production fixture loader or hardcoded login.

For iOS, `-PchatAppEvaluation=true` adds only the evaluation Kotlin directory to
Debug frameworks; Release tasks reject that property. Build the `SriSuChatTests`
scheme with `ORG_GRADLE_PROJECT_chatAppEvaluation=true` and the Swift condition
`CHAT_APP_EVALUATION`, then use the runner's `--ios --bind <Windows-private-IP>
--mac-runner tools/chat_test_mac.py` mode. Configure `SRISU_CHAT_MAC_HOST`,
`SRISU_CHAT_MAC_KEY` and `SRISU_CHAT_MAC_KNOWN_HOSTS` with the authorized host and
existing key paths. Verify the host fingerprint out of band before creating that
known-hosts file; the wrapper never accepts a new key automatically. This exposes only the disposable test
server on the selected LAN interface, using synthetic accounts. Production builds
must omit both evaluation switches. Evidence states whether this check passed;
these commands alone are not proof of execution.

For a manual demonstration, run two Debug app installations against a local
non-production backend, create/accept a partner invitation through the existing
onboarding, open chat on both devices and complete secure setup. Send in each
direction, go offline, queue a message, restart, reconnect and check one bubble.
Compare safety numbers/QR in person; try an Ask vote, each Spark answer, an existing
Plan response, and a permitted/expired Moment. The unrelated account must not open
the room. Unlinking archives local history; a new accepted relationship gets a new
room. Never repurpose the automated test tokens for a persistent demo.

## Maintenance

Keep Java/Swift libsignal versions and wire fixtures aligned; rerun the native
interoperability/persistence suite before upgrading. Rotate signed prekeys through
normal foreground publication and retain old private prekeys for offline delivery.
Monitor identifier-only dispatch/cleanup failures, queue depth and private storage
usage. Run the bounded dispatch/cleanup commands repeatedly to drain backlogs.
No private messages, media keys or credentials belong in operational logs.

## Local Docker server for manual testing

Use `docker-compose.yaml` from the backend root, not the disposable automated-test
Compose file. Start Docker Desktop first. Preserve the existing `.env` signing key
and PostgreSQL credentials; enable `DJANGO_DEBUG=true`, `OTP_MOCK_DELIVERY=true`,
`OTP_MOCK_CODE=123456`, `COUPLE_CHAT_PREVIEW_ENABLED=true` and set
`SRISU_DEV_LAN_HOST` to the Windows Wi-Fi IPv4 address. All devices must use the
same server origin. The address inspected on 2026-10-10 was `192.168.1.69`.

```powershell
Set-Location D:\Djangos\SriSu
docker version
docker compose config --quiet
docker compose build web
docker compose up -d srisu_db redis
docker compose exec srisu_db sh -c 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
docker compose exec redis redis-cli ping
docker compose run --rm web python manage.py migrate --noinput
docker compose run --rm web python manage.py backfill_couple_chat --dry-run
docker compose run --rm web python manage.py backfill_couple_chat
docker compose up -d --force-recreate web
docker compose exec web python manage.py check
docker compose ps
curl.exe -i http://127.0.0.1:8000/api/auth/interests/
```

Wait for PostgreSQL to report accepting connections and Redis to return `PONG`
before migrations. If the backfill reports conflicts, review them instead of
altering membership records manually. New invitation acceptance creates rooms
automatically; the backfill is for existing accepted couples.

In a second PowerShell window, run the bounded maintenance commands while testing:

```powershell
Set-Location D:\Djangos\SriSu
while ($true) {
    docker compose exec -T web python manage.py dispatch_couple_chat
    docker compose exec -T web python manage.py cleanup_couple_chat_media
    Start-Sleep -Seconds 15
}
```

Stop that loop with Ctrl+C. Inspect server output with
`docker compose logs --tail 100 -f web`; stop services with `docker compose stop`.
Do not remove the PostgreSQL volume when retaining test accounts/history.
Build the current frontend checkout in Debug with
`-Psrisu.apiBaseUrl=http://192.168.1.69:8000/ -Psrisu.environment=development`;
its old default `192.168.1.73` does not match this inspected network. The Mac/iOS
simulator uses the Windows server address, not the Mac's own address or localhost.

Docker setup follow-up: the build now pins Python 3.13.5, installs the existing
lock with `pipenv sync`, and excludes `.env`, caches and uploaded media from the
image. Compose passes the `POSTGRES_*` names actually read by Django and connects
internally to `srisu_db`; Redis uses its internal port 6379. The existing local
`.env` remains available through the development source mount. `docker compose
config --quiet` passed. The Docker daemon timed out during the read-only status
check, so this follow-up did not build/start the manual-test server or migrate its
database; run the commands above after Docker Desktop's engine is ready.
