# eval/ — 河图 gate runtime 栈

**状态**: v2.3 现役真源。runtime 跑在 `/data/.hermes/eval/`（容器内 fuse 挂载，真源在 HF bucket `sonoke/logic/.hermes/eval/`)。

## 架构历史

- `eval/.archive-v3/` — 旧 v3 skeleton (replay harness + nexus 旧自评),已归档。
- `eval/` 顶层 — 现役 gate 栈：
  - `harness.py` — run_case 跑题器，TASK_MODEL 默认 `nvidia/moonshotai/kimi-k3`,Authorization 从 `/proc/1/env` 读 `XNEXUS_API_KEY`
  - `gate.py` — gate 评分 + rework + auto-merge
  - `judge.py` — 裁判模型接口
  - `score.py` — 评分维度计算
  - `cases_train.json` / `cases_heldout.json` — 案例集
  - `gate_state.json` — runtime 状态（processed / rework queue / baseline)

## 历史 bug（已修复，2026-09-13)

`harness.py` 曾长期带 `Authorization: Bearer ***` 占位符（脱敏时被写死），导致 eval 全 0 分。修复：
- L63 改为读 `/proc/1/environ` 里 `XNEXUS_API_KEY` 真值
- 同步到 bucket 真源

## 跑法

```bash
cd /data/.hermes/eval
EVAL_TASK_MODEL=sensenova/sensenova-6.8-flash-lite python3 harness.py cases_train.json
```

**base line**: 0.87 — train_mean=0.856 CI[0.794,0.91] / sensenova=0.846 CI[0.776,0.91]（均容差内）

**路径红线**: eval 栈永远入 git;runtime 目录每一处改动须回写 git 主线 + bucket（同步由 hermes 启动钩子做）。

**bug 修**: gate rework / auto-merge 自动召唤，不改的是 cron (eval-gate, 15min) 自动析 gate_state.json。
