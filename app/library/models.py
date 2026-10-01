"""Unified document/version model shared by adapters, catalog and renderers."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

VALID_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

# Version content state
STATE_READY = "ready"
STATE_MISSING = "missing"
STATE_CONFLICT = "conflict"


@dataclass
class Version:
    """A content version.

    ``version_id`` and ``sha256`` are the immutable *expected* content identity
    taken from the read-only source index. ``observed_sha256`` is what was
    actually hashed at ingestion time. A version is only servable while
    ``state == 'ready'`` (expected == observed) and the file still matches the
    observed size/mtime.
    """

    version_id: str
    sha256: Optional[str] = None
    bytes: Optional[int] = None
    media_type: Optional[str] = None
    rel_path: Optional[str] = None
    ext: Optional[str] = None
    is_current: bool = False
    state: str = STATE_READY
    source_url: Optional[str] = None
    content_changed_at: Optional[str] = None
    observed_sha256: Optional[str] = None
    observed_bytes: Optional[int] = None
    mtime_ns: Optional[int] = None

    def to_row(self) -> Dict[str, Any]:
        return {
            "version_id": self.version_id,
            "sha256": self.sha256,
            "bytes": self.bytes,
            "media_type": self.media_type,
            "rel_path": self.rel_path,
            "ext": self.ext,
            "is_current": 1 if self.is_current else 0,
            "state": self.state,
            "source_url": self.source_url,
            "content_changed_at": self.content_changed_at,
            "observed_sha256": self.observed_sha256,
            "observed_bytes": self.observed_bytes,
            "mtime_ns": self.mtime_ns,
        }


@dataclass
class Document:
    source: str
    doc_id: str
    title: Optional[str] = None
    display_title: Optional[str] = None
    summary: Optional[str] = None
    author: Optional[str] = None
    market: Optional[str] = None
    symbol: Optional[str] = None
    doc_type: Optional[str] = None
    language: Optional[str] = None
    report_period: Optional[str] = None
    filing_date: Optional[str] = None
    published_at: Optional[str] = None
    report_date: Optional[str] = None
    status: Optional[str] = None
    available: bool = False
    source_url: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    versions: List[Version] = field(default_factory=list)

    def current_version(self) -> Optional[Version]:
        current = [v for v in self.versions if v.is_current]
        if len(current) != 1:
            return None
        return current[0]

    def effective_date(self) -> Optional[str]:
        for value in (self.report_date, self.published_at, self.filing_date):
            if value:
                return value[:10]
        return None


def is_valid_id(value: str) -> bool:
    return bool(value) and bool(VALID_ID_RE.match(value)) and value not in (".", "..")


def safe_relpath(rel_path: str) -> bool:
    """True when a source-relative path is a plain, non-escaping relative path."""
    if not rel_path:
        return False
    normalized = rel_path.replace("\\", "/")
    if normalized.startswith("/") or os.path.isabs(rel_path):
        return False
    drive, _ = os.path.splitdrive(rel_path)
    if drive:
        return False
    parts = [p for p in normalized.split("/") if p not in ("", ".")]
    if any(part == ".." for part in parts):
        return False
    return True
