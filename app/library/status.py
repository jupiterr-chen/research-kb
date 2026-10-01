"""Build the public status model shown by the Chinese dashboard.

Honesty rules enforced here:

* Never report "healthy"/"complete" from stale data. An old success does not
  stay green once the scheduler heartbeat stops or the next check is overdue.
* Missing history is reported as ``unknown``/``initializing``, not healthy.
* Unknown remote counts stay ``None`` (rendered as 未知), never ``0``.
* Server-local idle is not Windows completion: the peer connection, remote
  completion and remote backlog are evaluated separately.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from . import monitor
from .changes import KIND_LABELS
from .locking import FileLock
from .runtime import (
    CHINA_TZ_LABEL,
    china_display,
    china_iso,
    parse_iso,
    read_scheduler_state,
    seconds_between,
    utc_now,
)

HEARTBEAT_STALE_SECONDS = 60
NEXT_CHECK_GRACE_SECONDS = 120

SOURCE_LABELS = {
    "reports": "财报归档",
    "discord": "Discord 导出",
}

SOURCE_KIND_LABELS = {
    "reports_archive": "财报归档（只读 SQLite）",
    "discord_export": "Discord 导出（只读 JSONL）",
}


def _iso(value: Optional[str]) -> Optional[str]:
    return value or None


def _duration(start: Optional[str], end: Optional[str]) -> Optional[float]:
    return seconds_between(start, end)


def task_running(config) -> Optional[bool]:
    """Best-effort reliable 'an ingest/render is running' probe via the lock."""
    path = os.path.join(config.state_dir, "ingest.lock")
    try:
        lock = FileLock(path)
        acquired = lock.acquire(blocking=False)
        if acquired:
            lock.release()
            return False
        return True
    except OSError:
        return None


def scheduler_panel(config, now_iso: str) -> Dict[str, Any]:
    state = read_scheduler_state(config.state_dir)
    interval = max(60, int(config.ingest_interval_seconds))
    if not state:
        return {
            "configured": True,
            "interval_seconds": interval,
            "state": "unknown",
            "state_label": "未知（无调度记录）",
            "last_attempt_started_at": None,
            "last_finished_at": None,
            "last_ok": None,
            "last_error": None,
            "next_check_at": None,
            "next_check_estimated": True,
            "overdue": None,
            "stale": True,
            "running": task_running(config),
            "heartbeat_at": None,
            "heartbeat_age_seconds": None,
        }

    heartbeat = state.get("updated_at")
    heartbeat_at = parse_iso(heartbeat)
    heartbeat_age = _duration(heartbeat, now_iso)
    raw_state = state.get("state") or "unknown"
    known_state = raw_state in ("idle", "running", "stopped")
    heartbeat_invalid = heartbeat_at is None
    heartbeat_stale = heartbeat_invalid or (
        heartbeat_age is not None and heartbeat_age > HEARTBEAT_STALE_SECONDS)
    next_check = state.get("next_check_at")
    next_check_at = parse_iso(next_check)
    next_check_invalid = bool(next_check) and next_check_at is None
    overdue = False
    if raw_state == "idle" and next_check_at is not None:
        gap = _duration(next_check, now_iso)
        overdue = gap is not None and gap > NEXT_CHECK_GRACE_SECONDS

    if not known_state:
        state_label = "调度状态未知"
    elif raw_state == "stopped":
        state_label = "已停止"
    elif heartbeat_invalid:
        state_label = "调度心跳缺失或无效"
    elif heartbeat_stale:
        state_label = "调度线程已失联（心跳过期）"
    elif next_check_invalid:
        state_label = "下次检查时间无效"
    elif raw_state == "running":
        state_label = "正在入库"
    elif overdue:
        state_label = "已超过预计检查时间"
    elif raw_state == "idle":
        state_label = "空闲，等待下次检查"
    else:
        state_label = "未知"

    stale = heartbeat_stale or not known_state or next_check_invalid
    running = raw_state == "running" or bool(task_running(config))
    return {
        "configured": True,
        "interval_seconds": int(state.get("interval_seconds") or interval),
        "state": raw_state if known_state else "unknown",
        "state_label": state_label,
        "last_attempt_started_at": _iso(state.get("last_attempt_started_at")),
        "last_finished_at": _iso(state.get("last_finished_at")),
        "last_ok": state.get("last_ok"),
        "last_error": state.get("last_error"),
        "next_check_at": next_check if next_check_at else None,
        "next_check_estimated": True,
        "next_check_invalid": next_check_invalid,
        "overdue": overdue,
        "stale": stale,
        "running": running,
        "heartbeat_at": heartbeat if heartbeat_at else None,
        "heartbeat_age_seconds": heartbeat_age,
    }


def _run_panel(runs: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not runs:
        return {
            "last_attempt_at": None,
            "last_finished_at": None,
            "last_ok": None,
            "last_error": None,
            "last_duration_seconds": None,
            "latest_changes": 0,
            "recent": [],
        }
    latest = runs[0]
    recent = []
    for run in runs[:5]:
        recent.append({
            "started_at": run.get("started_at"),
            "finished_at": run.get("finished_at"),
            "ok": bool(run.get("ok")),
            "error": run.get("error"),
            "changes": run.get("changes", 0),
            "duration_seconds": _duration(run.get("started_at"), run.get("finished_at")),
        })
    return {
        "last_attempt_at": latest.get("started_at"),
        "last_finished_at": latest.get("finished_at"),
        "last_ok": bool(latest.get("ok")),
        "last_error": latest.get("error"),
        "last_duration_seconds": _duration(latest.get("started_at"), latest.get("finished_at")),
        "latest_changes": int(latest.get("changes", 0) or 0),
        "recent": recent,
    }


def _index_observed(config, name: str) -> Optional[Dict[str, Any]]:
    """Index file mtime, explicitly labelled as local observation only."""
    source = config.sources.get(name)
    if source is None:
        return None
    candidate = None
    if source.type == "reports_archive":
        candidate = source.extra.get("db")
    elif source.type == "discord_export":
        candidate = source.extra.get("index")
    if not candidate:
        return None
    try:
        stat = os.stat(candidate)
    except OSError:
        return {"available": False, "mtime": None, "bytes": None}
    from datetime import datetime, timezone

    mtime = datetime.fromtimestamp(stat.st_mtime, timezone.utc).replace(microsecond=0)
    return {
        "available": True,
        "mtime": mtime.isoformat().replace("+00:00", "Z"),
        "bytes": stat.st_size,
        "label": "索引文件修改时间（本地观测，不代表上游下载任务成功）",
    }


def _source_panel(config, catalog, name: str) -> Dict[str, Any]:
    state = catalog.source_state(name) or {}
    breakdown = catalog.source_breakdown(name)
    source = config.sources.get(name)
    last_error = state.get("last_error")
    last_ok_at = state.get("last_ok_at")
    last_attempt = state.get("last_attempt_at") or last_ok_at

    # A failed source keeps its last successful catalog but must show failure
    # freshness separately and must not be considered healthy.
    failed = bool(last_error)
    if failed and last_attempt:
        freshness = "扫描失败"
    elif last_ok_at:
        freshness = "最近扫描成功"
    else:
        freshness = "尚未成功扫描"
    healthy = bool(last_ok_at) and not failed

    status_counts = breakdown["status_counts"]
    state_counts = breakdown["version_state_counts"]
    waiting = status_counts.get("discovered", 0)
    unavailable_states = state_counts.get("missing", 0) + state_counts.get("conflict", 0)
    return {
        "name": name,
        "label": SOURCE_LABELS.get(name, name),
        "kind": source.type if source else None,
        "kind_label": SOURCE_KIND_LABELS.get(source.type if source else "", source.type if source else None),
        "healthy": healthy,
        "freshness": freshness,
        "last_attempt_at": last_attempt,
        "last_ok_at": last_ok_at,
        "last_error_at": state.get("last_error_at"),
        "last_error": last_error,
        "documents_total": breakdown["documents_total"],
        "documents_available": breakdown["documents_available"],
        "status_counts": status_counts,
        "version_state_counts": state_counts,
        "waiting": waiting,
        "unavailable": unavailable_states,
        "index_observed": _index_observed(config, name),
        "counts_snapshot": state.get("counts") or {},
    }


def _change_panel(config, catalog) -> List[Dict[str, Any]]:
    items = []
    for change in catalog.recent_changes(20):
        source = change["source"]
        doc_id = change.get("doc_id")
        detail_url = None
        if doc_id:
            detail_url = "%s/api/v1/documents/%s/%s" % (
                config.public_base_url, source, doc_id)
        items.append({
            "at": change["at"],
            "source": source,
            "source_label": SOURCE_LABELS.get(source, source),
            "doc_id": doc_id,
            "kind": change["kind"],
            "kind_label": KIND_LABELS.get(change["kind"], change["kind"]),
            "title": change.get("title"),
            "detail": change.get("detail") or {},
            "detail_url": detail_url,
        })
    return items


def _sync_panel(config, now_iso: str) -> Dict[str, Any]:
    monitor_cfg = getattr(config, "sync_monitor", None) or {}
    path = monitor.snapshot_path(config)
    loaded = monitor.load_snapshot(config)
    present = loaded["present"]
    snapshot = loaded.get("snapshot") or {}
    age = monitor.sample_age_seconds(snapshot, now_iso) if present else None
    stale_limit = monitor.stale_after(config)
    stale = True if not present else (age is None or age > stale_limit)

    panel: Dict[str, Any] = {
        "configured": bool(monitor_cfg) or present,
        "configured_path": bool(monitor_cfg.get("snapshot_path")),
        "status": "unknown",
        "status_label": "未知",
        "collector": {
            "snapshot_present": present,
            "poll_ok": snapshot.get("poll_ok"),
            "observed_at": snapshot.get("observed_at"),
            "failed_at": snapshot.get("failed_at"),
            "age_seconds": age,
            "stale_after_seconds": stale_limit,
            "stale": stale,
            "errors": [monitor.redact_text(str(e)) for e in (snapshot.get("errors") or [])],
        },
        "server": {
            "available": False,
            "version": None,
            "uptime_seconds": None,
            "local_id_short": None,
        },
        "folder": {
            "available": False,
            "id": None,
            "label": None,
            "paused": None,
            "state": None,
            "state_changed_at": None,
            "local_files": None,
            "global_files": None,
            "in_sync_files": None,
            "need_items": None,
            "need_bytes": None,
            "errors": None,
            "pull_errors": None,
            "error": None,
        },
        "peer": {
            "available": False,
            "alias": monitor_cfg.get("peer_alias") or "Windows 设备",
            "id_short": None,
            "connected": None,
            "paused": None,
            "last_seen_at": None,
            "completion": None,
            "remote_state": None,
            "need_items": None,
            "need_bytes": None,
            "remote_sequence": None,
        },
        "backlog": {
            "server_local": {"need_items": None, "need_bytes": None},
            "remote": {"need_items": None, "need_bytes": None},
        },
        "server_backlog_note": "服务器本地待拉取项，与远端待发送项是两回事。",
        "units_note": "同步文件数包含生成的索引与笔记（如 537 项），并非报告篇数（报告/文档数见入库统计）。",
    }

    if not present:
        panel["status"] = "unknown"
        panel["status_label"] = ("等待采集器（未配置同步监控）"
                                 if not monitor_cfg else "等待采集器首次采集")
        return panel

    server = snapshot.get("server") or {}
    folder = snapshot.get("folder") or {}
    peer = snapshot.get("peer") or {}
    backlog = snapshot.get("backlog") or {}

    panel["server"] = {
        "available": bool(server),
        "version": server.get("version"),
        "uptime_seconds": server.get("uptime_seconds"),
        "local_id_short": server.get("id_short"),
    }
    panel["folder"] = {
        "available": bool(folder),
        "id": folder.get("id"),
        "label": folder.get("label"),
        "paused": folder.get("paused"),
        "state": folder.get("state"),
        "state_changed_at": folder.get("state_changed_at"),
        "local_files": folder.get("local_files"),
        "global_files": folder.get("global_files"),
        "in_sync_files": folder.get("in_sync_files"),
        "need_items": folder.get("need_items"),
        "need_bytes": folder.get("need_bytes"),
        "errors": folder.get("errors"),
        "pull_errors": folder.get("pull_errors"),
        "error": monitor.redact_text(folder.get("error")) if folder.get("error") else None,
    }
    alias = monitor_cfg.get("peer_alias") or peer.get("alias") or "Windows 设备"
    panel["peer"] = {
        "available": bool(peer),
        "alias": alias,
        "id_short": peer.get("id_short"),
        "connected": peer.get("connected"),
        "paused": peer.get("paused"),
        "last_seen_at": peer.get("last_seen_at"),
        "completion": peer.get("completion"),
        "remote_state": peer.get("remote_state"),
        "need_items": peer.get("need_items"),
        "need_bytes": peer.get("need_bytes"),
        "remote_sequence": peer.get("remote_sequence"),
    }
    panel["backlog"] = {
        "server_local": {
            "need_items": folder.get("need_items"),
            "need_bytes": folder.get("need_bytes"),
        },
        "remote": {
            "need_items": peer.get("need_items"),
            "need_bytes": peer.get("need_bytes"),
        },
    }

    errors = panel["collector"]["errors"]
    poll_ok = snapshot.get("poll_ok")
    observed_valid = parse_iso(snapshot.get("observed_at")) is not None
    folder_available = bool(folder) and folder.get("state") is not None
    peer_available = bool(peer)
    folder_error_text = str(folder.get("error") or "").strip()
    folder_paused = folder.get("paused")
    peer_paused = peer.get("paused")
    connected = peer.get("connected")
    remote_state = peer.get("remote_state")
    completion = peer.get("completion")
    remote_need_items = peer.get("need_items")
    remote_need_bytes = peer.get("need_bytes")
    local_need_items = folder.get("need_items")
    local_need_bytes = folder.get("need_bytes")
    folder_errors = folder.get("errors")
    folder_pull_errors = folder.get("pull_errors")

    # Fail closed: only report "complete" when polling explicitly succeeded, the
    # sample is fresh and the required local + remote fields are present/valid.
    # Missing or invalid observation data is unknown/error/stale, never complete.
    if poll_ok is not True:
        panel["status"] = "error"
        panel["status_label"] = "采集失败（显示上次可用样本）"
    elif not observed_valid:
        panel["status"] = "unknown"
        panel["status_label"] = "样本时间缺失或无效"
    elif stale:
        panel["status"] = "stale"
        panel["status_label"] = "数据已过期"
    elif folder_error_text:
        # Any non-empty folder error is an error regardless of numeric counters.
        panel["status"] = "error"
        panel["status_label"] = "同步文件夹报错（已脱敏）"
    elif folder_errors is None or folder_pull_errors is None:
        panel["status"] = "unknown"
        panel["status_label"] = "文件夹错误计数未知"
    elif folder_errors > 0 or folder_pull_errors > 0:
        panel["status"] = "error"
        panel["status_label"] = "同步出现错误"
    elif folder_paused is not True and folder_paused is not False:
        panel["status"] = "unknown"
        panel["status_label"] = "文件夹暂停状态未知"
    elif folder_paused or peer_paused is True:
        panel["status"] = "paused"
        panel["status_label"] = "已暂停"
    elif not folder_available:
        panel["status"] = "unknown"
        panel["status_label"] = "本地文件夹状态未知"
    elif not peer_available:
        panel["status"] = "unknown"
        panel["status_label"] = "未识别到对端设备"
    elif connected is False:
        panel["status"] = "offline"
        panel["status_label"] = "Windows 端未连接"
    elif connected is not True:
        panel["status"] = "unknown"
        panel["status_label"] = "Windows 连接状态未知"
    elif remote_state not in ("valid",):
        panel["status"] = "unknown"
        panel["status_label"] = "远端状态未知（%s）" % (remote_state or "无")
    elif None in (completion, remote_need_items, remote_need_bytes,
                  local_need_items, local_need_bytes):
        panel["status"] = "unknown"
        panel["status_label"] = "完成度或积压计数未知"
    elif remote_need_items > 0 or remote_need_bytes > 0:
        panel["status"] = "pending"
        panel["status_label"] = "远端存在待传输项"
    elif local_need_items > 0 or local_need_bytes > 0:
        panel["status"] = "pending"
        panel["status_label"] = "服务器本地存在待拉取项"
    elif completion == 100:
        panel["status"] = "complete"
        panel["status_label"] = "已同步至最新"
    else:
        panel["status"] = "pending"
        panel["status_label"] = "同步进行中（%.0f%%）" % (completion or 0)
    return panel


def _fmt_local(value: Optional[str]) -> Optional[str]:
    return china_display(value)


def build_status(config, catalog) -> Dict[str, Any]:
    now_iso = utc_now()
    counts = catalog.counts()
    runs = catalog.recent_runs(10)
    scheduler = scheduler_panel(config, now_iso)
    run_panel = _run_panel(runs)
    render = catalog.last_render()
    changes = _change_panel(config, catalog)

    latest_changes = run_panel["latest_changes"]

    # Ingestion state combines the persisted run history (includes manual runs)
    # with the scheduler heartbeat; an old run never stays green after the
    # scheduler stops or a check is overdue.
    if run_panel["last_ok"] is None:
        ingestion_state = "initializing"
        ingestion_label = "尚无入库历史（初始化中）"
    elif scheduler["stale"] or scheduler["state"] == "stopped":
        ingestion_state = "stale"
        ingestion_label = "调度已停止或失联，最近成功可能已过时"
    elif scheduler["overdue"]:
        ingestion_state = "overdue"
        ingestion_label = "已超过预计检查时间"
    elif run_panel["last_ok"] is False:
        ingestion_state = "error"
        ingestion_label = "最近一次入库存在失败"
    elif latest_changes == 0:
        ingestion_state = "ok"
        ingestion_label = "检查成功，无新增变化"
    else:
        ingestion_state = "ok"
        ingestion_label = "检查成功，发现 %d 项变化" % latest_changes

    render_panel = None
    if render is not None:
        render_panel = {
            "at": render.get("at"),
            "ok": render.get("ok"),
            "error": render.get("error"),
            "stats": render.get("stats") or {},
        }

    return {
        "kind": "status",
        "generated_at": now_iso,
        "timezone": CHINA_TZ_LABEL,
        "pipeline": [
            {"key": "archives", "label": "原始归档（只读）", "detail": "财报 SQLite + Discord JSONL"},
            {"key": "catalog", "label": "统一目录与卡片", "detail": "元数据编目、版本、Markdown 卡片"},
            {"key": "vault", "label": "Windows Vault", "detail": "经 Syncthing 单向/双向同步笔记"},
        ],
        "catalog": counts,
        "ingestion": {
            "state": ingestion_state,
            "state_label": ingestion_label,
            "scheduler": scheduler,
            "runner": run_panel,
            "render": render_panel,
            "running": bool(scheduler.get("running")),
            "latest_changes": latest_changes,
            "manual_run_note": "手动入库不影响下一次自动检查时间的计算。",
        },
        "sources": [_source_panel(config, catalog, name) for name in sorted(config.sources)],
        "recent_changes": changes,
        "recent_runs": run_panel["recent"],
        "sync": _sync_panel(config, now_iso),
        "notices": [
            "本页仅展示状态，不触发入库；‘刷新状态’只重新读取数据。",
            "元数据检索为标题/摘要/日期/代码检索，不是 PDF 全文或语义检索。",
            "本工具不监控上游下载任务，来源索引时间仅为本地文件观测。",
        ],
    }
