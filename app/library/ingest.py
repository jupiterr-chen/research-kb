"""Ingestion orchestration: scan adapters, commit per source, render vault.

All ingest/render write paths are guarded by a task-scoped cross-process file
lock so a manual run cannot race the scheduled run. Source failures are
sandboxed per source and only a sanitized summary is published via the API;
full traces stay in the private state log.
"""

from __future__ import annotations

import json
import os
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .adapters import build_adapter
from .catalog import Catalog, utc_now
from .config import Config
from .locking import FileLock
from .markdown import render_vault


class Ingestor:
    def __init__(self, config: Config, catalog: Optional[Catalog] = None):
        self.config = config
        self.catalog = catalog or Catalog(config.catalog_db)
        self._owns_catalog = catalog is None

    def lock_path(self) -> str:
        return os.path.join(self.config.state_dir, "ingest.lock")

    def _log(self, message: str) -> None:
        os.makedirs(self.config.state_dir, exist_ok=True)
        path = os.path.join(self.config.state_dir, "ingest.log")
        stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("%s %s\n" % (stamp, message))

    def run(self) -> Dict[str, Any]:
        lock = FileLock(self.lock_path())
        if not lock.acquire(blocking=False):
            return {"ok": False, "skipped": True, "reason": "another ingest/render holds the task lock"}
        started = utc_now()
        summary: Dict[str, Any] = {"ok": True, "sources": {}, "rendered": None}
        error_text = None
        try:
            for source, source_config in self.config.sources.items():
                try:
                    existing = self.catalog.existing_version_map(source)
                    adapter = build_adapter(source_config, existing_versions=existing)
                    self.catalog.mark_source_attempt(source)
                    scan = adapter.scan()
                    commit = self.catalog.commit_snapshot(
                        source, scan.documents, scan.snapshot, scan.counts
                    )
                    summary["sources"][source] = {
                        "ok": True, "documents": len(scan.documents),
                        "counts": scan.counts, "changes": commit.get("changes", 0),
                    }
                    self._log("source %s ok documents=%d changes=%d counts=%s" % (
                        source, len(scan.documents), commit.get("changes", 0),
                        json.dumps(scan.counts, ensure_ascii=False)))
                except Exception as exc:  # isolated per-source failure
                    sanitized = "%s: source scan failed" % type(exc).__name__
                    self.catalog.record_source_error(source, sanitized)
                    summary["sources"][source] = {"ok": False, "error": sanitized}
                    summary["ok"] = False
                    error_text = error_text or sanitized
                    self._log("source %s ERROR %s" % (source, sanitized))
                    self._log(traceback.format_exc())
            try:
                rendered = render_vault(self.config, self.catalog)
                summary["rendered"] = rendered
                self.catalog.record_render_result(True, None, rendered)
                self._log("render ok %s" % json.dumps(rendered, ensure_ascii=False))
            except Exception as exc:
                sanitized = "%s: render failed" % type(exc).__name__
                summary["ok"] = False
                error_text = error_text or sanitized
                self.catalog.record_render_result(False, sanitized, None)
                self._log("render ERROR %s" % sanitized)
                self._log(traceback.format_exc())
        finally:
            counts = {k: v.get("counts", {}) if isinstance(v, dict) else {} for k, v in summary["sources"].items()}
            total_changes = sum(
                int(v.get("changes", 0)) for v in summary["sources"].values() if isinstance(v, dict)
            )
            self.catalog.record_run(started, summary["ok"], counts, error_text, changes=total_changes)
            lock.release()
        return summary

    def close(self) -> None:
        if self._owns_catalog:
            self.catalog.close()
