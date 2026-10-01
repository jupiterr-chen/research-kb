# 部署与数据设计

## 数据流

两个源归档（只读） -> adapter / catalog -> Markdown 卡片与页面 -> Syncthing -> Windows Obsidian。
Obsidian 点击固定链接 -> library API -> ID 映射与允许路径检查 -> 原目录原文流式读取。

## 物理布局

部署数据根目录（环境变量 `RESEARCHKB_HOME`，容器内挂载为 `/data`）：`app/`（代码）、`vault/`（笔记）、`catalog/`（统一 SQLite）、`state/`（日志任务与同步配置）、`backups/`（本任务小文件版本快照，仅本机且明确非灾备）。未来 `extracted/` 不在本次范围。具体主机路径属部署私有信息，记录于未发布的 RUNBOOK/ENVIRONMENT。

## 服务

- Docker Compose 独立项目 research-kb，library API 与定时 adapter 可以同容器或独立 worker；具备健康检查、互斥任务、启动对账、每小时重扫元数据。
- 两源目录只读 bind mount，写目录仅本项目。权限不靠 chmod/chown 修复；按既有 ACL/UID 情况选择安全运行方式并记录。
- API 端口 8765；容器内监听 `0.0.0.0`，由 compose 端口映射限定到配置的主机地址（默认回环，可信 LAN 时显式指定，见 `.env` 的 `RESEARCHKB_BIND`）；不对公网开放。第一版访问边界为可信 LAN；经 VPN 的额外绑定需明确记录。不修改现有反向代理。
- Syncthing 服务器容器或独立进程、Windows 用户进程；唯一配置目录。管理 UI 回环监听；设备 ID 双向配对，固定 LAN 地址可关闭公共发现与中继。仅同步 vault，忽略 .obsidian 设备设置、临时文件、冲突处理记录敏感项。首次检查双方已有数据。
- Windows 后台启动使用 Hidden；持久化优先用户登录任务/用户 Startup，任务名 ResearchKB-Syncthing；不覆盖已有任务。
- Obsidian 官方安装源，用户级或项目独立目录；安装完成后注册/打开指定 Vault。无需 Obsidian 专用远程插件。

## 文档与版本

- 财报 source_doc_id=report_id，version=artifact_id；映射路径仅接受 `/app/reports/` 的合法相对后缀。保存 ready 历史版本，默认目录只显示当前版。
- Discord source_doc_id=doc_id，version=sha256；source summary 单独标识。标题 display 解码，原值保留。源相对路径经 resolve 和根包含检查，拒绝路径穿越及跨根 symlink。
- 可按哈希共享内容解析身份，但不同来源记录不能合并丢失。未知期不推断。source author 不等于报告作者。
- 同步索引以事务刷新：读取有效完整输入后对账；失败不清空上次 catalog。已入库但当前未见记录保留，缺失文件标记不可用。
- 文件服务通过 registry 查版本和允许路径，streaming，不向任意传入 filepath 开放。文档详情和 API 使用 HTML 转义；HTML 原文禁脚本执行（如 CSP sandbox，外链资源提示）。下载不修改源。

## Obsidian 内容

首页、资料目录/财报、资料目录/研报为程序生成区；公司研究、主题研究、周报与复盘为人工区；分析草稿预留。稳定 MD 文件名使用来源及 ID，标题放显示属性。可用 Bases 或普通 Markdown 索引，禁止依赖复杂社区插件才能打开。

每张卡片含来源、报告日、披露/推送时间、公司（已知时）、文档编号、当前版本、固定版本原文链接、来源 URL/消息说明。不把短期签名 URL 当永久引用，不嵌入 secrets。

## 安全与运维

保留自动生成文件所有权边界；未知文件不覆盖。不要把活跃 catalog SQLite 交给 Syncthing。原库只读、源码与部署可复现、自动安装校验下载来源与校验和（可用时）。凭据不进文档、日志和 git。未来 OCR 与全文检索另开任务。

## 实施记录（2026-10-01，OpenCode）

与初版设计的偏差及理由：

