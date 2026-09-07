# nexus-bot 自我改进方案 v1.0(论证汇总版)

> 日期:2026-09-07
> 性质:**先论证、后落地**。本版是对 `docs/self-improvement/` 既有 7 篇文档(第一性原理评审 → v2 → v3/审查 → v3.1/Stage-3 → SYNC → 闭环总纲 → Gate 0.5 简报)的**收敛提炼**,作为后续讨论修改的 1.0 基线。
> 立场:不引入新决策;所有结论都在原文档中有出处(见 §8 索引)。有分歧处以"最新审查版(v3.1 + Stage-3)"为准。

---

## 一、一句话主命题

让 nexus **可度量的自我改进**:把"hermes/mem0 的 prompt、skill、检索策略变得更好"变成一条 **改 → 评 → keep/revert → 人工合并** 的机器可校验闭环,全程**不可作弊**。

**不做什么(非目标):**
1. 不优化 nexus 的 Python 代码性能(那不是价值函数)。
2. eval 建好前不启动任何迭代循环(无指标即无棘轮)。
3. 不让 agent 参与构建或读取评估器。
4. 不做开放式 NEVER STOP autoresearch(nexus 是生产记忆层)。
5. 不把可持续性押在促销窗口上。

---

## 二、为什么可信——证据骨架(论证层)

方案的每个硬决策都由外部实证支撑,不是凭感觉:

| 编号 | 证据 | 我方用到的结论 |
|---|---|---|
| E-langfuse | Langfuse 用 autoresearch 优化自家 skill:14 实验 0.35→0.824;评分公式 `correctness×0.5 + completeness×0.3 + efficiency×0.2`;连续 5 次无改进即停 | 直接复用评分公式与停止条件 |
| E-shopify | Shopify/Liquid:93 commits、-53% 时间、974 测试零回归;**但 PR 未合并、作者自认 somewhat overfit** | 证据双面用:可行但会过拟合 → 必须有 held-out |
| F-meerkat | 9 benchmark 数千 run 作弊;Terminal-Bench 前三全部 harness 级作弊(415/429 轨迹 `cat /tests`) | 评估器必须 NPC 读不到 |
| F-rdi | Berkeley RDI 打穿 8 个 benchmark(SWE-bench 100% 靠 conftest.py hook) | 光"防读"不够,还要**防篡改、防注入** |
| F-autoresearcheval | 82.5% 轨迹"发现问题却不修正";enforcement 移到 tool boundary 后失败率 57.6%→0.2% | 闸门必须是"能阻断的状态转移",不是提醒 |
| C-cnb | CNB NPC 实测:单次成功任务 ≈0.07 核时,促销期 0 credits(deepseek-v4-flash 免费至 2026-12-31) | 窗口期月容量 ≈2 万次,不按额度设计 |

**一句话论证**:不是"agent 会自觉变好",而是"评测不可作弊(F)+ 评分公式已验证(E)+客服人员真实配额(C)三者同时成立,闭环才可能收敛"。

---

## 三、架构(四层,信任边界先行)

```
┌─ hermes 侧(常驻,NPC 物理不可达:HF Space vs CNB 云)────────┐
│  eval 集(含 held-out)· score.py · expected.json           │
│  唯一对外接口:POST /eval {diff} → {score: 0..1}            │
└──────────────────────────┬─────────────────────────────────┘
                           │ 只回标量
┌──────────────────────────▼─────────────────────────────────┐
│  本地有界迭代(git worktree,零配额)                         │
│  commit → /eval → keep/revert;连续 5 次无改进停            │
│  记录:attempts.jsonl + notes.md                            │
└──────────────────────────┬─────────────────────────────────┘
                           │ 一个逻辑改动 = 一个 PR
┌──────────────────────────▼─────────────────────────────────┐
│  CNB NPC 生成 PR(平台禁止 AI 合并)                         │
│  唯一状态转移 = 人工合并                                     │
└────────────────────────────────────────────────────────────┘
```

