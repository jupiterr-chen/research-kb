#!/usr/bin/env python3
"""Live acceptance checks against the running Research KB service.

Run on the NAS host (has read access to the read-only sources and the catalog).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def http(base, path, method="GET", headers=None):
    req = urllib.request.Request(base + path, method=method, headers=headers or {})
    try:
        resp = urllib.request.urlopen(req, timeout=60)
        return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8765")
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--reports-root", required=True)
    parser.add_argument("--discord-root", required=True)
    args = parser.parse_args()

    con = sqlite3.connect("file:%s?mode=ro" % args.catalog, uri=True)
    con.row_factory = sqlite3.Row
    results = []

    def check(name, ok, detail=""):
        results.append((name, bool(ok), detail))
        print("%-40s %s %s" % (name, "PASS" if ok else "FAIL", detail))

    def candidates(source):
        root = args.reports_root if source == "reports" else args.discord_root
        rows = con.execute(
            "SELECT d.doc_id, d.metadata_json, v.version_id, v.sha256, v.bytes, v.media_type, "
            "v.rel_path, v.ext FROM documents d JOIN versions v "
            "ON v.source=d.source AND v.doc_id=d.doc_id "
            "WHERE d.source=? AND v.is_current=1 ORDER BY d.doc_id", (source,)).fetchall()
        out = []
        for row in rows:
            meta = json.loads(row["metadata_json"] or "{}")
            src = os.path.join(root, row["rel_path"])
            if os.path.exists(src):
                out.append((row, meta, src))
        return out

    def fetch_match(source, predicate, label):
        for row, meta, src in candidates(source):
            if predicate(row, meta):
                status, headers, body = http(args.base, "/api/v1/files/%s/%s" % (source, row["doc_id"]))
                served = hashlib.sha256(body).hexdigest()
                source_sha = sha256_file(src)
                check(label, status == 200 and served == source_sha == row["sha256"],
                      "doc=%s bytes=%d type=%s" % (row["doc_id"], len(body), headers.get("Content-Type")))
                return row
        check(label, False, "no matching sample with present source file")
        return None

    # A09 metadata search
    status, _, body = http(args.base, "/api/v1/search?source=reports&page_size=3")
    payload = json.loads(body)
    check("A09 search metadata-only", status == 200 and payload.get("kind") == "metadata_search"
          and "Metadata only" in payload.get("notice", ""), "total=%s" % payload.get("total"))

    # A10 file integrity per type, compared against the live read-only source
    fetch_match("reports", lambda r, m: r["media_type"] == "application/pdf", "A10 reports PDF sha vs source")
    fetch_match("reports", lambda r, m: r["media_type"] == "text/html", "A10 reports HTML sha vs source")
    fetch_match("discord", lambda r, m: r["media_type"] == "application/pdf", "A10 discord PDF sha vs source")
    fetch_match("discord", lambda r, m: r["media_type"] == "image/png", "A10 discord PNG sha vs source")
    fetch_match("discord", lambda r, m: m.get("file_name_local") and "." not in m["file_name_local"],
                "A11 extension-less PDF served")

    # A11 HTTP semantics on an existing reports PDF
    sample = next(((r, m, s) for r, m, s in candidates("reports") if r["media_type"] == "application/pdf"), None)
    row = sample[0]
    path = "/api/v1/files/reports/%s" % row["doc_id"]
    status, headers, body = http(args.base, path, method="HEAD")
    check("A11 HEAD content-length", status == 200 and int(headers.get("Content-Length", -1)) == row["bytes"],
          "len=%s" % headers.get("Content-Length"))
    status, headers, body = http(args.base, path, headers={"Range": "bytes=0-99"})
    check("A11 Range 206", status == 206 and len(body) == 100 and headers.get("Content-Range", "").startswith("bytes 0-99/"),
          headers.get("Content-Range"))
    status, headers, body = http(args.base, path, headers={"Range": "bytes=99999999-100000000"})
    check("A11 invalid range 416", status == 416 and headers.get("Content-Range", "").startswith("bytes */"))
    status2, headers2, _ = http(args.base, path)
    status3, _, _ = http(args.base, path, headers={"If-None-Match": headers2.get("ETag", "x")})
    check("A11 ETag 304", status3 == 304 and bool(headers2.get("ETag")))

    # A12 path safety (live)
    bad = ["/api/v1/files/reports/..%2f..%2fetc%2fpasswd", "/api/v1/files/reports/%2e%2e%2fsecret",
           "/api/v1/files/reports/etc%2fpasswd", "/api/v1/files/badsource/x",
           "/api/v1/files/reports/nonexistent-id"]
    codes = [http(args.base, b)[0] for b in bad]
    check("A12 path traversal blocked", all(c in (400, 404) for c in codes), str(codes))

    # A13 HTML isolation / escaping
    html = next(((r, m, s) for r, m, s in candidates("reports") if r["media_type"] == "text/html"), None)
    if html:
        status, headers, body = http(args.base, "/api/v1/files/reports/%s" % html[0]["doc_id"])
        csp = headers.get("Content-Security-Policy", "")
        check("A13 HTML sandbox CSP", "sandbox" in csp and "default-src 'none'" in csp, csp[:60])
    status, _, body = http(args.base, "/api/v1/documents/reports/does-not-exist")
    check("A13 error no path leak", status == 404 and args.reports_root.encode() not in body)

    con.close()
    failures = [n for n, ok, _ in results if not ok]
    print("\nSUMMARY: %d checks, %d failed" % (len(results), len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
