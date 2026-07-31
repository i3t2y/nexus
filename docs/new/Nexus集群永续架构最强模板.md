# Nexus 集群永续架构最强模板

> **提炼日**: 2026-07-28 | **血统**: omn-merge `a5d92a6` + nexus `4fc098e` + hermes-agent `ad6df5e` + langgraph `41341457` + OmniRoute `v3.8.49`
>
> **三合一自包含文档**: 架构原则叙述 + 内嵌可复制骨架件 + 现役实证引用。
> 任何新 Nexus 节点照搬骨架件 + 按自身业务改逻辑层即可落地。
>
> **护栏纪律**: 本文 secret/token 值零入文，一律 env 占位名；测试用合成串；**git push 只能人工裁决，我永不自动 push HF**——本文 §7.2 骨架层 workflow 含 `git push --force` 自动化属 omn 血统模板叙述，部署前须改为 `workflow_dispatch` 显令点火（见修正项红线 + §7.2 注）。
>
> **组件血统实情(2026-07-29 核实)**: 本文继承自 omn-merge 五空间原型,§1 拓扑图/§2/§5 组件表含 `omniroute` 作五空间之一系 **omn 原型北区原貌**,非臆造亦非实装真组件。**Nexus 仓实装仅 4 组件**(`spaces/` = hermes/langgraph/claude-code/codex,**无 omniroute 目录**);`omniroute` 实指外部 `godiegosouzapw/OmniRoute` 模型路由网关,作 **Nexus 下游模型数据面后端独立部署调用不合码**(见 [[docs/archive/连接-gpt5.6sol.md]] "Agent→OmniRoute→Provider" 拓扑论证)。故拓扑图/组件表按 omn 原型原貌保留作速查;凡引 omniroute 行均应理解为外部下游组件血统参考,**不可当 Nexus 内建组件实施**(NEXUS_COMPONENT ARG 默认值 omniroute 同此,见 skeleton 注 + 部署/Dockerfile L32-33)。

---

## §0 为什么需要这套架构

HF Docker Space（PRO 层，2026-07 实测）五条硬约束：

| 约束 | 后果 | 本架构对策 |
|------|------|-----------|
| **CPU-Basic 48h 休眠自醒，冷启 ephemeral 盘丢** | `/data` 重启即可能丢运行态 | SQLite 件经 litestream→R2 持久化, boot 期 restore; **langgraph 主存 Postgres 不走 litestream**(见修正项⑧) |
| **7/16 后密集推送冻 build 权限** | 频繁改 Dockerfile 触 Rebuild 易撞冻 + 风控 | 三层解耦 + 版本驱逐 ARG，日常升级零 Rebuild |
| **30min 日志可见窗口** | boot 后未及时抓日志，证据永久丢 | cron 每 30min fetch-logs 落 evidence 分支 + 脱敏 |
| **无 shell 长驻进程管理** | 多子进程死无感知，SPACE 静默崩 | entrypoint daemon 模式 + 监督循环 STRICT/WARN |
| **跨 Space 协同无内建机制** | 5 组件孤岛，调度困难 | OmniRoute 统一 AI 网关 + Supabase Postgres 集群共享状态 |

**核心目标**：一次设计，五组件长期跑不崩、不被风控、证据不丢、升级零 Rebuild、集群协同。

---

