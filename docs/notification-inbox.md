# Home notification centre

`notification-inbox-1` adds authenticated activity history to the existing
`notifications-1` API. Frontend: `SriZan12/SriSu`; backend: `SrizanKhadka/SriSu`.
Both changes continue `codex/notification-previews`, from frontend `5c39af6` and
backend `8585348`. Apple enrollment/APNs remains skipped.

## Included activity and destinations

| Activity | Destination |
| --- | --- |
| Partner invitation / acceptance | Pending invitations / current couple profile |
| Sharing proposal / approval | Couple sharing settings |
| Partner nickname set/change | Current couple profile; current nickname revision only |
| Plan invitation, response, cancellation | Exact plan, including current/history state |
| Question started, first answer, mutual reveal | Exact Spark; no private answer preview |
| Challenge invitation, response, progress, completion, cancellation | Exact challenge |
| Moment publication or partner heart | Exact live Moment |
| Appreciation or reply | Authorized private note thread, independent of Moment expiry |
| Partner status | Home; only the latest status update is listed |
| Shared date idea / mutual interest | Exact open shared idea |

Chat/Matrix messages, typing, receipts, local reminders and diagnostic messages
are excluded. All 23 current non-chat policies are eligible; channel/category
preferences control phone alerts, not this account history.

## HTTP and consistency

All responses use the normal `{data: ...}` envelope, current authenticated device
session, existing notification throttle, and `Cache-Control: private, no-store`.
Schema: `contracts/notifications-1/inbox.schema.json`; route inventory is pinned
in both repositories. No existing endpoint or payload shape was changed.

- `GET /api/notifications/v1/inbox/?unread=false&cursor=...`: 25 rows ordered by
  creation time and UUID descending; signed account/filter-bound cursor; total
  current unread count, server time and signed read boundary.
- `GET /api/notifications/v1/inbox/summary/`: account unread count.
- `POST /api/notifications/v1/inbox/read/`: either `ids` (1–50 UUIDs) or the page's
  `through` token. Idempotent; read-all cannot consume events created after that
  page's boundary. Tokens expire after 24 hours; refresh to obtain another.
- `POST /api/notifications/v1/inbox/{event_id}/open/`: reauthorizes the resource,
  marks the event read and returns its typed destination, or `available:false`
  with no destination. Opening a list does not mark it read.

History lasts 30 days using existing event retention. Read state is per recipient,
shared across devices. Current audience membership, blocked users, private notes
and current resources are rechecked before counting/listing/opening. Blocked or
former-couple records disappear; missing/expired resources retain only safe
unavailable copy. A stale first-answer notification does not claim it is still
that user's turn. Private copy is projected from the authorized current resource;
it is not an archived snapshot and is not newly stored in event metadata.

`NOTIFICATIONS_ENABLED` now gates push delivery, not capture of these domain events.
This allows history without Firebase/device permission. Existing events within the
retention window become visible as unread; there is no reconstruction of events
that were never captured. Push TTL, mute, category, capability and session policies
remain unchanged. No old push deliveries are created by opening history.

## Deployment and rollback

Deploy backend first. Stop the notification worker, fast-forward the matching
branch, run `python manage.py migrate notifications 0006 --noinput`, then restart
web and Matrix reconciler and start the worker. Check the migration plan first:
only the additive read timestamp and index are introduced by this increment.
Do not rerun the historic Matrix cutover. Existing Docker volumes, credentials and
media remain in place; no image/dependency rebuild is required for this bind-mounted
Windows development stack. Install the new Android APK after the API is ready.

The nullable column/index are backwards compatible with old application code.
Roll application code back if needed; leave the additive schema in place. An older
backend has no inbox endpoint, and the new client displays a retryable unavailable
state rather than fabricated activity. Do not reverse historical migrations.

## Client and design

Home's bell opens Notifications with an unread badge. All/Unread, day groups,
category icons, specific copy, read styling, mark-all-read, refresh, pagination,
settings, empty/error/retry and unavailable states use existing SriSu theme tokens.
Request-review actions on Home remain separate. Navigation uses existing typed
feature routes and never opens a chat from this surface. Account/background changes
clear private list memory and reject late responses; the screen uses existing
screenshot protection. Summary polling runs only while signed in and foregrounded.

