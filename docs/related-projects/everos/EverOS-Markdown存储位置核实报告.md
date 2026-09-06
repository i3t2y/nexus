# EverOS 的 .md 文件到底存在哪？（基于真实源码核实）

> 日期：2026-09-06
> 核实方式：**直接拉取 GitHub 源码**，非凭印象
> 已核实文件：
> - `src/everos/core/persistence/memory_root.py`
> - `src/everos/config/default.toml`
> - `docs/storage_layout.md`
> - `src/everos/entrypoints/cli/commands/config_cmd.py`

---

## ⚠️ 前提说明

**EverOS 当前未安装在你本机，也不在本次工作区。** 已验证：

```
pip show everos          → WARNING: Package(s) not found: everos
python -c "import everos" → ModuleNotFoundError: No module named 'everos'
ls ~/.everos             → No such file or directory
```

工作区 `2026-09-05-15-51-16` 内只有我前几轮写的方案文档，**没有 EverOS 源码**。

因此以下结论全部来自 `EverMind-AI/EverOS` 仓库 `main` 分支的真实源码。
**若你实际安装的是某个具体版本（如 v1.2.3），路径可能与 main 分支有差异** —— 安装后请用第 3 节的 `everos config show` 复核一次。

---

## 0. 核心结论（先回答你的问题）

**都不是。EverOS 的 .md 存放在「运行 EverOS 的那台机器的本地文件系统」上，与七牛云 / 坚果云 / 夸克网盘 / Gmail 云盘 / 微软 OneDrive 等任何云存储没有任何原生关系。**

证据：
1. `default.toml` 中**不存在任何** S3 / R2 / WebDAV / OSS / Qiniu 配置段
2. `[sqlite]` 段只有 PRAGMA 参数（`journal_mode` / `synchronous` / `busy_timeout_ms` …），**没有路径**
3. `[lancedb]` 段只有 `read_consistency_seconds`，**没有路径**
4. `memory_root.py` 全程使用 `pathlib.Path`，纯本地文件系统语义

**唯一能让云存储参与的方式：把云盘挂载 / 同步到本地某个路径，再把 `EVEROS_ROOT` 指过去。EverOS 自己只看到本地路径。**

---

## 1. 定位 .md 读取入口（配置文件 / 环境变量 / CLI 参数 / 代码常量）

### 1.1 配置优先级（来源：`src/everos/config/default.toml` 头部注释，原文）

```
# Lookup order (later overrides earlier):
#   1. This file (shipped defaults; lowest priority)
#   2. <root>/everos.toml — user config (optional; root resolved by
#      resolve_root(): EVEROS_ROOT env > ~/.everos)
#   3. Environment variables — EVEROS_<SECTION>__<KEY>
#         e.g. EVEROS_SQLITE__BUSY_TIMEOUT_MS=10000
#   4. Programmatic init args (highest priority)
```

### 1.2 根路径解析规则（来源：`memory_root.py` 的 `MemoryRoot.resolve` docstring，原文）

```
Resolution: ``explicit_root`` > ``EVEROS_ROOT`` env > ``~/.everos``
```

| 项 | 名称 / 值 | 优先级 |
|---|---|---|
| **CLI 参数** | `--root <path>` | 最高 |
| **环境变量** | `EVEROS_ROOT` | 中 |
| **内置默认** | `~/.everos` | 兜底 |
| 解析函数 | `resolve_root()` @ `src/everos/config/settings.py` | — |
| 路径管理器 | `MemoryRoot` 类 @ `src/everos/core/persistence/memory_root.py` | — |
| 用户配置文件 | `<root>/everos.toml` | — |
| 策略配置文件 | `<root>/ome.toml`（热重载，~2s） | — |

**实现细节**：`--root` 的实现方式是把值写进环境变量。
`config_cmd.py` 原文：
```python
if root:
    os.environ["EVEROS_ROOT"] = root
resolved = resolve_root(root)
```

