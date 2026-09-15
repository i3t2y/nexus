# nexus 自我改进闭环 —— 二次审查与优化报告（v3.1）

> 审查对象：`nexus自我改进方案-v3-修订版-2026-09-06.md` + `nexus自我改进方案-v3-首席架构师审查报告-2026-09-06.md`
> 日期：2026-09-06（本轮）
> 审查者：首席架构师视角（第一性原理）
> 性质：在 v3 已闭环 P1–P15 的基础上，复核**文档与现实的一致性**、**契约完整性**与**事实新鲜度**，并纳入本轮已落地的代码（M0 eval 脚手架 + M1 /eval 服务端）反推修正。

---

## 〇、总体结论（结论先给）

v3 的**架构方向、三层信任边界、四道门禁、风险登记**全部成立，且本轮已把 v3 规划的代码骨架（M0 + M1 stub）真实写出来并跑通端到端，**证明方案可执行**。

但 v3 文档存在三类需修补的问题：
1. **文档↔代码契约漂移**（N1–N4）：v3 只定义了 `/eval` 单例接口，而 `loop.py` 实际依赖 `/eval/batch`（含 heldout 字段）；且单轮 keep/revert 决策与 G-3 终验阈值混用。
2. **关键占位未填 / 口径未定义**（N5–N6）：Gate 0.5 的"<X 分钟"、heldout 测量口径、Gate 1 grep 扫描范围。
3. **事实又过时 + 状态脱节**（N7–N8）：superpowers 版本号 v5.1.0 已非最新（现 v6.3.0 / ~280k stars）；里程碑 M0/M1 在 v3 里仍标"待做"，现实已大部分完成。

**严重度分布**：🔴 3（N1 决策语义漂移、N4 /eval 契约缺口、N8 状态脱节）｜🟠 5（N2/N3/N5/N6/N9）｜🟡 4（N7/N10/N11/N12）。无架构级矛盾，均为"可落地性 + 自洽性"补强。

---

## 一、本轮事实查证结果（一手为主）

| # | 查证项 | 结论 | 状态 | 依据 |
|---|---|---|---|---|
| V1 | CNB 定价：构建 160 核时/月、开发 1600 核时/月、AI Credits 500/月（¥0.05/credit）、GPU 0 免费、每 5min 预冻结、100GiB×2 存储 | 与 v2/v3 完全一致 | ✅ 一手 | 本轮直连 `docs.cnb.build/zh/pricing.html` 逐字核对 |
| V2 | AutoResearchEval：800 轨迹 / 100 任务 / 7 领域 / 8 组合；F.4 未纠正自我觉察 82.5%；D.4 77.5% / E.2 78.1% / C.3 72.1% / C.1 69.0% / A.5 68.1% | 真实且数值吻合 | ✅ 一手 | arXiv:2608.14905 |
| V3 | superpowers 当前版本 | **v6.3.0（2026-08-12），~280k stars** | 🔶 多源交叉 | repositorystats（280,692）/ 多镜像（38.7k–242k 噪声大，取 repositorystats 为准） |
| V4 | tool-boundary 57.6%→0.2% | 仅 worldprogramming.org 二手转引 | 🔶 待一手 | 维持 v3 已降级结论，不升格 |
| V5 | @CodeBuddy + DeepSeek-V4-Flash 免费至 2026-12-31 | 用户已确认（A1 窗口期适用） | ✅ | 对话确认 |

**关键修正**：v3 审查报告 P14 声称"已修复为 v5.1.0 / ~249k stars"——本轮查得现 v6.3.0 / ~280k，**修复声明本身又过时**。结论：star 数/版本号属高速变动量，文档不应写死，应改为"以核查日 GitHub/README 为准 + 标注扫描日"。

---

## 二、问题清单（注明位置与严重度）

