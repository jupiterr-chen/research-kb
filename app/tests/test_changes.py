import unittest

from fixtures import temp_dir

from library.catalog import Catalog
from library.models import Document, Version


def make_doc(doc_id, title="标题", available=True, state="ready", versions=None):
    if versions is None:
        versions = [Version(
            version_id="v-" + doc_id, sha256="sha-" + doc_id, bytes=10,
            media_type="application/pdf", is_current=True, state=state,
        )]
    return Document(
        source="reports", doc_id=doc_id, title=title, display_title=title,
        available=available, status="done", versions=versions,
    )


class ChangeLedgerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir()
        self.catalog = Catalog(self.tmp + "/catalog.sqlite3")

    def tearDown(self):
        self.catalog.close()

    def _commit(self, docs):
        return self.catalog.commit_snapshot("reports", docs, {}, {})

    def _reset_to_upgrade_state(self):
        # Emulate a catalog created before the ledger existed: documents are
        # present but no ledger baseline/metadata.
        self.catalog._conn.execute("DELETE FROM changes")
        self.catalog._conn.execute(
            "DELETE FROM meta WHERE key LIKE 'change_ledger_initialized%' "
            "OR key LIKE 'change_ledger_seeded_at%'")
        self.catalog._conn.commit()

    def _kinds(self):
        return [c["kind"] for c in self.catalog.recent_changes(50)]

    def test_fresh_install_seeds_baseline_and_imports(self):
        self._commit([make_doc("a"), make_doc("b")])
        kinds = self._kinds()
        self.assertIn("ledger_baseline", kinds)
        self.assertEqual(kinds.count("imported"), 2)

    def test_upgrade_baseline_does_not_report_existing_as_imported(self):
        self._commit([make_doc("a"), make_doc("b")])
        self._reset_to_upgrade_state()
        self._commit([make_doc("a"), make_doc("b")])
        kinds = self._kinds()
        self.assertIn("ledger_baseline", kinds)
        self.assertNotIn("imported", kinds)
        self.assertNotIn("metadata_updated", kinds)

    def test_noop_scan_is_zero_changes(self):
        self._commit([make_doc("a"), make_doc("b")])
        before = self.catalog.changes_count()
        self._commit([make_doc("a"), make_doc("b")])
        self.assertEqual(self.catalog.changes_count(), before)

    def test_new_document_reported(self):
        self._commit([make_doc("a")])
        before = self.catalog.changes_count()
        self._commit([make_doc("a"), make_doc("b")])
        self.assertEqual(self.catalog.changes_count(), before + 1)
        latest = self.catalog.recent_changes(1)[0]
        self.assertEqual(latest["kind"], "imported")
        self.assertEqual(latest["doc_id"], "b")

    def test_metadata_only_change(self):
        self._commit([make_doc("a", title="旧标题")])
        self._commit([make_doc("a", title="新标题")])
        latest = self.catalog.recent_changes(1)[0]
        self.assertEqual(latest["kind"], "metadata_updated")
        self.assertIn("title", latest["detail"]["fields"])

    def test_new_content_version(self):
        self._commit([make_doc("a")])
        versions = [
            Version(version_id="v-a", sha256="sha-a", bytes=10, media_type="application/pdf",
                    is_current=False, state="ready"),
            Version(version_id="v-a2", sha256="sha-a2", bytes=12, media_type="application/pdf",
                    is_current=True, state="ready"),
        ]
        self._commit([make_doc("a", versions=versions)])
        kinds = [c["kind"] for c in self.catalog.recent_changes(5)]
        self.assertIn("new_version", kinds)
        self.assertIn("current_changed", kinds)
        new_version = next(c for c in self.catalog.recent_changes(5) if c["kind"] == "new_version")
        self.assertIn("v-a2", new_version["detail"]["version_ids"])

    def test_missing_then_recovered(self):
        self._commit([make_doc("a")])
        self._commit([make_doc("a", available=False, state="missing")])
        self.assertEqual(self.catalog.recent_changes(1)[0]["kind"], "became_unavailable")
        self._commit([make_doc("a", available=True, state="ready")])
        self.assertEqual(self.catalog.recent_changes(1)[0]["kind"], "recovered")

    def test_failed_source_preserves_catalog_and_records_freshness(self):
        self._commit([make_doc("a")])
        self.catalog.record_source_error("reports", "SourceError: source scan failed")
        state = self.catalog.source_state("reports")
        self.assertEqual(state["last_error"], "SourceError: source scan failed")
        self.assertIsNotNone(state["last_error_at"])
        self.assertIsNotNone(state["last_attempt_at"])
        self.assertIsNotNone(state["last_ok_at"])
        self.assertEqual(self.catalog.counts()["documents_total"], 1)

    def test_render_failure_persisted(self):
        self.catalog.record_render_result(False, "RuntimeError: render failed", None)
        render = self.catalog.last_render()
        self.assertFalse(render["ok"])
        self.assertIn("render failed", render["error"])

    def test_changes_survive_restart(self):
        self._commit([make_doc("a")])
        self._commit([make_doc("a"), make_doc("b")])
        expected = self.catalog.changes_count()
        self.catalog.close()
        reopened = Catalog(self.tmp + "/catalog.sqlite3")
        try:
            self.assertGreaterEqual(reopened.changes_count(), expected)
            self.assertTrue(reopened.recent_changes(5))
        finally:
            reopened.close()

    def test_source_breakdown_counts(self):
        self._commit([make_doc("a"), make_doc("b", available=False, state="missing")])
        breakdown = self.catalog.source_breakdown("reports")
        self.assertEqual(breakdown["documents_total"], 2)
        self.assertEqual(breakdown["documents_available"], 1)
        self.assertEqual(breakdown["version_state_counts"].get("missing"), 1)


if __name__ == "__main__":
    unittest.main()
