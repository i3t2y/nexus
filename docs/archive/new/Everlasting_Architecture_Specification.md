> ⚠️ DEPRECATED（2026-07-29 合并去冗）
> 本文为部署/子目录骨架件配属白皮书，与 `Nexus集群永续架构最强模板.md`（架构 SSOT 权威件）内容重叠，保留备查，新读优先看权威件。
> 本文已渗八大修正项前的旧叙述，已知偏差：
> - §1.2 litestream 主路径 → 仅复 SQLite WAL，Postgres 误配，正解 Supabase 备份。
> - §2.1 双账号 dev/prod 双 Space → HF 2026-07 免费层禁建 Docker Space，不再适用。
> - §1 三层解耦未引官方铁义（Dataset 永远只读，仅 Bucket rw）；§4 部署清单未对齐 Nexus a142da9 已落 Bucket 单挂路径。

# Nexus 混合 Agent 集群永续架构部署与配置白皮书

> **发布日期**: 2026-07-29 | **架构版本**: v4.0-Everlasting (Omni-Merge)
>
> **适用范围**: HF Docker Spaces 免费账号/PRO 账号高可用多节点集群部署

---

## §1 架构总览与核心设计原则

### 1.1 三层解耦模型 (Three-Layer Decoupling)
为了规避 Hugging Face 频繁构建触发的风控机制，以及冷启动导致的 ephemeral 盘重置问题，本系统采用物理拆分法，将运行生命周期划分为三层独立载体：

1. **环境层 (Environment Layer - GHCR Base Image)**: 包含底座 Dockerfile、自愈脚本 `start.sh` 及 `README.md`。此层变动极低，避免触发 HF 侧的 Rebuild。
2. **逻辑层 (Logic Layer - HF Dataset)**: 存储真正决定业务流程的逻辑代码，平铺于 HF Dataset 根目录下。由系统动态拉取，改动即生效，零 Rebuild。
3. **持久化层 (State Layer - R2 + Supabase)**: 主数据和 SQLite 运行态文件保留在 ephemeral `/data` 中保证 IO 性能，并通过 Litestream 实时增量同步至 Cloudflare R2，形成绝对的高可用主存储机制。

### 1.2 跨云灾备双轨方案 (Dual-Track Resilience - Scheme C)
主 SQLite 数据通过 Litestream S3 协议在 10s 内进行近实时的 WAL 帧增量同步至 Cloudflare R2；备用方案则是通过内置的 CommitScheduler 在后台每 5 分钟自动打包整库快照、脱敏聚合日志后，冷备至 Hugging Face 私有 Dataset 长期归档。双路备份体系完美互补，实现极强的跨云容灾能力。

---

## §2 集群拓扑结构与命名规约

五个组件相互协同，统一入口由 Cloudflare Workers 代理转发，后端采用 single-process 模式部署在各自物理隔离的 Space 中：

| 组件名称 (Component) | 独立 Space 名 | 逻辑层 Dataset 仓库 | 主数据持久化路径 | 对外业务端口 (Internal) |
| :--- | :--- | :--- | :--- | :--- |
| **hermes** | `i3t2y/nexus-hermes` | `i3t2y/nexus-hermes-logic` | `/data/hermes.sqlite` | `8080` |
| **langgraph** | `i3t2y/nexus-langgraph` | `i3t2y/nexus-langgraph-logic` | Supabase Postgres | `8000` |
| **omniroute** | `i3t2y/nexus-omniroute` | `i3t2y/nexus-omniroute-logic` | `/data/omniroute.sqlite` | `3000` |
| **claude-code** | `i3t2y/nexus-claude` | `i3t2y/nexus-claude-logic` | `/data/claude.sqlite` | `8080` |
| **codex** | `i3t2y/nexus-codex` | `i3t2y/nexus-codex-logic` | `/data/codex.sqlite` | `8080` |

### 2.1 双账号对称部署 (Dev/Prod Symmetric Strategy)
- **Canary (金丝雀/测试环境)**: `i3t2y/nexus-<component>`，绑定逻辑 Dataset `i3t2y/nexus-<component>-logic`。
- **Production (生产环境)**: `i3t2y/nexus-<component>-prod`，绑定逻辑 Dataset `i3t2y/nexus-<component>-logic-prod`。
- **晋级生产路径**: 升级仅需对逻辑 Dataset 进行同步，并在 Space 侧触发 `Restart` 即可，实现 0 秒编译延迟。

---

## §3 安全加固与合规红线 (Security Hardening)

### 3.1 LangGraph 零信任运行模式
- **漏洞防范 (CVE-2026-28277)**: 必须在环境层和 Python 依赖中强制锁死 `langgraph>=1.2.10` 及 `langgraph-checkpoint-sqlite>=3.0.1`，彻底拦截利用 SQLite 触发的 Msgpack 任意反序列化 RCE 链。
- **开源许可规避 (L5 铁律)**: 严禁引入企业级商业授权二进制包 `langgraph-api` Server。本系统全部采用开源 **Library Mode** 构建，使用轻量级 FastAPI 托管整个 Graph 节点，确保无授权侵权合规隐患。

### 3.2 敏感数据脱敏与分级保护
为了保障私密凭据绝对安全，日志归档方案采用严格的“脱敏防火墙”机制，将日志进行分级拦截：
- **A类日志 (网关状态/健康状态)**: 可公开，脱敏后冷备到公开 Dataset。
- **B类日志 (数据库/网络交互)**: 进行高级加密和关键字剔除后（禁止包含 `api_key`、`bearer`、`psk`、`jwt` 等），视安全性决定是否备份。
- **C类日志 (系统自愈初始化/容器环境变量)**: **最高机密等级**，严禁进行任何形式的公开存储，仅允许通过 SSH/Gradio 内部文件系统离线审计。

---

## §4 极速部署指南 (Step-by-Step Deployment)

1. **构建 Base 镜像**: 将 `docker/Dockerfile` 与 `docker/start.sh` 上传至 GitHub 仓库，并通过 GitHub Action 自动构建成 `ghcr.io/i3t2y/nexus-base:stable` 镜像。
2. **创建逻辑 Dataset 仓库**: 在 Hugging Face 上创建一个名为 `i3t2y/nexus-canary-logic` 的私有数据集仓库，将 `logic/` 目录下的所有配置文件平铺上传至该仓库。
3. **配置 Space 环境变量 (Secrets)**:
   - `INTERNAL_PSK`: 必须大于 16 位的高强度网关主密钥。
   - `HF_TOKEN`: 用于冷备上传的 Hugging Face 授权令牌。
   - `LOGIC_BUCKET_REPO`: 设置为逻辑层私有 Dataset 地址（如 `i3t2y/nexus-canary-logic`）。
   - `LOG_PUBLIC_DATASET_REPO`: 脱敏日志聚合公开 Dataset 地址。
   - `R2_BUCKET`/`R2_ACCOUNT_ID`/`R2_ACCESS_KEY_ID`/`R2_SECRET_ACCESS_KEY`: 用于 Litestream 增量备份的 R2 密钥。
4. **启动 Space**: 创建相应的 Space（SDK 选择 Docker 容器），其将自适应引导环境，动态载入 Dataset，并在 10 秒内恢复数据，自动完成集群注册。
