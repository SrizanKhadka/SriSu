# Couple Moments API

## Review and implementation

Work stays on `dev-couple-moments`. Relevant history:

- `bb1e4d5` introduced moments, ordered photo records and CRUD.
- `41a0619` added page-number pagination, replacement and photo deletion. It saved changes before checking the combined photo count; returning HTTP 400 inside `atomic` could commit those changes. Object-level creator permissions, expiry and validated image uploads were missing.
- `05daf44` replaced gender-specific partners with exclusive couple memberships. The new API uses that membership architecture and accepted connections, rather than trusting `is_engaged` or client ownership fields.

The existing route names, moment metadata and list envelope remain. Views delegate validation to DRF serializers and transactional writes to `social/services/moment_service.py`. Notes use a small separate model and API; there are no public comments, ratings or broadcasts. Existing `SingleConnectionModel.BLOCKED` records are honored in either direction. No reporting API was found. Chat is now a separate Matrix E2EE transport and is not reused for Moment notes.

## Product and authorization defaults

- Authentication is required everywhere, including photos. An active couple has exactly two active members matching an accepted couple connection.
- Either partner creates; the server derives `created_by` and defaults `couple` to the caller's membership. A submitted couple ID must match their active membership. Ownership and timestamps are read-only. Moments cannot move between couples.
- Only the creator edits/deletes. A partner has read access, not modification access. Expired moments have no ordinary detail, media, update or delete access.
- Visibility stays `private` by default. `public` opts into the authenticated community feed. Private moments are visible only to their original current couple members. New writes with `friends` are rejected because no couple-friends audience is defined; legacy `friends` moments are treated as private. Archived/time-capsule records remain excluded.
- Moments disappear exactly at `created_at + 24 hours`; edits never renew them. Filtering happens in SQL independently of cleanup. Ordering is `-created_at, -id`. Existing page-number pagination is retained for compatibility: default 10, maximum 100. As with any offset/page feed, inserts or expiry between requests can shift page boundaries; clients should deduplicate IDs and refresh page one.
- Caption may be blank only if at least one photo remains. Caption maximum is 1,000 characters; existing title/date/mood/location/tags/partner-memory metadata remains supported. `moment_date` defaults to today's project-local date and does not control expiry/order. Tags allow 20 nonblank strings, each at most 50 characters.
- Photos are newly uploaded JPEG, PNG or WebP files, decoded/validated using DRF/Pillow. Each is at most 10 MiB by default. At most five total photos survive any update. Existing photo IDs may only remove photos of this creator's moment; arbitrary URLs, foreign IDs, malformed IDs and duplicates are rejected. No separate media attachment/reassignment endpoint exists.
- Retained photos keep relative order; uploads append in multipart submission order. Updates compact order to zero-based consecutive positions. `replace_photos=true` removes all old photos. `deleted_photo_ids` explicitly removes selected photos. Both controls still validate every submitted ID; replacement takes precedence when both are supplied.
- Other users may send a note only while they can view the moment. Notes to one's own couple are rejected. Messages are trimmed, nonblank and at most 1,000 characters. Sending is throttled to 30 attempts/hour/user, including invalid attempts. The sender and original partners can read; only the sender can delete. Editing is unsupported (405).
- Original membership IDs **and user IDs** are snapshotted. Reassigning a membership row, joining as a replacement, or leaving and rejoining cannot grant historical access. Moments are hidden whenever their original membership pair no longer matches. A breakup or inactive partner hides the couple's moments and disables recipient access to notes; the sender retains access unless blocked. An original partner retaining their membership can read historical notes once the couple is active, but replacement partners cannot.
- Notes remain accessible through the separate notes endpoints after moment expiry, under the same participant/blocking rules. Permanently deleting a moment cascades its notes and photos. Deleting a creator account deletes their moments; deleting a note sender deletes their notes; deleting a couple deletes all its moments. A surviving couple with fewer than two members is inactive.

## Endpoints

All paths below start with `/api/social/`. Authentication is JWT: `Authorization: Bearer <access_token>`. GET also supports HEAD; OPTIONS is provided by DRF.

