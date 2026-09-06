# nexus 自我改进闭环 —— 可执行完整方案 v3.1（二次修订）

> 版本：v3.1（在 v3 已闭环 P1–P15 基础上，纳入本轮二次审查 N1–N12 修正）
> 日期：2026-09-06
> 性质：可直接执行。已纳入 M0/M1 代码实证（eval/ 脚手架 + hermes_eval 服务端跑通端到端）。

---

## 〇、相对 v3 改了什么

| # | v3 问题 | v3.1 修正 |
|---|---|---|
| N1 | 单 diff 决策套 G-3 硬门槛，扼杀累积改进 | 双层决策：迭代层（每 diff 软门槛 keep）+ 批次终验 G-3（heldout +0.05） |
| N2 | 只定义 `/eval` 单例，缺 `/eval/batch` 契约 | 补双接口契约（loop.py 已依赖） |
| N3 | heldout 测量口径缺失 | 明确 heldout 由服务端同快照重跑返回；本地不重算 |
| N4 | Gate 2(+0.03) 与 G-3(+0.05) 关系不清 | 显式：Gate 2=训练集推进门禁；G-3=held-out 终验 |
| N5 | Gate 0.5 "<X 分钟" 占位 | 量化：real <10min / stub <1min |
| N6 | Gate 1 grep 扫描范围未限 | 仅扫 diff 头行（+++ / --- / diff --git） |
| N7 | superpowers 版本写死又过时 | 改为"以核查日为准 + 标注扫描日" |
| N8 | 里程碑状态与现实脱节 | 重写为真实状态表（M0/M1 已落地） |
| N9 | attempts R2 快照仅 stub | 给自动化快照方案 |
| N10 | 停止条件无自动计数 | loop.py 加 `--auto`（连续 5 次无改进即停） |
| N11 | trace 录制未闭环 | harness 加 `record` 子命令 |
| N12 | 基建无 smoke 预检 | Gate 0.5 强制 `score --self-test` + `loop --dry` PASS |

---

## 一、核心目标与非目标

### 1.1 目标
| 层级 | 目标 | 度量 |
|---|---|---|
| G-1 | 不可作弊的 eval 基线 | eval ≥8 用例/4 层；baseline 连跑 n=5 方差<5% |
| G-2 | hermes 侧黑盒评估器，NPC 不可读写 | nexus 仓 grep 评估器痕迹为空 |
| G-3 | 首靶在 **held-out** 相对 baseline 提升 **≥+0.05 绝对**且**零回归** | held-out 分数 delta |
| G-4 | 保护内核零被改 | diff 审计 |
| G-5 | 常态（2027）每月数十次下仍工作 | 双容量推演通过 |

### 1.2 非目标
同 v3：不优化 Python 性能；不无指标启动循环；不让 agent 构建/读评估器；不做开放式 autosearch；不依赖促销窗口做可持续性。

---

## 二、架构与信任边界

```
[hermes 侧｜NPC 不可达｜PSK 网关后置]
  eval 集(含 held-out) · 评估器 · score.py · 保护内核副本
  POST /eval      {repo,base_ref,diff_patch,target} → {score,breakdown,kept}
  POST /eval/batch {repo,base_ref,diff_patch}
        → {scores, baseline_scores, dataset, heldout:{before,after}, kept}   ← 仅标量
[本地有界迭代｜用户 PC + n-omn 免费池｜驱动器=本地 CodeBuddy 会话]
  改 → commit → 调 /eval/batch → 双层决策 keep/revert → 记 attempts(worktree 外)
[CNB｜NPC 只搬运已验证 diff → 一个逻辑改动=一个 PR｜平台禁止 AI 合并]
  人工复审 → 合并（唯一状态转移点）
```
**三层防作弊论证（v3 已立，维持）**：防读（F2）/ 防篡改（F5）/ 防注入（F3）→ 评估器必须在 NPC 可读写范围外，只回标量。
**闸门（F12）**：review 不能改执行态 = 可观测非控制；有效闸门 = 评估器低分→不进 PR（tool-boundary 阻断）+ PR 未合并→不进 main（平台强制）。

---

## 三、关键定义（修正量化粒度，P3 已解，维持）

