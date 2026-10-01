import json
import os
import threading
import unittest
import urllib.error
import urllib.request

from fixtures import make_config, temp_dir

from library.api import build_server
from library.ingest import Ingestor


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = temp_dir()
        cls.config, cls.reports, cls.discord = make_config(cls.tmp)
        cls.config.bind_port = 0
        cls.ingest = Ingestor(cls.config)
        cls.ingest.run()
        cls.server = build_server(cls.config)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.ingest.catalog.close()

    def _get(self, path, method="GET", headers=None):
        url = "http://127.0.0.1:%d%s" % (self.port, path)
        request = urllib.request.Request(url, method=method, headers=headers or {})
        try:
            response = urllib.request.urlopen(request)
            return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def test_healthz(self):
        status, headers, body = self._get("/healthz")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["status"], "ok")
        self.assertGreaterEqual(payload["counts"]["documents_total"], 5)

    def test_search_metadata_only(self):
        status, _, body = self._get("/api/v1/search?q=%E8%8C%85%E5%8F%B0")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["kind"], "metadata_search")
        self.assertIn("Metadata only", payload["notice"])
        self.assertGreaterEqual(payload["total"], 1)

    def test_search_pagination_and_empty(self):
        status, _, body = self._get("/api/v1/search?page_size=1&page=2")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["page_size"], 1)
        self.assertEqual(len(payload["items"]), 1)
        status, _, body = self._get("/api/v1/search?symbol=ZZZZZZ")
        self.assertEqual(json.loads(body)["total"], 0)

    def test_document_detail(self):
        status, _, body = self._get("/api/v1/documents/reports/a1111111111111111111")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(len(payload["versions"]), 2)
        current = [v for v in payload["versions"] if v["is_current"]][0]
        self.assertEqual(current["version_id"], "b2222222222222222222")

    def test_file_full_body_matches_source(self):
        status, headers, body = self._get("/api/v1/files/reports/a1111111111111111111")
        self.assertEqual(status, 200)
        source_path = self.reports["root"] + "/" + self.reports["rel_two"]
        with open(source_path, "rb") as handle:
            self.assertEqual(body, handle.read())
        self.assertEqual(headers["Content-Type"], "application/pdf")
        self.assertIn("Accept-Ranges", headers)
        self.assertTrue(headers["ETag"])

    def test_file_range_and_head(self):
        path = "/api/v1/files/reports/a1111111111111111111"
        status, headers, body = self._get(path, headers={"Range": "bytes=0-9"})
        self.assertEqual(status, 206)
        self.assertEqual(len(body), 10)
        self.assertTrue(headers["Content-Range"].startswith("bytes 0-9/"))
        status, headers, body = self._get(path, method="HEAD")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")
        with open(self.reports["root"] + "/" + self.reports["rel_two"], "rb") as handle:
            self.assertEqual(int(headers["Content-Length"]), len(handle.read()))

    def test_invalid_range_416(self):
        status, headers, _ = self._get("/api/v1/files/reports/a1111111111111111111",
                                       headers={"Range": "bytes=99999-100000"})
        self.assertEqual(status, 416)
        self.assertTrue(headers["Content-Range"].startswith("bytes */"))

    def test_etag_not_modified(self):
        status, headers, _ = self._get("/api/v1/files/reports/a1111111111111111111")
        etag = headers["ETag"]
        status, _, _ = self._get("/api/v1/files/reports/a1111111111111111111",
                                 headers={"If-None-Match": etag})
        self.assertEqual(status, 304)

    def test_unknown_and_traversal_doc_ids(self):
        for path in ("/api/v1/documents/reports/..%2f..%2fetc",
                     "/api/v1/files/reports/does-not-exist",
                     "/api/v1/files/badsource/whatever"):
            status, _, _ = self._get(path)
            self.assertIn(status, (400, 404))

    def test_historic_version_served(self):
        status, _, body = self._get(
            "/api/v1/files/reports/a1111111111111111111?version=b1111111111111111111")
        self.assertEqual(status, 200)
        with open(self.reports["root"] + "/" + self.reports["rel_one"], "rb") as handle:
            self.assertEqual(body, handle.read())

    def test_non_pdf_types(self):
        # PNG and extension-less TXT from the discord fixture.
        for doc_id, content_type in (("1500000000000000001_0", "image/png"),
                                     ("1500000000000000002_0", "text/plain; charset=utf-8")):
            status, headers, _ = self._get("/api/v1/files/discord/" + doc_id)
            self.assertEqual(status, 200)
            self.assertEqual(headers["Content-Type"], content_type)


class ApiContentFailClosedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = temp_dir()
        cls.config, cls.reports, cls.discord = make_config(cls.tmp)
        cls.config.bind_port = 0
        cls.ingest = Ingestor(cls.config)
        cls.ingest.run()
        cls.server = build_server(cls.config)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.ingest.close()

    def _get(self, path, headers=None, method="GET"):
        url = "http://127.0.0.1:%d%s" % (self.port, path)
        request = urllib.request.Request(url, headers=headers or {}, method=method)
        try:
            response = urllib.request.urlopen(request)
            return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def test_content_change_after_ingest_fails_closed(self):
        path = "/api/v1/files/reports/a1111111111111111111"
        status, headers, body = self._get(path)
        self.assertEqual(status, 200)
        old_etag = headers.get("ETag")
        # Timestamp-preserving, equal-size replacement after ingestion.
        target = os.path.join(self.reports["root"], self.reports["rel_two"])
        with open(target, "rb") as handle:
            original = handle.read()
        st = os.stat(target)
        replacement = bytes((b ^ 0xFF) for b in original)  # same length, different bytes
        with open(target, "wb") as handle:
            handle.write(replacement)
        os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns))  # preserve mtime
        for method in ("GET", "HEAD"):
            status2, headers2, _ = self._get(path, method=method)
            self.assertEqual(status2, 409, "%s should fail closed" % method)
        status3, _, _ = self._get(path, headers={"Range": "bytes=0-9"})
        self.assertNotEqual(status3, 206)
        status4, _, _ = self._get(path, headers={"If-None-Match": old_etag})
        self.assertNotEqual(status4, 304)

    def test_explicit_old_version_also_fails_closed_after_tamper(self):
        path = "/api/v1/files/reports/a1111111111111111111?version=b1111111111111111111"
        status, _, body = self._get(path)
        self.assertEqual(status, 200)
        target = os.path.join(self.reports["root"], self.reports["rel_one"])
        with open(target, "rb") as handle:
            original = handle.read()
        st = os.stat(target)
        with open(target, "wb") as handle:
            handle.write(bytes((b ^ 0xFF) for b in original))
        os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns))
        status2, _, _ = self._get(path)
        self.assertEqual(status2, 409)


class ApiDiagnosticsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = temp_dir()
        cls.config, cls.reports, cls.discord = make_config(cls.tmp)
        cls.config.bind_port = 0
        cls.sentinel = os.path.join(cls.tmp, "SECRET-leak-path-9f3a", "documents.jsonl")
        cls.config.sources["discord"].extra["index"] = cls.sentinel
        cls.ingest = Ingestor(cls.config)
        cls.ingest.run()
        cls.server = build_server(cls.config)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.ingest.close()

    def _get(self, path):
        with urllib.request.urlopen("http://127.0.0.1:%d%s" % (self.port, path)) as response:
            return response.read()

    def test_diagnostics_do_not_leak_paths_or_secrets(self):
        health = self._get("/healthz").decode("utf-8")
        sources = self._get("/api/v1/sources").decode("utf-8")
        for body in (health, sources):
            self.assertNotIn("SECRET-leak-path-9f3a", body)
            self.assertNotIn(self.sentinel, body)
        payload = json.loads(health)
        self.assertEqual(payload["status"], "degraded")
        self.assertFalse(payload["sources"]["discord"]["healthy"])
        self.assertTrue(payload["sources"]["reports"]["healthy"])


if __name__ == "__main__":
    unittest.main()