## §1 Nexus 集群拓扑（五空间架构地图）

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           Nexus 集群 (i3t2y owner)                              │
│                                                                                 │
│  ┌─────────────┐    ┌──────────────┐    ┌──────────────┐    ┌───────────────┐  │
│  │   hermes    │    │  langgraph   │    │  omniroute   │    │ claude-code   │  │
│  │ 主控大脑    │◄──►│ 编排持久化   │◄──►│ AI 统一网关  │◄──►│ 强推理 Agent │  │
│  │ port: 7860  │    │ port: 7860   │    │ port: 7860   │    │ port: 7860    │  │
│  │ SQLite+R2   │    │ Postgres+R2  │    │ SQLite+R2    │    │ 无状态+R2    │  │
│  └──────┬──────┘    └──────┬───────┘    └──────┬───────┘    └───────┬───────┘  │
│         │                  │                   │                    │           │
│         └──────────────────┼───────────────────┼────────────────────┘           │
│                            │                   │                                │
│                    ┌───────┴───────┐    ┌──────┴──────┐                        │
│                    │ Supabase Postgres│   │ codex       │                        │
│                    │ (LangGraph     │    │ 快速编码    │                        │
│                    │ AsyncPostgres  │    │ port: 7860  │                        │
│                    │ Saver 共享)    │    │ 无状态+R2   │                        │
│                    └───────────────┘    └─────────────┘                        │
│                                                                                 │
│  ┌──────────────────────────────────────────────────────────────────────────┐   │
│  │             Cloudflare Worker 统一入口 (workers/gateway)                  │   │
│  │  路由规则: /hermes/* → hermes | /langgraph/* → langgraph                  │   │
│  │            /v1/* → omniroute | /code/* → claude-code | /codex/* → codex  │   │
│  └──────────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────┘

持久化层:
  R2 真桶 (Nexus 现役四桶, 共用, 修正项 red 对齐 a142da9 现状):
    nexus-backups   / nexus-checkpoints / nexus-skills / nexus-artifacts
  (模板规约每组件独立 bucket=nexus-<component>-dev/prod 见 §2.1, 为相邻部署蓝图)
  共享 Supabase Postgres (仅 LangGraph AsyncPostgresSaver 使用 — 非走 litestream 见 §5.2)
  Dataset 二线快照 (CommitScheduler 5min 全量 + 30min 日志归档)
  (Dataset 永远只读 mount; 运行态可写件去 Bucket rw — 修正项①)
```

### §1.1 三层解耦（所有 Space 共用架构地基）

```
┌─────────────────────────────────────────────────────────┐
│ 环境层 (GHCR base 镜像)  — 低频变, 触 Rebuild              │
│   ARG BASE_IMAGE → 预构建镜像                             │
│   Dockerfile + start.sh + README.md (.gitattributes)      │
├─────────────────────────────────────────────────────────┤
│ 逻辑层 (HF Dataset)      — 高频变, 零 Rebuild              │
│   业务件全部: entrypoint.sh / gate.js / init.sh           │
│               litestream.yml / package.json               │
│   热更方式: git push Dataset + 显式 manual restart_space   │
│   (dataset push 不触发挂它的 Space rebuild, 见下注)         │
├─────────────────────────────────────────────────────────┤
│ 运行态持久件层 (R2 via litestream + /data ephemeral)       │
│   <service>.sqlite → R2 replicate (跨云容灾真持久)         │
│   /data 卷 = 允许丢, R2 是数据主路径                         │
└─────────────────────────────────────────────────────────┘
```

> **修正项①③（manage-spaces / spaces-overview 铁义）**: HF 官方 manage-spaces Note 逐字: "Models, datasets, Spaces **always mounted as read-only**. Only **storage buckets support read-write** mounts." 故逻辑层通过 HF Dataset 挂载时**永远只读**，运行态可写件必须去 **Storage Bucket**（唯一支持 RW）。热更逻辑层不是"push Dataset → Space 自动生效":HF spaces-overview 原文 "the Space" 指重建的是 Space 自身 repo（即骨架层 Dockerfile/start.sh push），**与挂载的 Dataset 无关**; manage-spaces TaskScheduler 例旁证: Space 从 sleep/durablepause 醒来靠显式 `request_space_hardware` 调用，**非 dataset push 事件**。故变更逻辑层正确流程 = `git push Dataset + 显式 restart_space`，不撞付费墙不触 Rebuild。

> **修正项④（挂载形态对齐 omn-merge 现役）**: 上图逻辑层标 "(HF Dataset)" 为血统模板抽象分层（audit:188-205 三轴分层: 逻辑层五件=Dataset RO / 运行态 RW 件=Storage Bucket 挂 /data RW / R2 备份层不动）。**但 omn-merge 现役实装并非只读 Volume 挂载**:start.sh 走 `hf download --local-dir /tmp/logic + cp -a /tmp/logic/. /logic/` 拷出**可写副本**（本文件 §3.2 L215-221 同此），entrypoint.sh:246 `npm install` 写 `/logic/node_modules` 为证。只读 Dataset Volume 挂载是 audit:268 "**本轮勘探裁决闭环非实施**" 的未施蓝图（即下注所述 a142da9 实装形态）。Nexus 现役已落 a142da9 改走 **Bucket rw 单挂逻辑层**:见 spaces/hermes/*。

### §1.2 铁律汇总

| 编号 | 铁律 | 违反后果 |
|------|------|----------|
| L1 | **R2 bucket 永不双写**: dev/prod 不得同时在线写同一 R2 bucket | 数据损坏，恢复极难 |
| L2 | **三件永不再改定态**: `Dockerfile` / `start.sh` / `README.md` | 触 Rebuild，版本驱逐 ARG 体系失效 |
| L3 | **逻辑层 Dataset 根平铺**: 必须存在 `entrypoint.sh` | start 与逻辑层契约断裂，boot FATAL |
| L4 | **Secret 值零入文/git**: 一律 env 占位，测试用合成串 | Secret 泄露，不可撤销 |
| L5 | **LangGraph Library Mode**: 禁用 `langgraph-api` Server 二进制（需企业 License） | License 违规 + 意外费用 |
| L6 | **langgraph-checkpoint-sqlite≥3.0.1**: 修复 SQLi+RCE CVE | 容器 RCE，完全被控 |
| L7 | **Claude Code / Codex 用 API Key 模式**: 容器内禁 OAuth 浏览器跳转 | boot 卡死，服务无法启动 |
| L8 | **litestream sync-interval≥10s**: 防 R2 Class A 配额超限 | 免费额度爆，意外账单 |
| 修正⑧ | **litestream 仅 SQLite WAL, 不懂 Postgres**: langgraph Postgres 主存禁用 litestream, 走 Supabase PITR/db dump | 误配=技术错误, 数据不被备份 |
| 修正① | **Dataset 永远只读 mount, Bucket 唯一 RW**: 运行态可写件去 Bucket | manage-spaces 铁义违反=写只读区失败 |
| 修正② | **Dataset git 版本化回退锚, Bucket 无锚**: 逻辑层放 Dataset 取版本回滚 | 误用 Bucket 存逻辑=无回退点 |

---

## §2 五组件清单与 Space 命名规约

| 组件 | Space 名 | 逻辑层 Dataset | 用途 | 端口 | DB 路径 |
|------|---------|---------------|------|------|---------|
| **hermes** | `i3t2y/nexus-hermes` | `i3t2y/nexus-hermes-logic` | 主控大脑 + 日志归档 + CommitScheduler | 7860 | `/data/hermes.sqlite` |
| **langgraph** | `i3t2y/nexus-langgraph` | `i3t2y/nexus-langgraph-logic` | 编排 + AsyncPostgresSaver | 7860 | Supabase Postgres |
| **omniroute** | `i3t2y/nexus-omniroute` | `i3t2y/nexus-omniroute-logic` | AI 统一网关（核心流量入口） | 7860 | `/data/storage.sqlite` |
| **claude-code** | `i3t2y/nexus-claude` | `i3t2y/nexus-claude-logic` | Claude Code headless Agent | 7860 | `/data/claude.sqlite` |
| **codex** | `i3t2y/nexus-codex` | `i3t2y/nexus-codex-logic` | Codex CLI headless Agent | 7860 | `/data/codex.sqlite` |

### §2.1 相邻部署（dev/prod 双 Space）

每组件两 Space 隔离，命名空间隔离 token，爆炸半径各半：

| 项 | dev (金丝雀) | prod (生产) |
|----|-------------|------------|
| Space | `i3t2y/nexus-<component>` | `i3t2y/nexus-<component>-prod` |
| 逻辑层 Dataset | `i3t2y/nexus-<component>-logic` | `i3t2y/nexus-<component>-logic-prod` |
| R2 Bucket | `nexus-<component>-dev` (或现役共用 `nexus-*` 四桶, 见 §1 注) | `nexus-<component>-prod` |
| token | `HF_TOKEN_DEV` (仅写 dev 范围) | `HF_TOKEN_PROD` (仅写 prod 范围) |
| 升级路径 | 逻辑层: `git push Dataset` + **显式 manual restart_space** (修正项③, dataset push 不触挂载 Space rebuild) | 仅 `workflow_dispatch` 显令点火 |

**晋级生产 = 变量切换 + Restart，零 Rebuild 零净室首跑**
> 注:dev 逻辑层同步走 push 自触 sync-logic workflow（仅推 Dataset repo, 零 Rebuild）, **但 Space 醒来生效须显式 `restart_space`**——见修正项③ manage-spaces 原文, dataset push 不触发挂它的 Space rebuild。骨架层（Dockerfile/start.sh）push 触 Rebuild 走 §7.2, **该 workflow 含 `git push --force` 自动化属 omn 血统模板, 部署前须改 dispatch 显令点火（红线: 我永不自动 push HF）**。

---

## §3 三件永不再改定态（版本驱逐）

所有五组件共用同一 Dockerfile + start.sh 模板，仅 `BASE_IMAGE` ARG 区分组件：

### §3.1 Dockerfile 骨架（三件之一）

```dockerfile
# ══════════════════════════════════════════════════════════════
# Nexus 集群通用 Dockerfile — 三件定态, 永不再改业务逻辑
# 版本号全部驱逐为 ARG 默认值, 升级改 ARG 或 GHCR 推新 tag
# ══════════════════════════════════════════════════════════════

# ARG = build 期值 / HF Variable buildtime 覆盖 / 默认值兜底 三层优先级
ARG BASE_IMAGE=ghcr.io/i3t2y/nexus-base:stable
FROM ${BASE_IMAGE}
USER root

ARG BASE_IMAGE                                    # FROM 后重声明继承全局值
ENV BASE_IMAGE=${BASE_IMAGE}                      # 转存 runtime env

ARG LITESTREAM_VERSION=0.5.9
ENV LITESTREAM_VERSION=${LITESTREAM_VERSION}

ARG HF_HUB_RANGE=">=1.0,<2.0"                    # 双引号包防注入
ENV HF_HUB_RANGE=${HF_HUB_RANGE}

ARG NEXUS_COMPONENT=omniroute                     # 组件名 (hermes/langgraph/omniroute/claude/codex)
ENV NEXUS_COMPONENT=${NEXUS_COMPONENT}

# /data 软链到 /app/data (HF Space persistent volume 规范路径)
RUN mkdir -p /data /app/data && ln -sfn /data /app/data || true

COPY start.sh /start.sh
RUN chmod +x /start.sh

EXPOSE 7860
ENTRYPOINT ["/start.sh"]
```

### §3.2 start.sh 自适应引导（三件之一，110 行内三段）

```sh
#!/bin/sh
# ══════════════════════════════════════════════════════════════════
# Nexus 集群通用 start.sh — 三件定态, 永不再改
# 与逻辑层唯一契约: Dataset 根必须存 entrypoint.sh
# ══════════════════════════════════════════════════════════════════
set -e
echo "[start] 启动 $(date '+%F %T') component=${NEXUS_COMPONENT:-(未注入)}"
echo "[start] 基础镜像: ${BASE_IMAGE:-(未注入 ENV)}"

# ── 1. 环境自愈 (GHCR base 预装则跳过; 裸上游则 apt+pip+curl 补装) ──
# 注: 本文 §12.1 base Dockerfile 预装全工具链 (curl/jq/python3/pip/sqlite3/build-essential/git/node/litestream/hf),
#     正常路径此段全跳 (_need_install=0); 此段仅裸上游 Rebuild 兜底。两处工具链描述去冗对齐, 不需重复维护。
_need_install=0
for t in python3 curl pip3 jq sqlite3; do command -v "$t" >/dev/null 2>&1 || _need_install=1; done
command -v litestream >/dev/null 2>&1 || _need_install=1
{ command -v hf >/dev/null 2>&1 || command -v huggingface-cli >/dev/null 2>&1; } || _need_install=1

if [ "$_need_install" = "1" ]; then
  command -v apt-get >/dev/null 2>&1 || { echo "[start] FATAL: 非 Debian 系"; exit 1; }
  apt-get update -qq && apt-get install -y --no-install-recommends \
    curl jq python3 python3-pip sqlite3 ca-certificates build-essential && \
    rm -rf /var/lib/apt/lists/*
  pip3 install --no-cache-dir --break-system-packages \
    "huggingface_hub${HF_HUB_RANGE:->=1.0,<2.0}"
  # litestream 拉取 (GHCR base 已预装则此段跳过)
  _ls_v="${LITESTREAM_VERSION:-0.5.9}"
  _arch=$(uname -m | sed 's/aarch64/arm64/;s/x86_64/amd64/')
  curl -fsSL "https://github.com/benbjohnson/litestream/releases/download/v${_ls_v}/litestream-${_ls_v}-linux-${_arch}.tar.gz" \
    | tar -xz -C /usr/local/bin litestream && chmod +x /usr/local/bin/litestream
fi

echo "[start] 环境就绪: litestream=$(litestream version 2>/dev/null||echo n/a) component=${NEXUS_COMPONENT}"

# ── 2. 变量校验 (HF_TOKEN 可选: 公共 Dataset 无需令牌) ──
# 注: 变量名 LOGIC_BUCKET_REPO 沿用 omn 血统, 实为 Dataset repo (repo_type=dataset);
#     a142da9 已修正 Bucket vs Dataset 概念混用 (bootstrap 改 hf buckets sync 拉真 Bucket)。
[ -n "$LOGIC_BUCKET_REPO" ] || { echo "[start] FATAL: 缺 LOGIC_BUCKET_REPO"; exit 1; }

# ── 3. 拉取逻辑层 (修正项⑦ 竞速根治: 先锁 Dataset HEAD commit_id 再按 revision 拉) ──
# 防 boot vs sync-logic push 竞速拉旧池 (boot#4 15:30Z 拉 8 员旧池事件实证)
# list_repo_commits 首个 commit_id = HEAD 锁同点 + --revision 锁全件同快照
mkdir -p /tmp/logic
_rev=$(LOGIC_BUCKET_REPO="$LOGIC_BUCKET_REPO" python3 -c '
import os, sys
try:
    from huggingface_hub import HfApi
    commits = list(HfApi().list_repo_commits(os.environ["LOGIC_BUCKET_REPO"], repo_type="dataset"))
    print(commits[0].commit_id)
except Exception as e:
    sys.stderr.write(f"[start] WARN: HEAD resolve失败 回退main: {e}\n")
' 2>/tmp/start_rev.err) || true

_tk=""; [ -n "$HF_TOKEN" ] && _tk="--token $HF_TOKEN"
_rev_arg=""; [ -n "$_rev" ] && _rev_arg="--revision $_rev"
echo "[start] 拉取逻辑层 repo=${LOGIC_BUCKET_REPO} rev=${_rev:-main}"

hf download "$LOGIC_BUCKET_REPO" --repo-type dataset \
  --local-dir /tmp/logic $_tk $_rev_arg --quiet \
  || { echo "[start] FATAL: 逻辑层拉取失败"; cat /tmp/start_rev.err 2>/dev/null; exit 1; }

mkdir -p /logic && cp -a /tmp/logic/. /logic/ && \
  chmod +x /logic/*.sh 2>/dev/null || true
rm -rf /tmp/logic

echo "[start] 逻辑层就绪 → exec /logic/entrypoint.sh"
exec /logic/entrypoint.sh
```

### §3.3 README.md（三件之一，纯 HF frontmatter）

```yaml
---
title: Nexus <Component>
sdk: docker
app_port: 7860
pinned: false
license: mit
---
```

> **铁律**: README.md 不入版本号，不入业务描述，不入 token。

---

## §4 逻辑层骨架件（Dataset 根平铺，高频变，零 Rebuild）

### §4.1 entrypoint.sh daemon 编排（PID 1 主控）

```sh
#!/bin/sh
# ══════════════════════════════════════════════════════════════════
# Nexus 集群通用 entrypoint.sh daemon 编排
# PID 1 主监控循环; 非 exec 接管; 多子进程全 & + $! 捕获
# 组件业务差异仅在 ── 2. 启动上游业务服务 ── 段内
# ══════════════════════════════════════════════════════════════════
set -eo pipefail

# ── PID 声明 ──
SVC_PID=""; INIT_PID=""; LS_PID=""; GATE_PID=""; SCHED_PID=""

# ── 优雅停 ──
_cleanup_done=0
_forward_signal() {
  for pid in "$SVC_PID" "$INIT_PID" "$LS_PID" "$GATE_PID" "$SCHED_PID"; do
    [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && kill -"$1" "$pid" 2>/dev/null || :
  done
}
_shutdown() {
  [ "$_cleanup_done" = 1 ] && return; _cleanup_done=1
  echo "[entry] shutdown signal received, graceful stop..."
  _forward_signal TERM
  g=0; while [ "$g" -lt 50 ]; do
    all_dead=1
    for pid in "$SVC_PID" "$INIT_PID" "$LS_PID" "$GATE_PID" "$SCHED_PID"; do
      [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && all_dead=0
    done
    [ "$all_dead" = 1 ] && break
    sleep 0.1; g=$((g+1))   # grace ~5s
  done
  _forward_signal KILL
  for pid in "$SVC_PID" "$INIT_PID" "$LS_PID" "$GATE_PID" "$SCHED_PID"; do
    [ -n "$pid" ] && wait "$pid" 2>/dev/null || :
  done
  echo "[entry] shutdown complete"
}
trap '_shutdown' TERM INT

# ── 0. 数据目录初始化 ──
# 修正项⑥ 铁律: 运行态所有可写件全去 /data (Bucket RW 挂) 或 /tmp, 严禁写 /logic 逻辑层只读区
#   唯一写 /logic = entrypoint.sh:246 npm install 一次性 (omn-merge). /data/logs 失败=Dataset 只读消解, Bucket /data RW 专职日志/DB 运行态。
DATA_DIR="${DATA_DIR:-/data}"                 # ephemeral POSIX, R2 是数据主路径
COMPONENT="${NEXUS_COMPONENT:-omniroute}"
DB_PATH="${DATA_DIR}/${COMPONENT}.sqlite"        # SQLite 主件 → litestream 复制 R2 (见 §5)
LOG_DIR="${LOG_DIR:-/data/log}"                  # 运行态日志去 /data/log (Bucket RW), 非 /logic
mkdir -p "$DATA_DIR" "$LOG_DIR"
echo "[entry] DATA=$DATA_DIR DB=$DB_PATH LOG=$LOG_DIR COMPONENT=$COMPONENT"

# ── 1. litestream restore (R2 → 本地 DB, 持久化根) ──
if [ -n "$R2_BUCKET" ] && command -v litestream >/dev/null 2>&1; then
  echo "[entry] litestream restore 开始..."
  DB_TMP=$(mktemp "/tmp/ls_restore_XXXXXX.sqlite")
  rc=0
  litestream restore -config /logic/litestream.yml -o "$DB_TMP" \
    2>/tmp/ls_restore.err || rc=$?
  if [ "$rc" = "0" ] && [ -s "$DB_TMP" ]; then
    # quick_check + 原子 mv
    _qc=$(sqlite3 "$DB_TMP" "PRAGMA quick_check;" 2>/dev/null | head -1)
    if [ "$_qc" = "ok" ]; then
      mv "$DB_TMP" "$DB_PATH"
      echo "[entry] restore 成功: DB=$DB_PATH quick_check=ok"
    else
      echo "[entry] WARN: restore quick_check 失败($_qc), 保留空库继续"
      rm -f "$DB_TMP"
    fi
  else
    echo "[entry] WARN: restore rc=$rc 或空文件, 新库启动 (R2 可能首次)"
    rm -f "$DB_TMP"
    cat /tmp/ls_restore.err 2>/dev/null | head -5 || true
  fi
else
  echo "[entry] WARN: R2_BUCKET 未注入, 跳过 restore (ephemeral 模式)"
fi

# ── 2. 启动上游业务服务 (组件差异点 §4.2 各组件专属段) ──
# ▶▶▶ 此段由各组件 entrypoint.sh 覆写 ◀◀◀

# ── 3. 健康等待 (最多 180s 探 /healthz 内部端点) ──
_svc_port="${SVC_PORT:-3000}"
echo "[entry] 等待业务服务就绪 port=$_svc_port (最多 180s)..."
_waited=0
while [ "$_waited" -lt 180 ]; do
  _resp=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:${_svc_port}/healthz" \
    --max-time 3 2>/dev/null) || _resp="000"
  [ "$_resp" = "200" ] && { echo "[entry] 业务服务就绪 t=${_waited}s"; break; }
  sleep 2; _waited=$((_waited+2))
  kill -0 "$SVC_PID" 2>/dev/null || { echo "[entry] FATAL: 业务服务在等待期退出"; _shutdown; exit 1; }
done
[ "$_waited" -ge 180 ] && { echo "[entry] WARN: 业务服务 180s 未就绪, 继续 (gate 将代理)"; }

# ── 4. 业务初始化 (后台, init 幂等) ──
if [ -f /logic/init.sh ]; then
  bash /logic/init.sh & INIT_PID=$!
  echo "[entry] init 后台运行 PID=$INIT_PID"
fi

# ── 5. litestream 后台复制 (DB → R2, sync-interval 10s) ──
if [ -n "$R2_BUCKET" ] && command -v litestream >/dev/null 2>&1; then
  litestream replicate -config /logic/litestream.yml & LS_PID=$!
  echo "[entry] litestream replicate 后台 PID=$LS_PID"
fi

# ── 6. CommitScheduler 日志归档 (后台, 5min 触发) ──
if [ -f /logic/commit_scheduler.sh ] && [ -n "$LOG_PUBLIC_DATASET_REPO" ]; then
  bash /logic/commit_scheduler.sh & SCHED_PID=$!
  echo "[entry] CommitScheduler 后台 PID=$SCHED_PID"
fi

# ── 7. 启动网关 (业务对外暴露层) ──
node /logic/gate.js & GATE_PID=$!
echo "[entry] gate 后台 PID=$GATE_PID"

echo "[entry] 所有子进程已起: SVC=$SVC_PID INIT=$INIT_PID LS=$LS_PID GATE=$GATE_PID SCHED=$SCHED_PID"

# ── 8. 监督循环 (STRICT/WARN 策略) ──
# 注(修正项⑤): 本文件对 gate/SVC 退出采 STRICT 硬 exit 1 偏离 omn-merge 现役 fail-open 模式
#   omn-merge entrypoint.sh:202-212 注: "只告警不 exit, 上游前滚迁移让旧库自动进新 schema, 版本不齐仍可跑"
#   真硬 exit 1 仅 gate.js PSK 缺失 (gate.js:46-49) + entrypoint npm install FATAL (:249)
#   Nexus 若要硬断言须自写, omn-merge 给反例; 此处 STRICT 保留为血统模板叙述, 部署按业务裁决。
_init_logged=0; _sched_logged=0
while true; do
  # STRICT: 对外服务死 = 全部停 (血统模板; omn-merge 现役为 fail-open WARN, 见上注)
  kill -0 "$GATE_PID" 2>/dev/null || { echo "[entry] FATAL: gate 退出"; _shutdown; exit 1; }
  kill -0 "$SVC_PID"  2>/dev/null || { echo "[entry] FATAL: 业务服务退出"; _shutdown; exit 1; }
  # WARN: 内部进程死 = 记一次不 exit
  if [ -n "$INIT_PID" ] && ! kill -0 "$INIT_PID" 2>/dev/null && [ "$_init_logged" = 0 ]; then
    wait "$INIT_PID" 2>/dev/null; _rc=$?
    echo "[entry] WARN: init 退出 rc=$_rc (幂等可重跑, 主链不崩)"; _init_logged=1
  fi
  if [ -n "$SCHED_PID" ] && ! kill -0 "$SCHED_PID" 2>/dev/null && [ "$_sched_logged" = 0 ]; then
    echo "[entry] WARN: CommitScheduler 退出, 日志归档停止"; _sched_logged=1
  fi
  if [ -n "$LS_PID" ] && ! kill -0 "$LS_PID" 2>/dev/null; then
    [ "${LITESTREAM_STRICT:-0}" = 1 ] && { echo "[entry] FATAL: litestream strict"; _shutdown; exit 1; }
    echo "[entry] WARN: litestream 退出, DB 不再备份"; LS_PID=""
  fi
  sleep 1
done
```

### §4.2 各组件业务段（entrypoint.sh §2 差异替换处）

#### OmniRoute 业务段

```sh
# ── 2. OmniRoute Next.js 服务 ──
SVC_PORT="${OMNIROUTE_PORT:-3000}"
export NODE_OPTIONS="--max-old-space-size=4096"
cd /app && node server.js & SVC_PID=$!
echo "[entry:omniroute] OmniRoute 启动 PID=$SVC_PID port=$SVC_PORT"
```

#### Hermes 业务段（含保活机制）

```sh
# ── 2. Hermes Agent 服务 (s6-overlay 已管理, exec python3 app.py) ──
SVC_PORT="${HERMES_PORT:-8080}"
export HERMES_HOME="${HERMES_HOME:-/data/hermes}"
mkdir -p "$HERMES_HOME"
cd /app && python3 -u app.py --port "$SVC_PORT" --data-dir "$HERMES_HOME" & SVC_PID=$!
echo "[entry:hermes] Hermes 启动 PID=$SVC_PID port=$SVC_PORT"

# 保活辅助 (keepalive_relay.sh 内部互探, 间隔随机化)
if [ -f /logic/keepalive_relay.sh ]; then
  bash /logic/keepalive_relay.sh & echo "[entry:hermes] 保活后台已起"
fi
```

#### LangGraph 业务段

```sh
# ── 2. LangGraph FastAPI Library Mode (禁用 langgraph-api Server 二进制) ──
# L5 铁律: 使用 Library Mode FastAPI 承载 Graph, 不用 langgraph-api
SVC_PORT="${LANGGRAPH_PORT:-8000}"
export SUPABASE_URL="${SUPABASE_URL?FATAL: 缺 SUPABASE_URL}"
export SUPABASE_SERVICE_KEY="${SUPABASE_SERVICE_KEY?FATAL: 缺 SUPABASE_SERVICE_KEY}"
cd /app && uvicorn app:app --host 0.0.0.0 --port "$SVC_PORT" & SVC_PID=$!
echo "[entry:langgraph] LangGraph FastAPI 启动 PID=$SVC_PID port=$SVC_PORT"
```

#### Claude Code 业务段

```sh
# ── 2. Claude Code headless API 封装 ──
# L7 铁律: API Key 模式, 禁 OAuth 浏览器跳转
SVC_PORT="${CLAUDE_PORT:-8080}"
export ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY?FATAL: 缺 ANTHROPIC_API_KEY}"
cd /app && node claude_server.js --port "$SVC_PORT" & SVC_PID=$!
echo "[entry:claude] Claude Code 服务启动 PID=$SVC_PID port=$SVC_PORT"
```

#### Codex 业务段

```sh
# ── 2. Codex CLI headless 封装 ──
# L7 铁律: API Key 模式, codex exec 无头执行
SVC_PORT="${CODEX_PORT:-8080}"
export OPENAI_API_KEY="${OPENAI_API_KEY?FATAL: 缺 OPENAI_API_KEY}"
cd /app && node codex_server.js --port "$SVC_PORT" & SVC_PID=$!
echo "[entry:codex] Codex 服务启动 PID=$SVC_PID port=$SVC_PORT"
```

---

## §5 litestream R2 持久化（双轨方案 C 最优解）

> **修正项⑧ 边界警示**: litestream **仅懂 SQLite WAL**，**跑不起 Postgres**。本章方案仅适用于 SQLite 组件（omniroute/hermes/claude/codex）。**langgraph 主存 Supabase PostgreSQL 不走 litestream**（技术误配），正解见 §5.2 末 Supabase PITR / db dump 入 R2。

**架构原则**: SQLite 保留本地 ephemeral `/data`（POSIX 高性能，fsync 10-100μs vs NFS 1-10ms 相差 100 倍），R2 作主灾备（litestream 10s 增量 WAL），Dataset 作二线跨平台快照（CommitScheduler 5min 全量）。

### §5.1 litestream.yml 模板（组件专属，替换 `<component>` 名）

```yaml
# ══════════════════════════════════════════════════════════════
# Nexus litestream.yml — 每组件独立 R2 bucket (永不双写)
# ALL secrets 走 env 注入, 本文件零硬码凭据
# ══════════════════════════════════════════════════════════════

# Class A 配额减量: l0-retention-check-interval 5min 砍 LIST 20x
# 月量: ~26万 PUT (10s sync, 24h×30d) < R2 免费额 100万 ✓
# ⚠️ 严禁 sync-interval < 10s: 已有案例 480req/h vs 1/h 意外账单

dbs:
  - path: /data/<component>.sqlite        # 本地 DB (ephemeral POSIX, fsync μs 级)
    replica:
      type: s3
      bucket: ${R2_BUCKET}                # env 注入: nexus-<component>-dev/prod
      path: db/<component>.sqlite         # R2 内对象路径
      endpoint: https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com
      access-key-id: ${R2_ACCESS_KEY_ID}
      secret-access-key: ${R2_SECRET_ACCESS_KEY}
      region: auto
      sync-interval: 10s                  # ≥10s 防 Class A 超限 (L8 铁律)
      auto-recover: false                 # 严交 entrypoint 显式 restore, 不绕 guard

    # L0 保留窗: 5min 轮询砍 LIST 次数 20x
    l0-retention: 5m
    l0-retention-check-interval: 5m

snapshot:
  interval: 1h
  retention: 24h
```

### §5.2 LangGraph 组件的持久化特殊处理

> **修正项⑧ 边界警示（必读）**: litestream **仅懂 SQLite WAL**（WAL 模式增量复制），**跑不起 Postgres**。Nexus langgraph 主存是 Supabase PostgreSQL（物理 WAL），**litestream 读不了 Postgres 物理 WAL = 技术误配**。下块 litestream.yml 仅复制 langgraph 本地**轻量 metadata SQLite**（非核心 checkpoint），**绝不可误用于 Supabase Postgres 主存**。OmniRoute 用 SQLite 故 litestream 正用; Supabase Postgres 的正解见本节末。

LangGraph 使用 Supabase Postgres（AsyncPostgresSaver）作为 checkpoint 主存，无主 SQLite。litestream.yml 配置为空或指向轻量 metadata 库（**仅 metadata, 非 Postgres 主存**）：

```yaml
# langgraph 组件: 主状态在 Supabase Postgres (litestream 不懂 Postgres, 见上警示)
# 本块仅备份 langgraph 本地 metadata SQLite (非核心, 可无 litestream)
# ⚠️ 严禁将此 litestream.yml 用于 Supabase Postgres — 替它见本节末 Supabase 正解
dbs:
  - path: /data/langgraph.sqlite          # 仅本地 metadata (非 Postgres 主存)
    replica:
      type: s3
      bucket: ${R2_BUCKET}
      path: db/langgraph-meta.sqlite
      endpoint: https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com
      access-key-id: ${R2_ACCESS_KEY_ID}
      secret-access-key: ${R2_SECRET_ACCESS_KEY}
      region: auto
      sync-interval: 60s                  # metadata 轻量, 60s 足够
      auto-recover: false
snapshot:
  interval: 6h
  retention: 48h
```

**Supabase Postgres 正解（修正项⑧ 替代 litestream）**:

| 档位 | 机制 | RPO | 来源 |
|------|------|-----|------|
| **Pro+** | Supabase 自动 daily backup + PITR (Point-in-Time Recovery) | 2min | supabase.com/docs/guides/platform/backups |
| **免费档** | `supabase db dump` cron → 上传 R2 `nexus-backups` 桶 | 24h | 手动编排 |

```sh
# 免费档 cron 示例 (Supabase db dump → R2, 替代 litestream 误配)
# 每日 dump Postgres schema+data → R2 nexus-backups 桶
PG_CONN="${SUPABASE_POSTGRES_URL}"          # env 占位, 值零入文
R2_BUCKET_BACKUP="nexus-backups"
pg_dump "$PG_CONN" --no-owner --clean --if-exists \
  | gzip > "/tmp/langgraph_$(date -u +%F).sql.gz" && \
  r2cli put "$R2_BUCKET_BACKUP/langgraph/$(date -u +%F).sql.gz" \
    < "/tmp/langgraph_$(date -u +%F).sql.gz"
```
> Pro+ 档优先用 Supabase 自带 PITR (RPO 2min), 免费档才需上述 db dump 入 R2 兜底。**切勿对 Supabase Postgres 配置 litestream**。

### §5.3 CommitScheduler 日志归档（Dataset 二线 + 公开分级）

```sh
#!/bin/sh
# ══════════════════════════════════════════════════════════════════
# commit_scheduler.sh — Dataset 二线快照 + 日志公开归档
# 后台 daemon, 5min 触发一次全量 DB 快照推私有 Dataset
#                     30min 触发一次日志脱敏推公开 Dataset
# ══════════════════════════════════════════════════════════════════
set -e
COMPONENT="${NEXUS_COMPONENT:-omniroute}"
DATA_DIR="${DATA_DIR:-/data}"
DB_PATH="${DATA_DIR}/${COMPONENT}.sqlite"
PRIVATE_DATASET="${PRIVATE_SNAPSHOT_DATASET_REPO:-}"  # 私有快照库
PUBLIC_LOG_DATASET="${LOG_PUBLIC_DATASET_REPO:-}"      # 公开日志库 (可选)

_last_db_push=0
_last_log_push=0
echo "[sched] CommitScheduler 启动 component=$COMPONENT"

while true; do
  _now=$(date +%s)

  # ─── 5min 触发: 全量 DB 快照推私有 Dataset ───
  if [ "$((_now - _last_db_push))" -ge 300 ] && [ -n "$PRIVATE_DATASET" ]; then
    if [ -f "$DB_PATH" ]; then
      _snap_dir="/tmp/nexus_snap_$$"
      mkdir -p "$_snap_dir"
      # 原子 cp (WAL checkpoint 前快照)
      sqlite3 "$DB_PATH" ".backup ${_snap_dir}/${COMPONENT}.sqlite" 2>/dev/null && \
        echo "backup_ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "${_snap_dir}/meta.txt" && \
        python3 -c "
import os, hashlib
from huggingface_hub import HfApi
api = HfApi(token=os.environ.get('HF_TOKEN',''))
api.upload_folder(
    folder_path='${_snap_dir}',
    path_in_repo='snapshots/${COMPONENT}',
    repo_id=os.environ.get('PRIVATE_SNAPSHOT_DATASET_REPO',''),
    repo_type='dataset',
    commit_message='snap(${COMPONENT}): \$(date -u +%Y-%m-%dT%H:%M:%SZ)'
)" 2>/dev/null && _last_db_push="$_now" && \
        echo "[sched] DB 快照推送成功 t=$_now"
      rm -rf "$_snap_dir"
    fi
  fi

  # ─── 30min 触发: 日志脱敏推公开 Dataset (若配置) ───
  if [ "$((_now - _last_log_push))" -ge 1800 ] && [ -n "$PUBLIC_LOG_DATASET" ]; then
    _log_dir="/tmp/nexus_logs_$$"
    mkdir -p "$_log_dir"

    # 聚合表导出 (OmniRoute/Hermes: hourly_usage_summary + compression_analytics)
    if [ -f "$DB_PATH" ] && command -v sqlite3 >/dev/null 2>&1; then
      sqlite3 "$DB_PATH" -json \
        "SELECT provider,model,requests,tokens_input,tokens_output,cost
         FROM hourly_usage_summary ORDER BY hour DESC LIMIT 500;" \
        2>/dev/null > "${_log_dir}/hourly_usage.jsonl" || true

      sqlite3 "$DB_PATH" -json \
        "SELECT combo_name,provider,original_tokens,compressed_tokens,saved_percentage,duration_ms
         FROM compression_analytics ORDER BY created_at DESC LIMIT 200;" \
        2>/dev/null > "${_log_dir}/compression_analytics.jsonl" || true
    fi

    # gate logGate JSON (component=gate 行, 已脱敏)
    # 从 journald/容器日志拉 — 此处为占位, 真实依赖 fetch-logs workflow
    echo "[sched] 日志归档: $(ls ${_log_dir}/*.jsonl 2>/dev/null | wc -l) 文件"

    # 元数据
    echo "{\"component\":\"${COMPONENT}\",\"ts\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",\"nexus_version\":\"$(cat /logic/.version 2>/dev/null||echo unknown)\"}" \
      > "${_log_dir}/meta.json"

    python3 -c "
import os
from huggingface_hub import HfApi
api = HfApi(token=os.environ.get('HF_TOKEN',''))
api.upload_folder(
    folder_path='${_log_dir}',
    path_in_repo='logs/${COMPONENT}/\$(date -u +%Y/%m/%d)',
    repo_id=os.environ.get('LOG_PUBLIC_DATASET_REPO',''),
    repo_type='dataset',
    commit_message='logs(${COMPONENT}): \$(date -u +%Y-%m-%dT%H:%M:%SZ)'
)" 2>/dev/null && _last_log_push="$_now" && \
      echo "[sched] 日志推送成功 t=$_now"

    rm -rf "$_log_dir"
  fi

  sleep 60   # 1min 轮询粒度
done
```

---

## §6 网关层契约（gate.js，所有组件共用骨架）

```js
// ══════════════════════════════════════════════════════════════════
// Nexus 集群通用 gate.js — 认证 + 限流 + 透传 + 日志
// 组件差异仅在 SVC_PORT + 透传目标 (UPSTREAM_BASE)
// ══════════════════════════════════════════════════════════════════
'use strict';
const http = require('http');
const crypto = require('crypto');
const { createProxyMiddleware } = require('http-proxy-middleware');
const express = require('express');

const app = express();
const GATE_PORT = parseInt(process.env.GATE_PORT || '7860', 10);
const SVC_PORT  = parseInt(process.env.SVC_PORT  || '3000', 10);
const UPSTREAM_BASE = `http://127.0.0.1:${SVC_PORT}`;
const COMPONENT = process.env.NEXUS_COMPONENT || 'unknown';

// ── 认证 PSK (fail-closed: 缺/<16 即 FATAL exit) ──
// 修正项⑤: 此处为 omn-merge 真硬 exit 1 (gate.js:46-49), 区别于 entrypoint.sh 监督循环的 STRICT 误配;
//           omn-merge 唯二硬断言 = 此处 PSK 缺失 + entrypoint npm install FATAL。其余全 fail-open WARN。
const INTERNAL_PSK = process.env.INTERNAL_PSK || '';
if (!INTERNAL_PSK || INTERNAL_PSK.length < 16) {
  console.error(`[gate:${COMPONENT}] FATAL: INTERNAL_PSK missing or <16 chars`);
  process.exit(1);
}

function safeEqual(a, b) {
  if (!a || !b) return false;
  const ba = Buffer.from(a), bb = Buffer.from(b);
  if (ba.length !== bb.length) return false;
  return crypto.timingSafeEqual(ba, bb);
}

// ── 后台开关 (纯布尔 fail-closed) ──
const ADMIN_ENABLED = process.env.GATE_ADMIN_ENABLED === '1';

// ── 限流: 单阈值字节守卫 ──
const CTX_GUARD_ENABLED  = process.env.GATE_CTX_GUARD_ENABLED  !== '0';
const CTX_MAX_BYTES      = parseInt(process.env.GATE_CTX_MAX_BYTES      || '1500000', 10) || 1500000;
const CTX_BYTES_PER_TOKEN= parseInt(process.env.GATE_CTX_BYTES_PER_TOKEN|| '8',       10) || 8;

// ── 超时 ──
const UPSTREAM_TIMEOUT_MS = parseInt(process.env.GATE_UPSTREAM_TIMEOUT_MS || '30000', 10) || 30000;

let shuttingDown = false;
process.on('SIGTERM', () => { shuttingDown = true; });
process.on('SIGINT',  () => { shuttingDown = true; });

// ── 日志 (单行 JSON stderr, 无 headers/body/psk 脱敏) ──
let _reqId = 0;
function logGate(req, fields) {
  const line = JSON.stringify({
    ts: Date.now(), level: 'info', component: 'gate', nexus_component: COMPONENT,
    requestId: req._gateReqId, method: req?.method,
    path: req?._normPath || req?.path,
    httpStatus: fields.httpStatus, errorCode: fields.errorCode,
    elapsedMs: fields.elapsedMs, msg: fields.msg
  });
  process.stderr.write(line + '\n');
}

// ── 中间件: request ID + 计时 ──
app.use((req, res, next) => {
  req._gateReqId = ++_reqId;
  req._startTs   = Date.now();
  req._normPath  = req.path.replace(/[?#].*/, '').slice(0, 128);
  next();
});

