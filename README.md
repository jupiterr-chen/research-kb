# research-kb

A small self-hosted library that unifies metadata from two read-only archives —
a financial-report archive (SQLite) and a Discord research export (JSONL) — into
one catalog, renders deterministic Obsidian Markdown cards, and serves
**metadata search** plus **on-demand, versioned original-document access** over a
LAN HTTP API. It can mirror the notes to a client with Syncthing and open them in
Obsidian. Original PDFs/HTML are never copied into the Vault.

Status: implementation complete; first-stage scope is metadata search only
(no PDF full-text / semantic search, no OCR, no LLM processing).

## Capabilities

- Read two read-only sources; unified catalog with stable IDs and versions.
- Deterministic Markdown cards + index pages + homepage (idempotent; human note
  directories are never overwritten).
- Metadata search API (`source/symbol/date/keyword`, pagination); explicitly
  labelled as metadata-only.
- Versioned file service: `HEAD`, `Range` (206/416), strong `ETag` (304), correct
  MIME, UTF-8 filenames, HTML served with a sandbox CSP, path-traversal safe.
- Content identity is immutable and verified by SHA256 on the same file
  descriptor before every `GET/HEAD/Range/304`; changed bytes fail closed (409).
- Scheduled ingestion (in-process hourly) with a cross-process lock.
- Docker Compose project with read-only source mounts and a Syncthing sidecar.
- Chinese status dashboard at `/` showing ingestion vs. Windows sync, last/next
  checks, per-source counts, an honest change ledger and abnormal records.
- Read-only Syncthing status collector (`status-collector` compose service):
  host network for loopback REST access only, no listening port, sanitized
  snapshot with no API key / full device id / host paths.
- Public status API at `/api/v1/status` (same data the dashboard renders).

## Layout

```
app/library/     service code (Python standard library only)
app/tests/       unittest suite with synthetic fixtures
scripts/         deployment / verification helpers
config/          example service config + Syncthing ignore file
docker-compose.yml
```

## Tests

```sh
PYTHONPATH=app python -m unittest discover -s app/tests
```
All fixtures are synthetic; no real documents or source snapshots are needed.

## Quick start

1. Prepare two read-only source roots (a reports archive with `archive.sqlite3`
   and a Discord export with `index/documents.jsonl`).
2. Copy `config/.env.example` to `.env` next to `docker-compose.yml` and set
   `RESEARCHKB_HOME`, `RESEARCHKB_REPORTS`, `RESEARCHKB_DISCORD`, and optionally
   `RESEARCHKB_BIND` (a trusted LAN address; default is loopback only).
3. Copy `config/config.example.json` to `config/config.json` and adjust
   `public_base_url` for your host. The optional `sync_monitor` block points the
   dashboard at the sanitized snapshot written by the `status-collector`.
4. Run `bash scripts/deploy.sh` (or `docker compose up -d --build`).
5. Open the dashboard at `http://<host>:8765/`.

See [docs/PUBLIC_RUNBOOK.md](docs/PUBLIC_RUNBOOK.md) for operations, backup and
a safe restore procedure.

## Notes and limitations

- First version is **metadata search**, not full-text or semantic search.
- HTML originals are isolated with `Content-Security-Policy: sandbox`; external
  resources are not localized.
- Source metadata is not trusted blindly: file presence, size and content hash
  are verified, and an upstream overwrite of historic bytes makes that version
  explicitly unavailable instead of serving wrong content under an old ID.
- Only notes/index and small attachments are meant to be synchronized; never
  synchronize the active catalog database, state or credentials.
- Bidirectional sync is not a backup.

## Documentation

- [Design](docs/DESIGN.md)
- [Requirements](docs/REQUIREMENTS.md)
- [Changelog](docs/CHANGELOG.md)
- [Public runbook](docs/PUBLIC_RUNBOOK.md)
- [Git delivery plan](docs/GIT_DELIVERY.md)
