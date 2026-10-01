"""Load and re-sanitize the read-only Syncthing snapshot.

The status collector (a separate, narrowly scoped compose service) is the only
component that talks to the Syncthing REST API and reads its config/key. It
writes a *sanitized* JSON snapshot; this module never sees the API key. As
defence in depth we strip anything that still looks credential-like and shorten
device identifiers before they can reach a public response.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, Optional

from .runtime import parse_iso, read_json, utc_now

SENSITIVE_KEY_RE = re.compile(r"(api[_-]?key|apikey|token|secret|password|passwd|credential)", re.I)
DEVICE_FIELD_RE = re.compile(r"(device_?id|my_?id|remote_?id|peer_?id)$", re.I)
# Absolute filesystem paths (Windows drive/UNC or POSIX) and long token-like
# values that could appear inside free-form upstream error strings.
_PATH_RE = re.compile(
    r"(?<!\w)(?:[A-Za-z]:[\\/]|\\\\)[^\s\"']*|(?<!\w)/[^\s\"']+"
)
_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-]{24,}")

DEFAULT_STALE_SECONDS = 90
ERROR_TEXT_LIMIT = 300


def redact_text(value: str, limit: int = ERROR_TEXT_LIMIT) -> str:
    """Redact filesystem paths and token-like values from free-form text."""
    text = _PATH_RE.sub("[path]", str(value))
    text = _TOKEN_RE.sub("[redacted]", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def short_device_id(value: Optional[str], keep: int = 7) -> Optional[str]:
    if not value:
        return None
    text = str(value)
    head = text.split("-", 1)[0]
    if len(head) >= keep:
        return head[:keep]
    return head or text[:keep]


def sanitize(value: Any, key: str = "") -> Any:
    """Recursively drop credential-like keys, shorten device ids and redact
    paths/token-like values inside free-form strings (e.g. upstream errors)."""
    if SENSITIVE_KEY_RE.search(key or ""):
        return None
    if isinstance(value, dict):
        cleaned: Dict[str, Any] = {}
        for child_key, child_value in value.items():
            if SENSITIVE_KEY_RE.search(str(child_key)):
                continue
            result = sanitize(child_value, str(child_key))
            if result is not None:
                cleaned[child_key] = result
        return cleaned
    if isinstance(value, list):
        return [sanitize(item, key) for item in value]
    if isinstance(value, str):
        if DEVICE_FIELD_RE.search(key or ""):
            return short_device_id(value)
        return redact_text(value)
    return value


def snapshot_path(config) -> str:
    monitor = getattr(config, "sync_monitor", None) or {}
    configured = monitor.get("snapshot_path")
    if configured:
        return str(configured)
    return os.path.join(config.state_dir, "monitor", "syncthing.json")


def stale_after(config) -> int:
    monitor = getattr(config, "sync_monitor", None) or {}
    try:
        return int(monitor.get("stale_after_seconds", DEFAULT_STALE_SECONDS))
    except (TypeError, ValueError):
        return DEFAULT_STALE_SECONDS


def load_snapshot(config) -> Dict[str, Any]:
    path = snapshot_path(config)
    raw = read_json(path)
    if not isinstance(raw, dict):
        return {"present": False, "path_configured": bool(path), "snapshot": None}
    cleaned = sanitize(raw)
    return {"present": True, "path_configured": True, "snapshot": cleaned}


def sample_age_seconds(snapshot: Optional[Dict[str, Any]], now_iso: Optional[str] = None) -> Optional[float]:
    if not snapshot:
        return None
    observed = parse_iso(snapshot.get("observed_at"))
    if observed is None:
        return None
    now = parse_iso(now_iso or utc_now())
    if now is None:
        return None
    return (now - observed).total_seconds()
