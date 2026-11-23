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
| Plan invitation, response, cancellation | Exact plan, including current/history state |
| Question started, first answer, mutual reveal | Exact Spark; no private answer preview |
| Challenge invitation, response, progress, completion, cancellation | Exact challenge |
| Moment publication or partner heart | Exact live Moment |
| Appreciation or reply | Authorized private note thread, independent of Moment expiry |
| Partner status | Home; only the latest status update is listed |
| Shared date idea / mutual interest | Exact open shared idea |

Chat/Matrix messages, typing, receipts, local reminders and diagnostic messages
are excluded. All 22 current non-chat policies are eligible; channel/category
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
