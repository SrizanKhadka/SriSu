# Couple recommendations and personal Faves

## Release scope

The new APIs recommend **couples**, returning one card per couple and an eligible public
moment preview. Moments within a couple have a separate chronological sequence API.
Existing user suggestions, couple connections, chat, moment CRUD, notes, media permissions,
and page-number list APIs keep their contracts.

Faves belong to the authenticated **individual user**, never to their partner or shared
profile. They are one-way preferences, require no acceptance, and trigger no notifications
or chat changes. There is no public membership list or Fave count. Users cannot Fave their
own couple. Active authenticated users may Fave an active, unblocked couple by known ID
even if it has no current public content.

Explore (26 October 2026) reuses Couple Profile's existing two-partner publication consent.
Cards/library entries include `display_name` and authorized `avatars` only when both current
members approved the current identity fingerprint and audience. Otherwise only the couple ID
is returned, rendered as “A SriSu couple” with a fallback. Identity edits invalidate consent.
The member-only serializer and private fields are never exposed by discovery. No public
Fave count, Interested feature, new notification, or dating relationship is introduced.

## Endpoints

All paths below are under `/api/social/` and require `Authorization: Bearer <token>`.
Personalized responses, including errors, use `Cache-Control: private, no-store`.

| Method | Path | Result |
| --- | --- | --- |
| GET | `couple-faves/{couple_id}/` | Authoritative personal state, availability and account Faves version |
| GET | `couple-feed/visible/?couples=7,8&section=global` | Reauthorize up to 50 visible IDs in supplied order, without reranking |
| PUT | `couple-faves/{couple_id}/` | Ensure the requester's Fave exists; no request body |
| DELETE | `couple-faves/{couple_id}/` | Remove the requester's Fave |
| GET | `couple-faves/` | Personal saved-couple library, including no-content/unavailable entries |
| GET | `couple-feed/?section=global&content=moments` | Ranked distinct couple cards |
| GET | `couple-feed/?section=faves&content=moments` | Only Faved couples with eligible moments |
| GET | `couple-moments/sequence/?couple=7&mode=unopened` | Eligible moments oldest first, excluding this user's recorded opens |
| GET | `couple-moments/sequence/?couple=7&mode=all` | Eligible moments oldest first, including recorded opens |

`section` defaults to `global`, `content` to `moments`, `scope` to `local`, and `mode` to `unopened`.
Global accepts `scope=broad` for explicit worldwide sampling. Scope is bound to its feed session.
Unsupported content types are rejected; no challenge endpoint or media model is implemented.
All new lists accept `page_size` (default 10, maximum 50). Follow `data.next` verbatim;
it contains a signed cursor with the original parameters. Do not change them mid-session.

### Fave writes

First PUT returns 201; repeated PUT returns 200 with the original timestamp:

```json
{
  "message": "Couple added to Faves.",
  "data": {"couple_id": 7, "is_faved": true, "created_at": "2026-09-15T06:00:00Z"}
}
```

DELETE returns 204 even if the Fave/target is absent or inaccessible. Removing and adding
again creates a new Fave timestamp. Ownership comes exclusively from authentication;
PUT bodies are rejected. Concurrent writes are serialized on the requester row, and the
database enforces `UNIQUE(user, couple)`. Legacy requests without `If-Match` retain last
serialized write behavior. New KMP writes first GET the account revision and send its numeric
value in `If-Match`. A changed version returns 409 without writing. Successful conditional
writes advance the revision even for a no-op: a newer Remove of an absent Fave prevents an
older delayed Add from committing with its stale revision. PUT also returns `version`.
DELETE remains 204; KMP GETs again to confirm the actual state. A lost acknowledgement
shows Retry rather than claiming success. The application-lifetime FaveStore orders writes,
coalesces opposite desires, re-reads after conflicts, and rejects old-account responses.
Version rows are private metadata, not a second Faves table. No-op legacy writes stay
idempotent without invalidating a feed.

An inactive/blocked couple remains a removable library placeholder. Deleted couples/users
cascade their Faves. Faves are not transferred to new couples or partners. Repeated PUT to
a now-unavailable target returns 404 without removing the existing saved relationship.

### Library

