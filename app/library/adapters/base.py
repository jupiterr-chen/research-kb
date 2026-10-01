"""Adapter base types and shared helpers."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from ..models import Document


class SourceError(Exception):
    """Raised when a source snapshot cannot be read completely/validly."""


@dataclass
class ScanResult:
    documents: List[Document]
    snapshot: Dict[str, Any]
    counts: Dict[str, Any]


def sha256_file(path: str, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(chunk_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def file_stat(path: str) -> Tuple[bool, int]:
    try:
        return True, os.path.getsize(path)
    except OSError:
        return False, -1


def hash_file_stable(path: str, attempts: int = 3):
    """Hash a file and confirm it did not change while being read.

    Returns ``(sha256_or_None, size, mtime_ns, stable)``. A concurrent in-place
    write or swap makes the before/after stat disagree and marks the sample
    unstable so callers fail closed rather than blessing torn content.
    """
    size = -1
    mtime_ns = None
    digest = None
    for _ in range(attempts):
        try:
            before = os.stat(path)
            digest = sha256_file(path)
            after = os.stat(path)
        except OSError:
            return None, -1, None, False
        size, mtime_ns = after.st_size, after.st_mtime_ns
        if before.st_size == after.st_size and before.st_mtime_ns == after.st_mtime_ns:
            return digest, size, mtime_ns, True
    return digest, size, mtime_ns, False


class BaseAdapter:
    source_name = "base"

    def __init__(self, source_config, existing_versions: Dict[Tuple[str, str], Dict[str, Any]] | None = None):
        self.config = source_config
        self.root = os.path.realpath(source_config.root)
        self.existing_versions = existing_versions or {}

    def scan(self) -> ScanResult:  # pragma: no cover - interface
        raise NotImplementedError