// ── 健康端点 (无认证) ──
app.get('/healthz', async (req, res) => {
  if (shuttingDown) return res.status(503).json({ ok: false, reason: 'shutting_down' });
  try {
    const ctrl = new AbortController();
    setTimeout(() => ctrl.abort(), 2000);
    const r = await fetch(`${UPSTREAM_BASE}/healthz`, { signal: ctrl.signal });
    r.ok ? res.json({ ok: true, component: COMPONENT })
         : res.status(503).json({ ok: false });
  } catch {
    res.status(503).json({ ok: false });
  }
});

// ── /v1 路由: 认证 + 限流 + 透传 ──
app.use('/v1', (req, res, next) => {
  // 认证
  const auth = req.headers.authorization || '';
  if (!auth.startsWith('Bearer ') ||
      !safeEqual(auth.slice(7).trim(), INTERNAL_PSK)) {
    logGate(req, { httpStatus: 401, errorCode: 'unauthorized', elapsedMs: Date.now()-req._startTs });
    return res.status(401).json({ error: 'unauthorized' });
  }
  // 限流 (仅判 content-length 不缓冲 body 保流式)
  if (CTX_GUARD_ENABLED && req.method === 'POST') {
    const cl = parseInt(req.headers['content-length'] || '0', 10);
    if (cl > CTX_MAX_BYTES) {
      const estTokens = Math.floor(cl / CTX_BYTES_PER_TOKEN);
      logGate(req, { httpStatus: 413, errorCode: 'context_length_exceeded', elapsedMs: Date.now()-req._startTs, msg: `est=${estTokens}` });
      return res.status(413).json({ error: { type: 'context_length_exceeded', est_tokens: estTokens, limit_bytes: CTX_MAX_BYTES } });
    }
  }
  next();
});

