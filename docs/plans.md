# Plans API and rollout

Private couple agenda implemented on `codex/couple-chat-rebuild`, base
`04f5652fd843daf1dc653f08a19c10f149b219fb`, paired with frontend
`dev-core-architecture`, base `93fff640dd180614e78d9185355fbb287b2dc43a`.
Figma Planner `LztysD1YvINX7RwZpnhhTt`, node `185:781`.
See frontend `docs/flows/plans.md` for design and native reminder limitations.

## Contract

`contracts/plans-1/openapi.json` is mirrored in the frontend and tested against
actual responses/routes. Normal auth-1 session/profile gates and private no-store
responses apply. Routes:

| Method/path | Behavior |
| --- | --- |
| GET `/api/social/plans/home/` | Current couple/members, nearest agreed future plan and pending invitation/change count; unpaired returns empty home |
| GET `/api/social/plans/?mode=upcoming\|invitations\|past&cursor=…` | 20 chronological rows, signed couple/mode-bound cursor; past descending |
| POST `/api/social/plans/` | request_id, couple, title, starts_at, optional place/time_zone |
| GET `/api/social/plans/{id}/` | Current original-audience plan, optional active Matrix room metadata |
| POST `/api/social/plans/{id}/actions/` | request_id, expected_revision, action, action-specific values |
| PUT `/api/social/plans/{id}/card/` | Creator acknowledges Matrix event_id without changing revision |
| GET `/api/social/plans/reminders/?ids=…` | Full authorized snapshot of up to 32 agreed future plans; missing IDs cancel local reminders |

Actions: accept, decline, propose, withdraw_change, cancel, complete,
undo_complete, add_item, set_item, remove_item. Propose values are title,
starts_at, place and time_zone. Add item: text and optional assigned_to user ID.
Check item: item_id UUID/checked boolean. Remove: item_id. Other actions reject
unexpected values. Titles 120 chars, places 240, checklist text 120, max 30 items;
new dates must be future and zones valid IANA names. Writes limited to 180/hour/user.

Lock the couple then plan for mutations. Original membership IDs are checked
against both current partners; unrelated accounts, ended membership and blocks
cannot read/mutate. Proposer cannot accept their own change. Only the other
partner can respond. Original agreed time/place stay live until new acceptance;
declining a change keeps that agreement. Either partner can cancel, prepare the
checklist, or mark an agreed elapsed plan done/undo it. No automatic completion.

Persist original creation payload and PlanOperation receipts in the mutation
transaction. Exact duplicate requests return the current state before checking a
stale revision, without undoing later actions; reused IDs with changed payload
conflict. Stale independent writes receive 409. Row-lock tests cover duplicate
creation, competing checklist changes, acceptance/cancellation and receipt rollback.

## Privacy and reminders

Plan metadata is authorized private couple data, server readable. Chat invitation
references and replies use existing Matrix encryption. No new plaintext chat
transport, custom encryption or invented read receipt is introduced. Reminder
preferences stay on each client; there is no server push scheduler. Reminders are
generic and reconcile only when the app can sync. Calendar is a user-confirmed
system editor copy, not automatic synchronization.

## Migration and compatibility

Apply additive `social.0020_coupleplanmodel_cancelled_at_and_more` **before** the
new client. Existing CouplePlanModel IDs, audience, dates, response notes, request
IDs and revisions stay unchanged. New empty time_zone means the original zone is
unknown (the stored datetime instant remains authoritative). Existing completed
records retain completion even without new attribution. A migration regression
seeds a record on schema 0019 and checks every legacy field after 0020.

Legacy `/profiles/{id}/plans/` reads/writes remain. Cancelled records project as
response=no; past filters include them even if their date is future. Old writes
cannot overwrite cancelled/pending-change plans, respond after expiry/completion,
or accidentally remove new fields. Existing profile counters count only agreed,
active future plans. Do not reverse/drop new columns after they contain user data.

Local migration applied 2026-10-24; there were zero pre-existing local plan rows.
Preservation evidence comes from the migration regression plus an older-shaped
synthetic record exercised through both native clients. No production deployment.

## Checks

`social.test_plans` is explicitly in `tools/workspace.py`'s allowlist. Use that
runner (SQLite) and `tools/auth_postgres_tests.py` (disposable PostgreSQL); do not
run unrestricted discovery. Frontend `docs/validation/plans/README.md` records
counts, screenshots, actual native two-partner coverage and remaining limits.
