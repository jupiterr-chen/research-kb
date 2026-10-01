import json
import threading
import unittest
import urllib.error
import urllib.request

from fixtures import make_config, temp_dir

from library.api import build_server
from library.dashboard import DASHBOARD_JS
from library.ingest import Ingestor


class DashboardTest(unittest.TestCase):
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

    def _get(self, path, method="GET"):
        url = "http://127.0.0.1:%d%s" % (self.port, path)
        request = urllib.request.Request(url, method=method)
        try:
            response = urllib.request.urlopen(request)
            return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def test_root_is_chinese_dashboard(self):
        status, headers, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        text = body.decode("utf-8")
        self.assertIn("Research KB 状态面板", text)
        self.assertIn("刷新状态", text)
        self.assertIn("Windows 同步", text)
        self.assertIn("元数据检索", text)

    def test_dashboard_csp_is_local_only(self):
        _, headers, _ = self._get("/")
        csp = headers["Content-Security-Policy"]
        self.assertIn("default-src 'none'", csp)
        self.assertIn("script-src 'self'", csp)
        self.assertIn("style-src 'self'", csp)
        self.assertIn("connect-src 'self'", csp)
        self.assertNotIn("unsafe-inline", csp)

    def test_no_external_assets(self):
        _, _, body = self._get("/")
        lowered = body.lower()
        self.assertNotIn(b"http://", lowered)
        self.assertNotIn(b"https://", lowered)
        self.assertNotIn(b"cdn", lowered)

    def test_local_assets_served(self):
        status, headers, body = self._get("/assets/dashboard.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", headers["Content-Type"])
        self.assertIn(b"/api/v1/status", body)
        # XSS safety: dynamic values go through textContent, never innerHTML.
        self.assertNotIn(b"innerHTML", body)
        self.assertIn(b"textContent", body)
        status, headers, body = self._get("/assets/dashboard.css")
        self.assertEqual(status, 200)
        self.assertIn("text/css", headers["Content-Type"])

    def test_status_api_shape_and_no_secrets(self):
        status, _, body = self._get("/api/v1/status")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["kind"], "status")
        self.assertEqual(payload["timezone"], "Asia/Shanghai (UTC+8)")
        self.assertIn("ingestion", payload)
        self.assertIn("sync", payload)
        self.assertIn("recent_changes", payload)
        self.assertGreaterEqual(payload["catalog"]["documents_total"], 1)
        text = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("api_key", text.lower())
        self.assertNotIn("apikey", text.lower())
        self.assertNotIn(self.tmp, text)

    def test_status_head_supported(self):
        status, headers, body = self._get("/api/v1/status", method="HEAD")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")

    def test_metadata_search_preserved(self):
        status, _, body = self._get("/api/v1/search?q=%E7%A0%94")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["kind"], "metadata_search")


class DashboardEscapingTest(unittest.TestCase):
    def test_js_depends_on_textcontent(self):
        self.assertIn("textContent", DASHBOARD_JS)
        self.assertNotIn("innerHTML", DASHBOARD_JS)
        self.assertNotIn("document.write", DASHBOARD_JS)


if __name__ == "__main__":
    unittest.main()
