"""Honest, bounded change ledger for reconciliation events.

Only *semantic* document fields and version identities are compared. Observed
timestamps (``last_seen_at``/``observed_at``/``mtime_ns``) and fresh-hash flags
are deliberately ignored so that a no-op scan produces zero changes.

Event kinds
-----------
``ledger_baseline``    first run upgrades an existing catalog: existing rows are
                       seeded as the baseline and are *not* reported as imports.
``imported``           a document id not previously in the catalog.
``metadata_updated``   one or more stable metadata fields changed.
``new_version``        a version id not previously persisted for the document.
``current_changed``    the authoritative current version changed.
``became_unavailable`` availability or the current content state turned bad.
``recovered``          a document that was unavailable became available again.
``content_conflict``   a version's observed bytes disagree with its identity.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .models import STATE_CONFLICT, STATE_READY
from .runtime import utc_now

KIND_LEDGER_BASELINE = "ledger_baseline"
KIND_IMPORTED = "imported"
KIND_METADATA_UPDATED = "metadata_updated"
KIND_NEW_VERSION = "new_version"
KIND_CURRENT_CHANGED = "current_changed"
KIND_BECAME_UNAVAILABLE = "became_unavailable"
KIND_RECOVERED = "recovered"
KIND_CONTENT_CONFLICT = "content_conflict"

KIND_LABELS = {
    KIND_LEDGER_BASELINE: "变更记录基线",
    KIND_IMPORTED: "新编目文档",
    KIND_METADATA_UPDATED: "元数据更新",
    KIND_NEW_VERSION: "新增版本",
    KIND_CURRENT_CHANGED: "当前版本变更",
    KIND_BECAME_UNAVAILABLE: "转为不可用",
    KIND_RECOVERED: "恢复可用",
    KIND_CONTENT_CONFLICT: "内容冲突",
}

# Stable, semantic fields. Deliberately excludes last_seen_at/observed_*.
STABLE_DOC_FIELDS = (
    "title", "display_title", "summary", "author", "market", "symbol",
    "doc_type", "language", "report_period", "filing_date", "published_at",
    "report_date", "status", "source_url",
)


def _normalize(value: Any) -> Any:
    if value is None:
        return None
    text = str(value)
    return text if text != "" else None


def metadata_diff(old_row: Optional[Dict[str, Any]], doc) -> List[str]:
    """Return the list of stable fields whose value changed."""
    if old_row is None:
        return []
    changed: List[str] = []
    for field in STABLE_DOC_FIELDS:
        if _normalize(old_row.get(field)) != _normalize(getattr(doc, field, None)):
            changed.append(field)
    return changed


def build_event(source: str, doc_id: Optional[str], kind: str, title: Optional[str],
                detail: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "at": utc_now(),
        "source": source,
        "doc_id": doc_id,
        "kind": kind,
        "title": title,
        "detail": detail or {},
    }


def doc_events(
    source: str,
    doc,
    old_row: Optional[Dict[str, Any]],
    old_versions: Dict[str, Dict[str, Any]],
    effective: Dict[str, tuple],
    current_id: Optional[str],
    doc_available: bool,
) -> List[Dict[str, Any]]:
    """Compute the change events for one document in one reconciliation."""
    title = doc.display_title or doc.title
    events: List[Dict[str, Any]] = []

    changed = metadata_diff(old_row, doc)
    if changed:
        events.append(build_event(source, doc.doc_id, KIND_METADATA_UPDATED, title,
                                  {"fields": changed}))

    new_version_ids = [vid for vid in effective if vid not in old_versions]
    if new_version_ids:
        events.append(build_event(source, doc.doc_id, KIND_NEW_VERSION, title,
                                  {"count": len(new_version_ids),
                                   "version_ids": new_version_ids[:5]}))

    old_current = [vid for vid, row in old_versions.items() if row.get("is_current")]
    if old_current and current_id and old_current[0] != current_id:
        events.append(build_event(source, doc.doc_id, KIND_CURRENT_CHANGED, title,
                                  {"from": old_current[0], "to": current_id}))

    if old_row is not None:
        old_available = bool(old_row.get("available"))
        if old_available and not doc_available:
            current_state = effective.get(current_id, (None, None, None))[2] if current_id else None
            events.append(build_event(source, doc.doc_id, KIND_BECAME_UNAVAILABLE, title,
                                      {"current_state": current_state}))
        elif not old_available and doc_available:
            events.append(build_event(source, doc.doc_id, KIND_RECOVERED, title, {}))

    for version_id, (_, _, state) in effective.items():
        old_state = (old_versions.get(version_id) or {}).get("state")
        if state == STATE_CONFLICT and old_state != STATE_CONFLICT:
            events.append(build_event(source, doc.doc_id, KIND_CONTENT_CONFLICT, title,
                                      {"version_id": version_id}))
            break

    return events
