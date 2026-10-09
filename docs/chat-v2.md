# Matrix-only couple chat contract and cutover

Status: selected implementation. SriSu couple chat uses a private Synapse
1.162.0 homeserver and client-side Matrix E2EE. Django remains authoritative
for SriSu accounts, device sessions, accepted couple membership, blocking and
relationship revocation. Django stores stable identity/room mappings, but no
Matrix message content, device keys or attachment ciphertext.

The previous Django plaintext/WebSocket transport and the temporary opaque
envelope transport were retired together. Migration
`chat/0006_destroy_legacy_chat_transport.py` irreversibly deletes their rows and
tables and refuses to run unless the operator supplies the exact reviewed
confirmation phrase. It keeps `ChatRoom` only as the stable
couple-relationship UUID plus its two users, couple reference and timestamps.
Non-couple room rows are deleted; `SingleConnectionModel` itself remains intact
for BLOCKED privacy policy. The additive migration
`chat/0005a_durable_matrix_revocation.py` first snapshots both Matrix localparts
and changes the room link to `SET_NULL`, leaving a durable retry tombstone if a
Django room or account is deleted. The tombstone is read-only/non-deletable
through Django admin. Historical migration `0005` remains unchanged, and the
destructive `0006` depends on the additive safety migration.

## Django control-plane API

The sole route under `/api/chat/` is:

```text
POST /api/chat/v2/rooms/{chatRoomUuid}/matrix/session/
```

It requires a current SriSu access token with a live `DeviceSession.sid` and
current membership in the room's accepted couple. Unrelated, blocked, former or
revoked members receive a concealed `404`; an expired/revoked Django session
receives `401`; throttling returns `429`; unavailable provisioning returns the
generic `503 temporarily_unavailable`. Responses are `private, no-store`.

The response supplies the private homeserver origin, stable Matrix room/member
IDs, membership epoch, and a short-lived `org.matrix.login.jwt` login credential.
After remote token work, Django rechecks both the `DeviceSession` and the full
relationship policy (including an either-direction BLOCKED edge) immediately
before returning it. It also binds the response to the still-current Matrix
room ID, alias, membership epoch and exact member snapshot, so a concurrent
rotation cannot return a stale bootstrap.
The JWT is replayable until its short expiry and is not claimed to be one-use.
The app requests no refresh token, verifies the returned Matrix user/device IDs,
and uses standard Matrix APIs directly for sync, encrypted events, key traffic,
receipts and media.

Django acceptance commits only deterministic mapping/retry rows. Remote Synapse
provisioning runs after commit and through the reconciliation worker. A dedicated
server-controlled `srisu_provisioner` identity creates room-version 11 rooms,
receives power 100 while both humans remain at power 0, sets all membership and
policy-changing state to power 100, and leaves after setup. Room version 11 is
intentional: creator ownership is verified from the `m.room.create` state
event's top-level sender (not a removed content field). Rooms are private,
invite-only, guest-forbidden, non-federated, history-visible from invitation,
and configured with `m.megolm.v1.aes-sha2`. Before activation Django verifies
the creator, locked power levels, directory privacy and that the exact two
partners—and no provisioning or rogue identity—are the only joined members. An
existing deterministic alias is accepted only after the same verification.
The Synapse configuration loads SriSu's fail-closed room-policy module. Only the
exact `srisu_provisioner` identity may create a room, create a deterministic
`#srisu_chat_<uuid>_e<epoch>` alias, or invite a SriSu Matrix identity. Human
accounts cannot use raw Matrix APIs to create rooms, aliases, or invitations,
and directory publication remains globally denied. The loaded module exposes a
non-secret, read-only policy-version probe; cutover readiness requires its exact
policy version, server name and provisioning identity rather than assuming that
the checked-in Synapse configuration was actually loaded.

