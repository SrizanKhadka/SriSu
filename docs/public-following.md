# Explicit public following

This is separate from private Faves. No migration reads, copies, or publishes an
existing Fave. A follow is one individual's explicit choice, never a claim that
their partner also follows. It creates no chat, invitation, or notification.

Before enabling Follow, explain that the target couple's current members will
see the user's approved public name and avatar. Require positive confirmation
before sending `share_identity: true`. Both source and target must be active
couples with current mutually approved public identity. Existing private Faves
still work independently. An unapproved/private target is unavailable for public
following even when its numeric ID is known.

## API

All routes require authentication, use private/no-store responses, and follow the
existing `COUPLE_FEED_ENABLED` feature gate.

- `GET /api/social/couple-follows/{couple_id}/` returns `data` containing
  `couple_id`, `is_following`, `can_follow`, `reason`, and the requester's `version`.
  Reason is null, `connect_partner`, `approve_public_identity`, `unavailable`, or
  `own_couple`. It is never another user's follow state.
- `PUT` on the same path requires exactly `{ "share_identity": true }` and returns
  the same snapshot (201 for creation, otherwise 200). It never reads private
  Faves. Repeated writes cannot create duplicate records.
- `DELETE` has no body and returns the same snapshot with the edge removed.
  Removal remains available after the target becomes unavailable.
- `PUT` and `DELETE` accept `If-Match: <version>`. A stale value produces 409;
  clients reload the snapshot before offering another write. Each user's writes
  are serialized, including contradictory requests when no edge existed yet.
- `GET /api/social/couple-followers/` returns only the requester's current couple's
  incoming list. `data` contains `couple_id`, `results`, `next`, and `previous`.
  Unlinked accounts receive null couple ID and an empty list. Supplying a target
  couple parameter is rejected. Each row contains `user_id`, `couple_id`,
  `display_name`, `avatar_url`, and `followed_at`, describing only the opted-in
  individual. It does not expose their partner's name or mark that partner as a
  follower. `couple_id` links to their already-public couple profile.

Pagination accepts `page_size` (default 10, max 50) and a signed `cursor` from
`next`. Cursors bind the requester and current membership audience. Every page
rechecks active relationships, blocks, and identity consent. A filtered page may
have fewer rows or be empty with a next link; clients must preserve pagination.
No unfiltered count reveals hidden followers.

## Revocation and media

Follow records bind the source membership, identity proposal version, and target
membership IDs. A changed identity proposal requires fresh follow confirmation.
Disconnected/replaced memberships, blocks, or invalid approvals cannot disclose
an old follower identity. Old private Faves are neither migrated nor affected.

Go private (`sharing` action `revoke`) atomically changes every existing public
Moment to private, including expired/archived rows, increments each affected
Moment revision, and deletes inbound/outbound public follows for that couple.
It performs this withdrawal even if the profile proposal was already empty.
Profile reapproval never republishes old Moments or restores deleted follows.
An intentional later public Moment upload remains a separate explicit action.
The existing shared couple lock serializes publication/follow writes with
withdrawal. Feed, sequence, detail, photos, video playback, and previously issued
stream cookies all continue through the shared current Moment authorization.
Owners retain their private Moment access.

Outsider Moment creator names and avatars use the approved identity projection;
without approval the label is `A SriSu member` and the avatar is absent. Direct
creator-photo requests also enforce approval. Existing numeric creator IDs remain
for released-client compatibility. This is not a change to members' private
profile/Moment access.

## Deployment and verification

Apply additive migration `social.0026_public_couple_follows` before enabling the
new client flow. There is no data backfill and no disclosure of historical Faves.
The privacy withdrawal change applies to existing clients' sharing revoke action.
Backend routes are additive; older clients remain compatible.

`social.test_public_follows` covers opt-in/authorization, private Faves separation,
conditional writes, blocking, identity and relationship changes, cursor isolation,
all Moment media withdrawal including issued video cookies, retained owner access,
and no republication. PostgreSQL-only tests cover competing conditional follows
and following concurrently with Go private. Use the isolated workspace runner;
SQLite cannot establish row-lock concurrency behavior.


### Repeatable live HTTP verification

From the backend checkout, use the locked `.venv` and installed FFmpeg:

```sh
.venv/bin/python tools/check_explore_http.py --spawn --moment-video --report /tmp/srisu-explore-http-report.json
```

This reuses `tools/core_integration_server.py --explore`, applies the current
migrations to a disposable SQLite database, generates synthetic accounts/photos
and a short video, and serves only on loopback. It checks real JWT-authenticated
requests for Global search, shared-interest filtering, cursor pagination, private
Faves, explicit individual Follow and conditional duplicates, incoming-list
permissions, paginated Moments, creator privacy, appreciation permissions, and
Go private. JPEG bytes and HLS playlists/segments work before withdrawal; the
same URLs and issued stream cookie fail afterward. Reapproving public identity
must not restore old Moments or follows. The JSON report contains method/path,
status and elapsed time only; no token, cookie, query text, or response bodies.

Each invocation creates fresh fixtures because revocation intentionally mutates
them. The helper stops its server and removes temporary tokens/media on success
or failure. Omit `--moment-video` for photo/text-only checks. `--fixture` can
instead attach to a freshly started disposable `--explore` server whose token
file is private (0600); that server's caller owns its cleanup. Never point this
verifier at a real account or an application database.

Separately, from the frontend checkout, `python3 tools/core_integration.py
--explore` runs the production KMP repositories/DTOs against another fresh
loopback server. These HTTP checks do not establish native keyboard behavior,
video playback smoothness, production PostgreSQL concurrency, SMS or Matrix
integration; those need their own verification.
