# Secrets 清单

> **不含实际值, 只列 key 名和说明。**
> 实际值分散在: HF Space Secrets, GitHub Repo Secrets (`i3t2y/nexus`)

## GitHub Repo Secrets (`i3t2y/nexus`)

| Key | 说明 | 已配 |
|---|---|---|
| `HF_TOKEN` | nmem 账号 HF token (push to Space + Dataset) | ✅ |

## HF Space Secrets (`nmem/memlg`)

| Key | 说明 | 已配 |
|---|---|---|
| `HF_TOKEN` | nmem 账号 token (拉 Dataset nmem/nworker) | ✅ |
| `ADMIN_API_KEY` | mem0 鉴权 key (hermes 发 X-API-Key 匹配此值) | ✅ |
| `POSTGRES_HOST` | Neon pooler endpoint | ✅ |
| `POSTGRES_PORT` | 5432 | ✅ |
| `POSTGRES_USER` | neondb_owner | ✅ |
| `POSTGRES_PASSWORD` | Neon 密码 | ✅ |
| `NIM_API_KEY` | NVIDIA NIM embedder API key | ✅ |
| `ZAI_API_KEY` | 智谱 LLM API key | ✅ |
| `JWT_SECRET` | mem0 JWT 签名 (启动需要, 鉴权未用) | ✅ |
| `OPENAI_API_KEY` | mem0 默认配置占位 (实际用 NIM/智谱) | ✅ |

## HF Space Variables (public, 非 Secret)

| Key | 值 | 说明 |
|---|---|---|
| `POSTGRES_COLLECTION_NAME` | `memories` | pgvector collection 名 |
| `MEM0_DEFAULT_LLM_MODEL` | `glm-4.7-flash` | 智谱模型 |
| `MEM0_DEFAULT_EMBEDDER_MODEL` | `nvidia/nemotron-3-embed-1b` | NIM embedder |
| `MEM0_TELEMETRY` | `false` | 关闭遥测 |
| `POSTGRES_DB` | `neondb` | ⚠️ 应改为 Secret |
| `APP_DB_NAME` | `neondb` | ⚠️ 应改为 Secret |

## Neon Postgres

- Project: us-east-1
- Database: `neondb`
- Extension: `vector` (pgvector)
- Connection: `postgresql://neondb_owner:***@ep-small-wildflower-audan7wz-pooler.c-10.us-east-1.aws.neon.tech/neondb`

## hermes 后台

- mem0 mode: Self-hosted server
- Host: `https://nmem-memlg.hf.space`
- API Key: `ADMIN_API_KEY` 的值

## cron-job.org (待建)

- URL: `https://nmem-memlg.hf.space/health`
- 频率: 每 4 分钟
- Method: GET
- 无需 auth header
