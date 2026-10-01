#!/bin/sh
# Safe catalog backup + isolated restore verification.
# Uses the SQLite online backup API (consistent snapshot) instead of copying a
# live database/WAL. Non-destructive: writes only a new file under backups/.
set -eu
: "${RESEARCHKB_HOME:?set RESEARCHKB_HOME to the deployment data directory}"
HOME_DIR="$RESEARCHKB_HOME"
SRC="$HOME_DIR/catalog/catalog.sqlite3"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DEST="$HOME_DIR/backups/catalog-$STAMP.sqlite3"

python3 - "$SRC" "$DEST" <<'PY'
import sqlite3, sys
src = sqlite3.connect("file:%s?mode=ro" % sys.argv[1], uri=True)
dst = sqlite3.connect(sys.argv[2])
with dst:
    src.backup(dst)
dst.close(); src.close()
print("backup written:", sys.argv[2])
PY

# Restore verification against the NEW file only (never overwrites the live db).
python3 - "$DEST" <<'PY'
import sqlite3, sys, os
path = sys.argv[1]
conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
print("restore_check_integrity=%s" % conn.execute("PRAGMA integrity_check").fetchone()[0])
print("restore_check_documents=%d" % conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0])
print("restore_check_versions=%d" % conn.execute("SELECT COUNT(*) FROM versions").fetchone()[0])
conn.close()
print("restore_check_size_bytes=%d" % os.path.getsize(path))
PY
