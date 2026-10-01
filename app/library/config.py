"""Configuration loading for the Research KB library service."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict


class ConfigError(Exception):
    pass


@dataclass
class SourceConfig:
    type: str
    root: str
    extra: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, name: str, data: Dict[str, Any]) -> "SourceConfig":
        if "type" not in data:
            raise ConfigError("source %r missing 'type'" % name)
        if "root" not in data:
            raise ConfigError("source %r missing 'root'" % name)
        extra = {k: v for k, v in data.items() if k not in ("type", "root")}
        return cls(type=str(data["type"]), root=str(data["root"]), extra=extra)


@dataclass
class Config:
    bind_host: str = "127.0.0.1"
    bind_port: int = 8765
    catalog_db: str = "catalog/catalog.sqlite3"
    vault_dir: str = "vault"
    state_dir: str = "state"
    public_base_url: str = "http://localhost:8765"
    ingest_interval_seconds: int = 3600
    sources: Dict[str, SourceConfig] = field(default_factory=dict)
    human_dirs: Dict[str, str] = field(default_factory=dict)
    dispatch_date_field: str = "coalesce"
    sync_monitor: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Config":
        raw_sources = data.get("sources") or {}
        if not isinstance(raw_sources, dict) or not raw_sources:
            raise ConfigError("config must define a non-empty 'sources' object")
        sources = {name: SourceConfig.from_dict(name, value) for name, value in raw_sources.items()}
        human_dirs = data.get("human_dirs") or {}
        if not isinstance(human_dirs, dict):
            raise ConfigError("'human_dirs' must be an object mapping dir -> description")
        sync_monitor = data.get("sync_monitor") or {}
        if not isinstance(sync_monitor, dict):
            raise ConfigError("'sync_monitor' must be an object when provided")
        return cls(
            bind_host=str(data.get("bind_host", "127.0.0.1")),
            bind_port=int(data.get("bind_port", 8765)),
            catalog_db=str(data.get("catalog_db", "catalog/catalog.sqlite3")),
            vault_dir=str(data.get("vault_dir", "vault")),
            state_dir=str(data.get("state_dir", "state")),
            public_base_url=str(data.get("public_base_url", "http://localhost:8765")).rstrip("/"),
            ingest_interval_seconds=int(data.get("ingest_interval_seconds", 3600)),
            sources=sources,
            human_dirs={str(k): str(v) for k, v in human_dirs.items()},
            dispatch_date_field=str(data.get("dispatch_date_field", "coalesce")),
            sync_monitor={str(k): v for k, v in sync_monitor.items()},
        )

    @classmethod
    def load(cls, path: str) -> "Config":
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return cls.from_dict(data)

    def resolve(self, base: str) -> "Config":
        """Return a copy with relative paths resolved against *base*."""
        import copy

        clone = copy.deepcopy(self)
        for attr in ("catalog_db", "vault_dir", "state_dir"):
            value = getattr(clone, attr)
            if not os.path.isabs(value):
                setattr(clone, attr, os.path.normpath(os.path.join(base, value)))
        return clone

