"""Shared fixture builders for the Research KB tests (stdlib only)."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_file(path: str, data: bytes) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)
    return sha256_bytes(data)


REPORTS_SCHEMA = """
CREATE TABLE manifest (
    report_id TEXT PRIMARY KEY,
    market TEXT, symbol TEXT, source_id TEXT, source_url TEXT,
    title TEXT, doc_type TEXT, source_form TEXT, filing_date TEXT,
    report_period TEXT, period_source TEXT, language TEXT,
    is_amendment INTEGER, revision_of TEXT, source_metadata_json TEXT,
    status TEXT, current_artifact_id TEXT
);
CREATE TABLE artifacts (
    artifact_id TEXT PRIMARY KEY, report_id TEXT, sha256 TEXT, bytes INTEGER,
    media_type TEXT, local_path TEXT, fetched_at TEXT, state TEXT,
    source_url TEXT, final_url TEXT
);
"""


def build_reports_fixture(base: str) -> dict:
    root = os.path.join(base, "reports")
    os.makedirs(root, exist_ok=True)
    db_path = os.path.join(root, "archive.sqlite3")
    conn = sqlite3.connect(db_path)
    conn.executescript(REPORTS_SCHEMA)

    pdf_one = b"%PDF-1.4 first version content"
    pdf_two = b"%PDF-1.4 second version changed content"
    hk_pdf = b"%PDF-1.4 hk interim content"
    rel_one = "CN/600519/2026-06-30__H1__test__a1111111111111111111__b1111111111111111111.pdf"
    rel_two = "CN/600519/2026-06-30__H1__test__a1111111111111111111__b2222222222222222222.pdf"
    rel_hk = "HK/01810/unknown__INTERIM__mi__c1111111111111111111__d1111111111111111111.pdf"
    sha_one = write_file(os.path.join(root, rel_one), pdf_one)
    sha_two = write_file(os.path.join(root, rel_two), pdf_two)
    sha_hk = write_file(os.path.join(root, rel_hk), hk_pdf)

    conn.execute(
        "INSERT INTO manifest VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("a1111111111111111111", "CN", "600519", "src-cn", "http://example/cn",
         "%E8%8C%85%E5%8F%B0 2026 半年报", "H1", "H1", "2026-07-01", "2026-06-30",
         "explicit_title", "zh", 0, None, '{"k":1}', "done", "b2222222222222222222"),
    )
    conn.execute(
        "INSERT INTO manifest VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("c1111111111111111111", "HK", "01810", "src-hk", "http://example/hk",
         "小米集团中期报告", "INTERIM", "INTERIM", "2026-09-02", None,
         "unknown", "zh", 0, None, None, "done", "d1111111111111111111"),
    )
    conn.execute(
        "INSERT INTO manifest VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("e1111111111111111111", "US", "AAPL", "src-us", "http://example/us",
         "aapl 10-Q", "10-Q", "10-Q", "2026-08-01", "2026-06-27",
         "source_field", "en", 0, None, None, "discovered", None),
    )
    artifacts = [
        ("b1111111111111111111", "a1111111111111111111", sha_one, len(pdf_one), "application/pdf",
         "/app/reports/" + rel_one, "2026-07-01T00:00:00Z", "ready", "http://example/f1", None),
        ("b2222222222222222222", "a1111111111111111111", sha_two, len(pdf_two), "application/pdf",
         "/app/reports/" + rel_two, "2026-07-02T00:00:00Z", "ready", "http://example/f2", None),
        ("d1111111111111111111", "c1111111111111111111", sha_hk, len(hk_pdf), "application/pdf",
         "/app/reports/" + rel_hk, "2026-09-03T00:00:00Z", "ready", "http://example/f3", None),
    ]
    conn.executemany("INSERT INTO artifacts VALUES (?,?,?,?,?,?,?,?,?,?)", artifacts)
    conn.commit()
    conn.close()
    return {
        "root": root, "db": db_path, "rel_one": rel_one, "rel_two": rel_two,
        "rel_hk": rel_hk, "sha_one": sha_one, "sha_two": sha_two, "sha_hk": sha_hk,
        "pdf_one": pdf_one,
    }


def build_discord_fixture(base: str) -> dict:
    root = os.path.join(base, "discord")
    index = os.path.join(root, "index")
    os.makedirs(index, exist_ok=True)
    pdf_data = b"%PDF-1.7 discord report"
    png_data = b"\x89PNG\r\n\x1a\nfake"
    txt_data = b"plain text note"
    rel_pdf = "attachments/971418309876654080/chan_111/1500000000000000000_0_9.pdf"
    rel_png = "attachments/971418309876654080/chan_111/1500000000000000001_0_chart.png"
    rel_txt = "attachments/971418309876654080/chan_111/1500000000000000002_0_note"
    sha_pdf = write_file(os.path.join(root, rel_pdf), pdf_data)
    sha_png = write_file(os.path.join(root, rel_png), png_data)
    sha_txt = write_file(os.path.join(root, rel_txt), txt_data)
    records = [
        {
            "doc_id": "1500000000000000000_0", "message_id": "1500000000000000000",
            "attachment_index": 0, "title": "%E7%A0%94%E6%8A%A5%E6%A0%87%E9%A2%98",
            "summary": "source message summary body", "published_at": "2026-09-30T07:56:32.759000+00:00",
            "report_date": "2026-09-29", "guild_id": "971418309876654080",
            "channel_id": "111", "channel_name": "chan", "author": "facaiclaw",
            "file_path": rel_pdf, "file_name_local": "9.pdf", "file_size": len(pdf_data),
            "content_type": "application/pdf", "file_ext": "pdf", "sha256": sha_pdf,
            "hash_source": "computed", "first_seen_at": "2026-09-30T08:00:00Z",
            "last_seen_at": "2026-10-01T00:00:00Z", "file_missing": False,
        },
        {
            "doc_id": "1500000000000000001_0", "message_id": "1500000000000000001",
            "attachment_index": 0, "title": "chart", "summary": "", "published_at": "2026-09-29T01:00:00Z",
            "report_date": None, "guild_id": "971418309876654080",
            "channel_id": "111", "channel_name": "chan", "author": "bot",
            "file_path": rel_png, "file_name_local": "chart.png", "file_size": len(png_data),
            "content_type": "image/png", "file_ext": "png", "sha256": sha_png,
            "hash_source": "computed", "first_seen_at": "2026-09-29T01:00:00Z",
            "last_seen_at": "2026-10-01T00:00:00Z", "file_missing": False,
        },
        {
            "doc_id": "1500000000000000002_0", "message_id": "1500000000000000002",
            "attachment_index": 0, "title": "note", "summary": "", "published_at": "2026-09-28T01:00:00Z",
            "report_date": None, "guild_id": "971418309876654080",
            "channel_id": "111", "channel_name": "chan", "author": "bot",
            "file_path": rel_txt, "file_name_local": "note", "file_size": len(txt_data),
            "content_type": "text/plain", "file_ext": "txt", "sha256": sha_txt,
            "hash_source": "computed", "first_seen_at": "2026-09-28T01:00:00Z",
            "last_seen_at": "2026-10-01T00:00:00Z", "file_missing": False,
        },
    ]
    with open(os.path.join(index, "documents.jsonl"), "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    with open(os.path.join(index, "meta.json"), "w", encoding="utf-8") as handle:
        json.dump({"schema_version": 1, "stats": {"documents": len(records)}}, handle)
    return {
        "root": root, "records": records, "rel_pdf": rel_pdf,
        "sha_pdf": sha_pdf, "png": rel_png, "txt": rel_txt,
    }


def make_config(tmp: str, reports: dict | None = None, discord: dict | None = None):
    from library.config import Config

    reports = reports or build_reports_fixture(tmp)
    discord = discord or build_discord_fixture(tmp)
    data = {
        "bind_host": "127.0.0.1",
        "bind_port": 0,
        "catalog_db": os.path.join(tmp, "catalog", "catalog.sqlite3"),
        "vault_dir": os.path.join(tmp, "vault"),
        "state_dir": os.path.join(tmp, "state"),
        "public_base_url": "http://test.local",
        "ingest_interval_seconds": 3600,
        "sources": {
            "reports": {
                "type": "reports_archive", "root": reports["root"],
                "db": reports["db"], "path_prefix": "/app/reports",
                "read_mode": "direct",
            },
            "discord": {
                "type": "discord_export", "root": discord["root"],
                "index": os.path.join(discord["root"], "index", "documents.jsonl"),
                "meta": os.path.join(discord["root"], "index", "meta.json"),
            },
        },
        "human_dirs": {"公司研究": "公司笔记", "主题研究": "主题笔记"},
    }
    return Config.from_dict(data), reports, discord


def temp_dir() -> str:
    return tempfile.mkdtemp(prefix="researchkb-test-")
