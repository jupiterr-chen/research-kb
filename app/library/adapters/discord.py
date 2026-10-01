"""Adapter for the discord_export read-only JSONL index.

The JSONL index is authoritative for identity (doc_id + declared sha256). We
re-hash the attachment whenever the on-disk size *or* mtime differs from the
last observation, so a same-size content replacement is detected instead of
being served under an old identity. Per-request serving revalidates size/mtime
against what was recorded at ingestion and fails closed on any change.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from ..models import STATE_CONFLICT, STATE_MISSING, STATE_READY, Document, Version, safe_relpath
from ..textutil import display_title
from .base import BaseAdapter, ScanResult, SourceError, hash_file_stable


class DiscordAdapter(BaseAdapter):
    source_name = "discord"

    def _index_path(self) -> str:
        return self.config.extra.get("index") or os.path.join(self.root, "index", "documents.jsonl")

    def _meta_path(self) -> str:
        return self.config.extra.get("meta") or os.path.join(self.root, "index", "meta.json")

    def scan(self) -> ScanResult:
        index_path = self._index_path()
        if not os.path.exists(index_path):
            raise SourceError("discord documents index not found")
        records: List[Dict[str, Any]] = []
        with open(index_path, "r", encoding="utf-8") as handle:
            for lineno, raw in enumerate(handle, start=1):
                line = raw.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except ValueError as exc:
                    raise SourceError("invalid JSONL at line %d: %s" % (lineno, type(exc).__name__))

        meta = {}
        meta_path = self._meta_path()
        if os.path.exists(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as handle:
                    meta = json.load(handle)
            except ValueError as exc:
                raise SourceError("invalid discord meta.json: %s" % type(exc).__name__)

        documents: List[Document] = []
        verified = 0
        hashed_now = 0
        from_cache = 0
        unstable_count = 0
        missing = 0
        conflicts = 0
        ext_counts: Dict[str, int] = {}
        total_bytes = 0
        seen_doc_ids = set()
        for record in records:
            doc_id = str(record.get("doc_id") or "").strip()
            if not doc_id:
                raise SourceError("discord record without doc_id")
            if doc_id in seen_doc_ids:
                raise SourceError("duplicate discord doc_id: %s" % doc_id)
            seen_doc_ids.add(doc_id)
            rel_path = record.get("file_path")
            declared_sha = record.get("sha256")
            version_id = str(declared_sha or "")
            ext = (record.get("file_ext") or "").lower().lstrip(".")
            ext_counts[ext or "?"] = ext_counts.get(ext or "?", 0) + 1
            size_declared = record.get("file_size") or record.get("file_size_disk")

            abs_path = None
            if rel_path and safe_relpath(rel_path):
                abs_path = self._safe_join(rel_path)
            exists, size_actual = (False, -1)
            mtime_ns = None
            if abs_path:
                try:
                    st = os.stat(abs_path)
                    exists, size_actual, mtime_ns = True, st.st_size, st.st_mtime_ns
                except OSError:
                    exists = False
            file_missing = bool(record.get("file_missing")) or not exists
            if file_missing:
                missing += 1

            observed_sha = None
            fresh = False
            stable = True
            if not file_missing:
                # Always hash fresh: equal-size / preserved-mtime replacements are
                # normal file operations and size+mtime is not content identity.
                observed_sha, size_actual, mtime_ns, stable = hash_file_stable(abs_path)
                if observed_sha is None:
                    file_missing = True
                    missing += 1
                else:
                    fresh = True
                    hashed_now += 1
                    if not stable:
                        unstable_count += 1

            if observed_sha:
                verified += 1
                if not version_id:
                    version_id = observed_sha

            state = STATE_READY
            if file_missing:
                state = STATE_MISSING
            elif not stable:
                state = STATE_CONFLICT
                conflicts += 1
            elif observed_sha and version_id and observed_sha != version_id:
                state = STATE_CONFLICT
                conflicts += 1

            final_version_id = version_id or None
            media_type = record.get("content_type") or None
            versions: List[Version] = []
            if final_version_id:
                expected = declared_sha or (observed_sha if state == STATE_READY else None)
                versions.append(
                    Version(
                        version_id=final_version_id,
                        sha256=expected,
                        bytes=size_declared,
                        media_type=media_type,
                        rel_path=rel_path,
                        ext=ext or None,
                        # The index identifies a single authoritative version per
                        # document; state (ready/missing/conflict) governs serving.
                        is_current=True,
                        state=state,
                        source_url=None,
                        content_changed_at=record.get("last_seen_at"),
                        observed_sha256=observed_sha,
                        observed_bytes=size_actual if observed_sha else None,
                        mtime_ns=mtime_ns if observed_sha else None,
                    )
                )
                if state == STATE_READY and size_actual >= 0:
                    total_bytes += size_actual

            metadata = {
                "channel_id": record.get("channel_id"),
                "channel_name": record.get("channel_name"),
                "message_id": record.get("message_id"),
                "attachment_index": record.get("attachment_index"),
                "file_name_local": record.get("file_name_local"),
                "hash_source": record.get("hash_source"),
                "first_seen_at": record.get("first_seen_at"),
                "last_seen_at": record.get("last_seen_at"),
                "summary_kind": "source_message_summary",
                "declared_sha256": declared_sha,
                "hash_verified": bool(observed_sha and observed_sha == declared_sha),
                "hash_checked_fresh": fresh,
                "content_state": state,
                "file_missing": file_missing,
            }
            documents.append(
                Document(
                    source=self.source_name,
                    doc_id=doc_id,
                    title=record.get("title"),
                    display_title=display_title(record.get("title")),
                    summary=record.get("summary"),
                    author=record.get("author"),
                    market=None,
                    symbol=None,
                    doc_type=ext or None,
                    language=None,
                    report_period=None,
                    filing_date=None,
                    published_at=record.get("published_at"),
                    report_date=record.get("report_date"),
                    status="missing" if file_missing else ("conflict" if state == STATE_CONFLICT else "done"),
                    available=(state == STATE_READY),
                    source_url=None,
                    metadata=metadata,
                    versions=versions,
                )
            )

        snapshot = {
            "index_path": index_path,
            "meta": meta,
            "index_count": len(records),
            "hashed_now": hashed_now,
        }
        counts = {
            "documents": len(documents),
            "available": sum(1 for d in documents if d.available),
            "versions_seen": sum(len(d.versions) for d in documents),
            "hash_verified": verified,
            "hash_hashed_now": hashed_now,
            "hash_from_cache": from_cache,
            "unstable": unstable_count,
            "conflicts": conflicts,
            "missing": missing,
            "ext_counts": ext_counts,
            "bytes_available": total_bytes,
            "index_count": len(records),
        }
        return ScanResult(documents=documents, snapshot=snapshot, counts=counts)

    def _safe_join(self, rel: str) -> Optional[str]:
        candidate = os.path.realpath(os.path.join(self.root, rel))
        root = os.path.realpath(self.root)
        if candidate == root or candidate.startswith(root + os.sep):
            return candidate
        return None
