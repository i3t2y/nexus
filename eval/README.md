# nexus eval 脚手架（v3 M0）

本地可跑、0 成本的 eval 基线。**评估器由人写（非 NPC，违反非目标 3）。**

## 文件
- `score.py` —— 评分器，复用 Langfuse 公式但断言分级（修复 P3）。仅标准库。
- `eval_cases.json` —— 8 例（易2/中3/难2/边界1），每例含 `assertions`（带 weight）+ `banned_patterns`。
- `heldout.json` —— 4 例 held-out，**全程不参与优化**，仅终验（防过拟合）。
- `harness.py` —— 评估执行器骨架（v3 §3.5 / 阶段 0.5）。`--offline` 读录制 trace，`--online` 调 hermes /eval（需运行时）。
- `sample_traces/easy-01.json` —— 一条样例 trace，用于本地验证 scorer。

## 本地验证（现在就能跑）
```bash
python eval/score.py --self-test
python eval/harness.py --offline --cases eval/eval_cases.json --traces eval/sample_traces
```
self-test 会打印 正常 trace 的高分 与 违规 trace 的 `correctness=0`，证明 banned_patterns 归零生效。

## 接入真实链路（Gate 0.5 后）
1. 冻结 hermes memory 快照（Neon/R2 只读副本），保证可复现。
2. `harness.py --online` 需：`HERMES_EVAL_URL`（hermes /eval，PSK 网关后置）、`HERMES_PSK`、`NEXUS_REPO`、`BASE_REF`。
3. 模型调用走 n-omn 免费池（0 成本）；温度=0、固定 seed，baseline 跑 n=5 取方差<5%。

## 断言类型
`memory_hit`(值可列表，部分命中按比例) / `refuse` / `dispatch` / `action`(可列表) / `max_turns`。
`banned_patterns` 命中 → correctness=0（保护内核硬编码，E12 教训）。

## 迭代驱动器 loop.py（v3 §1.4 / §3.5 / §4，修复 P2）
端到端：取 diff → 隔离 worktree → 跑 harness K 次 → K=3 中位数（P12）→ G-3 决策 keep/revert → 记 attempts（worktree 外，修复 P4）+ R2 快照 + 追加 dashboard.md。

```bash
# 离线干跑（0 成本，用录制 trace；no-op 应被 G-3 拒）：
python eval/loop.py --dry --K 3

# 由 n-omn CodeBuddy 从目标生成 diff（需 NEXUS_OMN_URL/PSK）：
python eval/loop.py --goal "优化 hermes 路由，书签查询优先走 mem0" --propose --target <hermes配置git根>

# 直接给 diff 文件：
python eval/loop.py --diff my.patch --target <hermes配置git根>

# 在线（M1 /eval 建好后）：传 HERMES_EVAL_URL/HERMES_PSK
python eval/loop.py --eval-url https://omn.360710.xyz/v1/eval --psk <PSK> --K 3
```

`--dry`/离线：录制 trace 不随 diff 变化 → candidate==baseline → delta=0 → G-3 安全拒绝（证明控制流与门禁正确）。
真实测量需 online：hermes /eval 按 diff 重跑 agent 抓新 trace。

## 真相源
`attempts.jsonl` 须落 worktree 外（`~/.nexus-eval/attempts/`，每日 R2 快照，修复 P4）。
注意：早期误建的 `eval/cases/`、`eval/traces/`、`eval/heldout/` 空目录为残留，规范用例只在 `eval_cases.json` / `heldout.json`，录制 trace 落在 `sample_traces/`。
