#!/usr/bin/env python3
"""Verify version history + date semantics against the live service."""
import argparse
import json
import sqlite3
import sys
import urllib.request


def get(url):
    with urllib.request.urlopen(url, timeout=30) as resp:
        return resp.status, resp.read()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8765")
    ap.add_argument("--catalog", required=True)
    args = ap.parse_args()
    con = sqlite3.connect("file:%s?mode=ro" % args.catalog, uri=True)
    con.row_factory = sqlite3.Row

    print("=== reports with more than one ready version ===")
    rows = con.execute(
        "SELECT source, doc_id, COUNT(*) c FROM versions GROUP BY source, doc_id "
        "HAVING c > 1 ORDER BY c DESC").fetchall()
    for r in rows:
        print("  %s/%s versions=%d" % (r["source"], r["doc_id"], r["c"]))
    if not rows:
        print("  none")

    if rows:
        src, doc_id = rows[0]["source"], rows[0]["doc_id"]
        detail = json.loads(get("%s/api/v1/documents/%s/%s" % (args.base, src, doc_id))[1])
        versions = detail["versions"]
        print("  detail versions=%d current=%s" % (
            len(versions), [v["version_id"] for v in versions if v["is_current"]]))
        for v in versions:
            status, body = get(v["file_url"])
            print("  version %s status=%d bytes=%d is_current=%s" % (
                v["version_id"], status, len(body), v["is_current"]))

    print("=== date semantics ===")
    distinct = con.execute(
        "SELECT COUNT(*) c FROM documents WHERE source='reports' "
        "AND report_period IS NOT NULL AND filing_date IS NOT NULL AND report_period <> filing_date").fetchone()["c"]
    unknown = con.execute(
        "SELECT COUNT(*) c FROM documents WHERE source='reports' AND status='done' AND report_period IS NULL").fetchone()["c"]
    print("  reports report_period != filing_date: %d" % distinct)
    print("  done reports with unknown (NULL) period preserved: %d" % unknown)
    example = con.execute(
        "SELECT doc_id, report_period, filing_date FROM documents WHERE source='reports' "
        "AND report_period IS NOT NULL AND filing_date IS NOT NULL AND report_period <> filing_date LIMIT 1").fetchone()
    if example:
        print("  example %s report_period=%s filing_date=%s" % (
            example["doc_id"], example["report_period"], example["filing_date"]))
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
