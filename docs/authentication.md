# Authentication phase 1 (2026-09-28)

Companion client: `SriZan12/SriSu`, `dev-core-architecture`, starting
`23ccd1f374a00207f32a4ac124250033b743689e`. This backend is `SrizanKhadka/SriSu`,
`dev-core-architecture`, starting `e63a322cc76c304008a408232ea476f140fc2b04`.
Full shared audit, routing, design limitations and validation are in
`../SriSu/docs/flows/authentication.md`. No production data/SMS was used.

## Implemented API

All paths are under `/api/auth/`. Core error opt-in remains
`X-SriSu-Contract: core-1`. New device sessions use `X-SriSu-Auth: auth-1`.
Success resources retain `{data: ...}` and optional message. Secrets/OTP bodies
must not be logged by middleware, reverse proxies or application diagnostics.

| Endpoint | Request | Success | Errors |
| --- | --- | --- | --- |
| POST send-otp/ | phone_number, optional UUID request_id | 200 data: challenge_id, expires_at, resend_at, server_time, retry_after_seconds | 400 invalid phone; 409 pending same request; 429 + Retry-After; 503 ambiguous/failed provider delivery |
| POST verify-otp/ | phone_number, otp_code, optional UUID challenge_id | 200 data: user, tokens, progress | 400 field code invalid/expired/attempts_exhausted/consumed_or_unavailable; never treats bad OTP as expired session |
| GET setup-profile/ | authenticated actor | 200 data: user and progress | 401 invalid/revoked; 503/server failures remain recoverable |
| PATCH/PUT setup-profile/ | allowlisted existing profile fields; name/username, profile_photo or skip_photo for registration | 200 confirmed user/progress | 400 field errors; 403 another phone/unverified actor; unique conflict remains username code unique |
| POST refresh/ | refresh, optional UUID request_id | 200 data.tokens | 400 invalid input; 401 invalid/expired/replayed/revoked proof |
| POST logout/ | refresh | 204, idempotent | 400 invalid shape; expired/invalid proof also returns 204 |

User nullable fields retain their existing representation. Progress is required
for the new client and includes phone_verified/profile_complete/photo_skipped,
next_step=name/photo/complete, membership=linked/unlinked and nullable couple_id.
No progress flag grants couple authority or creates a relationship. Missing photo
is unresolved until an explicit skip or confirmed upload; already-complete accounts
retain completion. Legacy full registration payloads keep their established implicit
optional-photo skip. DOB/gender/zodiac/relationship are not mandatory for new signup.

Server-managed OTP retains Twilio Messaging, with cryptographic random six-digit
codes and HMAC binding to phone/challenge/purpose. Five-minute fixed expiry, five
failed guesses, single-use consumption and user creation under one transaction.
An established identity is never unverified by a send. Database budgets serialize
reservations across workers: three sends/phone/10min, 60s resend, configurable source
and global hourly budgets. Failed/uncertain delivery consumes the reservation;
there is no automatic SMS retry or production debug bypass. A reused send request_id
returns the accepted challenge while it remains current; a verify response lost
after consumption requires another proof, without duplicating the user.

Device sessions retain SimpleJWT signing: 10min access; 30day absolute refresh/session
expiry; refresh jti digest, prior digest and random rotation request ID; no raw OTP or
bearer credential stored. A refresh retry with the same request ID and immediately
previous proof reproduces the same signed response for 60s. A different replay
revokes the session. HTTP authorization checks current session status; socket
commands/watchdog check it too (watchdog bound 15s). Logout permits valid refresh
proof even when access expired. Incomplete users are denied protected REST/socket
access even with legacy JWTs; only setup and public catalogue capabilities remain.

Profile writes use request.user, reject another supplied phone, and keep identity,
verification, completion, privileges and engagement read-only. Exact nonempty
username uniqueness is a database constraint, preserving case/Unicode semantics.
Photos are owned directly by the authenticated profile, size/dimension bounded,
verified JPEG/PNG/WebP and re-encoded without metadata under random filenames.
No arbitrary URL, owner ID or MIME assertion can attach a profile image.

## Rollout and migration

### Development mock for SendOTPAPIView

The development `docker-compose.yaml` sets `OTP_MOCK_DELIVERY=true` and the shared
synthetic test code `OTP_MOCK_CODE=123456` by default
while the Twilio trial is unavailable. Pull `dev-core-architecture` and recreate
`web`; no frontend change or new endpoint is needed:

```sh
git pull --ff-only origin dev-core-architecture
docker-compose up -d --force-recreate web
```

`POST /api/auth/send-otp/` validates and reserves a normal challenge, then simulates
a new Twilio message with a generated `SM...` SID and `queued` status internally.
It does not construct a Twilio client or contact the provider. The API returns
200 with the same `data` fields (challenge ID, expiry/resend/server timestamps,
retry delay), and `message: "Mock OTP request accepted. No SMS was sent."`.
Each new eligible request gets a fresh challenge; a reused request ID retains
idempotency. Invalid phones, cooldowns and budgets still return their normal errors.

