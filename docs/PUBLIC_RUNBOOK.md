# Public runbook

Generic operations guide for a self-hosted `research-kb` deployment. Replace
placeholders (`/path/to/...`, `host.example`, `<device-id>`) with your values.

## 1. Prerequisites

- Linux host with Docker + Docker Compose v2.
- Two **read-only** source roots:
  - a financial-report archive containing `archive.sqlite3` (SQLite, may be WAL);
  - a Discord export containing `index/documents.jsonl` (and optional `meta.json`).
- Optional: Syncthing + Obsidian on a client to view the rendered notes.

## 2. Configuration

Copy and edit:

```sh
cp config/.env.example .env                 # next to docker-compose.yml
cp config/config.example.json config/config.json
```

`.env` (required):

```
RESEARCHKB_HOME=/path/to/research-kb-data
RESEARCHKB_REPORTS=/path/to/reports-fetcher/reports
RESEARCHKB_DISCORD=/path/to/discord_export
RESEARCHKB_BIND=127.0.0.1        # set a trusted LAN address to expose the API
```

`config/config.json` key fields: `bind_host` (`0.0.0.0` inside the container;
the host port mapping controls exposure), `catalog_db`/`vault_dir`/`state_dir`
(container paths under `/data`), `public_base_url` (the URL clients use), and the
`reports`/`discord` source types with their roots (`/archive`, `/discord`).

The library container mounts the sources read-only. Do not mount them writable.

## 3. Start / stop

```sh
docker compose up -d --build
docker compose ps
docker logs --tail 50 research-kb-library
docker compose stop library
docker compose down          # do NOT use `down -v`; never delete volumes
```

## 4. Ingestion

Ingestion runs immediately on start and then hourly (`ingest_interval_seconds`).
Manual, idempotent run (guarded by a cross-process lock):

```sh
docker exec research-kb-library python -m library ingest --config /app/config/config.json
```

## 5. API

Metadata search (not full text):

```sh
curl 'http://127.0.0.1:8765/api/v1/search?source=reports&symbol=000000&page=1&page_size=20'
curl 'http://127.0.0.1:8765/api/v1/documents/reports/<doc_id>'
curl -I 'http://127.0.0.1:8765/api/v1/files/reports/<doc_id>'                 # HEAD
curl -H 'Range: bytes=0-99' 'http://127.0.0.1:8765/api/v1/files/reports/<doc_id>'
curl 'http://127.0.0.1:8765/api/v1/files/reports/<doc_id>?version=<version_id>'  # historic
```

`GET/HEAD/Range/304` verify the file's SHA256 against the stored identity first;
a changed or missing file returns `409` rather than wrong bytes.

## 6. Backup

Use the SQLite online backup API (never copy a live database/WAL by hand):

```sh
RESEARCHKB_HOME=/path/to/research-kb-data sh scripts/backup-catalog.sh
```

Also back up the human note directories under `vault/` and the deployable files
(compose, config, scripts). Bidirectional sync is not a backup.

## 7. Safe restore (no live overwrite)

Container path ≠ host path: the host `RESEARCHKB_HOME` directory is mounted at
`/data`. A host file `RESEARCHKB_HOME/catalog-restore-X/catalog.sqlite3` is
`/data/catalog-restore-X/catalog.sqlite3` inside the container.

```sh
RESEARCHKB_HOME=/path/to/research-kb-data
TS=$(date -u +%Y%m%dT%H%M%SZ)
HOST_RESTORE="$RESEARCHKB_HOME/catalog-restore-$TS"
CT_RESTORE="/data/catalog-restore-$TS/catalog.sqlite3"

docker compose stop library
mkdir -p "$HOST_RESTORE"
cp "$RESEARCHKB_HOME/backups/catalog-<UTC>.sqlite3" "$HOST_RESTORE/catalog.sqlite3"

# read-only integrity check of the new file
python3 - "$HOST_RESTORE/catalog.sqlite3" <<'PY'
import sqlite3, sys
c = sqlite3.connect("file:%s?mode=ro" % sys.argv[1], uri=True)
print("integrity:", c.execute("PRAGMA integrity_check").fetchone()[0])
c.close()
PY

# point config.json at the CONTAINER path, then force-recreate to reload config
python3 - "$CT_RESTORE" <<'PY'
import json, sys
p = "config/config.json"
cfg = json.load(open(p, encoding="utf-8"))
cfg["catalog_db"] = sys.argv[1]
json.dump(cfg, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
PY
docker compose up -d --force-recreate library
```

Rollback: set `catalog_db` back to the original container path and
`docker compose up -d --force-recreate library`. The old catalog and its
`-wal/-shm` are never modified, so rollback is safe. Never overwrite a live
SQLite file or pair a restored database with stale sidecars.

## 8. Syncthing / Obsidian (optional)

- Run Syncthing with a dedicated config directory; bind its admin UI to
  `127.0.0.1`.
- Share only the `vault/` folder. Ignore `.obsidian`, `.stfolder`, `.stversions`,
  `*.tmp` and sync-conflict files (see `config/stignore`).
- Pair device IDs explicitly; keep global discovery/relay off for LAN-only.
- The active catalog SQLite, state and credentials must never be synced.

## 9. Troubleshooting

| Symptom | Check |
|---|---|
| `/healthz` not reachable | `docker compose ps`; port binding; container logs |
| `status: degraded` | a source `last_error`; fix source, next ingest recovers |
| File returns 409 | content changed after ingestion; re-ingest, or restore the original |
| Card not updated | run a manual ingest; confirm the generated file was not hand-edited |
