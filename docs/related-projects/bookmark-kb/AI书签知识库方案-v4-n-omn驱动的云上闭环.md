# AI 书签知识库方案 v4：n-omn 驱动的云上闭环（终版）

> 日期：2026-09-06 ｜ 基于 v3 + 用户方案「EverOS + HF + 保活 + n-omn 持久化 + n-omn 自建模型调用」
> 核实来源：`i3t2y/n-omn` 仓库 README、`docs/HANDOFF.md`（SSOT）、EverOS README、HF Spaces 官方硬件/存储文档

---

## 0. 直接结论

**你的思路是对的，而且比我 v3 给的"无状态架构"更优雅。它把三个冲突解决了两个半：**

| v3 提出的冲突 | 你的解法 | 判定 |
|---|---|---|
| 休眠（48h 自动挂起） | **保活**（外部 cron ping） | ✅ 解决（但 n-omn 里没有现成的，需自建） |
| LLM 成本 / 推理额度天花板 | **n-omn OmniRoute 聚合 32 路 NIM key + 自定义节点** | ✅ **漂亮地解决**，这是方案里最有价值的部分 |
| 持久化（ephemeral 磁盘） | **litestream → R2** | ⚠️ **只解决 1/3**——见第 2 节 |

**差的那一口气，恰恰是最不该丢的那部分：Markdown 真相源。**

补上两处（`.md` 走 rclone + LanceDB 存快照）之后，我认为**可以称得上"完美闭环"**——代价只剩冷启动与上游模型池稳定性两处妥协。

---

## 1. n-omn 是什么（核实结论）

| 项 | 内容 |
|---|---|
| 仓库 | `i3t2y/n-omn`（你的项目） |
| 本质 | **OmniRoute 3.8.50** —— OpenAI 兼容的 API 网关 / 智能模型路由层 |
| 部署 | HF Space `xnexus/o`（私有）+ **CF Pages `ho-proxy`** 前置 → `https://omn.360710.xyz/v1`（PSK 鉴权） |
| 内部 | `gate`（logic/，Bearer PSK → 20128）→ OmniRoute → 上游 |
| 上游 | **NVIDIA NIM 池（32 路 key 轮询）** + 自定义 OpenAI 兼容节点（sensenova / amd） |
| 附加 | FlareTunnel（10 CF 账号 × N Worker）做出网 IP 层；Health Autopilot 自清错态；429 处理；N-key round-robin |
| 持久化 | **litestream → R2 桶 `omn-data`，path `db/storage.sqlite`，sync-interval=10s** |
| 文档纪律 | HANDOFF（SSOT①）→ STATUS → DECISIONS；Restart ≠ Rebuild |

### 官方原文（关于持久化边界）

> "Litestream 仅复制 **SQLite 数据库文件**（`storage.sqlite`），**不是任意文件**。"

> "replicating to type=s3 sync-interval=10s bucket=omn-data path=db/storage.sqlite"

> "Restart 非 Rebuild（零数据清零）"

---

## 2. ⚠️ 唯一要补的洞：litestream 的精确边界

**litestream 只覆盖 SQLite。而 EverOS 在 HF Space 上有三类数据：**

| 数据 | 作用 | litestream 覆盖 | 丢失后果 | 补法 |
|---|---|---|---|---|
| **`.md` 记忆本体** | **唯一事实来源** | ❌ **盲区** | 🔴 **灾难性**——记忆全没了 | **rclone 双向同步 → R2 `md/`** |
| `system.db` | 状态 / 队列 / 审计 | ✅ 覆盖（10s 同步） | 丢队列与审计 | 无需补（n-omn 已解决） |
| `lancedb/` | 向量 + BM25 索引 | ❌ 盲区 | 🟡 只是慢——可从 md 重建 | **R2 `lance/` 存快照**，避免重 embed |

### 为什么 `.md` 这个洞必须补

EverOS 的设计哲学是 **"Markdown 是唯一事实来源"**。丢 SQLite 只是丢状态，丢 `.md` 是**丢记忆本身**。n-omn 的 litestream 恰好只保住了次要的那一个。

### 补法（三选一，推荐 A）

**A. rclone 双向同步（推荐）**
```bash
# 每 60s 把 memory root 的 .md 双向同步到 R2
rclone sync /data/memory r2:omn-data/md/ \
  --exclude '.index/**' --exclude '.tmp/**' \
  --fast-list --transfers 8
```

**B. 真相留在本地，云端只做索引副本**
本地 Obsidian vault 才是真相，单向推到 Space。Space 重启后从本地/云存储重新拉。**最简单，且符合"本地优先"哲学。**

