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
  A concurrently locked source/target couple also returns the existing retryable
  409 rather than waiting while holding the acting user's mutation lock.
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

### Follow/disconnect lock-order repair — 26 November 2026

Follow acquires the acting user's row before reading their current membership,
matching relationship lifecycle's user-before-couple order. It then acquires both
source and target couple rows in ID order with PostgreSQL `NOWAIT`. The
nonblocking couple acquisition also avoids waiting for another couple writer
that could need this user's foreign-key lock. Only PostgreSQL
`lock_not_available` (`55P03`) becomes the existing follow-conflict 409. Other
database errors remain errors, and the transaction rolls back before any retry or
snapshot read. SQLite does not establish this locking behavior.

Target participant users are not locked: following does not mutate them or create
foreign keys to them. The actor lock protects the follow/version user references;
the source/target couple locks protect publication/revocation. Context is checked
after these locks, and incoming projections still validate current membership,
identity approval, blocks and target audience. Existing follow/unfollow and Go
private row/version ordering remains unchanged. No API fields or migration change.

Deterministic PostgreSQL regressions coordinate source disconnect, target
disconnect, and a profile-interest writer with a follow attempt. The source test
reproduced an actual deadlock before this repair. The existing concurrent
same-version and Go private tests remain part of the verification suite.

Verification for this repair: the five selected suites (`social.test_couple_feed`,
`social.test_public_follows`, `social.test_couple_profile`,
`social.test_moment_publication`, `social.test_settings`) passed **153 tests** on a
disposable PostgreSQL cluster, with no skips. The cluster was stopped and removed.
The pre-fix single source-disconnect test failed with a real PostgreSQL deadlock;
its coordinated schedule now succeeds. Evidence logs remain local at
`/tmp/srisu-follow-disconnect-before.log` and
`/tmp/srisu-follow-locking-postgres-final.log`.

The focused SQLite suite passed 14 tests with 5 PostgreSQL-only skips (19 total).
A fresh disposable `tools/check_explore_http.py --spawn --moment-video` run also
passed all 74 HTTP requests, including genuine synthetic HLS authorization and
withdrawal. Its sanitized report is
`/tmp/srisu-explore-http-locking-report.json`; the server, temporary media and
credentials were removed after the run. All 17 unrelated local files still match
their baseline SHA-256 hashes. No application database was migrated or modified.


### Adversarial QA and lifecycle concurrency — 26 November 2026

A subsequent QA pass found and reproduced additional material failures using
coordinated PostgreSQL transactions, rather than relying on races to happen:

- Publishing held connection then couple, while Go private held couple then
  connection. The two requests could deadlock. Profile mutations now acquire
  current participant users in ID order, then connection, couple and memberships,
  all with `NOWAIT` inside a savepoint. The locked audience is checked again,
  followed by the existing active/member authorization and section revision checks.
  Only PostgreSQL `55P03` becomes the existing `ProfileConflict` 409; unrelated
  database errors propagate. The conflict means no withdrawal was committed:
  reload and retry. The regression explicitly retries and verifies the newly
  published Moment becomes private. If withdrawal commits first, a later explicit
  public upload is separate intent; previous public Moments remain private.
- Legacy Moment GET requests that record a view retained a Moment row before
  their viewer foreign-key check. This could deadlock with withdrawal holding the
  participant barriers. That path now obtains the same connection/couple barrier
  as `POST viewed` before the Moment row. Read-only `record_view=false` is unchanged.
- Publication and appreciation could deadlock with disconnection: the Moment
  writer held the connection while deferred creator/notification-recipient foreign
  keys needed user `KEY SHARE`; disconnect held those users `FOR UPDATE` while
  waiting for the connection. `end_connection` now takes participant
  `FOR NO KEY UPDATE` locks. These still exclude concurrent user/relationship
  mutations and deletion, but permit foreign-key references. Disconnect changes
  only the non-key `is_engaged` field. Existing connection, couple, membership,
  revision and actor checks are unchanged. Other lifecycle operations retain
  their existing lock mode; this is not a generalized lifecycle-policy rewrite.
- Incoming followers now require the target's current approved public identity
  as well as each source's. Previously a target name/photo edit made the follower's
  status unavailable while the target still received that incoming identity row.
  Rows remain hidden until approval is current. A profile edit keeps the same
  recipient audience; explicit Go private still deletes follows permanently.
- Follow confirmation accepts the actual JSON boolean `true` only. Strings and
  numbers such as `"true"` and `1` are rejected without changing the following
  version or creating an edge. The current client already sends a boolean.

Eleven new regressions cover these cases, unexpected database errors, and bounded
incoming-list queries for 1 versus 20 distinct source couples. Existing concurrent
Follow/profile tests were adjusted for the explicit retryable conflict. Added
PostgreSQL schedules exercise publication-first and revocation-first ordering,
both profile/disconnect orders, legacy view recording, publication/disconnect and
appreciation/disconnect. Post-disconnect requests cannot read the public Moment,
add appreciation, or access an old recipient note.

Final checks after all fixes:

- **250/250 PostgreSQL tests passed, no skips** across `social.test_couple_feed`,
  `social.test_public_follows`, `social.test_couple_profile`,
  `social.test_moment_publication`, `social.test_settings`, `social.test_moments`,
  `social.test_moment_replies`, `social.tests`, and `chat.test_matrix`, using the
  disposable PostgreSQL 17 runner with that explicit suite allowlist.
- `tools/workspace.py test`: **549 total, 498 passed, 51 skipped** in isolated
  SQLite. PostgreSQL-only cases require the separate run above.
- `tools/workspace.py migrations`: **No changes detected**. No schema, new API
  field, dependency or migration was added by these QA fixes.
- A fresh `tools/check_explore_http.py --spawn --moment-video` run passed **74/74
  requests**, including real synthetic HLS bytes and post-withdrawal denial.
- `git diff --check` is clean. All **17 unrelated local files** retain their
  recorded SHA-256 values. Verification used disposable databases and did not
  notify real users. Publication is tracked in backend PR #50; it remains unmerged.

Local evidence: `/tmp/srisu-profile-publication-before.log`,
`/tmp/srisu-profile-legacy-before.log`, and
`/tmp/srisu-moment-disconnect-before.log` contain the deterministic pre-fix
PostgreSQL deadlocks; `/tmp/srisu-follow-target-before.log` contains the failing
consent projection regression. Final logs are
`/tmp/srisu-global-adversarial-postgres-final.log`,
`/tmp/srisu-global-adversarial-sqlite-final.log`, and
`/tmp/srisu-global-adversarial-http-final.log`; the sanitized HTTP report is
`/tmp/srisu-global-adversarial-http-final-report.json`. Temporary servers, databases,
media and credentials were removed by their runners. Device UI, native video
smoothness and large production-directory load are separate verification tracks.
The previously identified user-facing block/report workflow gap remains outside
this backend QA repair; existing server block authorization is exercised.
