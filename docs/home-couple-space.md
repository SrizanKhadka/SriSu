# Home couple space — home-1

9 November 2026. Backend `codex/home-couple-space`, based on
`11891049ef323b6df3c6ee3c8f0688442b18c13f`; corresponding frontend branch has the
same name, based on `c1abef982b0e3a2b87bf342d08c526bc65c25bd8`.
No deployment or live migration has been performed.

## Contract and persistence

`contracts/home-1/openapi.json` specifies the authenticated daily-question,
private-status and date-idea endpoints. `contracts/core-1/routes.json` includes
the client calls. Migration `social.0023_home_couple_space` is additive:

- SparkSession gains nullable `finished_at` and `assigned_automatically`.
- CoupleQuestionDay records the selected question, stable IANA timezone, day
  and original membership audience.
- CoupleStatus records author, preset, expiry, revision and the latest retry ID.
- DateIdea and DateIdeaOperation record original-couple interest, revision,
  durable action receipts and at most one linked Plan.

New tables are private. Endpoints enforce current accepted couple membership and
original membership IDs, return no-store responses, validate inputs strictly,
and serialize writes under the couple lock. They do not change public-profile
mood or publication consent. Unrelated users cannot read or mutate these records.

## Daily question

`POST /api/social/sparks/daily/` accepts `couple` and `time_zone`. GET is not a
mutating fallback. The first supplied valid timezone becomes the stable shared
day boundary; no timezone editor is added in this phase.

The existing single open Spark slot is reused, including a manually selected
question. Unfinished questions carry across days. After reveal or skip the
question remains for the rest of its completion day; the next open on a later
day selects an unused question, then the least recently used catalogue entry.
There is no backlog. `revisit` lets the client label repeated content. Allocation
requires the current ready Matrix room epoch, with the existing fail-closed
relationship checks. A small new curated entry brings the catalogue to 25.

The Home projection always returns `answers: []`. Neither answer text nor answer
ciphertext is in this response, even after reveal. Existing authorized Spark
detail remains the retrieval path. `partner_activity_at/kind` describe actual
partner submission or manual creation; automatic assignment never pretends the
partner sent a question. Allocation does not publish a chat card or send a push.

## Status and shared ideas

`GET /api/social/couple-status/` returns current-audience statuses and server time.
`PUT /api/social/couple-status/mine/` accepts an allowed preset/clear, request ID
and expected revision. Only the author can write their status. Text expires after
24 hours; retrying the same write does not extend that expiry. Expired text is
omitted by projection; no scheduler is required to enforce expiry.

Date ideas support Coffee, Walk and Movie. There is at most one open idea per
author. Suggesting an already-open preset returns that idea without implicitly
adding interest. Recipient interest is explicitly reversible. Only the author
can withdraw. Both partners must be interested before a `plan` action can create
an existing Plan invitation. Interest never means acceptance of a dated Plan.

Revision checks and per-author request receipts make retries safe. Two concurrent
conversions cannot create two Plans; conversion and linkage are atomic. Existing
Plan validation, authorization, acceptance and encrypted-reference publication
remain authoritative. Ideas have no new push channel: clients discover updates
through foreground refresh.

## Existing endpoint changes

- Plans Home retains `next_plan` and `invitations`, adding `featured_plan`,
  `requests` and `completed_count`. A validated `time_zone` selects today's Plan.
  Priority: agreed today, future incoming proposal, next agreed, outgoing.
  Requests count incoming unresolved items, including elapsed proposals.
- `mode=invitations` also retains elapsed unresolved invitations. Old clients
  may see additional legitimate rows; existing action guards still reject an
  acceptance at a past time. Existing date changes retain the last agreed time.
- Challenges Home adds `last_completed` when no current challenge takes priority.
- Moments Home includes the actual couple title.
- Spark detail adds nullable `finished_at`; old fields/routes remain.

Django stores no Home chat-message preview. The frontend reads Matrix locally
with a read-only summary lease and never marks read merely for showing Home.

## Rollout, tests and limits

Apply additive migration 0023, deploy the updated backend, then release the
client. Existing clients retain their endpoints. Do not serve the new client
against a backend missing these endpoints. Keep the migration/data on a code
rollback rather than destructively reversing new private records. No cleanup or
production migration was run during this task.

The maintained suite allowlist now includes `social.test_home_space`. Use the
isolated commands in [workspace.md](workspace.md); do not use unrestricted test
discovery. Tests cover daily carry/skip/epoch changes, response schemas and
answer omission, status expiry/retry/access, idea interest/undo/conversion,
membership replacement, Plan priority/elapsed requests, and PostgreSQL races for
daily allocation and conversion. Frontend validation records exact results and
native screenshots. The final PostgreSQL run passed all 358 tests without skips.
A separate iOS native integration test passed against isolated loopback Django,
PostgreSQL and Synapse with three synthetic accounts and two real Matrix clients:
status sharing, idea conversion/retry, Plan acceptance, encrypted partner text,
Home read-receipt isolation, answer omission and account-loss cleanup. This is
not production delivery or a physical two-device exchange. Migration 0023 was
applied to that new isolated QA database only.
