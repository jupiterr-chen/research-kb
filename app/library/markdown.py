"""Deterministic Obsidian Markdown rendering.

Generated files live under the vault's generated area; human research notes live
in separate directories and are never overwritten. Every generated write is
content-compared so an unchanged rerun leaves file mtimes untouched, and every
generated target is verified to resolve inside the generated area.
"""

from __future__ import annotations

import csv
import hashlib
import io
import os
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from .catalog import Catalog
from .config import Config
from .models import Document, Version, is_valid_id
from .textutil import human_size, md_text, safe_url, yaml_scalar

GENERATED_AREA = "资料目录"
REPORTS_DIR = os.path.join(GENERATED_AREA, "财报")
DISCORD_DIR = os.path.join(GENERATED_AREA, "研报")

SOURCE_LABELS = {"reports": "财报归档", "discord": "Discord 研报推送"}


def safe_component(value: str) -> str:
    """Deterministic filesystem-safe encoding of an identifier.

    Legitimate ids (hex, ``message_index``) are preserved byte-for-byte; anything
    containing separators, traversal sequences or control characters becomes a
    sanitized token with a short hash suffix to avoid collisions.
    """
    if is_valid_id(value):
        return value
    cleaned = "".join(ch if (ch.isalnum() or ch in "._-") else "_" for ch in value)[:96] or "id"
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return "%s-%s" % (cleaned.strip("._") or "id", digest)


def _generated_root(config: Config, subdir: Optional[str] = None) -> str:
    base = os.path.realpath(os.path.join(config.vault_dir, GENERATED_AREA))
    if subdir:
        base = os.path.realpath(os.path.join(base, subdir))
    return base


def _assert_within(base: str, target: str) -> None:
    base = os.path.realpath(base)
    target = os.path.realpath(target)
    if target != base and not target.startswith(base + os.sep):
        raise ValueError("generated path escapes generated area")


def _write_if_changed(path: str, content: str, base: Optional[str] = None) -> bool:
    if base is not None:
        _assert_within(base, path)
    data = content.encode("utf-8")
    if os.path.exists(path):
        try:
            with open(path, "rb") as handle:
                if handle.read() == data:
                    return False
        except OSError:
            pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp-write"
    with open(tmp, "wb") as handle:
        handle.write(data)
    os.replace(tmp, path)
    return True


def _card_filename(doc: Document) -> str:
    return "%s_%s.md" % (doc.source, safe_component(doc.doc_id))


def _note_name(doc: Document) -> str:
    return "%s_%s" % (doc.source, safe_component(doc.doc_id))


def _api_url(config: Config, *parts: str) -> str:
    return "%s/api/v1/%s" % (config.public_base_url, "/".join(quote(str(p), safe="") for p in parts))


def _front_matter(doc: Document, version: Optional[Version], config: Config) -> List[str]:
    lines = ["---"]
    fields = [
        ("id", doc.doc_id),
        ("source", doc.source),
        ("title", doc.title),
        ("display_title", doc.display_title),
        ("author", doc.author),
        ("market", doc.market),
        ("symbol", doc.symbol),
        ("doc_type", doc.doc_type),
        ("language", doc.language),
        ("report_period", doc.report_period),
        ("filing_date", doc.filing_date),
        ("published_at", doc.published_at),
        ("report_date", doc.report_date),
        ("status", doc.status),
        ("available", "true" if doc.available else "false"),
    ]
    for key, value in fields:
        lines.append("%s: %s" % (key, yaml_scalar(value)))
    if version:
        lines.append("version_id: %s" % yaml_scalar(version.version_id))
        lines.append("sha256: %s" % yaml_scalar(version.sha256))
        lines.append("bytes: %d" % (version.bytes or 0))
        lines.append("media_type: %s" % yaml_scalar(version.media_type))
        lines.append("library_url: %s" % yaml_scalar(
            _api_url(config, "documents", doc.source, doc.doc_id)))
        lines.append("file_url: %s" % yaml_scalar(
            _api_url(config, "files", doc.source, doc.doc_id)))
    lines.append("source_url: %s" % yaml_scalar(doc.source_url))
    lines.append("generated: \"true\"")
    lines.append("---")
    return lines