| Methods | Path | Authentication | Permission / behavior |
|---|---|---|---|
| GET | `couple-moments/` | JWT | Visible, active, unexpired moments; `couple`, `page`, `page_size` filters |
| POST | `couple-moments/` | JWT | Either active partner; server-derived creator |
| GET | `couple-moments/{id}/` | JWT | Same visibility/expiry rules as feed |
| PUT, PATCH | `couple-moments/{id}/` | JWT | Creator only, still active/current/unexpired |
| DELETE | `couple-moments/{id}/` | JWT | Creator only; permanent deletion; legacy HTTP 200 message |
| GET | `couple-moments/{id}/photos/{photo_id}/` | JWT | Same authorization and expiry as moment; no-store image bytes |
| POST | `couple-moments/{id}/notes/` | JWT | Visible unexpired moment, outside its couple; 30/hour/user |
| GET | `moment-notes/` | JWT | Sender or original current recipient; optional `moment`, `page`, `page_size` |
| GET | `moment-notes/{id}/` | JWT | Same participant/blocking filters as note list; includes replies |
| GET | `moment-notes/{id}/replies/` | JWT | Note participants; paginated replies, oldest first |
| POST | `moment-notes/{id}/replies/` | JWT | Either original current partner, regardless of moment creator; 30/hour/user |
| DELETE | `moment-notes/{id}/` | JWT | Sender only; HTTP 204 |

Hidden/expired/nonexistent objects return 404. A visible object that the caller may not modify returns 403. Missing/invalid authentication returns 401. Validation returns 400 using the existing `error_details` / `message` envelope; throttling returns 429. Unexpected storage/database exceptions roll back and return server errors without disclosing private content through the API. Run with `DEBUG=False` outside development.

## Examples

Create a public moment with two photos (repeat the `photos` multipart key):

```sh
curl -X POST https://api.example/api/social/couple-moments/ \
  -H "Authorization: Bearer <access_token>" \
  -F 'caption=Coffee and a rainy afternoon' \
  -F 'visibility=public' \
  -F 'photos=@cafe.jpg' -F 'photos=@walk.png'
```

Representative HTTP 201 response (IDs/timestamps are illustrative):

```json
{
  "message": "Couple moment saved successfully.",
  "data": {
    "id": 42,
    "couple": 7,
    "created_by": 11,
    "title": null,
    "caption": "Coffee and a rainy afternoon",
    "moment_date": "2026-09-14",
    "mood": null,
    "location_name": null,
    "visibility": "public",
    "tags": [],
    "partner_memory": null,
    "created_at": "2026-09-14T10:00:00Z",
    "updated_at": "2026-09-14T10:00:00Z",
    "expires_at": "2026-09-15T10:00:00Z",
    "can_edit": true,
    "can_delete": true,
    "photos": [
      {"id": 81, "image": "https://api.example/api/social/couple-moments/42/photos/81/", "order": 0, "uploaded_at": "2026-09-14T10:00:00Z"},
      {"id": 82, "image": "https://api.example/api/social/couple-moments/42/photos/82/", "order": 1, "uploaded_at": "2026-09-14T10:00:00Z"}
    ]
  }
}
```

Text-only creation also accepts JSON: `{"caption":"A quiet evening together","visibility":"private"}`. GET detail returns the moment object directly, preserving the former DRF retrieve contract. Photo URLs require the Bearer header; web clients should fetch an authenticated blob rather than use an unauthenticated image tag.

Retain photo 82, delete 81 and append a new photo:

```sh
curl -X PATCH https://api.example/api/social/couple-moments/42/ \
  -H "Authorization: Bearer <access_token>" \
  -F 'deleted_photo_ids=[81]' -F 'photos=@dessert.jpg'
```

JSON-only edit: `{"caption":"Our favourite cafe","deleted_photo_ids":[81]}`. Multipart ID arrays are JSON strings; JSON bodies use actual arrays. Boolean replacement accepts JSON booleans or multipart `true`/`false`. PUT follows DRF full-update defaults for optional fields; PATCH preserves omitted fields. Concurrent accepted edits are serialized and the last edit to a field wins.

`GET /api/social/couple-moments/?couple=7&page_size=1` returns the existing envelope (each result is the complete moment object shown above):

```json
{
  "message": "Results fetched successfully.",
  "data": {
    "count": 2,
    "next": "https://api.example/api/social/couple-moments/?couple=7&page=2&page_size=1",
    "previous": null,
    "results": [{"id": 42, "caption": "Coffee and a rainy afternoon", "can_edit": false, "can_delete": false}]
  }
}
```

