> **⚠️ DEPRECATED（2026-07-29 合并去冗）**
>
> 本文为 omn 血统提炼的通用架构模板，与 `Nexus集群永续架构最强模板.md`（本组架构 SSOT 权威件，1585 行最全）内容高度重叠。本文保留备查，**新读优先看 `Nexus集群永续架构最强模板.md`**——后者已渗 2026-07-29 八大修正项纠错并对齐 Nexus 现役 commit a142da9（Bucket rw /data 单挂 + GHCR base 镜像 + Dockerfile 墓碑）实际落地。
>
> **本文与权威件存差异须注意**：
> - §4 执行用 `hf download + cp` 可写副本（非只读 Volume 挂载），为 omn 现役路径，非 Nexus 已落路径——Nexus 现役 a142da9 走 `hf buckets sync` 拉真 Bucket（commit 4fc098e），见 `spaces/hermes/start.sh`。
> - §1 "逻辑层 = HF Dataset" 暗示只读挂载语义，但管理空间管理记录的官方铁义是“模型、数据集、空间始终以只读模式挂载。仅存储桶支持读写挂载”——Bucket 才是唯一支持读写的挂载点；逻辑层是否走 Dataset 只读挂载，在 Nexus 仍属悬而未决（见 memory `nexus-omn-merge-port-plan` 分歧项⑤）。
> - §6 litestream 作"运行态主路径"叙述对 Nexus（Supabase Postgres）为技术误配——litestream 仅复 SQLite WAL，详见 `Nexus集群永续架构最强模板.md` §6 边界警示。

# HF Space 永续节点架构模板

> 从 omn (OmniRoute 永续节点) 现役血统提炼的通用架构模板,供其他在 HuggingFace Space 免费层上
> 部署的长跑服务共用。本文档 = **架构原则叙述 + 内嵌可复制骨架件 + omn 现役实证 file:line 引用**,
> 三合一自包含。任何新项目照搬骨架件 + 按自身业务改逻辑层即可落地。
>
> 提炼日 2026-07-28,源血统 commit `a5d92a6` (omn-merge main)。实证口径: 所有 `file:line` 引用
> 均经只读 agent 真码核对,非记忆臆测。omn 业务耦合处(模型路由/NIM key 池)标注 § **业务示例**,
> 非架构本质,新项目按自身业务替换。
>
> 护栏纪律: 本文 secret/token 值零入文,一律 env 占位名;测试用合成串;commit/push 须人工裁决。

---

## §0 为什么需要这套架构

HF 免费 Docker Space 有四条硬约束(2026-07 实测),裸跑任意服务都会撞:

| 约束 | 后果 | 本架构对策 |
|------|------|-----------|
| **48h 休眠自醒,冷启 ephemeral 盘丢** | `/data` 持久卷只是卷非真持久,重启即可能丢运行态 | 运行态经 litestream → R2 持久化,boot 期 restore |
| **7/16 后密集推送冻 build 权限** | 频繁改 Dockerfile 触 Rebuild 易撞冻 + 风控 | 三层解耦 + 版本驱逐 ARG,日常升级零 Rebuild |
| **30min 日志可见窗口** | boot 后未及时抓日志,证据永久丢 | cron 每 30min fetch-logs 落 evidence 分支 + 脱敏 |
| **无 shell 长驻进程管理** | 多子进程死无感知,SPACE 静默崩 | entrypoint daemon 模式 + 监督循环 STRICT/WARN |

**核心目标**:一次设计,长期跑不崩、不被风控、证据不丢、升级零 Rebuild。

---

## §1 三层解耦(架构地基)

把任何 HF Space 服务拆成三层,各层载体独立、变更频率不同、升级路径分离:

```
┌─────────────────────────────────────────────────────────┐
│ 环境层 (GHCR base 镜像)  — 低频变, 触 Rebuild              │
│   ARG BASE_IMAGE → 预构建镜像 (litestream 二进制 + 工具链预装) │
│   Dockerfile + start.sh + README.md (.gitattributes)      │
├─────────────────────────────────────────────────────────┤
│ 逻辑层 (HF Dataset)      — 高频变, 零 Rebuild              │
│   业务件全部: entrypoint.sh / gate.js / init / litestream.yml│
│   改动 push Dataset → Space Restart 即效, 不触 build       │
├─────────────────────────────────────────────────────────┤
│ 运行态持久件层 (R2 via litestream + /data ephemeral)       │
│   storage.sqlite → R2 replicate (跨云容灾真持久)           │
│   /data 卷 = 允许丢, R2 是数据主路径                         │
└─────────────────────────────────────────────────────────┘
```