// ── /v1 透传 (无具名子路径路由, 由上游自路由) ──
app.use('/v1', createProxyMiddleware({
  target: UPSTREAM_BASE,
  changeOrigin: true,
  proxyTimeout: UPSTREAM_TIMEOUT_MS,
  timeout: UPSTREAM_TIMEOUT_MS,
  on: {
    error: (err, req, res) => {
      logGate(req, { httpStatus: 502, errorCode: 'proxy_error', elapsedMs: Date.now()-req._startTs, msg: err.message.slice(0,80) });
      if (!res.headersSent) res.status(502).json({ error: 'bad_gateway' });
    }
  }
}));

// ── 后台管理路由 (布尔开关, 关时全 404) ──
app.use('/admin', (req, res, next) => {
  if (!ADMIN_ENABLED) return res.status(404).end();
  next();
});
// admin 路由业务层自实现

// ── 兜底 404 ──
app.use((req, res) => {
  logGate(req, { httpStatus: 404, errorCode: 'not_found', elapsedMs: Date.now()-req._startTs });
  res.status(404).json({ error: 'not_found' });
});

const server = http.createServer(app);
server.listen(GATE_PORT, () => {
  console.log(`[gate:${COMPONENT}] listening port=${GATE_PORT} svc_port=${SVC_PORT} admin=${ADMIN_ENABLED}`);
});
```

---

## §7 workflow 分流（六件制 × 5 组件 = 30 workflows）

命名规约: `<动作>-<层>-<component>.yml`

| 层 | 触 Rebuild? | dev (自触 push) | prod (显令 dispatch) |
|----|------------|----------------|---------------------|
| 逻辑层 | 否 | `sync-logic-<component>.yml` | `sync-logic-<component>-prod.yml` |
| 骨架层 | 是 | `sync-space-<component>.yml` | `sync-space-<component>-prod.yml` |
| 日志取证 | N/A | `fetch-<component>-logs.yml` | `fetch-<component>-prod-logs.yml` |

### §7.1 逻辑层同步 workflow（骨架，零 Rebuild）

```yaml
# sync-logic-<component>.yml
name: Sync logic to HF Dataset (<component> dev)
on:
  push:
    branches: [main]
    paths:
      - 'spaces/<component>/logic/**'
      - '.github/workflows/sync-logic-<component>.yml'
  workflow_dispatch:

