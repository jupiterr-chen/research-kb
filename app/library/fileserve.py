"""Safe versioned file resolution and HTTP range helpers."""

from __future__ import annotations

import mimetypes
import os
import re
from typing import Optional, Tuple
from urllib.parse import quote

from .config import Config
from .models import Version
from .textutil import ascii_fold, safe_filename

EXTRA_MIME = {
    "pdf": "application/pdf",
    "html": "text/html; charset=utf-8",
    "htm": "text/html; charset=utf-8",
    "txt": "text/plain; charset=utf-8",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "csv": "text/csv; charset=utf-8",
    "json": "application/json; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "zip": "application/zip",
}


class PathNotAllowed(Exception):
    pass


class ContentUnavailable(Exception):
    """Raised when a version's bytes can no longer be trusted/served."""


class ValidatedFile:
    def __init__(self, handle, size: int, mtime_ns: int, path: str):
        self.handle = handle
        self.size = size
        self.mtime_ns = mtime_ns
        self.path = path

    def close(self) -> None:
        try:
            self.handle.close()
        except Exception:
            pass


def open_validated(config: Config, source: str, version: Version) -> ValidatedFile:
    """Open the backing file and verify its bytes against the persisted identity.

    The expected content identity is the immutable SHA256 stored for the version.
    We SHA256 the *same open descriptor* and rewind before streaming, so a
    timestamp-preserving replacement, in-place edit or path swap cannot make us
    serve bytes that do not match the version id/ETag. Any mismatch fails closed.
    """
    import hashlib

    path = resolve_version_path(config, source, version)
    if version.state != "ready":
        raise ContentUnavailable("version content is %s" % version.state)
    expected = version.sha256 or version.observed_sha256
    if not expected:
        raise ContentUnavailable("no expected content identity")
    handle = open(path, "rb")
    try:
        before = os.fstat(handle.fileno())
        digest = hashlib.sha256()
        while True:
            block = handle.read(1 << 20)
            if not block:
                break
            digest.update(block)
        after = os.fstat(handle.fileno())
        if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
            raise ContentUnavailable("content changed during verification")
        if digest.hexdigest() != expected:
            raise ContentUnavailable("content hash mismatch")
        handle.seek(0)
    except ContentUnavailable:
        handle.close()
        raise
    except OSError:
        handle.close()
        raise ContentUnavailable("file not readable")
    return ValidatedFile(handle, before.st_size, before.st_mtime_ns, path)


def resolve_version_path(config: Config, source: str, version: Version) -> str:
    """Resolve a catalog version to a real path contained in the source root.

    The catalog is the only source of allowed relative paths; the resolved path
    (following symlinks) must stay inside the read-only source root.
    """
    source_config = config.sources.get(source)
    if source_config is None:
        raise PathNotAllowed("unknown source")
    root = os.path.realpath(source_config.root)
    rel = version.rel_path
    if not rel:
        raise PathNotAllowed("version has no path")
    normalized = rel.replace("\\", "/")
    if normalized.startswith("/") or os.path.isabs(rel):
        raise PathNotAllowed("absolute path rejected")
    if os.path.splitdrive(rel)[0]:
        raise PathNotAllowed("drive path rejected")
    if ".." in normalized.split("/"):
        raise PathNotAllowed("parent traversal rejected")
    candidate = os.path.realpath(os.path.join(root, normalized))
    if candidate != root and not candidate.startswith(root + os.sep):
        raise PathNotAllowed("path escapes source root")
    if not os.path.isfile(candidate):
        raise PathNotAllowed("file not available")
    return candidate


def guess_content_type(version: Version, path: str) -> str:
    if version.media_type:
        if version.media_type.startswith("text/") and "charset" not in version.media_type:
            return version.media_type + "; charset=utf-8"
        return version.media_type
    ext = (version.ext or os.path.splitext(path)[1].lstrip(".")).lower()
    if ext in EXTRA_MIME:
        return EXTRA_MIME[ext]
    guessed, _ = mimetypes.guess_type(path)
    return guessed or "application/octet-stream"


def content_disposition(filename: str, inline: bool) -> str:
    # The plain filename= parameter must stay ASCII (RFC 6266); the real UTF-8
    # name is carried by filename*=UTF-8''... which every modern client prefers.
    ascii_name = safe_filename(ascii_fold(filename)).strip() or "document"
    ascii_name = re.sub(r'[^A-Za-z0-9._ -]', "_", ascii_name) or "document"
    disposition = "inline" if inline else "attachment"
    encoded = quote(filename, safe="")
    return "%s; filename=\"%s\"; filename*=UTF-8''%s" % (disposition, ascii_name, encoded)


def parse_range(header: Optional[str], size: int) -> Tuple[str, Optional[int], Optional[int]]:
    """Return (kind, start, end). kind is 'full', 'range' or 'invalid'."""
    if not header:
        return "full", None, None
    header = header.strip()
    if not header.startswith("bytes="):
        return "invalid", None, None
    spec = header[len("bytes="):]
    if "," in spec:
        return "invalid", None, None
    if "-" not in spec:
        return "invalid", None, None
    start_text, _, end_text = spec.partition("-")
    try:
        if start_text == "":
            if end_text == "":
                return "invalid", None, None
            suffix = int(end_text)
            if suffix <= 0:
                return "invalid", None, None
            start = max(0, size - suffix)
            end = size - 1
        else:
            start = int(start_text)
            end = int(end_text) if end_text else size - 1
    except ValueError:
        return "invalid", None, None
    if start < 0 or start >= size:
        return "invalid", None, None
    if end < start:
        return "invalid", None, None
    end = min(end, size - 1)
    return "range", start, end
