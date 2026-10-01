"""Research KB unified library service.

Reads two read-only source archives (reports-fetcher SQLite archive and a
Discord export JSONL index), builds a unified catalog, renders deterministic
Obsidian Markdown into a vault, and serves metadata search plus versioned,
range-capable original-document access over a LAN HTTP API.
"""

__version__ = "0.1.0"
