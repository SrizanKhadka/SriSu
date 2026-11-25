# SriSu design and core implementation

The later user request authorizes the bounded core implementation on
`dev-core-architecture`. Its canonical record is
[08-core-implementation](../../SriSu/docs/architecture/system-design/08-core-implementation.md)
and its evidence is [09-core-validation](../../SriSu/docs/architecture/system-design/09-core-validation.md).
Backend [contracts/core-1](../contracts/core-1/manifest.json) now owns the implemented
subset; proposed v2 documents remain proposals. This local work is not published.

---

## Original design-only handoff (historical)

# SriSu system design reference — proposed 0.1

Status: **PROPOSED; design only, 2026-09-26. Implementation requires approval.**

The canonical architecture and incremental migration plan lives in the separate
frontend repository **`SriZan12/SriSu`**, at:

`docs/architecture/system-design/README.md`

With the standard sibling checkouts, open
[the local canonical design](../../SriSu/docs/architecture/system-design/README.md).
If checkout locations differ, resolve them through the existing project map or
workspace configuration rather than creating another design copy.

| Baseline | Commit / branch |
| --- | --- |
| Backend `SrizanKhadka/SriSu` | `1a2df95c5aece1d110408bf90aa9ed6f9970da18`; started from `codex/workspace-integration` |
| Frontend `SriZan12/SriSu` | `8af5deb9c9a246504bd1b3b4776122505687feb9`; started from `dev-new-theme-couple-profile` |
| Both local design branches | `refactor/srisu-system-design` |
| Integration targets | Backend `dev`; frontend `dev-new-theme` |
| Figma source | File `LztysD1YvINX7RwZpnhhTt`; page `0:1`, with feature-specific screen nodes still required |

Both baselines include preserved pre-existing workspace setup work. This pointer
does not mean the design is committed, pushed, deployed or available on another
laptop yet. Do not link to a nonexistent remote design commit. On later authorized
publication, replace the local reference with an immutable canonical GitHub commit
link and record both publication SHAs without duplicating the documents.

Implementation contract authority will live in this backend repository after the
relevant phase is approved. Until then, the frontend design's `contracts/` directory
contains a **proposed subset**, not the current production API. The plan is A minimal
foundations → B Authentication/session/linking → C Chat → D Sparks, Challenges,
Moments/discovery. Explicit approval is required between major phases.

No backend application behavior, dependencies, API routes or migration files were
changed for this system-design task. Existing isolated verification is documented
in the canonical design; concurrency and live service limitations remain explicit.

## Couple Profile implementation

The additive `couple-profile-1` feature reuses current social membership, core-1
responses, chat transport and media validation. Read [the implemented contract,
permission/concurrency model and rollout requirements](couple-profile.md). Shared
sections require current membership; visitor publication requires both partners'
content-bound consent. Plans/history remain private; author-owned answers and
individual interests are not transferable shared identity fields.