**关键铁律**:
- 环境层变 = 触 HF Rebuild (高频触冻/风控);逻辑层变 = 不触 Rebuild (低风险)。
- **R2 bucket 永不双写**: dev/prod 两 Space 不得同时在线写同一 R2 bucket (切换时先停旧再起新)。
- 逻辑层 Dataset 根目录平铺,**必须存在 `entrypoint.sh`** = start 与逻辑层唯一契约。

### omn 实证
- 环境层: `Dockerfile:26` `ARG BASE_IMAGE=ghcr.io/i3t2y/omniroute-base:stable`; `Dockerfile:27` `FROM ${BASE_IMAGE}`
- 逻辑层: `sync-logic-nonoke.yml:40-41` 推 `dev/logic/` 五件到 Dataset `nonoke/omn-logic` 根平铺
- 运行态: `dev/logic/litestream.yml:2` `path: /app/data/storage.sqlite` → `litestream.yml:5-9` R2 replica
- 三层铁律源: `CLAUDE.md:13-20`(§1 拓扑铁律)+ `HANDOFF.md:8-14`

---

## §2 相邻部署(dev/prod 双 Space)

两 Space 隔离 + 命名空间隔离 token,爆炸半径各半。**全程不新建任何 Space**(HF 免费层已关闭新建通道,祖父条款保两 Space)。

| 项 | dev (金丝雀) | prod (生产) |
|----|-------------|------------|
| Space | `nonoke/omn` | `nomke/omn` |
| 逻辑层 Dataset | `nonoke/omn-logic` | `nomke/omn-logic` |
| token | `HF_TOKEN_NONOKE` (仅写 dev 范围) | `HF_TOKEN_NOMKE` (仅写 prod 范围) |
| 升级路径 | logic/space 改动 `push` 自动触 | 仅 `workflow_dispatch` 显令点火 |

**晋级生产 = 变量切换 + Restart, 零 Rebuild 零净室首跑**: dev 六绿(boot/init/健康/长思考/限流基线/过夜)即"已验证现役架构",prod 仅切 R2 bucket 变量 + Restart,不重建。

### omn 实证
- dev 自触: `sync-space-nonoke.yml:22-30` push paths 四件 + `sync-logic-nonoke.yml:15-20` push paths `dev/logic/**`
- prod 显令: `sync-space-nomke.yml:18-19` 仅 `workflow_dispatch` + `sync-logic-nomke.yml:18-19` 同
- token 隔离: `DECISIONS.md` "HF_TOKEN 命名空间隔离" 条

---

## §3 三件永不再改定态(版本驱逐)

**docker base 层三件**: `Dockerfile` + `start.sh` + `README.md`(.gitattributes 配套)。版本号驱逐出三件 = 升级零改件零 Rebuild。

**双轨驱逐结构** (每个版本项 ARG 默认值 + ENV 转存两行):
```dockerfile
# ARG = build 期值 / HF Variable buildtime 覆盖 / 默认值兜底 三层优先级
ARG BASE_IMAGE=ghcr.io/<owner>/<repo>-base:stable     # 镜像驱逐
FROM ${BASE_IMAGE}
USER root
ARG BASE_IMAGE                                         # FROM 后重声明继承全局值
ENV BASE_IMAGE=${BASE_IMAGE}                           # 转存 runtime env

ARG LITESTREAM_VERSION=0.5.9                           # 二进制驱逐
ENV LITESTREAM_VERSION=${LITESTREAM_VERSION}

ARG HF_HUB_RANGE=">=1.0,<2.0"                          # Python 包区间驱逐 (双引号包防注入)
ENV HF_HUB_RANGE=${HF_HUB_RANGE}
```

**start.sh 读 ENV 兜底** (无裸硬版本号, ENV 不注入也能跑):
```sh
echo "[start] 基础镜像: ${BASE_IMAGE:-(未注入 ENV)}"
pip3 install --no-cache-dir --break-system-packages "huggingface_hub${HF_HUB_RANGE:->=1.0,<2.0}"
_ls_v="${LITESTREAM_VERSION:-0.5.9}"
curl -fsSL "https://github.com/benbjohnson/litestream/releases/download/v${_ls_v}/litestream-${_ls_v}-linux-$(uname -m|sed 's/aarch64/arm64/).tar.gz" | tar -xz -C /usr/local/bin litestream
```