The result object above is abbreviated only for documentation. A note sent by user 20:

```http
POST /api/social/couple-moments/42/notes/
Authorization: Bearer <access_token>
Content-Type: application/json

{"message":"You two look so happy. Wishing you many more afternoons like this!"}
```

```json
{
  "data": {
    "id": 9,
    "moment": 42,
    "sender": 20,
    "message": "You two look so happy. Wishing you many more afternoons like this!",
    "created_at": "2026-09-14T10:10:00Z"
  }
}
```

`GET /api/social/moment-notes/?moment=42` uses the same paginated `data.count/next/previous/results` envelope and includes only notes this user can read. Moment GET list/detail responses include `appreciation_notes` (with replies) only for authorized original current couple members. This field is omitted for outside viewers, including a note sender viewing a public moment; that sender reads their thread through the notes endpoints.

## Migration, storage and deployment

1. Apply `python manage.py migrate`. Apply both `social.0008_moment_expiry_and_notes` and `social.0009_moment_replies_and_views`. Migration `social.0008_moment_expiry_and_notes` adds nullable expiry, backfills it from each original creation time, snapshots existing memberships/users, then makes expiry non-null and adds indexes/models. Existing old moments will immediately be expired. No historical moments/photos are deleted by the migration. Schedule the migration appropriately for large tables: backfill and index creation run in the normal migration transaction.
2. Keep the existing storage configuration, but **deny all direct access to `couples/moments/`** at the reverse proxy/CDN/object store. Django's development media route now rejects these files (including normalized path variants). In nginx, put `location ^~ /media/couples/moments/ { return 404; }` before general media handling. Private object-store ACLs and removing cached public copies are required if media was previously public. Only the authenticated API should read these objects. Previously downloaded content cannot be recalled.
3. Set `MOMENT_MAX_IMAGE_BYTES` to override the default 10 MiB per photo. Set a matching total request-size limit at the proxy (allow multipart overhead for five uploads). Use a shared Django cache for the per-user note throttle in multi-worker deployments. DRF cache throttles are best-effort abuse controls, not strict concurrent quotas.
4. Schedule `python manage.py cleanup_moments --sweep-orphans` daily (or more frequently). Default permanent retention is **30 days after visibility expiry**. Purging cascades notes/photos. Expiry does not depend on this job. Monitor `MomentFileDeletion` backlog and logged deletion errors.

The API locks the moment row before validating or modifying photos, and locks the couple connection/memberships for writes. PostgreSQL is required for production row-lock behavior. Validation happens before changes; failed writes roll back the database and compensate new uploads. Deleting/replacing a photo records a durable deletion task in the same transaction, executed only after commit. Storage deletion failures stay queued for retry, including cascades from account/couple deletion.

Database and storage cannot commit atomically. Process death or simultaneous storage/database outages can defeat immediate compensation. `--sweep-orphans` reconciles unreferenced files older than 24 hours under the moment upload prefix; it requires storage `listdir` and `get_modified_time` support. Object stores without those methods need an equivalent inventory/lifecycle reconciliation job. The grace period protects normal in-flight uploads; do not keep upload transactions open for a day. This cannot guarantee immediate physical erasure during storage outages, but expired/deleted objects remain inaccessible via the API.

## Verification

Final verification on 2026-09-14: **48 social tests passed on PostgreSQL 17**, including partner replies, private embedded threads, concurrent unique views, concurrent photo writes and migration backfill. Django system checks, migration consistency (`makemigrations --check --dry-run`) and `git diff --check` passed. The earlier 32-test SQLite run passed as well; PostgreSQL was used for the final expanded suite.

Historical note: full discovery was previously blocked by an import-time legacy
WebSocket script. That script and transport were removed by the Matrix-only chat
cutover; current verification uses the maintained workspace suite allowlist.

Commands:

```sh
python manage.py test social.test_moment_replies social.test_moments social.tests --settings=srisu.test_settings --noinput
python manage.py test social.test_moment_replies social.test_moments social.tests --settings=srisu.test_postgres_settings --noinput
python manage.py check
python manage.py makemigrations --check --dry-run
```

