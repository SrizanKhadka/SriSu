# Challenges contract and operations

Local implementation, 2026-10-24. Backend base `a542c87053b3dd52551da8c794367b8d97666fb5`
on `codex/couple-chat-rebuild`; frontend base `f0969a00c2329fd34187ef802390ef4a5ab4f01e`
on `dev-core-architecture`. See frontend `docs/flows/challenges.md` for UI/design choices.

## Additive contract

Authenticated JSON routes under `/api/social/challenges/`:

| Route | Behavior |
| --- | --- |
| `GET home/` | Four collections, real members, current live activity; unpaired returns no couple |
| `POST /` | `request_id`, `couple`, `template_id`; sender implicitly accepts |
| `GET {uuid}/` | Authorized original audience only |
| `POST {uuid}/actions/` | `request_id`, `expected_revision`, `action` |
| `GET history/?before={uuid}` | Terminal states, 20 rows/page, stable creation/id cursor |
| `PUT {uuid}/card/` | Creator acknowledges the optional encrypted Matrix event ID |

Responses use the existing `data` envelope and private/no-store response mixin.
Input rejects unknown fields. Actions: accept, decline, confirm, unconfirm, pause,
resume, cancel. Writes are throttled at 120/hour per account. This is an additive
API; existing chat, Moments and Sparks contracts are unchanged. Mirrored schemas
and catalogue live in `contracts/challenges-1` in both repositories.

## Integrity and authorization

Every read/write checks the current active, unblocked couple and exact original
membership/user audience. Unrelated/replacement/ended members cannot read or mutate
earlier activities. Creator-only withdrawal before acceptance; recipient-only
accept/decline. Each account changes only its own confirmation/pause.

Couple then activity row locks serialize mutations. A conditional unique constraint
enforces one invited/active/paused activity per couple. Every action checks its
expected revision; stale clients receive 409 and reload. `ChallengeOperation`
stores a unique per-author request receipt. Replaying a committed request returns
the latest activity without applying it again, even after later resume/completion.
Reusing an ID with different intent is rejected. Completed timestamps are written
once. Each activity snapshots its versioned catalogue content; withdrawing a template
blocks new invitations without rewriting existing history.

Challenge choice/progress is server-readable metadata, not an end-to-end encrypted
answer store. No free-text reflection is accepted. The client sends the optional
invitation through the existing Megolm-encrypted Matrix room. The acknowledgement
is not proof of delivery/read status; no such status is fabricated. A forged card
reference cannot grant access. Matrix unavailability does not block Challenges.

The local live test also exposed a pre-existing PostgreSQL nullable outer-join lock
in `reconcile_accepted_relationship`; locking `of=("self",)` fixes new-couple room
reconciliation without weakening the existing couple lock. Its repeatability is
covered in the PostgreSQL suite.

## Migrations and release order

1. Deploy backend and apply `social.0018_challengetemplate_couplechallenge_challengeoperation_and_more`.
2. Apply `social.0019_challenge_catalogue` (16 frozen seed activities, four collections).
3. Release the frontend after these routes are available. Older clients ignore the
   additive feature; encrypted invitation cards remain ordinary readable text there.

Both migrations were applied only to the local Docker environment in this task.
Do not reverse schema migrations on a database with activity history as a rollout
strategy. Roll back the client first and preserve the additive tables/data.
Catalogue changes should create a new versioned ID; unpublish old templates when
needed. There is no new catalogue admin UI or automated scheduling job.

## Verification

Use the existing isolated runner: `.venv/bin/python tools/workspace.py test`.
`social.test_challenges` is explicitly allowlisted. The PostgreSQL runner is:
`.venv/bin/python tools/auth_postgres_tests.py --postgres-bin /opt/homebrew/opt/postgresql@17/bin`.
Never use unrestricted discovery (`chat/tests.py` connects to a live socket on import).

All 269 tests passed on isolated PostgreSQL, including concurrent invitations,
concurrent confirmations, pause-versus-completion, action retry receipts, stale
revisions, unauthorized/expired sessions, history pagination, snapshot stability,
rollback and JSON-schema validation. The SQLite runner passed with its 18 documented
PostgreSQL-only skips. A separate opt-in iOS native test used normal local OTP auth,
real Django/PostgreSQL and Synapse with two synthetic partners and an unrelated
account. It verified encrypted card exchange and the complete lifecycle plus lost
acknowledgement recovery. See frontend `docs/validation/challenges/README.md`.

The subsequent QA pass added partner-block and membership-replacement checks:
all 271 tests pass on isolated PostgreSQL. Production backend behavior is unchanged
by that pass; frontend recovery/navigation fixes and actual native two-partner
results are recorded in frontend `docs/validation/challenges/qa.md`.
