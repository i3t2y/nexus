# Nexus 云脑三件套生产部署方案

> **版本**: 2026-08-22 Production
> **原则**: 第一性原理（约束不可变 → 分层隔离 → 丢失窗口显式 → 确定性优先）
> **查证源**: HF 官方文档、Neon 官方、Cloudflare R2 官方、Postgres SKIP LOCKED 生产实践、Gork 论述（docs/new/nexus-grok.txt）、源码级核证（2 Explore agent）
> **目标**: 在 HF Docker 三席 + Neon Free + R2 Free 约束下，实现**可重启、可恢复、可观测、可演进**的单热脑系统。

---

## 一、三件套真实形态（核心结论）

**"三件套" ≠ 三个独立 Space。实际是 Hermes 单体内三个职能：**

| 件 | 形态 | 部署状态 |
|----|------|----------|
| **Hermes**（入口/路由/调度/IM） | 唯一热 Docker Space `sonoke/h`，四层永续已闭环 | ✅ 现役 |
| **Mem0**（记忆层） | **进程内 OSSBackend → pgvector**（base 镜像 fork 插件） | ⏳ 本方案部署 |
| **LangGraph**（编排） | **库非独立 Space**（requirements-base.txt 已含 langgraph==0.2.74 + psycopg3） | ✅ 作为库被 Hermes 引用 |
| **任务派发闭环** | act delegate kind=npc → 本机桥 → CNB CodeBuddy | ✅ 代码已完成（6b05c2c/95f2b95） |

**Gork 亲证（GROK-ANALYSIS.md 收口合同视角）**：
- L97: "mem0 HTTP → memlg：**进程内 oss pgvector**" —— Mem0 走进程内 OSS pgvector
- L98: "三件套 = 三 Space → **三件套缩进 Hermes 一进程**" + "**LangGraph 改库非 Space**" —— 不独立部署 LangGraph

---

## 二、目标拓扑

```
用户 → Telegram
  → CF Worker (tele.nexush.cc.cd)          # DNS 绕过 + ALLOWED_TOKENS
    → sonoke/h (Hermes 唯一热脑)
         ├─ OmniRoute (nonoke/omn)         # /v1/chat/completions
         ├─ Neon HTTP /sql + pgvector 短 TCP   # mem0 记忆 + 四表
         ├─ R2 (snapshots/<ts>/ + MANIFEST)   # 灾备快照
         └─ HF Bucket (逻辑层 /data + home 镜像)

nmem/memlg = 可选冷备（暂停，2026-08-22 降级可选，单 Space 部署不依赖）
langgraph/claude-code/codex = 永久取消（P4 无席）
```

**同时只开一台 Hermes**。OmniRoute 可常开。冷备降级为可选。

---

## 三、Mem0 OSS 部署链（本方案核心）

**路径**：Hermes agent → **OSSBackend**（base 镜像内置 fork 插件 `_backend.py`，路由 `oss > host > platform`）→ pgvector（pooler 短 TCP 用完即关，与 HTTP /sql 主路分离）。

### 四层永续（Mem0 遵循）

| 层 | 改动频率 | 触发 rebuild？ | 生产规则 |
|----|----------|----------------|----------|
| 镜像层 (GHCR `:stable`) | 月级 | 否（覆盖 tag） | 本地 build 验过再推；依赖升级才 README 一字符触发唯一 rebuild |
| 环境层 (Dockerfile + README + start.sh) | **永不** | 是 | 墓碑；仅 5 行 Dockerfile |
| 逻辑层 (Bucket `/data`) | 日级 | 否 | `sync-logic-bucket.sh --no-delete` + Restart |
| 配置层 (HF Secrets) | 按需 | 否 | 全 Secrets，不入 git |

### 技术可行性（已核证）

- **OSSBackend pgvector 走 psycopg3**（`psycopg_pool.ConnectionPool`），base 已装 `psycopg[binary]>=3.2.0` + `psycopg-pool>=3.2.0` → **无需 psycopg2**（Gork 原文说 psycopg2 是过时错误）
- **需补 `mem0ai==2.0.10`** 到 `requirements-base.txt` 尾部（`--no-deps` 跳 extras，不含 `[nlp]` spaCy）
- **Neon 已支持 pgvector**（memgraph 的 neondb 项目 vector extension 已装，SECRETS.md 确认）
- **mem0 的 pgvector.py 里 `CREATE EXTENSION` 调用在 Neon 非真 superuser 会报错** → 需按 `20_pgvector_ext.py` 方式 patch 成 pass（extension 由 Neon Console/cloud_admin 预装）

