import os
import unittest

from fixtures import make_config, temp_dir

from library.adapters import build_adapter
from library.catalog import Catalog
from library.markdown import render_vault


class MarkdownTest(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir()
        self.config, self.reports, self.discord = make_config(self.tmp)
        self.catalog = Catalog(self.config.catalog_db)
        for source, source_config in self.config.sources.items():
            scan = build_adapter(source_config).scan()
            self.catalog.commit_snapshot(source, scan.documents, scan.snapshot, scan.counts)

    def tearDown(self):
        self.catalog.close()

    def _card_path(self, source, doc_id):
        return os.path.join(self.config.vault_dir, "资料目录",
                            "财报" if source == "reports" else "研报",
                            "%s_%s.md" % (source, doc_id))

    def test_render_is_deterministic_and_mtime_stable(self):
        first = render_vault(self.config, self.catalog)
        self.assertGreater(first["cards_written"], 0)
        card = self._card_path("reports", "a1111111111111111111")
        self.assertTrue(os.path.exists(card))
        mtime_before = os.path.getmtime(card)
        with open(card, "rb") as handle:
            content_before = handle.read()
        second = render_vault(self.config, self.catalog)
        self.assertEqual(second["cards_written"], 0)
        self.assertEqual(second["cards_unchanged"], first["cards_written"])
        self.assertEqual(os.path.getmtime(card), mtime_before)
        with open(card, "rb") as handle:
            self.assertEqual(handle.read(), content_before)

    def test_human_note_never_overwritten(self):
        render_vault(self.config, self.catalog)
        human_dir = os.path.join(self.config.vault_dir, "公司研究")
        note = os.path.join(human_dir, "我的笔记.md")
        with open(note, "w", encoding="utf-8") as handle:
            handle.write("HUMAN CONTENT 人工笔记\n")
        render_vault(self.config, self.catalog)
        with open(note, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "HUMAN CONTENT 人工笔记\n")

    def test_card_frontmatter_and_unknown_period(self):
        render_vault(self.config, self.catalog)
        card = self._card_path("reports", "c1111111111111111111")
        with open(card, "r", encoding="utf-8") as handle:
            text = handle.read()
        self.assertTrue(text.startswith("---"))
        self.assertIn("report_period: \"\"", text)
        self.assertIn("未知", text)

    def test_index_and_home_generated(self):
        render_vault(self.config, self.catalog)
        self.assertTrue(os.path.exists(os.path.join(self.config.vault_dir, "首页.md")))
        self.assertTrue(os.path.exists(os.path.join(self.config.vault_dir, "资料目录", "财报.md")))
        self.assertTrue(os.path.exists(os.path.join(self.config.vault_dir, "资料目录", "研报.md")))

    def test_source_strings_are_escaped_as_text(self):
        from library.models import Document, Version
        malicious = Document(
            source="discord", doc_id="escape1", status="done", available=True,
            title='<img src="https://external.invalid/track">', display_title='<img src="https://external.invalid/track">',
            summary="*not bold* <script>alert(1)</script> [link](javascript:alert(1))",
            versions=[Version(version_id="v1", sha256="h", bytes=1, media_type="text/plain",
                              rel_path="attachments/x", is_current=True, state="ready")],
        )
        self.catalog.commit_snapshot("discord", [malicious], {}, {})
        render_vault(self.config, self.catalog)
        card = os.path.join(self.config.vault_dir, "资料目录", "研报", "discord_escape1.md")
        with open(card, "r", encoding="utf-8") as handle:
            text = handle.read()
        self.assertNotIn("<img", text.split("---", 2)[2])
        self.assertNotIn("<script>", text.split("---", 2)[2])
        self.assertIn("&lt;img", text)

    def test_unsafe_source_url_not_rendered_as_link(self):
        from library.models import Document, Version
        doc = Document(
            source="discord", doc_id="urltest", status="done", available=True, title="t",
            source_url="javascript:alert(document.cookie)",
            versions=[Version(version_id="v1", sha256="h", bytes=1, media_type="text/plain",
                              rel_path="attachments/x", is_current=True, state="ready")],
        )
        self.catalog.commit_snapshot("discord", [doc], {}, {})
        render_vault(self.config, self.catalog)
        card = os.path.join(self.config.vault_dir, "资料目录", "研报", "discord_urltest.md")
        with open(card, "r", encoding="utf-8") as handle:
            text = handle.read()
        self.assertNotIn("[来源 URL](javascript:", text)
        self.assertIn("非 http/https", text)

    def test_malicious_doc_id_cannot_escape_generated_area(self):
        from library.models import Document, Version
        render_vault(self.config, self.catalog)  # create human README
        readme = os.path.join(self.config.vault_dir, "公司研究", "README.md")
        with open(readme, "rb") as handle:
            before = handle.read()
        bad = Document(
            source="discord", doc_id="x/../../../公司研究/README",
            title="bad", display_title="bad", status="done", available=True,
            versions=[Version(version_id="v1", sha256="h", bytes=1, media_type="text/plain",
                              rel_path="attachments/x", is_current=True, state="ready")],
        )
        self.catalog.commit_snapshot("discord", [bad], {}, {})
        render_vault(self.config, self.catalog)
        with open(readme, "rb") as handle:
            self.assertEqual(handle.read(), before)
        # the card exists only under the generated area
        generated = os.path.join(self.config.vault_dir, "资料目录")
        found = []
        for root, _dirs, files in os.walk(generated):
            for name in files:
                if name.startswith("discord_") and "README" not in name:
                    found.append(os.path.join(root, name))
        self.assertTrue(any("x_.._" in os.path.basename(p) or "discord_" in os.path.basename(p) for p in found))
        self.assertFalse(os.path.exists(os.path.join(self.config.vault_dir, "资料目录", "..", "..", "公司研究", "README")))

    def test_unavailable_transition_updates_card_not_deletes(self):
        from library.models import Document, Version
        doc = Document(
            source="discord", doc_id="transition1", status="done", available=True, title="t",
            versions=[Version(version_id="v1", sha256="h", bytes=1, media_type="text/plain",
                              rel_path="attachments/x", is_current=True, state="ready")],
        )
        self.catalog.commit_snapshot("discord", [doc], {}, {})
        render_vault(self.config, self.catalog)
        card = os.path.join(self.config.vault_dir, "资料目录", "研报", "discord_transition1.md")
        with open(card, "r", encoding="utf-8") as handle:
            self.assertIn('available: "true"', handle.read())
        gone = Document(
            source="discord", doc_id="transition1", status="missing", available=False, title="t",
            versions=[Version(version_id="v1", sha256="h", bytes=1, media_type="text/plain",
                              rel_path="attachments/x", is_current=True, state="missing")],
        )
        self.catalog.commit_snapshot("discord", [gone], {}, {})
        render_vault(self.config, self.catalog)
        self.assertTrue(os.path.exists(card))
        with open(card, "r", encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn('available: "false"', text)
        self.assertIn("当前无可用原文", text)


if __name__ == "__main__":
    unittest.main()
