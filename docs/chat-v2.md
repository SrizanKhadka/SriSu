# Chat v2 foundation contract and rollout

Status: additive foundation, disabled by default. The backend stores opaque client
envelopes and synchronization metadata. It does not implement, inspect, or claim
end-to-end encryption. Production writes must remain disabled until a reviewed
client protocol adapter exists on every supported platform.

All routes require the authenticated user to be a current member of the room's
accepted relationship. Unauthorized, former-member, and cross-room access is
concealed as `404`. Responses use `Cache-Control: private, no-store`.

## Capability and gates

`GET /api/chat/v2/capabilities/` returns:

```json
{
  "data": {
    "api_version": 2,
    "encrypted_writes_enabled": false,
    "protocol_status": "adapter_required",
    "requires_device_session": true,
    "supported_kinds": ["text"],
    "max_message_bytes": 4000,
    "max_envelope_bytes": 65536,
    "max_attachment_bytes": 15728640
  }
}
```

`max_message_bytes` is an optional client plaintext policy; the server cannot
inspect plaintext. `max_envelope_bytes` is the authoritative server-enforced
serialized-envelope limit after sealing.

Writes require all of the following:

- `CHAT_V2_ENCRYPTED_WRITES_ENABLED=true`;
- `CHAT_V2_PROTOCOL_STATUS=ready` (`test_adapter` additionally requires the
  separate default-false `CHAT_V2_TEST_ADAPTER_ENABLED` synthetic-test switch
  and is never advertised ready);
- both room participants in the non-empty `CHAT_V2_ALLOWED_USER_IDS` allowlist;
- a current, unrevoked device-session `sid` when
  `CHAT_V2_REQUIRE_DEVICE_SESSION=true`; and
- no legacy message with `sequence IS NULL` in the room.

Capabilities apply the same current-session and two-participant room readiness
checks. Defaults are fail-closed: writes false, status `adapter_required`, empty
allowlist, and required device session.

Attachments have the additional independent
`CHAT_V2_ATTACHMENT_STAGING_ENABLED=false` gate. Text pilot enablement does not
enable staging or permit non-empty `attachment_ids`. Only `text` is advertised;
image, video, and audio remain unsupported until an attachment AEAD format is
reviewed.

## Development Compose and another-laptop setup

Pulling the branch, applying migrations, and starting Docker does **not** enable
secure messaging. The checked-in configuration intentionally reports
`protocol_status=adapter_required` and keeps encrypted writes, the synthetic
test adapter, and attachment staging disabled. Do not change those values merely
to bypass the client unavailable screen: this branch has no production protocol
adapter or device/prekey registration contract yet.

On a new laptop, check out `codex/couple-chat-rebuild` in both repositories. In
the backend checkout, create the ignored local environment once and replace every
placeholder with local development values. Set `SRISU_DEV_LAN_HOST` to the
backend laptop's current LAN address; keep `POSTGRES_HOST=127.0.0.1` for commands
run directly on the host and `POSTGRES_PORT=5433` for Compose's published host
port. Compose overrides those values with the `srisu_db` service name and port
`5432` inside containers.

```sh
cp .env.example .env
docker compose build
docker compose up -d srisu_db redis
```

For an existing database, run the read-only lifecycle preflight before applying
the new migrations:

```sh
docker compose run --rm web python manage.py preflight_relationship_lifecycle
```

A brand-new empty PostgreSQL volume has no legacy schema to preflight; skip that
standalone command because the migrations run their own guards at the appropriate
point. Then apply the committed migrations in either case:

```sh
docker compose run --rm web python manage.py migrate
```

Do not run `makemigrations` for setup. The required migrations are committed.
After migration, check and complete the resumable legacy sequence backfill. If
the check reports remaining rows, repeat the bounded backfill command until the
final check reports `remaining=0`.

```sh
docker compose run --rm web python manage.py backfill_chat_v2 --check
docker compose run --rm web python manage.py backfill_chat_v2 --batch-size 500 --max-batches 10
docker compose run --rm web python manage.py backfill_chat_v2 --check
docker compose up -d web chat_outbox chat_maintenance
docker compose exec web python manage.py chat_v2_status
docker compose ps
```

