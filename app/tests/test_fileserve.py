import os
import unittest

from fixtures import make_config, temp_dir

from library.fileserve import PathNotAllowed, content_disposition, parse_range, resolve_version_path
from library.models import Version


class ContentDispositionTest(unittest.TestCase):
    def test_ascii_fallback_and_utf8_star(self):
        header = content_disposition("中期報告 2023.pdf", inline=True)
        header.encode("latin-1")  # must be a valid HTTP header (ASCII/latin-1)
        self.assertIn("filename*=UTF-8''", header)
        self.assertIn("inline", header)
        # the plain filename= must not contain raw non-ascii
        plain = header.split("filename=\"", 1)[1].split("\"", 1)[0]
        plain.encode("ascii")

    def test_download_disposition(self):
        header = content_disposition("9.pdf", inline=False)
        self.assertTrue(header.startswith("attachment;"))
        self.assertIn('filename="9.pdf"', header)


class RangeTest(unittest.TestCase):
    def test_ranges(self):
        self.assertEqual(parse_range(None, 100), ("full", None, None))
        self.assertEqual(parse_range("bytes=0-9", 100), ("range", 0, 9))
        self.assertEqual(parse_range("bytes=10-", 100), ("range", 10, 99))
        self.assertEqual(parse_range("bytes=-10", 100), ("range", 90, 99))
        self.assertEqual(parse_range("bytes=0-999", 100), ("range", 0, 99))
        self.assertEqual(parse_range("bytes=100-200", 100)[0], "invalid")
        self.assertEqual(parse_range("bytes=5-1", 100)[0], "invalid")
        self.assertEqual(parse_range("bytes=abc-def", 100)[0], "invalid")
        self.assertEqual(parse_range("chunks=0-1", 100)[0], "invalid")
        self.assertEqual(parse_range("bytes=0-1,3-4", 100)[0], "invalid")


class PathSafetyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir()
        self.config, self.reports, self.discord = make_config(self.tmp)

    def _version(self, rel):
        return Version(version_id="x", rel_path=rel)

    def test_legal_path_resolves(self):
        version = self._version(self.reports["rel_one"])
        path = resolve_version_path(self.config, "reports", version)
        self.assertTrue(os.path.isfile(path))

    def test_traversal_rejected(self):
        for rel in ("../secret.txt", "CN/../../etc/passwd", "/etc/passwd", "..\\secret"):
            with self.assertRaises(PathNotAllowed):
                resolve_version_path(self.config, "reports", self._version(rel))

    def test_unknown_source_rejected(self):
        with self.assertRaises(PathNotAllowed):
            resolve_version_path(self.config, "nope", self._version("x"))

    def test_symlink_escape_rejected(self):
        link = os.path.join(self.reports["root"], "escape.pdf")
        try:
            os.symlink(os.path.join(self.tmp, "outside-secret.txt"), link)
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest("symlinks not available")
        with open(os.path.join(self.tmp, "outside-secret.txt"), "w", encoding="utf-8") as handle:
            handle.write("secret")
        with self.assertRaises(PathNotAllowed):
            resolve_version_path(self.config, "reports", self._version("escape.pdf"))


if __name__ == "__main__":
    unittest.main()
