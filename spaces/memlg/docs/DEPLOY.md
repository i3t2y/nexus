================================================================
  mem0 server 部署完整指南 — Neon + HF Space + cron-job.org
================================================================

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 1: Neon 建库 (5 分钟)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1.1 注册 Neon 免费版
    - 访问 https://neon.tech → Sign up (GitHub/Google 登录)
    - 免费版: 100 projects, 每个 0.5GB + 100 CU-h/月

1.2 创建 Project 1: mem0-memory
    - Console → New Project
    - Name: mem0-memory
    - Region: AWS us-east-1 (或离你最近的)
    - Postgres version: 16 (默认)
    - 创建后获得:
      * HOST: ep-xxx.us-east-1.aws.neon.tech
      * USER: mem0-memory_owner
      * PASSWORD: xxx
      * DATABASE: neondb (默认)

1.3 在 neondb 上启用 pgvector + 建表
    - Console → SQL Editor → 执行以下 SQL:

    -- 启用 pgvector 扩展
    CREATE EXTENSION IF NOT EXISTS vector;

    -- mem0 server auth 系统表 (alembic 会自动建, 但可手动预建)
    -- 实际让 alembic upgrade head 自动建即可, 这里只需 pgvector

    -- 验证 pgvector
    SELECT extname FROM pg_extension WHERE extname = 'vector';

1.4 (可选) 创建更多 database 用于隔离
    -- 在同一个 neondb 里用 schema 隔离, 或:
    CREATE DATABASE hermes_config;
    CREATE DATABASE agent_memory;

1.5 记录连接信息 (稍后填入 HF Secrets):
    POSTGRES_HOST=ep-xxx.us-east-1.aws.neon.tech
    POSTGRES_PORT=5432
    POSTGRES_USER=mem0-memory_owner
    POSTGRES_PASSWORD=xxx
    APP_DB_NAME=neondb
    POSTGRES_COLLECTION_NAME=memories

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 2: HF Space 部署 (10 分钟)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

2.1 创建 HF Space
    - 访问 https://huggingface.co/new-space
    - Name: mem0-server
    - SDK: Docker
    - License: MIT
    - Hardware: CPU basic (free)
    - Create

2.2 上传三文件 (用 git 或 Web UI)
    三文件 (Dockerfile + README.md + start.sh) 已在
    /home/user/mem0-server-space/ 目录

    方法A — git:
    cd /home/user/mem0-server-space
    git init
    git remote add origin https://huggingface.co/spaces/<你的用户名>/mem0-server
    git add .
    git commit -m "initial: mem0 server"
    git push -u origin main

    方法B — Web UI:
    拖三个文件上传到 Space

2.3 配置 HF Secrets (Settings → Repository secrets)
    逐个添加以下变量:

    ┌────────────────────────────┬──────────────────────────────────────┐
    │ Key                        │ Value                                │
    ├────────────────────────────┼──────────────────────────────────────┤
    │ POSTGRES_HOST              │ ep-xxx.us-east-1.aws.neon.tech       │
    │ POSTGRES_PORT              │ 5432                                 │
    │ POSTGRES_USER              │ mem0-memory_owner                    │
    │ POSTGRES_PASSWORD          │ <Neon密码>                           │
    │ APP_DB_NAME                │ neondb                               │
    │ POSTGRES_COLLECTION_NAME   │ memories                             │
    │ AUTH_DISABLED              │ true                                 │
    │ ADMIN_API_KEY              │ <自定义一个随机字符串>                │
    │ MEM0_DEFAULT_LLM_MODEL     │ glm-4.7-flash                        │
    │ MEM0_DEFAULT_EMBEDDER_MODEL│ nvidia/nemotron-3-embed-1b         │
    │ OPENAI_API_KEY             │ <NIM API key>                        │
    │ MEM0_TELEMETRY             │ false                                │
    └────────────────────────────┴──────────────────────────────────────┘

2.4 等待 Docker rebuild 完成 (5-10min)
    - Space 页面 → Settings → 查看构建日志
    - 构建完成后访问 https://<用户名>-mem0-server.hf.space/docs
    - 应看到 FastAPI Swagger UI

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 3: 配置 NIM + 智谱 (POST /configure, 一次性)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

3.1 调用 /configure 设置 LLM 和 embedder
    curl -X POST https://<用户名>-mem0-server.hf.space/configure \
      -H "Content-Type: application/json" \
      -d '{
        "llm": {
          "provider": "openai",
          "model": "glm-4.7-flash",
          "openai_base_url": "https://api.z.ai/api/paas/v4"
        },
        "embedder": {
          "provider": "openai",
          "model": "nvidia/nemotron-3-embed-1b",
          "openai_base_url": "https://integrate.api.nvidia.com/v1"
        }
      }'

    注: provider 写 "openai" 绕过 BUNDLED_PROVIDERS 校验
        openai_base_url 指向智谱/NIM 实际端点
        config 存 Neon settings 表, Restart 不丢, 只需配一次