| ID | 严重度 | 位置（v3 修订版） | 问题 |
|---|---|---|---|
| **N1** | 🔴 | §4 阶段2 动作2 / §1.2 G-3 | **决策语义漂移**：阶段2 写"升则 keep、降/崩则 revert"（逐 diff 软门槛），而 `loop.py` 对**每个单独 diff** 套用 G-3 硬门槛（heldout +0.05 绝对 + 零回归）。结果：在迭代早期几乎每个增量 diff 都被 REVERT，与"累积小改进"的 autoresearch 范式相悖。 |
| **N2** | 🟠 | §3.3 | **/eval 契约不全**：只定义 `POST /eval {repo,base_ref,diff_patch,target}→{score,breakdown,kept}`，但 `loop.py` 实际调 `POST /eval/batch` 并依赖 `heldout{before,after}`、`baseline_scores`。文档缺 batch 契约 → 后人无法实现服务端。 |
| **N3** | 🟠 | §3.3 / Gate 1 | **heldout 测量口径缺失**：heldout 应在"同一快照、按 candidate 配置重跑"，其 before/after 由服务端返回；文档未写明，导致 loop.py 离线分支自行重算（无意义）。 |
| **N4** | 🔴 | §3.3 / §4 | **门禁阈值关系不清**：Gate 2 用"eval 加权均分 ≥+0.03"推进，G-3 用"held-out +0.05 绝对"。两者都是"改进"判定却阈值不同、对象不同，未说明前者是**迭代推进门禁**、后者是**批次终验**。 |
| **N5** | 🟠 | §4 阶段 0.5 | **Gate 0.5 "<X 分钟"占位未填**：可复现性要求空悬，无法验收。 |
| **N6** | 🟡 | §3.3 Gate 1 | **grep 锚点未限扫描行**：`eval_cases/|/expected\.json$|/score\.py$` 若扫全文会误命中注释/字符串；实现里已限定只扫 `+++ / --- / diff --git` 头行，文档需补明以免 NPC 侧实现走偏。 |
| **N7** | 🟡 | 开篇修复清单 / §2.5 | **superpowers 版本又过时**：写死 v5.1.0 / ~249k，现实 v6.3.0 / ~280k。 |
| **N8** | 🔴 | §十 里程碑 M0/M1 | **文档状态与现实脱节**：M0（score/harness/loop）+ M1（/eval server stub）本轮已落地；8 例 cases + 4 例 heldout 已写好；PR #1 已关闭。v3 仍标"人天并将做"。剩余真缺口（7 例+4 heldout 真 trace、接 hermes real 后端、Gate 0 n=5 冻结快照）未显性列出。 |
| **N9** | 🟠 | §7.5 监控 | **attempts R2 快照仅打印 rclone 命令（stub）**：方案说"每日 R2 快照"但无自动化机制，易因遗忘丢失。 |
| **N10** | 🟡 | §4 阶段2 | **停止条件无自动计数器**：loop.py 当前单批次运行，未实现"连续 5 次无改进即停"的自动循环；文档应明确"每批手动驱动 / 或加 --auto 模式"。 |
| **N11** | 🟡 | §3.5 / M0 | **trace 录制未闭环**：Gate 0 要求 n=5 baseline，但目前仅 easy-01 有录制 trace；其余 7 例 + 4 heldout 缺真 trace。需给 harness 加 online→record 落盘路径。 |
| **N12** | 🟡 | 方法论 | **基建自测缺 smoke 项**：本轮实战抓到 `harness.py --offline` 参数反向 bug（已修）。教训：Gate 0.5 应强制"`score.py --self-test` + `loop.py --dry` 必须 PASS"作为 CI/预检。 |

---

## 三、逐项修改建议（理由 + 预期效果）