**README.md = 纯 HF frontmatter, 不入版本**:
```yaml
---
title: <项目名>
sdk: docker
app_port: 7860
pinned: false
license: mit
---
```

**升级两路径**:
- A 日常(推荐): GHCR 侧推新版镜像到 `:stable` 浮动标签 → Space Rebuild 自动拉新版,ARG 不动(零 git 变更)
- B 钉 digest(备选): 改 ARG 默认值钉 `<tag>@sha256:<digest>` → commit + push → sync-space 触 Rebuild

### omn 实证
- 三 ARG+ENV 对: `Dockerfile:26/39/40`(BASE_IMAGE) / `Dockerfile:49/50`(LITESTREAM) / `Dockerfile:57/58`(HF_HUB_RANGE)
- start.sh 三读: `start.sh:10` / `start.sh:32` / `start.sh:42`(全 `${ENV:-回退}`)
- README 纯 frontmatter: `README.md` 10 行 `sdk: docker / app_port: 7860` 无版本字段
- 闭环源: `DECISIONS.md` 三件永不再改三条

---

## §4 start.sh 自适应引导(环境层入口)

`ENTRYPOINT`, 110 行内三段:

```sh
#!/bin/sh
# 与逻辑层唯一契约: Dataset 根必须存 entrypoint.sh
set -e
echo "[start] 启动 $(date '+%F %T')"
echo "[start] 基础镜像: ${BASE_IMAGE:-(未注入)}"

# ── 1. 环境自愈 (永久机制: 上游 runner 镜像刻意不装工具链) ──
_need_install=0
for t in python3 curl pip3; do command -v "$t" >/dev/null 2>&1 || _need_install=1; done
command -v litestream >/dev/null 2>&1 || _need_install=1
{ command -v hf >/dev/null 2>&1 || command -v huggingface-cli >/dev/null 2>&1; } || _need_install=1
if [ "$_need_install" = "1" ]; then
  command -v apt-get >/dev/null 2>&1 || { echo "[start] FATAL: 非 Debian 系"; exit 1; }
  apt-get update && apt-get install -y --no-install-recommends curl jq python3 python3-pip sqlite3 ca-certificates && rm -rf /var/lib/apt/lists/*
  pip3 install --no-cache-dir --break-system-packages "huggingface_hub${HF_HUB_RANGE:->=1.0,<2.0}"
  # litestream curl 拉取分支 (见 §3)
fi

# ── 2. 变量校验 (HF_TOKEN 可选: 公共 Dataset 无需令牌) ──
[ -n "$LOGIC_BUCKET_REPO" ] || { echo "[start] FATAL: 缺 LOGIC_BUCKET_REPO"; exit 1; }

# ── 3. 拉取逻辑层 (竞速根治: 先锁 Dataset HEAD commit_id 再按 revision 拉) ──
mkdir -p /tmp/logic
_rev=$(LOGIC_BUCKET_REPO="$LOGIC_BUCKET_REPO" python3 -c '
import os
try:
    from huggingface_hub import HfApi
    print(next(iter(HfApi().list_repo_commits(os.environ["LOGIC_BUCKET_REPO"], repo_type="dataset"))).commit_id)
except: pass' 2>/dev/null) || true   # fail-open 回退 main HEAD
_tk=""; [ -n "$HF_TOKEN" ] && _tk="--token $HF_TOKEN"
_rev_arg=""; [ -n "$_rev" ] && _rev_arg="--revision $_rev"
hf download "$LOGIC_BUCKET_REPO" --repo-type dataset --local-dir /tmp/logic $_tk $_rev_arg --quiet || { echo FATAL; exit 1; }
mkdir -p /logic && cp -a /tmp/logic/. /logic/ && chmod +x /logic/*.sh 2>/dev/null || true
rm -rf /tmp/logic
exec /logic/entrypoint.sh
```

**自愈分镜像 A/B**: A=BASE_IMAGE 直指裸上游无 litestream → 触 curl 拉取分支;B=GHCR base 预装全工具 → 跳过。

**竞速根治**: boot 期取 Dataset HEAD commit_id 锁修订再拉 = atomic 同点全件,根治 sync-logic push 完成 vs boot 拉取竞速导致拉旧 HEAD。

