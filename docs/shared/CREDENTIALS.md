# 凭证清单

> 凭证真值在各 HF Space Secrets 与 Cloudflare Worker Secrets 中；本文件只记录凭证名称、来源与用途。
> **绝不**把真凭证提交进 git。

## 命名约定

所有凭证用一个统一前缀（见下表），跨组件同名便于核对。

## Cloudflare R2 (active — 灾备快照层, 2026-08-18 恢复)

> 2026-08-17 Supabase→Neon 迁移后 R2 一度砍掉。2026-08-18 恢复:读源改 Neon (HTTP /sql),
> R2 作 Neon 主路的灾备快照 (persist_to_r2.py 周期读 Neon 四表 → 原子上传 R2 + manifest)。

| 环境变量 | 来源 | 用途 |
|---------|------|------|
| `R2_ENDPOINT` | R2 → Manage API Tokens | S3 兼容端点 `https://<acct>.r2.cloudflarestorage.com` |
| `R2_ACCESS_KEY_ID` | 同上 | 访问 key |
| `R2_SECRET_ACCESS_KEY` | 同上 | 秘钥（Secret 不回显） |
| `R2_BUCKET` | R2 → bucket 创建 | 统一桶名，默认 `nexus-checkpoints` |
| `R2_REGION` | 固定 `auto` | — |

## Neon Postgres (active — 主路持久化, 2026-08-17 替代 Supabase)

| 环境变量 | 来源 | 用途 |
|---------|------|------|
| `POSTGRES_HOST` | Neon Dashboard → Connection Details | 主机名（带 `-pooler` 后缀会被脚本自动 strip，HTTP /sql 要 non-pooler） |
| `POSTGRES_PORT` | 同上 | 默认 `5432` |
| `POSTGRES_USER` | 同上 | 数据库用户名 |
| `POSTGRES_PASSWORD` | 同上 | 秘钥（Secret 不回显） |
| `POSTGRES_DB` | 同上 | 默认 `neondb` |

> Neon HTTP /sql 端点 (PR #9827 未文档化但官方 driver 走同端点,稳定): `POST https://{host}/sql`,
> header `Neon-Connection-String: postgresql://{user}:{password}@{host}:{port}/{db}?sslmode=require`,
> body `{query, params}`。每次 POST 完即断 → Neon 自然 scale-to-zero, CU-h ~0.5-3/月。
> `persist_to_neon.py` (主路四表) + `persist_to_r2.py` (副路读 Neon) 共用此读法。

## Supabase (archived — 2026-08-17 全退役)

> 已由 Neon 取代。旧 Secrets 可从 HF Settings 清除。留此节作回退凭证 (other/sql/ + other/spaces/hermes/)。

| 环境变量 | 来源 | 用途 |
|---------|------|------|
| `SUPABASE_URL` | Project Settings → API | `https://<id>.supabase.co` |
| `SUPABASE_ANON_KEY` | 同上 | 客户端/低权限 |
| `SUPABASE_SERVICE_ROLE_KEY` | 同上 | **服务端高权限** |
| `SUPABASE_DB_URI` | Database → Connection string (URI, port 6543) | 旧 LangGraph PostgresSaver 直连 |

## Hugging Face

| 环境变量 | 来源 | 用途 |
|---------|------|------|
| `HF_TOKEN` | HF Settings → Access Tokens | 私有 Space 的 HF 层访问（Worker/`keepalive.py` 探私有 Space 必需）；Space CI/私有库访问 |
| `SPACE_AUTHOR_NAME` | 运行时自动注入 | 自填也可 |
| `SPACE_REPO_NAME` | 运行时自动注入 | 自填也可 |

## Nexus 内部

| 环境变量 | 生成方式 | 用途 |
|---------|---------|------|
| `NEXUS_API_KEY` | `python -c "import secrets;print(secrets.token_urlsafe(32))"` | Space 间 + Worker 鉴权，全系统同一把 |
| `NEXUS_AUTH_MODE` | 空=生产 `fail-closed`；`dev`=本地免鉴权 | 缺 `NEXUS_API_KEY` 时拒绝（500）而非放行 |
| `GATEWAY_URL` | Worker 部署后 | Hermes 调 Worker 用 |

> 鉴权 header：调下游 Space 用 `X-Nexus-Key: Bearer <NEXUS_API_KEY>`（`Authorization` 留给 HF 层 `HF_TOKEN`）；调 Worker 网关（无 HF 层）仍用 `Authorization: Bearer <NEXUS_API_KEY>`。

## 下游 Space URL（Hermes 用）

| 环境变量 | 示例值 |
|---------|--------|
| `LANGGRAPH_URL` | `https://<owner>-langgraph.hf.space` |
| `CLAUDE_URL` | `https://<owner>-claude-code.hf.space` |
| `CODEX_URL` | `https://<owner>-codex.hf.space` |

## 安全红线

1. `SUPABASE_SERVICE_ROLE_KEY`、`R2_SECRET_ACCESS_KEY`、`POSTGRES_PASSWORD`、`NEXUS_API_KEY` 一律走 Secrets，不入代码、不入 `.env`（`*.env` 已 gitignore）。
2. 提交前自查：`grep -rnE "(sk-|token|secret|password|api_key)" --include=*.py --include=*.env .` 不应命中真值。
3. 最低权限：Space 用 anon key 能完成的别用 service_role。
4. 轮换：建议每 90 天轮换 `NEXUS_API_KEY` 与 R2 token。