def render_card(doc: Document, all_versions: List[Version], config: Config) -> str:
    current = doc.current_version() if doc.available else None
    lines = _front_matter(doc, current, config)
    lines.append("")
    heading = doc.display_title or doc.title or doc.doc_id
    lines.append("# %s" % md_text(heading))
    lines.append("")
    lines.append("> 本卡片由 library 服务自动生成，请勿手工编辑；人工研究请写入公司研究/主题研究等人工区。")
    lines.append("")
    if not doc.available:
        lines.append("> **当前无可用原文**（来源状态：%s）。元数据保留，原文不可用时不提供下载。"
                     % md_text(doc.status or "unknown"))
        lines.append("")
    lines.append("## 元数据")
    lines.append("")
    label = SOURCE_LABELS.get(doc.source, doc.source)
    rows = [
        ("来源", label),
        ("文档编号", doc.doc_id),
        ("标题", doc.display_title or doc.title),
        ("公司代码", doc.symbol),
        ("市场", doc.market),
        ("文件类型", doc.doc_type),
        ("语言", doc.language),
        ("报告期", doc.report_period if doc.report_period else "未知（来源未提供，未推断）"),
        ("披露日期", doc.filing_date),
        ("推送时间(UTC)", doc.published_at),
        ("研报发布日", doc.report_date),
    ]
    for name, value in rows:
        if value in (None, ""):
            continue
        lines.append("- **%s**：%s" % (name, md_text(value)))
    if doc.author:
        lines.append("- **推送者/来源**：%s" % md_text(doc.author))
    if doc.summary:
        lines.append("")
        lines.append("## 来源说明（原文摘要，非本任务生成）")
        lines.append("")
        for summary_line in str(doc.summary).replace("\r\n", "\n").split("\n"):
            lines.append("> %s" % md_text(summary_line) if summary_line else ">")
    lines.append("")
    lines.append("## 原文访问（按需，不复制到 Vault）")
    lines.append("")
    if current:
        lines.append("- 当前版本原文：%s" % _api_url(config, "files", doc.source, doc.doc_id))
        lines.append("- 当前版本 ID：`%s`" % md_text(current.version_id))
        lines.append("- 大小/类型：%s / %s" % (human_size(current.bytes), md_text(current.media_type)))
        lines.append("- SHA256：`%s`" % md_text(current.sha256 or "未知"))
    else:
        lines.append("- 当前没有可用原文版本（来源状态：%s）" % md_text(doc.status or "未知"))
    lines.append("- 查询 API（元数据检索，非全文检索）：%s?source=%s" % (
        _api_url(config, "search"), quote(doc.source, safe="")))
    lines.append("")
    if all_versions:
        lines.append("## 版本历史")
        lines.append("")
        lines.append("| 版本ID | 当前 | 状态 | 大小 | 类型 | 哈希 | 抓取时间 | 链接 |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for version in sorted(all_versions, key=lambda v: (not v.is_current, v.version_id)):
            link = _api_url(config, "files", doc.source, doc.doc_id) + "?version=" + quote(version.version_id, safe="")
            if version.state == "ready":
                link_text = "打开"
            else:
                link_text = "不可用"
            lines.append("| `%s` | %s | %s | %s | %s | `%s` | %s | %s |" % (
                md_text(version.version_id), "是" if version.is_current else "",
                md_text(version.state), human_size(version.bytes), md_text(version.media_type or ""),
                md_text((version.sha256 or "")[:16]), md_text(version.content_changed_at or ""),
                "[%s](%s)" % (link_text, link) if version.state == "ready" else link_text))
        lines.append("")
    if doc.source_url:
        chosen = safe_url(doc.source_url)
        lines.append("## 来源链接")
        lines.append("")
        if chosen:
            lines.append("[来源 URL](%s)" % chosen)
        else:
            lines.append("来源 URL 被忽略（非 http/https 协议）：%s" % md_text(doc.source_url))
        lines.append("")
    return "\n".join(lines)


def render_index(source: str, docs: List[Document], config: Config) -> str:
    label = SOURCE_LABELS.get(source, source)
    lines = ["---", "generated: \"true\"", "source: %s" % yaml_scalar(source), "---", ""]
    lines.append("# %s 目录" % md_text(label))
    lines.append("")
    lines.append("> 由 library 服务自动生成；共 %d 份可用文档。查询为元数据检索，不含 PDF 全文。" % len(docs))
    lines.append("")
    lines.append("| 标题 | 代码 | 类型 | 报告期 | 日期 | 链接 |")
    lines.append("|---|---|---|---|---|---|")
    for doc in docs:
        title = md_text(doc.display_title or doc.title or doc.doc_id).replace("|", "\\|")
        date = doc.effective_date() or ""
        lines.append("| %s | %s | %s | %s | %s | [[%s\\|原文卡片]] |" % (
            title, md_text(doc.symbol or ""), md_text(doc.doc_type or ""),
            md_text(doc.report_period or "未知"), md_text(date), _note_name(doc)))
    lines.append("")
    return "\n".join(lines)


def render_home(config: Config, catalog: Catalog) -> str:
    counts = catalog.counts()
    reports = counts["by_source"].get("reports", {}).get("available", 0)
    discord = counts["by_source"].get("discord", {}).get("available", 0)
    lines = ["---", "generated: \"true\"", "---", ""]
    lines.append("# 研究知识库首页")
    lines.append("")
    lines.append("本 Vault 通过 Syncthing 与服务器双向同步。**生成区**（资料目录/、首页.md）由服务端程序生成并只读，请不要手工编辑；"
                 "**人工区**（公司研究/主题研究/周报与复盘/分析草稿）由人工维护并双向同步，服务不会覆盖人工笔记，冲突副本会以 sync-conflict 文件保留。")
    lines.append("")
    lines.append("原文按需经局域网文档服务读取，不同步 PDF 全量。")
    lines.append("")
    lines.append("## 资料目录（程序生成）")
    lines.append("")
    lines.append("- [[财报]] 共 %d 份可用" % reports)
    lines.append("- [[研报]] 共 %d 份可用" % discord)
    lines.append("")
    lines.append("## 人工研究区（双向同步，不会被程序覆盖）")
    lines.append("")
    for name, desc in config.human_dirs.items():
        lines.append("- [[%s]]：%s" % (name, desc))
    lines.append("")
    lines.append("## 服务信息")
    lines.append("")
    lines.append("- 文档服务：%s" % config.public_base_url)
    lines.append("- 元数据查询：%s/api/v1/search?q=关键词" % config.public_base_url)
    lines.append("- 查询范围说明：仅元数据（标题/摘要/日期/代码等），不是全文或语义检索。")
    lines.append("")
    return "\n".join(lines)


def render_catalog_csv(catalog: Catalog) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow([
        "source", "doc_id", "symbol", "market", "doc_type", "report_period",
        "filing_date", "published_at", "report_date", "status", "available",
        "current_version", "sha256", "bytes", "media_type",
    ])
    for source in ("reports", "discord"):
        for doc in catalog.list_available(source):
            current = doc.current_version()
            writer.writerow([
                doc.source, doc.doc_id, doc.symbol or "", doc.market or "", doc.doc_type or "",
                doc.report_period or "", doc.filing_date or "", doc.published_at or "",
                doc.report_date or "", doc.status or "", "1" if doc.available else "0",
                current.version_id if current else "",
                (current.sha256 if current else "") or "",
                current.bytes if current else "",
                (current.media_type if current else "") or "",
            ])
    return buffer.getvalue()


def ensure_human_area(config: Config) -> int:
    created = 0
    for name, desc in config.human_dirs.items():
        directory = os.path.join(config.vault_dir, name)
        os.makedirs(directory, exist_ok=True)
        readme = os.path.join(directory, "README.md")
        if not os.path.exists(readme):
            content = (
                "# %s（人工区）\n\n%s\n\n"
                "此目录由人工维护并双向同步，library 服务只会在首次创建此 README，绝不覆盖你的笔记。\n\n"
                "同步冲突说明：如出现冲突副本（sync-conflict），请手动比对，服务不会自动删除人工内容。\n"
            ) % (name, desc)
            _write_if_changed(readme, content)
            created += 1
    return created


def render_vault(config: Config, catalog: Catalog) -> Dict[str, Any]:
    stats = {"cards_written": 0, "cards_unchanged": 0, "pages_written": 0, "human_initialized": 0}
    generated_root = _generated_root(config)
    for source, directory in (("reports", REPORTS_DIR), ("discord", DISCORD_DIR)):
        available = catalog.list_available(source)
        for doc in available:
            versions = catalog.all_versions(source, doc.doc_id)
            path = os.path.join(config.vault_dir, directory, _card_filename(doc))
            if _write_if_changed(path, render_card(doc, versions, config), base=generated_root):
                stats["cards_written"] += 1
            else:
                stats["cards_unchanged"] += 1
        # Update any previously generated card whose document is now unavailable
        # so no stale "available: true" card remains. Never create new noise cards
        # for documents that never had one, and never touch human notes.
        for doc in catalog.list_documents(source):
            if doc.available:
                continue
            path = os.path.join(config.vault_dir, directory, _card_filename(doc))
            if os.path.exists(path):
                versions = catalog.all_versions(source, doc.doc_id)
                if _write_if_changed(path, render_card(doc, versions, config), base=generated_root):
                    stats["cards_written"] += 1
                else:
                    stats["cards_unchanged"] += 1
        index_content = render_index(source, available, config)
        index_path = os.path.join(config.vault_dir, directory + ".md")
        if _write_if_changed(index_path, index_content, base=generated_root):
            stats["pages_written"] += 1
    home_path = os.path.join(config.vault_dir, "首页.md")
    if _write_if_changed(home_path, render_home(config, catalog)):
        stats["pages_written"] += 1
    csv_path = os.path.join(config.vault_dir, GENERATED_AREA, "_catalog.csv")
    if _write_if_changed(csv_path, render_catalog_csv(catalog), base=generated_root):
        stats["pages_written"] += 1
    stats["human_initialized"] = ensure_human_area(config)
    return stats