```json
{
  "message": "Results fetched successfully.",
  "data": {
    "next": null,
    "previous": null,
    "results": [
      {"couple_id": 7, "is_faved": true, "created_at": "2026-09-15T06:00:00Z",
       "available": true, "has_active_content": false}
    ]
  }
}
```

Available library entries include the same consent-bound `couple` identity projection.
`available=false` contains no identity fields and covers blocked, inactive,
invalid, or now-own couples, and also forces `has_active_content=false`.
The library uses `(created_at DESC, id DESC)` keyset pagination with a creation cutoff;
new Faves enter on refresh, deletions do not shift offsets. It has no 500-row truncation.

### Feed cards

```json
{
  "message": "Results fetched successfully.",
  "data": {
    "section": "global", "content_type": "moments", "ranking_version": "couple-v2-local",
    "as_of": "2026-09-15T06:00:00Z", "pagination_available": true, "empty_reason": null,
    "next": null, "previous": null,
    "results": [{
      "couple": {"id": 7}, "is_faved": true,
      "has_unopened": true, "active_moment_count": 2,
      "preview": {"id": 42, "title": "Morning walk", "caption": "Coffee together",
                  "created_at": "2026-09-15T04:00:00Z", "expires_at": "2026-09-16T04:00:00Z", "photos": []},
      "moments_url": "https://api.example/api/social/couple-moments/sequence/?couple=7&mode=unopened"
    }]
  }
}
```

Photos retain the existing authenticated moment photo serializer/URLs, ordered by `order,id`.
Preview selection is newest eligible moment; sequence playback is chronological.
The sequence returns the existing moment representation plus `has_opened`, and never records
views itself. Clients call the existing moment detail GET when the moment is actually opened.
`has_unopened` means a missing detail-view record, not qualified watching/completion.

### Empty, caught-up, and error handling

- `no_faves`: show an invitation to add Faves and a separate navigation action to Global.
- `no_active_fave_moments`: saved relationships exist but no eligible content is available.
- `no_active_moments`: Global has no remaining eligible cards.
- `no_unopened_moments`: the sequence has no unopened items; allow explicit `mode=all` replay.
- Faves with only opened content remain after unopened couples; clients can show caught-up
  presentation using `has_unopened`. Never backfill Faves with non-Faved couples.
- `data.next=null` means the current session/pool is exhausted, not necessarily the worldwide
  catalog. Filtered continuation pages can be empty. Refresh starts another session.

Errors follow the existing `error_details` / `message` exception envelope:

| Status | Meaning |
| --- | --- |
| 400 | Invalid ID/query/cursor, changed pagination parameters, self-Fave, or PUT body |
| 401 | Missing/invalid authentication |
| 404 | Inaccessible/missing add target, or new feature disabled |
| 409 | Faves changed during a feed session; discard cursor and refresh |
| 410 | Cursor/session expired or session storage unavailable; discard cursor and refresh |
| 429 | Fave writes or feed request/session creation throttled |

An inaccessible/unknown couple's sequence returns an empty list, matching the existing
moment list filtering behaviour, rather than exposing its existence.

## Ranking and content eligibility

`eligible_moments()` contains the existing permission predicate; `hydrate_moments()`
loads serializer relations. `visible_moments()` composes both for existing callers.

Discovery requires public moments from other couples, an accepted connection with two
active matching partners, intact membership/user audience snapshots, no block in either
direction with either member, and no archived/time-capsule/expired content. Faves grant no
additional access. Existing creator-only writes and private appreciation-note rules remain.

`Score = 40*I + 25*L + 20*F + 15*T`, where:

- I: Jaccard similarity of interest sets. Use non-removed requester interests (linked catalog
  name preferred); fall back to their own couple's shared interests. Candidate shared
  interests take precedence; otherwise union both members' non-removed interests.
- L: 1 for matching city **and country** with either member, 0.4 for matching country,
  otherwise 0. Take the best member match, never sum them.
- F: 1 for this user's Fave, 0 otherwise. Global includes Faves without reserved Fave slots.
- T: `2 ** (-age_hours / 8)` from latest eligible moment creation time at session start.