Ending a relationship, deactivating an account, or adding a BLOCKED edge marks
the mapping revoke-pending and removes both members with durable retries. When a
remote create succeeded before its room ID was saved, revocation resolves the
deterministic alias first. A fresh in-flight provisioning claim is allowed to
finish before alias-only revocation; a superseded creator's exact returned room
is placed in a durable cleanup queue and retried without overwriting the current
generation. If a previously revoked relationship becomes eligible again, it
starts a new membership epoch and alias and never reopens the old room. Already
downloaded ciphertext cannot be retracted.
An already exchanged Matrix access token is not synchronously revoked by a
Django logout. The replayable login credential lasts 120 seconds by default
(maximum 300), while Synapse limits resulting non-refreshable access to 300
seconds by default (maximum 900). Thus the default worst case from bootstrap
issuance is 420 seconds; once exchanged, remaining access is at most 300
seconds. Client logout shortens that window when the device is reachable.

Bootstrap and the reconciler periodically authenticate to Synapse and verify
the alias-to-room binding, creator, encryption, locked power levels, private
directory visibility, and exact joined-member set. Confirmed drift first queues
and retries removal from the old room, then increments `membership_epoch` and
provisions a new deterministic alias. Network, rate-limit, or Synapse 5xx
failures do not rotate a valid room. If the relationship becomes ineligible
during recovery, revocation completes without provisioning a replacement.

## Development Compose / another laptop

After pulling the same feature branch in both repositories:

```sh
test -f .env || cp .env.example .env
# For this HTTP-only LAN setup, edit the existing .env and set DJANGO_DEBUG=true.
python3 tools/configure_matrix_dev.py --public-base-url http://YOUR_LAN_IP:8008
docker compose config --quiet
docker compose pull synapse
docker compose build web matrix_reconciler
# Quiesce every old web/chat worker and remove orphan containers. Do not add -v
# or --volumes: the database and Synapse volumes are intentionally preserved.
docker compose down --remove-orphans
docker compose up -d srisu_db redis synapse_db synapse
# Before any destructive step, run both from the other laptop/device network
# (replace the placeholder) and confirm the policy response names the exact
# configured server/provisioner and policy_version srisu-room-policy-v1.
curl -f http://YOUR_LAN_IP:8008/_matrix/client/versions
curl -fsS http://YOUR_LAN_IP:8008/_synapse/client/srisu/policy/v1
docker compose run --rm web python manage.py migrate \
  chat 0005a_durable_matrix_revocation
docker compose run --rm web python manage.py reconcile_relationship_rooms
docker compose run --rm web python manage.py reconcile_relationship_rooms --apply
docker compose run --rm web python manage.py reconcile_matrix_rooms --limit 100
docker compose run --rm web python manage.py matrix_status --require-ready
# Take and verify the approved database and legacy-chat storage backups here.
# Refresh the short-lived live-readiness attestation after the backup finishes.
docker compose run --rm web python manage.py matrix_status --require-ready
docker compose run --rm \
  -e SRISU_CONFIRM_DESTROY_LEGACY_CHAT=DESTROY_LEGACY_CHAT_TRANSPORT_V1 \
  web python manage.py migrate
docker compose run --rm web python manage.py matrix_status
docker compose run --rm web python manage.py purge_legacy_chat_storage
docker compose run --rm web python manage.py purge_legacy_chat_storage \
  --execute --confirm DESTROY_LEGACY_CHAT_MEDIA
docker compose up -d --force-recreate web matrix_reconciler
curl -f http://127.0.0.1:8008/health
# Repeat the public-origin check after restart before app testing:
curl -f http://YOUR_LAN_IP:8008/_matrix/client/versions
```

Repeat `reconcile_matrix_rooms --limit 100` when more than 100 mappings require
work. Do not proceed to the gated migration until `matrix_status --require-ready`
exits successfully. It requires valid Matrix configuration, the additive schema,
exactly one ACTIVE mapping for every eligible relationship, no ACTIVE mapping
for an ineligible relationship, exact ACTIVE deterministic Matrix identities
for both members, no pending/failed/provisioning/revocation work,
no queued stale-room cleanup, a live policy-module probe, and live verification
of every eligible room. Eligibility is counted from accepted current Couple
membership rather than from existing ChatRoom rows, so a missing room cannot be
hidden from readiness. A successful run writes a
configuration- and relationship-state-bound database attestation valid for five
minutes by default and never more than fifteen. Migration `0006` independently
recomputes those bindings and refuses an absent, expired, or mismatched
attestation without calling Synapse.
The backup step is an operator checkpoint, not an optional comment to skip.