### omn 实证
- 环境自愈: `start.sh:12-49`(段 1)
- 变量校验: `start.sh:51-53`(LOGIC_BUCKET_REPO 必填)
- 竞速根治: `start.sh:64-80`(取 HEAD)+ `start.sh:82-96`(_dl 函数 stderr 脱敏回放)
- 移交: `start.sh:110` `exec /logic/entrypoint.sh`

---

## §5 entrypoint.sh daemon 编排(逻辑层 PID 1)

非 `exec` 接管,entrypoint 持 PID 1 主监控循环,多子进程全 `&`+`$!` 捕获 PID:

```sh
#!/bin/sh
set -eo pipefail
OR_PID=""; INIT_PID=""; LS_PID=""; GATE_PID=""     # 子进程 PID 声明

# ── trap 优雅停 ──
_forward_signal() {
  for pid in "$OR_PID" "$INIT_PID" "$LS_PID" "$GATE_PID"; do
    [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && kill -"$1" "$pid" 2>/dev/null || :
  done
}
_shutdown() {
  [ "$_cleanup_done" = 1 ] && return; _cleanup_done=1
  _forward_signal TERM
  g=0; while [ "$g" -lt 50 ]; do
    all_dead=1
    for pid in "$OR_PID" "$INIT_PID" "$LS_PID" "$GATE_PID"; do
      [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && all_dead=0
    done
    [ "$all_dead" = 1 ] && break
    sleep 0.1; g=$((g+1))   # grace ~5s
  done
  _forward_signal KILL
  for pid in "$OR_PID" "$INIT_PID" "$LS_PID" "$GATE_PID"; do
    [ -n "$pid" ] && wait "$pid" 2>/dev/null || :
  done
  echo "[entrypoint] shutdown complete"
}
trap '_shutdown' TERM
trap '_shutdown' INT

# ── 1. litestream restore (R2 → 本地 DB, 持久化根) ──
if [ -n "$R2_BUCKET" ] && command -v litestream >/dev/null 2>&1; then
  DB_TMP=$(mktemp)
  litestream restore -config /logic/litestream.yml -o "$DB_TMP" 2>/tmp/ls_restore.err || rc=$?
  # quick_check 验证后原子 mv 到 $DB_PATH (非空 guard 防覆盖有效 DB)
fi

# ── 2. 启动上游业务服务 ──
cd /app && NODE_OPTIONS=--max-old-space-size=4096 node server.js &
OR_PID=$!

# ── 3. 健康等待 180s (探业务健康端点) ──
# ── 4. 业务初始化 (后台, init 幂等) ──
[ -n "$NIM_KEYS" ] && bash /logic/init-nim-keys.sh & INIT_PID=$!
# ── 5. litestream 后台复制 (DB → R2) ──
[ -n "$R2_BUCKET" ] && litestream replicate -config /logic/litestream.yml & LS_PID=$!
# ── 6. 启动网关 (业务对外暴露层) ──
node /logic/gate.js & GATE_PID=$!

# ── 7. 监督循环 (PID 1 主循环) ──
_init_logged=0
while true; do
  kill -0 "$GATE_PID" 2>/dev/null || { echo "gate exited"; _shutdown; exit 1; }   # STRICT
  kill -0 "$OR_PID"   2>/dev/null || { echo "上游 exited"; _shutdown; exit 1; }   # STRICT
  if [ -n "$INIT_PID" ] && ! kill -0 "$INIT_PID" 2>/dev/null; then
    # WARN only: init 死只记一次不 exit (init 幂等可重跑)
    [ "$_init_logged" = 1 ] || { wait "$INIT_PID" 2>/dev/null; _init_logged=1; }
  fi
  if [ -n "$LS_PID" ] && ! kill -0 "$LS_PID" 2>/dev/null; then
    [ "${LITESTREAM_STRICT:-0}" = 1 ] && { echo "LS strict"; _shutdown; exit 1; }
    echo "WARN: litestream exited, DB 不再备份"; LS_PID=""   # 默认 WARN
  fi
  sleep 1
done
```

**STRICT vs WARN 策略**:
- 对外服务死(gate/上游)→ STRICT exit(停全部)
- 内部进程死(init/litestream 非 strict)→ WARN only(记一次,主链不崩)

### omn 实证
- 四 PID 声明: `entrypoint.sh:37`; 四 `&+$!` 捕获: `entrypoint.sh:187/216/225/260`
- trap: `entrypoint.sh:76-77`; _shutdown grace 5s: `entrypoint.sh:52-75`
- 监督循环 STRICT/WARN: `entrypoint.sh:267-286`

---

