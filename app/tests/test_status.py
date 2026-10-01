import json
import os
import unittest
from datetime import datetime, timedelta, timezone

from fixtures import make_config, temp_dir

from library import status
from library import monitor as monitor_module
from library.catalog import Catalog
from library.models import Document, Version
from library.runtime import scheduler_state_path, utc_now, write_json_atomic


def iso(dt):
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def make_doc(doc_id, title="标题"):
    return Document(
        source="reports", doc_id=doc_id, title=title, display_title=title,
        available=True, status="done",
        versions=[Version(version_id="v-" + doc_id, sha256="sha-" + doc_id, bytes=5,
                          media_type="application/pdf", is_current=True, state="ready")],
    )


class SchedulerPanelTest(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir()
        self.config, _, _ = make_config(self.tmp)

    def _write(self, data):
        write_json_atomic(scheduler_state_path(self.config.state_dir), data)

    def test_unknown_without_history(self):
        panel = status.scheduler_panel(self.config, utc_now())
        self.assertEqual(panel["state"], "unknown")
        self.assertTrue(panel["stale"])
        self.assertIsNone(panel["last_finished_at"])

    def test_running_state(self):
        now = utc_now()
        self._write({"state": "running", "updated_at": now, "interval_seconds": 3600,
                     "last_attempt_started_at": now})
        panel = status.scheduler_panel(self.config, utc_now())
        self.assertTrue(panel["running"])
        self.assertEqual(panel["state"], "running")

    def test_stale_heartbeat(self):
        old = iso(datetime.now(timezone.utc) - timedelta(seconds=300))
        self._write({"state": "idle", "updated_at": old, "interval_seconds": 3600})
        panel = status.scheduler_panel(self.config, utc_now())
        self.assertTrue(panel["stale"])
        self.assertIn("失联", panel["state_label"])

    def test_overdue_next_check(self):
        now = datetime.now(timezone.utc)
        self._write({"state": "idle", "updated_at": iso(now), "interval_seconds": 3600,
                     "next_check_at": iso(now - timedelta(seconds=400))})
        panel = status.scheduler_panel(self.config, utc_now())
        self.assertTrue(panel["overdue"])

    def test_stopped(self):
        self._write({"state": "stopped", "updated_at": utc_now(), "interval_seconds": 3600})
        panel = status.scheduler_panel(self.config, utc_now())
        self.assertEqual(panel["state"], "stopped")
        self.assertIn("停止", panel["state_label"])

    def test_invalid_heartbeat_is_stale(self):
        self._write({"state": "idle", "updated_at": "invalid", "interval_seconds": 3600})
        panel = status.scheduler_panel(self.config, utc_now())
        self.assertTrue(panel["stale"])
        self.assertIn("心跳", panel["state_label"])

    def test_invalid_next_check_is_stale(self):
        self._write({"state": "idle", "updated_at": utc_now(), "interval_seconds": 3600,
                     "next_check_at": "invalid"})
        panel = status.scheduler_panel(self.config, utc_now())
        self.assertTrue(panel["stale"])
        self.assertTrue(panel["next_check_invalid"])
        self.assertIsNone(panel["next_check_at"])

    def test_unknown_state_is_stale(self):
        self._write({"state": "gremlin", "updated_at": utc_now(), "interval_seconds": 3600})
        panel = status.scheduler_panel(self.config, utc_now())
        self.assertEqual(panel["state"], "unknown")
        self.assertTrue(panel["stale"])

    def test_manual_run_does_not_move_next_check(self):
        now = datetime.now(timezone.utc)
        planned = iso(now + timedelta(seconds=1800))
        self._write({"state": "idle", "updated_at": iso(now), "interval_seconds": 3600,
                     "next_check_at": planned})
        catalog = Catalog(self.config.catalog_db)
        try:
            catalog.record_run(utc_now(), True, {}, None)
        finally:
            catalog.close()
        panel = status.scheduler_panel(self.config, utc_now())
        self.assertEqual(panel["next_check_at"], planned)


class IngestionStatusTest(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir()
        self.config, _, _ = make_config(self.tmp)
        self.catalog = Catalog(self.config.catalog_db)

    def tearDown(self):
        self.catalog.close()

    def _fresh_scheduler(self):
        now = datetime.now(timezone.utc)
        write_json_atomic(scheduler_state_path(self.config.state_dir), {
            "state": "idle", "updated_at": iso(now), "interval_seconds": 3600,
            "next_check_at": iso(now + timedelta(seconds=3600)),
            "last_finished_at": iso(now), "last_ok": True,
        })

    def test_initializing_without_runs(self):
        result = status.build_status(self.config, self.catalog)
        self.assertEqual(result["ingestion"]["state"], "initializing")
        self.assertEqual(result["timezone"], "Asia/Shanghai (UTC+8)")

    def test_ok_with_no_new_changes(self):
        self.catalog.commit_snapshot("reports", [make_doc("a")], {}, {})
        self._fresh_scheduler()
        started = utc_now()
        self.catalog.record_run(started, True, {}, None)
        result = status.build_status(self.config, self.catalog)
        self.assertEqual(result["ingestion"]["state"], "ok")
        self.assertEqual(result["ingestion"]["latest_changes"], 0)
        self.assertIn("无新增变化", result["ingestion"]["state_label"])

    def test_error_run_reported(self):
        self._fresh_scheduler()
        self.catalog.record_run(utc_now(), False, {}, "SourceError: source scan failed")
        result = status.build_status(self.config, self.catalog)
        self.assertEqual(result["ingestion"]["state"], "error")
        self.assertIn("失败", result["ingestion"]["state_label"])

    def test_stale_scheduler_downgrades_old_success(self):
        self.catalog.commit_snapshot("reports", [make_doc("a")], {}, {})
        self.catalog.record_run(utc_now(), True, {}, None)
        write_json_atomic(scheduler_state_path(self.config.state_dir), {
            "state": "idle",
            "updated_at": iso(datetime.now(timezone.utc) - timedelta(seconds=600)),
            "interval_seconds": 3600,
            "last_ok": True,
        })
        result = status.build_status(self.config, self.catalog)
        self.assertEqual(result["ingestion"]["state"], "stale")
        self.assertNotEqual(result["ingestion"]["state_label"], "检查成功，无新增变化")

    def test_invalid_heartbeat_never_shows_ingress_ok(self):
        self.catalog.commit_snapshot("reports", [make_doc("a")], {}, {})
        self.catalog.record_run(utc_now(), True, {}, None, changes=0)
        write_json_atomic(scheduler_state_path(self.config.state_dir), {
            "state": "idle", "updated_at": "invalid", "interval_seconds": 3600,
            "next_check_at": "invalid", "last_ok": True,
        })
        result = status.build_status(self.config, self.catalog)
        self.assertNotEqual(result["ingestion"]["state"], "ok")
        self.assertEqual(result["ingestion"]["state"], "stale")


class SyncPanelTest(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir()
        self.config, _, _ = make_config(self.tmp)

    def _snapshot_path(self):
        return os.path.join(self.config.state_dir, "monitor", "syncthing.json")

    def _write(self, snapshot):
        write_json_atomic(self._snapshot_path(), snapshot)

    def _configure(self):
        self.config.sync_monitor = {
            "snapshot_path": self._snapshot_path(),
            "stale_after_seconds": 90,
            "peer_alias": "测试Windows",
        }

    def _base(self, **overrides):
        snapshot = {
            "schema": "researchkb.syncmonitor/1",
            "observed_at": utc_now(),
            "poll_ok": True,
            "errors": [],
            "server": {"version": "v2.1.5", "uptime_seconds": 100, "id_short": "ABCDEFG"},
            "folder": {"id": "research-vault", "label": "Research Vault", "paused": False,
                       "state": "idle", "need_items": 0, "need_bytes": 0, "errors": 0,
                       "pull_errors": 0, "error": None, "local_files": 537,
                       "global_files": 537, "in_sync_files": 537},
            "peer": {"id_short": "CIUVKDJ", "alias": "peer", "connected": True, "paused": False,
                     "completion": 100, "remote_state": "valid", "need_items": 0, "need_bytes": 0},
        }
        snapshot.update(overrides)
        return snapshot

    def test_connected_complete(self):
        self._configure()
        self._write(self._base())
        panel = status._sync_panel(self.config, utc_now())
        self.assertEqual(panel["status"], "complete")
        self.assertEqual(panel["peer"]["alias"], "测试Windows")

    def test_remote_pending_despite_local_idle(self):
        self._configure()
        snap = self._base()
        snap["peer"]["completion"] = 99
        snap["peer"]["need_items"] = 3
        snap["folder"]["need_items"] = 0
        self._write(snap)
        panel = status._sync_panel(self.config, utc_now())
        self.assertEqual(panel["status"], "pending")

    def test_disconnected_even_at_old_100(self):
        self._configure()
        snap = self._base()
        snap["peer"]["connected"] = False
        snap["peer"]["completion"] = 100
        self._write(snap)
        panel = status._sync_panel(self.config, utc_now())
        self.assertEqual(panel["status"], "offline")
        self.assertNotEqual(panel["status"], "complete")

    def test_unavailable_remote_state_is_not_complete(self):
        self._configure()
        snap = self._base()
        snap["peer"]["remote_state"] = "unknown"
        self._write(snap)
        panel = status._sync_panel(self.config, utc_now())
        self.assertEqual(panel["status"], "unknown")

    def test_poll_failure_is_error(self):
        self._configure()
        snap = self._base(poll_ok=False)
        snap["errors"] = ["unreachable on /rest/system/status (URLError)"]
        self._write(snap)
        panel = status._sync_panel(self.config, utc_now())
        self.assertEqual(panel["status"], "error")

    def test_stale_sample(self):
        self._configure()
        snap = self._base(observed_at=iso(datetime.now(timezone.utc) - timedelta(seconds=300)))
        self._write(snap)
        panel = status._sync_panel(self.config, utc_now())
        self.assertEqual(panel["status"], "stale")

    def test_unconfigured(self):
        panel = status._sync_panel(self.config, utc_now())
        self.assertFalse(panel["configured"])
        self.assertEqual(panel["status"], "unknown")

    def test_public_panel_has_no_secret_or_full_id(self):
        self._configure()
        snap = self._base()
        snap["apikey"] = "SECRET-KEY-VALUE"
        snap["peer"]["deviceID"] = "CIUVKDJ-KYDPWEE-5F3GKUR-EM2CMUX"
        snap["server"]["myID"] = "ABCDEFG-HIJKLMN-OPQRSTU"
        self._write(snap)
        panel = status._sync_panel(self.config, utc_now())
        text = json.dumps(panel, ensure_ascii=False)
        self.assertNotIn("SECRET", text)
        self.assertNotIn("KYDPWEE-5F3GKUR", text)
        self.assertNotIn("opencode-test-home", text)

    def test_folder_error_text_is_error_regardless_of_numeric(self):
        self._configure()
        snap = self._base()
        snap["folder"]["error"] = "open /private/review/secret-folder: permission denied"
        snap["folder"]["errors"] = 0
        self._write(snap)
        panel = status._sync_panel(self.config, utc_now())
        self.assertEqual(panel["status"], "error")
        text = json.dumps(panel, ensure_ascii=False)
        self.assertNotIn("/private/review/secret-folder", text)
        self.assertTrue(panel["folder"]["error"])

    def test_empty_folder_is_not_complete(self):
        self._configure()
        snap = self._base()
        snap["folder"] = {}
        self._write(snap)
        panel = status._sync_panel(self.config, utc_now())
        self.assertNotEqual(panel["status"], "complete")
        self.assertEqual(panel["status"], "unknown")

    def test_missing_local_counts_is_unknown_not_complete(self):
        self._configure()
        snap = self._base()
        snap["folder"]["need_items"] = None
        snap["folder"]["need_bytes"] = None
        self._write(snap)
        panel = status._sync_panel(self.config, utc_now())
        self.assertEqual(panel["status"], "unknown")

    def test_missing_remote_count_is_unknown(self):
        self._configure()
        snap = self._base()
        snap["peer"]["need_items"] = None
        self._write(snap)
        panel = status._sync_panel(self.config, utc_now())
        self.assertEqual(panel["status"], "unknown")

    def test_invalid_observed_at_is_not_complete(self):
        self._configure()
        snap = self._base(observed_at="not-a-time")
        self._write(snap)
        panel = status._sync_panel(self.config, utc_now())
        self.assertNotEqual(panel["status"], "complete")

    def test_bypassed_snapshot_loader_still_redacts(self):
        self._configure()
        raw = self._base()
        raw["folder"]["error"] = "open /private/review/secret-folder: permission denied"
        original = monitor_module.load_snapshot
        monitor_module.load_snapshot = lambda config: {
            "present": True, "path_configured": True, "snapshot": raw}
        try:
            panel = status._sync_panel(self.config, utc_now())
        finally:
            monitor_module.load_snapshot = original
        text = json.dumps(panel, ensure_ascii=False)
        self.assertNotIn("/private/review/secret-folder", text)
        self.assertEqual(panel["status"], "error")


if __name__ == "__main__":
    unittest.main()