`chat_outbox` continuously publishes bounded, content-free WebSocket wake-up
hints. `chat_maintenance` runs the existing bounded private-media/published-
outbox cleanup every five minutes. They are local/development supervisors; a
production deployment must schedule the one-shot commands using its own process
supervisor and monitoring.

The status command exposes no account IDs, message content, keys, credentials,
or allowlist contents. On this foundation branch, the expected safe output still
includes `encrypted_writes_enabled=false`, `protocol_status=adapter_required`,
and `attachment_staging_enabled=false`.

The app must use the backend laptop's LAN origin, not its own `localhost`. After
Authentication's device-session migration, sign out and sign in again on both
clients so their access tokens contain current `sid` claims. That is necessary
for the eventual secure path, but it cannot supply the missing E2EE adapter by
itself.

## HTTP contract

`GET /api/chat/v2/rooms/{roomId}/messages/?before_sequence=&limit=20` returns
newest-first messages and:

```json
{"data":{"room_id":"uuid","high_watermark":12,"messages":[],"has_more":false,"next_before_sequence":null}}
```

Every message contains `id`, nullable client `operation_id`, `sequence`,
`revision`, `sender_id`, `content_kind`, nullable opaque `envelope`,
`reply_to_id`, `attachment_ids`, desired-state `reactions`, `is_tombstone`,
`hidden_for_viewer`, `legacy_plaintext`, `created_at`, and nullable `edited_at`.
Legacy plaintext is never projected through v2 (`envelope` is null).
`high_watermark` is captured before the page query, and the query is bounded to
`sequence <= high_watermark`; a concurrent write is therefore recovered by the
next hint/catch-up rather than appearing above the response watermark.

`POST /api/chat/v2/rooms/{roomId}/messages/` accepts:

```json
{"operation_id":"uuid","content_kind":"text","envelope":{"opaque":"client-defined"},"reply_to_id":null,"attachment_ids":[]}
```

It returns `201` on first commit and `200` on an exact replay:

```json
{"data":{"operation_id":"uuid","replayed":false,"change_sequence":12,"message":{}}}
```

The operation UUID is stable per actor and room. Reusing it with a different
canonical payload returns `409`. A recovered history message exposes its nullable
`operation_id` so a client can reconcile a provisional row after a lost response.

`GET /api/chat/v2/rooms/{roomId}/changes/?after_sequence=0&limit=50` returns:

```json
{"data":{"room_id":"uuid","high_watermark":12,"changes":[],"has_more":false,"next_after_sequence":12}}
```

`next_after_sequence` is the highest scanned room sequence, not the last visible
change. It advances across user-private `delete_for_me` gaps, including a page
with zero visible rows, without emitting a partner-visible no-op.
Changes are likewise bounded to the response's captured `high_watermark`.

`POST /api/chat/v2/rooms/{roomId}/operations/` accepts one of:

```json
{"operation_id":"uuid","action":"edit","message_id":7,"expected_revision":1,"envelope":{}}
{"operation_id":"uuid","action":"delete_for_everyone","message_id":7,"expected_revision":1}
{"operation_id":"uuid","action":"delete_for_me","message_id":7}
{"operation_id":"uuid","action":"set_reaction","message_id":7,"envelope":{}}
```

For reaction removal, send `"envelope": null`. The response matches the send
operation envelope (`operation_id`, `replayed`, `change_sequence`, `message`).
Expected-revision mismatch is `409`. Edit is allowed through exactly 15 minutes
after creation (`age <= 15m`); delete-for-everyone through exactly 48 hours
(`age <= 48h`). At either boundary, `+1 microsecond` is rejected. Exact operation
replays are returned before the current-window check. Reactions on tombstones are
concealed as `404`.

`PUT /api/chat/v2/rooms/{roomId}/receipts/` accepts:

```json
{"operation_id":"uuid","delivered_through":12,"read_through":12}
```

and returns:

```json
{"data":{"operation_id":"uuid","replayed":false,"room_id":"uuid","delivered_through":12,"read_through":12,"change_sequence":13}}
```

The client should submit the highest authenticated/decrypted inbound message
sequence. The server normalizes cursors to actual visible, non-legacy messages
received by that user. Reaction, receipt, deletion, and outbound-only sequence
advances cannot amplify receipt changes.

