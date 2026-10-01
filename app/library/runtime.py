"""Runtime helpers: atomic JSON files, scheduler heartbeat and China-time.

The dashboard must show Asia/Shanghai local time explicitly. Asia/Shanghai has
been a fixed UTC+8 offset since 1991 and observes no DST, so a fixed offset is
used instead of depending on the optional ``tzdata`` database being present in
the slim container image.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

CHINA_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")
CHINA_TZ_LABEL = "Asia/Shanghai (UTC+8)"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def to_china(value: Optional[str]) -> Optional[datetime]:
    parsed = parse_iso(value)
    if parsed is None:
        return None
    return parsed.astimezone(CHINA_TZ)


def china_iso(value: Optional[str]) -> Optional[str]:
    converted = to_china(value)
    if converted is None:
        return None
    return converted.replace(microsecond=0).isoformat()


def china_display(value: Optional[str]) -> Optional[str]:
    converted = to_china(value)
    if converted is None:
        return None
    return converted.strftime("%Y-%m-%d %H:%M:%S")


def seconds_between(start: Optional[str], end: Optional[str]) -> Optional[float]:
    first, second = parse_iso(start), parse_iso(end)
    if first is None or second is None:
        return None
    return (second - first).total_seconds()


def write_json_atomic(path: str, data: Any) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    handle, tmp_path = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=directory or ".")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def read_json(path: str) -> Optional[Any]:
    try:
        with open(path, "r", encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, ValueError):
        return None


def scheduler_state_path(state_dir: str) -> str:
    return os.path.join(state_dir, "scheduler.json")


def read_scheduler_state(state_dir: str) -> Dict[str, Any]:
    data = read_json(scheduler_state_path(state_dir))
    return data if isinstance(data, dict) else {}