## §6 litestream R2 持久化(运行态数据主路径)

`/data` 卷允许丢,**R2 是数据主路径**。boot 期 restore,运行期 replicate。

```yaml
dbs:
  - path: /app/data/storage.sqlite          # 本地 DB (= /data 软链后)
    replica:
      type: s3
      bucket: ${R2_BUCKET}                   # env 注入, 非硬编
      path: db/storage.sqlite                # R2 内对象路径
      endpoint: https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com
      access-key-id: ${R2_ACCESS_KEY_ID}
      secret-access-key: ${R2_SECRET_ACCESS_KEY}
      region: auto
      sync-interval: 10s
      auto-recover: false                    # 严交 entrypoint 显式 restore, 不绕 guard

snapshot:
  interval: 1h
  retention: 24h
```

**Class A 配额减量**: `l0-retention-check-interval: 5m` 砍 LIST 20x, 月量 27万→10万(免费线 27%→10%)。

**ephemeral 真痛点**(litestream 复制范围**仅 storage.sqlite 一件**):
- `.or-api-key` / `.init-done` / init 日志等**不在复制范围** = 重启即丢
- 解法: 关键 key 走 env 而非落盘;日志走 fetch-logs cron 落 evidence 分支

### omn 实证
- litestream.yml: `dev/logic/litestream.yml:1-29`(R2 配置 + auto-recover false)
- restore guard: `entrypoint.sh:88-178`(三回退 + quick_check 原子 mv)
- R2 是主路径: `entrypoint.sh:39` 注释 "DATA=$DATA_DIR (ephemeral, R2 是数据主路径)"
- Class A 减量: `dev/logic/litestream.yml:25-29`

---

## §7 网关层契约(对外暴露)

业务对外暴露一律经网关,禁裸连上游。网关职责: 认证 + 限流 + 透传 + 日志。

### 认证 (safeEqual 常量时间比)

```js
const INTERNAL_PSK = process.env.INTERNAL_PSK || '';
// 启动 fail-closed: PSK 缺/<16 即 FATAL exit
if (!INTERNAL_PSK || INTERNAL_PSK.length < 16) {
  console.error('[gate] FATAL: INTERNAL_PSK missing or <16');
  process.exit(1);
}
function safeEqual(a, b) {
  if (!a || !b) return false;
  const ba = Buffer.from(a), bb = Buffer.from(b);
  if (ba.length !== bb.length) return false;       // 长度不等先 false 不泄内容
  return crypto.timingSafeEqual(ba, bb);           // 等长走常量时间比
}
app.use('/v1', (req, res, next) => {
  const auth = req.headers.authorization || '';
  if (!auth.startsWith('Bearer ')) return res.status(401).json({ error: 'unauthorized' });
  if (!safeEqual(auth.slice(7).trim(), INTERNAL_PSK)) return res.status(401).json({ error: 'unauthorized' });
  req.headers.authorization = `Bearer ${OR_API_KEY}`;   // 转换为后端凭据上行
  next();
});
```

### 后台开关 (纯布尔, 非 token)

```js
const ADMIN_ENABLED = process.env.GATE_ADMIN_ENABLED === '1';  // 仅 '1' 开, 其余关 (fail-closed)
// 非 /healthz 非 /v1 路径: 开关关时全 404 (门藏不泄后台存在)
if (!ADMIN_ENABLED) return res.status(404).end();
```
> omn 注: 旧 `GATE_ADMIN_TOKEN` 机制已废 (82d6559 改造),现纯布尔开关,后台鉴权全交业务层自身。

### 限流 (单阈值字节守卫)

```js
const CTX_GUARD_ENABLED = process.env.GATE_CTX_GUARD_ENABLED !== '0';  // 默认开
const CTX_MAX_BYTES = parseInt(process.env.GATE_CTX_MAX_BYTES || '1500000', 10) || 1500000;
const CTX_BYTES_PER_TOKEN = parseInt(process.env.GATE_CTX_BYTES_PER_TOKEN || '8', 10) || 8;
app.use('/v1', (req, res, next) => {
  if (!CTX_GUARD_ENABLED || req.method !== 'POST') return next();
  const cl = parseInt(req.headers['content-length'] || '0', 10);
  if (!cl || cl <= CTX_MAX_BYTES) return next();                  // 仅判 content-length 不缓冲 body 保流式
  const estTokens = Math.floor(cl / CTX_BYTES_PER_TOKEN);
  return res.status(413).json({ error: { type: 'context_length_exceeded', est_tokens: estTokens, limit_bytes: CTX_MAX_BYTES } });
});
```
> KNOWN-LIMITATION: chunked 无 content-length 不拦 (放行由堆 exhaustion 兜底)。