Figma file `LztysD1YvINX7RwZpnhhTt`, Home page `1:3`, design-system page `0:1`:
live discovery hit the connector Starter-plan tool limit. No notification node or
pixel match is claimed. This is an intentional derived layout, with no Figma write.
References: [Material lists](https://m3.material.io/components/lists/overview) for
scanable icon/text/metadata rows and [Carbon notification patterns](https://www.carbondesignsystem.com/building-blocks/core/patterns/notifications)
for persistent, revisitable activity rather than interrupting every interaction.
Validation is recorded in the frontend's
`docs/validation/notifications/inbox-20261124.md`.

## Moment media wording — 24 November 2026

The inbox and authenticated push-preview endpoint share `display_copy`. Publication
and heart titles now identify photo-only, video-only, and mixed photo/video Moments;
body fallback is respectively “Photo moment”, “Video moment”, or “Photo and video
moment”. Classification counts actual `video_id` relations, not the JPEG poster or
legacy `photos` relation name. Captions and legacy titles still take precedence in
the body, and an empty legacy Moment has neutral copy. Classification happens only
after current-resource authorization. FCM/public lock-screen fallbacks, event kinds,
destinations, expiry, categories and notification capability/schema versions remain
unchanged. This fixes refreshed existing inbox rows as well as new preview fetches;
it does not resend old alerts or store private copies in the outbox.

No migration or client production-code change is needed. Deploy/restart the backend
web process to use this formatter; the matching frontend adds regression coverage
for its existing shared preview reader and Android notification presentation. The
paired real HTTP video test also checks that a captionless published video creates
one correctly labeled partner inbox event even when publication is retried.

Verification: 467 backend tests completed with zero failures (35 PostgreSQL-only
skips), including media-type cases, captions/legacy titles, hearts, inbox agreement,
provider privacy, blocks and expiry. The notification regressions reproduced the
old photo fallback before the fix. Frontend: 54 notification tests passed, including
Robolectric Android presentation; Android and iOS simulator Kotlin compilation
passed. Real Ktor → isolated Django → FFmpeg → publication → partner inbox labeling
and deduplication passed. These checks use synthetic accounts/media; no real FCM or
APNs delivery was sent, and the other laptop has not been updated by this task.

## Reference layout support — 25 November 2026

The attached Notifications board replaces the client's earlier derived card list.
Additive optional fields on each authorized item are `actor_name`, `avatar`,
`thumbnail` and `segments` (plain text and a bold flag, no HTML). Existing title,
body, contract ID, pagination, open/read behavior and push payloads are unchanged.
The optional fields are absent/defaulted safely on older servers/clients.

`GET /api/notifications/v1/inbox/{event_id}/media/avatar/` and `/media/thumbnail/`
recheck recipient, live device session, current audience and resource access.
They return at most 160×160 JPEGs, with private/no-store and nosniff, without
revealing original storage URLs or marking an event read. A separate 180/minute
media throttle keeps image reads from consuming the action allowance. Missing,
expired, blocked, unrelated or revoked access returns 404/403/401. No thumbnails
are persisted. Moment posters are reused for video; Challenge proof media is not
invented. Actor photos for notes/invites are resolved from their authorized actor,
not blindly from the currently linked partner.

Spark first-answer copy checks the current reveal state and recipient submission;
it does not encourage a second answer after mutual reveal. This presentation never
includes encrypted answer envelopes. Unavailable resources have no actor/media
metadata. Original notification event capture and FCM fallback copy are untouched.

Deployment: restart the backend web process before installing the matching UI.
No database migration or worker/configuration change is needed for this increment.
Existing clients ignore the optional fields. The frontend falls back to original
copy and a generic avatar if the older API is still running.

Work was performed in the existing `codex/video-moments` folders, frontend base
`6c85951`, backend base `136f618`; no push/merge/deployment is included. The UI
verification record is frontend `docs/validation/notifications/reference-ui-20261125.md`.
Publication uses `codex/notifications-ui` targeting `dev-core-architecture` in both
repositories; paired PRs record the final implementation commits and CI results.

Follow-up QA on 25 November added blocked/revoked/membership media checks, sender
avatar attribution, corrupt-media fallback, and a real Ktor-to-Django inbox/media
test. The isolated SQLite run completed 474 tests (35 PostgreSQL-only skips); the
disposable PostgreSQL run passed all 474 without skips. No backend production
change or migration was needed in this QA increment. Client state and large-text
fixes are recorded in frontend `docs/validation/notifications/reference-qa-20261125.md`.


### Nickname alerts

`nickname.changed` is an additive `notification-inbox-1` kind in `requests`.
Initial copy says the actor gave you a nickname; later changes say they changed
it. Body and literal segments show `You’re now “<nickname>” to them.` Only the
actor-name segment carries `user_id`; the chosen nickname is literal content and
must not be substituted by the recipient's personal display-name resolver.

The row and unread count include only the setter's exact active nickname revision.
Clear, change, block or unlink removes stale rows; valid inbox rows can outlive the
six-hour push TTL within normal 30-day retention. Open still reauthorizes and routes
to `{kind:"couple",couple_id}`. Neither the snapshot text nor old nickname values
are copied into the outbox. See [rollout](notification-rollout.md#nickname-change-alerts).
