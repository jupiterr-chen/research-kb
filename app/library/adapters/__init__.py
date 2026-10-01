"""Source adapters for the two read-only archives."""

from .base import BaseAdapter, ScanResult, SourceError, sha256_file

__all__ = ["BaseAdapter", "ScanResult", "SourceError", "sha256_file", "build_adapter"]


def build_adapter(source_config, existing_versions=None):
    from .discord import DiscordAdapter
    from .reports import ReportsAdapter

    mapping = {
        "reports_archive": ReportsAdapter,
        "discord_export": DiscordAdapter,
    }
    adapter_cls = mapping.get(source_config.type)
    if adapter_cls is None:
        raise SourceError("unknown source type: %s" % source_config.type)
    return adapter_cls(source_config, existing_versions=existing_versions)
