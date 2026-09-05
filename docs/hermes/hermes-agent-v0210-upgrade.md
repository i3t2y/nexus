# Hermes Agent v0.21.0 升级部署说明

> 2026-09-03 · 核: v0.20.4(6001d05) → v0.21.0(v2026.8.31, "The Pantheon Release")
> 结论:这次是**"只改一行 tag"的安全升级** —— 零依赖 pin / zero-diff / patch/build 命令全不动。
> 本地 `ghcr.io/i3t2y/nexus-base:v2026.8.31-test` 已 build + 镜像内 runtime 全验通过。

---

## 1. 是什么变了

| 维度 | v0.20.4 | v0.21.0 | 影响 |
|------|---------|---------|------|
| **my_incarnation** | 2026.8.18 | 2026.8.31 | tag 一行 |
| **依赖 pin** | 见 requirements-base.txt | **零 diff** | 无 |
| **path 重构** | gateway/config 大文件 | 拆 subcommands/{gateway,dashboard}.py | 仅内部, CLI 稳定 |
| **api_server** | hermes_cli/ | gateway/platforms/api_server.py | POST /v1/runs + GET /v1/health 全在 |
| **headless server** | 仅 dashboard | 新增 `hermes serve` | start.sh 不用改 |
| **model catalog** | GLM-5.2 等 | + GLM-5.3-Flash / qwen3.8 / Gemini 3.7 | 广度升级, 兼容旧款 |
| **行为加固** | — | protected files 写需审批 + secret 泄漏收敛 | 对逻辑层无接口破坏 |

**对外 CLI/flag/env 零破坏**(已逐锚点实证, 见认证记忆 `nexus-hermes-upgrade`):
- `hermes gateway run --replace --accept-hooks` ✓
- `hermes dashboard --host/--port/--skip-build/--no-open` 仍启动 server ✓ (start.sh 调用不须改)
- CORS patch 锚点 `allow_origin_regex` v0.21.0 在 `web_server.py:679`(L543→L679 行漂, 文本锚无碍) ✓
- K-R6 SQLite 闸门 / K-R4 web_dist / K-R8 ui-tui 全在 ✓

---

## 2. 本地验证已闭环(无需再跑)

本地 `docker build` 默认 tag `ghcr.io/i3t2y/nexus-base:v2026.8.31-test` success, 镜像内 runtime 全验:

- `hermes version` = **0.21.0**
- `is_sqlite_wal_reset_vulnerable` = **False** (K-R6 ✔, SQLite 3.53.4)
- web_dist prebuild OK (K-R4 ✔) / ui-tui/dist/entry.js prebuild OK, 12s (K-R8 ✔)
- CORS patch **APPLIED**; aiohttp 3.14.3 + 注册了 /v1/runs + /v1/health
- anthropic 0.87.0 / mcp 2.0.0 / openai 2.24.0

---

## 3. 【用户手动】部署步骤 (红线: 以下每步都必须你手动做)

> ⚠️ 2026-09-03 更正(勿照旧版执行): 原"README 单字符 push 触发 rebuild"机制系误判。
> 实证三端(见下), **这次升级不 git push 任何仓库** —— 本地 build+push GHCR :stable → **HF Console 手动 Rebuild**。
> `hermes/space/` 的 `git remote` = `github.com/i3t2y/nexus.git`(nexus 主仓库, **非 sonoke/h HF 仓库**); 照推会推到 nexus origin/main(红线)且不触发 HF rebuild。

### 3a. 本地 build + 推 GHCR :stable (先 `docker login ghcr.io`)
```bash
# GHCR PAT(classic, write:packages) 先登录
docker login ghcr.io -u i3t2y
# 注意: 构建文件叫 nexus-base.Dockerfile(非 Dockerfile); -f 指定它, 上下文/工作目录用 old/docker/(内含 requirements-base.txt + patch_web_server.py)
docker build -t ghcr.io/i3t2y/nexus-base:stable -f old/docker/nexus-base.Dockerfile old/docker/
docker push ghcr.io/i3t2y/nexus-base:stable
```
> 红线: GHCR push 必须你手动做。已在 `:stable` 外验证过 `v2026.8.31-test` build+runtime 全过, 冒 stable tag 只是改名+push 同一镜像层。

### 3b. HF Console 手动 Rebuild (不 git push)
`hermes/space/Dockerfile` 是 `FROM ghcr.io/i3t2y/nexus-base:stable` 墓碑, **永远不用动 / 不 commit / 不 push**。
`README.md` 是纯 HF frontmatter 不入版本 — 不存在"README 触发 rebuild"机制。
正确触发: **Hugging Face → sonoke/h Space → Settings → Rebuild**(手动点)。HF 拉新 `:stable`,`FROM` 层缓存刷新, 内核升级生效。
> ⚠️ 红线: Rebuild 必须你手动点, 我不代触发。

### 3c. 验证
- GET `/v1/health` → `{"status":"ok","platform":"hermes-agent"}` (确认 api_server 起来)
- dashboard `--skip-build` 用缓存 dist, BasicAuth 正常
- GLM 链路 (provider=xnexus, base_url=https://omn.360710.xyz/v1, model=dp4f-pool) 实测一轮对话

---

## 4. 回滚

若异常, 在 HF Dockerfile `ARG BASE_IMAGE` 或本地重推 `:stable` 指回 v2026.8.18 旧镜像 + Restart 即回退。逻辑层在 Bucket, 不受镜像升级影响。