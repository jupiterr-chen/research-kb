import os
import unittest

from fixtures import temp_dir

from library import collector, monitor


class SanitizeTest(unittest.TestCase):
    def test_strips_credentials_and_shortens_device_ids(self):
        raw = {
            "apikey": "SECRET-KEY-VALUE",
            "server": {"id_short": "ABCDEFG", "myID": "ABCDEFG-HIJKLMN-OPQRSTU-VWXYZ12"},
            "peer": {"deviceID": "CIUVKDJ-KYDPWEE-5F3GKUR-EM2CMUX", "token": "another-secret"},
            "folder": {"path": "/vol2/private/path", "label": "Research Vault"},
            "nested": [{"password": "p", "ok": 1}],
        }
        cleaned = monitor.sanitize(raw)
        self.assertNotIn("apikey", cleaned)
        self.assertNotIn("token", cleaned["peer"])
        self.assertNotIn("password", cleaned["nested"][0])
        self.assertEqual(cleaned["peer"]["deviceID"], "CIUVKDJ")
        self.assertEqual(cleaned["server"]["myID"], "ABCDEFG")
        # Non-sensitive fields survive.
        self.assertEqual(cleaned["folder"]["label"], "Research Vault")

    def test_short_device_id(self):
        self.assertEqual(monitor.short_device_id("CIUVKDJ-KYDPWEE"), "CIUVKDJ")
        self.assertIsNone(monitor.short_device_id(None))
        self.assertEqual(monitor.short_device_id("ABC"), "ABC")

    def test_redact_text_removes_paths_and_tokens(self):
        raw = "open /private/review/secret-folder: permission denied"
        redacted = monitor.redact_text(raw)
        self.assertNotIn("/private/review/secret-folder", redacted)
        self.assertIn("[path]", redacted)
        self.assertIn("permission denied", redacted)
        win = monitor.redact_text(r"cannot open D:\Users\jupit\private\vault: busy")
        self.assertNotIn("D:\\Users\\jupit\\private\\vault", win)
        token = monitor.redact_text("auth failed for token abcdef0123456789ABCDEF0123456789")
        self.assertNotIn("abcdef0123456789ABCDEF0123456789", token)
        self.assertIn("[redacted]", token)

    def test_sanitize_redacts_error_strings(self):
        cleaned = monitor.sanitize({
            "folder": {"error": "open /srv/secret/path: denied", "errors": 0},
        })
        self.assertNotIn("/srv/secret/path", cleaned["folder"]["error"])
        self.assertIn("[path]", cleaned["folder"]["error"])


class SnapshotLoadTest(unittest.TestCase):
    def test_missing_snapshot_is_not_present(self):
        tmp = temp_dir()
        os.makedirs(tmp + "/state", exist_ok=True)
        config = type("C", (), {"state_dir": tmp + "/state", "sync_monitor": {}})()
        loaded = monitor.load_snapshot(config)
        self.assertFalse(loaded["present"])
        self.assertIsNone(loaded["snapshot"])

    def test_corrupt_snapshot_is_treated_as_missing(self):
        tmp = temp_dir()
        path = tmp + "/state/monitor/syncthing.json"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{not valid json")
        config = type("C", (), {"state_dir": tmp + "/state", "sync_monitor": {}})()
        loaded = monitor.load_snapshot(config)
        self.assertFalse(loaded["present"])


class CollectorSelectPeerTest(unittest.TestCase):
    def test_prefers_connected_non_local_device(self):
        devices = [
            {"deviceID": "LOCAL-AAA", "name": "fnOS"},
            {"deviceID": "REMOTE-BBB", "name": "peer"},
            {"deviceID": "REMOTE-CCC", "name": "other"},
        ]
        connections = {"REMOTE-CCC": {"connected": True}, "REMOTE-BBB": {"connected": False}}
        chosen = collector.select_peer(devices, "LOCAL-AAA", connections, None)
        self.assertEqual(chosen["deviceID"], "REMOTE-CCC")

    def test_configured_peer_prefix_wins(self):
        devices = [
            {"deviceID": "LOCAL-AAA"},
            {"deviceID": "REMOTE-BBB"},
            {"deviceID": "REMOTE-CCC"},
        ]
        connections = {"REMOTE-CCC": {"connected": True}}
        chosen = collector.select_peer(devices, "LOCAL-AAA", connections, "REMOTE-BB")
        self.assertEqual(chosen["deviceID"], "REMOTE-BBB")

    def test_configured_peer_absent_is_not_fallback(self):
        devices = [{"deviceID": "LOCAL-AAA"}, {"deviceID": "OTHER-CCC"}]
        connections = {"OTHER-CCC": {"connected": True}}
        self.assertIsNone(collector.select_peer(devices, "LOCAL-AAA", connections, "MISSING-XX"))

    def test_choose_folder_strict(self):
        folders = [{"id": "research-vault", "label": "Research Vault"}]
        self.assertEqual(collector.choose_folder(folders, "research-vault")["id"], "research-vault")
        self.assertEqual(collector.choose_folder(folders, None)["id"], "research-vault")
        with self.assertRaises(collector.PollError):
            collector.choose_folder(folders, "does-not-exist")
        with self.assertRaises(collector.PollError):
            collector.choose_folder([], None)


if __name__ == "__main__":
    unittest.main()