⚠️ 因此 `--root` 只对**当前进程**生效，跨进程/跨命令需 `export EVEROS_ROOT=...`。

### 1.3 目录名常量（`memory_root.py`）

```python
_DEFAULT_SCOPE_ID    = "default"
_DEFAULT_APP_DIR     = "default_app"
_DEFAULT_PROJECT_DIR = "default_project"
```

即 `app_id="default"` 在磁盘上落地为 `default_app`，`project_id="default"` 落地为 `default_project`。

---

## 2. 「Markdown 源文件目录」vs「索引 / 元数据存储位置」

### 2.1 结论：是「集中写入」，不是「记录原始路径」

EverOS **自己把 .md 写进 memory root**，且是它私有的存储格式（带 YAML frontmatter + HTML 注释 entry 标记）。它不是把外部 .md 的路径登记进索引，而是**生成并持有**这些 .md。

### 2.2 完整目录树（来源：`docs/storage_layout.md` 原文）

```
<memory-root>/                             默认 ~/.everos
│
├── <app_id>/                              "default" → default_app
│   └── <project_id>/                      "default" → default_project
│       ├── users/
│       │   └── <user_id>/
│       │       ├── user.md                        single-file rewrite (profile)
│       │       ├── episodes/
│       │       │   └── episode-<YYYY-MM-DD>.md
│       │       ├── .atomic_facts/                 (隐藏)
│       │       │   └── atomic_fact-<YYYY-MM-DD>.md
│       │       └── .foresights/                   (隐藏)
│       │           └── foresight-<YYYY-MM-DD>.md
│       ├── agents/
│       │   └── <agent_id>/
│       │       ├── .cases/                        (隐藏)
│       │       │   └── agent_case-<YYYY-MM-DD>.md
│       │       └── skills/
│       │           └── skill_<name>/
│       │               ├── SKILL.md
│       │               ├── references/            (optional)
│       │               └── scripts/               (optional)
│       └── knowledge/                             user-visible (shared / global)
│
├── .index/                              system-managed, rebuildable
│   ├── sqlite/
│   │   ├── system.db                    cascade queue (md_change_state) / buffer / audit / LSN
│   │   ├── ome.db                        Offline Memory Engine state
│   │   ├── ome.aps.db                    APScheduler jobstore
│   │   └── ome.db.lock                   OME single-engine guard
│   └── lancedb/
│       └── <kind>.lance/                one directory per LanceDB table
│
├── ome.toml                             user-editable OME strategy overrides (hot-reloaded)
└── .tmp/                                staging dir for batch / multi-step writes
```

> 另有 `.lock`（单进程锁锚点），由 `MemoryRoot.lock_file` 暴露。

### 2.3 三种存储策略

| 策略 | 文件名 | 变更方式 | 例子 |
|---|---|---|---|
| **Daily-log append** | `<FILE_PREFIX>-<YYYY-MM-DD>.md` | 追加 entry | episode / atomic_fact / foresight / agent_case |
| **Skill-named dir** | `skills/skill_<name>/SKILL.md` | 覆盖整个文件 | agent 技能（程序性记忆） |
| **Single-file rewrite** | `user.md` | 覆盖整个文件 | 用户画像 |

### 2.4 真相源 vs 派生索引（关键区分）

`docs/storage_layout.md` 原文：
> "The contents are the **source of truth**; SQLite and LanceDB are **derived indexes** that can be rebuilt from markdown alone."

| 层 | 位置 | 存什么 | 能否丢 |
|---|---|---|---|
| **真相源** | `<root>/<app>/<project>/{users,agents,knowledge}/` | .md 记忆本体 | 🔴 丢了=丢记忆 |
| **SQLite** | `<root>/.index/sqlite/system.db` | **仅系统/协调表**：`md_change_state`（级联队列）、`memcell`（边界账本）、`unprocessed_buffer`、`conversation_status`、`cluster`、`knowledge`、`reflection_report`。**不存业务行** | 🟡 丢队列/审计 |
| **LanceDB** | `<root>/.index/lancedb/<kind>.lance/` | **业务行**（text / vector / tokens / metadata），键控 `<owner_id>_<entry_id>` | 🟡 可重建 |

