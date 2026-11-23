# Notification catalogue rollout — 22 November 2026

Implementation branch: `codex/notification-rollout`, based on the freshly fetched
`dev-core-architecture` backend `b30f0319406c5c78d5b1bab87f192feb39a43e5f` and
frontend `bf6c37de447d6a944f0f641e1a0c0e94aa099ed9`. Apple enrollment/APNs remains
skipped. This document describes the code; it does not attest to Windows deployment
or new physical-phone delivery tests. Earlier October device evidence is historical.

## Three corrections

1. Before publishing an encrypted Plan, Challenge or Spark card, the sender calls
   authenticated `POST /api/notifications/v1/cards/` with `kind` and `resource`.
   The server verifies the original creator, current couple, resource and Matrix
   room. It returns a stable opaque UUID bound to sender, room, membership epoch
   and resource. The sender attaches only this UUID as
   `org.srisu.notification_card` on the outer encrypted Matrix event. Ingress
   verifies the actual sender/room/epoch and binds the UUID to its first event ID.
   Invalid, forged, wrong-sender or reused markers retain ordinary chat behavior.
   No ciphertext, message/answer text or feature title is inspected or retained.
   This works before the later card acknowledgment and survives transaction retries.
2. OS notification tags distinguish the chat room from each activity resource.
   Updates to the same Plan replace its earlier tray entry; another Plan or chat
   has its own entry. Appreciation thread IDs have a separate namespace from
   Moment IDs. Schema-2 activity pushes are non-collapsible so unrelated queued
   events do not replace each other. Legacy schema-1 clients still use the two
   collapse keys, chat and activity. The tray is not a durable activity inbox.
3. Plan alerts use the effective original/proposed future deadline and a keyed
   signature of meaningful proposal state. Checklist-only revisions do not
   invalidate responses. Declines, withdrawn changes and cancellations have their
   own outcome validity even if the former date has elapsed. Changes to the
   proposal, completion, removal or authorization still invalidate stale entries.

## Events and defaults

Updated 23 November 2026: missing category values now enable the full catalogue,
repeated actual status changes are no longer dropped for an hour, and schema-2
activity transport no longer collapses unrelated events. No new migration is needed
on top of the preview release. Deploy backend code and restart web/worker, then
update Android for matching settings and the Home status banner. Explicit opt-outs,
quiet policies, stale-event checks and capability gates remain authoritative.
Challenge progress copy identifies confirmation, pause and resume separately.

The original generic copy is superseded by `notification-previews-1` (23 November
2026). Capable Android clients receive schema-2 data-only routing metadata and safe,
event-specific fallback text, then fetch the authenticated `/preview/` endpoint.
Names, message text, plan details and private content never enter FCM or the outbox.
Matrix message text is decrypted only on Android. Legacy clients retain schema-1
auto-display with specific safe copy. No historical events are backfilled.
The actor is excluded except an explicit visitor/couple appreciation exchange.

| Event | Recipient / destination | Default | Sound / maximum lifetime |
| --- | --- | --- | --- |
| `matrix.message` | Other partner / authorized chat | On | Audible, 10 minutes |
| `invite.received` | Invited partner / requests | On | Audible, 24 hours |
| `invite.accepted` | Original inviter / couple | On | Audible, 6 hours |
| `sharing.proposed` | Other partner / sharing review | On | Audible, 24 hours |
| `sharing.approved` | Other partner / sharing | On | Quiet, 6 hours |
| `plan.created` | Other partner / Plan | On | Audible, 6 hours or Plan deadline |
| `plan.responded` | Other partner / Plan | On | Audible, 6 hours or applicable deadline |
| `plan.cancelled` | Other partner / cancelled Plan | On | Audible, 6 hours |
| `spark.started` | Other partner / manual question | On | Quiet, 6 hours |
| `spark.revealed` | Partner who answered first / revealed answers | On | Quiet, 6 hours |
| `spark.submitted` | Partner who has not answered / question | On | Quiet, 6 hours |
| `challenge.invited` | Other partner / Challenge | On | Audible, 24 hours |
| `challenge.responded` | Original inviter / Challenge | On | Quiet, 6 hours |
| `challenge.completed` | Partner who confirmed first / Challenge | On | Quiet, 6 hours |
| `challenge.cancelled` | Other partner / formerly active Challenge | On | Quiet, 6 hours |
| `challenge.progress` | Other partner / confirmation, pause or resume | On | Quiet, 6 hours |
| `moment.published` | Other partner / live Moment | On | Quiet, 6 hours or Moment expiry |
| `moment.hearted` | Moment creator / Moment | On | Quiet, 6 hours or Moment expiry |
| `moment.appreciated` | Each enabled original couple member / private thread | On | Quiet, 24 hours |
| `moment.replied` | Original appreciation sender / private thread | On | Quiet, 24 hours |
| `status.changed` | Other partner / current Home status | On | Quiet, 1 hour; each actual nonempty change |
| `idea.created` | Other partner / exact shared idea | On | Quiet, 6 hours |
| `idea.interested` | Idea creator / mutual idea | On | Quiet, 6 hours |