After recreating the container, request a **new OTP** from the app and enter
**123456** for any development user. Old requests created before this change
retain their old proof; do not reuse their request/challenge IDs. Wait for the
existing resend cooldown if necessary. The database stores a one-way HMAC, not a
decryptable OTP, so no database inspection is needed.

`verify-otp/` still requires a requested challenge for that phone. Incorrect codes,
five-minute expiry, five-attempt exhaustion, stale challenges and single-use
consumption are enforced normally. Phone/challenge binding remains distinct even
though the synthetic code is shared. The OTP is not returned in responses or logs.
Set a different six-digit ASCII `OTP_MOCK_CODE` in the local `.env` to change the
shared test code. Changing it or disabling mock delivery invalidates outstanding
fixed-code proofs. Invalid mock code configuration fails rather than using an
unintended value. This is for development accounts, not a deployed sign-in option.

Set `OTP_MOCK_DELIVERY=false` in the local `.env` and recreate `web` to resume
Twilio; the fixed code is then ignored and new codes are generated randomly.
Normal non-Compose settings default to real delivery with no fixed code. A real provider
failure still returns 503; it never silently falls back to mock success. This
change adds no migrations and preserves the KMP challenge contract. Development
Compose settings must not be used as production settings.

Initial delivery-mock validation: the isolated suite passed 119 tests with 10 PostgreSQL-only skips;
new API tests cover fresh mock challenges, unchanged response schema, idempotency,
validation/cooldown, zero Twilio calls, restoring real delivery and real-provider
failure without fallback. Django checks and migration consistency passed. No
real SMS, database migration or remote Docker restart was performed.

Shared-code validation: the isolated suite passed 126 tests with 10 PostgreSQL-only
skips. Seven added tests cover existing/new users, phone/challenge binding, expiry,
attempt limits, single use, configuration changes and real-delivery isolation.
Django checks and migration consistency passed. Docker restart and live-device
verification remain on the development laptop.

### Production rollout

1. Before rollout, run `manage.py audit_auth_identities` against an explicitly
   approved copy/environment. It reports counts, not identities, and mutates nothing.
   Resolve duplicate usernames and noncanonical phone records with affected users;
   never delete or silently rename accounts. Phones retain the existing 15-character
   model limit (plus and at most 14 digits); the client uses the same syntax.
2. Apply 0021/0022 in the approved deployment workflow, **not from this task**.
   0021 fails safely on duplicate nonempty usernames, invalidates existing short OTP
   proofs and adds schema fields. Existing user IDs/couples/completed flags remain.
   Database defaults support older app processes during a rolling deployment.
3. Deploy server before new mobile clients. Existing clients can continue legacy
   token issuance while `AUTH_ACCEPT_LEGACY_TOKENS=true`. New clients opt into
   device sessions and can exchange an existing valid legacy refresh proof once.
4. Announce a client support cutoff, then set `AUTH_ACCEPT_LEGACY_TOKENS=false`.
   Verification then rejects clients without `X-SriSu-Auth: auth-1` before consuming
   their OTP, and asks them to update rather than issuing unusable legacy tokens.
   Legacy long-lived tokens cannot be individually revoked; compatibility is a
   temporary release gate, not the final security policy. Signing-key rotation is
   a separate coordinated operation, not performed here.
5. Configure provider-side spending limits and trusted ingress source-IP handling.
   The service uses REMOTE_ADDR, never blindly trusts X-Forwarded-For. Defaults:
   OTP_GLOBAL_HOURLY_LIMIT=100; OTP_IP_HOURLY_LIMIT=10.

Rollback must not restore plaintext/replayable OTP handling or remove schema while
new clients depend on it. Feature rollout is backend-first. No shared database was
inspected/migrated and no signing credentials were changed during implementation.

Tests are explicitly included in `tools/workspace.py`; never use unrestricted
legacy chat test discovery. Auth API, protected-profile, upload, session replay,
legacy restriction and socket-revocation tests use synthetic identities/mocked SMS.
SQLite skips the row-lock tests; the disposable PostgreSQL 17.11 runner passed all
122 selected backend tests, including OTP send/consume, username, refresh and
legacy-upgrade concurrency. See `docs/workspace.md` for its isolated command.


## Guest client boundary

The frontend now offers persisted guest entry using the already-public
`GET /api/auth/interests/` catalogue. It creates no UserModel or DeviceSession,
uses no bearer token, and does not mount private navigation/sockets. Existing
private permissions remain unchanged. A dedicated anonymous API test covers
catalogue success, profile/feed/moment/connection/suggestion read denial and
profile/invitation write denial, without SMS or identity creation. Guest home is
a derived client preview, not a new server feature or public couple feed.

Visual source: SriSu Figma file `LztysD1YvINX7RwZpnhhTt`, supplied page `1:2`;
Onboarding and Space exports supplied by the user on 2026-09-28 after MCP quota
failure. Paired frontend branch `SriZan12/SriSu:dev-core-architecture`, starting
commit `23ccd1f374a00207f32a4ac124250033b743689e`. The frontend handoff records
both repositories and actual validation in `docs/flows/authentication.md`.