The production scorer normalizes available weights to sum to one: unknown interest sets
or missing country metadata omit their signal weight; a known non-match remains an observed
zero. Fave membership is always known, and freshness is always available. The defaults remain
40/25/20/15, versioned as `couple-v2-local`, rather than inventing the proposal's unavailable
engagement/context signals. Pure legacy scorer callers retain the old optional behavior.
Interest/scoring strings use NFKC, trimming, case folding and deduplication. Retrieval uses
existing user-selected city/country strings with case-insensitive equality; no precise GPS
or new location permission. Tags, `moment_date`,
free-text moment location, zodiac, age, gender, view totals, Fave totals, and private notes
are not ranking signals. No engagement minimum is required for new couples.

For example: I=.5, L=1, F=0, latest age=8h scores 52.5; I=.25, L=0, F=1,
age=2h scores approximately 42.6. These coefficients and the eight-hour half-life are
tunable launch assumptions, not measured effectiveness claims.

Global loads and scores at most 500 distinct eligible couples. Local retrieval fills that cap
from the selected city first, then the country excluding that city, then broader supply.
The same tier order is enforced during composition, so a high global score cannot displace
suitable local supply. Within a tier, deterministic user/day MD5 sampling bounds metadata
loading; score descending, latest time descending and ID ascending order the sample.
Every tenth position within each tier uses the existing deterministic SHA256 exploration,
preferring remaining non-Faves without crossing geographic tiers. Scope=broad removes the
tier priority and samples the wider eligible pool. Sampling rotates daily and makes no
claim to find the globally optimal ranking or enforce a strict database scan bound.

`coverage=local|local_with_fallback|broader` explains the actual selected pool. Country or
wider fallback triggers the visible “further afield” copy; no country uses broader discovery.
City/interests, city only, interests only and neither are supported without blocking setup.
A couple appears once regardless of upload volume. Card reasons use only actual locality,
interest overlap or freshness, never another user's private Faves.

Faves has no Global candidate cap, no scoring, and no exploration: sort by unopened content,
latest eligible publication descending, then couple ID ascending. Both feeds allow revisits
after unopened content. New users with no metadata get freshness and Global exploration.

## Stable sessions and 24-hour expiry

Only ordered IDs and session context are stored in the dedicated Redis cache for 15 minutes.
Sessions bind to the user, section, content type, page size, Fave fingerprint, ranking version,
scope and creation cutoff. Fave fingerprints combine the private revision with a database
count/max-row aggregate (without materializing the whole library). Remove/re-add invalidates
old sessions. Repeated legacy PUT does not invalidate; conditional no-op writes do. Page
state and revision are bound together so a concurrent write cannot stamp stale booleans with
a new revision. Library/visible revalidation rejects a concurrent version change with 409.
Global also binds a hash of the requester's normalized country, city and effective
interest set. Editing these preferences invalidates its continuation with 409; a fresh
request uses the new preferences. Unrelated profile edits and geographical changes do
not invalidate Faves ordering. Saved-library pages expose their revision so the client
can restart assembly instead of merging pages from different memberships.

Every page checks current PostgreSQL eligibility, skips unavailable couples and replenishes
from later saved IDs. Cursors advance past all scanned positions, including skipped ones.
There is a final eligibility/expiry check after assembling the page. If content becomes
unavailable during assembly, that check can produce a short page rather than stale content.
The same cursor can be retried; it is not consumable. Retries may omit newly inaccessible
content, but never rebuild the ranking. Following returned cursors does not repeat couples.
New publications wait until refresh. Profile edits do not reorder an existing session.

The session freezes couple order, not visibility or previews. Existing pre-cutoff content
can change eligibility, and the preview may change. Detail/photo endpoints remain the final
authority. Moment sequence pagination uses `(created_at ASC,id ASC)` and a creation cutoff;
opened/deleted/expired entries can disappear without shifting offsets.

Every moment expires individually at server-created time plus 24 hours. Edits/uploads do not
renew older moments. The existing cleanup job's retention period does not grant feed access.
Clients must remove expired cards and stop expired playback using `expires_at`. Preserve
the existing deployment rule denying direct access to `media/couples/moments/`; do not replace
authenticated photo URLs with permanent public URLs. Already delivered bytes cannot be revoked.

If Redis fails on the initial request, return a freshly authorized first page with
`pagination_available=false` and no next link. A lost continuation session returns 410;
it is never silently reranked. Database authorization failures propagate as errors.

## Configuration, migration, and rollout

