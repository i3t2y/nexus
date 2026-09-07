# CNB Issue → NPC(CodeBuddy) → PR 链路实操指南 (Stage 3 通关版)

> 2026-09-07 小思在 `nexus.zen/hello-cnb` 实操打通全链。本文记录所有官方文档没写清楚、必须踩坑才知道的细节。

## 0. 先决条件

- 访问令牌 scope(资源范围=指定 repo)至少需要:
  `repo-code:rw`、`repo-pr:rw`、`repo-issue:rw`、`repo-notes:rw`、
  `repo-cnb-trigger`(触发构建)、`repo-cnb-history:r`(读构建日志)、
  `repo-basic-info:r`、`repo-manage`(保护分支)、`account-profile:r`
- **改 scope 后必须重新生成 token**,旧 token 不回收新权限
- API base:`https://api.cnb.cool`,**无** `/api/v1` 前缀,**无** `/v1` 前缀
- 所有请求(包括 GET)要带 `Accept: application/vnd.cnb.api+json`

## 1. 官方文档没写明白的三个编码坑

| 端点 | 编码 | 备注 |
|---|---|---|
| `/build/start` 简单触发 | `application/x-www-form-urlencoded` | 仅传 branch/event 时 |
| `/build/start` 带 `npc` 对象 | `Content-Type: application/json`(**不是** `application/vnd.cnb.api+json`) | 嵌套对象必须纯 JSON |
| 其余业务端点(issue/PR/settings) | `Content-Type: application/vnd.cnb.api+json` | 错一个都会 400 |
| `/workspace/start` | `application/x-www-form-urlencoded` | JSON 会报 `branch is required.`(它把 JSON body 整个吞了) |

**经验**:先看 `"errmsg"` 是 `PARAM_MISS` 还是 `must be an object`——前者说明 body 没被解析=编码不对,后者说明结构不对。

## 2. `.cnb.yml` 事件挂载规则

事件挂载位置是文档最容易让读者误解的点:

```yaml
# ✓ 对
main:                        # 分支名下
  push: [...]                # 分支 event
  pull_request: [...]
  tag_push: [...]
  web_trigger: [...]
  vscode: [...]
  api_trigger: [...]         # api 触发,挂在分支下
  api_trigger_xxx: [...]     # 自定义 api 触发名
$:                           # 顶层 "$" 只收 NPC 事件
  issue.comment@npc: [...]
  pull_request.comment@npc: [...]
```

**错挂到 `$` 下的 api_trigger / web_trigger 会成功触发但报 CONFIG_EVENT_EMPTY**(实测过了)。

## 3. NPC 集成要点(官方页 https://docs.cnb.cool/zh/build/npc-integration.html)

### 3.1 流水线定义

```yaml
main:
  api_trigger_npc:
    - docker:
        image: cnbcool/default-npc:latest
      sandbox: false         # ← 官方 integration 页示例是 true!会炸
      stages:
        - name: npc-go
          type: npc:go
          options:
            systemPrompt: $systemPrompt   # 必填,env 注入
            userPrompt: $userPrompt       # 必填
```

### 3.2 API 触发

```bash
curl -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Accept: application/vnd.cnb.api+json" \
  -H "Content-Type: application/json" \
  -d '{
    "event":"api_trigger_npc",
    "branch":"main",
    "npc":{"name":"CodeBuddy","workMode":true},
    "env":{
      "systemPrompt":"You are a coding agent with git write access.",
      "userPrompt":"具体任务描述"
    }
  }' \
  "https://api.cnb.cool/$ORG/$REPO/-/build/start"
```

- `npc.name`:**只能是 `"CodeBuddy"`**,其他值 400
- `npc.workMode: true` → token 带 git push / PR 写权限(否则只读)
- **只要不传 `npc` 字段,api_trigger 默认反而拿更宽的「可信事件 scope」**

## 4. 验证闭环的四个 API

| 目的 | 端点 |
|---|---|
| 构建状态 | `GET /{repo}/-/build/logs?page_size=N&event=api_trigger_npc` |
| NPC 提交历史 | `GET /{repo}/-/npc-observability/prs` |
| PR 列表 | `GET /{repo}/-/pulls?state=all` |
| Issue 评论 | `GET /{repo}/-/issues/{number}/comments` |

构建从 `pending` → `success` 大约 20-30 秒(LLM 实际干活);如果 5 秒就 `error`,基本是配置炸了(比如 sandbox)。

## 5. 常见错误速查

| 错误 | 根因 |
|---|---|
| `Event must be 'api_trigger' or start with 'api_trigger_'` | 用错了 urlencoded 风格或 event 拼写错 |
| `CONFIG_EVENT_EMPTY ... for branch main and $.` | 事件挂在 `$` 而不是 `main` 下 |
| `PARAM_MISS branch is required`(workspace) | 发 JSON 被吞了,要 form |
| `"npc" must be an object` | `npc` 传成了 URL 编码字符串而不是 JSON 对象 |
| `npc.workMode must be a boolean` | form 编码没法传 bool,切 JSON |
| NPC build 5 秒就 error | 大概率 `sandbox: true` 或缺必填 options |

## 6. 全链演示脚本

参考 /tmp/hello-cnb/driver.py 思路(本地不留档,此处记录关键步骤):

1. 写 `.cnb.yml` 含 `main.api_trigger_npc` 流水线(§3.1)
2. push 到 main 让配置生效(被保护分支就走 PR 先合)
3. POST /build/start 带 `npc` + `env.systemPrompt/userPrompt`(§3.2)
4. 轮询 `build/logs?event=api_trigger_npc` 直到 success
5. `npc-observability/prs` 应出现新 PR

## 7. Stage 3 在 nexus 上的意义

把这条链路放大到主仓 = Hermes 守护进程(`hermes-bot`):
- 发现 eval 跌出阈值 → 在某开发 repo 开 Issue
- 调 `api_trigger_npc` workMode=true 描述问题
- CodeBuddy 自主写代码 → 推分支 → 开 PR
- 人工审查合并(就是我们这边)

模型成本:CodeBuddy 内置(deepseek-v4-flash 免费额度到 2026-12-31),不需要自付 LLM 钱。
