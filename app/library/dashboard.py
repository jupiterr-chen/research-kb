"""Chinese status dashboard: root HTML plus local CSS/JS assets.

No external CDN or assets are used. The page only reads the local status API
and the existing metadata search API. All dynamic values are inserted with
``textContent`` (never ``innerHTML``) so titles/summaries cannot inject markup.
"""

from __future__ import annotations

DASHBOARD_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Research KB 状态面板</title>
<link rel="stylesheet" href="/assets/dashboard.css">
<script defer src="/assets/dashboard.js"></script>
</head>
<body>
<header class="hero">
  <div class="hero-text">
    <h1>Research KB 状态面板</h1>
    <p class="sub">原始归档（只读） → 统一目录/卡片 → Windows Vault</p>
  </div>
  <div class="hero-actions">
    <span id="refresh-meta" class="muted">尚未刷新</span>
    <button id="refresh-btn" type="button">刷新状态</button>
  </div>
</header>

<div id="banner" class="banner hidden" role="status"></div>

<section id="overview" class="grid" aria-label="总览">
  <div class="card skeleton">正在加载状态…</div>
</section>

<section class="panel">
  <h2>入库状态</h2>
  <div id="ingestion" class="kv">正在加载…</div>
</section>

<section class="panel">
  <h2>资料来源</h2>
  <div id="sources" class="grid">正在加载…</div>
</section>

<section class="panel">
  <h2>最近变化</h2>
  <div id="changes" class="muted">正在加载…</div>
</section>

<section class="panel">
  <h2>异常与不可用</h2>
  <div id="abnormal" class="muted">正在加载…</div>
</section>

<section class="panel">
  <h2>Windows 同步（Syncthing，仅只读观测）</h2>
  <div id="sync" class="muted">正在加载…</div>
</section>

<section class="panel">
  <h2>元数据检索</h2>
  <form id="search-form" class="search">
    <input id="search-q" type="search" placeholder="按标题/摘要/代码/日期关键字" autocomplete="off">
    <button type="submit">检索</button>
  </form>
  <p class="muted">元数据检索（标题/摘要/日期/代码），不是 PDF 全文或语义检索，也不触发入库。</p>
  <div id="search-results"></div>
</section>

<section class="panel">
  <h2>最近入库记录</h2>
  <div id="runs" class="muted">正在加载…</div>
</section>

<section class="panel">
  <h2>技术细节</h2>
  <details>
    <summary>展开索引观测与说明</summary>
    <div id="details" class="muted">正在加载…</div>
  </details>
</section>

