# Git delivery and publication policy

**Independent acceptance passed on 2026-10-01.** Initial publication follows
an explicit staging review. Deployment records and credentials remain local;
the Git history is the record of published releases. Never force-push over
existing history. Target remote:

```
git@github.com:jupiterr-chen/research-kb.git
```

## Publication allowlist (42 files, independently reviewed)

Generated and scanned by `scripts/scan-publication.py` (local evidence script;
result before final Git metadata: **41 files, 0 private-marker matches**.
The final staged tree adds `.gitattributes` to preserve Linux script line endings;
Codex independently scans all 42 staged files for private markers and credentials.

```
README.md
.gitignore
.gitattributes
.dockerignore
Dockerfile
docker-compose.yml
config/config.example.json
config/stignore
config/.env.example
app/library/**/*.py            (16 modules)
app/tests/**/*.py              (6 test files + fixtures)
scripts/deploy.sh
scripts/backup-catalog.sh
scripts/syncthing_configure.py
scripts/install-windows.ps1
scripts/verify-api.py
scripts/verify-versions.py
docs/DESIGN.md
docs/REQUIREMENTS.md
docs/CHANGELOG.md
docs/GIT_DELIVERY.md
docs/PUBLIC_RUNBOOK.md
```

## Local-only (excluded by `.gitignore`; kept on disk)

| Path | Why |
|---|---|
| `opencode.json`, `AGENTS.md` | agent permissions / local orchestration instructions |
| `.env`, `config/config.json` | real deployment values (host, paths, bind address) |
| `catalog/`, `state/`, `backups/`, `vault/`, `tools/` | runtime DBs, logs, Vault, installers |
| `*.sqlite3*` | indexes/databases |
| `docs/ENVIRONMENT.md`, `docs/ACCEPTANCE.md`, `docs/ISSUES.md`, `docs/RUNBOOK.md`, `docs/DEPLOYMENT_MANIFEST.json`, `docs/TASKBOOK.md`, `docs/OPENCODE_TASK.md`, `docs/REVIEW-*.md`, `docs/source-readmes/` | deployment/acceptance/issue/review evidence with real infrastructure details |
| `scripts/configure-syncthing-server.sh`, `scripts/syncthing-status-server.sh`, `scripts/lock-demo.sh`, `scripts/verify-server.sh`, `scripts/deploy-checks.sh`, `scripts/diag_*.py`, `scripts/scan-publication.py` | one-off local evidence/diagnostic helpers containing real IDs/paths |

Excluding is not deleting: all files remain on disk.

## Parameterization

- `docker-compose.yml` takes `RESEARCHKB_HOME/REPORTS/DISCORD/BIND` from the
  environment or a local `.env`; source mounts stay `:ro`; the default bind is
  `127.0.0.1` (loopback only) and must be set explicitly to expose a LAN address.
- `config/config.example.json` uses `http://localhost:8765` and container paths.
- `config/.env.example` documents the required variables.
- `scripts/deploy.sh`/`backup-catalog.sh` require `RESEARCHKB_*` and never
  contain private paths.
- `scripts/install-windows.ps1` requires `-ServerHost`, `-ServerDeviceId`,
  `-VaultPath`, `-ToolsDir` (no workstation path defaults).
- `.dockerignore` keeps private config/state/evidence/.git out of the build
  context while preserving `app/` (the only build input).

## Deployment equivalence

The real deployment keeps working because the values live in the excluded
`repo/.env`. Before/after `docker inspect` showed identical mounts
(`/archive`,`/discord` ro; `/data`,`/var/syncthing` rw), identical port binding
(the configured LAN bind address on 8765), and unchanged project/service identities
(`research-kb`, `research-kb-library`, `research-kb-syncthing`); the compose
resolved config is unchanged. `docker compose up -d` reported `Running`
(no recreate), both containers healthy.

## Secrets / credentials

No API keys, Syncthing API keys, Discord tokens or private keys are present in
the allowlist. Syncthing **device IDs** are identifiers, not secrets, and only
appear in excluded private files. The publishable tree contains no real catalog
database, no source archive, no Vault contents and no installer binaries.
