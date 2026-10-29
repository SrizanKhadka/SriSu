# Couple-only navigation contract and dating retirement

> Historical document. Its Django WebSocket/chat retention statements are
> superseded by the [Matrix-only chat cutover](chat-v2.md); dating retirement
> and `SingleConnectionModel.BLOCKED` policy remain in force.

2026-10-01, implemented from dev-core-architecture.
Publication branch: codex/modular-navigation-retire-singles; PR base: dev-core-architecture.
Backend baseline: 6433d60556d0c55e1ae06a1848e788c0fa487126.
Frontend SriZan12/SriSu baseline: 00a198a3e579d32eeb578942bd2c86936257e2c5.
Feature commit/push and PR creation were authorized in a follow-up.
No deployment or production migration was performed.

The frontend owns navigation. No server screen-routing engine was added. It reuses
auth profile completion and current `profiles/me/` membership to gate its existing
flows. Resource APIs remain authoritative even after a client has opened a screen.

## Retired operations

Authenticated requests under these route families return HTTP 410:

- `/api/social/connect-single/**`
- `/api/social/single-connection/**`
- `/api/social/user-suggestions/**`
- `/api/social/get-suggestion-profile/**`

Core-1 clients receive `error.code=feature_retired`, `retryable=false` in the existing
error envelope. This is distinct from 410 `cursor_expired` for pagination. Legacy
clients retain the existing APIException envelope. No request creates/accepts a
relationship or redirects to a phone invitation. Unauthenticated requests require
sign-in before reaching the tombstone. Unsupported methods use normal API semantics.

The old dating handlers/serializers/scoring modules and dating-only seed commands
were removed. The later Matrix cutover also removed the Django chat consumer,
message transport, transport commands and WebSocket routes.

## Preserved data and authorization

This navigation change originally introduced no destructive migration. The later
Matrix cutover deleted legacy message tables while retaining `ChatRoom` relationship
identity and `SingleConnectionModel`. BLOCKED
records remain authoritative for existing couple discovery/Moments restrictions.
The model stays available for administrative historical maintenance; it is not a
user-facing dating API. Legacy SINGLE enum values and defaults remain for database
compatibility, and are not evidence of a current partnership.

A dating-only relationship does not authorize Matrix bootstrap. The shared room
selector requires a current accepted couple with both current memberships. Legacy
messages are destroyed by `chat/0006` and are not an archive/export source. Relinking
the same legitimate pair through the established couple service preserves the stable
relationship room UUID rather than restoring the removed transport data.

Individual accounts, personal profile fields, phone invitations (pending/accepted/
rejected/cancelled), unlinked users, Faves and publication consent are preserved.
The legacy personal-preferences endpoint is retained, with its list now constrained
to the authenticated user. It no longer feeds a dating discovery service.

## Contract and release order

`contracts/core-1/routes.json` lists current KMP operations separately from
`retired_endpoints` and `legacy_endpoints`. Schema metadata documents the named
retirement error. Frontend copies and generated fixtures were refreshed together.
Retained auth/couple/chat methods are unchanged. No DB migration is necessary.

Coordinate the server retirement with client update communication: retained calls
work backend-first; old dating clients intentionally receive 410. Nothing has been
deployed by these local edits. Do not apply destructive cleanup later without a
separate review, backups, historical dependency audit and explicit authorization.

## Validation

Selected offline tests cover retirement methods/authentication, no relationship writes,
retained historical messages, denied dating-only chat, current couple revocation,
owner-scoped preferences, auth/partner linking, Faves, Moments and Couple Profile.
Core chat fixtures now exercise current accepted couples; dedicated retirement fixtures
prove dating rows cannot authorize access. See frontend `docs/flows/navigation.md`
for paired platform/transport checks and final exact outcomes. Never run unrestricted
Django test discovery; use tools/workspace.py and the disposable PostgreSQL runner.
