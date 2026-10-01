"""HTTP API: metadata search, document detail, safe versioned file streaming."""

from __future__ import annotations

import json
import os
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, unquote, urlparse

from .catalog import Catalog
from .config import Config
from .dashboard import DASHBOARD_CSP, DASHBOARD_CSS, DASHBOARD_HTML, DASHBOARD_JS
from .fileserve import (
    ContentUnavailable,
    PathNotAllowed,
    content_disposition,
    guess_content_type,
    open_validated,
    parse_range,
)
from .status import build_status


CHUNK = 256 * 1024


class Handler(BaseHTTPRequestHandler):
    config: Config = None  # type: ignore[assignment]
    catalog: Catalog = None  # type: ignore[assignment]
    server_version = "ResearchKB/0.1"

    # ---------------------------------------------------------------- helpers
    def _send_json(self, status: int, payload: Dict[str, Any], head_only: bool = False) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def _error(self, status: int, message: str) -> None:
        self._send_json(status, {"error": message})

    def _parts(self):
        return [unquote(p) for p in urlparse(self.path).path.split("/") if p]

    # ------------------------------------------------------------------ verbs
    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch(head_only=True)

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch(head_only=False)

    def _dispatch(self, head_only: bool) -> None:
        try:
            parts = self._parts()
            query = parse_qs(urlparse(self.path).query)
            if not parts:
                self._root(head_only)
            elif parts[0] == "healthz":
                self._healthz(head_only)
            elif parts[0] == "assets" and len(parts) == 2:
                self._asset(parts[1], head_only)
            elif len(parts) >= 2 and parts[:2] == ["api", "v1"]:
                self._api(parts[2:], query, head_only)
            else:
                self._error(404, "not found")
        except BrokenPipeError:
            pass
        except Exception:
            self._log_exception()
            self._error(500, "internal error")

    def _log_exception(self) -> None:
        try:
            os.makedirs(self.config.state_dir, exist_ok=True)
            with open(os.path.join(self.config.state_dir, "api-errors.log"), "a", encoding="utf-8") as handle:
                handle.write(traceback.format_exc())
                handle.write("\n")
        except Exception:
            pass

    # ----------------------------------------------------------------- routes
    def _root(self, head_only: bool) -> None:
        body = DASHBOARD_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy", DASHBOARD_CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def _asset(self, name: str, head_only: bool) -> None:
        assets = {
            "dashboard.css": ("text/css; charset=utf-8", DASHBOARD_CSS),
            "dashboard.js": ("application/javascript; charset=utf-8", DASHBOARD_JS),
        }
        asset = assets.get(name)
        if asset is None:
            return self._error(404, "not found")
        content_type, text = asset
        body = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def _source_health(self, name: str) -> Dict[str, Any]:
        state = self.catalog.source_state(name) or {}
        last_error = state.get("last_error")  # already sanitized at ingestion time
        healthy = bool(state.get("last_ok_at")) and not last_error
        return {
            "healthy": healthy,
            "last_ok_at": state.get("last_ok_at"),
            "last_error": last_error,
            "counts": state.get("counts"),
        }

    def _healthz(self, head_only: bool) -> None:
        counts = self.catalog.counts()
        sources = {name: self._source_health(name) for name in self.config.sources}
        runs = self.catalog.recent_runs(1)
        last_run = runs[0] if runs else None
        ingest_ok = bool(last_run and last_run.get("ok"))
        all_healthy = all(item["healthy"] for item in sources.values()) if sources else False
        payload = {
            "status": "ok" if (all_healthy and ingest_ok) else "degraded",
            "listener": "ok",
            "ingest_ok": ingest_ok,
            "counts": counts,
            "sources": sources,
            "last_ingest": last_run,
        }
        self._send_json(200, payload, head_only)

    def _api(self, parts, query, head_only: bool) -> None:
        if parts and parts[0] == "search":
            self._search(query, head_only)
        elif parts and parts[0] == "sources":
            self._sources(head_only)
        elif parts and parts[0] == "status":
            self._status(head_only)
        elif len(parts) >= 3 and parts[0] == "documents":
            self._document(parts[1], "/".join(parts[2:]), head_only)
        elif len(parts) >= 3 and parts[0] == "files":
            self._file(parts[1], "/".join(parts[2:]), query, head_only)
        elif len(parts) >= 3 and parts[0] == "versions":
            self._document(parts[1], "/".join(parts[2:]), head_only)
        else:
            self._error(404, "not found")

    def _sources(self, head_only: bool) -> None:
        payload = {"sources": []}
        for name in sorted(self.config.sources):
            health = self._source_health(name)
            payload["sources"].append({
                "name": name,
                "type": self.config.sources[name].type,
                "healthy": health["healthy"],
                "last_ok_at": health["last_ok_at"],
                "last_error": health["last_error"],
                "counts": health["counts"],
            })
        self._send_json(200, payload, head_only)

    def _status(self, head_only: bool) -> None:
        self._send_json(200, build_status(self.config, self.catalog), head_only)

    def _search(self, query, head_only: bool) -> None:
        def first(key, default=None):
            values = query.get(key)
            return values[0] if values else default

        try:
            page = int(first("page", "1") or "1")
            page_size = int(first("page_size", "20") or "20")
        except ValueError:
            return self._error(400, "invalid pagination")
        result = self.catalog.search(
            source=first("source"),
            market=first("market"),
            symbol=first("symbol"),
            doc_type=first("doc_type"),
            query=first("q"),
            date_from=first("date_from"),
            date_to=first("date_to"),
            available_only=(first("available_only", "1") != "0"),
            page=page,
            page_size=page_size,
        )
        payload = {
            "kind": "metadata_search",
            "notice": "Metadata only: no PDF full-text or semantic search in this version.",
            "query": {k: v[0] for k, v in query.items() if v},
            "total": result["total"],
            "page": result["page"],
            "page_size": result["page_size"],
            "page_count": result["page_count"],
            "items": result["items"],
        }
        self._send_json(200, payload, head_only)

    def _document(self, source: str, doc_id: str, head_only: bool) -> None:
        if source not in self.config.sources:
            return self._error(404, "not found")
        doc = self.catalog.get_document(source, doc_id)
        if not doc:
            return self._error(404, "not found")
        versions = []
        for version in doc.versions:
            versions.append({
                "version_id": version.version_id,
                "sha256": version.sha256,
                "bytes": version.bytes,
                "media_type": version.media_type,
                "ext": version.ext,
                "is_current": version.is_current,
                "state": version.state,
                "content_changed_at": version.content_changed_at,
                "file_url": "%s/api/v1/files/%s/%s?version=%s" % (
                    self.config.public_base_url, source, doc_id, version.version_id),
            })
        self._send_json(200, {
            "source": doc.source,
            "doc_id": doc.doc_id,
            "title": doc.title,
            "display_title": doc.display_title,
            "summary": doc.summary,
            "author": doc.author,
            "market": doc.market,
            "symbol": doc.symbol,
            "doc_type": doc.doc_type,
            "language": doc.language,
            "report_period": doc.report_period,
            "filing_date": doc.filing_date,
            "published_at": doc.published_at,
            "report_date": doc.report_date,
            "status": doc.status,
            "available": doc.available,
            "source_url": doc.source_url,
            "metadata": doc.metadata,
            "versions": versions,
            "self_url": "%s/api/v1/documents/%s/%s" % (self.config.public_base_url, source, doc_id),
        }, head_only)

    def _file(self, source: str, doc_id: str, query, head_only: bool) -> None:
        if source not in self.config.sources:
            return self._error(404, "not found")
        doc = self.catalog.get_document(source, doc_id)
        if not doc:
            return self._error(404, "not found")
        version_id = query.get("version", [None])[0]
        version = self.catalog.get_version(source, doc_id, version_id)
        if not version:
            return self._error(404, "version not found or no authoritative current version")
        validated = None
        try:
            validated = open_validated(self.config, source, version)
        except PathNotAllowed:
            return self._error(404, "file not available")
        except ContentUnavailable as exc:
            return self._error(409, "content unavailable: %s" % exc)
        try:
            size = validated.size
            etag = '"%s"' % (version.sha256 or version.version_id)
            download = query.get("download", ["0"])[0] == "1"
            filename = (doc.metadata or {}).get("file_name_local") or os.path.basename(validated.path)
            content_type = guess_content_type(version, validated.path)

            if self.headers.get("If-None-Match") == etag:
                self.send_response(304)
                self.send_header("ETag", etag)
                self.end_headers()
                return

            range_header = self.headers.get("Range")
            kind, start, end = parse_range(range_header, size)
            if kind == "invalid":
                self.send_response(416)
                self.send_header("Content-Range", "bytes */%d" % size)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

            common = [
                ("Content-Type", content_type),
                ("ETag", etag),
                ("Accept-Ranges", "bytes"),
                ("X-Content-Type-Options", "nosniff"),
                ("Content-Disposition", content_disposition(filename, inline=not download)),
            ]
            if content_type.startswith("text/html"):
                common.append((
                    "Content-Security-Policy",
                    "sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'",
                ))
            if kind == "range":
                length = end - start + 1
                self.send_response(206)
                for key, value in common:
                    self.send_header(key, value)
                self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
                self.send_header("Content-Length", str(length))
                self.end_headers()
                if not head_only:
                    self._stream(validated.handle, start, length)
            else:
                self.send_response(200)
                for key, value in common:
                    self.send_header(key, value)
                self.send_header("Content-Length", str(size))
                self.end_headers()
                if not head_only:
                    self._stream(validated.handle, 0, size)
        finally:
            validated.close()

    def _stream(self, handle, offset: int, length: int) -> None:
        remaining = length
        handle.seek(offset)
        while remaining > 0:
            block = handle.read(min(CHUNK, remaining))
            if not block:
                break
            self.wfile.write(block)
            remaining -= len(block)

    def log_message(self, fmt: str, *args) -> None:  # keep docker logs concise
        return


def build_server(config: Config, catalog: Optional[Catalog] = None) -> ThreadingHTTPServer:
    catalog = catalog or Catalog(config.catalog_db)
    handler = type("BoundHandler", (Handler,), {"config": config, "catalog": catalog})
    server = ThreadingHTTPServer((config.bind_host, config.bind_port), handler)
    server.daemon_threads = True
    return server
