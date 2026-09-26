# SriSu backend guidance

- This is `SrizanKhadka/SriSu` (Django), integration branch `dev`.
  The frontend is the different repository `SriZan12/SriSu`, integration branch
  `dev-new-theme`; its usual local checkout is `../SriSu`.
- At task start, inspect each relevant repository's origin, branch, HEAD, and
  worktree status. Preserve feature branches and user changes. Read live remote
  integration refs when checking upstream; never silently reset or switch.
- Read `docs/workspace.md` and the frontend's `AGENTS.md` and
  `docs/project-context.md` before cross-repository work. The machine-readable
  map is `../SriSu/docs/integration/project-map.json` in the default layout.
  When paths differ, resolve the correct checkout before editing it.
- Figma file: `LztysD1YvINX7RwZpnhhTt`; page `0:1` is the design system.
  Use a feature's screen node. Designs express intended UX, not proof that an
  endpoint, response field, permission, or event exists.
- Inspect frontend DTOs/callers before changing routes, serializers, handlers,
  authorization, pagination, or WebSocket events. Maintain released-client
  compatibility and document migration/deployment order and related PRs.
- Enforce authorization on the server. Test unrelated-user and cross-couple
  access, session expiry, duplicate/concurrent writes, private media, and
  relevant failure states. Never log credentials, OTPs, or private messages.
- Reuse current services/repositories and keep significant architecture
  decisions explicit. Update the shared feature map for behavior changes.
- Use the locked environment and offline runner in `docs/workspace.md`.
  `requirements.txt` differs from `Pipfile.lock`. Do not silently upgrade either.
- Do not run unrestricted test discovery: legacy `chat/tests.py` opens a live
  WebSocket at import time. The workspace runner selects the isolated social
  suites and blocks socket connections. Use a separately specified disposable
  environment for PostgreSQL concurrency and live WebSocket integration tests.
- Never use production credentials/data for local checks. Keep `.venv`, local
  environment files, uploaded media, and secrets out of commits.
- Report commands actually run, skipped coverage, failures, and touched repos.
  Local filesystem access does not imply permission to push to GitHub.