**C. 定时快照整个 memory root 到 R2**（含 LanceDB）
启动时先拉快照，再增量同步。冷启动最快。

---

## 3. 最优架构（把你的方案补完整）

```
┌─────────────── 真相层 ───────────────┐
│  Obsidian vault（本地，你独占写）      │
│  PARA + Zettelkasten + 99-Memory/    │
└────────────┬─────────────────────────┘
             │ 坚果云 Nutstore Sync（双向增量）
             ▼
┌─────────────── 持久化层 ─────────────┐
│  R2 omn-data（n-omn 已有桶）          │
│   ├── md/     ← rclone 双向同步【补】 │
│   ├── db/     ← litestream 10s ✅    │
│   └── lance/  ← 快照，省重 embed【补】│
└────────────┬─────────────────────────┘
             │ 启动时恢复
             ▼
┌─────────────── 计算层 ───────────────┐
│  HF Space：EverOS                     │
│  · memory root → /data/memory         │
│  · cascade watcher：改 .md 秒级重索引 │
│  · OME 离线引擎：闲时抽取事实/画像/技能│
│  · 保活：外部 cron 每 5 分钟 ping【补】│
└────────────┬─────────────────────────┘
             │ EVEROS_LLM__BASE_URL
             ▼
┌─────────────── 模型层 ───────────────┐
│  OmniRoute via n-omn                  │
│  https://omn.360710.xyz/v1 （PSK）    │
│  → NIM 池 32 key 轮询 + 自定义节点    │
│  → Health Autopilot / 429 / fallback  │
└────────────┬─────────────────────────┘
             │ REST API / MCP
             ▼
┌─────────────── 消费层 ───────────────┐
│  Agent：Claude Code / Codex / hermes  │
│  recall（混合检索带溯源）              │
│  remember（写回落 .md → 回 Obsidian） │
└──────────────────────────────────────┘
                    ⟲ 闭环
```

---

## 4. 接入配置（漂亮的地方：零适配代码）

**OmniRoute 是 OpenAI 兼容的，EverOS 也是 OpenAI 协议兼容的——直接对接，改几行配置即可。**

```bash
# everos.toml / .env
EVEROS_LLM__BASE_URL=https://omn.360710.xyz/v1
EVEROS_LLM__API_KEY=<你的 PSK>
EVEROS_LLM__MODEL=<OmniRoute 池里的 chat 模型>

EVEROS_EMBEDDING__BASE_URL=https://omn.360710.xyz/v1
EVEROS_EMBEDDING__API_KEY=<你的 PSK>
EVEROS_EMBEDDING__MODEL=<OmniRoute 池里的 embedding 模型>
```

> ⚠️ **务必确认 OmniRoute 池里有 embedding 模型**（不只是 chat 模型）。EverOS 没有 embedding 就只剩关键词检索，语义检索会失效。NIM 目录里有 nemotron 系列 embedding，你的池应该能覆盖，但要实测验证。

---

## 5. 三处【补】的具体实现

### 补 1：保活（n-omn 里没有现成的）

文档里的 `omniroute-keepalive` 是**网关对上游的长连接保活**（防 30s 错包切），**不是**防 HF Space 休眠。需要自建：

**方案：CF Worker Cron 定时 ping**（你已经有 CF 账号和 Worker 基础设施）
```toml
# wrangler.toml
[triggers]
crons = ["*/5 * * * *"]
```
```js
export default {
  async scheduled(e, env) {
    await fetch(`https://omn.360710.xyz/health`, {
      headers: { Authorization: `Bearer ${env.PSK}` }
    });
  }
};
```
> 注意：ping 的是 **EverOS 所在的 Space**，不一定和 n-omn 同 Space。若 EverOS 单独开 Space，ping 那个 Space 的 health。

### 补 2：`.md` 持久化（rclone → R2）

在 Space 的 `entrypoint.sh` 里加：
```bash
# 启动：从 R2 恢复 md
rclone sync r2:omn-data/md/ /data/memory --exclude '.index/**'

# 后台：每 60s 回推
while true; do
  sleep 60
  rclone sync /data/memory r2:omn-data/md/ \
    --exclude '.index/**' --exclude '.tmp/**'