jobs:
  sync:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.12' }

      - name: Upload logic files to Dataset (flat layout)
        env:
          HF_TOKEN: ${{ secrets.HF_TOKEN_DEV }}
        run: |
          pip install -q "huggingface_hub>=1.0,<2.0"
          LOGIC_DIR="spaces/<component>/logic"
          DATASET_REPO="i3t2y/nexus-<component>-logic"
          for f in entrypoint.sh gate.js init.sh litestream.yml package.json \
                   commit_scheduler.sh keepalive_relay.sh; do
            [ -f "${LOGIC_DIR}/$f" ] || continue
            hf upload "${DATASET_REPO}" "${LOGIC_DIR}/$f" "$f" \
              --repo-type dataset --token "$HF_TOKEN" \
              --commit-message "sync(logic/<component>): ${GITHUB_SHA::7} $f" || exit 1
          done
          echo "${GITHUB_SHA}" > /tmp/version.txt
          hf upload "${DATASET_REPO}" /tmp/version.txt ".version" \
            --repo-type dataset --token "$HF_TOKEN" \
            --commit-message "version: ${GITHUB_SHA::7}" || true

      - name: Verify sha256 readback (逐字节血缘验证)
        env:
          HF_TOKEN: ${{ secrets.HF_TOKEN_DEV }}
        run: |
          python3 - <<'EOF'
          import hashlib, os, sys
          from huggingface_hub import hf_hub_download
          dataset = "i3t2y/nexus-<component>-logic"
          logic_dir = "spaces/<component>/logic"
          files = ["entrypoint.sh","gate.js","init.sh","litestream.yml","package.json"]
          for f in files:
              src = f"{logic_dir}/{f}"
              if not os.path.exists(src): continue
              local  = hashlib.sha256(open(src,"rb").read()).hexdigest()
              remote = hashlib.sha256(
                  open(hf_hub_download(dataset, f, repo_type="dataset",
                                       token=os.environ["HF_TOKEN"]),"rb").read()
              ).hexdigest()
              if local != remote:
                  raise SystemExit(f"[MISMATCH] {f}: 血缘断裂! local={local[:8]} remote={remote[:8]}")
              print(f"[OK] {f}: {local[:8]}")
          EOF
```

### §7.2 骨架层同步 workflow（触 Rebuild，高风险）

> **红线注**: 下块 `on: push` + `git push --force` 自动化属 omn 血统模板叙述。**用户红线: git push 只能手动, 我永不自动 push HF**。部署前须改为**仅 `workflow_dispatch` 显令点火**（删 `on: push` 段），把 `git push` 从 build runner 自动改为人工触发后执行；或彻底改用 `huggingface_hub` API 上传骨架件（同 §7.1 逻辑层方式, 走 `hf upload` 而非 `git push --force`）。修正项见护栏纪律 L8 + §0。

```yaml
# sync-space-<component>.yml
# ⚠️ 红线: 部署前删 on: push 自触段, 改仅 workflow_dispatch 显令点火 (永不自动 push HF)
name: Sync Space skeleton to HF (<component> dev) [REBUILD TRIGGER]
on:
  workflow_dispatch:          # 修正: 仅显令点火, 删原 on: push 自动段 (红线: 我永不自动 push HF)
  # push:                     # ← 删此段
  #   branches: [main]
  #   paths:
  #     - 'spaces/<component>/Dockerfile'
  #     - 'spaces/<component>/start.sh'
  #     - 'spaces/<component>/README.md'
  #     - 'spaces/<component>/.gitattributes'

jobs:
  sync:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Push skeleton to HF Space git (triggers Rebuild)
        env:
          HF_TOKEN: ${{ secrets.HF_TOKEN_DEV }}
        run: |
          git config --global user.email "nexus-ci@i3t2y"
          git config --global user.name  "nexus-ci"
          rm -rf /tmp/hf_space && mkdir /tmp/hf_space
          cd /tmp/hf_space
          git init
          git remote add origin \
            "https://user:${HF_TOKEN}@huggingface.co/spaces/i3t2y/nexus-<component>"
          # 白名单 cp (仅四件定态)
          for f in Dockerfile start.sh README.md .gitattributes; do
            cp "${GITHUB_WORKSPACE}/spaces/<component>/$f" ./ 2>/dev/null || true
          done
          git add Dockerfile start.sh README.md .gitattributes
          git commit -m "space(<component>): ${GITHUB_SHA::7}" --allow-empty
          git push origin HEAD:main --force
# ⚠️ 上述 git push --force 仍属血统模板; 部署可二选一:
#   (a) 改用 huggingface_hub API: hf upload 三件定态到 Space repo (同 §7.1 方式, 无 git push)
#   (b) 保留 git push 但 workflow 已限 workflow_dispatch 显令点火 (人工触发方执行 push)
```

### §7.3 日志取证 workflow（30min cron + dispatch）

```yaml
# fetch-<component>-logs.yml
name: Fetch logs (<component> dev)
on:
  schedule:
    - cron: '*/30 * * * *'    # 每 30min (HF 日志 30min 可见窗口)
  workflow_dispatch:
    inputs:
      log_type:
        description: 'run/build/both'
        default: 'run'
        type: choice
        options: [run, build, both]

jobs:
  fetch:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { ref: evidence, fetch-depth: 1 }

      - name: Fetch and redact HF Space logs
        env:
          HF_TOKEN: ${{ secrets.HF_TOKEN_DEV }}
          LOG_TYPE: ${{ inputs.log_type || 'run' }}
        run: |
          pip install -q "huggingface_hub>=1.0,<2.0" requests
          python3 - <<'EOF'
          import os, re, json, datetime, requests

          COMPONENT = "<component>"
          OWNER     = "i3t2y"
          SPACE     = f"nexus-{COMPONENT}"
          HF_TOKEN  = os.environ["HF_TOKEN"]
          LOG_TYPE  = os.environ.get("LOG_TYPE", "run")
          OUT_DIR   = f"evidence/{COMPONENT}"
          os.makedirs(OUT_DIR, exist_ok=True)

          # 脱敏正则 (fail-closed: 命中即替换, 放行 ${VAR}/$(...) 占位)
          REDACT_PATTERNS = [
              (re.compile(r'nvapi-[A-Za-z0-9_\-]{20,}'), '<REDACTED_NVAPI>'),
              (re.compile(r'(?i)bearer\s+[A-Za-z0-9_\-\.]{16,}'), 'Bearer <REDACTED>'),
              (re.compile(r'(?i)X-Internal-PSK\s*[:=]\s*\S+'), 'X-Internal-PSK: <REDACTED>'),
              (re.compile(r'(?i)(Authorization|Cookie|Set-Cookie|NIM_KEY[^=]*)\s*[:=]\s*\S+'),
               r'\1: <REDACTED>'),
              (re.compile(r'(?i)key#\d+\s+(HTTP/\S+|alive|dead|skip)'), '<KEY_STATUS_REDACTED>'),
              (re.compile(r'nim-\d{2,}\s+(OK|skip|FAIL|HTTP)'), '<NIM_ACCOUNT_REDACTED>'),
              (re.compile(r'(?i)(access[_-]?key[_-]?id|secret[_-]?access[_-]?key)\s*[:=]\s*\S+'),
               r'\1: <REDACTED>'),
          ]

          def redact(line: str) -> str:
              # 跳过占位符行
              if '${' in line or '$(' in line or '<REDACTED' in line:
                  return line
              for pat, repl in REDACT_PATTERNS:
                  line = pat.sub(repl, line)
              return line

          types = ['run','build'] if LOG_TYPE == 'both' else [LOG_TYPE]
          ts = datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')

          for lt in types:
              url = f"https://huggingface.co/api/spaces/{OWNER}/{SPACE}/logs/{lt}"
              resp = requests.get(url, headers={"Authorization": f"Bearer {HF_TOKEN}"}, timeout=30)
              if resp.status_code != 200:
                  print(f"[fetch] WARN: {lt} logs 获取失败 {resp.status_code}")
                  continue
              lines = resp.text.splitlines()
              redacted = [redact(l) for l in lines]
              out_path = f"{OUT_DIR}/{COMPONENT}_{lt}_{ts}.log"
              with open(out_path, 'w') as f:
                  f.write('\n'.join(redacted))
              print(f"[fetch] {lt}: {len(lines)} lines → {out_path}")

          # 7 天留存清理
          import glob, time
          for old in glob.glob(f"{OUT_DIR}/*.log"):
              if time.time() - os.path.getmtime(old) > 7*86400:
                  os.remove(old)
                  print(f"[fetch] 清理过期: {old}")
          EOF

      - name: Commit to evidence branch
        run: |
          git config user.email "nexus-ci@i3t2y"
          git config user.name  "nexus-ci"
          git add evidence/<component>/
          git diff --cached --quiet || \
            git commit -m "evidence(<component>): $(date -u +%Y-%m-%dT%H:%M:%SZ)"
          git push origin evidence
