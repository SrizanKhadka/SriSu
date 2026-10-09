# Couple Profile (couple-profile-1)

Implemented on `dev-core-architecture`, based on `9303cdd`. Companion client is
SriZan12/SriSu `dev-core-architecture` based on `7c3f1ed`. The paired core-1 bundle
is authoritative for response shapes and routes; the client pins its digest.

Design: Figma LztysD1YvINX7RwZpnhhTt, Couple UI final, page 1:4. The connector was
quota-limited. The supplied 11661×23196 board was inspected in 17 crops; hidden
states, child node IDs and component properties were unavailable. See the client
`docs/flows/couple-profile.md` for the screen/data/permission matrix and renders.
The user explicitly chose selected-section publication with both partners' consent.

## Domain and ownership

- Existing CoupleModel, current memberships and accepted connection remain the
  identity authority. Both current members have equal shared-section rights.
- Story answers belong to their authenticated author: three fixed prompt keys,
  Unicode text <=240 characters; unique couple/author/prompt. Missing prompts in
  PATCH are untouched; an explicit empty answer removes only the actor's answer.
- Song is one shared record: required title <=120, optional artist/band <=120,
  optional note <=240. Metadata only; there is no licensed playback integration.
- Interests reuse each member's UserInterestModel. Max20 selected names <=100;
  case-insensitive duplicates removed. Existing IDs/catalogue associations are
  retained, deselection sets removed=True. Custom Unicode names are supported.
  Visitors receive only the exact common names, never personal interest lists.
- Anniversary remains the existing optional date; today or earlier. Days together
  and next-anniversary countdown are derived; Feb29 maps to Feb28 in non-leap years.
- Cover is an existing stored image or a reference to an eligible Moment photo,
  with focal_y in [0,1]. It is not copied from a Moment to bypass expiry/audience.
- Plans are a private couple resource: title <=120, future starts_at on creation,
  creator/request UUID uniqueness, integer revision, response pending/yes/no/
  another_time and optional response_note <=240. Only the other partner responds;
  either member may mark an agreed past plan completed. The original membership
  IDs bind plan access, so replacement partners cannot inherit old plans.
- Profile change history contains actor, section, action and time, not old answer
  contents. It is member-only; actor deletion cascades their history so existing account deletion remains available. Counts are derived, not duplicated UI counters.

## HTTP contract

All new routes require authentication and return private, no-store responses.
Base `/api/social/profiles/{couple_id}/`; `GET /api/social/profiles/me/` resolves
current membership. Existing core-1 error header/envelope behavior is reused.

| Method / suffix | Request / result |
| --- | --- |
| GET (base) | Viewer-specific profile; explicit can_edit/can_fave capabilities |
| PATCH sections/story/ | expected_revision; answers map of fixed prompt keys |
| PATCH sections/song/ | expected_revision; title/artist/band/note or remove=true |
| PATCH sections/interests/ | expected_revision; names array (actor only) |
| PATCH sections/date/ | expected_revision; anniversary_date date or null |
| PATCH sections/sharing/ | expected_revision; action propose/approve/revoke; proposal sections array |
| PATCH cover/ | expected_revision; optional photo multipart OR moment_photo_id OR remove; focal_y |
| GET cover/ | Authenticated bytes, fresh permission/expiry check |
| GET members/{user_id}/photo/ | Authenticated member portrait projection |
| GET cover-choices/ | Eligible own-couple Moment photos, bounded20, next_before |
| GET history/ | Member-only bounded20, next_before |
| GET plans/ | mode upcoming/past, bounded20, next_before |
| POST plans/ | request_id UUID, title, starts_at; 201 new / 200 exact replay |
| GET plans/{plan_id}/ | Current membership-bound private plan |
| PATCH plans/{plan_id}/ | expected_revision integer; response+note OR completed |
| POST story-invites/ | request_id UUID, prompt; idempotent saved domain record, never fake chat delivery IDs |

Unknown JSON fields and invalid values return 400 with field-level errors. Missing,
blocked, inactive or unauthorized protected resources return 404; unauthenticated
requests return 401. Stale conditional updates return 409 `section_changed`.
All section saves return the confirmed current profile. Empty success is not used
as proof of persistence by the client. No new universal response envelope exists.