- `score = 0.5·correctness + 0.3·completeness + 0.2·efficiency`
- correctness = 非效率断言的加权命中均值；completeness = action/dispatch 断言加权命中均值；efficiency = `max_turns` 断言命中。
- **断言分级**：每条可部分命中（如 `memory_hit` 列表按命中比例给分），使 G-3 的 +0.05 绝对提升数学可分辨。
- `banned_patterns` 命中 → correctness 直接归零（保护内核硬编码，E12 教训）。

---

## 四、实施步骤

### 阶段 0 —— 建 eval（M0，✅ 已完成代码，待录全量 trace）
| 项 | 内容 |
|---|---|
| 产出 | `eval/score.py`（标准库，分级断言，banned 归零）· `eval_cases.json`（8 例）· `heldout.json`（4 例）· `eval/harness.py` · `eval/loop.py` · `eval/README.md` |
| 验证 | `score.py --self-test` 通过（good=1.0 / bad=correctness=0）；`harness.py --offline` 出分；`loop.py --dry` 端到端通过 |
| **剩余** | 仅 easy-01 有录制 trace；需 `harness.py record` 经 hermes /eval 录其余 7 例 + 4 heldout（N11） |

### 阶段 0.5 —— 评估执行器（M1，✅ stub 已完成）
| 项 | 内容 |
|---|---|
| 产出 | `eval/hermes_eval/server.py`（FastAPI：`/eval` `/eval/batch` `/eval/health` `/eval/dryrun` `/eval/gate1`）+ `replay.py` + `spaces.Dockerfile` + `requirements.txt` + `README_deploy.md` |
| 鉴权 | Bearer PSK（n-omn gate 模式）；无 PSK→503；错 PSK→401 |
| Gate 1 | grep 锚定 `eval_cases/\|/expected\.json$\|/score\.py$`，**仅扫 diff 头行**；命中→`kept:false` 不生成 PR |
| 验证（本轮实战） | HTTP 冒烟：health/dryrun(高分1.0/归零 correctness=0)/batch/dryrun/gate1 全绿；`loop.py --eval-url …/eval` 真在线端到端跑通（baseline median=0.5463） |
| **剩余** | 接 hermes real 后端（见 `eval/hermes_eval/hermes_core_adapter.py` 适配骨架）；`HERMES_EVAL_BACKEND=real` |

### 阶段 1 —— hermes 侧黑盒评估器（契约，v3.1 补全）
- `POST /eval {repo,base_ref,diff_patch,target} → {score,breakdown,kept}`
- `POST /eval/batch {repo,base_ref,diff_patch} → {scores, baseline_scores, dataset, heldout:{before,after}, kept}`
  - `baseline_scores` = 同一快照、**无 diff** 时的逐例分（用于回归计数）
  - `heldout.before/after` = 同一快照、按 candidate 配置重跑 held-out 4 例（**服务端算，本地不重算**，N3）
- dry-run 两类（Gate 1 证明）：高分 / 归零(banned)。

### 阶段 2 —— 本地有界迭代（双层决策，N1/N4）
| 项 | 内容 |
|---|---|
| 驱动器 | 本地 CodeBuddy 会话，经 n-omn 免费池（`--propose` 调 `/chat/completions`，凭证走 env）；非 CNB NPC |
| 首靶 | hermes 路由 prompt 或 mem0 检索策略（一次一文件） |
| 迭代层决策（每 diff） | `cand_med > baseline_med 且无回归 → keep`；否则 `revert` |
| 批次终验 G-3 | 该批所有 keep 的 diff 合并后，held-out 加权均分 **≥+0.05 绝对 且 零回归** → 该批可交付 |
| 停止 | `--auto` 模式：连续 5 次无改进即停（E11）；默认手动批次 |
| 记录 | `attempts` 落 `~/.nexus-eval/attempts/<batch>.jsonl`（worktree 外）+ **每日 R2 快照自动化**（N9）+ 追加 `dashboard.md` |

### 阶段 3 —— CNB PR + 人工合并
- NPC 只搬运已验证 diff（一个逻辑改动=一个 PR）；人工按"审初级工程师 PR"标准复审；合并唯一状态转移点，AI 不可合并。
- PR #1（探针）**已关闭**（2026-09-06，非生产清理）。

---

## 五、门禁（修正）

