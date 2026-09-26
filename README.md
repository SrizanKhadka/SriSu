# SriSu backend

Django backend for SriSu. The integration branch is `dev`; the shared workflow
setup is available on `codex/workspace-integration` while its review is open.

- [Backend setup and isolated checks](docs/workspace.md)
- [Workflow from another laptop](https://github.com/SriZan12/SriSu/blob/codex/workspace-integration/docs/integration/other-laptops.md)
- [KMP frontend](https://github.com/SriZan12/SriSu/tree/dev-new-theme)
- [Figma design](https://www.figma.com/design/LztysD1YvINX7RwZpnhhTt/Srisu?node-id=0-1)
- [Server verification runs](https://github.com/SrizanKhadka/SriSu/actions)

After creating the environment described in the setup guide:

```sh
.venv/bin/python tools/workspace.py check
.venv/bin/python tools/workspace.py migrations
.venv/bin/python tools/workspace.py test
```

These checks use synthetic settings and an in-memory test database. They do not
start or deploy the application. Read [AGENTS.md](AGENTS.md) before changing code.
