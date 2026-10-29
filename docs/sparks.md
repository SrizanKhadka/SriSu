# Sparks — sparks-1

Implementation 2026-10-23; recovery added 2026-10-24. Frontend base `ee9742becd54bd1c30ad76e04e482085662d497b`
on `dev-core-architecture`; backend base `ace26f623db19858bce62392c9a905cb81d8ffb1`
on `codex/couple-chat-rebuild`. See Git history for publication commits.
Figma: `LztysD1YvINX7RwZpnhhTt`, section `184:1634`; design-system page `0:1`.

## Contract and ownership

`contracts/sparks-1/openapi.json` is the backend-owned API contract, mirrored in
frontend `contracts/sparks-1/`. `catalogue.json` records the initial curated
catalogue. `social/services/sparks.py` is the runtime catalogue: six versioned
questions in each of Deep, Memories, Playful, Future. Existing sessions copy their
question and deck, so future catalogue edits cannot silently change their prompt.

All routes are authenticated JSON under `/api/social/sparks/`:

| Route | Behavior |
| --- | --- |
| `GET home/` | Catalogue, actual participant identities, one active session, latest 30 completed/skipped sessions. No answer envelopes, even after reveal. Unpaired users get a null couple. |
| `POST /` | UUID `request_id`, current `couple`, `question_id`. Exact create replay is 200; first create 201; another active Spark or changed intent is 409. |
| `GET {uuid}/` | Current original audience only. Own ciphertext may be returned while open; partner ciphertext is absent until reveal. |
| `PUT {uuid}/answer/` | Strict `envelope` only. First submission is immutable; identical retry succeeds; changed submission is 409. |
| `POST {uuid}/skip/` | Empty JSON. Either participant may skip before reveal. Deletes pending envelopes. Idempotent; already revealed is 409. |
| `PUT {uuid}/card/` | Creator-only acknowledgement of an encrypted Matrix chat-card event ID. It does not affect reveal or grant permissions. |

Responses are private/no-store. Mutation throttle: 120/hour/user. Envelopes have
bounded base64 fields and only `m.megolm.v1.aes-sha2`; plaintext `answer`, keys,
extra fields, arbitrary algorithms and unbounded ciphertext are rejected.
The service cannot determine whether a valid-shaped ciphertext encodes a meaningful
answer. It must never pretend to validate plaintext it cannot read.

## Encryption and mutual reveal

The client uses the already pinned Trixnity 5.9.2 room-encryption service and
vodozemac driver, including normal Olm delivery of Megolm room keys. There is no
new cipher or server-held decryption key. See the primary
[Matrix encryption specification](https://spec.matrix.org/v1.16/client-server-api/#end-to-end-encryption).

Answer ciphertext is **not sent as a Matrix timeline event**. Django holds the
opaque envelope; it releases both only after two submissions commit. Knowing the
room key does not help a waiting partner fetch ciphertext the service withholds.
The chat timeline carries only an encrypted question/reference card.

Clients bind decrypted content to version, session UUID, couple, Matrix membership
epoch, question ID and text, and sender Matrix ID. They verify the inbound Megolm
session against the SDK's sender device keys, require a trusted key-delivery
channel, and reject invalid/blocked/untrusted device trust levels. Unverified but
valid devices retain the chat product's existing trust policy. The SDK replay
cache uses a deterministic non-timeline event ID and timestamp zero for each
session/author; retrying the same envelope is safe, moving it to another session
is rejected. Never later publish these answer ciphertexts as timeline events.

Trust boundary: E2EE protects answer confidentiality from Django/Synapse.
**Django remains trusted for release timing and authenticated ownership**, as it
is for current couple membership. A malicious server can release withheld
ciphertext early. This is not a cryptographic fair-exchange or zero-knowledge
protocol; a malicious client can also submit meaningless encrypted data. Do not
claim otherwise. Transport metadata, deck/question, participants, submission
status and timestamps are server-readable. Account/device compromise and existing
Matrix key-recovery limitations remain. Missing, deleted or unavailable device
keys produce a recoverable “secure sync” state, never a fabricated answer.

## Transactions and authorization

Lock order is existing Couple lock, then Spark row. A conditional uniqueness
constraint permits one open session per couple. Separate unique answers per
session/author make simultaneous submissions and retries safe. The second answer
and reveal timestamp commit atomically. Skip racing the second answer yields
either a revealed pair or a skipped session with zero envelopes, never a partial
reveal. Snapshot membership IDs/user IDs plus Matrix room/epoch prevent a new
relationship from resuming an old one. Unrelated, blocked, ended or replaced
relationships cannot read or mutate sessions. Starting in a replacement epoch
clears inaccessible pending ciphertext from the old active session.

No changes to existing chat transport, auth permissions, Moment APIs or dependency
versions. `card_event_id` is untrusted client-supplied presentation metadata, not
proof of submission, delivery, or reveal.

## Rollout and validation

Additive migration `social.0017_sparksession_sparkanswer_and_more` must be applied
before releasing the client. It adds two tables and constraints; no existing
message data is migrated. Old clients remain compatible. Deploy server/migration
first, then the client. Rolling the client back is safe; dropping these tables
would destroy Sparks and requires an explicit data-retention decision.

The migration was applied to the local Docker database only. Existing local
services remain running. No remote deployment was performed.

Validation: `social.test_sparks` covers strict ciphertext inputs, schema responses,
mutual reveal, either-partner-first flow, exact retries, one active session,
immutable answers, skip, unrelated/anonymous access, relationship epoch changes,
and rollback after failure. PostgreSQL tests cover concurrent creates, concurrent
answers, and skip versus reveal. Run through the allowlisted `tools/workspace.py`
runner and isolated `tools/auth_postgres_tests.py`; see the frontend validation
record for exact final counts and platform coverage.

## Encrypted phone-change recovery (2026-10-24)

The frontend now supports a user-held recovery key and authenticated encrypted
copies of previously readable Spark answers in the user's private Matrix account
data. Django receives neither recovery keys nor backup plaintext. The normal
retrieve/mutual-reveal/original-audience gate is required before a restored copy can
be displayed. See `../SriSu/docs/validation/sparks/recovery.md` and the frontend
flow for cryptographic binding, scope and limitations.

Added `GET /api/social/sparks/history/?after=<uuid>`: authenticated original-couple,
current-room/epoch filtering; UUID keyset pagination; max 25 IDs and `next`, no
ciphertext or plaintext. This enables complete backup beyond Home's latest 30.
GET is additive to sparks-1; deploy backend first. No recovery migration required.
The new regression covers pagination, unauthenticated/unrelated requests, epoch
change, no-store, validation and the unrevealed partner's empty answer response.