3.2 验证 /health 端点
    curl https://<用户名>-mem0-server.hf.space/health
    → {"status":"ok","db":"connected"}

3.3 验证记忆写入
    curl -X POST https://<用户名>-mem0-server.hf.space/memories \
      -H "Content-Type: application/json" \
      -H "X-API-Key: <ADMIN_API_KEY>" \
      -d '{"messages": [{"role": "user", "content": "测试记忆写入"}]}'

3.4 验证记忆搜索
    curl -X POST https://<用户名>-mem0-server.hf.space/search \
      -H "Content-Type: application/json" \
      -H "X-API-Key: <ADMIN_API_KEY>" \
      -d '{"query": "测试"}'

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 4: cron-job.org 保活 (专门账号, 5 分钟)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

4.1 注册专门保活账号
    - 访问 https://cron-job.org/en/ → Register
    - 用一个专用邮箱注册 (如 xxx.keepalive@gmail.com)
    - 这个账号只管保活 job, 不混用

4.2 创建保活 job
    - LOGIN → Create Cron Job

    ┌─────────────────────┬─────────────────────────────────────────────┐
    │ Title               │ mem0-server-keepalive                       │
    │ URL                 │ https://<用户名>-mem0-server.hf.space/health │
    │ Execution Frequency │ Every 4 minutes                             │
    │ Request Method      │ GET                                         │
    │ Request Timeout     │ 30 seconds                                  │
    │ Notifications       │ On failure (email)                          │
    │ Save                │                                             │
    └─────────────────────┴─────────────────────────────────────────────┘

    - 原理: /health 端点内部执行 SELECT 1 → 唤醒 Neon compute
             同时保持 HF Space 48h 内有访问 → 不休眠
             一次 ping 保活两个服务

4.3 (多 project 时) 重复创建更多 job
    未来如果建 project 2 (hermes-config), project 3 (agent-memory):
    - 每个 Neon project 有独立 endpoint
    - 但 mem0 server 的 /health 只连 project 1
    - 需要额外保活 project 2/3:
      方案: 在 mem0 server 的 /health 里加多个 SELECT 1
            或在 cron-job.org 加多个 job pinging 不同 URL

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 5: hermes 切连 (2 分钟)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

5.1 修改 hermes mem0.json (/opt/data/.hermes/mem0.json)
    将 mode 从 "oss" 改为 "self_hosted", host 指向新 Space:

    {
      "mode": "self_hosted",
      "self_hosted": {
        "host": "https://<用户名>-mem0-server.hf.space",
        "api_key": "<ADMIN_API_KEY>"
      }
    }

5.2 重启 hermes daemon
    hermes daemon restart

5.3 验证
    mem0_add("测试连接")
    mem0_search("测试")

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
永续架构图
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  ┌─────────────┐    每4min GET     ┌──────────────┐    SELECT 1    ┌──────────────┐
  │ cron-job.org│ ────────────────→ │  HF Space    │ ──────────────→ │  Neon        │
  │  (免费)     │    /health        │  mem0 server │    psycopg     │  Postgres    │
  │  专门账号   │                   │  7860端口    │                │  pgvector    │
  └─────────────┘                   │  三文件Git   │                │  0.5GB免费   │
           ↑                        └──────┬───────┘                └──────────────┘
           │                               │
    保活HF Space(48h不休眠)                 │ POST /memories, /search
    保活Neon(5min不sleep)                   │
           │                               ↓
           │                        ┌──────────────┐
           │                        │ hermes/任意   │
           │                        │ agent        │
           │                        │ X-API-Key    │
           │                        └──────────────┘
           │
    Restart 后:
    HF Git 拉三文件 (不丢) → Docker rebuild → start.sh → alembic → uvicorn
    HF Secrets 加载 (不丢) → 环境变量 → 连 Neon
    Neon 数据 (不丢) → 记忆恢复
    cron-job.org 继续 ping → 保活恢复

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
维护
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

- mem0 上游更新: 不需要做任何事 (git clone --depth 1 每次建建拉最新)
- 除非要改 start.sh 或 Dockerfile, 否则不需要 push
- Neon 用量监控: Console Dashboard (当前用量 11MB << 500MB)
- cron-job.org 监控: 失败会邮件通知