### 超时 + 健康端点 + 透传 + 日志

```js
const UPSTREAM_TIMEOUT_MS = parseInt(process.env.GATE_UPSTREAM_TIMEOUT_MS || '30000', 10) || 30000;
app.get('/healthz', async (req, res) => {
  if (shuttingDown) return res.status(503).json({ ok: false });
  const r = await fetch(`http://127.0.0.1:${OR_PORT}/api/monitoring/health`, { signal: AbortSignal.timeout(2000) }).catch(()=>null);
  r?.ok ? res.json({ ok: true }) : res.status(503).json({ ok: false });
});
app.use('/v1', (req, res) => proxyV1(req, res));   // /v1 整挂透传 (无具名子路径路由, 由上游业务自路由)
function logGate(req, fields) {
  const line = JSON.stringify({ ts: Date.now(), level: 'error', component: 'gate',
    requestId: req?._gateReqId, method: req?.method, path: req?._normPath,
    httpStatus: fields.httpStatus, errorCode: fields.errorCode, msg: fields.msg });
  process.stderr.write(line + '\n');   // 单行 JSON stderr, 无 headers/body/psk (脱敏)
}
```
> omn 注: `GATE_UPSTREAM_TIMEOUT_MS` 代码默认 30000;长思考流场景 Space Variable 覆盖 180000(对齐上游 M7)。
> gate.js **无 retry/退避/Retry-After**(错一次即终态 502/503/504), 如需重试在上游业务自身实现。

### omn 实证
- PSK safeEqual: `gate.js:23/46-49/67-73/187-198`
- admin 布尔 `'1'`: `gate.js:24/182`(无 GATE_ADMIN_TOKEN)
- CTX guard 单阈值: `gate.js:41-43/204-217`
- timeout/healthz/透传/日志: `gate.js:27/148-159/341/84-107(105 stderr)`
- 契约源: `CLAUDE.md:53-57`(§6 网关接线段)

---

## §8 workflow 分流(零 Rebuild 升级机制)

六件制,dev/prod 对称,命名规约 `<动作>-<层>-<space>`.yml:

| 层 | 触 Rebuild? | dev (自触 push) | prod (显令 dispatch) |
|----|------------|----------------|---------------------|
| 逻辑层 | 否 | `sync-logic-nonoke.yml` | `sync-logic-nomke.yml` |
| 骨架层 | 是 | `sync-space-nonoke.yml` | `sync-space-nomke.yml` |
| 日志取证 | N/A | `fetch-nonoke-logs.yml` | `fetch-nomke-logs.yml` |

**逻辑层同步 (骨架, 零 Rebuild)**:
```yaml
name: Sync logic to HF Dataset (dev)
on:
  push:
    branches: [main]
    paths: ['dev/logic/**', '.github/workflows/sync-logic-nonoke.yml']
  workflow_dispatch:
jobs:
  sync:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.12' }
      - name: Upload logic files to Dataset (flat layout)
        env: { HF_TOKEN: ${{ secrets.HF_TOKEN_NONOKE }} }
        run: |
          pip install -q "huggingface_hub>=1.0,<2.0"
          for f in entrypoint.sh gate.js init.sh litestream.yml package.json; do
            hf upload <owner>/<repo>-logic "dev/logic/$f" "$f" --repo-type dataset --token "$HF_TOKEN" \
              --commit-message "sync(logic): ${GITHUB_SHA::7} $f" || exit 1
          done
      - name: Verify sha256 readback          # 逐字节血缘验证
        run: |
          python3 - <<'EOF'
          import hashlib, os
          from huggingface_hub import hf_hub_download
          for f in ["entrypoint.sh","gate.js","init.sh","litestream.yml","package.json"]:
              local = hashlib.sha256(open(f"dev/logic/{f}","rb").read()).hexdigest()
              remote = hashlib.sha256(open(hf_hub_download("<owner>/<repo>-logic", f, repo_type="dataset", token=os.environ["HF_TOKEN"]),"rb").read()).hexdigest()
              if local != remote: raise SystemExit(f"{f}: MISMATCH 血缘断裂")
              print(f"{f}: OK {local[:8]}")
          EOF
