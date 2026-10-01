#!/usr/bin/env python3
"""Configure a local Syncthing instance over its REST API (stdlib only).

Used by both the Linux server (inside the syncthing container, via the host
loopback GUI) and the Windows client. Idempotent: safe to run repeatedly.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET


def api_key_from_home(home: str) -> str:
    config_path = os.path.join(home, "config.xml")
    if not os.path.exists(config_path):
        raise SystemExit("config.xml not found in %s" % home)
    tree = ET.parse(config_path)
    root = tree.getroot()
    node = next((element for element in root.iter("apikey") if (element.text or "").strip()), None)
    if node is None:
        raise SystemExit("apikey missing in config.xml")
    return node.text.strip()


def request(url: str, key: str, method: str = "GET", body=None, timeout: int = 20):
    data = None
    headers = {"X-API-Key": key}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read()
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        return raw.decode("utf-8", "replace")


def wait_for(base: str, key: str, attempts: int = 60) -> dict:
    last = None
    for _ in range(attempts):
        try:
            status = request(base + "/rest/system/status", key)
            if status and status.get("myID"):
                return status
        except Exception as exc:  # not up yet
            last = exc
        time.sleep(2)
    raise SystemExit("syncthing REST API not reachable: %s" % last)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", default="http://127.0.0.1:8384")
    parser.add_argument("--api-key")
    parser.add_argument("--home", help="syncthing home dir (to read apikey from config.xml)")
    parser.add_argument("--folder-id", default="research-vault")
    parser.add_argument("--folder-path", required=True)
    parser.add_argument("--folder-label", default="Research Vault")
    parser.add_argument("--peer", action="append", default=[], help="peer device id")
    parser.add_argument("--peer-address", action="append", default=[], help="peer address tcp://host:22000")
    parser.add_argument("--set-gui", default=None)
    parser.add_argument("--ignore-file", default=None)
    parser.add_argument("--restart", action="store_true")
    parser.add_argument("--no-lan-only", action="store_true")
    args = parser.parse_args()

    key = args.api_key or (api_key_from_home(args.home) if args.home else None)
    if not key:
        raise SystemExit("provide --api-key or --home")

    base = args.api_url.rstrip("/")
    status = wait_for(base, key)
    my_id = status["myID"]
    print("local device id: %s" % my_id)

    config = request(base + "/rest/config", key)
    folder_ids = {folder["id"] for folder in config.get("folders", [])}
    devices = {device["deviceID"]: device for device in config.get("devices", [])}

    for peer in args.peer:
        if peer and peer != my_id and peer not in devices:
            address = "dynamic"
            if args.peer_address:
                index = args.peer.index(peer)
                if index < len(args.peer_address):
                    address = args.peer_address[index]
            devices[peer] = {
                "deviceID": peer, "name": "peer-" + peer[-6:],
                "addresses": [address], "compression": "metadata",
                "introducer": False, "skipIntroductionRemovals": False,
                "introducedBy": "", "paused": False,
            }
    config["devices"] = list(devices.values())

    peer_ids = [p for p in args.peer if p and p != my_id]
    folder_devices = [{"deviceID": pid} for pid in peer_ids]
    folder = None
    for existing in config.get("folders", []):
        if existing["id"] == args.folder_id:
            folder = existing
            break
    if folder is None:
        folder = {
            "id": args.folder_id, "label": args.folder_label, "path": args.folder_path,
            "type": "sendreceive", "devices": folder_devices, "rescanIntervalS": 3600,
            "fsWatcherEnabled": True, "fsWatcherDelayS": 10, "markerName": ".stfolder",
            "copyprotection": True, "maxConflicts": 10, "paused": False,
        }
        config.setdefault("folders", []).append(folder)
    else:
        folder["path"] = args.folder_path
        folder["devices"] = folder_devices
        folder["type"] = "sendreceive"
    if args.folder_id not in folder_ids:
        pass  # already appended above

    options = config.setdefault("options", {})
    if not args.no_lan_only:
        options["globalAnnounceEnabled"] = False
        options["relaysEnabled"] = False
        options["natEnabled"] = False
        options["localAnnounceEnabled"] = True
        options["globalAnnounceServers"] = []
    if args.set_gui:
        config.setdefault("gui", {})["address"] = args.set_gui
        config["gui"]["enabled"] = True

    request(base + "/rest/config", key, method="PUT", body=config)
    print("configuration applied")

    # Changing the GUI address makes syncthing restart on the new address.
    if args.set_gui:
        base = "http://" + args.set_gui
        wait_for(base, key)
        print("GUI moved to %s" % base)

    if args.ignore_file and os.path.exists(args.ignore_file):
        with open(args.ignore_file, "r", encoding="utf-8") as handle:
            patterns = [line.rstrip("\n") for line in handle if line.strip()]
        request(base + "/rest/db/ignores?folder=%s" % args.folder_id, key,
                method="POST", body={"ignore": patterns})
        print("ignore patterns applied: %d" % len(patterns))

    if args.restart:
        try:
            request(base + "/rest/system/restart", key, method="POST")
            print("restart requested")
        except urllib.error.HTTPError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