1. Apply `python manage.py migrate` before enabling client use. Migration
   `social.0010_couplefavemodel` creates the Faves table, unique `(user,couple)` constraint,
   and `(user,-created_at,-id)` index. Migration `social.0021_couplefaveversion` adds one private
   revision row per user, created lazily at first mutation; existing Faves are preserved.
   Apply 0021 and deploy the additive backend endpoints/fields **before** the new client.
   Older PUT/DELETE callers remain compatible. No production migration/deployment was run.
2. The installed Channels Redis dependency also supports Django's Redis cache backend;
   no new package or Redis service is required. Set `COUPLE_FEED_REDIS_URL` when needed
   (supports deployment credentials/TLS through the URL). Default is
   `redis://<REDIS_HOST>:<REDIS_PORT>/1`, with prefix `srisu-couple-feed` and one-second
   connect/read timeouts. This is separate from Channels and the default throttle cache.
3. `COUPLE_FEED_ENABLED=false` disables **all new endpoints**, including Faves, without
   affecting old endpoints or deleting saved data. Default is true. Roll out the new
   client screens only after migration and a cache connectivity check.
4. Python settings `COUPLE_FEED_SESSION_TTL`, `COUPLE_FEED_CANDIDATE_LIMIT`,
   `COUPLE_FEED_EXPLORATION_INTERVAL` (0 disables exploration), `COUPLE_FEED_WEIGHTS`,
   and `COUPLE_FEED_RANKING_VERSION` control ranking. Bump the version when tuning weights.
   A ranking-version change expires old sessions; unchanged sessions retain order until expiry.

Fave writes allow 120 requests/min/user. Feed requests allow 120/min/user, with at most
30 new sessions/min/user. These follow the existing DRF throttle/default-cache convention;
they are best-effort application throttles, not a distributed abuse-prevention system.

Existing moment expiry/feed indexes and the view uniqueness index are reused. Interest
loading uses bulk prefetches; only returned cards load photos. No serialized private response
cache, category index, outbox, worker, or engagement aggregate is added. At larger active
catalog sizes, benchmark candidate sampling/snapshot sizes and Faves scans before changing
indexes or retrieval. Faves snapshots intentionally contain the whole eligible saved pool.

INFO logs under `social.api.couple_views` report section, pool size, result count, request
duration, ranking version, and cache availability, without private membership lists or
profile metadata. These are delivery diagnostics, not measured impressions.

## Verification and extension boundary

```sh
.venv/bin/python tools/workspace.py test
.venv/bin/python tools/workspace.py check
.venv/bin/python tools/workspace.py migrations
.venv/bin/python tools/auth_postgres_tests.py --postgres-bin /opt/homebrew/opt/postgresql@17/bin
```

Use the allowlisted isolated runners; do not use unrestricted discovery or the application
DB for unit/concurrency tests. October 26: 293 selected tests pass on PostgreSQL (no skips);
SQLite executes 270 and skips 23 row-lock-specific cases. New tests cover conditional no-op
barriers, concurrent opposite writes, lost-ack replay, consent withdrawal, bounded tier
retrieval, scope-bound cursors, cold starts and in-flight revalidation. Existing suites cover
partner independence, blocks/audience/expiry, pagination and constant per-card query counts.
The frontend's `docs/validation/explore/README.md` records render, transport and device checks.
No new analytics SDK or impression tracking was added; existing aggregate latency logs remain.

Implementation verification (15 September 2026): all 77 social regression/new tests passed
on PostgreSQL, including five row-locking/concurrency tests. Migration drift and Django
system checks passed. A separate disposable database with 500 couples, 1,500 active moments,
and 100 Faves verified both feed continuations against real Redis in a temporary namespace;
those cache keys and the database were removed afterward. PostgreSQL EXPLAIN ANALYZE was
also checked for the largest queries. This is a local synthetic check, not a production
latency SLA or recommendation-quality evaluation.

The couple scorer is pure and media-independent. A future challenge candidate query supplies
eligible couple IDs and content freshness, while retaining common active-couple/block and
Faves rules. Challenges define their own audience, lifecycle, preview, media readiness, and
content ordering; they do not automatically inherit moment expiry. Add that concrete query
and serializer when the product is defined. No generic content registry/table is needed now.