```

**骨架层同步 (触 Rebuild)**: paths = `Dockerfile`/`start.sh`/`README.md`/`.gitattributes`,白名单 cp 后 `git push --force` 到 HF Space git 仓 → 触 HF 自动 Rebuild。

**日志取证**: cron `schedule` + `workflow_dispatch`(input `log_type: run/build/both`),抓 HF `/api/spaces/<owner>/<space>/logs/<kind>` → 脱敏闸(fail-closed 扫 nvapi-/Bearer/PSK)→ 落 evidence 分支 7 天留存。

### omn 实证
- 逻辑层不触 Rebuild: `sync-logic-nonoke.yml:3`(注释低风险)+ `sync-logic-nonoke.yml:40`(推 Dataset)
- 骨架层触 Rebuild: `sync-space-nonoke.yml:3`(高风险)+ `sync-space-nonoke.yml:63`(`git push --force`)
- paths 互斥: `dev/logic/**`(五件) vs `Dockerfile/start.sh/README.md/.gitattributes`(四件)
- prod 显令: `sync-logic-nomke.yml:18-19` 仅 `workflow_dispatch`
- 逻辑层 readback: `sync-logic-nonoke.yml:48-68`

---

## §9 secret 纪律(护栏)

四道防线:

| 线 | 真态 |
|----|------|
| §2 纪律 | secret 值零入会话/文档/git, 记录只写位置; 一律 env 占位; 测试合成串(chr 拼接); 最小 scope |
| .gitignore | `.env*` / `*.key` / `secrets/` / `*.pem` / `pm.py`(含明文 dev key) |
| PreToolUse hook | `secret-scan.py` 扫 `nvapi-[A-Za-z0-9_\-]{20,}` / `X-Internal-PSK[:=]...` / `Bearer ...` 三类, 命中 `exit(2)` 拦, 放行 `${VAR}`/`$(...)`/`<REDACTED>`, matcher `Bash\|Write\|Edit` |
| settings.json deny | `Read(**/.env*)` / `Read(**/secrets/**)` / `Read(**/*.key)` / `Bash(git push*)` / `Bash(*factory_reboot*)` 16 条 deny + `Bash(git add*)`/`Bash(git commit*)` 2 条 ask |

**部署件全 env 占位 (零硬码)**: start.sh/Dockerfile/init 全 `$HF_TOKEN`/`$NIM_KEYS`/`$INTERNAL_PSK` env 名引用 + 主动 `<REDACTED>` `sed` 脱敏 stderr 回放。

### omn 实证
- secret-scan.py 真源: `.claude/hooks/secret-scan.py:6-10`(三正则)
- settings.json deny/ask: `.claude/settings.json:3-24`(16 deny + 2 ask)
- §2 纪律: `CLAUDE.md:25-28`
- 全 env 占位: `start.sh:79/93`(sed 脱敏)+ `init-nim-keys.sh:526/910/997`(env 名)
- 口径缺口: secret-scan.py 三类未含 R2 access key (旧 sh 版有未接线),Zen若需可补

---

## §10 升级与回滚

| 升级 OmniRoute 上游 | 路径 |
|---------------------|------|
| 日常 (推荐) | GHCR 推新版镜像到 `:stable` tag → Space Rebuild 自动拉 → ARG 不动(零 git) |
| 钉 digest (备选) | 改 ARG 默认值钉 `<tag>@sha256:<digest>` → commit + push → sync-space 触 Rebuild → dev 24h 绿 → dispatch prod |

| 升级依赖包 (litestream/huggingface_hub) | 路径 |
|-----------------------------------------|------|
| 改 Dockerfile ARG 默认值 (区间驱逐改上限) | commit + push → sync-space 触 Rebuild, 或 HF Variable buildtime 覆盖零改件 |

回滚: 日常路径 `:stable` 重推旧 digest 即回;钉 digest 路径 `git revert` + push + Rebuild。

---

## §11 新项目落地清单

照搬骨架件 + 按业务改逻辑层:

1. **建 GHCR base 镜像**: Dockerfile 仅装 `litestream` 二进制 + 工具链(curl/jq/python3/sqlite3)+ `/data` 软链,**不 COPY 业务件** → push `:stable`
2. **项目 Dockerfile**: 抄 §3 三件定态(三 ARG+ENV 双轨)+ §4 start.sh 自愈引导
3. **逻辑层**: 业务件全放 Dataset 根平铺,**必须存 `entrypoint.sh`**(daemon 编排抄 §5)
4. **R2 配置**: 照 §6 配 litestream.yml,secret 全 Space Secret(env)
5. **网关**: 抄 §7 gate.js(认证+限流+透传+日志),改业务路由部分
6. **workflow**: 六件制抄 §8,改 owner/repo 名 + token secret 名
7. **secret 护栏**: .gitignore + secret-scan hook + settings.json deny 照 §9
8. **dev 先跑六绿**: boot/init/健康/长思考/限流基线/过夜 → 才 dispatch prod

---

## §12 omn 现役文件拓扑速查

```
环境层 (git 仓根):
  Dockerfile              # 三件定态 ARG+ENV (BASE_IMAGE/LITESTREAM/HF_HUB_RANGE)
  start.sh               # 自愈引导 (110行三段: 自愈/校验/拉逻辑)
  README.md              # 纯 HF frontmatter (10行)
  .gitattributes
  .github/workflows/     # 六件制 (sync-logic/space × dev/prod, fetch-logs × 2)