```

---

## §8 安全加固（Security Hardening）

### §8.1 LangGraph 安全版本锁（L6 铁律）

```txt
# spaces/langgraph/requirements.txt
# L6 铁律: 必须锁定, 防 SQLi+RCE CVE (langgraph-checkpoint-sqlite)
langgraph>=1.2.10
langgraph-checkpoint-sqlite>=3.0.1
langgraph-checkpoint-postgres>=2.0.0

# DeltaChannel 支持 (1.2.10 已包含)
# 注意: langgraph-api (生产 Server 二进制) 需企业 License
# L5 铁律: 使用 Library Mode (FastAPI 承载), 不引用 langgraph-api
```

```python
# spaces/langgraph/app.py — Library Mode 骨架 (非 langgraph-api Server)
from fastapi import FastAPI
from langgraph.graph import StateGraph
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
import os

app = FastAPI()

async def build_graph():
    """构建 LangGraph 编排图 (Library Mode, AsyncPostgresSaver)"""
    conn_str = os.environ["SUPABASE_POSTGRES_URL"]  # 从 Secrets 注入
    async with AsyncPostgresSaver.from_conn_string(conn_str) as checkpointer:
        # 创建 schema (幂等)
        await checkpointer.setup()
        # 定义业务 Graph...
        builder = StateGraph(...)
        graph = builder.compile(checkpointer=checkpointer)
        return graph

@app.get("/healthz")
async def healthz():
    return {"ok": True, "component": "langgraph"}

@app.post("/v1/runs")
async def run_graph(request: dict):
    graph = await build_graph()
    # ... 处理编排请求
```

### §8.2 Secret 纪律（四道防线）

```python
# .claude/hooks/secret-scan.py
# ══════════════════════════════════════════════════════════════════
# Nexus 集群 Secret 扫描钩子 (PreToolUse, fail-closed)
# matcher: Bash|Write|Edit
# 命中 exit(2) 拦截; ${VAR}/$(...) 占位放行
# ══════════════════════════════════════════════════════════════════
import sys, re, json

PATTERNS = [
    re.compile(r'nvapi-[A-Za-z0-9_\-]{20,}'),
    re.compile(r'(?i)X-Internal-PSK\s*[:=]\s*[A-Za-z0-9]{8,}'),
    re.compile(r'(?i)Bearer\s+[A-Za-z0-9_\-\.]{20,}'),
    re.compile(r'(?i)(access[_-]?key[_-]?id|secret[_-]?access[_-]?key)\s*[:=]\s*[A-Za-z0-9+/]{16,}'),
    re.compile(r'sk-[A-Za-z0-9]{20,}'),          # OpenAI keys
    re.compile(r'sk-ant-[A-Za-z0-9\-]{20,}'),    # Anthropic keys
]
SAFE_PATTERN = re.compile(r'\$\{[A-Z_]+\}|\$\([^)]+\)|<REDACTED')

try:
    inp = json.load(sys.stdin)
    text = json.dumps(inp)
    if not SAFE_PATTERN.search(text):
        for pat in PATTERNS:
            if pat.search(text):
                print(f"[secret-scan] BLOCKED: 检测到 secret 模式匹配 {pat.pattern[:40]}", file=sys.stderr)
                sys.exit(2)
except Exception as e:
    print(f"[secret-scan] parse error: {e}", file=sys.stderr)
    sys.exit(0)  # fail-open on parse error

sys.exit(0)
```

```json
// .gitignore 关键条目
{
  "patterns": [
    ".env*",
    "*.key",
    "secrets/",
    "*.pem",
    "**/R2_*",
    "**/*_SECRET*",
    "**/*_TOKEN*",
    "**/*.sqlite",
    "**/.litestream/"
  ]
}
```

### §8.3 Claude Code / Codex 容器认证（L7 铁律）

```sh
# spaces/claude-code/logic/init.sh
# ── Claude Code API Key 模式初始化 (L7 铁律: 禁 OAuth) ──
# 验证 API Key 存在 (不打印值)
[ -n "$ANTHROPIC_API_KEY" ] || { echo "[init:claude] FATAL: 缺 ANTHROPIC_API_KEY"; exit 1; }

# 配置 Claude Code headless 模式 (claude -p 无头执行)
mkdir -p /data/hermes/.hermes
cat > /data/hermes/.hermes/config.json <<CONF
{
  "model": "${CLAUDE_MODEL:-claude-opus-4-5}",
  "apiKeyHelper": null,
  "disableTelemetry": true,
  "headless": true
}
CONF

echo "[init:claude] Claude Code 配置就绪 (API Key 模式, headless)"
```

```sh
# spaces/codex/logic/init.sh
# ── Codex CLI API Key 模式初始化 (L7 铁律: 禁 OAuth) ──
[ -n "$OPENAI_API_KEY" ] || { echo "[init:codex] FATAL: 缺 OPENAI_API_KEY"; exit 1; }

# codex exec --api-key (headless, 无交互)
echo "[init:codex] Codex CLI 配置就绪 (API Key: ${OPENAI_API_KEY:0:7}***)"
```

---

## §9 保活机制（Hermes 主控专属）

```sh
#!/bin/sh
# keepalive_relay.sh — 内部互探保活 + 随机化间隔防固定周期识别
# 保活策略: 外部监测主 + 内部互探辅
set -e
COMPONENT="${NEXUS_COMPONENT:-hermes}"
SELF_URL="${HERMES_HEALTHZ_URL:-http://127.0.0.1:${GATE_PORT:-7860}/healthz}"
# 其他组件探点 (从 Space Variables 注入)
PEER_URLS="${NEXUS_PEER_HEALTHZ_URLS:-}"  # 空格分隔的其他组件健康端点

echo "[keepalive] 启动 component=$COMPONENT self=$SELF_URL"

while true; do
  # 随机间隔 180~540s 防固定周期 fingerprint
  _interval=$(( (RANDOM % 360) + 180 ))

  # 自探
  _resp=$(curl -s -o /dev/null -w "%{http_code}" "$SELF_URL" \
    --max-time 10 2>/dev/null) || _resp="000"
  echo "[keepalive] self=$_resp ts=$(date -u +%H:%M:%S)"

  # 互探 (若配置了 peer)
  if [ -n "$PEER_URLS" ]; then
    for url in $PEER_URLS; do
      _pr=$(curl -s -o /dev/null -w "%{http_code}" "$url" \
        --max-time 10 2>/dev/null) || _pr="000"
      echo "[keepalive] peer=$url status=$_pr"
    done
  fi

  sleep "$_interval"
done
```

---

## §10 Ephemeral Package Replay（借鉴 HuggingMes）

```sh
#!/bin/sh
# replay_packages.sh — boot 期重装历史 apt/pip 包 (48h 唤醒 ephemeral 丢后恢复)
# 参考: somratpro/HuggingMes startup.sh 重放机制
# 安装记录存 Dataset (随 CommitScheduler 归档)
COMPONENT="${NEXUS_COMPONENT:-hermes}"
REPLAY_FILE="${DATA_DIR:-/data}/installed_packages.json"

if [ ! -f "$REPLAY_FILE" ] && [ -n "$PRIVATE_SNAPSHOT_DATASET_REPO" ]; then
  echo "[replay] 从 Dataset 下载安装记录..."
  python3 -c "
import os
from huggingface_hub import hf_hub_download
try:
    path = hf_hub_download(
        repo_id=os.environ['PRIVATE_SNAPSHOT_DATASET_REPO'],
        filename='installed_packages.json',
        repo_type='dataset',
        token=os.environ.get('HF_TOKEN','')
    )
    import shutil; shutil.copy(path, '${REPLAY_FILE}')
    print('[replay] 安装记录下载成功')
except Exception as e:
    print(f'[replay] WARN: 无安装记录 ({e}), 跳过 replay')
" 2>/dev/null || true
fi

if [ -f "$REPLAY_FILE" ]; then
  echo "[replay] 重放历史安装..."
  python3 -c "
import json, subprocess, sys
with open('${REPLAY_FILE}') as f:
    pkgs = json.load(f)
for pkg in pkgs.get('pip', []):
    r = subprocess.run(['pip3','install','--quiet','--break-system-packages', pkg],
                       capture_output=True, timeout=60)
    print(f'[replay] pip {pkg}: {\"ok\" if r.returncode==0 else \"fail\"}')
for cmd in pkgs.get('apt', []):
    r = subprocess.run(['apt-get','install','-y','--no-install-recommends', cmd],
                       capture_output=True, timeout=120)
    print(f'[replay] apt {cmd}: {\"ok\" if r.returncode==0 else \"fail\"}')
" 2>/dev/null || true
fi
```

---

## §11 日志分级与公开策略

### §11.1 分级矩阵（完整版）

| 日志类 | 内容 | 敏感度 | 入私有 R2 | 入公开 Dataset | 分析价值 |
|--------|------|--------|----------|---------------|---------|
| **gate logGate JSON** | path/method/httpStatus/errorCode/elapsedMs | 低 | ✓ | ✓ (直接) | T1 中 |
| **entrypoint boot 流** | restore路径/版本/PID/健康等待 | 低 | ✓ | ✓ (直接) | T1 中 |
| **init SUMMARY** | KEYS=/RPM=/probe alive=N dead=M 聚合 | 中 | ✓ | ✓ (脱敏聚合) | T1 中 |
| **hourly/daily_usage_summary** | provider/model/tokens/cost 聚合 | 低 | ✓ | ✓ (直接) | T1 中 |
| **compression_analytics** | combo/original_tokens/saved_percentage | 低 | ✓ | ✓ (直接) | T1 中 |
| **semantic_cache 统计** | hit_count/tokens_saved/TTL | 低 | ✓ | ✓ (直接) | T1 中 |
| **call_logs 子集** | provider/model/status/tokens/latency (删account/key_id) | 中 | ✓ | ✓ (字段级脱敏) | T0 高 |
| **usage_history 子集** | provider/model/tokens/ttft (删api_key_id) | 中 | ✓ | ✓ (字段级脱敏) | T0 高 |
| **quota_snapshots** | provider/remaining_percentage/exhausted | 中 | ✓ | ✓ (conn#N脱敏) | T0 高 |
| **init 逐 key 编号** | key#N alive/dead/nim-NN HTTP | 高 | ✓ (加密) | ✗ | T0 高 |
| **provider_connections 健康** | backoff_level/rate_limited/last_error | 高 | ✓ | ✗ | T0 高 |
| **init 登录流** | auth_token/cookie/OR_API_KEY | 极高 | ✗ (ephemeral) | ✗ | N/A |

### §11.2 脱敏规则（生产级）

```python
# redact.py — 字段级脱敏引擎 (所有组件共用)
import re, hashlib