The SQLite test settings use a test-only backend mapping CharField to TEXT so the historical unlimited password CharField migration can replay under Django 5.1. This does not modify production migrations. Concurrency tests skip SQLite. PostgreSQL test settings target a disposable loopback-only cluster on port 55439 (`MOMENT_TEST_PG_PORT` override) and database `test_couple_moments`, never application credentials.

Coverage includes partner creation, creator-only mutations, ownership spoofing, photo limits/replacement/order/invalid references, decoded type and size validation, empty submissions, exact expiry, pagination, stable query counts, note privacy/length/throttle/blocking, membership reassignment, database/storage failures, deferred deletion retries, retention, legacy migration backfill, and real simultaneous photo appends/delete races. Production proxy/object-store policy and scheduler setup must be applied by deployment; they cannot be verified by Django's test client.


## Partner replies and view counts

`POST /api/social/moment-notes/{note_id}/replies/` accepts JSON:

```json
{"message":"Thank you! We really appreciate your kind words."}
```

Either original, current member of the active couple may reply, regardless of who created the moment. The server sets `author` and `note`; submitted ownership fields are ignored. A note sender outside the couple may read the thread but cannot reply through this partner-reply endpoint (403); unrelated users receive 404. Messages are trimmed, nonblank and limited to 1,000 characters. Posting replies has a separate 30-attempt/hour/user throttle; GET and HEAD do not consume it. Replies are immutable and have no separate edit/delete endpoint; deleting the parent note/moment cascades replies. Authorized partners may continue replying to retained notes after moment expiry. Existing membership snapshots, active-couple checks and blocking rules apply.

HTTP 201 example:

```json
{"data":{"id":3,"note":9,"author":12,"message":"Thank you! We really appreciate your kind words.","created_at":"2026-09-14T11:00:00Z"}}
```

`GET /api/social/moment-notes/{note_id}/replies/` returns the existing paginated envelope. Note list/detail responses include a `replies` array, ordered by creation time then ID, oldest first.

Both moment list and detail GET responses include `appreciation_notes` for authorized original current couple members, even when the requesting partner did not create the moment. It is an empty array when there are no readable notes. Each note includes its replies. Notes blocked for the requester are excluded. The entire field is omitted for outside viewers; private notes are not made public. This embedding applies to GET responses, not moment create/update responses. Threads are prefetched to avoid a query for each moment or reply.

Example additional fields for a partner viewing a moment (other moment fields omitted):

```json
{
  "id":42,
  "total_view_count":3,
  "appreciation_notes":[
    {"id":9,"moment":42,"sender":20,"message":"Happy for you both!","created_at":"2026-09-14T10:10:00Z",
     "replies":[{"id":3,"note":9,"author":12,"message":"Thank you!","created_at":"2026-09-14T11:00:00Z"}]}
  ]
}
```

`total_view_count` is returned in every moment representation, including create/update and list/detail responses. It means **distinct authenticated users who successfully requested moment detail**, including the two partners. A successful detail GET records the viewer and returns the updated total. Repeated GETs by that user count once. Feed requests, photo downloads, HEAD, writes, anonymous, hidden and expired requests do not record a view. The database enforces one view per moment/user and concurrent requests are serialized against the moment row. Viewer identities are not exposed. Viewer-account deletion clears the viewer reference while preserving the total; moment deletion removes its view records. Existing moments start at zero because historical views were not tracked.

Migration `0009_moment_replies_and_views` adds the reply and unique-view tables without changing existing moment or note records. Run `python manage.py migrate` before using this version. No new background job or configuration is needed.

## Home + staged publication (moments-1, 2026-10-22)

Implemented on `codex/couple-chat-rebuild` starting at
`2a7d1f7d502bbaf3fb11b2330835a0d64bbe3ea5`, paired with frontend
`dev-core-architecture` starting at `98f17e769bf4d0a43b444919f6f81aac91b76b3b`.
The historical branch named above describes the original implementation, not the
current checkout. Figma reference: file `LztysD1YvINX7RwZpnhhTt`, Home page `1:3`.

Apply additive migration `social/0016_couplemomentmodel_revision_and_more` and
release this backend before the Moments KMP client. No migration of existing
Moment content or audience is required. No live migration/deployment was performed.
The new draft tables stage owned uploads; publication still creates the original
`CoupleMomentModel` and `CoupleMomentPhotoModel` records.

