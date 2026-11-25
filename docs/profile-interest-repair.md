# Profile interest writes and onboarding photo verification

Local changes on `codex/couple-chat-rebuild`, based at `6e0935c1c4f24267b9a956d259db5d113e9bb903`, 2026-11-22. No deployment or migration.

Publication: `codex/profile-interest-fixes` targets the current `dev-core-architecture`; only the task’s scoped patch is published, including the couple-profile interest writer and regression tests added during review. The older working checkout and its unrelated local changes remain untouched. Frontend companion branch: `SriZan12/SriSu:codex/onboarding-interests-home-fixes`. Hosted CI verifies the isolated PR tree; the results below describe the working-checkout verification.

`authentication/interests.py` provides normalized catalogue presentation and actor-scoped interest writes. Existing catalogue IDs and wire shapes remain unchanged. Blank catalogue labels are omitted from presentation; category/name sorting is stable. No catalogue records are renamed or deleted.

Profile PATCH/PUT retains the current transaction and user row lock. Interest entries validate before writes, including catalogue existence and ownership of any supplied user-interest ID. Catalogue IDs determine the canonical saved label. Duplicate requests do not create new selections, legacy duplicate rows are marked removed, and removal/reselection remains supported. Legacy name-only interests continue to work. Existing clients may keep sending names; a valid ID now wins over a stale client label. Install this backend before the new client when deploying. Existing catalogue caching/invalidations are preserved.

The onboarding upload endpoint itself is unchanged. The paired client now prepares JPEGs before upload and retains validated bytes for retry, including native HEIC/orientation conversion on iOS. Server size, image validation, authorization and profile-completion rules remain in place. No permissions or pre-link profile privacy rules changed.

Validation: isolated workspace suite ran 318 tests with 25 skips and no failures; workspace system check and migration consistency passed. `authentication.test_auth.InterestRepairTests` covers catalogue presentation, canonical ID writes, repeated/removal/reselection writes, foreign IDs, validation before mutation, preservation of legacy rows and JPEG onboarding completion. Real KMP multipart traffic against the disposable backend separately verified upload/reload and interest save/replay/remove/reload. SQLite skips are not evidence of PostgreSQL concurrency verification.

Frontend handoff: `../SriSu/docs/flows/profile-onboarding-loading-repair.md`.

Review follow-up (2026-11-23): normalize legacy name-only selections when removing or promoting them, retain owned row IDs, reject blank catalogue selections before any mutation, and version the presentation cache. Both personal-profile and couple-profile interest writers now serialize on the same user lock; the couple writer takes that lock before checking its revision. Couple-profile normalization preserves catalogue links and deterministically retires duplicate rows. Existing sharing and notification behavior is unchanged.

The working-checkout follow-up suite passed 324 tests on disposable PostgreSQL with no skips; SQLite passed 324 tests with 26 database-feature skips. Added coverage includes stale concurrent edits across both interest editors, legacy whitespace/name-only requests, duplicate promotion, blank-label validation and old cache isolation. No database schema changes or migrations are required.

The isolated publication tree was then checked against the current integration code: 402 maintained tests passed on disposable PostgreSQL (no skips), and 402 passed on SQLite (34 database-feature skips). This confirms the follow-up alongside the integration branch’s newer functionality, independently of the older working checkout.
