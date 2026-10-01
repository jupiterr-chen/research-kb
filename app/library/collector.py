"""Read-only Syncthing status collector (task-scoped compose service).

This process is intentionally tiny and narrow:

* It runs with host networking only so it can reach the loopback Syncthing REST
  API (127.0.0.1:8384); it listens on no port.
* It reads only the Syncthing config/key file via a read-only narrow mount and
  makes only ``GET`` requests. It never changes Syncthing state.
* It writes a *sanitized* snapshot (no API key, no full device id, no host
  paths, no peer address) atomically to the monitor directory.
* When polling fails it keeps the previous last-good values and records
  ``poll_ok=false`` plus an error so consumers can mark the sample stale.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

from .monitor import redact_text, short_device_id
from .runtime import read_json, utc_now, write_json_atomic

SCHEMA = "researchkb.syncmonitor/1"
DEFAULT_CONFIG = "/syncthing-config/config.xml"
DEFAULT_MONITOR_DIR = "/monitor"
DEFAULT_GUI = "http://127.0.0.1:8384"
DEFAULT_INTERVAL = 20
DEFAULT_ALIAS = "Windows 设备"
HTTP_TIMEOUT = 8.0


class PollError(Exception):
    pass


def _env(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value if value else default


def read_api_key(config_path: str) -> str:
    try:
        root = ET.parse(config_path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise PollError("cannot read syncthing config: %s" % type(exc).__name__)
    node = root.find(".//gui/apikey")
    if node is None or not (node.text or "").strip():
        raise PollError("syncthing config has no gui apikey")
    return node.text.strip()


def api_get(gui: str, key: str, path: str) -> Any:
    request = urllib.request.Request(gui.rstrip("/") + path)
    request.add_header("X-API-Key", key)
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise PollError("HTTP %s on %s" % (exc.code, path.split("?")[0]))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise PollError("unreachable on %s (%s)" % (path.split("?")[0], type(exc).__name__))
    except ValueError:
        raise PollError("invalid JSON on %s" % path.split("?")[0])


def select_peer(devices: List[Dict[str, Any]], my_id: Optional[str],
                connections: Dict[str, Any], configured: Optional[str]) -> Optional[Dict[str, Any]]:
    """Pick the paired peer. When an explicit target is configured it must match
    exactly; never fall back to an unrelated device and mislabel it."""
    candidates = [
        device for device in devices
        if (device.get("deviceID") or "") and device.get("deviceID") != my_id
    ]
    if configured:
        wanted = configured.upper()
        matches = [d for d in candidates if (d.get("deviceID") or "").upper().startswith(wanted)]
        if not matches:
            return None
        candidates = matches
    if not candidates:
        return None

    def connected(device: Dict[str, Any]) -> bool:
        return bool((connections.get(device.get("deviceID")) or {}).get("connected"))

    for device in candidates:
        if connected(device):
            return device
    return candidates[0]


def choose_folder(folders: List[Dict[str, Any]], folder_id: Optional[str]) -> Dict[str, Any]:
    """Return the configured folder, or the first one when unconfigured. A
    configured-but-absent folder is an error, not a silent fallback."""
    if folder_id:
        for folder in folders:
            if folder.get("id") == folder_id:
                return folder
        raise PollError("configured syncthing folder not found")
    if folders:
        return folders[0]
    raise PollError("no syncthing folder configured")


def collect(gui: str, key: str, folder_id: Optional[str], alias: str) -> Dict[str, Any]:
    errors: List[str] = []
    version = api_get(gui, key, "/rest/system/version")
    status = api_get(gui, key, "/rest/system/status")
    my_id = status.get("myID")
    folders = api_get(gui, key, "/rest/config/folders")
    devices = api_get(gui, key, "/rest/config/devices")
    connections_doc = api_get(gui, key, "/rest/system/connections")
    connections = connections_doc.get("connections") or {}

    chosen_folder = choose_folder(folders, folder_id)

    fid = chosen_folder.get("id")
    folder_status = api_get(gui, key, "/rest/db/status?folder=%s" % fid)

    configured_peer = os.environ.get("RESEARCHKB_SYNCTHING_PEER_ID")
    peer = select_peer(devices, my_id, connections, configured_peer)
    if configured_peer and peer is None:
        raise PollError("configured peer device not found")
    peer_panel: Dict[str, Any] = {}
    if peer is not None:
        peer_id = peer.get("deviceID")
        conn = connections.get(peer_id) or {}
        completion = None
        remote_state = None
        remote_need_items = None
        remote_need_bytes = None
        remote_sequence = None
        try:
            completion_doc = api_get(
                gui, key, "/rest/db/completion?folder=%s&device=%s" % (fid, peer_id))
            completion = completion_doc.get("completion")
            remote_state = completion_doc.get("remoteState")
            remote_need_items = completion_doc.get("needItems")
            remote_need_bytes = completion_doc.get("needBytes")
            remote_sequence = completion_doc.get("sequence")
        except PollError as exc:
            errors.append(redact_text(str(exc)))
        peer_panel = {
            "id_short": short_device_id(peer_id),
            "alias": alias,
            "connected": bool(conn.get("connected")),
            "paused": bool(conn.get("paused")) if conn else None,
            "last_seen_at": conn.get("at"),
            "completion": completion,
            "remote_state": remote_state,
            "need_items": remote_need_items,
            "need_bytes": remote_need_bytes,
            "remote_sequence": remote_sequence,
        }

    folder_error = folder_status.get("error")
    ready_folder = {
        "id": fid,
        "label": chosen_folder.get("label") or fid,
        "paused": bool(chosen_folder.get("paused")),
        "state": folder_status.get("state"),
        "state_changed_at": folder_status.get("stateChanged"),
        "local_files": folder_status.get("localFiles"),
        "global_files": folder_status.get("globalFiles"),
        "in_sync_files": folder_status.get("inSyncFiles"),
        "need_items": folder_status.get("needTotalItems"),
        "need_bytes": folder_status.get("needBytes"),
        "errors": folder_status.get("errors"),
        "pull_errors": folder_status.get("pullErrors"),
        # Free-form upstream errors are redacted before they can reach any
        # snapshot or public response.
        "error": redact_text(folder_error) if folder_error else None,
    }

    return {
        "schema": SCHEMA,
        "observed_at": utc_now(),
        "poll_ok": True,
        "errors": errors,
        "server": {
            "version": version.get("version"),
            "uptime_seconds": status.get("uptime"),
            "id_short": short_device_id(my_id),
        },
        "folder": ready_folder,
        "peer": peer_panel,
        "backlog": {
            "server_local": {
                "need_items": ready_folder["need_items"],
                "need_bytes": ready_folder["need_bytes"],
            },
            "remote": {
                "need_items": peer_panel.get("need_items"),
                "need_bytes": peer_panel.get("need_bytes"),
            },
        },
    }


def _failure_snapshot(path: str, message: str) -> Dict[str, Any]:
    previous = read_json(path)
    if not isinstance(previous, dict):
        previous = {"schema": SCHEMA, "observed_at": None, "server": {}, "folder": {}, "peer": {}}
    snapshot = dict(previous)
    snapshot["schema"] = SCHEMA
    snapshot["poll_ok"] = False
    snapshot["failed_at"] = utc_now()
    snapshot["errors"] = [redact_text(message)]
    return snapshot


def run_once(config_path: str, monitor_dir: str, gui: str, folder_id: Optional[str],
             alias: str) -> Dict[str, Any]:
    path = os.path.join(monitor_dir, "syncthing.json")
    try:
        key = read_api_key(config_path)
        snapshot = collect(gui, key, folder_id, alias)
    except PollError as exc:
        snapshot = _failure_snapshot(path, str(exc))
    except Exception as exc:  # never let an unexpected shape kill the collector
        snapshot = _failure_snapshot(path, "collector error: %s" % type(exc).__name__)
    write_json_atomic(path, snapshot)
    return snapshot


def main(argv=None) -> int:
    config_path = _env("RESEARCHKB_SYNCTHING_CONFIG", DEFAULT_CONFIG)
    monitor_dir = _env("RESEARCHKB_MONITOR_DIR", DEFAULT_MONITOR_DIR)
    gui = _env("RESEARCHKB_SYNCTHING_GUI", DEFAULT_GUI)
    folder_id = os.environ.get("RESEARCHKB_SYNCTHING_FOLDER") or None
    alias = _env("RESEARCHKB_SYNCTHING_PEER_ALIAS", DEFAULT_ALIAS)
    try:
        interval = max(5, int(_env("RESEARCHKB_MONITOR_INTERVAL", str(DEFAULT_INTERVAL))))
    except ValueError:
        interval = DEFAULT_INTERVAL
    os.makedirs(monitor_dir, exist_ok=True)
    print("status collector polling %s every %ds" % (gui, interval), flush=True)
    while True:
        snapshot = run_once(config_path, monitor_dir, gui, folder_id, alias)
        state = "ok" if snapshot.get("poll_ok") else "error"
        print("collector %s at %s" % (state, snapshot.get("observed_at") or snapshot.get("failed_at")),
              flush=True)
        time.sleep(interval)


if __name__ == "__main__":
    sys.exit(main())