### 2.5 原子写语义

`MarkdownWriter` 使用**同目录临时文件** `.<name>.tmp.<uuid>` + `os.replace`。

⚠️ 注意：不是 `.tmp/` 目录。文档原文：
> "`MarkdownWriter` does *not* use this for atomic single-file writes; it uses a same-directory temp file to guarantee a same-filesystem rename. This directory is reserved for callers that need scratch space outside any single target directory."

---

## 3. 如何验证「当前环境真实生效的路径」

### 3.1 ✅ 最直接：官方命令 `everos config show`

来源：`src/everos/entrypoints/cli/commands/config_cmd.py`（docstring: `everos config show — display effective configuration`）

```bash
everos config show
# 或对比不同 root 的解析结果
everos config show --root /path/to/test
```

输出内容（代码原文）：
```
Root: {resolved}                                    ← 解析后的绝对路径
Config: {root}/everos.toml   或 "(no everos.toml found, using defaults)"
Strategy: {root}/ome.toml                           ← 存在才打印

[memory] [api] [sqlite] [lancedb] [llm] [multimodal]
[embedding] [rerank] [boundary_detection] [memorize]
[clustering] [search] [knowledge]                   ← 各段生效值
```
`api_key` 自动打码（`sk-ab****xyzw`）。

⚠️ **注意一个坑**：`_SECTION_NAMES` 元组里**没有** `cascade` 和 `observability` 两个段，所以这两个段的值**不会被打印**。

### 3.2 其他验证手段

```bash
# ① 查环境变量
echo $EVEROS_ROOT          # Linux / macOS / Git Bash
echo %EVEROS_ROOT%         # Windows CMD
echo $env:EVEROS_ROOT      # PowerShell

# ② init 后看是否生成了预期文件
everos init --root /path/to/root
ls -la /path/to/root       # 应见 everos.toml + ome.toml + .index/

# ③ 查级联队列反推真实 .md 路径
sqlite3 <root>/.index/sqlite/system.db \
  "SELECT * FROM md_change_state LIMIT 20;"

# ④ 数一下实际写了多少 .md
find <root> -name "*.md" -not -path "*/.index/*" | head -50
```

### 3.3 开启路径级日志（可选）

`default.toml` 的 `[observability]` 段：
```toml
capture_content = false   # true 时会把 .md 路径作为 span input/output 发出
```
注释原文：
> "false (default) = metadata only; true also emits query / extracted memory / .md paths as span input/output (redacted + truncated)."

需同时 `enabled = true` 并配 OTLP endpoint。

---

## 4. 修改路径 + 让索引同步 / 重建

### 4.1 修改方式

```bash
# 方式 A：环境变量（推荐，跨进程一致）
export EVEROS_ROOT=/path/to/vault/99-Memory
everos server start

# 方式 B：CLI 参数（仅当前进程）
everos server start --root /path/to/vault/99-Memory

# 方式 C：改 <root>/everos.toml（注意：改不了 root 本身，root 由 resolve_root() 决定）
```

⚠️ `everos.toml` 位于 root **内部**，所以它配置的是模型/检索等参数，**不能用来改 root 自身位置**。

### 4.2 重建索引（官方给出的方法）

`docs/storage_layout.md` 原文：
> "Both layers are **fully derivable from markdown** — wipe `.index/` and the in-process cascade subsystem re-builds everything by scanning the user-visible tree (the durable `md_change_state` SQLite queue covers crash-recovery replay)."

**步骤：**
```bash
# 1. 停服务
# 2. 删派生索引
rm -rf <root>/.index/
# 3. 重启 → cascade 扫描 users/agents/knowledge 重建全部索引
everos server start
```