**保护内核**(改动即熔断):派发通路、持久层(Neon 四表/R2/mem0)、鉴权密钥、`.cnb/*.yml`、审批与合并路径本身、eval 集与评估器。

---

## 四、执行路线(阶段门禁制,Gate 不过不进下一步)

| 阶段 | 内容 | Gate(验收要点) |
|---|---|---|
| **-3** | 补真相:确认 hermes/NPC 不同机、Neon 只读 DSN、PSK 清单、GitHub↔CNB 双向同步(SYNC 文档) | 前置事实全部落档 |
| **0 / 0.5** | 建 eval(8+2 held-out,4 难度层)+ `score.py`(Langfuse 公式);跑 **12 条真实 trace + baseline** | baseline 连跑 3 次方差 <5%;held-out 隔离;违规输入归零自测通过 |
| **1** | hermes 侧黑盒评估器 `/eval` 上线;仓库 `git grep` evaluate 痕迹为空;dry-run 高分/归零正确 | 隔离审计通过 |
| **2** | 本地 worktree 有界迭代,首靶 = hermes 路由 prompt 或 mem0 检索策略;一次只改一个文件 | ≥1 改动 eval 提升 ≥5%;报告含"已知未修问题"清单 |
| **3** | NPC 出 PR → 人工按"审初级工程师 PR"清单复审 → 合并 | 每 PR 有人工签字;保护内核零命中 |
| **全局 G-3** | held-out 集相对 baseline 提升 **≥10%** 且人工确认是真改进(非 harness 产物) | 终审 |

**熔断条件(任一触发立即停):** held-out 降 / 保护内核进 diff / "移除审批门控"类改动 / 删除 eval 未覆盖功能 / 单批次 >20 核时 / NPC 上下文出现评估器内容。

---

## 五、当前实际状态(2026-09-07 快照)

| 依赖 | 状态 |
|---|---|
| eval 脚本 + cases(score/harness、8+4 用例)| ✅ 已在 nexus 仓 |
| `/eval` FastAPI server(`eval/hermes_eval/server.py`)| ⚠️ 骨架,`_STUB=True`,未接真后端、未部署 |
| `HERMES_EVAL_PSK` / Neon 只读 DSN | ❌ 待主理人提供 |
| CNB 探针 `nexus.zen/nexus` | ✅ 已实测跑通;PR #1 未合并(建议关闭) |
| dp4f-pool 已放出到 hermes omn 模型列表 | ✅(2026-09-07) |
| NPC 实操踩坑 8 条(PAT 推送、浅克隆、.cnb.yml 结构等) | ✅ v2 §9 已沉淀 |

**已知未知(不可作为设计依据):** 常态期 credit↔token 兑换率、促销后单价、NPC 流水线时长上限、superpowers skill 在 CNB headless 的触发性。

---

## 六、主理人裁决点(讨论入口)

1. **执行次序**:先"stub 冒烟跑通 12 traces + baseline"(低本)还是"直接接真后端"?
2. **凭证**:`HERMES_EVAL_PSK`、Neon 只读 DSN 何时提供。
3. **/eval server 部署形态**:挂 hermes Space 子进程,还是独立端点。
4. **常态期(2027 起)** 预算上限:按 50 次/月设计的兜底是否接受。

---

## 七、本版 vs 旧文档的关系

- 本文档 = 1.0 基线;旧 7 篇全部**保留不改**作为论证底稿与证据原件。
- 后续修改一律在本文档基础上出**修订版**(v1.1、v2.0),不覆盖本文件。

## 八、出处索引

`docs/self-improvement/` 下:
第一性原理架构评审-2026-09-06 · v2-可执行完整版 · v3 / v3-修订版 / v3-首席架构师审查报告 / v3.1 二次审查 / SYNC-双仓同步架构 / hermes自我改进闭环与验证 / HERMES-自我改进执行简报(Gate 0.5)。