| Gate | 判据 |
|---|---|
| **Gate 0** | ① score.py 一键出 [0,1]；② **冻结 hermes memory 快照下 baseline 跑 n=5（temp=0、固定 seed）方差<5%**；③ held-out 4 例隔离；④ banned 归零自测通过；⑤ 评估器/用例人写、grep 为空 |
| **Gate 0.5** | ① `score.py --self-test` PASS；② `loop.py --dry` PASS（N12 smoke）；③ 全量 eval **real <10min / stub <1min**（N5）；④ n=2 复跑差<2% |
| **Gate 1** | ① grep 锚定（仅 diff 头行）为空；② dry-run 两类正确；③ 违规 diff `kept:false` 不生成 PR |
| **Gate 2（推进）** | 训练集加权均分 **≥+0.03**（软门槛，决定单 diff keep） |
| **Gate 3** | ① 每 PR 人工签字；② 零 AI 合并；③ 保护内核零命中 |

**G-3（终验）**：held-out 加权均分 **≥+0.05 绝对 且 零回归**（硬门槛，批次级）。

---

## 六、风险登记（合并 v2 §六 + v3 §6，新增 N13）

| ID | 风险 | 等级 | 应对 |
|---|---|---|---|
| R1 | 促销窗口到期成本跃升 | 🔴 | 双容量；2026-11 复查演练 |
| R2 | eval 过窄→过拟合 | 🔴 | 4 层 + 4 held-out + 轮换 |
| R3 | 未覆盖功能被删 | 🔴 | 保护内核进 checks |
| R4 | harness 级作弊 | 🔴 | 评估器物理隔离（A5 确认不同机→已消除） |
| R5 | 未纠正自我觉察(82.5%) | 🟠 | 批次报告列已知未修项 + 人工闸门 |
| R6 | 代码质量退化(56.1%) | 🟠 | 复审加可维护性项 |
| R7 | NPC 自我提权 | 🟠 | 保护内核 + 双人确认 |
| **N13** | **文档↔代码漂移**（本轮教训） | 🟠 | Gate 0.5 smoke 强制；契约单源（本方案 §三/§阶段1） |
| R8–R12 | 见 v2 §六 | — | 同 v2 |

---

## 七、监控与回滚（v3 已立，维持 + N9 自动化）

- **监控**：`~/.nexus-eval/dashboard.md` 由 loop.py 自动追加（批次分数趋势、heldout delta、rollback 计数）；异常（heldout 降 / rollback>0）标红。
- **回滚**：attempts 落 worktree 外 + R2 快照（**自动化**：WorkBuddy 每日 automation 或 Windows 计划任务跑 `rclone copy ~/.nexus-eval/attempts <R2目标> --include '*.jsonl'`）。
- **熔断**：eval 升但 heldout 降→作废；保护内核入 diff→停；拆审批→拒；删未测功能→拒；单批核时>20。

---

## 八、里程碑现状（替换 v3 §十）

| 里程碑 | 状态 | 剩余 |
|---|---|---|
| M0 eval 脚手架 | ✅ 完成 | 录全量 trace（N11） |
| M0.5 Gate 0 | 🟡 评分器可跑 | 冻结快照 n=5 + 全量 trace |
| M1 /eval 服务端 | ✅ stub 完成 | 接 hermes real 后端（core.replay 适配） |
| M2 本地迭代 | 🟡 loop 可跑 | `--auto` 停止（N10） |
| M3 CNB PR | — | PR #1 已关闭；真实 PR 待 M0.5 |
| R1 到期复查 | 待 | 2026-11 |

---

## 九、superpowers 纪律层（事实修正，N7）

- obra/superpowers（MIT）。**版本与 star 数高速变动，本方案不写死**；核查日（2026-09-06）参考：~280k stars / v6.3.0（2026-08-12）。
- 自动触发依赖 `SessionStart` hook；CNB headless NPC 无此机制 → 纪律层在本方案为"可选增强"，非闭环必需。
- 可用非交互技能（TDD / verification / debugging / git-worktrees / code-review）可作 loop.py 驱动器参考实现。

---

## 十、下一步

1. **录全量 trace**（N11）：`harness.py record` 经 hermes /eval 录 7+4 例 → 跑 Gate 0 冻结快照 n=5。
2. **接 hermes real 后端**：实现 `hermes_core_adapter.py` 的 `replay()`（见骨架），设 `HERMES_EVAL_BACKEND=real`。
3. **加 `--auto`**（N10）与 **R2 自动快照**（N9）。
4. 2026-11 到期复查演练（R1）。
