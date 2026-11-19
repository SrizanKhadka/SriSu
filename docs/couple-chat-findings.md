# Couple chat implementation record

Inspection: 2026-10-10. This is an incremental implementation record, not a release claim.

## Verified baseline

- Backend `SrizanKhadka/SriSu`, `dev-core-architecture-chat-system`,
  `c68e7b068229cb57d37064bea903108f237b1251`; live architecture ref matches.
  Existing untracked private profile media is preserved.
- Frontend `SriZan12/SriSu`, isolated checkout `.cache/frontend-couple-chat`,
  branch `dev-core-architecture-chat-system`, based on live architecture ref
  `fa7e4fa20a90d2e1289c80fc5552d2bf69e00dd5`. The older
  `D:/StudioProjects/SriSu` checkout is untouched.
- Django 6.0.3 / Python 3.13.5 are locked. Inspection of installed packages later
  found that the pre-existing `.venv` actually used Django 5.1.3/DRF 3.15.2.
  A separate `.cache/couple-chat-venv` was installed from the existing lock
  for authoritative verification, preserving the original runtime environment.
  PostgreSQL 17, Redis, Channels and
  Daphne are configured. Docker Desktop is reachable; project services were stopped.
- Mobile uses Kotlin 2.2.0, Compose 1.8.2, Ktor 3.2.3, Koin, typed Navigation,
  Room 2.7.2 and bundled SQLite 2.5.2. Existing Room storage is not evidence of
  encrypted chat persistence. No native chat encryption integration was found.
- Existing invitation update responses omit the `data` envelope expected by
  current KMP. Existing acceptance reuses legacy rooms and terminal invitations;
  pending checks exclude accepted connections. These need additive compatibility
  fixes and serialized relationship transitions.
- Plans and 24-hour Moments exist in social. Legacy profile cards use `chat`.
  New couple chat must not adopt that plaintext business logic.
- Figma `184:2` design context, screenshot and variables all returned the Starter
  MCP call-limit error. Exact visual implementation/verification remains blocked;
  the other requested sections cannot be assumed inspected.

## Decisions and prerequisites

One new `couple_chat` app, independent room identities, relationship membership
snapshots, ordered committed changes and retryable dispatch. HTTP owns commands;
user sockets carry invalidation hints and require authenticated catch-up.
No legacy plaintext migration or fallback. New room creation does not depend on
encryption readiness. Never accept private content before native E2EE is ready.

Libsignal is the leading protocol candidate. Its [primary documentation](https://github.com/signalapp/libsignal)
states AGPLv3, unsupported external use, Java/Swift bindings and changeable APIs.
Android needs both client and Android artifacts and native packaging exclusions.
The [Swift integration](https://github.com/signalapp/libsignal/blob/main/swift/README.md)
uses CocoaPods plus Rust FFI/checksum; consumer SwiftPM integration is unsupported.
License/distribution review remains a release prerequisite, not an assumption that
SriSu can be relicensed. The user requires a complete feature and has offered a
Mac. Its host fingerprint is now verified and authenticated SSH works. Xcode 27.0,
macOS 27.0.1 and iOS 26.5 were verified remotely. The main KMP simulator framework
compiles and links. Isolated real native tests pass: three Android API 35 tests,
two iOS tests and ten Android–iOS interop phases with process restarts, protected
SQLite storage, atomic ratchet/outbox/inbox rollback, delayed delivery, tamper and
replay rejection. See the frontend `tools/e2ee-spike/README.md` for exact commands,
release checksum and limitations. Libsignal is selected for internal development;
app integration and independent security review remain outstanding.

That paragraph records the initial spike checkpoint. The feature is now wired
into both apps, and real KMP two-account repository flows pass on Android and iOS.
The later [implementation and verification record](couple-chat.md) supersedes
these initial test counts and lists the current device/UI/security review gaps.
Challenge references intentionally remain unavailable, by the user's explicit
decision, with a versioned source identifier for a future authorized resolver.

No calls, notifications, reminders, public rooms or new infrastructure platform.
An incomplete secure setup is explicitly represented, never disguised as working
E2EE. Any temporary development gate is not the requested final deliverable.
