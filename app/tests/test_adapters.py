import hashlib
import json
import os
import unittest

from fixtures import build_discord_fixture, build_reports_fixture, temp_dir

from library.adapters import build_adapter
from library.config import SourceConfig


class ReportsAdapterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir()
        self.fixture = build_reports_fixture(self.tmp)
        self.source = SourceConfig.from_dict("reports", {
            "type": "reports_archive", "root": self.fixture["root"],
            "db": self.fixture["db"], "path_prefix": "/app/reports", "read_mode": "direct",
        })

    def test_scan_statuses_and_versions(self):
        result = build_adapter(self.source).scan()
        docs = {d.doc_id: d for d in result.documents}
        self.assertEqual(len(docs), 3)
        report = docs["a1111111111111111111"]
        self.assertTrue(report.available)
        self.assertEqual(report.status, "done")
        self.assertEqual(report.report_period, "2026-06-30")
        self.assertEqual(len(report.versions), 2)
        current = report.current_version()
        self.assertEqual(current.version_id, "b2222222222222222222")
        self.assertEqual(current.sha256, self.fixture["sha_two"])
        self.assertEqual(len(result.documents), 3)

    def test_discovered_not_available_and_unknown_period_preserved(self):
        result = build_adapter(self.source).scan()
        docs = {d.doc_id: d for d in result.documents}
        discovered = docs["e1111111111111111111"]
        self.assertFalse(discovered.available)
        self.assertEqual(discovered.status, "discovered")
        hk = docs["c1111111111111111111"]
        self.assertIsNone(hk.report_period)
        self.assertEqual(result.counts["available"], 2)
        self.assertEqual(result.counts["status_counts"]["discovered"], 1)

    def test_concurrent_wal_writer_is_read_safely(self):
        # The live archive is a WAL database being written concurrently; a scan
        # must read one consistent snapshot and never raw-copy the file trio.
        import sqlite3
        import threading
        import time
        db = self.fixture["db"]
        setup = sqlite3.connect(db)
        setup.execute("PRAGMA journal_mode=WAL")
        setup.commit()
        setup.close()
        stop = {"v": False}

        def writer():
            conn = sqlite3.connect(db)
            i = 0
            try:
                while not stop["v"]:
                    i += 1
                    conn.execute(
                        "INSERT OR REPLACE INTO manifest VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        ("z%019d" % i, "US", "ZZ", "s", "u", "t", "10-Q", "10-Q",
                         "2026-01-01", "2025-12-31", "source_field", "en", 0, None, None,
                         "discovered", None))
                    conn.commit()
                    time.sleep(0.001)
            finally:
                conn.close()

        thread = threading.Thread(target=writer, daemon=True)
        thread.start()
        try:
            for _ in range(5):
                result = build_adapter(self.source).scan()
                self.assertGreaterEqual(result.counts["documents"], 3)
        finally:
            stop["v"] = True
            thread.join(timeout=3)

    def test_display_title_decoded(self):
        result = build_adapter(self.source).scan()
        docs = {d.doc_id: d for d in result.documents}
        self.assertIn("茅台", docs["a1111111111111111111"].display_title)

    def test_hash_mismatch_keeps_expected_identity_and_marks_conflict(self):
        # Corrupt the DB-declared sha; identity must stay as declared and the
        # version becomes non-servable (conflict), never silently rewritten.
        import sqlite3
        conn = sqlite3.connect(self.fixture["db"])
        conn.execute("UPDATE artifacts SET sha256='deadbeef' WHERE artifact_id='b2222222222222222222'")
        conn.commit()
        conn.close()
        result = build_adapter(self.source).scan()
        docs = {d.doc_id: d for d in result.documents}
        current = docs["a1111111111111111111"].current_version()
        self.assertEqual(current.sha256, "deadbeef")
        self.assertEqual(current.observed_sha256, self.fixture["sha_two"])
        self.assertEqual(current.state, "conflict")
        self.assertFalse(docs["a1111111111111111111"].available)
        self.assertEqual(result.counts["version_conflicts"], 1)

    def _set_current(self, value_sql):
        import sqlite3
        conn = sqlite3.connect(self.fixture["db"])
        conn.execute("UPDATE manifest SET current_artifact_id=%s WHERE report_id='a1111111111111111111'" % value_sql)
        conn.commit()
        conn.close()

    def test_null_current_is_not_available(self):
        self._set_current("NULL")
        result = build_adapter(self.source).scan()
        docs = {d.doc_id: d for d in result.documents}
        self.assertIsNone(docs["a1111111111111111111"].current_version())
        self.assertFalse(docs["a1111111111111111111"].available)
        # historic versions are retained
        self.assertEqual(len(docs["a1111111111111111111"].versions), 2)

    def test_invalid_current_id_is_not_available(self):
        self._set_current("'does-not-exist'")
        result = build_adapter(self.source).scan()
        docs = {d.doc_id: d for d in result.documents}
        self.assertIsNone(docs["a1111111111111111111"].current_version())
        self.assertFalse(docs["a1111111111111111111"].available)

    def test_missing_current_file_not_available(self):
        os.remove(os.path.join(self.fixture["root"], self.fixture["rel_two"]))
        result = build_adapter(self.source).scan()
        docs = {d.doc_id: d for d in result.documents}
        current = docs["a1111111111111111111"].current_version()
        self.assertEqual(current.state, "missing")
        self.assertFalse(docs["a1111111111111111111"].available)
        # the historic, still-present version is retained
        historic = [v for v in docs["a1111111111111111111"].versions if v.version_id == "b1111111111111111111"][0]
        self.assertEqual(historic.state, "ready")


class DiscordAdapterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir()
        self.fixture = build_discord_fixture(self.tmp)
        self.source = SourceConfig.from_dict("discord", {
            "type": "discord_export", "root": self.fixture["root"],
        })

    def test_scan_all_records(self):
        result = build_adapter(self.source).scan()
        self.assertEqual(len(result.documents), 3)
        self.assertEqual(result.counts["available"], 3)
        self.assertEqual(result.counts["ext_counts"]["pdf"], 1)
        docs = {d.doc_id: d for d in result.documents}
        pdf_doc = docs["1500000000000000000_0"]
        self.assertEqual(pdf_doc.summary, "source message summary body")
        self.assertEqual(pdf_doc.metadata["summary_kind"], "source_message_summary")
        self.assertEqual(pdf_doc.current_version().version_id, self.fixture["sha_pdf"])
        self.assertTrue(pdf_doc.current_version().sha256)

    def test_incomplete_jsonl_raises(self):
        index = os.path.join(self.fixture["root"], "index", "documents.jsonl")
        with open(index, "a", encoding="utf-8") as handle:
            handle.write("{not valid json\n")
        with self.assertRaises(Exception):
            build_adapter(self.source).scan()

    def test_missing_attachment_not_available(self):
        os.remove(os.path.join(self.fixture["root"], self.fixture["rel_pdf"]))
        result = build_adapter(self.source).scan()
        docs = {d.doc_id: d for d in result.documents}
        self.assertFalse(docs["1500000000000000000_0"].available)

    def test_same_size_overwrite_detected(self):
        doc_id = "1500000000000000000_0"
        path = os.path.join(self.fixture["root"], self.fixture["rel_pdf"])
        with open(path, "rb") as handle:
            original = handle.read()
        old_mtime = os.stat(path).st_mtime_ns
        cache = {(doc_id, self.fixture["sha_pdf"]): {
            "observed_sha256": self.fixture["sha_pdf"], "bytes": len(original), "mtime_ns": old_mtime}}
        # Replace with different bytes of the SAME length, index left untouched.
        replacement = original.upper()
        self.assertEqual(len(replacement), len(original))
        self.assertNotEqual(replacement, original)
        with open(path, "wb") as handle:
            handle.write(replacement)
        result = build_adapter(self.source, existing_versions=cache).scan()
        docs = {d.doc_id: d for d in result.documents}
        doc = docs[doc_id]
        self.assertFalse(doc.available)
        self.assertEqual(doc.current_version().state, "conflict")
        self.assertNotEqual(doc.current_version().observed_sha256, self.fixture["sha_pdf"])
        self.assertEqual(result.counts["conflicts"], 1)

    def test_index_hash_mismatch_marks_conflict(self):
        index = os.path.join(self.fixture["root"], "index", "documents.jsonl")
        with open(index, "r", encoding="utf-8") as handle:
            lines = [json.loads(line) for line in handle if line.strip()]
        lines[0]["sha256"] = "deadbeef"
        with open(index, "w", encoding="utf-8") as handle:
            for record in lines:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        result = build_adapter(self.source).scan()
        docs = {d.doc_id: d for d in result.documents}
        doc = docs["1500000000000000000_0"]
        self.assertFalse(doc.available)
        self.assertEqual(doc.current_version().sha256, "deadbeef")

    def test_metadata_only_update_keeps_available(self):
        # Change the summary in the index but not the attachment bytes.
        index = os.path.join(self.fixture["root"], "index", "documents.jsonl")
        with open(index, "r", encoding="utf-8") as handle:
            lines = [json.loads(line) for line in handle if line.strip()]
        lines[0]["summary"] = "updated source summary"
        with open(index, "w", encoding="utf-8") as handle:
            for record in lines:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        doc_id = "1500000000000000000_0"
        result = build_adapter(self.source).scan()
        docs = {d.doc_id: d for d in result.documents}
        doc = docs[doc_id]
        self.assertTrue(doc.available)
        self.assertEqual(doc.summary, "updated source summary")
        # every attachment is hash-verified fresh (size+mtime is not identity)
        self.assertEqual(result.counts["hash_hashed_now"], 3)

    def test_same_size_preserved_mtime_overwrite_detected(self):
        doc_id = "1500000000000000000_0"
        path = os.path.join(self.fixture["root"], self.fixture["rel_pdf"])
        with open(path, "rb") as handle:
            original = handle.read()
        st = os.stat(path)
        replacement = original.upper()
        self.assertEqual(len(replacement), len(original))
        with open(path, "wb") as handle:
            handle.write(replacement)
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))  # restore mtime
        result = build_adapter(self.source).scan()
        docs = {d.doc_id: d for d in result.documents}
        doc = docs[doc_id]
        self.assertFalse(doc.available)
        self.assertEqual(doc.current_version().state, "conflict")
        self.assertNotEqual(doc.current_version().observed_sha256, self.fixture["sha_pdf"])

    def test_real_change_with_index_update_becomes_ready(self):
        doc_id = "1500000000000000000_0"
        path = os.path.join(self.fixture["root"], self.fixture["rel_pdf"])
        new_bytes = b"%PDF-1.7 discord report v2"
        new_sha = hashlib.sha256(new_bytes).hexdigest()
        with open(path, "wb") as handle:
            handle.write(new_bytes)
        index = os.path.join(self.fixture["root"], "index", "documents.jsonl")
        with open(index, "r", encoding="utf-8") as handle:
            lines = [json.loads(line) for line in handle if line.strip()]
        lines[0]["sha256"] = new_sha
        lines[0]["file_size"] = len(new_bytes)
        with open(index, "w", encoding="utf-8") as handle:
            for record in lines:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        result = build_adapter(self.source).scan()
        docs = {d.doc_id: d for d in result.documents}
        doc = docs[doc_id]
        self.assertTrue(doc.available)
        self.assertEqual(doc.current_version().version_id, new_sha)
        self.assertEqual(doc.current_version().state, "ready")


if __name__ == "__main__":
    unittest.main()
