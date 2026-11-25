# Settings context and disconnection

Additive API on `codex/settings-ui`, based on `e9878cdda1a5d30986aa6295ff633168e66d5684`.
Frontend base `f810047d939327974efb011096e5932be8c07e33`, same feature branch in SriZan12/SriSu.
Design reference: user-provided Settings frames (Frame.png / Frame (1).png), Figma file
LztysD1YvINX7RwZpnhhTt; exact node unavailable. See frontend `docs/flows/settings.md`.

- Authenticated GET `/api/social/settings/`: `data.contract = settings-1`, `data.partner`
  is null if there is no accepted active membership. Otherwise returns `user_id`, `name`,
  `photo` (public profile avatar URL or null), `couple_id`, `connection_id`, `revision`,
  `connected_days` (acceptance elapsed days or null for legacy unknown acceptance time).
  Does not expose partner phone, bio, private photos or notification preferences.
  Validates exactly two active members against both connection participant numbers;
  mismatched, inactive or missing membership returns 409 without a partner projection.
  The normal authenticated projection uses two bounded queries (excluding authentication).
- Authenticated POST `/api/social/settings/disconnect/`: strict integer `connection_id`
  and `expected_revision`. Unrelated IDs concealed as 404; stale/pending records 409.
  Uses existing transactional `end_connection`: ends memberships, clears engaged flags,
  queues Matrix room revocation. Retains the existing shared-data lifecycle (not account deletion).
  Duplicate confirmation of the same revision is safe and does not requeue revocation.
  Returns the current Settings context, including any newer relationship after a retry.
- Both responses use private/no-store. No new database models/migrations or dependencies.
- Existing `end_connection` callers omit the optional revision and keep their existing behavior.
- Deploy backend before updated frontend. Old clients continue to work. New clients on an
  older server show connection-unavailable/retry rather than inventing an unlinked state.
- No couple-wide notification switches were added: existing notification API remains account scoped.

Tests: `social.test_settings` is included in both isolated workspace and PostgreSQL runners.
Covers linked/unlinked/pending, auth, unrelated account concealment, fields/revision validation,
revocation scheduling, replay, state after disconnection, and protection of a later relationship.
QA adds malformed-membership privacy regressions and a query-count bound. The two
Settings endpoints are also registered in `contracts/core-1/routes.json`, mirrored
by the frontend and exercised through actual Ktor service calls in the route tests.


## Private partner nicknames (25 November 2026)

Backend branch `codex/home-refresh-partner-nicknames`, base
`8e5c91645caae34541d5d523afab0dc0b5b77c2f`. Frontend implementation is in the original
SriZan12/SriSu checkout, based on `3872d11ca4d17a40691040830a28efed918bae74`, preserving
its local dark-mode refinement. This is a requested extension to the approved
Settings/partner flow, not a new Figma export.

- `GET /api/social/settings/` retains `settings-1` and canonical `partner.name`.
  It adds `partner.nickname` (string or null) and `partner.nickname_revision` (>=1).
  The nickname is the authenticated viewer's label for their current partner;
  the other partner cannot read it. The linked projection remains two queries.
- `PUT /api/social/settings/partner-nickname/` accepts exactly `connection_id`,
  `expected_revision` (connection revision), `expected_nickname_revision`, and
  `nickname`. Returns the current `settings-1` context. Empty/blank string or null
  clears. Names normalize to NFC with collapsed whitespace, up to 40 Unicode
  codepoints. Controls, bidi overrides and invisible-only names are rejected;
  emoji joiners and non-Latin scripts are supported.
- Different concurrent edits with the same nickname revision produce one success
  and one 409. An identical retry succeeds without incrementing the revision.
  Personal nickname edits do not change the connection's revision or partner's
  nickname, notifications, or public/full name. Generic staff membership forms
  exclude the private fields; operational database access/backups remain server-held
  data, not end-to-end encryption. Unknown fields return 400;
  unauthenticated calls 401; unrelated connections concealed as 404; pending,
  ended, mismatched or stale relationships 409. Responses are private/no-store.
- Additive migration `social.0025_private_partner_nickname` adds two fields on the
  setter's membership. The older shared `CoupleMembership.nickname` field is not
  reused or migrated. Unlinking clears private aliases in the same transaction;
  a new connection starts without one, even when reconnecting to the same user.
  Lock order matches relationship transitions (users, connection, couple, members).
- The frontend resolves typed user IDs through its account/connection-scoped
  nickname store across Home, Chat, Questions, Challenges, Plans, Moments and
  profiles. Canonical identity/search values remain unchanged in backend APIs.
- Authenticated notification preview and inbox copy resolves the recipient's
  current nickname only after resource authorization, including existing inbox
  history on refresh. A stranger's invitation/note never inherits the partner
  nickname. Additive `actor_id`, `actor_name`, `actor_real_name` and actor-only
  segment `user_id`/`user_name` support safe client updates without replacing prose.
  The inbox batches nickname lookup once per page. FCM payloads remain generic;
  existing preview/redaction choices apply. Already displayed OS notifications
  are not retroactively rewritten by this API.

Deployment: apply migration 0025 and deploy the backend before the updated client.
Existing clients ignore the additive fields and retain canonical profile names.
No production data migration/backfill, dependency changes, or Matrix name changes.
Verification covers API permissions/validation/replays, separate viewers, malformed
membership, unlink/relink, notification identity, and PostgreSQL races; see the
frontend task validation record for actual run results and native coverage.
