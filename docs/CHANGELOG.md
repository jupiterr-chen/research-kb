# CHANGELOG

## [0.1.3] — 2026-10-01 (Codex review round 5: 恢复路径 + 发布准备)

### 修复 / 变更
- **恢复路径映射**：RUNBOOK/PUBLIC_RUNBOOK 明确主机 `RESEARCHKB_HOME` → 容器 `/data` 映射，`catalog_db` 必须写容器路径；恢复用新目录且 `--force-recreate` 重载配置，旧库/WAL/SHM 不动、可回滚；恢复校验只读新库，不动线上库。
- **发布准备**：`docker-compose.yml`、示例配置、`deploy.sh`、`backup-catalog.sh`、`install-windows.ps1` 参数化（环境变量/必填参数），默认绑定回环；新增 `.env.example`、`.dockerignore`、`docs/PUBLIC_RUNBOOK.md`；重写公共 README；`.gitignore` 明确排除本地配置/证据/一次性诊断脚本；`docs/GIT_DELIVERY.md` 记录 41 文件候选清单与 0 私密标记扫描结果。真实部署值保存在被忽略的 `.env`，前后 `docker inspect` 等价。

### 测试
- Windows：56 run / 55 pass / 1 skip；Linux：56 pass。无功能回归。

## [0.1.2] — 2026-10-01 (Codex review round 4 修复)

### 修复（F01 余项 + F07 恢复）
- **持久化内容身份**：versions 新增 `sha256/bytes` 的“首次接受期望身份”语义；同一 artifact_id/discord version 后续声明的 sha/bytes 不再改写期望身份，而记入 `declared_sha256` 诊断；若观测/声明与期望不符则 state=conflict，且 catalog 据此把 `documents.available` 置 0（不再盲信 adapter 的 ready）。
- **请求期按字节校验**：`open_validated` 始终对**同一打开的描述符**做 SHA256 并与持久化期望比对，校验期间 stat 变化判为不稳定并失败关闭；随后 rewind 再流式/HEAD/Range/304。时间戳保留的等长替换、原地改写、路径置换都无法以旧 ID/ETag 提供错误字节。
- **Discord 每次入库全量哈希**：移除 size+mtime 缓存跳过；`hash_file_stable` 以读前/读后 stat 检测读取期间变化，避免 bless 撕裂内容。
- **F07 安全恢复**：RUNBOOK 改为“停本项目 library + 使用新 catalog 目录/路径 + 校验 + 改 config + 启动”，绝不覆盖活动库、不带入陈旧 WAL/SHM、旧库保留可回滚；在线备份脚本保留。
- **Git 交付准备**：新增 `.gitignore` 与 `docs/GIT_DELIVERY.md`，区分可发布源码/测试/模板与本地配置、权限、证据、运行时数据；**不在验收前 commit/push**。

### 测试
- Windows：56 run / 55 pass / 1 skip（symlink 权限）；Linux：56 pass。
- 新增回归：等长且保留 mtime 的替换在 GET/HEAD/206/条件请求下失败关闭；同 artifact_id 声明改写不改身份且标 conflict；Discord 跨入库等长保 mtime 变更被检出。

## [0.1.1] — 2026-10-01 (Codex review round 3 修复)