1. **容器内监听地址**：库服务在容器内绑定 `0.0.0.0:8765`（容器不拥有宿主 LAN IP），对外由 compose `ports: ${RESEARCHKB_BIND}:8765:8765` 限定，仍满足"仅指定主机地址、不绑定公网"。见 ISSUES I005。
2. **财报 SQLite 读取**：`read_mode=auto`，先尝试只读直连（`file:...?mode=ro`），失败时把 `archive.sqlite3(-wal/-shm)` 复制到项目可写目录 `catalog/source-snapshot/` 后再只读打开；不修改源、不使用 immutable。实测只读挂载下直连成功（`read_mode=direct`）。
3. **内容校验**：reports 每次入库对每个 ready 版本实际计算 sha256 与源声明比对（发现 4 条文件缺失）；discord 首次入库全量计算 sha256（438 条），此后按 (doc_id, version_id, size) 命中缓存跳过，避免每小时重算 1.3GB。源声明 sha 只作参考，观测 sha 落库。
4. **Syncthing**：官方镜像 v2.1.5；服务器网络模式 host，GUI 强制回环 `127.0.0.1:8384`，同步端口 22000；关闭全局发现与中继（LAN-only），显式地址配对。Windows 因系统保留端口段改用回环 `18384`。忽略 `.obsidian/.stfolder/.stversions/.sync-conflict*/临时文件`。
5. **客户端**：Syncthing 与 Obsidian 均官方来源；Syncthing 发行包用 GitHub release digest 校验 sha256；Obsidian 静默安装并注册指定 Vault（合并 `obsidian.json`，不清空已有 vault）。登录启动项 `ResearchKB-Syncthing` 隐藏启动。安装脚本参数化，不内置本机路径。
6. **服务形态**：标准库 `http.server` 线程化服务，无第三方依赖；入库调度为容器内后台线程（默认 3600s，互斥锁），满足"同容器 worker + 定时 + 健康"。
7. **生成内容**：确定性输出，按字节比较后写入以保持 mtime；生成区 `资料目录/`，人工区独立目录仅初始化 README。

### 第三轮修订（2026-10-01，回应 Codex F01–F07）

8. **版本身份与 fail-closed**：`version_id`/`sha256` 为源码索引给出的**期望身份**，不可被观测值改写；另存 `observed_sha256/observed_bytes/mtime_ns/state`。current 事务化唯一化（每次提交先清零该文档全部 is_current，再设唯一权威版本，或都不设）。无权威 current 时不回退历史版本。服务在 200/206/304 前用同一打开句柄复核 size+mtime，不一致（含同大小替换）→ 409；不提供旧 ETag 下的新字节。
9. **只读快照**：删除“分文件复制 live WAL 三件套”的旧兜底。仅以 `mode=ro` 打开并 `BEGIN` 单事务读取，所有元数据与计数来自同一逻辑快照；打开失败保留上次 catalog 并报可重试错误（ISSUES I011）。
10. **并发互斥**：任务级跨进程文件锁（POSIX `flock` / Windows `msvcrt`），入库与独立 render 共用；定时线程每次运行后关闭 Ingestor/Catalog，避免连接泄漏。
11. **Markdown 安全**：外部字符串按纯文本处理（HTML 实体转义 + Markdown 标点转义，摘要以引用块保留换行）；YAML 转义控制字符；仅 http/https 作为可点击来源链接；文件名对非法 doc_id 做确定性安全编码并做生成区 resolved 包含检查。
12. **诊断脱敏**：API 只返回脱敏后的来源错误摘要与 `status=ok|degraded`；完整 traceback 仅写私有 `state/ingest.log`。

### 第四轮修订（2026-10-01，Codex F01 余项/F07）

13. **期望身份持久化不可变**：versions 的 `sha256/bytes` 为首次接受的期望身份；后续同一 version 的声明变化不改写，写入 `declared_sha256` 诊断并使 state=conflict。catalog 依有效状态决定 `documents.available`（adapter 的 ready 不再直接决定）。
14. **字节级校验**：文件 GET/HEAD/Range/304 前对同一 fd 做 SHA256 并比对期望身份，校验期间 stat 变化视为不稳定失败关闭；因此 size+mtime 相等不再是信任依据，也无需缓存失效策略。Discord 入库每次全量 hash，`hash_file_stable` 防止读取中被替换。

### 第五轮修订（2026-10-01，Codex A21 恢复路径 + 发布准备）

15. **恢复与发布**：恢复流程明确主机 `RESEARCHKB_HOME` → 容器 `/data` 的路径映射（`catalog_db` 必须是容器路径），并要求 `--force-recreate` 重载配置，旧库/WAL/SHM 保留可回滚。可复用文件（compose、示例配置、deploy/backup/install 脚本）参数化：来源/数据/绑定地址经环境变量提供，示例与文档使用占位符，默认仅回环；真实部署值保存在被忽略的 `.env`，前后 `docker inspect` 等价。`.gitignore`/`.dockerignore` 隔离私有配置、证据与运行时数据。