**或等自动重建**（`default.toml` `[cascade]` 段）：
```toml
optimize_rebuild_interval_seconds = 43200.0   # 12 小时
optimize_heartbeat_seconds = 60.0
optimize_prune_interval_seconds = 300.0
```

**增量同步**由两部分承担：
- 正常运行：`memory/cascade/` 子系统的 md → LanceDB 增量同步
- 崩溃恢复：从 `system.db` 的 `md_change_state` 队列重放

### 4.3 ⚠️ 重建的真实代价

重建要**重新 embedding 全部 .md 内容**。这直接印证了我们前几轮的判断：
> v4/v5 里建议把 `.index/lancedb/` 存 R2 快照，正是为了把"冷启动重算"降级为"冷启动下载"。

---

## 5. 对你方案的三条直接结论

### ① EverOS 不能直连云存储路径
`EVEROS_ROOT` 必须是**本机可见路径**。不能填 `qiniu://...` / `webdav://...` / `r2://...`。

### ② 可行接法：先挂载/同步到本地，再指过去

| 云存储 | 可行性 | 做法 |
|---|---|---|
| **坚果云** | ✅ 最顺 | 坚果云客户端同步文件夹 → `EVEROS_ROOT=~/坚果云/vault/99-Memory` |
| **七牛 Kodo / R2** | ✅ | `rclone mount` 挂载 → `EVEROS_ROOT=/mnt/qiniu/99-Memory` |
| **OneDrive** | △ | 客户端同步文件夹可用，但需实测文件锁行为 |
| **夸克网盘** | ❌ 存疑 | 无官方 WebDAV / 挂载能力，基本不可行 |
| **Gmail 云盘** | ❌ | 不是文件系统，无挂载手段 |

### ③ ⚠️ 强烈建议：不要让 EverOS 直接写在同步盘里

两个风险：
1. **`.index/`（SQLite WAL + LanceDB）被同步盘实时同步会膨胀 / 损坏** —— 必须在同步工具里排除 `.index/`、`.tmp/`（我们 v3 已列此条）
2. **撞上「单一写入者原则」** —— 若两端各跑一个 EverOS 写同一份 .md，必然冲突

**更稳的做法**：
```
EVEROS_ROOT → 本地纯目录（如 ~/everos-memory，不被任何同步盘覆盖）
      ↓ 定期单向推送（rclone / 坚果云）
云端备份副本
```
让 EverOS 写本地，云端只做备份，而不是让 EverOS 直接写在同步盘里。

---

## 6. 如果信息仍不够，需要你补充什么

本次核实受限于「EverOS 未安装」。若需进一步确认，请提供以下任一：

1. **实际安装环境**：`pip install everos` 后执行 `everos config show` 的完整输出
2. **具体版本号**：`everos --version`（main 分支可能与 v1.2.3 有差异）
3. **你想挂载的目标**：七牛 / 坚果云 / R2 中具体哪一个，以及是否已有 rclone 配置
4. 若你打算把 EverOS 部署到 HF Space：需要看该 Space 的 `entrypoint.sh` 与 Space Variables

---

## 附：本次核实涉及的全部源码位置

| 用途 | 路径 |
|---|---|
| 根路径解析 + 目录常量 | `src/everos/core/persistence/memory_root.py` |
| 内置默认配置 + 优先级说明 | `src/everos/config/default.toml` |
| 权威存储布局文档 | `docs/storage_layout.md` |
| 打印生效配置的 CLI | `src/everos/entrypoints/cli/commands/config_cmd.py` |
| `resolve_root()` 实现 | `src/everos/config/settings.py` |
| Markdown 读写原语 | `src/everos/core/persistence/markdown/` |
| 业务写入器 | `src/everos/infra/persistence/markdown/writers/` |
| md → LanceDB 同步 | `src/everos/memory/cascade/` |
| SQLite 表定义 | `src/everos/infra/persistence/sqlite/tables/` |
| LanceDB 表定义 | `src/everos/infra/persistence/lancedb/tables/` |