Audible means eligible for the existing audible channel, subject to the shared
30-second sound budget and Android permission, channel, volume and DND settings.
Schema-2 activity uses high FCM transport priority, including quiet categories,
so an eligible visible notification can arrive promptly during idle. Sound remains
controlled by the channel and does not follow transport priority. Legacy schema-1
quiet activity retains normal priority. Foreground updates use authorized previews
and are suppressed on the matching feature destination, except status updates:
Home is a broad dashboard, so status banners also appear while Home is visible. Already accepted provider messages cannot be recalled.

There is no extra alert for Matrix reactions/edits/reads/presence, ordinary profile
or Moment edits, deletion/expiry, views, checklist updates, status clearing,
idea withdrawal/uninterest, pending Challenge withdrawal, unconfirmation, skipped
questions or automatic daily-question assignment. A Moment chat reply uses the
existing Matrix alert. Converting an idea emits only the resulting Plan invitation.
Repeated hearts on one Moment do not create additional alerts.

## Controls and compatibility

The existing push/activity/chat switches and chat mute remain authoritative.
All twelve category keys in `notifications/catalogue.py` now default on. Explicit
stored false values still win; missing keys adopt the current defaults without
rewriting user preferences. Preferences writes merge only supplied keys and retain
optimistic revisions; old clients omitting categories preserve all choices.
Unknown keys are rejected. Preferences remain account-wide; permission and channel
sound remain device-specific. Local Plan reminders retain their existing controls.

`notification-catalogue-2` gates all **new** event kinds to Android installations
that can resolve their destinations. Existing seven event kinds remain compatible.
Android advances its registration version when it adopts the new capability.
For marked cards, Plan duplicates are suppressed on every eligible installation;
Challenge/Spark duplicates are suppressed on catalogue-capable installations.
Older Android receivers retain ordinary chat fallback for Challenge/Spark cards.
**Older senders that omit the marker can still produce a feature-card chat alert.**
Upgrade sending clients for complete correlation; the server cannot identify
unmarked encrypted cards without inspecting their encrypted contents.

Reservation failure leaves the existing retryable card-publishing UI; there is no
unmarked fallback in the new client. The feature itself has already been created
transactionally and its domain alert is independent of optional chat publication.
Card bindings are purged after 30 days with notification metadata. They confer no
read access to the feature or conversation.

New destination kinds: `challenge`, `moment`, `moment_note`, `idea`, `home`.
Private note threads use their own authorized endpoint after Moment expiry; the
response includes `can_reply`, calculated by the existing visibility queryset
without per-note queries. The client shows the latest 50 replies, clears private
content on background/account change and rechecks reply permission server-side.
Idea taps read the exact idea; choosing a time requires an explicit action and
the existing Plan editor. Home's bell remains the current requests view.

## Deployment order and rollback

1. Back up the existing host using its established procedure. Stop the notification
   worker briefly, deploy this backend and apply additive migration
   `notifications.0005_event_context_event_quiet_preferences_categories_and_more`.
   Do not rerun historical destructive Matrix cutover migrations.
2. Restart web/worker on the same image. Keep the existing Firebase sender identity,
   AS token, cutoff and Docker volumes. No new Google sign-in, APNs setup, public
   endpoint, database replacement or credential distribution is needed.
3. Install the new Android app and update clients used to send feature cards.
   Check registration, categories, one domain alert per marked card, and correct
   Plan/Challenge/Moment/note/idea taps. Check an opted-out category, repeated actual
   status changes, revoked/blocked access, offline expiry and closed/locked delivery.
   Confirm actual sound with the device owner; provider acceptance is insufficient.