| ID | 修改 | 理由 | 预期效果 |
|---|---|---|---|
| N1 | 引入**双层决策**：① 迭代层（每 diff）= `cand_med > baseline_med 且无回归 → keep`；② 批次终验（G-3）= heldout +0.05 绝对 + 零回归。改 `loop.py` 的 `decide()` 为迭代层语义，G-3 在批次末单独判定。 | 单轮套 G-3 会扼杀累积改进；autoresearch 范式靠"小步 keep + 终验把关" | 既保留高频迭代，又守住终验硬门槛 |
| N2 | v3.1 补 `POST /eval/batch` 契约：`{repo,base_ref,diff_patch}→{scores,baseline_scores,dataset,heldout:{before,after},kept}`。本轮服务端已实现，文档对齐 | 文档是他人落地的唯一依据 | 消除 doc↔code 漂移 |
| N3 | 明确 heldout 由 `/eval/batch` 在同快照、按 candidate 配置重跑并返回 before/after；loop.py 离线分支**不再**自算 heldout | heldout 必须在服务端隔离环境测，本地无快照 | 口径唯一、防作弊 |
| N4 | 在 §1.2 与 §7 显式标注：Gate 2（+0.03，训练集，推进门禁）/ G-3（+0.05，held-out，终验） | 避免后人误用单一阈值 | 门禁语义无歧义 |
| N5 | Gate 0.5 量化：全量 8+4 例 × K=3 ≈ **<10 min（real，经 n-omn）/ <1 min（stub）** | 给可验收的数字 | 可复现性可测 |
| N6 | Gate 1 grep 明确"仅扫描 diff 头行（+++ / --- / diff --git）" | 防误命中 | NPC 侧实现一致 |
| N7 | superpowers 改为"以核查日为准 + 标注扫描日"，不写死版本号 | star 数高速变动 | 不再过期 |
| N8 | §十 里程碑改写为真实状态表（见下"里程碑现状"），把剩余缺口单列 | 文档即真相 | 状态自洽 |
| N9 | 给一个**自动化快照**方案：WorkBuddy 每日 automation 或 Windows 计划任务跑 `rclone copy ~/.nexus-eval/attempts ...` | 防遗忘丢失 | R2 快照真正生效 |
| N10 | `loop.py` 加 `--auto`（连续 5 次无改进即停）；文档标注默认手动批次 | 落实 E11 停止条件 | 自动收敛 |
| N11 | `harness.py` 加 `record` 子命令：online 回放后把 trace 落 `sample_traces/<case>.json` | 闭环 trace 录制 | Gate 0 n=5 可行 |
| N12 | Gate 0.5 预检清单加入 `score.py --self-test` 与 `loop.py --dry` 必须 PASS | 防基建回归 | 类似 --offline bug 不再漏过 |

---

## 四、里程碑现状（替换 v3 §十 待办）

| 里程碑 | v3 原状态 | 本轮真实状态 | 剩余缺口 |
|---|---|---|---|
| **M0** eval 8 例 + score + harness | 待做 | ✅ 已完成（score.py / eval_cases.json / heldout.json / harness.py / loop.py 均落地并验证） | 仅 easy-01 有真 trace；需录 7+4 例（N11） |
| **M0.5** Gate 0 | 待做 | 🟡 评分器可跑✅；**全量 trace + n=5 冻结快照未做** | 录 trace + 冻结 hermes memory 快照 |
| **M1** /eval 接口 + PSK | 待做 | ✅ stub 完成（FastAPI + PSK + Gate1 + dry-run；HTTP 冒烟通过） | 接 hermes real 后端（core.replay 适配） |
| **M2** 本地有界迭代 | 部分 | 🟡 loop.py 可跑 dry/online；缺 --auto（N10） | 加自动停止 |
| **M3** CNB PR + 人工合并 | — | PR #1 已关闭（探针仓清理） | 真实改进 PR 待 M0.5 后 |
| **R1** 2026-11 到期复查 | 待 | 待 | 同前 |

**资源实测**：M0+M1 stub 实际耗时远小于 v3 估的"人 1.5+1 天"——证明方案低估了可执行性，是正向偏差。

---

## 五、修订后方案版本

完整修订见 `nexus自我改进方案-v3.1-修订版-2026-09-06.md`（已并入 N1–N12 全部修正 + 本轮代码现状）。

---

## 六、查证依据索引

- `docs.cnb.build/zh/pricing.html`（本轮直连，逐字核对 C1–C10）
- arXiv:2608.14905（AutoResearchEval，V2）
- `github.com/obra/superpowers` + repositorystats.com（V3，~280k stars / v6.3.0）
- worldprogramming.org（V4，🔶 二手，维持降级）
- 本轮代码产物：`eval/score.py` `eval/harness.py` `eval/loop.py` `eval/hermes_eval/server.py`（M0/M1 实证）
