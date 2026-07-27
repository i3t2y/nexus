# Space 间通信方案

## 背景

4 个 HF Space 需互相调用。基于官方文档查证后选定方案。

## 官方事实（2026-07 查证）

1. **Space URL 格式**：`https://{owner}-{repo}.hf.space`。例 `osanseviero/i-like-flan` → `https://osanseviero-i-like-flan.hf.space`。
2. **免费 Space 会休眠**：CPU Basic / ZeroGPU 闲置后睡，首调冷启动慢（数十秒）。手动暂停不计费。付费硬件可常驻。
3. **出站端口**：仅 80 / 443 / 8080 放行，其余被挡。
4. **可见性**：Private Space 的 embed URL 仅 owner/collaborators 可访问，外部 404。需带 HF token。
5. **持久存储**：Space 内 `/data` 已下线，跨重启持久必须外置（R2/Supabase）。

## 三个候选

| 方案 | 实时性 | 鉴权 | 冷启动规避 | 复杂度 | 成本 |
|------|--------|------|-----------|--------|------|
| A 直调 hf.space | 高 | 需自加 | 无 | 低 | 免费 |
| B Cloudflare Worker 网关 | 高 | 内置 | 可探测预热 | 中 | 免费额度够 |
| C Supabase Queue 轮询 | 低（秒级延迟） | 表权限 | 无 | 中 | 免费 |

## 决策：B 作为主方案，A 作为回退

### 选 B 的理由

- **鉴权统一**：Worker 校验 `NEXUS_API_KEY`，Space 自身不暴露无鉴权接口 → 风控面最小。
- **冷启动缓解**：Worker 可周期性探测下游 Space，唤醒休眠实例（keep-alive）。
- **路由集中**：Hermes 调 Worker，Worker 按 `space` 参数转发，Space URL/owner 变动只改 Worker 配置。
- **可加缓存/限流**：Worker 层免费加 retry、超时、限流，Space 代码更薄。
- **免费额度**：Cloudflare Workers 免费档 10 万请求/天，本系统调用频次远低于此。

### A 作回退的理由

Worker 故障时直调保底。Hermes 内置 Space URL 列表，Worker 不可达则降级直调（自加 header 鉴权）。

### 何时考虑 C

任务可异步、能容忍秒级延迟、需削峰填谷时（批量代码审查等）。当前主流程是请求-响应，C 作未来扩展位。

## 调用契约

所有调用走 JSON POST，Header：
```
Authorization: Bearer <NEXUS_API_KEY>
Content-Type: application/json
```

### 经 Worker（主）

```
POST https://nexus-gateway.<your-workers-dev>.workers.dev/route
Body: { "space": "langgraph", "path": "/execute", "task": {...} }
```

Worker 转发到 `https://{owner}-langgraph.hf.space/execute`，透传 Body 与鉴权 header。

### 直调（回退）

```
POST https://{owner}-langgraph.hf.space/execute
Authorization: Bearer <NEXUS_API_KEY>
Body: { "task": {...} }
```

## 超时与重试

- 客户端总超时 90s（含可能冷启动）。
- Worker 转发超时 60s。
- 5xx / 超时重试 2 次，指数退避。
- 下游 Space 自身处理超 60s 的任务应改异步：收 task → 入 Supabase `task_logs` → 返回 task_id → 轮询状态。

## 未决项（凭证就位后定）

- [ ] Worker 域名（取决于 Cloudflare 账号）
- [ ] 各 Space owner（取决于 HF 账号）
- [ ] `NEXUS_API_KEY` 生成与分发
- [ ] keep-alive 探测频率（避免过度占用免费额度）

## 参考

- HF Spaces Overview（休眠/可见性/网络）：https://huggingface.co/docs/hub/spaces-overview
- Docker Spaces（端口/secrets）：https://huggingface.co/docs/hub/spaces-sdks-docker
- Spaces Config Reference：https://huggingface.co/docs/hub/spaces-config-reference
