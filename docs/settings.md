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