Attachment staging routes exist for isolated tests but remain separately gated:

- `POST /api/chat/v2/rooms/{roomId}/attachments/` uses multipart fields
  `operation_id`, lowercase SHA-256 `ciphertext_sha256`, and
  `application/octet-stream` `ciphertext`;
- `GET /api/chat/v2/rooms/{roomId}/attachments/{attachmentId}/` downloads an
  authorized staged-owner or visible claimed ciphertext; and
- `DELETE` on the same URL cancels an unclaimed owned stage.

Errors follow the core error handler: validation `400`, authentication `401`,
concealed authorization/resource `404`, idempotency/revision/window conflict
`409`, scoped throttle `429`, and fail-closed gate or incomplete backfill `503`
with code `encrypted_chat_unavailable`. Default rates are 120 v2 reads/minute,
60 mutations/minute, and 10 attachment mutations/minute per authenticated scope.

For live content-free hints, send this authenticated WebSocket command on the
existing `/ws/chat/` connection:

```json
{"action":"subscribe_room","request_id":"client-stable-id","payload":{"chat_room_id":"uuid"}}
```

The authorized acknowledgement is:

```json
{"type":"success","action":"subscribe_room","request_id":"client-stable-id","message":"Room subscribed.","data":{"chat_room_id":"uuid"},"protocol_version":1}
```

Unauthorized rooms return the existing concealed socket `404` error; exceeding
the 20-room connection bound returns `429`. Published `chat_v2_changed` events
contain only room/high-watermark metadata and tell the client to call `changes/`.

On successful relationship acceptance or breakup, both participants' existing
user socket groups receive a content-free reconciliation hint after commit:

```json
{"type":"event","action":"relationship_changed","message":"Event","data":{"connection_id":7,"chat_room_id":"uuid","revision":1,"status":"ACCEPTED"},"protocol_version":1,"event_id":"uuid","emitted_at":"timestamp"}
```

Wire `status` is `ACCEPTED` or `ENDED`; the database's historical breakup token
is internal and is never emitted.

The event is best-effort and may be duplicated or lost. It authorizes no state;
clients reconcile through `GET /api/social/have-couple-connection-requested/`.
For `ENDED`, the consumer re-reads the relationship by participant and emits
only this authoritative lifecycle projection; it never forwards stale or
client-controlled room data. Subsequent room traffic is denied and follows the
ordinary `access_revoked` behavior.

## Relationship identity and legacy cutoff

Each accepted `CoupleModel` owns exactly one `ChatRoom`; relinking the same users
creates a new relationship and room. The old room and messages remain stored but
former members cannot authorize them. Accepting a relationship invalidates other
pending invitations involving either participant.

The first committed v2 message permanently sets `ChatRoom.encrypted_v2_started_at`.
After that cutoff, legacy plaintext send/media upload/typing/receipt paths reject
the room, legacy mutation paths reject non-legacy messages, and legacy history and
room preview do not project a blank representation of v2 ciphertext. Never clear
this marker to restore a plaintext client.

## Migration and deployment order

Backend schema and compatibility code must deploy before a v2 client. This is not
an atomic two-repository release.

1. Back up the database and drain old HTTP/WebSocket chat writers. Do not run a
   mixed old/new backend fleet during the sequencing transition.
2. Run `.venv/bin/python manage.py preflight_relationship_lifecycle` against the
   production schema. `social.0014` also aborts before constraints if a legacy
   membership belongs to a couple without a relationship identity, or if its
   lifecycle backfill leaves one user in multiple active couples. Errors report
   bounded couple/user IDs. Explicitly link or retire each ambiguous relationship
   under an approved operator runbook; neither preflight guesses or mutates data.
   The same command reports historical global `DELETE_FOR_ME` message flags that
   have no per-user deletion record. Because those rows contain no actor identity,
   an operator must reconcile the deleting participant explicitly; assigning the
   sender or receiver by guess could re-expose or over-delete private history.
   Until reconciled, these ambiguous rows are quarantined from both viewers in
   legacy history/live/room previews, v2 pages, reply previews, and media delivery.
