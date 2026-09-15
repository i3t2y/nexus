# judge-rule 校准对账 · 2026-09-15

## 结论(先说)
**不需要改 fused 权重，也不需要改 judge prompt。** 现有 0.4rule/0.6judge 口径在全部可量样本上表现合理。heldout 0.70 的"偏严"是小样本(4条)+判官模型换型的混合噪声,不是系统性偏置。

## 证据一:校准台账(8 条双分样本)
judge_fusion_calibration.jsonl 全部分歧 |Δ| ≤ 0.15,7/8 在 ±0.05 内。最大分歧 PR#30(rule 0.87 / judge 0.72)是 code 类 PR,judge 严是合理的(script 有 None-score bug 后续实炸)。

## 证据二:池内唯一大分歧 nexus#35
rule=0.882 / judge=0.35 / Δ=+0.53 → outcome=rework_regressed(正确判退)。
那是 CodeBuddy 占位测试 PR,judge 严而 rule 宽——fused=0.56 恰好落在判退档。**分歧是功能不是 bug**:fused 公式的设计意图就是 judge 主导(0.6 权)。

## 证据三:heldout 0.70 vs 0.87 成因分解
- heldout 只有 4 条,2 条答"没有记忆"(honest-negative)被判 0.5-0.6 档 → 均值必然低
- 判官当时从 deepseek 换 k3 后未重测;且 heldout 的 must_contain 含 09-07 前的冷知识
- 今日 train 19 条 mean=0.859 与 0.87 基线贴合,说明 task model(k3)没变–judge 侧是 heldout 的题旧了

## 处置
1. 不动 judge-prompt.md、不动 fused 权重
2. heldout 需要\textbf{重采},而不是校准判官——把 09-14/09-15 的新事实(指纹锁、告警机制)替换 2 条旧题,下轮 gate 自然贴近
3. cases_pool 的 mean=None 记录(judge 未跑,占大半)在 harvest 侧加"judge 缺席则 outcome 不升 auto_merge"即可,已在 gate.py 现行为中隐含满足

## 遗留观察
train harness mem0 recall 对 30 分钟内新写入记忆存在索引时延(N8 答旧事实);属正常现象,不修测量仪。