done &
```

### 补 3：LanceDB 快照（可选，但强烈建议）

```bash
# 优雅关闭 / 定时把 .lance 推到 R2，避免重启后重 embed 整个 vault
rclone sync /data/memory/.index/lancedb r2:omn-data/lance/ --fast-list
```
> 不存也能跑（EverOS 索引可从 md 重建），但 vault 大时重 embed 很慢且吃 embedding 额度。**存快照 = 冷启动从"重算"降为"下载"。**

---

## 6. 仍然存在的妥协（诚实清单）

| # | 妥协 | 影响 | 缓解 |
|---|---|---|---|
| 1 | **私有 Space 成本** | README 标注 `xnexus/o` 为私有 Space —— HF 私有 Space 通常需 **PRO（$9/月）** | 确认你的 n-omn 是否已付/有额度。若已付，边际成本为 0 |
| 2 | **上游模型池不稳定** | HANDOFF 自己记录：已剔除 `gpt-oss-120b`、`llama-3.3-70b`（上游挂/极慢）、`deepseek-v4-flash` 等（NVIDIA 目录无） | OmniRoute 的 Health Autopilot + N-key 轮询已在兜底；但 EverOS 的记忆抽取是持续依赖，上游抖动会直接影响记忆质量 |
| 3 | **冷启动** | 保活能避免休眠，但 Space **rebuild**（代码更新触发）仍会冷启动，此时要拉 md + 恢复 db + 拉 lance 快照 | 有 lance 快照时主要是下载时间 |
| 4 | **rclone 双向同步的冲突风险** | 若 Space 在写、本地也在写，rclone sync 可能覆盖 | 靠**单一写入者原则**（见 v3）：`99-Memory/` 只有 EverOS 写，人写区只有你写 |
| 5 | **EverOS 仍是早期项目** | 2026-06 首发（v1.2.3），API 有 v1/v2 混用痕迹 | 锁定版本，关注 CHANGELOG |
| 6 | **MCP 需自包** | `evermemos-mcp` 默认连 EverMemOS Cloud | 用 EverOS 自带 REST API 自包一层薄 MCP |

---

## 7. 判定：这算"完美闭环"吗？

**补完上述三处后：是的，我认为可以称为完美闭环。** 理由：

| 维度 | 状态 |
|---|---|
| 持久化 | ✅ 三类数据全覆盖（md 靠 rclone，db 靠 litestream，lance 靠快照） |
| 常驻 | ✅ 保活 cron 解决 48h 休眠 |
| 模型成本 | ✅ OmniRoute 聚合多 key，绕开单一免费额度天花板，且零适配代码 |
| 隐私 | ✅ 私有 Space + PSK 网关 + 私有 R2 桶 |
| 闭环 | ✅ 人写 → 索引 → Agent 检索 → 写回 .md → 回到 Obsidian |
| 零冲突 | ✅ 单一写入者原则（人写区 / AI 写区隔离） |
| 零成本 | ⚠️ 边际成本为 0，但**私有 Space 可能需 PRO $9/月**（待你确认） |

**唯一的实质性妥协：上游 NIM 模型池的动态稳定性**，以及**私有 Space 的 $9/月（若适用）**。这两条都不影响"闭环成立"，只影响"体验的稳定性"和"绝对零成本"。

---

## 8. 落地 Checklist

- [ ] **确认 n-omn 的 Space 是私有且已付费/有额度**（影响零成本结论）
- [ ] **确认 OmniRoute 池里有可用的 embedding 模型**（不只是 chat）
- [ ] 本地先跑通 EverOS + 指向 n-omn 网关（先验证链路，再上 Space）
- [ ] Vault 建好 PARA 结构 + `99-Memory/` 分区
- [ ] Space 里部署 EverOS，memory root → `/data/memory`，端口改 **7860**
- [ ] 配置 litestream 复制 `system.db` → R2 `db/`（复用 n-omn 现成链路）
- [ ] 加 rclone 双向同步 `.md` → R2 `md/`【补】
- [ ] 加 LanceDB 快照 → R2 `lance/`【补】
- [ ] 加 CF Worker Cron 保活，每 5 分钟 ping【补】
- [ ] 配置 `EVEROS_*__BASE_URL` 指向 `https://omn.360710.xyz/v1`
- [ ] 跑通 add → flush → search 最小链路
- [ ] 自包 MCP 或直接 REST 接入 Agent
- [ ] 用 HANDOFF 纪律记录这套新部署（沿用你的 SSOT 体系）

---

## 9. 一句话总结

> **你的方案成立，且比 v3 的"无状态架构"更优。**
> n-omn 的 OmniRoute 解决了模型成本（零适配代码，OpenAI 协议直接对接），litestream 解决了 SQLite 持久化，保活解决休眠。
> **唯一要补的是 litestream 的盲区——它只复制 SQLite，而 EverOS 的 `.md` 真相源和 LanceDB 索引不在覆盖内。**
> 补上 `rclone → R2 md/` + `LanceDB 快照` + `CF Cron 保活` 这三处后，这就是一个**持久化完整、成本近零、零冲突、真正闭环**的云上第二大脑。