| Method | Path under `/api/social/` | Body / behavior |
| --- | --- | --- |
| GET | `couple-moments/home/` | Minimal greeting identity, nullable active couple, two real member summaries and `server_time`; unlinked users get 200 |
| POST | `moment-drafts/` | `{request_id: UUID, couple: ID}`; creator-owned, same-ID replay 200, new 201 |
| GET / DELETE | `moment-drafts/{uuid}/` | Recover / discard unpublished owned draft; delete 204 |
| PUT | `moment-drafts/{uuid}/photos/{uuid}/` | One multipart `photo`; retry same bytes/ID, conflict different content; five under row lock |
| GET / DELETE | `moment-drafts/{uuid}/photos/{uuid}/` | Guarded staged bytes / idempotent removal and updated draft revision |
| POST | `moment-drafts/{uuid}/publish/` | `{expected_revision, caption, visibility, photo_ids}`; exact ordered uploaded set, private default; 201 once / 200 replay |
| GET | `couple-moments/{id}/?record_view=false` | Raw resource; no view side effect; eligible expired reference 410, concealed inaccessible/missing/deleted 404 |
| POST | `couple-moments/{id}/viewed/` | Empty body, idempotent actual presentation event; never counted for creator |
| PUT | `couple-moments/{id}/reaction/` | `{reacted: boolean}`, original current partner only; desired-state heart |
| PATCH | `couple-moments/{id}/caption/` | `{expected_revision, caption}`, creator only; stale 409; publication/expiry unchanged |
| GET | `couple-moments/{id}/creator-photo/` | Same audience/expiry authorization as Moment |

Existing CRUD/notes endpoints and response shapes remain. Legacy GET without
`record_view=false` still counts views. Sequence accepts optional `creator`, bound
to its signed requester/couple cursor; order is ascending `(created_at,id)`.
Global/Faves retain existing ranking/session cursors. Both return server time.
Added projection fields: `creator`, photo `width/height` (nullable on legacy rows),
`revision`, `published_at` (alias of publication `created_at`), `server_time`,
`has_opened`, `can_reply`, `partner_seen_at`, `reacted`, `partner_reacted`.
Receipt details are not exposed to visitors. Home never marks a creator's own
Moment as unseen. Authorize using active original membership **and** user IDs;
ended membership joins cannot grant replacement partners access.

Uploads are authenticated and decoded outside application transactions. Accept
non-animated JPEG/PNG/WebP <=10 MiB, <=40 MP and <=12000px per edge. Apply EXIF
orientation, bound to 2560px, flatten transparency and re-encode JPEG without
EXIF/GPS/comments. Transactions lock couple then draft/Moment and recheck current
membership before ownership changes. Storage writes are compensated on failure;
published files survive draft-row deletion because shared reference guards run
before cleanup. Legacy writes also stage expensive storage work before locking.

The 24-hour clock begins only after publication with ready media. Publication
retries must reuse the same UUID and exact caption/audience/order, even after a
lost response. Deleted/expired publications return 410 on replay and never
resurrect. Unpublished staging expires after seven days. Published retry
records retain only IDs, original audience and a one-way payload hash until
account/couple deletion; they keep no caption or photos. Existing physical
Moment cleanup remains 30 days after expiry. Guarded media is `private, no-store`;
visibility expiry does not depend on cleanup. Downloaded copies cannot be recalled.

View hydration is bounded to the requester and original partners; aggregate
counts remain database counts. Embedded appreciation notes and their embedded
replies are capped at 50 each; existing separately paginated note/reply endpoints
remain available for older entries. KMP shows paginated notes for the active
Moment, with private partner replies, using these existing APIs. There is no new
inbox or notification service. Historical notes remain server-readable under the
existing retention policy even though this phase has no separate history inbox.

Matrix replies use the client encryption pipeline with an `external_url` containing
only `srisu://moments/{id}/couples/{id}`. No server-authored plaintext event, photo,
or caption is copied into Matrix. The client revalidates reference eligibility on
open/resume/local changes and clears its label at known expiry. Deleting a Matrix
reference does not delete the Moment; opening any stale label rechecks the server.

