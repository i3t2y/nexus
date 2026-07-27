# 凭证清单

> 模板阶段：凭证占位，未真填。就位后填入各 Space Secrets（HF）与 Worker Secrets（Cloudflare）。
> **绝不**把真凭证提交进 git。`.env.example` 是模板，`.env` 已在 `.gitignore`。

## 命名约定

所有凭证用一个统一前缀（见下表），跨组件同名便于核对。

## Cloudflare R2

| 环境变量 | 来源 | 用途 |
|---------|------|------|
| `R2_ENDPOINT` | R2 → Manage API Tokens | S3 兼容端点 `https://<acct>.r2.cloudflarestorage.com` |
| `R2_ACCESS_KEY_ID` | 同上 | 访问 key |
| `R2_SECRET_ACCESS_KEY` | 同上 | 秘钥（Secret 不回显） |
| `R2_REGION` | 固定 `auto` | — |

## Supabase

| 环境变量 | 来源 | 用途 |
|---------|------|------|
| `SUPABASE_URL` | Project Settings → API | `https://<id>.supabase.co` |
| `SUPABASE_ANON_KEY` | 同上 | 客户端/低权限 |
| `SUPABASE_SERVICE_ROLE_KEY` | 同上 | **服务端高权限，仅入 Space Secret** |
| `SUPABASE_DB_URI` | Database → Connection string (URI, port 6543) | LangGraph PostgresSaver 直连 |

## Hugging Face

| 环境变量 | 来源 | 用途 |
|---------|------|------|
| `HF_TOKEN` | HF Settings → Access Tokens | Space CI/私有库访问（可选） |
| `SPACE_AUTHOR_NAME` | 运行时自动注入 | 自填也可 |
| `SPACE_REPO_NAME` | 运行时自动注入 | 自填也可 |

## Nexus 内部

| 环境变量 | 生成方式 | 用途 |
|---------|---------|------|
| `NEXUS_API_KEY` | `python -c "import secrets;print(secrets.token_urlsafe(32))"` | Space 间 + Worker 鉴权，全系统同一把 |
| `GATEWAY_URL` | Worker 部署后 | Hermes 调 Worker 用 |

## 下游 Space URL（Hermes 用）

| 环境变量 | 示例值 |
|---------|--------|
| `LANGGRAPH_URL` | `https://<owner>-langgraph.hf.space` |
| `CLAUDE_URL` | `https://<owner>-claude-code.hf.space` |
| `CODEX_URL` | `https://<owner>-codex.hf.space` |

私有 Space 调用需带 `Authorization: Bearer <HF_TOKEN>` 或经 Worker 转发。

## 安全红线

1. `SUPABASE_SERVICE_ROLE_KEY`、`R2_SECRET_ACCESS_KEY`、`NEXUS_API_KEY` 一律走 Secrets，不入代码、不入 `.env`（`*.env` 已 gitignore）。
2. 提交前自查：`grep -rnE "(sk-|token|secret|password|api_key)" --include=*.py --include=*.env .` 不应命中真值。
3. 最低权限：Space 用 anon key 能完成的别用 service_role。
4. 轮换：建议每 90 天轮换 `NEXUS_API_KEY` 与 R2 token。