### 修复（F01–F07）
- **F01 版本一致性**：版本身份不可改写；Discord 缓存加入 mtime，检测同大小内容替换；catalog 事务化保证每文档恰好一个 current（或 0）；服务端 200/206/304 前用同一句柄复核 size+mtime，变更返回 409，杜绝旧 ID/ETag 返回新字节。新增多项回归测试。
- **F02 权威 current**：移除 reports 与 catalog 的“最新/首个历史版本”回退；无权威 current 即不可用，历史版本仅经显式 `?version=` 访问。生成区会把已存在卡片更新为 `available: false`，不删除卡片、不触碰人工笔记。
- **F03 安全快照**：删除分文件复制 live WAL 的旧逻辑，改为只读单事务读取；失败保留旧 catalog 并报可重试错误。
- **F04 互斥**：新增任务级跨进程文件锁（flock/msvcrt），覆盖 ingest 与独立 render；定时任务每轮关闭连接。
- **F05 生成安全**：标题/摘要/作者等按纯文本转义（HTML + Markdown），YAML 控制字符转义，仅 http/https 来源链接可点击；非法 doc_id 确定性编码 + 生成区 resolved 包含检查。
- **F06 诊断脱敏**：API 仅返回脱敏摘要，健康状态区分 ok/degraded；traceback 仅进私有日志。
- **F07 文档**：新增安全备份脚本 `scripts/backup-catalog.sh`（在线备份 + 独立恢复校验）；精确范围停止 Syncthing；首页说明双向同步与生成/人工区归属；记录 LAN 本地发现开启的实况；ISSUES 去重编号。

### 测试
- Windows：53 run / 52 pass / 1 skip（symlink 权限）；Linux：53 pass（含 symlink）。

## [0.1.0] — 2026-10-01

首个可用版本：实现并部署双来源统一文档库、Obsidian 同步与 Windows 客户端。

### 新增
- `app/library/`：标准库 Python 服务
  - 双源 adapter（`reports_archive` 只读 SQLite；`discord_export` JSONL）。
  - 统一 catalog（SQLite，事务按来源提交，失败不清库；未见记录保留并标 `not_in_snapshot`）。
  - 确定性 Markdown 卡片与索引（首页、财报、研报、_catalog.csv）；人工区只初始化不覆盖。
  - 元数据检索 API 与版本化文件服务：HEAD、Range/206、416、ETag/304、MIME、UTF-8 文件名、HTML sandbox CSP、根目录 resolve 检查。
  - 后台定时入库（默认 3600s）+ 启动即对账；健康检查。
- `app/tests/`：33 个 unittest（fixtures 全离线），覆盖 adapter、catalog 失败恢复、Markdown 幂等/人工保护、Range、路径穿越、API。
- `Dockerfile`、`docker-compose.yml`（项目名 `research-kb`，library + syncthing，源 `:ro`）。
- `scripts/`：部署、Syncthing 配置/状态、Windows 安装、验收脚本。
- `config/config.example.json`、`config/stignore`。

### 部署
- Linux：library 容器 `research-kb-library:0.1.0`，API 端口 8765（主机绑定可配置）；Syncthing 容器（官方镜像 v2.1.5），管理 UI 回环 `8384`，同步端口 22000。
- 首次入库：统一 catalog 496 文档（482 可用）、版本 487；来源为财报归档与 Discord 导出。
- 客户端：Syncthing v2.1.5（管理 UI 回环 `18384`）与 Obsidian 1.13.7 静默安装并注册指定 Vault；登录启动项 `ResearchKB-Syncthing`。
- 双向同步 canary 正反均字节一致；人工笔记在重跑入库后保持不变。

### 修复（开发中发现）
- `__main__` 子命令解析 `--config` 位置错误（改为子解析器参数）。
- reports adapter 在 `sqlite3.Connection` 上设置属性失败（改为返回 `(conn, mode)`）。
- `text/plain` 等文本 MIME 缺少 charset。
- 非 ASCII 文件名导致 `Content-Disposition` 头非法、响应截断（改为 ASCII fallback + `filename*=UTF-8''`）。
- Syncthing v2 CLI/API 变化：`serve` 子命令、`<gui><apikey>` 嵌套、GUI 端口探测；Windows 保留 8384 段，改用 18384。
- 容器内绑定宿主 LAN IP 失败（改为容器内 `0.0.0.0`，由 compose 端口映射限定到配置的宿主地址）。

### 已知限制
- 4 条 HK 00700 done/ready 记录物理文件缺失，标记不可用（I004）。
- 无头环境无法核验 GUI 渲染；未做 reboot 持久化验证。