FIELD_RULES = {
    # 删除字段
    "DELETE": ["api_key_id", "api_key_name", "connection_id", "account_raw",
               "auth_token", "cookie", "set_cookie", "credentials"],
    # Hash 化字段 (SHA256 前 8 位, 保留分析相关性)
    "HASH":   ["account"],
    # 截断前缀 (保留 /24 子网用于地域分析)
    "PREFIX": ["public_ip"],           # x.x.x.* 截断最后段
    # 仅保留 hostname (去掉 path/query)
    "HOST_ONLY": ["target_url", "base_url", "endpoint"],
}

def redact_record(record: dict) -> dict:
    out = {}
    for k, v in record.items():
        if k in FIELD_RULES["DELETE"]:
            continue
        elif k in FIELD_RULES["HASH"]:
            out[k] = hashlib.sha256(str(v).encode()).hexdigest()[:8] if v else None
        elif k in FIELD_RULES["PREFIX"] and isinstance(v, str):
            parts = v.split('.')
            out[k] = '.'.join(parts[:-1]) + '.0' if len(parts) == 4 else v
        elif k in FIELD_RULES["HOST_ONLY"] and isinstance(v, str):
            from urllib.parse import urlparse
            out[k] = urlparse(v).hostname or v
        else:
            out[k] = v
    return out
```

---

## §12 GHCR base 镜像构建（nexus-base:stable）

### §12.1 base Dockerfile（推送到 GHCR，低频变）

```dockerfile
# nexus-base Dockerfile — GHCR base 镜像
# 预装全工具链: litestream + python3 + jq + sqlite3 + node + huggingface_hub
# 推 ghcr.io/i3t2y/nexus-base:stable (浮动 tag, 日常升级推新版)
FROM debian:bookworm-slim

ARG LITESTREAM_VERSION=0.5.9
ARG NODE_VERSION=22

ENV DEBIAN_FRONTEND=noninteractive

