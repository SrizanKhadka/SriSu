# Find Partner profile details

Decision, 22 November 2026: before linking, the owner must opt in to sharing
bio and interests. Extra profile album photos remain private. This does not
change couple-profile section consents or Moment/chat authorization.

## API and privacy

- `GET /api/social/find-partner/?phone_number=...` retains the existing minimal
  identity response and adds `data.profile_access_token`. It conceals users
  blocked in either direction. The grant is signed, lasts 30 minutes, and binds
  viewer ID, target ID and a keyed fingerprint of the target's current phone.
- `GET /api/social/partner-profiles/{id}/` accepts that grant in
  `X-Partner-Profile-Access`, never a URL parameter. Authentication is still
  required. A current pending/accepted invitation between these exact people
  also authorizes the read without the grant. Arbitrary ID enumeration, grant
  reuse by another account, inactive users, blocks and expired grants get 404.
- The `data` envelope contains `user`, `details_visible`, `connection_state`
  and `request_id`. User identity contains ID, name, username, phone and avatar;
  bio and non-removed interests appear only when opted in, self, or connected.
  Extra photos, DOB, email, location, private Moments and couple data are omitted.
- Connection states: `available`, `outgoing`, `incoming`, `connected`,
  `unavailable`, `self`. An unrelated existing invitation/relationship yields
  only `unavailable`, with no other person's identity or request ID.
- Reads use the existing discovery throttle and `private, no-store` response.
  Every read rechecks current sharing/block/relationship state. Already viewed
  information cannot be recalled from somebody's memory or screenshots.
- Owner-only `GET/PATCH/PUT /api/auth/setup-profile/` includes the boolean
  `share_partner_details`. New and existing accounts default to false. Omitting
  it preserves the saved value, including older multipart clients.

No new invite mutation API: clients retain the existing idempotent connection
request and invitation-response flows. The detail endpoint does not send anything.

## Rollout

Apply additive migration `authentication/0023_usermodel_share_partner_details`
and restart/deploy the backend before distributing the client that uses the
new detail route. Do not substitute this for reviewing other pending migrations
in a deployment. Older clients can continue to search and update their profile.
No migration or deployment was applied to an application database during this task.

Implementation is local in `codex/couple-chat-rebuild`, based at `6e0935c`;
remote integration `dev-core-architecture` was fetched at `421a982`. Existing
unrelated backend modifications remain in this checkout. Frontend is
`SriZan12/SriSu`, current feature branch `codex/couple-profile-actions`, based at
`4008707`; no branch was switched or reset. These are different repositories.

Tests: `social.test_partner_profiles` covers grants, owner opt-in/out, removal,
blocks, inactive users, invitation direction/revocation, compatibility and
rate limiting. The schema/fixture and route inventory live in `contracts/core-1`.
The frontend's `PartnerProfileTransportIntegrationTest` exercises real Ktor to
Django HTTP with two disposable accounts, including invitation replay.

Verification: maintained isolated suite 311 run, 286 passed and 25 environment
skips, no failures. All 9 new partner profile cases passed, including ended
membership revocation. Django system/migration consistency and 14 shared schema
fixtures passed. Real Ktor/Django synthetic-account search → opt-in → detail →
duplicate invitation → opt-out also passed. No production migration/deployment
or physical-device end-to-end verification was performed.

Publication follow-up (22 November 2026): approved target is
`dev-core-architecture`. The scoped publication branch is
`codex/partner-profile-details`, based on current integration `e265a1d`; the
original dirty `codex/couple-chat-rebuild` checkout is preserved unchanged.
Companion frontend PR: https://github.com/SriZan12/SriSu/pull/33. CI and isolated
verification use the exact publication tree, including current upstream tests.
Publication does not migrate or deploy an application database.