逻辑层 (dev/logic/, 同步到 Dataset 根平铺):
  entrypoint.sh          # daemon 编排 PID 1 (287行, 四子进程+监督循环)
  gate.js                # 网关 (464行: PSK/admin/ctx-guard/透传/日志)
  init-nim-keys.sh       # 业务初始化 (幂等, 后台跑)
  litestream.yml         # R2 复制配置
  package.json           # 逻辑层依赖声明
  helper.sh              # 补包统一入口 (本轮加: cryptography/boto3) [业务示例]
  omn_redact.py          # 脱敏引擎 lib [业务示例]
  omn_encrypt.py         # 加密保真 lib (Fernet) [业务示例]
  omn_scheduler.py       # stdout 日志 CommitScheduler [业务示例, 本轮加]

运维层 (git 仓, 永不进 Space):
  ops/STATUS.md          # 当前部署态硬指标
  ops/DECISIONS.md       # 只增不改决策账
  ops/release-checklist.md  # 切流量前 A/B/C/M 验收
  ops/incidents/         # 七段式事故档
  audit/                 # 历史堪察档
  HANDOFF.md             # 交接+架构契约 SSOT
  CLAUDE.md              # 工作宪法 (护栏+拓扑铁律)
```

---

## 附录 A: omn 业务耦合点(新项目替换处)

| 耦合点 | omn 业务示例 | 新项目替换 |
|--------|------------|-----------|
| `init-nim-keys.sh` | NIM key 池初始化 + NVIDIA 上游 | 改为自身业务的密钥池/init 逻辑 |
| `gate.js` OR 透传 | 透传 OmniRoute (127.0.0.1:3000) | 改后端业务端口/路由 |
| `package.json` | OmniRoute Next.js 依赖 | 改自身业务依赖 |
| `omn_encent/scheduler/redact.py` | stdout 公开存储加密/脱敏一条龙 | 按需保留或删 (架构本质是"日志不丢",实现可换) |
| 模型路由 | glm-5.2/gpt-oss 等模型名 | 删除或替换 |

架构本质 = §0~§9 骨架(三层+bypass铁律+三件驱逐-entrypoint-daemon-R2-litestream-gate-workflow-secret),业务耦合仅在逻辑层件内,替换不影响架构永续性。

---

## 附录 B: 已知局限与边界

- **HF 免费层 cpu-basic 资源**: 2 vCPU/16GB RAM/50GB ephemeral/48h 休眠自醒,出站限 80/443/8080/7-16 后密集推送冻 build
- **litestream 复制范围单一**: 仅 `storage.sqlite` 一件,非 DB 件(.init-done/log/key)不复制重启丢
- **HF 日志 30min 窗口**: boot 后 30min 内须 cron 抓取归档,有意 reboot 前先抓尾段
- **gate CTX guard 不拦 chunked**: 无 content-length 放行,由堆 exhaustion 兜底
- **不新建 Space**: HF 2026-07 关闭新建通道,全程两 Space 保(祖父条款)
- **secret-scan.py 口径缺口**: 三正则未含 R2 access key,如需可补入 PATTERNS

---

> 本模板提炼自 omn 血统,源血统见 `omn-merge` 仓 `DECISIONS.md`(三件永不再改三条)+ `CLAUDE.md`(工作宪法)+ `HANDOFF.md`(架构契约)。
> 维护原则: 本文随 omn 现役血统演进同步,重大架构变更(如改三层定义/加新空间)须更新本文 §1 + 附录。
