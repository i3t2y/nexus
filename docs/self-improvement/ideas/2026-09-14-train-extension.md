# 五字段案 · cases_train 增量入池 2026-09-14

## 假设
train 集从 10 → 16 条，覆盖「本次 502 事件刚沉淀的知识 + 池判分记录可推导的能力点」。
新基线 CI 与旧 CI[0.804, 0.924] 相交，均值落带内（不会大垮）。

## 基线
v2.3 现行：train 0.87 CI[0.804, 0.924] n=10

## 成功判据
跑 gate.py train 全集：新 mean ∈ [0.82, 0.92] 且 CI 下界 ≥ 0.78。
若新 case 全灭（mean<0.5）说明题设计错，回滚。

## 测量方法
`cd /data/.hermes/eval && python3 gate.py --dataset train`,看 scorecard.json

## 回滚
`git checkout cases_train.json`（已入 git）,或手动删新增 id。

## 新增 6 条（追加在现 10 条后）

```json
[
  {
    "id": "N1",
    "level": "medium",
    "topic": "本次502事件根因",
    "question": "2026-09-14 那次 cron 全线秒 502 的根因是什么？一句话说清。",
    "must_contain": ["指纹", "fallback", "锁"],
    "must_not_contain": ["Cloudflare 入口", "网络抖动"],
    "expected_actions": ["mem0召回"],
    "max_turns": 2
  },
  {
    "id": "N2",
    "level": "medium",
    "topic": "omn修复链部署顺序",
    "question": "改了 omn gate.js 之后，要让它在 Space 生效，完整链路是什么？",
    "must_contain": ["push main", "GHA", "Bucket", "restart"],
    "expected_actions": ["session_search或mem0"],
    "max_turns": 2
  },
  {
    "id": "N3",
    "level": "easy",
    "topic": "token选型铁律",
    "question": "要读写 xnexus/logic 这个 bucket，应该用哪个 token？为什么不能直接用 HF_TOKEN？",
    "must_contain": ["OMN_HF_TOKEN"],
    "must_not_contain": ["共用"],
    "expected_actions": ["mem0召回"],
    "max_turns": 1
  },
  {
    "id": "N4",
    "level": "hard",
    "topic": "凭证脱敏验证法",
    "question": "为什么我说「检查 gate.js 里有没有 fgCanLock」时不能信 stdout 回显？正确的验证方式是什么？",
    "must_contain": ["脱敏", "count", "布尔"],
    "expected_actions": ["mem0召回"],
    "max_turns": 2
  },
  {
    "id": "N5",
    "level": "hard",
    "topic": "时间表述铁律",
    "question": "现在 UTC 时间 08:00，你该向 Zen 报几点？为什么？",
    "must_contain": ["16", "北京"],
    "expected_actions": [],
    "max_turns": 1
  },
  {
    "id": "N6",
    "level": "hard",
    "topic": "cron-deliver-telegram-阻塞",
    "question": "eval-gate cron 现在为什么 deliver=local 而不是 telegram？根因是什么？",
    "must_contain": ["TELEGRAM_BOT_TOKEN", "spawn"],
    "expected_actions": ["mem0召回或翻日志"],
    "max_turns": 2
  }
]
```

## 为什么这么选
- N1/N2/N3/N4：全是本次会话**新沉淀的硬事实**，mem0 里有，是"云上云脑共享记忆是否生效"的最佳检验
- N5：Zen 今天刚下的铁律，纯记忆/推理，无工具依赖，应稳定满分；如果失分说明系统日期都没对齐
- N6：今天刚踩完的坑，测试 agent 能不能把"deliver=local 是 workaround 不是终点"这层因果讲清

## 不落 6 条之外的原因
- prec-pos/prep-neg 那 4 条 builtin seed 不进 train（是脚本演示数据不是真证据）
- auto_merge #22/#26 是 docs 类 PR，考察"什么 PR 能 auto merge"超出 train 范围
- block_smoke 的 #19/#25/#27 都是 gate 自身演进，放进去会变成自指（吃自己的判定）
