"""Adapter for the reports-fetcher read-only SQLite archive.

Safety rules:
* Open the live WAL database read-only and read every field for one scan inside a
  single deferred read transaction, so metadata/counts come from one logical
  snapshot. We never copy the live database/WAL/SHM files.
* A version's expected content identity (artifact_id + declared sha256) is
  immutable. We record what we actually observed; a mismatch becomes a
  non-servable ``conflict`` rather than silently rewriting identity.
* No fallback current version: without an authoritative ``current_artifact_id``
  the document is not available (historic versions may still be addressable).
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Any, Dict, List, Optional

from ..models import STATE_CONFLICT, STATE_MISSING, STATE_READY, Document, Version, safe_relpath
from ..textutil import display_title
from .base import BaseAdapter, ScanResult, SourceError, file_stat, hash_file_stable

MAX_OPEN_RETRIES = 3


class ReportsAdapter(BaseAdapter):
    source_name = "reports"

    def _extras(self) -> Dict[str, Any]:
        return dict(self.config.extra)

    def _db_path(self) -> str:
        return self._extras().get("db") or os.path.join(self.root, "archive.sqlite3")

    def _connect(self) -> sqlite3.Connection:
        db_path = self._db_path()
        if not os.path.exists(db_path):
            raise SourceError("reports archive db not found")
        last_error: Optional[Exception] = None
        for attempt in range(MAX_OPEN_RETRIES):
            try:
                conn = sqlite3.connect(
                    "file:%s?mode=ro" % db_path, uri=True, timeout=30,
                    isolation_level=None,  # explicit BEGIN/COMMIT for one snapshot
                )
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA busy_timeout=30000")
                conn.execute("BEGIN")
                conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchall()
                return conn
            except sqlite3.OperationalError as exc:
                last_error = exc
                time.sleep(0.5 * (attempt + 1))
        raise SourceError("cannot open reports archive (read-only) safely: %s" % type(last_error).__name__)

    def scan(self) -> ScanResult:
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT m.report_id, m.market, m.symbol, m.source_id, m.source_url,
                       m.title, m.doc_type, m.source_form, m.filing_date, m.report_period,
                       m.period_source, m.language, m.is_amendment, m.revision_of,
                       m.source_metadata_json, m.status, m.current_artifact_id,
                       a.artifact_id, a.sha256 AS a_sha256, a.bytes AS a_bytes,
                       a.media_type AS a_media_type, a.local_path AS a_local_path,
                       a.state AS a_state, a.fetched_at AS a_fetched_at,
                       a.source_url AS a_source_url
                FROM manifest m
                LEFT JOIN artifacts a ON a.report_id = m.report_id AND a.state = 'ready'
                ORDER BY m.report_id, a.fetched_at
                """
            ).fetchall()
            db_max = conn.execute("SELECT MAX(fetched_at) AS m FROM artifacts WHERE state='ready'").fetchone()["m"]
            status_counts = {
                row["status"]: row["c"]
                for row in conn.execute("SELECT status, COUNT(*) c FROM manifest GROUP BY status")
            }
            market_counts = {
                row["market"]: row["c"]
                for row in conn.execute("SELECT market, COUNT(*) c FROM manifest GROUP BY market")
            }
            ready_artifacts = conn.execute(
                "SELECT COUNT(*) c, COALESCE(SUM(bytes),0) b FROM artifacts WHERE state='ready'"
            ).fetchone()
        except sqlite3.Error as exc:
            raise SourceError("reports read failed: %s" % type(exc).__name__)
        finally:
            try:
                conn.execute("COMMIT")
            except sqlite3.Error:
                pass
            conn.close()

        prefix = self._extras().get("path_prefix", "/app/reports")
        grouped: Dict[str, Dict[str, Any]] = {}
        order: List[str] = []
        for row in rows:
            rid = row["report_id"]
            if rid not in grouped:
                grouped[rid] = {"manifest": row, "artifacts": []}
                order.append(rid)
            if row["artifact_id"]:
                grouped[rid]["artifacts"].append(row)

        documents: List[Document] = []
        available_count = 0
        version_count = 0
        conflict_count = 0
        for rid in order:
            manifest = grouped[rid]["manifest"]
            current_id = manifest["current_artifact_id"]
            versions: List[Version] = []
            current_version: Optional[Version] = None
            for artifact in grouped[rid]["artifacts"]:
                rel = self._local_path_to_rel(artifact["a_local_path"], prefix)
                abs_path = self._safe_join(rel) if rel else None
                exists, size = file_stat(abs_path) if abs_path else (False, -1)
                expected = artifact["a_sha256"]
                observed = None
                mtime_ns = None
                state = STATE_READY
                if exists:
                    observed, size, mtime_ns, stable = hash_file_stable(abs_path)
                    if observed is None:
                        exists = False
                    elif not stable:
                        state = STATE_CONFLICT
                        conflict_count += 1
                if not exists:
                    state = STATE_MISSING
                elif expected and observed != expected:
                    state = STATE_CONFLICT
                    conflict_count += 1
                version = Version(
                    version_id=artifact["artifact_id"],
                    sha256=expected,  # immutable expected identity
                    bytes=artifact["a_bytes"],
                    media_type=artifact["a_media_type"],
                    rel_path=rel,
                    ext=os.path.splitext(rel)[1].lstrip(".").lower() if rel else None,
                    is_current=False,
                    state=state,
                    source_url=artifact["a_source_url"] or manifest["source_url"],
                    content_changed_at=artifact["a_fetched_at"],
                    observed_sha256=observed,
                    observed_bytes=size if exists else None,
                    mtime_ns=mtime_ns,
                )
                if artifact["artifact_id"] == current_id:
                    version.is_current = True
                    current_version = version
                versions.append(version)
                version_count += 1

            doc_available = (
                manifest["status"] == "done"
                and current_version is not None
                and current_version.state == STATE_READY
            )
            if doc_available:
                available_count += 1
            metadata = {
                "source_id": manifest["source_id"],
                "source_form": manifest["source_form"],
                "period_source": manifest["period_source"],
                "is_amendment": bool(manifest["is_amendment"]),
                "revision_of": manifest["revision_of"],
                "source_metadata": _json_or_none(manifest["source_metadata_json"]),
                "current_artifact_id": current_id,
            }
            documents.append(
                Document(
                    source=self.source_name,
                    doc_id=rid,
                    title=manifest["title"],
                    display_title=display_title(manifest["title"]),
                    summary=None,
                    market=manifest["market"],
                    symbol=manifest["symbol"],
                    doc_type=manifest["doc_type"],
                    language=manifest["language"],
                    report_period=manifest["report_period"],
                    filing_date=manifest["filing_date"],
                    published_at=None,
                    report_date=None,
                    status=manifest["status"],
                    available=doc_available,
                    source_url=manifest["source_url"],
                    metadata=metadata,
                    versions=versions,
                )
            )

        snapshot = {
            "db_path": self._db_path(),
            "read_mode": "direct-ro-transaction",
            "max_fetched_at": db_max,
            "status_counts": status_counts,
            "market_counts": market_counts,
        }
        counts = {
            "documents": len(documents),
            "available": available_count,
            "versions_ready": ready_artifacts["c"],
            "versions_seen": version_count,
            "version_conflicts": conflict_count,
            "ready_bytes": ready_artifacts["b"],
            "status_counts": status_counts,
        }
        return ScanResult(documents=documents, snapshot=snapshot, counts=counts)

    def _local_path_to_rel(self, local_path: Optional[str], prefix: str) -> Optional[str]:
        if not local_path:
            return None
        normalized = local_path.replace("\\", "/")
        if normalized.startswith(prefix.rstrip("/") + "/"):
            rel = normalized[len(prefix.rstrip("/")) + 1:]
        elif normalized.startswith(prefix.rstrip("/")):
            rel = normalized[len(prefix.rstrip("/")):].lstrip("/")
        else:
            rel = normalized.lstrip("/")
        if not safe_relpath(rel):
            return None
        return rel

    def _safe_join(self, rel: str) -> Optional[str]:
        candidate = os.path.realpath(os.path.join(self.root, rel))
        root = os.path.realpath(self.root)
        if candidate == root or candidate.startswith(root + os.sep):
            return candidate
        return None


def _json_or_none(value: Optional[str]) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return value