## Concurrency and publication

Transactions lock the couple, accepted connection and membership rows. Section
revision fingerprints combine current authoritative content and a section counter.
Story/interests revisions are scoped to the author. Narrow update_fields/section
writes preserve unrelated edits. Same-section stale writes fail; the client keeps
the draft and explicitly refreshes/reloads. Plans use a revision counter and stable
UUID request identity; a retry after the proposed time still returns the same plan.

Publishable sections: identity, story, song, shared interests, cover, date. A proposal
binds exact content fingerprints and current membership row IDs. The proposer
approves their proposal; the second current member must approve that exact version.
Changed content automatically ceases to be published until approved again. Either
member can revoke immediately even using an old revision (restriction only).
Private Moment photos cannot be widened to public via cover publication.

Visitors receive only approved sections. No phone, email, precise location,
authentication details, private interest lists, revisions, settings, history, plans
or private partner room is included. Former members get only ordinary visitor
rights; blocks/inactive relationships deny the resource. Anonymous access is not
introduced. Personal Faves and creator-only Moment modification remain unchanged.
Profile endpoints have no shared response cache; media is no-store. Underlying
personal-name/photo changes alter the publication fingerprint too.

## Media, chat, and cleanup

Uploads reuse the authentication image validator: JPEG/PNG/WebP, max5MB, max20MP
and8192px decoded bounds, orientation corrected and metadata removed, reencoded
bounded1600px JPEG with generated names. Upload work/storage happens outside long
DB transactions. Association is conditional; failed association is compensated and
old valid cover is deleted only after successful replacement through cleanup tasks.
Run the existing cleanup_moments command with --sweep-orphans to sweep abandoned
private cover files; currently referenced Couple.cover_photo files are retained.

Story/plan cards are server-authored `profile_action` message metadata in the
existing private couple room. Normal message creation cannot set this metadata.
The existing chat service/room authorization and content-free chat_room_updated
invalidation are reused. A mismatched room is rejected; cards are idempotent.
This never opens a visitor's access to the partner conversation.

## Migration and deployment order

1. Review/apply additive social0011–0013 and chat0003 migrations; no table recreation,
   identifier replacement or relationship backfill is performed.
2. Configure the production proxy/object store to deny direct public delivery of
   couples/moments, couples/profile_private and legacy couples/profile_photos.
   Django enforces these boundaries too, but cannot control a separate CDN/bucket.
3. Deploy backend support before the updated client. Legacy JSON routes remain;
   legacy cover responses now use authenticated URLs. Old clients that fetch images
   without authorization need the new client. This intentional privacy correction
   is not a promise of fully unchanged media behavior for old clients.
4. Deploy/release KMP after backend compatibility verification. Pushing these
   branches is not deployment. No production environment or data was touched.

Revocation prevents future authorized reads; it cannot retract already downloaded
content or invalidate copies previously delivered by an external public server.
Avoid rolling back migrations with new records; roll back application code only
with a reviewed forward-compatible data plan.

## Validation (2026-10-01)

The counts below are the historical profile baseline. References to profile chat
cards/recovery are superseded by the Matrix-only cutover: plans now save without
a server-authored message, and story invites return a saved domain record.

- Baseline selected offline suites:136 passed,10 PostgreSQL-only skips.
- `.venv/bin/python tools/workspace.py check`: passed.
- `.venv/bin/python tools/workspace.py migrations`: passed, no missing migrations.
- `.venv/bin/python tools/auth_postgres_tests.py --postgres-bin /opt/homebrew/opt/postgresql@17/bin`:
  158 tests passed on disposable PostgreSQL, including real concurrent writes.
- core-1 schema and15 shared fixtures passed.
- Client `JAVA_HOME='/Applications/Android Studio.app/Contents/jbr/Contents/Home'
  python3 tools/core_integration.py --backend ../SriSu-backend`: passed against a
  disposable real HTTP server: saves/conflicts, validated multipart media,
  publication/revocation, visitor denial, question/plan cards, auth and chat recovery.
- Tests use synthetic accounts and isolated databases. No unrestricted discovery,
  production data, live Twilio calls or deployment.