Public sharing is explicit and authenticated, with existing bilateral blocking.
No reporting API/workflow exists: moderation/reporting is an unresolved release
requirement, not claimed as implemented. Private Moments are server-readable;
only the separate Matrix messages are E2EE.

Contracts live in `contracts/core-1/{schema,fixtures,manifest,routes}.json`; the KMP
copy is pinned by `tools/core_contracts.py`. Tests use synthetic accounts and
isolated SQLite/PostgreSQL/media. See frontend `docs/flows/moments.md` for the
cross-repository validation and screenshot index.

## Optional photo framing at publication — 19 November 2026

`POST /api/social/moment-drafts/{uuid}/publish/` additionally accepts `crops`,
a map from submitted staged-photo UUID to `{x, y, width, height}`. Coordinates
are finite normalized values in the orientation-corrected staging image, bounded
inside that image with positive dimensions. Missing entries share the full photo.
Unknown/foreign IDs and invalid rectangles return 400; staging ownership and
current couple membership are still checked before reading images.

Crops are included in the publication fingerprint. A successful retry returns
the same Moment without recropping; changing the crop on a published UUID is a
409 conflict. Legacy fingerprints with no crop map are unchanged. Pillow creates
JPEG derivatives at quality 95 with no chroma subsampling, no upscaling and no new
metadata, outside database locks. Positive pixel rounding matches the client
preview, with a one-pixel minimum for tiny sources. Final publication rechecks
revision, ownership and photo IDs under existing couple/draft locks and assigns
the resulting files/dimensions to ordered Moment photos. Staged originals use
existing guarded cleanup; failed/duplicate derivatives are compensated. A cleanup
callback failing after commit cannot delete a now-published derivative. On an
ambiguous database outage, the existing orphan sweep handles leftover files.

This supports the frontend's Full/Fill/Crop, pan/zoom and per-photo secure draft
metadata. Crops are only editable before publication. New requests use the same
API, privacy rules, retention and response shape; no schema migration is needed.
Deploy this server support before the crop-enabled client. An older strict server
will reject `crops` rather than silently publish an uncropped image.

PR bases: backend `codex/moment-upload-crop` / `1594c3b`; frontend
`codex/moment-upload-polish` / `bcb1163`. See frontend `docs/flows/moments.md` for
UI evidence and verification limitations. Both PRs target `dev-core-architecture`
per the user's explicit branch decision. Deploy backend support before the client.

Verified locally with the disposable PostgreSQL runner on the integration base: 367 passing tests,
including concurrent cropped publication, rollback/storage compensation,
post-commit cleanup failure, exact synthetic crop pixels and partner-only reads.
Core-1 schema/fixtures validate. This is server integration coverage with synthetic
accounts. The paired KMP HTTP test also verifies cropped pixels/dimensions,
photo ordering, duplicate replay, changed-intent conflict, partner receipts and
unrelated-user denial against a disposable loopback server. No deployed service
or real couple account is used; migration and core-1 cross-repository checks pass.

## Video Moments — 24 November 2026

Additive client contract; existing `photos` arrays/IDs/order are retained. Each item
now has `video: null | {id, state, source_bytes, received_bytes, chunk_bytes,
duration_ms, width, height, error_code}`. For videos, `image` is an authenticated
JPEG poster. Old clients therefore see a still image; they never receive raw
uploaded video or a public media URL. This is server-processed private media,
not Matrix end-to-end encryption.

All routes below are under `/api/social/`. Upload, status and playback handshake
require the current authenticated user. Draft uploads are owner-only; published
streams use the existing current audience, blocking and 24-hour expiry rules.

| Route | Method and request | Result |
| --- | --- | --- |
| `moment-drafts/{draft}/videos/{video}/` | POST `{source_bytes}`; stable client UUID | Idempotent reservation; mismatch 409 |
| Same route | GET | Confirmed byte offset and processing state |
| `.../videos/{video}/chunks/{offset}/` | PUT `application/octet-stream`, Content-Length, sequential 2 MiB chunks (last may be smaller) | Confirmed offset; identical replay accepted, changed bytes 409 |
| `.../videos/{video}/complete/` | POST `{}` | Queue only after all bytes are present |
| `.../videos/{video}/retry/` | POST `{}` | Retry transient processing failure, at most three attempts |
| `moment-videos/{video}/playback/` | POST `{}` | `url`, `cookie_name`, `cookie_value`, `cookie_path`, `expires_in`, `duration_ms` |
| `moment-video-stream/{video}/{name}` | GET/HEAD, asset cookie or current JWT | HLS playlist/TS segment/poster; byte ranges 206 or invalid range 416 |

