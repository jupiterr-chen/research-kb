import os
import unittest

from fixtures import build_discord_fixture, build_reports_fixture, make_config, temp_dir

from library.catalog import Catalog


class CatalogTest(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir()
        self.config, self.reports, self.discord = make_config(self.tmp)
        self.catalog = Catalog(self.config.catalog_db)

    def tearDown(self):
        self.catalog.close()

    def _commit_reports(self):
        from library.adapters import build_adapter
        scan = build_adapter(self.config.sources["reports"]).scan()
        return scan

    def test_commit_and_query(self):
        scan = self._commit_reports()
        self.catalog.commit_snapshot("reports", scan.documents, scan.snapshot, scan.counts)
        self.assertEqual(self.catalog.counts()["documents_total"], 3)
        found = self.catalog.search(symbol="600519")
        self.assertEqual(found["total"], 1)
        doc = self.catalog.get_document("reports", "a1111111111111111111")
        self.assertEqual(len(doc.versions), 2)
        self.assertEqual(self.catalog.get_version("reports", "a1111111111111111111", None).is_current, True)

    def test_failed_source_keeps_previous_catalog(self):
        scan = self._commit_reports()
        self.catalog.commit_snapshot("reports", scan.documents, scan.snapshot, scan.counts)
        before = self.catalog.counts()["documents_total"]
        # Simulate a broken snapshot: record an error without committing.
        self.catalog.record_source_error("reports", "simulated read failure")
        state = self.catalog.source_state("reports")
        self.assertEqual(state["last_error"], "simulated read failure")
        self.assertEqual(self.catalog.counts()["documents_total"], before)

    def test_absent_document_retained_but_unavailable(self):
        scan = self._commit_reports()
        self.catalog.commit_snapshot("reports", scan.documents, scan.snapshot, scan.counts)
        # Second snapshot omits one report entirely (window shrink).
        reduced = [d for d in scan.documents if d.doc_id != "c1111111111111111111"]
        self.catalog.commit_snapshot("reports", reduced, {}, {})
        doc = self.catalog.get_document("reports", "c1111111111111111111")
        self.assertIsNotNone(doc)
        self.assertFalse(doc.available)
        self.assertEqual(doc.status, "not_in_snapshot")
        self.assertEqual(len(doc.versions), 1)

    def test_exactly_one_current_after_new_current(self):
        from library.models import Version
        scan = self._commit_reports()
        self.catalog.commit_snapshot("reports", scan.documents, scan.snapshot, scan.counts)
        doc = next(d for d in scan.documents if d.doc_id == "a1111111111111111111")
        # New authoritative version only; the old one is not in the incoming list.
        new_version = Version(version_id="b9999999999999999999", sha256="new", bytes=1,
                              media_type="application/pdf", rel_path=self.reports["rel_two"],
                              is_current=True, state="ready")
        doc.versions = [new_version]
        self.catalog.commit_snapshot("reports", scan.documents, scan.snapshot, scan.counts)
        versions = self.catalog.all_versions("reports", doc.doc_id)
        current = [v for v in versions if v.is_current]
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0].version_id, "b9999999999999999999")
        # historic rows retained but no longer current
        historic = [v for v in versions if v.version_id == "b2222222222222222222"]
        self.assertTrue(historic)
        self.assertFalse(historic[0].is_current)

    def test_metadata_only_update_changes_no_version(self):
        scan = self._commit_reports()
        self.catalog.commit_snapshot("reports", scan.documents, scan.snapshot, scan.counts)
        before = self.catalog.get_document("reports", "a1111111111111111111")
        before_versions = len(before.versions)
        for doc in scan.documents:
            if doc.doc_id == "a1111111111111111111":
                doc.title = "edited title only"
                doc.display_title = "edited title only"
        self.catalog.commit_snapshot("reports", scan.documents, scan.snapshot, scan.counts)
        after = self.catalog.get_document("reports", "a1111111111111111111")
        self.assertEqual(after.title, "edited title only")
        self.assertEqual(len(after.versions), before_versions)

    def test_expected_identity_not_rewritten_on_same_artifact_id(self):
        scan = self._commit_reports()
        self.catalog.commit_snapshot("reports", scan.documents, scan.snapshot, scan.counts)
        doc = next(d for d in scan.documents if d.doc_id == "a1111111111111111111")
        current = doc.current_version()
        original_sha, original_bytes = current.sha256, current.bytes
        # Source reuses the same artifact_id but now declares different content.
        current.sha256 = "newdeclaredhash"
        current.observed_sha256 = "newdeclaredhash"
        current.bytes = 999
        current.state = "ready"
        doc.available = True
        self.catalog.commit_snapshot("reports", scan.documents, scan.snapshot, scan.counts)
        after = self.catalog.get_version("reports", doc.doc_id, "b2222222222222222222")
        self.assertEqual(after.sha256, original_sha)      # identity preserved
        self.assertEqual(after.bytes, original_bytes)
        self.assertEqual(after.state, "conflict")
        self.assertFalse(self.catalog.get_document("reports", doc.doc_id).available)
        row = self.catalog._conn.execute(
            "SELECT declared_sha256 FROM versions WHERE source='reports' AND doc_id=? AND version_id='b2222222222222222222'",
            (doc.doc_id,)).fetchone()
        self.assertEqual(row["declared_sha256"], "newdeclaredhash")  # diagnostic kept

    def test_version_change_detected_and_history_kept(self):
        scan = self._commit_reports()
        self.catalog.commit_snapshot("reports", scan.documents, scan.snapshot, scan.counts)
        # New content version appended for the same report.
        doc = next(d for d in scan.documents if d.doc_id == "a1111111111111111111")
        from library.models import Version
        doc.versions.append(Version(
            version_id="b3333333333333333333", sha256="newhash", bytes=10,
            media_type="application/pdf", rel_path=self.reports["rel_one"], is_current=False,
        ))
        for v in doc.versions:
            v.is_current = (v.version_id == "b3333333333333333333")
        self.catalog.commit_snapshot("reports", scan.documents, scan.snapshot, scan.counts)
        versions = self.catalog.all_versions("reports", doc.doc_id)
        self.assertEqual(len(versions), 3)
        self.assertEqual(self.catalog.get_version("reports", doc.doc_id, None).version_id, "b3333333333333333333")


if __name__ == "__main__":
    unittest.main()
