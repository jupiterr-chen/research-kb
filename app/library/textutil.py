"""Text helpers: deterministic Markdown-safe escaping and filename handling."""

from __future__ import annotations

import re
import unicodedata
from typing import Optional
from urllib.parse import unquote

_UNSAFE_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_HTML_ESCAPES = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}


def html_escape(value: Optional[str]) -> str:
    if not value:
        return ""
    return "".join(_HTML_ESCAPES.get(ch, ch) for ch in str(value))


def safe_filename(name: str, maxlen: int = 120) -> str:
    cleaned = _UNSAFE_FILENAME.sub("_", name or "").strip(" .")
    if not cleaned:
        cleaned = "unnamed"
    return cleaned[:maxlen]


_YAML_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
    "\x00": "\\0",
}


def yaml_scalar(value) -> str:
    """Render a scalar as a double-quoted, control-char-safe YAML string."""
    if value is None:
        return '""'
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    out = []
    for ch in text:
        if ch in _YAML_ESCAPES:
            out.append(_YAML_ESCAPES[ch])
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append("\\x%02x" % ord(ch))
        else:
            out.append(ch)
    return '"%s"' % "".join(out)


_MD_SPECIAL = re.compile(r'([\\`*_{}\[\]()#+.!|<>&~])')


def md_text(value, block: bool = False) -> str:
    """Render an untrusted source string as literal Markdown text.

    HTML-significant characters are entity-escaped and Markdown punctuation is
    backslash-escaped so source titles/summaries cannot inject markup or links.
    Newlines in block context are preserved as line breaks.
    """
    if value is None:
        return ""
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    escaped = _MD_SPECIAL.sub(r"\\\1", text)
    if not block:
        escaped = escaped.replace("\n", " ")
    return escaped


def safe_url(url) -> Optional[str]:
    """Return the URL only if it uses a safe scheme (http/https), else None."""
    if not url:
        return None
    text = str(url).strip()
    lowered = text.lower()
    if lowered.startswith("http://") or lowered.startswith("https://"):
        return text
    return None


def display_title(title: Optional[str]) -> str:
    """Return a friendly display title, decoding percent-encoding when present."""
    if not title:
        return ""
    candidate = title.strip()
    if "%" in candidate:
        try:
            decoded = unquote(candidate)
        except Exception:
            decoded = candidate
        if decoded and decoded != candidate and "%" not in decoded:
            return decoded
    return candidate


def normalize_symbol(value: Optional[str]) -> str:
    return (value or "").strip().upper()


def ascii_fold(value: str) -> str:
    return unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")


def human_size(num_bytes: Optional[int]) -> str:
    if num_bytes is None:
        return "unknown"
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            if unit == "B":
                return "%d B" % int(size)
            return "%.1f %s" % (size, unit)
        size /= 1024
    return "%.1f TB" % size