Use the backend laptop's reachable LAN address, not the phone/simulator's own
`localhost`. The setup command deliberately refuses local HTTP unless the
existing `.env` explicitly contains `DJANGO_DEBUG=true`; it never replaces the
file. Production-like environments must use trusted HTTPS. Keep
`MATRIX_SERVER_NAME` stable after Synapse creates
data because it is part of every Matrix identifier. Sign out and sign back in on
both devices after the authentication/session migration so the SriSu access
tokens contain current `sid` claims.

`matrix_reconciler` is the only chat worker. The former outbox and maintenance
workers no longer exist. Redis remains because the couple feed still uses it.

## Destructive legacy cleanup

Database cleanup happens in irreversible migration `chat/0006`, only while the
old web/chat workers remain quiesced and after the additive `0005a` migration,
Matrix reconciliation, strict live readiness checks, and a verified backup. The
migration fails before deleting any chat row unless
`SRISU_CONFIRM_DESTROY_LEGACY_CHAT` exactly equals
`DESTROY_LEGACY_CHAT_TRANSPORT_V1`. Supply it only to the reviewed cutover
`migrate` command, then clear it; do not persist it as a normal application
setting. External/object
storage is intentionally separate because database migrations must not perform
unbounded remote I/O. First inspect the fixed allowlist:

```sh
docker compose run --rm web python manage.py purge_legacy_chat_storage
```

It recursively lists only:

- `chats/media/`
- `chats_media/`
- `messages/media/`
- `chat_encrypted/`

It is dry-run by default. After checking the output, deletion requires both the
execution switch and the exact confirmation phrase:

```sh
docker compose run --rm web python manage.py purge_legacy_chat_storage \
  --execute --confirm DESTROY_LEGACY_CHAT_MEDIA
```

The command has no configurable prefix and tests prove that couple profile,
Moment and generic media namespaces are not traversed or deleted. Retired media
URLs remain denied until operators have completed cleanup everywhere.

Tracked sample chat uploads in the Git working tree require an explicit reviewed
file deletion. Removing them from the current branch does not erase copies from
Git history; no force-push/history rewrite is part of this cutover.

Migrations `0005a` and `0006` were prepared together for this unpublished
cutover. A machine that only pulled the earlier remote feature branch has not
applied them. If an operator manually copied or applied any unpublished local
variant of `0006` or the removed `0007`, stop: do not fake migration-recorder
rows. Inspect that database and restore or reconcile it with a reviewed recovery
plan before continuing.

## Profile plans and story prompts

Creating a plan no longer authors a plaintext chat card. Story prompt requests
are stored as idempotent `CoupleStoryInviteModel` domain records. Responses return
only the saved domain object and never fake `message_id`, `room_id` or delivery
success. If a product flow wants an encrypted card in chat, the authenticated
client must send that content through Matrix after the domain save succeeds.

## Rollout and rollback

Deploy Synapse and the Django Matrix control-plane code, quiesce the old web and
worker containers without deleting volumes, apply only through the additive
`0005a` migration, backfill and provision rooms, require a ready status, and
take a verified database/storage backup. Refresh the short-lived readiness
attestation immediately before applying `0006` with the
one-time exact destruction confirmation, purge the allowlisted storage, start
the services and release Matrix-capable clients. Migration `chat/0006` is an
explicit point of no return for the old message data. Rollback means restoring
the verified pre-cutover database/storage backup and compatible old code;
reversing the migration cannot reconstruct destroyed data.

Normal settings are fail-closed when Matrix configuration is absent. Secrets,
login JWTs, access tokens, message content and Matrix error bodies must never be
logged. The development topology is not a production deployment; production
still needs TLS/reverse proxy, secret management, backups, monitoring and a
tested Synapse upgrade process.
