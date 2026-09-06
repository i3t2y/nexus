# HERMES 自我改进执行简报（Gate 0.5）

> 用途：把本文件完整交给 hermes（nexus 云大脑），让他独立驱动「Gate 0.5：接真实后端、录 12 条真实 trace、出 baseline」。
> 本文件自包含，不要求你先读完 nexus 其他文档。配套硬约束见仓库根 `CONSTRAINTS.md`（必须注入 hermes 的 role prompt）。

## 0. 你的角色
你是 hermes —— nexus 栈的编排者（云大脑，负责路由 / 调度 / 派发）。在本任务中你不是「被评估的对象」，而是「评估的执行者」：你驱动真实后端、录制 trace、跑评测仪、产出 baseline 数字。

## 1. 当前目标：Gate 0.5
- 把自我改进闭环从「离线录制 trace」升级到「真实后端 trace」。
- 用真实 n-omn + hermes 路由，跑通 12 条真实 trace，产出可复现的 baseline 分数（`score.py` 的 dataset 均值 + 方差）。
- 这是「阶段 0 只读审计」之前的关键一跳；完成后才能谈后续优化（Gate 1+ 才允许 NPC 改配置）。

## 2. 开始之前，你必须向用户拿到的 3 样东西（仓库里没有）
1. **n-omn PSK**：OmniRoute 网关入口 `https://omn.360710.xyz/v1`，需要 Pre-Shared Key。没有它你调不通模型池。
2. **hermes 路由 prompt / 配置**：确认过的 hermes 调度与路由设定（已核实不在 nexus 仓内）。
3.（可选）**Neon DSN**：若需冻结 hermes memory 快照做可复现基线。

> 这三样是密钥/配置，绝不入库、绝不进 git。拿到后只在运行时环境变量使用。

## 3. 后端怎么接
- 模型调用统一走 n-omn 免费池（0 成本），温度=0、固定 seed，baseline 跑 n=5 取方差<5%。
- hermes 评估端点约定：`hermes /eval`，由 PSK 网关后置保护。
- 相关环境变量（运行时注入，不写文件）：
  - `HERMES_EVAL_URL` —— hermes /eval 地址
  - `HERMES_PSK` —— 网关 PSK
  - `NEXUS_OMN_URL` / `NEXUS_OMN_PSK` —— n-omn 入口与 PSK
  - `NEXUS_REPO` / `BASE_REF` —— 待测配置 git 根与基线 ref

## 4. 用哪几个脚本（都在 `eval/`）
| 脚本 | 作用 | 关键命令 |
|------|------|----------|
| `score.py` | 评分器（stdlib） | `python eval/score.py --self-test`；`python eval/score.py --cases eval/eval_cases.json --trace <trace.json>` |
| `harness.py` | 评估执行器 | `--offline` 读录制 trace；`--online` 调 hermes /eval（需运行时） |
| `loop.py` | 迭代驱动器（Gate 1+ 才用） | `--dry --K 3` 干跑；在线传 `--eval-url`+`--psk` |

Gate 0.5 你只需要 `score.py` + `harness.py`（在线模式）。`loop.py` 用于后续有 candidate diff 时，本次不跑。

## 5. 执行步骤
1. 冻结 hermes memory 快照（Neon/R2 只读副本），保证可复现。
2. `python eval/score.py --self-test` 先确认评分器本地可跑。
3. `python eval/harness.py --online --cases eval/eval_cases.json --eval-url $HERMES_EVAL_URL --psk $HERMES_PSK` —— 对 8 例（易2/中3/难2/边界1）各跑 n=5，录制真实 trace 到 `eval/sample_traces/`（或新增 `eval/traces/`）。
4. 再用 `heldout.json` 的 4 例做终验（全程不参与优化，仅验证）。
5. 汇总 12 条 trace 的 `score.py` 输出，算 dataset 均值 + 方差，写入 `eval/baseline.md`（或 `dashboard.md`）。

## 6. 硬约束（违反即中止）
- **评测仪不可自改**：`score.py` / `harness.py` / `loop.py` 是测量仪器，只能由人类修改。任何「改脚本让分数变高」的行为都是作弊，立即拒绝。
- **保护内核**：不得触及派发通路 / Neon / R2 / mem0 / PSK / `.cnb.yml`。你只录 trace、跑评测，不改生产配置。
- **不直接写 GitHub 生产库**：产物落在 **CNB 开发/测试库**（`nexus.zen/nexus`），由 CI 同步到 GitHub。
- **单仓限制**：默认只在 `nexus.zen/nexus` 内操作，不跨仓。
- 其余约束见根 `CONSTRAINTS.md`，必须注入你的 role prompt。

## 7. 完成标准（Done）
- [ ] 12 条真实 trace 已录制（8 例 + 4 held-out），落 `eval/` 且可复现（温度=0、固定 seed）。
- [ ] `score.py` 跑出 dataset 均值 + 方差（方差<5%）。
- [ ] baseline 数字写入 `eval/baseline.md`。
- [ ] 全部产物已在 CNB 开发库提交（待 CI 同步 GitHub）。

## 8. 出问题时
- 评测仪行为看 `eval/README.md`。
- 架构/同步看 `docs/self-improvement/SYNC-双仓同步架构.md`。
- 方案全貌看 `docs/self-improvement/nexus自我改进方案-v3.1-修订版-2026-09-06.md`。
- 缺密钥/配置 → 停下，向用户要，不要猜测或硬编码。