### mem0.json 配置（固化 4 改，2026-08-14 端到端实证）

```json
{
  "mode": "oss",
  "agent_id": "hermes",
  "oss": {
    "vector_store": {
      "provider": "pgvector",
      "config": {
        "connection_string": "${MEM0_PG_URI}",
        "collection_name": "hermes_mem0",
        "hnsw": false,
        "sslmode": "require",
        "embedding_model_dims": 2048
      }
    },
    "llm": {
      "provider": "openai",
      "config": {
        "model": "glm-4.7-flash",
        "openai_base_url": "https://api.z.ai/api/paas/v4",
        "api_key": "${ZAI_API_KEY}",
        "temperature": 0.1,
        "max_tokens": 2000
      }
    },
    "embedder": {
      "provider": "openai",
      "config": {
        "model": "nvidia/nemotron-3-embed-1b",
        "openai_base_url": "https://integrate.api.nvidia.com/v1",
        "api_key": "${NVIDIA_API_KEY}"
      }
    }
  }
}
```

**4 改依据**：
1. embedder.model: bge-m3 → **nemotron-3-embed-1b**（NIM 唯一账户可用对称 model，无需 input_type；bge-m3 对此账户返 500）
2. embedding_model_dims: 1024 → **2048**（匹配 nemotron 返维）
3. hnsw: true → **false**（pgvector HNSW 索引上限 2000 维，nemotron 2048 超限）
4. openai_base_url 去末尾 `/`（`paas/v4/` → `paas/v4`）

**维度分层**：embedder 段**不写** embedding_dims（避免 NIM 收 dimensions 参）；vector_store 段显写 embedding_model_dims=2048（pgvector 建表列宽）。两层分治是 mem0 OSS 设计本质。

### 激活路径（agent_init.py 938-1065）

- 读 config.yaml 的 `memory.provider`（:987 `mem_config.get("provider", "")`）→ 激活开关
- `load_memory_provider("mem0")` → `find_provider_dir`（bundled 优先）→ import 插件 `__init__.py` → `is_available()` 闸门 → `add_provider` + `initialize_all`（:1036）→ 工具注入（:1045，mem0_search/mem0_add）
- 失败兜底：plugin init 异常被 try/except 吞 warning 不 crash boot

---

## 四、LangGraph 库定位

- **不独立部署 LangGraph Space**（Hermes 原生是完整 agent loop：max_iterations=90 + 重试3 + 工具护栏 + memory 生命周期钩子，单 agent 多步循环已够）
- memgraph 的 `graph/__init__.py`（LangGraph StateGraph worker）是独立 memgraph Space 的编排器，非 sonoke/h 部署对象
- langgraph 库已在 requirements-base.txt L23-25，被 Hermes 直接引用

---

## 五、任务派发闭环

- act delegate 已改 `kind='npc'`（6b05c2c）
- 本机桥 `poll_worker_tasks.py` 已落地（Stage B, 95f2b95）：扫 Neon task_queue kind=npc → FOR UPDATE SKIP LOCKED 消费 → CNB CodeBuddy OpenAPI
- 任务状态：pending|running|completed|failed；kind: generic|graph|npc|claude_code|pi|dsh（workbuddy_npc 已收）

---

## 六、HF Secrets 清单（sonoke/h 新增）

| Secret | 值 | 说明 |
|---|---|---|
| `MEM0_MODE` | `oss` | 门控激活 mem0 记忆层 |
| `MEM0_PG_URI` | Neon 连接串 `postgresql://neondb_owner:***@<host>/neondb?sslmode=require` | 指向**已装 vector extension 的 Neon 项目**（部署时确认与 sonoke/h 现役 persist 同一库或 memgraph neondb） |
| `ZAI_API_KEY` | 智谱 API key | LLM 提炼（glm-4.7-flash） |
| `NVIDIA_API_KEY` | NIM API key | embedder（nemotron-3-embed-1b） |