States: `uploading → queued → processing → ready`, or `failed`. Publication is
blocked until every selected video is ready. Source size is capped at 100 MiB;
verified duration at 60 seconds, at most 4096×4096 / 9 million source pixels,
one video track and at most four tracks. Accept MP4/MOV and Matroska/WebM that
FFmpeg can decode. Five mixed items share the existing attachment limit. Existing
limits of ten pending drafts per owner and seven-day staging expiry still apply.
Status polling has its own 120/minute allowance; it does not consume the 60/hour
draft-write budget. Chunks are bounded to 2 MiB and hash checked on retries.

`process_moment_videos --watch` claims jobs with database leases. Encoding runs
outside database/API transactions with fixed commands, input format/protocol
allowlists, process timeouts, two CPU threads and temporary files. Output is SDR
H.264/AAC, at most 30 fps, with two-second HLS segments. The ladder has long-edge
bounds 640 and 1280 (approximately 360p/720p at 16:9), never upscales, and skips
the larger rendition for small sources. Posters omit source metadata. Encoded
files are committed only if the lease, draft and audience are still valid;
original chunks are then deleted. Stale ten-minute leases are reclaimable.

Playback credentials live for at most 120 seconds and never appear in URLs.
Every playlist/segment request rechecks active user, current device session
(where present), relationship membership, block status and expiry. Input chunks
are not routable. Responses use `private, no-store`; a private object-store/CDN
must not expose the underlying files. Expired playback after a long pause can
be refreshed through the viewer's Retry action. Already buffered pixels cannot
be remotely recalled. Legacy JWTs without device sessions retain their existing
revocation limitation, bounded here by the short cookie lifetime.

### Deployment order and storage

1. Ship the backend image with FFmpeg/ffprobe and apply **social.0024_moment_video**.
   It depends on **social.0023_home_couple_space** on `dev-core-architecture`.
   QA rebased the original local-only `0022` video migration to this current graph.
   No deployment or live application migration was performed during this task.
2. Start `moment_video_worker` from Docker Compose alongside the web process.
   Both local startup wrappers now start it automatically, validate FFmpeg/ffprobe,
   and include it in the migration stop/restore sequence. It uses the shared
   `srisu-backend-app:local` image; look for its container rather than a separate
   image. See [local startup](local-server-startup.md).
   It shares private storage and the database; its container is limited to two
   CPUs and 2 GiB. Without a worker, uploads remain queued and the client offers
   a retry instead of publishing an unplayable item.
3. Keep the existing scheduled `cleanup_moments --sweep-orphans` job. Unpublished
   drafts expire after seven days; published media retains the existing purge
   policy (30 days after expiry). Deletion/expiry immediately denies new reads;
   failed physical file deletion stays in the cleanup queue.
4. Release the frontend after these APIs and worker are healthy. Old photo clients
   remain compatible. A rollback must preserve the schema/assets until the newer
   client is retired; do not drop video tables on a live deployment.

This baseline deliberately serves authorized segments through Django instead of
adding public CDN URLs. A signed CDN/origin deployment, load testing, worker
capacity alerts and real cellular benchmarks are additional rollout work, not
claims established by local simulator checks. No AV1, video trimming/recording,
background OS upload service, or new public visibility rules were introduced.

Exact PR source validation: 459 tests passed in the disposable PostgreSQL runner
with zero skips; migration
consistency, Django configuration and backend core fixture validation passed.
Real KMP HTTP upload → FFmpeg worker → publish → partner playback credential
checks also passed. Native ExoPlayer and AVPlayer consumed the protected HLS
stream on synthetic Android/iOS simulators. See the frontend
`docs/flows/moments.md#video-moments--24-november-2026` for exact scope, timing
caveats, prior contract drift and remaining physical-device/deployment checks.

PR branch: `codex/video-moments`, targeting `dev-core-architecture` from
`9e2f5b8`. Companion frontend uses the same feature/integration branch names.
PR descriptions record final commits and CI. Unrelated local work is excluded.