4. Roll back Android first if returning to the old backend; new Android registration
   and card reservation require the new API. Keep the additive database fields.
   Disable activity/category preferences or stop the worker to suspend delivery;
   this never retracts an alert already accepted by FCM.

Later work remains separate: APNs, security/device alerts, anniversary/daily reminder
scheduling and a durable recent-activity inbox. No new Google Analytics campaigns,
tracking consent or reminder scheduler is introduced.

## Verification

Executed on 22 November 2026: workspace verification passed; the SQLite allowlist
reported 410 tests with 33 PostgreSQL-only skips; the disposable PostgreSQL 17 run
passed all 410 with no skips. Android JVM checks reported 619 tests, zero failures
and three opt-in integration skips. The core and Moment HTTP integration checks
were separately executed against a disposable Django server and passed. Android
compilation/instrumentation compilation, APK assembly, shared iOS simulator
compilation, theme checks and migration drift checks passed. All twelve shared
JSON contract files match the paired repositories. No live FCM/phone test was run.

Use the explicit workspace allowlist (now includes `notifications.test_catalogue`)
and disposable PostgreSQL runner. It covers producer lifecycle/idempotency,
category/legacy-write behavior, private payloads, card correlation and mixed-client
fallback, stale authorization, rescheduling and note expiry. See the paired
frontend `docs/validation/notifications/rollout-20261122.md` for executed counts,
HTTP/UI checks, commits and remaining live-device coverage.

## Specific notification previews — 23 November 2026

The `notification-previews-1` capability replaces the universal update text with
an action, actor and useful preview. Android receives a schema-2 **data-only** push,
posts a safe event-specific fallback immediately, and schedules bounded background
work to load `/api/notifications/v1/deliveries/{delivery_id}/preview/`. This endpoint
rechecks the current user, device session, installation binding, permission,
preferences and live resource authorization. Responses are `private, no-store`.
Older installations retain schema-1 automatic display with specific safe wording.

| Notification | Detailed presentation |
| --- | --- |
| Chat message | Partner name and the actual text; photo/video/voice labels with a caption when present |
| Plan invitation, response or cancellation | Partner/action, plan title, scheduled or proposed local date/time, time zone and place |
| Local Plan reminder | “Coming up: [title]”, agreed date/time, time zone and place |
| Partner invitation/acceptance | Person's name and connection action |
| Sharing proposal/approval | Partner's name and sharing action |
| Question started/answered/revealed | Partner/action and the question; unrevealed private answers are never previewed |
| Challenge invitation/response/progress/completion/cancellation | Partner/action and challenge title |
| Moment published/hearted | Partner/action and moment caption, title or photo label |
| Appreciation or reply | Actual author and note/reply text |
| Status | Partner name and current authorized status text |
| Shared date idea/mutual interest | Partner/action and the idea, such as Coffee together |

Encrypted Matrix messages are decrypted **only on the receiving device**, from the
exact authorized event in the existing protected Matrix store. Django/FCM never
receive their plaintext. The background reader does not enable the outbox, send
read receipts, download media, or take over a foreground conversation. Android's
activity and workers share one process-owned dependency graph/crypto client.
This is necessary for a closed app to read the same keys without opening a second
writer on the encrypted store. iOS retains its existing graph lifetime.

Android uses MessagingStyle for chat and expanded text for activity. Updating a
fallback with private detail does not alert again. A newer delivery, dismissal,
expiry, revoked authorization, logout or account/binding change prevents stale
work from displaying old content. WorkManager and replay metadata contain only
opaque IDs and safe copy; private fetched content is not persisted by this feature.
Local reminder details use encrypted KVault storage and are cleared when removed;
reminders still reflect the last authorized sync while offline.

Private details have Android PRIVATE visibility with an event-specific public
version; the user's lock-screen settings control whether details are visible.
Missing network or decryption keys keep a specific safe fallback, not invented
message text. Existing sound/mute/category policies and tap destinations remain.
Foreground banners use the same preview where available, suppressing the matching
visible destination. Notification text is bounded and strips unsafe controls.

Deploy the backend first, then install/launch the Android update to register the
new capability. This increment adds **no migration** beyond the catalogue baseline
`notifications.0005`. Do not roll a registered schema-2 client back before removing
its capability. The application has no push-delivery guarantee after Android
Force stop. Apple enrollment/APNs remains skipped.

These are native Android presentation changes, with no new Figma screen or claimed
live Figma match. Figma remains `LztysD1YvINX7RwZpnhhTt`; no design file is changed.