3. Apply migrations. `chat.0004` is schema-only for message history and performs
   a bounded duplicate-couple-room preflight before its uniqueness constraint. It
   aborts with `couple_id:count` evidence rather than guessing which history to
   detach. Resolve any reported duplicates explicitly and retry.
4. Run `.venv/bin/python manage.py backfill_chat_v2 --check`. With writers still
   drained, repeatedly run `backfill_chat_v2 --batch-size 500 --max-batches 10`
   until `remaining=0`, then run `--check` again. It orders legacy rows by
   `(timestamp,id)`, is resumable, and refuses unsafe mixed chronology. New-code
   legacy writers also leave rows unsequenced while a backlog exists, but that is
   a recovery guard, not permission for a mixed-version rollout.
5. Deploy/restart only the new backend code and resume legacy traffic. Keep v2
   writes disabled.
6. Run `.venv/bin/python manage.py dispatch_chat_outbox --limit 100` from a
   frequent externally supervised production job. One invocation publishes one
   bounded batch. Failures
   return rows to pending with bounded exponential backoff, jitter,
   `available_at`, and a non-sensitive error code. Redis/Channels is only a wakeup
   hint; clients recover from `changes/`.
7. Schedule `.venv/bin/python manage.py cleanup_chat_media --limit 100` through
   the production scheduler as a bounded recurring job. It removes
   expired/unclaimed legacy uploads and
   cancelled, expired, or tombstoned attachment blobs. A storage deletion
   failure retains its database row for a later retry. It also prunes bounded
   published outbox-delivery metadata after seven days by default while
   retaining the authoritative `ChatChange` catch-up log.
8. After the client crypto adapter is reviewed and deployed, allowlist both
   synthetic/internal participant IDs, set protocol status ready, and enable
   writes for the limited pilot. Keep attachment staging false.

Rollback before any v2 room starts: disable writes, restore
`adapter_required`, empty the allowlist, stop the outbox job, and leave additive
schema/data in place. After `encrypted_v2_started_at` is set, rolling back to a
plaintext-only backend/client is unsafe; keep the compatibility backend deployed
and disable new v2 writes while repairing clients. Do not reverse migrations or
delete ciphertext/history as an operational rollback.

## Private media origin requirement

The active legacy uploader accepts only decoded JPEG/PNG raster images, applies
file/total, dimension, and pixel limits, and always re-encodes to JPEG to strip
metadata. Direct client `media_url` and `sticker_url` values are rejected in
couple rooms. Video, voice, HTML, SVG, and arbitrary files are unsupported.
Responses expose only guarded first-party delivery routes, never storage URLs.

The paths `/media/chats/media/`, `/media/messages/media/`, and
`/media/chat_encrypted/` must not be served directly by nginx, a CDN, or a public
object-store origin. Route them through the authenticated Django delivery views
(or an equivalent authorization service). Ownerless unattached legacy rows are
quarantined with `404`; files are preserved. A public media bucket or bypassing
proxy defeats the application authorization checks.

All chat mutations use the same database lock order: participant user rows in
ascending ID order, then the room, then (when present) the device session. A
relationship end takes participant rows before its connection/couple/room, so a
committed revocation is linearizable with both legacy and v2 writes.

`AUTH_ACCEPT_LEGACY_TOKENS=true` is authentication-rollout compatibility:
sid-less WebSocket tokens have no `DeviceSession` row to revoke. Before a
production encrypted-chat pilot, set it false, force existing clients to
reauthenticate, and verify sid-bearing socket coverage. V2 mutations still
require a current session. This compatibility mode is a release blocker, not an
end-to-end encryption guarantee.

This foundation does not yet define expiry/resnapshot semantics for
`ChatChange`, message ciphertext, or account-deletion retention, so those rows
are not automatically pruned. Django also does not delete arbitrary FileField
blobs merely because a row cascades. A reviewed retention/account-deletion job
and client cursor-expiry contract remain production release requirements; do
not delete `ChatChange` ahead of that contract.

Frontend integration references:
`../SriSu/docs/architecture/decisions/002-chat-e2ee.md` and
`../SriSu/docs/flows/couple-chat.md` on the paired frontend branch.