<footer id="notices" class="muted"></footer>
</body>
</html>
"""

DASHBOARD_CSS = """
:root {
  color-scheme: light dark;
  --bg: #f4f6f8;
  --fg: #1d2733;
  --muted: #64748b;
  --card: #ffffff;
  --line: #dbe3ea;
  --ok: #1f9d55;
  --ok-bg: #e6f6ec;
  --pending: #b7791f;
  --pending-bg: #fdf3e2;
  --error: #c0392b;
  --error-bg: #fdeceb;
  --unknown: #5b6b7c;
  --unknown-bg: #eef2f6;
  --stale: #b45309;
  --stale-bg: #fdf0e3;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0f1720; --fg: #e6edf3; --muted: #94a3b8; --card: #16202b;
    --line: #2a3947; --ok-bg: #12301f; --pending-bg: #33280f;
    --error-bg: #3a1b19; --unknown-bg: #1d2833; --stale-bg: #33240f;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 1rem; background: var(--bg); color: var(--fg);
  font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", system-ui, sans-serif;
  line-height: 1.5;
}
.hero {
  display: flex; flex-wrap: wrap; gap: 0.75rem; align-items: center;
  justify-content: space-between; margin-bottom: 1rem;
}
.hero h1 { margin: 0; font-size: 1.4rem; }
.sub { margin: 0.2rem 0 0; color: var(--muted); }
.hero-actions { display: flex; gap: 0.6rem; align-items: center; }
button {
  font: inherit; padding: 0.45rem 0.9rem; border-radius: 8px; cursor: pointer;
  border: 1px solid var(--line); background: var(--card); color: var(--fg);
}
button:hover { border-color: var(--ok); }
button:disabled { opacity: 0.55; cursor: progress; }
.muted { color: var(--muted); }
.hidden { display: none; }
.banner {
  padding: 0.6rem 0.9rem; border-radius: 8px; margin-bottom: 1rem;
  background: var(--stale-bg); color: var(--stale); border: 1px solid var(--line);
}
.banner.error { background: var(--error-bg); color: var(--error); }
.grid {
  display: grid; gap: 0.75rem; margin-bottom: 1rem;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
}
.card, .panel {
  background: var(--card); border: 1px solid var(--line); border-radius: 12px;
  padding: 0.9rem 1rem;
}
.card h3 { margin: 0 0 0.4rem; font-size: 0.95rem; color: var(--muted); font-weight: 600; }
.card .value { font-size: 1.35rem; font-weight: 700; }
.card .value.small { font-size: 1rem; font-weight: 600; }
.panel { margin-bottom: 1rem; }
.panel h2 { margin: 0 0 0.6rem; font-size: 1.05rem; }
.badge {
  display: inline-block; padding: 0.1rem 0.55rem; border-radius: 999px;
  font-size: 0.82rem; font-weight: 600;
}
.badge.ok { background: var(--ok-bg); color: var(--ok); }
.badge.pending { background: var(--pending-bg); color: var(--pending); }
.badge.error { background: var(--error-bg); color: var(--error); }
.badge.unknown { background: var(--unknown-bg); color: var(--unknown); }
.badge.stale { background: var(--stale-bg); color: var(--stale); }
.kv { display: grid; grid-template-columns: max-content 1fr; gap: 0.25rem 0.9rem; }
.kv .k { color: var(--muted); }
table { width: 100%; border-collapse: collapse; font-size: 0.92rem; }
th, td { text-align: left; padding: 0.35rem 0.5rem; border-bottom: 1px solid var(--line); vertical-align: top; }
th { color: var(--muted); font-weight: 600; }
.error-text { color: var(--error); }
.ok-text { color: var(--ok); }
.search { display: flex; gap: 0.5rem; flex-wrap: wrap; margin-bottom: 0.4rem; }
.search input { flex: 1 1 240px; padding: 0.45rem 0.6rem; border-radius: 8px; border: 1px solid var(--line); background: var(--card); color: var(--fg); }
details summary { cursor: pointer; color: var(--muted); }
ul.clean { list-style: none; padding: 0; margin: 0; }
ul.clean li { padding: 0.3rem 0; border-bottom: 1px solid var(--line); }
a { color: var(--ok); }
@media (max-width: 520px) {
  .kv { grid-template-columns: 1fr; }
  .kv .k { font-weight: 600; }
  body { padding: 0.6rem; }
}
"""

DASHBOARD_JS = r"""
"use strict";
(function () {
  var REFRESH_MS = 15000;
  var state = { data: null, lastGood: null, lastRefresh: null, loading: false, error: null };
  var els = {};

  function $(id) { return document.getElementById(id); }

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) { node.className = cls; }
    if (text !== undefined && text !== null) { node.textContent = String(text); }
    return node;
  }

  function card(title, value, cls, small) {
    var node = el("div", "card");
    node.appendChild(el("h3", null, title));
    var v = el("div", "value" + (small ? " small" : ""));
    if (cls) { v.className += " " + cls; }
    v.textContent = value;
    node.appendChild(v);
    return node;
  }

  function badge(text, cls) { return el("span", "badge " + (cls || "unknown"), text); }

  function statusClass(kind) {
    if (kind === "complete" || kind === "ok") { return "ok"; }
    if (kind === "pending" || kind === "overdue") { return "pending"; }
    if (kind === "error") { return "error"; }
    if (kind === "stale") { return "stale"; }
    return "unknown";
  }

  function fmtLocal(iso) {
    if (!iso) { return "—"; }
    var d = new Date(iso);
    if (isNaN(d.getTime())) { return String(iso); }
    try {
      return d.toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false });
    } catch (e) {
      return d.toLocaleString("zh-CN", { hour12: false }) + "（本地偏移，未显式指定上海时区）";
    }
  }

  function fmtDuration(seconds) {
    if (seconds === null || seconds === undefined) { return "未知"; }
    var s = Math.round(seconds);
    if (s < 60) { return s + " 秒"; }
    if (s < 3600) { return Math.floor(s / 60) + " 分 " + (s % 60) + " 秒"; }
    return (s / 3600).toFixed(1) + " 小时";
  }

  function fmtBytes(value) {
    if (value === null || value === undefined) { return "未知"; }
    var n = Number(value);
    if (!isFinite(n)) { return "未知"; }
    var units = ["B", "KB", "MB", "GB", "TB"];
    var i = 0;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i += 1; }
    return (i === 0 ? n : n.toFixed(1)) + " " + units[i];
  }

  function fmtCount(value) {
    if (value === null || value === undefined) { return "未知"; }
    return String(value);
  }

  function setBanner(kind, text) {
    if (!text) { els.banner.className = "banner hidden"; els.banner.textContent = ""; return; }
    els.banner.className = "banner" + (kind === "error" ? " error" : "");
    els.banner.textContent = text;
  }

  function load() {
    if (state.loading) { return; }
    state.loading = true;
    els.button.disabled = true;
    var controller = null;
    var timer = null;
    if (typeof AbortController !== "undefined") {
      controller = new AbortController();
      timer = setTimeout(function () { controller.abort(); }, 9000);
    }
    fetch("/api/v1/status", { cache: "no-store", signal: controller ? controller.signal : undefined })
      .then(function (response) {
        if (!response.ok) { throw new Error("HTTP " + response.status); }
        return response.json();
      })
      .then(function (data) {
        state.data = data;
        state.lastGood = data;
        state.lastRefresh = new Date();
        state.error = null;
      })
      .catch(function (err) {
        state.error = err && err.name === "AbortError" ? "请求超时" : ("无法获取状态：" + (err && err.message ? err.message : err));
      })
      .then(function () {
        if (timer) { clearTimeout(timer); }
        state.loading = false;
        els.button.disabled = false;
        render();
      });
  }

  function render() {
    var data = state.data;
    els.meta.textContent = state.lastRefresh
      ? "上次刷新：" + state.lastRefresh.toLocaleString("zh-CN", { hour12: false }) + "（浏览器本地时间）"
      : "尚未刷新";
    if (state.error) {
      setBanner("error", state.error + "。以下为上次成功获取的数据，可能已过时。");
    } else if (!data) {
      setBanner(null, null);
      return;
    } else {
      setBanner(null, null);
    }
    if (!data) { return; }
    renderOverview(data);
    renderIngestion(data);
    renderSources(data);
    renderChanges(data);
    renderAbnormal(data);
    renderSync(data);
    renderRuns(data);
    renderDetails(data);
    renderNotices(data);
  }

  function renderOverview(data) {
    var box = els.overview;
    box.textContent = "";
    var ing = data.ingestion || {};
    var sync = data.sync || {};
    var cat = data.catalog || {};
    var sched = ing.scheduler || {};
    box.appendChild(card("入库状态", (ing.state_label || "未知"), statusClass(ing.state)));
    box.appendChild(card("Windows 同步", (sync.status_label || "未知"), statusClass(sync.status)));
    box.appendChild(card("文档总数 / 可用", fmtCount(cat.documents_total) + " / " + fmtCount(cat.documents_available), null));
    var nextText = sched.next_check_at ? fmtLocal(sched.next_check_at) + "（预计）" : "未知";
    if (sched.overdue) { nextText += " · 已超时"; }
    box.appendChild(card("下次自动检查", nextText, sched.overdue ? "stale" : null, true));
    box.appendChild(card("版本总数", fmtCount(cat.versions_total), null));
    box.appendChild(card("可用原始字节", fmtBytes(cat.ready_bytes), null, true));
  }

  function kv(container, pairs) {
    container.textContent = "";
    pairs.forEach(function (pair) {
      container.appendChild(el("div", "k", pair[0]));
      container.appendChild(el("div", "v", pair[1]));
    });
  }

  function renderIngestion(data) {
    var ing = data.ingestion || {};
    var sched = ing.scheduler || {};
    var run = ing.runner || {};
    var render = ing.render || {};
    var pairs = [
      ["总体状态", ing.state_label || "未知"],
      ["调度状态", sched.state_label || "未知"],
      ["配置间隔", fmtDuration(sched.interval_seconds)],
      ["正在运行", ing.running ? "是" : "否"],
      ["最近一次尝试（调度）", fmtLocal(sched.last_attempt_started_at)],
      ["最近一次成功（调度）", sched.last_ok === true ? fmtLocal(sched.last_finished_at) : (sched.last_ok === false ? "最近调度失败" : "未知")],
      ["调度心跳", sched.heartbeat_at ? fmtLocal(sched.heartbeat_at) + "（" + fmtDuration(sched.heartbeat_age_seconds) + "前）" : "无记录"],
      ["下次自动检查", sched.next_check_at ? fmtLocal(sched.next_check_at) + "（预计，按完成时间+间隔）" : "未知"],
      ["最近入库耗时", fmtDuration(run.last_duration_seconds)],
      ["最近入库结果", run.last_ok === true ? "成功" : (run.last_ok === false ? "失败" : "未知")],
      ["最近卡片渲染", render.at ? (render.ok ? "成功 · " + fmtLocal(render.at) : "失败 · " + fmtLocal(render.at)) : "无记录"]
    ];
    if (run.last_error) { pairs.push(["最近入库错误", run.last_error]); }
    if (render.error) { pairs.push(["最近渲染错误", render.error]); }
    if (sched.last_error) { pairs.push(["调度错误", sched.last_error]); }
    kv(els.ingestion, pairs);
  }

  function renderSources(data) {
    var box = els.sources;
    box.textContent = "";
    (data.sources || []).forEach(function (src) {
      var node = el("div", "card");
      var head = el("div");
      head.appendChild(el("h3", null, src.label || src.name));
      head.appendChild(badge(src.freshness || "未知", src.healthy ? "ok" : (src.last_error ? "error" : "unknown")));
      node.appendChild(head);
      var pairs = [
        ["已编目", fmtCount(src.documents_total)],
        ["可用", fmtCount(src.documents_available)],
        ["等待/发现", fmtCount(src.waiting)],
        ["缺失/冲突", fmtCount(src.unavailable)],
        ["最近扫描", fmtLocal(src.last_ok_at)],
        ["最近尝试", fmtLocal(src.last_attempt_at)]
      ];
      var body = el("div", "kv");
      kv(body, pairs);
      node.appendChild(body);
      if (src.last_error) {
        node.appendChild(el("div", "error-text", "错误：" + src.last_error));
      }
      if (src.index_observed && src.index_observed.available) {
        node.appendChild(el("div", "muted", "索引文件修改时间（本地观测）：" + fmtLocal(src.index_observed.mtime)));
      }
      box.appendChild(node);
    });
    if (!box.children.length) { box.appendChild(el("div", "muted", "未配置来源。")); }
  }

  function renderChanges(data) {
    var box = els.changes;
    box.textContent = "";
    var changes = data.recent_changes || [];
    if (!changes.length) {
      box.appendChild(el("p", "ok-text", "检查成功，无新增变化（或变更记录基线刚建立）。"));
      return;
    }
    var table = el("table");
    var thead = el("thead");
    var hrow = el("tr");
    ["时间", "来源", "类型", "标题", "详情"].forEach(function (t) { hrow.appendChild(el("th", null, t)); });
    thead.appendChild(hrow); table.appendChild(thead);
    var tbody = el("tbody");
    changes.forEach(function (c) {
      var row = el("tr");
      row.appendChild(el("td", null, fmtLocal(c.at)));
      row.appendChild(el("td", null, c.source_label || c.source));
      row.appendChild(el("td", null, c.kind_label || c.kind));
      row.appendChild(el("td", null, c.title || "（无标题）"));
      var detail = c.detail || {};
      var text = "";
      if (detail.fields) { text = "字段：" + detail.fields.join("、"); }
      else if (detail.version_ids) { text = "版本：" + detail.version_ids.join("、"); }
      else if (detail.to) { text = "→ " + detail.to; }
      else if (detail.reason) { text = "原因：" + detail.reason; }
      else if (detail.current_state) { text = "当前状态：" + detail.current_state; }
      else if (detail.existing_documents !== undefined) { text = "既有文档 " + detail.existing_documents + " 条作为基线"; }
      var cell = el("td");
      cell.appendChild(el("span", null, text));
      if (c.detail_url) {
        cell.appendChild(document.createTextNode(" "));
        var a = el("a", null, "查看");
        a.href = c.detail_url;
        cell.appendChild(a);
      }
      row.appendChild(cell);
      tbody.appendChild(row);
    });
    table.appendChild(tbody);
    box.appendChild(table);
  }

  function renderAbnormal(data) {
    var box = els.abnormal;
    box.textContent = "";
    var found = false;
    (data.sources || []).forEach(function (src) {
      if (src.last_error || src.unavailable) {
        found = true;
        var line = el("div", "error-text");
        line.textContent = (src.label || src.name) + "：不可用 " + fmtCount(src.unavailable) + "，错误 " + (src.last_error || "无");
        box.appendChild(line);
      }
    });
    var sync = data.sync || {};
    if (sync.status === "error" || sync.status === "offline" || sync.status === "stale") {
      found = true;
      box.appendChild(el("div", "error-text", "同步：" + (sync.status_label || sync.status)));
    }
    if (!found) { box.appendChild(el("div", "ok-text", "当前无异常记录。")); }
  }

  function renderSync(data) {
    var box = els.sync;
    box.textContent = "";
    var sync = data.sync || {};
    var collector = sync.collector || {};
    var server = sync.server || {};
    var folder = sync.folder || {};
    var peer = sync.peer || {};
    var backlog = sync.backlog || {};

    var head = el("div");
    head.appendChild(badge(sync.status_label || "未知", statusClass(sync.status)));
    box.appendChild(head);

    if (!sync.configured || !collector.snapshot_present) {
      box.appendChild(el("p", "muted", "未获取到同步监控样本（未配置采集器或尚未运行）。"));
      box.appendChild(el("p", "muted", sync.units_note || ""));
      return;
    }

    var pairs = [
      ["样本时间", fmtLocal(collector.observed_at)],
      ["样本年龄", collector.age_seconds !== null && collector.age_seconds !== undefined ? fmtDuration(collector.age_seconds) : "未知"],
      ["样本过期阈值", fmtDuration(collector.stale_after_seconds)],
      ["采集状态", collector.poll_ok === false ? "上次采集失败" : (collector.poll_ok === true ? "正常" : "未知")],
      ["服务器版本", server.version || "未知"],
      ["服务器本地设备", server.local_id_short || "未知"],
      ["文件夹状态", folder.state || "未知"],
      ["文件夹暂停", folder.paused === true ? "是" : (folder.paused === false ? "否" : "未知")],
      ["Windows 设备（别名）", peer.alias || "Windows 设备"],
      ["Windows 连接", peer.connected === true ? "已连接" : (peer.connected === false ? "未连接" : "未知")],
      ["Windows 最近可见", fmtLocal(peer.last_seen_at)],
      ["远端完成度", peer.completion === null || peer.completion === undefined ? "未知" : peer.completion + "%"],
      ["远端状态", peer.remote_state || "未知"],
      ["服务器本地待拉取", fmtCount(backlog.server_local ? backlog.server_local.need_items : null) + " 项 / " + fmtBytes(backlog.server_local ? backlog.server_local.need_bytes : null)],
      ["远端待传输", fmtCount(backlog.remote ? backlog.remote.need_items : null) + " 项 / " + fmtBytes(backlog.remote ? backlog.remote.need_bytes : null)],
      ["同步文件（服务器本地）", fmtCount(folder.local_files)],
      ["同步文件（全局）", fmtCount(folder.global_files)]
    ];
    var body = el("div", "kv");
    kv(body, pairs);
    box.appendChild(body);
    box.appendChild(el("p", "muted", sync.server_backlog_note || ""));
    box.appendChild(el("p", "muted", sync.units_note || ""));
    if (collector.errors && collector.errors.length) {
      box.appendChild(el("p", "error-text", "采集错误：" + collector.errors.join("；")));
    }
  }

  function renderRuns(data) {
    var box = els.runs;
    box.textContent = "";
    var runs = data.recent_runs || [];
    if (!runs.length) { box.appendChild(el("p", "muted", "暂无入库记录。")); return; }
    var table = el("table");
    var thead = el("thead");
    var hrow = el("tr");
    ["开始", "结束", "耗时", "结果", "错误"].forEach(function (t) { hrow.appendChild(el("th", null, t)); });
    thead.appendChild(hrow); table.appendChild(thead);
    var tbody = el("tbody");
    runs.forEach(function (r) {
      var row = el("tr");
      row.appendChild(el("td", null, fmtLocal(r.started_at)));
      row.appendChild(el("td", null, fmtLocal(r.finished_at)));
      row.appendChild(el("td", null, fmtDuration(r.duration_seconds)));
      var res = el("td", r.ok ? "ok-text" : "error-text", r.ok ? "成功" : "失败");
      row.appendChild(res);
      row.appendChild(el("td", null, r.error || "—"));
      tbody.appendChild(row);
    });
    table.appendChild(tbody);
    box.appendChild(table);
  }

  function renderDetails(data) {
    var box = els.details;
    box.textContent = "";
    var list = el("ul", "clean");
    (data.sources || []).forEach(function (src) {
      if (src.index_observed) {
        list.appendChild(el("li", null, (src.label || src.name) + " 索引观测：" + (src.index_observed.available ? fmtLocal(src.index_observed.mtime) + "（" + fmtBytes(src.index_observed.bytes) + "）" : "不可读")));
      }
      list.appendChild(el("li", null, (src.label || src.name) + " 原始计数：" + JSON.stringify(src.counts_snapshot || {})));
    });
    list.appendChild(el("li", null, "生成时间：" + fmtLocal(data.generated_at) + "（时区：" + (data.timezone || "Asia/Shanghai") + "）"));
    box.appendChild(list);
  }

  function renderNotices(data) {
    els.notices.textContent = "";
    (data.notices || []).forEach(function (text) {
      els.notices.appendChild(el("div", null, "· " + text));
    });
  }

  function search(event) {
    event.preventDefault();
    var q = ($("search-q").value || "").trim();
    var box = $("search-results");
    box.textContent = "";
    if (!q) { return; }
    box.appendChild(el("p", "muted", "检索中…"));
    fetch("/api/v1/search?q=" + encodeURIComponent(q) + "&page_size=10", { cache: "no-store" })
      .then(function (r) { if (!r.ok) { throw new Error("HTTP " + r.status); } return r.json(); })
      .then(function (data) {
        box.textContent = "";
        box.appendChild(el("p", "muted", "元数据检索结果共 " + data.total + " 条，显示前 " + (data.items || []).length + " 条（非全文检索）。"));
        var table = el("table");
        var tbody = el("tbody");
        (data.items || []).forEach(function (item) {
          var row = el("tr");
          row.appendChild(el("td", null, item.display_title || item.title || item.doc_id));
          row.appendChild(el("td", null, item.source));
          row.appendChild(el("td", null, item.symbol || item.market || ""));
          row.appendChild(el("td", null, item.report_date || item.filing_date || item.published_at || ""));
          tbody.appendChild(row);
        });
        table.appendChild(tbody);
        box.appendChild(table);
      })
      .catch(function (err) { box.textContent = ""; box.appendChild(el("p", "error-text", "检索失败：" + err.message)); });
  }

  function init() {
    els = {
      meta: $("refresh-meta"), button: $("refresh-btn"), banner: $("banner"),
      overview: $("overview"), ingestion: $("ingestion"), sources: $("sources"),
      changes: $("changes"), abnormal: $("abnormal"), sync: $("sync"),
      runs: $("runs"), details: $("details"), notices: $("notices")
    };
    els.button.addEventListener("click", load);
    var form = $("search-form");
    if (form) { form.addEventListener("submit", search); }
    load();
    setInterval(load, REFRESH_MS);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
"""

DASHBOARD_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; "
    "connect-src 'self'; img-src data:; base-uri 'none'; "
    "form-action 'none'; frame-ancestors 'none'"
)