# 工具链安装
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl jq python3 python3-pip sqlite3 ca-certificates \
    build-essential git && \
    rm -rf /var/lib/apt/lists/*

# Node.js 安装
RUN curl -fsSL https://deb.nodesource.com/setup_${NODE_VERSION}.x | bash - && \
    apt-get install -y nodejs && rm -rf /var/lib/apt/lists/*

# litestream 安装
RUN _arch=$(uname -m | sed 's/aarch64/arm64/;s/x86_64/amd64/') && \
    curl -fsSL "https://github.com/benbjohnson/litestream/releases/download/v${LITESTREAM_VERSION}/litestream-${LITESTREAM_VERSION}-linux-${_arch}.tar.gz" \
    | tar -xz -C /usr/local/bin litestream && \
    chmod +x /usr/local/bin/litestream && \
    litestream version

# huggingface_hub Python 包
RUN pip3 install --no-cache-dir --break-system-packages \
    "huggingface_hub>=1.0,<2.0"

# /data 目录预建
RUN mkdir -p /data /app/data /logic

LABEL org.opencontainers.image.source="https://github.com/i3t2y/nexus"
LABEL org.opencontainers.image.description="Nexus cluster base image"
```

### §12.2 GHCR build + push workflow

```yaml
# .github/workflows/build-nexus-base.yml
name: Build and push nexus-base to GHCR
on:
  push:
    branches: [main]
    paths: ['docker/nexus-base/Dockerfile']
  workflow_dispatch:
    inputs:
      tag:
        description: 'Image tag (default: stable)'
        default: 'stable'

jobs:
  build:
    runs-on: ubuntu-latest
    permissions:
      packages: write
      contents: read
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - uses: docker/build-push-action@v5
        with:
          context: docker/nexus-base
          push: true
          tags: |
            ghcr.io/i3t2y/nexus-base:stable
            ghcr.io/i3t2y/nexus-base:${{ github.sha }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

---

## §13 Space Variables 完整清单

每组件所需 HF Space Variables（在 HF Space 设置页填写，非 git 入库）：

### §13.1 所有组件共用变量

| 变量名 | 说明 | 必填 | 示例占位 |
|--------|------|------|---------|
| `LOGIC_BUCKET_REPO` | 逻辑层 Dataset repo (沿用 omn 血统命名; 实为 dataset repo_type, a142da9 已修正 Bucket/Dataset 概念混用) | ✅ | `i3t2y/nexus-<component>-logic` |
| `HF_TOKEN` | HF 写权限 token（逻辑层拉取） | 可选 | `hf_...` |
| `R2_BUCKET` | R2 bucket 名 (现役四桶共用: nexus-backups/checkpoints/skills/artifacts; 或相邻部署每组件独立 nexus-<component>-dev/prod) | ✅ | `nexus-<component>-prod` 或 `nexus-backups` |
| `R2_ACCOUNT_ID` | Cloudflare R2 账户 ID | ✅ | `<cf_account_id>` |
| `R2_ACCESS_KEY_ID` | R2 访问 Key ID | ✅ | `<r2_key_id>` |
| `R2_SECRET_ACCESS_KEY` | R2 Secret Key | ✅ | `<r2_secret>` |
| `INTERNAL_PSK` | 网关 PSK（≥16 字符） | ✅ | （随机生成，≥32 位） |
| `NEXUS_COMPONENT` | 组件名 | ✅ | `omniroute` / `hermes` / 等 |
| `LOG_PUBLIC_DATASET_REPO` | 公开日志 Dataset | 可选 | `i3t2y/nexus-logs-public` |
| `PRIVATE_SNAPSHOT_DATASET_REPO` | 私有快照 Dataset | 可选 | `i3t2y/nexus-snapshots-private` |
| `GATE_UPSTREAM_TIMEOUT_MS` | 网关超时（ms） | 可选 | `180000`（长思考场景） |
| `LITESTREAM_STRICT` | litestream 严格模式 | 可选 | `0` |

### §13.2 组件专属变量

| 组件 | 变量 | 说明 |
|------|------|------|
| **omniroute** | `NIM_KEYS` | NIM key 池（逗号分隔） |
| **omniroute** | `GATE_CTX_MAX_BYTES` | 上下文字节限制（默认 1500000） |
| **langgraph** | `SUPABASE_URL` | Supabase REST URL |
| **langgraph** | `SUPABASE_SERVICE_KEY` | Supabase Service Role Key |
| **langgraph** | `SUPABASE_POSTGRES_URL` | Postgres 连接串（AsyncPostgresSaver） |
| **hermes** | `HERMES_HEALTHZ_URL` | Hermes 自探健康端点 |
| **hermes** | `NEXUS_PEER_HEALTHZ_URLS` | 集群其他组件健康端点列表 |
| **claude-code** | `ANTHROPIC_API_KEY` | Anthropic API Key（L7 铁律，非 OAuth） |
| **claude-code** | `CLAUDE_MODEL` | 模型名（默认 claude-opus-4-5） |
| **codex** | `OPENAI_API_KEY` | OpenAI API Key（L7 铁律，非 OAuth） |
| **codex** | `CODEX_MODEL` | 模型名（默认 o4-mini） |

---

## §14 新组件落地清单（8 步）

1. **建 GHCR base 镜像**（若 `nexus-base:stable` 已存在可复用）：
   - `docker/nexus-base/Dockerfile` 推 GHCR → `nexus-base:stable`

2. **组件 Dockerfile 三件定态**：
   - 抄 §3.1 骨架，设 `ARG NEXUS_COMPONENT=<component>`

3. **逻辑层六件**（放 `spaces/<component>/logic/`）：
   - `entrypoint.sh`（抄 §4.1 骨架 + §4.2 对应业务段）
   - `gate.js`（抄 §6 骨架）
   - `litestream.yml`（抄 §5.1 骨架，替换组件名）
   - `init.sh`（组件专属初始化，幂等）
   - `commit_scheduler.sh`（抄 §5.3）
   - `package.json`（组件依赖声明）

4. **R2 配置**：在 Cloudflare 创建独立 bucket `nexus-<component>-dev/prod`（永不双写）——或复用现役四桶 `nexus-backups/checkpoints/skills/artifacts`（共用以省额度, 见 §1 注对齐 a142da9 现状）。**Bucket 唯一支持 RW（修正项①）, 运行态可写件此去; Dataset 逻辑层永远只读**。

5. **Supabase 配置**（仅 langgraph）：执行 `sql/00_schema.sql` 建 schema

6. **workflow 六件制**（抄 §7，替换 `<component>` 名）：
   - `sync-logic-<component>.yml`
   - `sync-space-<component>.yml` —— **部署前删 `on: push` 改仅 `workflow_dispatch` 显令点火（红线: 我永不自动 push HF, 见 §7.2 红线注）**
   - `fetch-<component>-logs.yml`（及对应 prod 版本）

7. **secret 护栏**：
   - `.gitignore` 更新（§8.2）
   - `secret-scan.py` 钩子（§8.2）
   - HF Space Variables 填写（§13）

8. **dev 六绿验收后 dispatch prod**：
   - boot 正常 / init 完成 / 健康端点 200 / 长思考 180s / 限流 413 / 过夜不崩

---

## §15 运维速查

### §15.1 升级路径

| 场景 | 操作 | 触 Rebuild? |
|------|------|------------|
| 日常业务逻辑更新 | push `spaces/<component>/logic/**` → sync-logic workflow 自触 | 否 |
| 升级 GHCR base 镜像 | GHCR 侧推新版到 `:stable` tag | 自动拉（HF 侧 Rebuild） |
| 钉 base 镜像 digest | 改 Dockerfile `ARG BASE_IMAGE` 默认值 → sync-space | 是（受控） |
| 升级 litestream 版本 | 改 `ARG LITESTREAM_VERSION` 默认值 或 HF Variable buildtime 覆盖 | 是 / 否 |
| 升级 Python 包版本 | 改 `ARG HF_HUB_RANGE` 上限区间 | 是（受控） |

### §15.2 回滚路径

> **修正项②（storage-buckets 铁义对比）**: HF 官方 storage-buckets 对比表 — Dataset Repos: "Full Git history"; Storage Bucket: "**None** mutable, overwrite-in-place"。即 **Dataset 改逻辑 = 1 commit = 1 不可变回退锚**（datasets `revision` 接 commit SHA / git tag, 可 `hf download --revision <sha>` 锁回退点）；**Bucket 改逻辑 = sync 覆盖, 无回退锚**（覆盖写, 旧版不留）。故逻辑层放 Dataset 根平铺的核心价值之一即版本化回滚; 运行态 RW 件走 Bucket 无版本化回滚须靠 litestream snapshot / R2 对象版本化兜底。

| 场景 | 操作 |
|------|------|
| 逻辑层回滚 | Dataset `git revert` + push → sync-logic 触同步; 或 start.sh `hf download --revision <旧sha>` 锁历史快照 (修正项② 回退锚) |
| base 镜像回滚 | GHCR 重推旧 digest 到 `:stable` |
| 数据回滚（SQLite 组件） | litestream restore 指定旧 snapshot 时间戳 |
| 数据回滚（langgraph Postgres） | Supabase PITR 恢复到指定时间点 (Pro+); 或 R2 db dump 还原 (免费档, 修正项⑧) |
| Bucket 逻辑层覆盖误改 | Bucket 无 git 回退锚, 须重跑 sync 推正确版本 (修正项②: Bucket = overwrite-in-place) |
| 晋级失败 prod 回滚 | prod Space 切回旧 R2 bucket 变量 + Restart |

### §15.3 监控信号

```sh
# boot 验收九段（从 HF Logs 页面或 fetch-logs 抓取验证）
grep -E "\[start\] 启动|基础镜像|逻辑层就绪|restore 成功|业务服务就绪|init 后台运行|litestream replicate|CommitScheduler|gate.*listening" <logfile>

# 健康探点
curl -s https://<owner>-nexus-<component>.hf.space/healthz

# litestream 配额监控（R2 Class A 月量估算）
# 10s sync * 86400s/day * 30day = ~259200 PUT/month < 100万免费额 ✓
```

### §15.4 常见故障处理

| 故障现象 | 诊断 | 处置 |
|---------|------|------|
| boot FATAL: 缺 LOGIC_BUCKET_REPO | HF Space Variable 未填 | 填写 `LOGIC_BUCKET_REPO` |
| litestream restore 失败（R2 首次） | 正常（空库启动） | 忽略，新库会自动创建并同步 |
| gate FATAL: INTERNAL_PSK <16 | PSK 未填或太短 | 重新生成 ≥32 位随机串填入 |
| LangGraph 连接 Supabase 失败 | SUPABASE_POSTGRES_URL 错误 | 核查 Supabase 连接串格式 |
| Claude Code 启动卡住 | 触发 OAuth 浏览器跳转（L7 违反） | 确认 ANTHROPIC_API_KEY 已设，禁用 OAuth |
| HF build 冻结 | 密集推送触风控 | 停推 24h，改用逻辑层路径（零 Rebuild） |
| R2 Class A 超限意外账单 | sync-interval < 10s | 立即改 litestream.yml sync-interval ≥ 10s |

---

## §16 已知局限与边界

| 限制 | 真相 | 对策 |
|------|------|------|
| HF 免费层不适用 | 2026-07 Docker Space 需 PRO 层 | PRO 账号（$9/月），祖父条款保现有 Space |
| ephemeral /data 48h 丢 | 非 SQLite 件（.init-done/log）重启丢 | 关键状态走 env，日志走 Dataset 归档 |
| **litestream 仅懂 SQLite WAL** | **Postgres 物理 WAL litestream 读不了（修正项⑧ 误配）** | SQLite 件→litestream; Postgres→Supabase PITR/db dump 入 R2 |
| litestream 仅复制 SQLite | 非 DB 文件不在 R2 备份范围 | init.sh 幂等，重启重跑无副作用 |
| HF 日志 30min 窗口 | 超窗日志不可追溯 | fetch-logs workflow 每 30min cron 抓 |
| Dataset CommitScheduler 非实时 | 5min 快照 RPO = 5min | litestream 10s 为主灾备，Dataset 为跨平台二线 |
| Dataset 无 git 回退锚? | **有**（修正项②）: Dataset 1 commit=1 回退锚, Bucket 仅 overwrite 无锚 | 逻辑层放 Dataset 即取版本化回滚; 运行态写件去 Bucket 无版本化靠 litestream 兜底 |
| gate CTX guard 不拦 chunked | 无 content-length 放行 | 由堆 exhaustion 兜底 |
| LangGraph Library Mode 性能 | 无 Server 二进制优化层 | FastAPI + uvicorn 足够 HF Space 场景 |
| Public Dataset Best-effort | 非真无限，软限几 GB | 日志量 MB/日，实务安全 |
| **Dataset 永远只读 mount（修正项①）** | manage-spaces 原文: Models/datasets/Spaces always RO; only buckets RW | 运行态可写件去 Storage Bucket (唯一 RW), 逻辑层热更靠 git push + manual restart_space |
| **本轮 B 路径三分歧悬决（勘探非实施）** | (a) 挂载形态: 只读 Volume vs hf download+cp 可写副本 (b) 失败模式: fail-open WARN vs 硬 exit1 (c) workflow: 六件制 vs 单件 | 待论证落地, 见 MEMORY nexus-omn-merge-port-plan.md |
| **omn-merge fail-open vs 本模板 STRICT** | 修正项⑤: omn-merge 故意 fail-open (entrypoint.sh:202-212); 真硬 exit1 仅 gate.js PSK+npm install | 本模板监督循环 STRICT 属血统叙述, 部署按业务裁决 |
| **Nexus 现役 a142da9 实装** | GHCR base+Bucket rw 单挂+Dockerfile 墓碑 (非本文 Dataset 逻辑层路线) | 见 spaces/hermes/*; 本模板保留 omn 血统模板作新节点照搬蓝本 |

---

## 附录 A：仓库文件拓扑速查

```
nexus/
├── .github/workflows/
│   ├── build-nexus-base.yml              # GHCR base 镜像构建
│   ├── sync-logic-hermes.yml             # 逻辑层同步 (hermes dev, 自触, 零Rebuild)
│   ├── sync-space-hermes.yml             # 骨架同步 (hermes dev, 触Rebuild)
│   ├── fetch-hermes-logs.yml             # 日志取证 (hermes dev, 30min cron)
│   ├── sync-logic-langgraph.yml          # ... 其他四组件各三件, 共 15 yml
│   ├── sync-space-langgraph.yml
│   ├── fetch-langgraph-logs.yml
│   ├── sync-logic-omniroute.yml
│   ├── sync-space-omniroute.yml
│   ├── fetch-omniroute-logs.yml
│   ├── sync-logic-claude.yml
│   ├── sync-space-claude.yml
│   ├── fetch-claude-logs.yml
│   ├── sync-logic-codex.yml
│   ├── sync-space-codex.yml
│   └── fetch-codex-logs.yml
│   # prod 版本各组件另 15 yml = 共 30 + 1 (base build) = 31 workflow 文件
│
├── docker/
│   └── nexus-base/
│       └── Dockerfile                    # GHCR base 镜像 (§12.1)
│
├── spaces/
│   ├── hermes/
│   │   ├── Dockerfile                    # 三件定态 (§3.1, NEXUS_COMPONENT=hermes)
│   │   ├── start.sh                      # 三件定态 (§3.2, 通用)
│   │   ├── README.md                     # 三件定态 (§3.3, 纯 frontmatter)
│   │   ├── .gitattributes
│   │   └── logic/
│   │       ├── entrypoint.sh             # daemon 编排 (§4.1 + §4.2 hermes段)
│   │       ├── gate.js                   # 网关 (§6)
│   │       ├── litestream.yml            # R2 配置 (§5.1)
│   │       ├── init.sh                   # Hermes 初始化
│   │       ├── commit_scheduler.sh       # Dataset 二线 (§5.3)
│   │       ├── keepalive_relay.sh        # 保活 (§9)
│   │       ├── replay_packages.sh        # Ephemeral 重放 (§10)
│   │       └── package.json
│   ├── langgraph/
│   │   ├── Dockerfile                    # NEXUS_COMPONENT=langgraph
│   │   ├── start.sh                      # 通用
│   │   ├── README.md
│   │   └── logic/
│   │       ├── entrypoint.sh             # §4.2 langgraph 段
│   │       ├── gate.js
│   │       ├── litestream.yml            # 仅 metadata (§5.2)
│   │       ├── init.sh                   # Supabase schema setup
│   │       ├── commit_scheduler.sh
│   │       ├── app.py                    # Library Mode FastAPI (§8.1)
│   │       └── requirements.txt          # 锁定版本 (§8.1, L6 铁律)
│   ├── omniroute/
│   │   ├── Dockerfile                    # NEXUS_COMPONENT=omniroute
│   │   ├── start.sh
│   │   ├── README.md
│   │   └── logic/
│   │       ├── entrypoint.sh             # §4.2 omniroute 段
│   │       ├── gate.js
│   │       ├── litestream.yml
│   │       ├── init.sh                   # NIM key 池初始化
│   │       ├── commit_scheduler.sh
│   │       └── package.json
│   ├── claude-code/
│   │   └── logic/
│   │       ├── entrypoint.sh             # §4.2 claude 段
│   │       ├── gate.js
│   │       ├── litestream.yml
│   │       ├── init.sh                   # API Key 模式初始化 (§8.3)
│   │       └── claude_server.js          # headless 封装
│   └── codex/
│       └── logic/
│           ├── entrypoint.sh             # §4.2 codex 段
│           ├── gate.js
│           ├── litestream.yml
│           ├── init.sh                   # API Key 模式 (§8.3)
│           └── codex_server.js           # headless 封装
│
├── workers/
│   └── gateway/
│       └── index.ts                      # Cloudflare Worker 统一入口
│
├── sql/
│   ├── 00_schema.sql                     # Supabase schema
│   └── 01_pgvector.sql                   # pgvector 扩展 (可选)
│
├── libs/
│   ├── storage/                          # 跨 Space 共享: R2+Supabase 客户端
│   └── shared/                           # gate.js / redact.py 共享逻辑
│
├── scripts/
│   └── sync-spaces.sh                    # libs/ 复制进各 Space 目录
│
├── .claude/
│   └── hooks/
│       └── secret-scan.py                # Secret 扫描钩子 (§8.2)
│
├── .gitignore                            # §8.2 规则
├── .env.example                          # 占位示范 (零真值)
├── DECISIONS.md                          # 只增不改决策账
├── HANDOFF.md                            # 交接 + 架构契约 SSOT
├── CLAUDE.md                             # 工作宪法 (护栏 + 拓扑铁律)
└── README.md                             # 项目概览
```

---

## 附录 B：铁律速查卡

> **去冗注**: 八铁律权威定义见 **§1.2 铁律汇总表**（含违反后果列）。本附录原为重复速查卡, 去冗合并指向 §1.2, 不再全量重列。附补充两条上轮未单列的修正项铁义:

```
┌─────────────────────────────────────────────────────────────────┐
│  补充铁义 (上承 §1.2 八铁律, 不重列 L1-L8)                         │
├────┬────────────────────────────────────┬───────────────────────┤
│ ①A│ Dataset 永远只读 mount            │ 运行态 RW 件去 Bucket  │
│ ②A│ Dataset 改逻辑=1 commit=1 回退锚  │ Bucket 无锚靠 litestream│
│ ⑧A│ litestream 仅 SQLite WAL 不懂 PG   │ Postgres 走 Supabase PITR│
└────┴────────────────────────────────────┴───────────────────────┘

权威源: manage-spaces Note (取消①原文):
  "Models,datasets,Spaces always mounted as read-only.
   Only storage buckets support read-write mounts."
storage-buckets 对比表(取消②):
  Dataset Repos = Full Git history (有版本回退)
  Storage Bucket = None mutable, overwrite-in-place (无回退锚)
```

---

> **本模板提炼自**: omn-merge 血统 `a5d92a6` + nexus `a142da9`/`4fc098e` + langgraph v1.2.10 + OmniRoute v3.8.49 + HuggingMes/HermesFace 参考实现。
> **注**: hermes-agent (`ad6df5e`, NousResearch) 是 Nous 另一 hermes, **非本 Nexus**, 已从血统源弃用此误引。
> **Nexus 现役锚**: a142da9 (GHCR base + Bucket rw 单挂逻辑层 + Dockerfile 墓碑) / fe275ae (ARG 作用域修正) / 4fc098e (bootstrap 改 hf buckets sync 拉真 Bucket)。
>
> **维护原则**: 本文随 Nexus 现役血统演进同步；重大架构变更须更新 §1 拓扑图 + §16 已知局限；铁律变更须同步 DECISIONS.md。
> **现状对齐**: 本文保留 omn 血统模板作新节点照搬蓝本; 与 Nexus 现役 a142da9 Bucket 单挂+GHCR base+Dockerfile 墓碑实际相违处, 文内各段已加 "本段为 omn 血统模板叙述, Nexus 现役已落 [实际], 见 spaces/hermes/*" 对齐注。
>
> **提炼日**: 2026-07-28 | **格言**: 一次设计，长期跑不崩。
