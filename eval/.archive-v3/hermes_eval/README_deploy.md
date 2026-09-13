# hermes /eval 服务端（v3 阶段1 / M1，P7 鉴权）

黑盒评估器：本地 `loop.py` 把 prompt/检索策略 diff 推给 hermes，hermes 在**隔离环境**回放 8 例 + 4 held-out，只回标量分数。NPC 不可达、eval 集与评估器仅在服务端、返回体不含用例（防 harness-level cheating）。

## 目录结构（部署到 hermes HF Space）
```
hermes_eval/
  server.py        # FastAPI：/eval /eval/batch /eval/health /eval/dryrun /eval/gate1
  replay.py        # replay_case：stub（离线自测）| real（接 hermes 路由核心）
  requirements.txt
  spaces.Dockerfile
eval_cases.json    # 8 例（人写，NPC 不可读）
heldout.json       # 4 例 held-out（人写）
score.py           # 评分器（人写）
```
> 把 `server.py` `replay.py` `requirements.txt` `spaces.Dockerfile` + `eval_cases.json` `heldout.json` `score.py` 一并拷进 Space 根，同目录即可（`HERMES_EVAL_DIR` 默认=本目录）。

## 环境变量
| 变量 | 说明 |
|---|---|
| `HERMES_EVAL_PSK` | **必填**。Bearer PSK，n-omn gate 模式。空则拒绝对外服务（503）。|
| `HERMES_EVAL_BACKEND` | `stub`（默认，离线自测桩）/ `real`（接真实 hermes）|
| `HERMES_EVAL_DIR` | score.py / cases 所在目录，默认=本目录 |
| `HERMES_CORE_MODULE` | real 后端：你的 hermes 路由核心模块名（提供 `replay(case, diff, neon_ro_dsn)`）|
| `HERMES_CORE_PATH` | real 后端：核心模块搜索路径 |
| `NEON_RO_DSN` | real 后端：只读 Neon pgvector 快照连接串 |
| `PORT` | HF Space 固定 7860 |

## 端点契约（loop.py 已对齐）
- `POST /eval` `{repo,base_ref,diff_patch,target}` → `{score,breakdown,kept}`
- `POST /eval/batch` `{repo,base_ref,diff_patch}` → `{scores,baseline_scores,dataset,heldout:{before,after},kept}`
- `GET /eval/health` → `{ok,backend,psk_set,score_ok}`
- `GET /eval/dryrun` → `{high:score, banned:score}`（高分 / 归零两类，Gate 1 证明）
- `POST /eval/gate1` `{diff_patch}` → `{blocked:bool}`

鉴权：除 `/eval/health` 外所有 `/eval/*` 均需 `Authorization: Bearer <PSK>`；缺失/错误返回 401。

## Gate 1（P11 防 NPC 篡改评估器）
diff 触碰 `eval_cases/` | `/expected.json$` | `/score.py$` → `kept:false`，不生成 PR。`/eval/gate1` 可预检。

## 本地验证（0 成本，stub 后端）
```bash
pip install -r eval/hermes_eval/requirements.txt
cd eval
HERMES_EVAL_DIR=. PYTHONPATH=hermes_eval:. python -m hermes_eval.server
# 另开终端：
curl -H "Authorization: Bearer dev" localhost:7860/eval/dryrun
curl -X POST -H "Authorization: Bearer dev" -d '{"diff_patch":""}' localhost:7860/eval/batch
```
或纯 core 自测（无需 fastapi）：
```bash
cd eval && HERMES_EVAL_DIR=. PYTHONPATH=hermes_eval:. python -c "import hermes_eval.server as s; print(s.self_test())"
```

## 接真实 hermes（backend=real）
1. 把你的 hermes 路由 + mem0 检索包成 `core.replay(case, diff_patch, neon_ro_dsn) -> trace`（trace 字段见 replay.py）。
2. Space 设 `HERMES_EVAL_BACKEND=real`、`HERMES_CORE_MODULE=...`、`NEON_RO_DSN=...`（只读快照）。
3. 重跑 dry-run + batch，确认分数可复现（n=5 方差<5%，Gate 0）。
