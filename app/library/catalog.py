"""SQLite catalog for the unified Research KB.

The catalog is the service's own database (never handed to Syncthing). All
writes for a single source happen inside one transaction so that a failed
parse can never clear previously committed rows.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .models import STATE_CONFLICT, STATE_MISSING, STATE_READY, Document, Version

LEDGER_BASELINE_KEY = "change_ledger_initialized"
LEDGER_SEEDED_AT_KEY = "change_ledger_seeded_at"

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    source TEXT NOT NULL,
    doc_id TEXT NOT NULL,
    title TEXT,
    display_title TEXT,
    summary TEXT,
    author TEXT,
    market TEXT,
    symbol TEXT,
    doc_type TEXT,
    language TEXT,
    report_period TEXT,
    filing_date TEXT,
    published_at TEXT,
    report_date TEXT,
    status TEXT,
    available INTEGER NOT NULL DEFAULT 0,
    source_url TEXT,
    metadata_json TEXT,
    first_seen_at TEXT,
    last_seen_at TEXT,
    PRIMARY KEY (source, doc_id)
);
CREATE TABLE IF NOT EXISTS versions (
    source TEXT NOT NULL,
    doc_id TEXT NOT NULL,
    version_id TEXT NOT NULL,
    sha256 TEXT,
    bytes INTEGER,
    media_type TEXT,
    rel_path TEXT,
    ext TEXT,
    is_current INTEGER NOT NULL DEFAULT 0,
    state TEXT,
    source_url TEXT,
    content_changed_at TEXT,
    observed_at TEXT,
    observed_sha256 TEXT,
    observed_bytes INTEGER,
    mtime_ns INTEGER,
    declared_sha256 TEXT,
    PRIMARY KEY (source, doc_id, version_id)
);
CREATE TABLE IF NOT EXISTS sources (
    source TEXT PRIMARY KEY,
    last_ok_at TEXT,
    last_error TEXT,
    snapshot_json TEXT,
    counts_json TEXT
);
CREATE TABLE IF NOT EXISTS ingest_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT,
    finished_at TEXT,
    ok INTEGER,
    counts_json TEXT,
    error TEXT,
    changes INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    source TEXT NOT NULL,
    doc_id TEXT,
    kind TEXT NOT NULL,
    title TEXT,
    detail_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_changes_at ON changes(at);
CREATE INDEX IF NOT EXISTS idx_changes_source ON changes(source);
CREATE INDEX IF NOT EXISTS idx_documents_source_available ON documents(source, available);
CREATE INDEX IF NOT EXISTS idx_documents_market ON documents(market);
CREATE INDEX IF NOT EXISTS idx_documents_symbol ON documents(symbol);
CREATE INDEX IF NOT EXISTS idx_documents_period ON documents(report_period);
CREATE INDEX IF NOT EXISTS idx_versions_current ON versions(source, doc_id, is_current);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class Catalog:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.RLock()
        directory = os.path.dirname(os.path.abspath(path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=15000")
        self._conn.executescript(SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(versions)")}
        for name, decl in (
            ("observed_sha256", "TEXT"),
            ("observed_bytes", "INTEGER"),
            ("mtime_ns", "INTEGER"),
            ("declared_sha256", "TEXT"),
        ):
            if name not in columns:
                self._conn.execute("ALTER TABLE versions ADD COLUMN %s %s" % (name, decl))
        source_columns = {row[1] for row in self._conn.execute("PRAGMA table_info(sources)")}
        for name, decl in (
            ("last_attempt_at", "TEXT"),
            ("last_error_at", "TEXT"),
        ):
            if name not in source_columns:
                self._conn.execute("ALTER TABLE sources ADD COLUMN %s %s" % (name, decl))
        run_columns = {row[1] for row in self._conn.execute("PRAGMA table_info(ingest_runs)")}
        if "changes" not in run_columns:
            self._conn.execute("ALTER TABLE ingest_runs ADD COLUMN changes INTEGER NOT NULL DEFAULT 0")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def _tx(self):
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    # ------------------------------------------------------------------ writes
    def commit_snapshot(
        self,
        source: str,
        documents: Iterable[Document],
        snapshot: Optional[Dict[str, Any]] = None,
        counts: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, int]:
        from .changes import KIND_BECAME_UNAVAILABLE, KIND_LEDGER_BASELINE, build_event, doc_events

        docs = list(documents)
        now = utc_now()
        seen_ids = set()
        events: List[Dict[str, Any]] = []
        with self._tx() as conn:
            old_docs = {
                row["doc_id"]: dict(row)
                for row in conn.execute("SELECT * FROM documents WHERE source=?", (source,))
            }
            old_versions: Dict[str, Dict[str, Dict[str, Any]]] = {}
            for row in conn.execute(
                "SELECT doc_id, version_id, sha256, bytes, state, is_current "
                "FROM versions WHERE source=?", (source,)
            ):
                old_versions.setdefault(row["doc_id"], {})[row["version_id"]] = dict(row)
            ledger_key = "%s:%s" % (LEDGER_BASELINE_KEY, source)
            ledger_seeded_key = "%s:%s" % (LEDGER_SEEDED_AT_KEY, source)
            ledger_ready = self._get_meta_conn(conn, ledger_key) == "1"
            baseline_seen = 0

            for doc in docs:
                seen_ids.add(doc.doc_id)
                existing = old_docs.get(doc.doc_id)
                first_seen = (
                    existing["first_seen_at"] if existing and existing.get("first_seen_at") else now
                )
                # Expected content identity is immutable once persisted. Decide the
                # effective (persisted) identity and state before writing so that a
                # reused artifact id with different bytes is a conflict, not a
                # silent identity rewrite, and document availability follows suit.
                effective: Dict[str, tuple] = {}
                for version in doc.versions:
                    row = conn.execute(
                        "SELECT sha256, bytes FROM versions WHERE source=? AND doc_id=? AND version_id=?",
                        (source, doc.doc_id, version.version_id),
                    ).fetchone()
                    declared = version.sha256
                    if row is not None:
                        persisted_sha, persisted_bytes = row["sha256"], row["bytes"]
                        eff_sha = persisted_sha if persisted_sha else declared
                        eff_bytes = persisted_bytes if persisted_bytes is not None else version.bytes
                        state = version.state
                        if version.state == STATE_MISSING:
                            state = STATE_MISSING
                        elif persisted_sha and version.observed_sha256 and version.observed_sha256 != persisted_sha:
                            state = STATE_CONFLICT
                        elif persisted_sha and declared and declared != persisted_sha:
                            state = STATE_CONFLICT
                    else:
                        eff_sha, eff_bytes, state = declared, version.bytes, version.state
                    effective[version.version_id] = (eff_sha, eff_bytes, state)

                current_candidates = [v.version_id for v in doc.versions if v.is_current]
                current_id = current_candidates[0] if len(current_candidates) == 1 else None
                current_state = effective[current_id][2] if current_id else None
                doc_available = bool(doc.available) and current_id is not None and current_state == STATE_READY
                conn.execute(
                    """
                    INSERT INTO documents (
                        source, doc_id, title, display_title, summary, author, market, symbol,
                        doc_type, language, report_period, filing_date, published_at, report_date,
                        status, available, source_url, metadata_json, first_seen_at, last_seen_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(source, doc_id) DO UPDATE SET
                        title=excluded.title, display_title=excluded.display_title,
                        summary=excluded.summary, author=excluded.author, market=excluded.market,
                        symbol=excluded.symbol, doc_type=excluded.doc_type, language=excluded.language,
                        report_period=excluded.report_period, filing_date=excluded.filing_date,
                        published_at=excluded.published_at, report_date=excluded.report_date,
                        status=excluded.status, available=excluded.available,
                        source_url=excluded.source_url, metadata_json=excluded.metadata_json,
                        last_seen_at=excluded.last_seen_at
                    """,
                    (
                        source, doc.doc_id, doc.title, doc.display_title, doc.summary, doc.author,
                        doc.market, doc.symbol, doc.doc_type, doc.language, doc.report_period,
                        doc.filing_date, doc.published_at, doc.report_date, doc.status,
                        1 if doc_available else 0, doc.source_url, json.dumps(doc.metadata, ensure_ascii=False),
                        first_seen, now,
                    ),
                )
                # Exactly one authoritative current version, or none. Every other
                # retained/historic version for this document is forced to 0 so a
                # real content change can never leave two rows flagged current.
                for version in doc.versions:
                    eff_sha, eff_bytes, state = effective[version.version_id]
                    conn.execute(
                        """
                        INSERT INTO versions (
                            source, doc_id, version_id, sha256, bytes, media_type, rel_path, ext,
                            is_current, state, source_url, content_changed_at, observed_at,
                            observed_sha256, observed_bytes, mtime_ns, declared_sha256
                        ) VALUES (?,?,?,?,?,?,?,?,0,?,?,?,?,?,?,?,?)
                        ON CONFLICT(source, doc_id, version_id) DO UPDATE SET
                            sha256=excluded.sha256, bytes=excluded.bytes,
                            media_type=excluded.media_type, rel_path=excluded.rel_path,
                            ext=excluded.ext, state=excluded.state,
                            source_url=excluded.source_url,
                            content_changed_at=excluded.content_changed_at,
                            observed_at=excluded.observed_at,
                            observed_sha256=excluded.observed_sha256,
                            observed_bytes=excluded.observed_bytes,
                            mtime_ns=excluded.mtime_ns,
                            declared_sha256=excluded.declared_sha256
                        """,
                        (
                            source, doc.doc_id, version.version_id, eff_sha, eff_bytes,
                            version.media_type, version.rel_path, version.ext,
                            state, version.source_url, version.content_changed_at, now,
                            version.observed_sha256, version.observed_bytes, version.mtime_ns,
                            version.sha256,
                        ),
                    )
                conn.execute(
                    "UPDATE versions SET is_current = CASE WHEN version_id = ? THEN 1 ELSE 0 END "
                    "WHERE source = ? AND doc_id = ?",
                    (current_id, source, doc.doc_id),
                )

                # Change ledger. On the very first reconciliation after the ledger
                # was introduced, documents already present are the baseline and
                # must not be reported as newly imported today.
                if existing is None:
                    events.append(build_event(
                        source, doc.doc_id, "imported", doc.display_title or doc.title, {}))
                elif not ledger_ready:
                    baseline_seen += 1
                else:
                    events.extend(doc_events(
                        source, doc, existing, old_versions.get(doc.doc_id, {}),
                        effective, current_id, doc_available,
                    ))

            # Documents previously known for this source but absent from the new
            # snapshot are retained but marked unavailable (never deleted).
            if seen_ids:
                placeholders = ",".join("?" for _ in seen_ids)
                absent = conn.execute(
                    "SELECT doc_id, display_title, title, available FROM documents "
                    "WHERE source=? AND doc_id NOT IN (%s)" % placeholders,
                    (source, *seen_ids),
                ).fetchall()
                conn.execute(
                    "UPDATE documents SET available=0, status='not_in_snapshot', last_seen_at=? "
                    "WHERE source=? AND doc_id NOT IN (%s)" % placeholders,
                    (now, source, *seen_ids),
                )
                if ledger_ready:
                    for row in absent:
                        if row["available"]:
                            events.append(build_event(
                                source, row["doc_id"], KIND_BECAME_UNAVAILABLE,
                                row["display_title"] or row["title"],
                                {"reason": "not_in_snapshot"}))
            conn.execute(
                """
                INSERT INTO sources (source, last_ok_at, last_error, snapshot_json, counts_json, last_attempt_at)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(source) DO UPDATE SET
                    last_ok_at=excluded.last_ok_at, last_error=NULL,
                    last_attempt_at=excluded.last_attempt_at,
                    snapshot_json=excluded.snapshot_json, counts_json=excluded.counts_json
                """,
                (source, now, None, json.dumps(snapshot or {}, ensure_ascii=False),
                 json.dumps(counts or {}, ensure_ascii=False), now),
            )
            if not ledger_ready:
                events.insert(0, build_event(
                    source, None, KIND_LEDGER_BASELINE, None,
                    {"existing_documents": baseline_seen, "seeded_at": now}))
                self._set_meta_conn(conn, ledger_key, "1")
                self._set_meta_conn(conn, ledger_seeded_key, now)
            for event in events:
                conn.execute(
                    "INSERT INTO changes (at, source, doc_id, kind, title, detail_json) "
                    "VALUES (?,?,?,?,?,?)",
                    (event["at"], event["source"], event["doc_id"], event["kind"],
                     event["title"], json.dumps(event["detail"], ensure_ascii=False)),
                )
        return {
            "documents": len(docs),
            "versions": sum(len(d.versions) for d in docs),
            "changes": len(events),
        }

    def mark_source_attempt(self, source: str) -> None:
        now = utc_now()
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO sources (source, last_ok_at, last_error, snapshot_json, counts_json, last_attempt_at)
                VALUES (?, NULL, NULL, NULL, NULL, ?)
                ON CONFLICT(source) DO UPDATE SET last_attempt_at=excluded.last_attempt_at
                """,
                (source, now),
            )

    def record_source_error(self, source: str, error: str) -> None:
        now = utc_now()
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO sources (source, last_ok_at, last_error, snapshot_json, counts_json,
                                     last_attempt_at, last_error_at)
                VALUES (?, NULL, ?, NULL, NULL, ?, ?)
                ON CONFLICT(source) DO UPDATE SET
                    last_error=excluded.last_error,
                    last_attempt_at=excluded.last_attempt_at,
                    last_error_at=excluded.last_error_at
                """,
                (source, error[:2000], now, now),
            )

    def record_run(self, started_at: str, ok: bool, counts: Dict[str, Any], error: Optional[str],
                   changes: int = 0) -> None:
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO ingest_runs (started_at, finished_at, ok, counts_json, error, changes) "
                "VALUES (?,?,?,?,?,?)",
                (started_at, utc_now(), 1 if ok else 0, json.dumps(counts, ensure_ascii=False),
                 error[:2000] if error else None, max(0, int(changes or 0))),
            )

    def record_render_result(self, ok: bool, error: Optional[str], stats: Optional[Dict[str, Any]]) -> None:
        payload = {
            "at": utc_now(),
            "ok": bool(ok),
            "error": (error or "")[:500] or None,
            "stats": stats or {},
        }
        self.set_meta("last_render_json", json.dumps(payload, ensure_ascii=False))

    def last_render(self) -> Optional[Dict[str, Any]]:
        raw = self.get_meta("last_render_json")
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return None

    # ------------------------------------------------------------------- reads
    def source_state(self, source: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM sources WHERE source=?", (source,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["snapshot"] = json.loads(result.pop("snapshot_json") or "{}")
        result["counts"] = json.loads(result.pop("counts_json") or "{}")
        return result

    def _get_meta_conn(self, conn, key: str) -> Optional[str]:
        row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def _set_meta_conn(self, conn, key: str, value: str) -> None:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    def get_meta(self, key: str) -> Optional[str]:
        with self._lock:
            row = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def recent_changes(self, limit: int = 20) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM changes ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["detail"] = json.loads(item.pop("detail_json") or "{}")
            except (ValueError, TypeError):
                item["detail"] = {}
            result.append(item)
        return result

    def changes_count(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) c FROM changes").fetchone()["c"]

    def changes_since(self, at: Optional[str]) -> int:
        if not at:
            return self.changes_count()
        with self._lock:
            return self._conn.execute(
                "SELECT COUNT(*) c FROM changes WHERE at >= ?", (at,)
            ).fetchone()["c"]

    def source_breakdown(self, source: str) -> Dict[str, Any]:
        with self._lock:
            status_counts = {
                (row["status"] or "unknown"): row["c"]
                for row in self._conn.execute(
                    "SELECT status, COUNT(*) c FROM documents WHERE source=? GROUP BY status",
                    (source,),
                )
            }
            state_counts = {
                (row["state"] or "unknown"): row["c"]
                for row in self._conn.execute(
                    "SELECT state, COUNT(*) c FROM versions WHERE source=? GROUP BY state",
                    (source,),
                )
            }
            total = self._conn.execute(
                "SELECT COUNT(*) c FROM documents WHERE source=?", (source,)
            ).fetchone()["c"]
            available = self._conn.execute(
                "SELECT COUNT(*) c FROM documents WHERE source=? AND available=1", (source,)
            ).fetchone()["c"]
        return {
            "documents_total": total,
            "documents_available": available,
            "status_counts": status_counts,
            "version_state_counts": state_counts,
        }

    def counts(self) -> Dict[str, Any]:
        with self._lock:
            total = self._conn.execute("SELECT COUNT(*) c FROM documents").fetchone()["c"]
            available = self._conn.execute("SELECT COUNT(*) c FROM documents WHERE available=1").fetchone()["c"]
            by_source = {
                row["source"]: {"documents": row["d"], "available": row["a"]}
                for row in self._conn.execute(
                    "SELECT source, COUNT(*) d, SUM(available) a FROM documents GROUP BY source"
                )
            }
            versions = self._conn.execute("SELECT COUNT(*) c FROM versions").fetchone()["c"]
            ready_bytes = self._conn.execute(
                "SELECT COALESCE(SUM(bytes),0) b FROM versions WHERE state='ready'"
            ).fetchone()["b"]
        return {
            "documents_total": total,
            "documents_available": available,
            "versions_total": versions,
            "ready_bytes": ready_bytes,
            "by_source": by_source,
        }

    def _doc_from_row(self, row: sqlite3.Row, versions: List[Version]) -> Document:
        metadata = json.loads(row["metadata_json"] or "{}")
        return Document(
            source=row["source"], doc_id=row["doc_id"], title=row["title"],
            display_title=row["display_title"], summary=row["summary"], author=row["author"],
            market=row["market"], symbol=row["symbol"], doc_type=row["doc_type"],
            language=row["language"], report_period=row["report_period"],
            filing_date=row["filing_date"], published_at=row["published_at"],
            report_date=row["report_date"], status=row["status"],
            available=bool(row["available"]), source_url=row["source_url"],
            metadata=metadata, versions=versions,
        )

    def _versions_for(self, source: str, doc_id: str) -> List[Version]:
        rows = self._conn.execute(
            "SELECT * FROM versions WHERE source=? AND doc_id=? ORDER BY is_current DESC, version_id",
            (source, doc_id),
        ).fetchall()
        return [
            Version(
                version_id=r["version_id"], sha256=r["sha256"], bytes=r["bytes"],
                media_type=r["media_type"], rel_path=r["rel_path"], ext=r["ext"],
                is_current=bool(r["is_current"]), state=r["state"] or "ready",
                source_url=r["source_url"], content_changed_at=r["content_changed_at"],
                observed_sha256=r["observed_sha256"], observed_bytes=r["observed_bytes"],
                mtime_ns=r["mtime_ns"],
            )
            for r in rows
        ]

    def get_document(self, source: str, doc_id: str) -> Optional[Document]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM documents WHERE source=? AND doc_id=?", (source, doc_id)
            ).fetchone()
            if not row:
                return None
            return self._doc_from_row(row, self._versions_for(source, doc_id))

    def get_version(self, source: str, doc_id: str, version_id: Optional[str]) -> Optional[Version]:
        versions = self._versions_for(source, doc_id)
        if version_id:
            for version in versions:
                if version.version_id == version_id:
                    return version
            return None
        # No explicit version requested: only the single authoritative current.
        current = [v for v in versions if v.is_current]
        if len(current) == 1:
            return current[0]
        return None

    def all_versions(self, source: str, doc_id: str) -> List[Version]:
        return self._versions_for(source, doc_id)

    def search(
        self,
        source: Optional[str] = None,
        market: Optional[str] = None,
        symbol: Optional[str] = None,
        doc_type: Optional[str] = None,
        query: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        available_only: bool = True,
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        where: List[str] = []
        params: List[Any] = []
        date_expr = "COALESCE(report_date, substr(published_at,1,10), filing_date)"
        if source:
            where.append("source = ?")
            params.append(source)
        if available_only:
            where.append("available = 1")
        if market:
            where.append("market = ?")
            params.append(market)
        if symbol:
            where.append("UPPER(symbol) = UPPER(?)")
            params.append(symbol)
        if doc_type:
            where.append("doc_type = ?")
            params.append(doc_type)
        if query:
            where.append("(title LIKE ? OR display_title LIKE ? OR summary LIKE ? OR symbol LIKE ?)")
            like = "%%%s%%" % query
            params.extend([like, like, like, like])
        if date_from:
            where.append(date_expr + " >= ?")
            params.append(date_from)
        if date_to:
            where.append(date_expr + " <= ?")
            params.append(date_to)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        page = max(1, int(page))
        page_size = max(1, min(int(page_size), 100))
        offset = (page - 1) * page_size
        with self._lock:
            total = self._conn.execute(
                "SELECT COUNT(*) c FROM documents" + clause, params
            ).fetchone()["c"]
            rows = self._conn.execute(
                "SELECT * FROM documents" + clause
                + " ORDER BY " + date_expr + " DESC, source, symbol, doc_id LIMIT ? OFFSET ?",
                (*params, page_size, offset),
            ).fetchall()
            items = []
            for row in rows:
                current = self._conn.execute(
                    "SELECT * FROM versions WHERE source=? AND doc_id=? AND is_current=1",
                    (row["source"], row["doc_id"]),
                ).fetchone()
                items.append(self._brief(row, current))
        return {
            "total": total, "page": page, "page_size": page_size,
            "page_count": (total + page_size - 1) // page_size if page_size else 0,
            "items": items,
        }

    def _brief(self, row: sqlite3.Row, current: Optional[sqlite3.Row]) -> Dict[str, Any]:
        version = None
        if current:
            version = {
                "version_id": current["version_id"], "sha256": current["sha256"],
                "bytes": current["bytes"], "media_type": current["media_type"],
                "ext": current["ext"],
            }
        return {
            "source": row["source"], "doc_id": row["doc_id"],
            "title": row["title"], "display_title": row["display_title"],
            "market": row["market"], "symbol": row["symbol"], "doc_type": row["doc_type"],
            "report_period": row["report_period"], "filing_date": row["filing_date"],
            "published_at": row["published_at"], "report_date": row["report_date"],
            "available": bool(row["available"]), "status": row["status"],
            "current_version": version,
        }

    def list_available(self, source: str) -> List[Document]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM documents WHERE source=? AND available=1 ORDER BY "
                "COALESCE(report_date,'') DESC, symbol, doc_id",
                (source,),
            ).fetchall()
            return [self._doc_from_row(r, self._versions_for(source, r["doc_id"])) for r in rows]

    def known_doc_ids(self) -> Dict[str, set]:
        with self._lock:
            result: Dict[str, set] = {}
            for row in self._conn.execute("SELECT source, doc_id FROM documents"):
                result.setdefault(row["source"], set()).add(row["doc_id"])
        return result

    def existing_version_map(self, source: str) -> Dict[Tuple[str, str], Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT doc_id, version_id, sha256, bytes, observed_sha256, mtime_ns "
                "FROM versions WHERE source=?", (source,)
            ).fetchall()
        return {
            (row["doc_id"], row["version_id"]): {
                "sha256": row["sha256"], "bytes": row["bytes"],
                "observed_sha256": row["observed_sha256"], "mtime_ns": row["mtime_ns"],
            }
            for row in rows
        }

    def list_documents(self, source: str) -> List[Document]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM documents WHERE source=? ORDER BY "
                "COALESCE(report_date,'') DESC, symbol, doc_id",
                (source,),
            ).fetchall()
            return [self._doc_from_row(r, self._versions_for(source, r["doc_id"])) for r in rows]

    def recent_runs(self, limit: int = 10) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM ingest_runs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["counts"] = json.loads(item.pop("counts_json") or "{}")
            result.append(item)
        return result

    def set_meta(self, key: str, value: str) -> None:
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO meta (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