- 若 sonoke/h 的 Neon 未装 vector：用户先在 Neon Console 用 cloud_admin 跑 `CREATE EXTENSION IF NOT EXISTS vector;`
- 不新增 ADMIN_API_KEY/JWT_SECRET（OSSBackend 模式不需要，那是 server 模式）
- 不新增 MEM0_TELEMETRY（real-start.sh 自动 export=false）

---

## 七、部署步骤（用户手动，红线不替）

1. 本地 `docker build -t ghcr.io/i3t2y/nexus-base:stable` + `docker push`（补 mem0ai 后重建 base）
2. HF Space Settings → Factory Rebuild（拉新 base digest）
3. HF Space Settings → Secrets 补 5 项（上述 4 项 + 若需改 MEM0_PG_URI）
4. `source ~/.env.sonoke && bash scripts/sync-logic-bucket.sh`（推 real-start.sh + mem0.json.template + config.yaml.template 入 Bucket）
5. HF Space Settings → Restart（不 rebuild）
6. 查 boot log：搜 "Memory provider 'mem0' activated"（成功）/ "plugin init failed"（失败）/ "mem0.json generated"（注入成功）

---

## 八、验收标准

1. 本地 `bash -n hermes/scripts/real-start.sh` 语法过
2. 本地 `python -m py_compile` mem0 注入段语法过
3. 本地 envsubst 真值测试：set ZAI/NVIDIA/MEM0_PG_URI → JSON valid + 占位符全替换（已验 ✅）
4. yaml valid：config.yaml.template memory.provider=mem0（已验 ✅）
5. 端到端（部署后）：HF boot log "Memory provider 'mem0' activated" → 对话触发 mem0_add → Neon hermes_mem0 表有 row → mem0_search 能返回
6. 任务派发闭环（代码已完成，部署后验）：act delegate 写 kind=npc → 本机桥 poll → CNB 返回
7. Neon CU 月消耗 < 30（留余量）
8. 无密钥入 git；旧泄漏 key 已失效
9. 单热脑运行 48h 无异常重启循环

---

## 九、持久化（四条独立介质）

| 介质 | 内容 | 方式 | 丢失窗口 |
|------|------|------|----------|
| A Neon 活数据 | Mem0 向量 + 四表 + task_queue | 请求时 HTTP 或短 TCP | 实时 |
| B R2 大脑快照 | home.tar.gz + sessions.tar.gz + snapshots/<ts>/ | 10min + SIGTERM + CAS | 30 min |
| C Bucket 逻辑 | 代码，不参与记忆 | — | 无 |
| D Secrets | 密钥现场合成 .env | 永不进包 | 无 |

---

## 十、不做（明确划出）

- **不独立部署 LangGraph Space**（Hermes 原生编排已够单 agent；Gork 亲证"LangGraph 改库非 Space"）
- 不碰 memgraph/bucket/graph/__init__.py（独立 memgraph Space 的 worker，非 sonoke/h）
- 不 push 任何 GitHub/HF 除非用户显式同意
- 三文件墓碑（Dockerfile+README+start.sh）不动
- 密钥只放 Secrets 不入 git

---

## 附：关键文件清单

- `/home/laisi/nexus/old/docker/requirements-base.txt` — 已追加 mem0ai==2.0.10 ✅
- `/home/laisi/nexus/hermes/scripts/real-start.sh` — 已加 mem0 注入段（门控 MEM0_MODE=oss）✅
- `/home/laisi/nexus/hermes/scripts/config.yaml.template` — 已加 memory.provider=mem0 ✅
- `/home/laisi/nexus/hermes/scripts/mem0.json.template` — 已恢复（813 字节固化 4 改）✅
- `/home/laisi/nexus/old/memgraph/bucket/patches/20_pgvector_ext.py` — Neon CREATE EXTENSION pass 参考
- `/home/laisi/nexus/old/docker/nexus-base.Dockerfile` — 构建真源，pip install -r 自动装 mem0ai
- `/home/laisi/nexus/memgraph/docs/SECRETS.md` — Neon vector extension + secrets 对标