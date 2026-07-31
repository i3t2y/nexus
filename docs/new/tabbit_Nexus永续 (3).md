### **首席架构裁决：方向正确，但当前模板不能直接进入生产。**

> **组件血统实情(2026-07-29 核实)**: 本评审件含 OmniRoute 作集群拓扑一节点(L573 mermaid)+职责段(L603)+litestream yml(L125)+ARG digest 示例(L447),系继承 omn 血统五空间原型原貌。**Nexus 仓实装仅 4 组件**(`spaces/` = hermes/langgraph/claude-code/codex,**无 omniroute 目录**);OmniRoute 实指外部 `diegosouzapw/OmniRoute` 模型路由网关作 **Nexus 下游模型数据面后端独立部署调用不合码**。凡件含 omniroute 均应理解为外部下游血统参考绝非 Nexus 内建组件,详见同血统注释 `Nexus集群永续架构最强模板.md` 头注组件血统实情段。

按第一性原理审查，它的核心路线——**计算无状态化、本地 SQLite 热写、R2 增量备份、Postgres 保存分布式状态、逻辑与镜像分离**——是合理的；但文档目前混合了“架构原则、未经证明的平台经验、示意代码和可执行模板”。其中存在多处会造成启动失败、备份失效、并发错误或供应链失控的问题。综合成熟度约 **6/10**：可作为设计草案，不应标称“最强生产模板”。

## **一、第一性原理裁决**

永续系统并不是“进程一直运行”，而是满足以下不变量：

1. 任意 Space 可被销毁并重新创建。
2. 唯一状态必须存在于容器之外。
3. 每项状态只能有一个明确的权威来源。
4. 恢复过程必须经过演练，而不是仅配置了备份。
5. 发布物必须可重复、可验证、可回滚。
6. 故障必须可观测，不能只检查进程 PID。

按这些标准，模板中的存储分层大体成立：

| 状态类型 | 合理权威源 | 裁决 |
|---|---|---|
| LangGraph checkpoint、任务状态 | Supabase Postgres | 正确 |
| OmniRoute 单节点 SQLite 状态 | 本地 SQLite + Litestream/R2 | 正确，但必须保证单写；且 Litestream 仅懂 SQLite WAL，不可用于 Postgres |
| 工作区、上传文件、工具产物 | R2 或 HF Storage Bucket | 当前模板覆盖不足（Bucket 唯一支持 RW 读写挂载，Dataset 强制只读） |
| 镜像和依赖 | GHCR digest | 方向正确，但模板仍使用浮动标签 |
| 配置与逻辑版本 | Git/不可变制品 | Dataset 可用，但当前同步方式不原子；Dataset 提供 Full Git history 可作回退锚，Bucket 为 overwrite-in-place 无锚 |
| 日志与审计 | 私有不可变日志存储 | 公开 Dataset 不应承担主要审计职责 |

**HF 平台铁律（官方 manage-spaces Note 逐字）**："Models, datasets, Spaces always mounted as read-only. Only storage buckets support read-write mounts." 即 Dataset 永远只读（RO），**Storage Bucket 是唯一支持读写（RW）挂载**的存储类型。据此三轴分层：R2 备份层不动 / 逻辑层五件=Dataset RO / 运行态 RW 件=Storage Bucket 挂 `/data` RW；运行态写件全去 `/data` 或 `/tmp`，唯一写 `/logic` 的是 `entrypoint.sh:246 npm install` 一次性写 `/logic/node_modules`。

HF 官方当前明确说明：Space 本地磁盘是 ephemeral；Storage Bucket 可以作为持久卷挂载。CPU Basic 仍无小时费，但创建 Gradio/Docker Space 需要付费计划。因而"PRO 才能创建计算型 Space"基本正确，但"PRO 层 CPU-Basic""48h 必然休眠""日志严格仅保留 30 分钟"应分开描述，不能合并成官方硬约束。官方只保证免费硬件在闲置后会休眠，并未在所查官方页面给出统一 48 小时或 30 分钟契约。 [Hugging Face](https://huggingface.co/docs/hub/spaces-overview) [Hugging Face](https://huggingface.co/docs/hub/spaces-storage)

**关于 dataset push 与 Space rebuild（平台反直觉前提）**：HF 官方 spaces-overview 中"each time a new commit pushed the Space automatically rebuild"的 "the Space" 指**自身 repo**，而非旁挂的 Dataset。挂载某 Dataset 的 Space 并不会因该 Dataset 的新 commit 而自动 rebuild 或唤醒——manage-spaces 的 TaskScheduler 例旁证：Space 醒来靠**显式 `request_space_hardware`**，非 dataset push 事件触发。因此改逻辑的热更路径是 `git push + manual restart_space`，不撞付费墙，也不依赖"push 自动唤醒旁挂 Space"这一不存在的前提。 [Hugging Face](https://huggingface.co/docs/hub/spaces-overview)

## **二、必须立即修复的阻断问题**

### **1. Dockerfile 中 `/app/data` 软链接写法无效**

模板先执行：

```dockerfile
RUN mkdir -p /data /app/data && ln -sfn /data /app/data || true
```

`/app/data` 已是目录时，`ln -sfn` 通常会在目录内创建链接，而不是把该目录替换为指向 `/data` 的链接。末尾 `|| true` 又会吞掉失败。

应改为：

```dockerfile
RUN mkdir -p /data /app \
 && rm -rf /app/data \
 && ln -s /data /app/data
```

同时，HF Docker Space 默认以 UID 1000 运行，官方要求显式处理目录所有权。模板使用 `USER root`，却没有可靠地切回非 root 用户，也没有验证 `/data` 的运行时可写权限，这是权限和容器安全双重问题。 [Hugging Face](https://huggingface.co/docs/hub/spaces-sdks-docker)

> **Nexus 现状对齐**：本节为 omn 血统模板叙述；Nexus 现役 commit `a142da9` 已把 Dockerfile 改为**墓碑骨架**（1 行 `FROM ghcr` + ARG 占位），逻辑层不进 HF repo，故本节所述软链与 UID 问题在 Nexus 侧已部分消解（`/data` 现为 Storage Bucket RW 单挂，由平台挂载提供权限；软链写法不再出现在墓碑 Dockerfile 中），见 `spaces/hermes/*`。

### **2. `entrypoint.sh` 的 PID 捕获存在 shell 优先级错误**

模板多处使用：

```sh
cd /app && node server.js & SVC_PID=$!
```

在 POSIX shell 中，`&` 会让前面的 compound list 后台运行，捕获到的 PID 未必是目标 Node/Python 进程。监督循环可能监控错误 PID，信号转发也可能失效。

应使用明确子 shell：

```sh
(
  cd /app
  exec node server.js
) &
SVC_PID=$!
```

五个业务段都要按此修正。

### **3. 声称 POSIX `sh`，实际依赖 Bash**

脚本标头为 `#!/bin/sh`，但使用：

```sh
set -eo pipefail
RANDOM
```

Debian 的 `/bin/sh` 通常是 `dash`：

- `pipefail` 不受支持，脚本会在启动时失败；
- `$RANDOM` 不存在；
- 模板因此不是可移植的 POSIX shell。

应二选一：

- 全部改成 `#!/usr/bin/env bash`，并确保镜像安装 Bash；
- 或坚持 `/bin/sh`，删除 `pipefail`，用 `od`/Python 产生随机数。

推荐统一采用 Bash，因为模板已经大量依赖其语义。

### **4. Litestream 0.5 配置的层级错误**

官方 Litestream 0.5 配置中：

- `snapshot` 是全局块，模板这一点正确；
- `l0-retention` 与 `l0-retention-check-interval` 是**全局设置**；
- 模板却把二者缩进到了单个 `db` 下。

这可能导致配置拒绝启动或字段无效。正确形式应类似：

```yaml
sync-interval: 10s

snapshot:
  interval: 1h
  retention: 24h

l0-retention: 5m
l0-retention-check-interval: 30s

validation:
  interval: 6h

dbs:
  - path: /data/omniroute.sqlite
    replica:
      type: s3
      bucket: ${R2_BUCKET}
      path: db/omniroute
      endpoint: https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com
      region: auto
      access-key-id: ${R2_ACCESS_KEY_ID}
      secret-access-key: ${R2_SECRET_ACCESS_KEY}
      auto-recover: false
```

此外，文档把 `l0-retention-check-interval: 5m` 描述为“砍 LIST 20 倍”，但官方说明它是过期 L0 文件清理检查周期，并建议比 L1 compaction 周期更频繁。把它拉到 5 分钟不能直接等价为可靠的 Class A 成本优化。

Litestream 0.5 还提供：

- `validation.interval`：副本连续性验证；
- `heartbeat-url`：只有所有数据库近期同步成功才发送心跳；
- shutdown final sync；
- `retention.enabled: false`：配合对象存储生命周期规则，减少凭据删除权限。

这些比"只看 Litestream PID"更可靠，应纳入生产基线。 [Litestream](https://litestream.io/reference/config)

> **边界警示**：以上全部配置仅适用于 SQLite WAL。Litestream 不懂 PostgreSQL 的物理 WAL 流，**不可替换 Supabase 的备份**——对 Supabase 应走其原生 daily backup + Pro+ PITR（详见阻断项 7）。

### **5. 备份 RPO 不能被写成固定 10 秒**

本项与阻断项 4 同批驳同一份 `litestream.yml`（前者是配置层级错，本项是 RPO 写死错），故互参。此外适用上节边界警示：本 RPO 讨论仅限 SQLite WAL；Postgres 的 RPO 走 Supabase PITR 而非 Litestream。

`sync-interval: 10s` 只是同步调度间隔，并不自动保证 RPO 为 10 秒。真实 RPO 受以下因素影响：

- 数据库是否持续产生事务；
- 网络失败与重试；
- Litestream 是否落后；
- Space 是否被强杀，能否完成 final sync；
- R2 写入成功与否；
- WAL/LTX 连续性。

因此应把“RPO=10s”改为：

> 目标 RPO 约 10 秒；SLO 通过 `litestream_last_sync_timestamp`、副本验证及定期恢复演练证明。

同理，`10s × 30 天 = 259,200 PUT` 只是持续写入时的上界近似，不能作为精确账单模型。官方也明确说 Litestream 只会在数据库变化时上传。 [Litestream](https://litestream.io/reference/config)

### **6. LangGraph 生命周期代码是致命错误**

模板的 `build_graph()`：

```python
async with AsyncPostgresSaver.from_conn_string(conn_str) as checkpointer:
    ...
    graph = builder.compile(checkpointer=checkpointer)
    return graph
```

函数返回后，`async with` 已退出，数据库连接或连接池被关闭；返回的 graph 持有失效 checkpointer。并且每次请求都会执行 `setup()` 和重新编译图，性能与并发语义都不合理。

正确方式是用 FastAPI lifespan 保持连接池、checkpointer 和 graph 在整个进程生命周期内存活：

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from psycopg_pool import AsyncConnectionPool
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = AsyncConnectionPool(
        conninfo=os.environ["SUPABASE_POSTGRES_URL"],
        min_size=1,
        max_size=5,
        kwargs={"autocommit": True},
        open=False,
    )
    await pool.open()

    checkpointer = AsyncPostgresSaver(pool)
    await checkpointer.setup()  # 最好迁移阶段执行，非每请求执行

    app.state.graph = build_graph().compile(checkpointer=checkpointer)
    yield

    await pool.close()

app = FastAPI(lifespan=lifespan)
```

还要为每次执行强制指定稳定且经过租户隔离的 `thread_id`，不能让调用者随意读取其他租户线程。

### **7. Supabase 密钥模型混乱**

LangGraph 的 `AsyncPostgresSaver` 使用的是 Postgres 连接串，而不是 `SUPABASE_URL` 和 `SUPABASE_SERVICE_KEY`。模板业务段却把后二者设为强制依赖，随后示例代码只使用 `SUPABASE_POSTGRES_URL`。

应拆分为：

- `SUPABASE_POSTGRES_URL`：LangGraph checkpointer 必需；
- `SUPABASE_URL`、服务角色密钥：只有调用 Supabase REST/Auth/Storage 时才需要；
- 服务角色密钥必须作为 Secret，绝不可被客户端或公开日志接触。

同时，"Supabase 自带 PITR"不成立。官方说明：

- Pro/Team/Enterprise 档含每日备份；
- PITR 是 Pro+ 档**另行启用的付费附加项**，提供 RPO 约 2 分钟；
- 免费层须自行定期 `db dump` 并保留站外副本。

因此模板必须改为"按实际套餐验证备份能力"，并增加 `pg_dump` 到 R2 的独立恢复链。 [Supabase](https://supabase.com/docs/guides/platform/backups)

**Litestream 边界警示（关键）**：Litestream 仅懂 SQLite WAL 流，**跑不起 Postgres**——Supabase 用的是 PostgreSQL，其物理 WAL 流格式 Litestream 读不了，把 Litestream 指向 Postgres 是技术误配。正解：Supabase 应直接用其自带 daily backup + Pro+ PITR（RPO 2min），免费档用 `supabase db dump` 入 R2 作站外冷备；仅当数据库真身是单文件 SQLite（如 OmniRoute 的 `omniroute.sqlite`）时 Litestream 才是正用。

### **8. Dataset 逻辑层上传不是原子发布**

workflow 逐文件调用 `hf upload`：

```sh
for f in ...; do
  hf upload ...
done
```

这会产生多个 commit。虽然启动时读取一次 HEAD revision 可以避免单次下载过程中漂移，但它不能避免 Space 在上传序列中途启动并锁定"半套新版本"。

> **平台反直觉前提（修正项③）**：挂载某 Dataset 的 Space 并不因该 Dataset 的新 commit 而自动 rebuild 或唤醒——HF spaces-overview 中 "the Space" 指自身 repo。因此"上传中途 Space 自动启动并锁定半套"这一风险描述需精确化为 **boot vs sync-logic push 竞速**：boot 时 `hf download` 拉到的逻辑池若在 sync 逻辑层 push 的新 commit 落地前发起，便拉到旧池。改逻辑的热更路径是 `git push`（仅手动，用户红线：永不自动 push HF）+ `manual restart_space`，不撞付费墙。

> **omn-merge 现役动线对照（修正项④，Nexus 现役未施）**：omn-merge 现役**非 Volume 只读挂载**——`start.sh` 走 `hf download --local-dir /tmp/logic` 取逻辑层到临时目录，再 `cp -a /tmp/logic/. /logic/` 生成**可写副本**（`entrypoint.sh:246 npm install` 写 `/logic/node_modules` 为证）。只读 Dataset Volume 是 audit 裁决"已准未施蓝图"（audit:268"本轮勘探裁决闭环非实施"）。本段原文假设 Dataset 以 Volume/直挂形态被 Space 读，未覆盖"可写副本化"这条已实装动线——**挂载形态 Volume vs hf download+cp** 是本轮 Nexus B 路径未决分歧项之一，此处按下标。

真正的原子发布必须：

1. 将整套逻辑文件放入暂存目录；
2. 生成 manifest；
3. 一次 `upload_folder` 或一次 commit 提交全部文件；
4. 校验远端 manifest 和所有 SHA-256；
5. 最后更新单一的 release pointer。

> **竞速根治（修正项⑦）**：omn-merge `start.sh:64-80` 取 Dataset HEAD 的 `commit_id` 锁作 atomic 拉取——`list_repo_commits` 首个 `commit_id` + `--revision` 锁同点全件下载，防 boot vs sync-logic push 竞速拉旧池（boot#4 15:30Z 拉 8 员旧池事件签名实证）。本段原文"读一次 HEAD revision 避免单次下载漂移"未达此根治强度，须升级为 commit_id 锁 + `--revision` 同点全件。

同时，当前 readback 只校验五个文件，遗漏：

- `commit_scheduler.sh`
- `keepalive_relay.sh`
- `replay_packages.sh`
- `app.py`
- `requirements.txt`
- Claude/Codex server wrapper

这与"逐字节血缘验证"的声明不一致。

> **手动 push 红线约束**：用户红线为 git push 只能手动、永不自动 push HF。本节及 Ln226-230 的"workflow 逐文件 `hf upload`"、Ln385-391 的"git push --force"均假设自动化 push 为既定前提，与 Nexus 部署侧实际可行边界脱节——发布流程须在"手动 push"约束下重设：CI 仅产出 manifest + SHA-256，由人工或显式触发器的 `hf buckets sync`（Nexus 现役已用，见下方对齐）落地，不内置自动 push。

### **9. Dataset 快照并没有真正保留历史快照**

代码每次都上传到：

```text
snapshots/<component>/
```

文件名也始终相同。Dataset 的 Git 历史确实可能保留旧版本，但：

- 恢复必须依赖 Git revision；
- 仓库会无限累积整库对象和 commit；
- 没有快照索引、校验值、恢复状态；
- 没有清晰保留策略；
- “5 分钟全库快照 × 5 个组件”会造成极高 commit 噪声与存储增长。

更合理的方案是：

- R2/Litestream：主连续备份；
- 每小时或每天生成一份独立 SQLite backup；
- 文件名包含 UTC 时间和 SHA-256；
- 写 `manifest.json`；
- Dataset 只保存低频跨平台冷备，或干脆改用 HF Bucket；
- 定期从冷备恢复到临时库并执行 `PRAGMA integrity_check`。

HF 官方也明确区分：Dataset 是版本化 Git 仓库，Bucket 是非版本化、可变对象存储，更适合工作存储、日志和 rolling backup。 [Hugging Face](https://huggingface.co/docs/hub/storage-buckets)

> **Bucket 代价须显标（修正项①②）**：HF storage-buckets 对比表逐字——Dataset 提供 "Full Git history"（可接 commit SHA / git tag 作回退锚，1 commit=1 不可变回退锚），Bucket 标 "None (mutable, overwrite-in-place)"（**无版本、无回退锚**，sync 覆盖即丢旧）。因此"改用 Bucket"须明确标注：Bucket 换取 RW 写能力与低工作存储成本的代价是**失去 Git 版本化回滚**——改逻辑在 Bucket 上是 sync 覆盖无锚回退，不可误作"等价升级"。

### **10. CommitScheduler 启动条件错误**

当前只有设置了公开日志库才启动：

```sh
if [ -f /logic/commit_scheduler.sh ] &&
   [ -n "$LOG_PUBLIC_DATASET_REPO" ]; then
```

这意味着只配置私有快照库、没有公开日志库时，**私有数据库快照完全不会运行**。

应改为：

```sh
if [ -f /logic/commit_scheduler.sh ] &&
   { [ -n "${PRIVATE_SNAPSHOT_DATASET_REPO:-}" ] ||
     [ -n "${LOG_PUBLIC_DATASET_REPO:-}" ]; }; then
  bash /logic/commit_scheduler.sh &
  SCHED_PID=$!
fi
```

### **11. 恢复失败后“新库继续”可能覆盖有效远端历史**

当前 restore 失败就允许业务创建新库，再启动 Litestream replicate。这是危险的 fail-open：暂时性 R2 网络故障可能被误判成“首次部署”，随后空库可能建立新的复制历史。

必须区分：

- `not found`：首次部署，可新建；
- 认证失败、网络失败、校验失败：生产必须 fail-closed；
- 有本地有效库：保留本地库，不应无条件覆盖；
- dev 可选择 fail-open，但必须显式设置变量。

生产建议：

```text
RESTORE_POLICY=strict
ALLOW_EMPTY_BOOT=0
```

并在启动复制前验证：

- 数据库存在且非空；
- `PRAGMA quick_check` 为 `ok`；
- schema version 合法；
- 恢复时间不早于允许的最大陈旧阈值。

> **omn-merge fail-open 对照（修正项⑤，模式分歧项）**：omn-merge **故意 fail-open WARN**——`entrypoint.sh:202-212` 注明"只告警不 exit，上游前滚迁移让旧库自动进新 schema，版本不齐仍可跑"。真正的硬 `exit 1` 只出现在 `gate.js` PSK 缺失（`gate.js:46-49 process.exit(1)`）与 `entrypoint.sh` npm install FATAL（`:249`）。即 omn-merge 的设计语义是"schema 不齐可前滚续跑"而非"严格 fail-closed"。本段一刀切判"fail-open 危险须强制 fail-closed"，未承认"schema 不齐仍可跑"是有意的前滚迁移语义；**模式 fail-open vs 硬 exit1** 是本轮 Nexus B 路径未决分歧项之一，Nexus 若要硬断言须自写，omn-merge 给的是反例而非范本。本段"恢复时间不早于陈旧阈值"等验证项在 omn-merge 反例面前应降级为"有状态权威库的严格档"，而非一律适用。

### **12. `HF_TOKEN` 权限描述错误**

文档把 `HF_TOKEN` 描述为“写权限 token（逻辑层拉取）”。拉取私有 Dataset 只需要读权限；CommitScheduler 才需要写权限。一个通用写令牌贯穿五个 Space，会扩大爆炸半径。

应拆为：

- `HF_LOGIC_READ_TOKEN`
- `HF_SNAPSHOT_WRITE_TOKEN`
- `HF_LOG_WRITE_TOKEN`

每个 token 仅限对应 repo 和动作。公开 Dataset 下载不需要 token，私有 Dataset 需要最小读权限。

### **13. Secret 扫描钩子并非 fail-closed**

代码出现：

```python
except Exception:
    sys.exit(0)  # fail-open on parse error
```

却称为 fail-closed，这是自相矛盾。此外，只要整个 payload 中出现一个安全占位符，代码便会跳过全部扫描：

```python
if not SAFE_PATTERN.search(text):
    ...
```

攻击性或意外真密钥只需与 `${VAR}` 同时出现就可绕过。应先逐处删除或掩码安全占位符，再扫描剩余文本；解析失败必须退出非零。

还应采用：

- GitHub Secret Scanning；
- push protection；
- `gitleaks`/`trufflehog`；
- CI 中对完整 Git 历史扫描；
- R2/HF token 定期轮换。

### **14. `.gitignore` 示例格式错误**

模板把 `.gitignore` 写成 JSON：

```json
{
  "patterns": [...]
}
```

这不是合法 `.gitignore`，不会产生预期效果。应直接写逐行 glob。

### **15. 工作流发布凭据可能进入进程列表和 Git 配置**

```sh
git remote add origin "https://user:${HF_TOKEN}@..."
```

令牌可能出现在进程参数、错误输出或 `.git/config` 中。应使用凭据 helper、临时 HTTP header，或者官方 API 上传，不要把 token 嵌入 remote URL。

此外，强推 Space 主分支：

```sh
git push --force
```

会破坏审计历史，也容易覆盖人工修复。更可靠的是 fast-forward、专用部署分支或 release commit。

### **16. evidence 分支工作流并不稳健**

`actions/checkout` 直接指定 `ref: evidence`，若分支尚不存在，第一次运行会失败；多个组件每 30 分钟并发写同一分支，也会发生 non-fast-forward 冲突。还没有：

```yaml
permissions:
  contents: write
```

应至少：

- 初始化 evidence 分支；
- 设置 workflow `concurrency`；
- fetch/rebase 后重试；
- 或按组件使用独立分支；
- 更优是把证据直接写入私有 Bucket/日志服务，而不是高频提交 Git。

## **三、重要但非阻断的架构修正**

### **浮动 `:stable` 与可重复部署冲突**

文档一方面强调可回滚，一方面推荐重推 `:stable`。浮动标签会造成：

- 同一 Git commit 在不同时间构建出不同镜像；
- 无法证明当前运行的真实内容；
- 回滚依赖覆盖标签，审计性差。

生产应钉 digest：

```dockerfile
ARG BASE_IMAGE=ghcr.io/i3t2y/nexus-omniroute@sha256:<digest>
```

`stable` 只能用于 dev。生产晋级应记录 image digest、逻辑 release digest 和数据库 schema version 三元组。

> **Nexus 现状对齐（已落）**：本节是 omn 血统模板叙述；Nexus 现役已落 commit `a142da9` 的 **ARG 双轨**——GHCR base 镜像（依赖进镜像永不进 HF repo）+ Dockerfile 墓碑（1 行 `FROM ghcr` + ARG 占位）；commit `fe275ae` 进一步修正 ARG 作用域（`ARG BASE_IMAGE` 全局声明 + `FROM ${BASE_IMAGE}` + `ARG BASE_IMAGE` 重声明 + `ENV BASE_IMAGE=${BASE_IMAGE}` 转存）并填真 GHCR owner `i3t2y`。故本节所述"模板仍使用浮动标签"问题在 Nexus 侧已降级为**历史模板问题**——现役 Dockerfile 为墓碑骨架，digest pin 通过 ARG 在发布时注入，见 `spaces/hermes/*`。

### **“三件永不再改”应改为“低频、受控、可审计”**

不可变的是**某个发布版本的制品**，不是文件名永远不能改。Dockerfile、启动脚本或 README 一旦发现安全问题，必须可修复。

正确原则应是：

> 骨架层禁止日常业务变更；安全、平台兼容与供应链修复通过受控发布窗口修改。

### **五个 Space 并不都需要 SQLite 和 Litestream**

Claude Code、Codex 如果只是无状态执行器，不应人为创建 `claude.sqlite`、`codex.sqlite`。它们真正需要持久化的是：

- 工作目录；
- Git checkout 或工作树；
- 执行产物；
- session metadata；
- 审批与审计记录。

建议：

- 任务状态：Postgres；
- 工件和工作区：R2/HF Bucket；
- 临时 checkout：ephemeral；
- 不存在权威 SQLite 状态时，不运行 Litestream。

LangGraph 同理：如果 metadata SQLite 没有真实消费者，应删除，而不是为了统一模板制造一个数据库。

### **Hermes 数据路径不能凭模板臆造**

模板把 Hermes 启动简化为：

```sh
python3 -u app.py --port ... --data-dir ...
```

但官方 Hermes Agent 是复杂 CLI/gateway 系统，并非天然提供该 `app.py` HTTP 服务。这只能是 Nexus 自己的 adapter，不能声称是上游直接启动方法。需明确：

- `hermes-adapter` 是本项目实现；
- 上游 Hermes 版本和入口点固定；
- gateway/session DB、skills、memory、workspace 的真实路径来自源码审计；
- 每个路径确定权威存储。

Claude Code 的 `claude_server.js` 和 Codex 的 `codex_server.js` 也同样是待实现 adapter，不是复制模板后即存在的上游能力。

> **hermes 误引澄清**：HF Hub 上的 `hermes-agent`（NousResearch 出品）是 Nous 旗下的**另一个 hermes**，非本 Nexus 主控。Nexus 的 Hermes 是 Python FastAPI + langgraph + Gradio 主控，二者不可混引。此外 Nexus 本体**无 skills/plugins 装载器代码**（grep 全零），`skills_index` 表仅元数据（schema 注释"Skill 内容存 R2"，未实现）——任何"Hermes 装载 skill 插件"叙述均属臆造，未证实。

### **Claude/Codex API Key 方向正确，但措辞过度绝对**

官方资料确认：

- Claude Code 支持 Anthropic API Key、云厂商凭据等；
- Codex CLI 支持 ChatGPT 登录和 API Key，官方明确推荐 API Key 用于程序化 CI/CD；
- Codex 也支持 device code 或企业 access token。

因此应把铁律改为：

> 非交互容器必须使用可自动化的非浏览器认证；默认优先 API Key，企业环境可用受管 access token 或云工作负载身份。

Codex 文档还明确警告：不要把 Codex 执行暴露在不可信或公共环境。当前模板仅有一个 PSK，没有作业级隔离、审批、目录沙箱和网络策略，远远不够。 [Anthropic](https://docs.anthropic.com/en/docs/claude-code/third-party-integrations) [OpenAI](https://developers.openai.com/codex/auth)

### **网关安全仍不足**

`INTERNAL_PSK` 仅是共享秘密，不是完整安全边界。五个 Agent 能执行代码、Git 和网络访问，至少需要：

- 外部身份认证与租户授权；
- Cloudflare Access/JWT 或 mTLS；
- 每服务独立凭据，不使用全局 PSK；
- 重放保护或短期签名；
- 请求体和输出大小限制；
- 并发、速率、成本预算；
- 任务超时与取消；
- 工作区隔离；
- 出网 allowlist；
- 审批模式，禁止默认 unrestricted execution；
- 审计日志不得包含 prompt、源码、diff、凭据。

`content-length` 守卫也不是可靠内存保护。chunked 请求、压缩炸弹和流式无限输入均可绕过。应在边缘和应用层同时使用字节计数流，超限主动断开。

### **健康检查过浅**

当前 `/healthz` 主要验证上游返回 200。生产至少拆成：

- `/livez`：进程存活；
- `/readyz`：依赖可用，可接流量；
- `/healthz/detail`：仅内部可见。

Readiness 应验证：

- R2 最近成功同步时间；
- Postgres 连接；
- schema version；
- 磁盘剩余空间；
- 逻辑 release digest；
- 下游模型网关状态；
- 恢复年龄与复制滞后。

### **保活互探应删除或降级**

内部互探并不能提高可用性，只会人为制造流量、掩盖真实休眠策略，并可能导致五个 Space 相互保持唤醒。HF 官方说明免费硬件会在闲置后休眠；若要求持续运行，应购买付费硬件，而不是将“防固定周期识别”写入架构。后者既不是可靠工程机制，也不应作为合规设计目标。 [Hugging Face](https://huggingface.co/docs/hub/spaces-overview)

### **Package Replay 不应进入生产主路径**

启动时重放任意 apt/pip 包会破坏：

- 可重复构建；
- 依赖锁定；
- 软件物料清单；
- 漏洞扫描；
- 启动时间 SLO；
- 上游包被替换时的供应链安全。

生产依赖必须写入镜像并锁版本、hash 和 digest。Package Replay 只能作为开发沙箱的实验功能，且必须有 allowlist、版本和哈希校验。

## **四、建议的最终拓扑**

```mermaid
flowchart LR
    U[用户/自动化调用方] --> E[Cloudflare Access/WAF/API Gateway]
    E --> H[Hermes 控制面]
    E --> O[OmniRoute 模型网关]
    E --> L[LangGraph 编排面]

    H --> L
    L --> O
    L --> C[Claude Worker]
    L --> X[Codex Worker]

    H --> P[(Supabase Postgres)]
    L --> P
    O --> P
    C --> P
    X --> P

    O --> S[(本地 SQLite)]
    S --> R[(R2 Litestream 副本)]

    H --> A[(R2/HF Bucket 工件库)]
    C --> A
    X --> A

    P --> B[(定期 pg_dump 跨云冷备)]
    R --> T[恢复演练与完整性验证]
    B --> T
```

建议重新划分职责：

- **Hermes**：控制面、计划、用户记忆入口；不要承担所有日志归档职责。
- **LangGraph**：唯一工作流状态机和 checkpoint 权威源。
- **OmniRoute**：模型调用数据面；只保存路由所必需状态。
- **Claude/Codex**：短生命周期 worker；默认无权威本地数据库。
- **Postgres**：任务、租户、审批、执行状态、幂等键。
- **R2/HF Bucket**：工件、工作区快照、日志和大文件。注意 HF Storage Bucket 是**唯一支持读写挂载**的存储类型，但无 Git 版本化回滚锚（storage-buckets 对比表 "None, mutable, overwrite-in-place"），须配合 R2 作冷备补足回滚能力。
- **Litestream/R2**：仅用于真正存在的单写 **SQLite**（边界警示：仅懂 SQLite WAL，跑不起 Postgres；Supabase Postgres 走自带 daily backup + Pro+ PITR / 免费档 `pg_dump` 入 R2）。
- **Dataset**：低频、版本化、人工可审阅的冷备或发布制品，不做每五分钟日志总线。注意 Dataset **强制只读**（manage-spaces Note 明示），不可用作运行态 RW 写件。

## **五、生产发布门槛**

在修订为正式模板前，至少完成以下验收：

1. **Shell 静态检查**：`shellcheck`，并在 Debian `dash`/Bash 中分别验证。
2. **配置检查**：每个 `litestream.yml` 执行配置加载与本地 MinIO 集成测试。
3. **故障注入**：业务进程、gate、Litestream、网络分别被杀。
4. **恢复测试**：从空盘恢复，并比对 SHA-256、schema、`integrity_check`。
5. **RPO 测试**：持续写入后随机强杀，统计真实数据丢失窗口。
6. **原子发布测试**：同步中途终止，Space 不得启动半套逻辑。
7. **回滚测试**：镜像 digest、逻辑 release、schema 三者兼容。
8. **并发测试**：LangGraph 多请求、多 thread、多租户隔离。
9. **安全测试**：chunked body、压缩输入、PSK 重放、日志泄密。
10. **DR 演练**：R2 不可用、HF 不可用、Supabase 不可用分别演练。
11. **恢复目标**：明确每个组件的 RPO/RTO，而不是集群只给一组数字。
12. **供应链**：镜像签名、SBOM、依赖 hash、digest pin、漏洞扫描。

## **最终裁决**

模板中最值得保留的是：

- 环境、逻辑、状态三层分离；
- SQLite 本地热写而非直接放对象/NFS 挂载；
- R2 与跨平台冷备双轨；
- PID 1 监督与优雅停机思想；
- dev/prod 隔离；
- LangGraph 采用外部 Postgres；
- Secrets 不进 Git；
- Claude/Codex 使用非交互认证。

必须撤回或重写的表述包括：

- "48h、30min、build 冻结"一律视为官方硬契约（见第一节末平台前提勘误）；
- "三件永不再改"（见非阻断"低频受控可审计"修正）；
- "10 秒即严格 RPO"（见阻断项 5）；
- "Supabase 自带 PITR"（见阻断项 7 + Litestream 边界警示）；
- "五个组件都需要 SQLite/Litestream"（见非阻断"五 Space 不都需 SQLite"）；
- "每五分钟 Dataset 全量快照是最优"（见阻断项 9；Dataset 强制只读且无 RW 写件语义）；
- "互探保活防识别"（见非阻断"保活互探应删除"）；
- "Package Replay 是生产永续能力"（见非阻断 Package Replay 段）；
- "当前代码可直接落地"（见上述 16 阻断项）。

在修完上述阻断项后，这套设计可提升至 **8/10**；再补齐原子发布、恢复演练、作业隔离、镜像签名和可观测性后，才有资格成为 Nexus 的生产级永续基线。

> **本件为 omn 血统模板叙述总则**：本裁决正文多基于 omn 模板经验。Nexus 现役 commit `a142da9` 已落 **Bucket rw `/data` 单挂逻辑层**（app/scripts/libs）+ **GHCR base 镜像**（依赖先苦进镜像永不进 HF repo）+ **Dockerfile 墓碑**（1 行 FROM ghcr + ARG 占位），commit `fe275ae` 修正 ARG 作用域、commit `4fc098e` 改 bootstrap 走 `hf buckets sync` 拉真 Bucket。凡上述阻断项/非阻断项若与 Nexus 现役 a142da9/fe275ae 相违（如浮动标签、Dockerfile 软链、逐文件 hf upload push），读者应视为**历史模板问题**并以现役 `spaces/hermes/*` 实际为准，本件保留模板叙述作设计裁决参照，不删。
>
> **本轮 Nexus B 路径三分歧项（未决）**：本件正文多处假定其一而未标注未决，特此声明——① 挂载形态 **Dataset Volume 直挂 vs hf download + cp 可写副本**（现役 omn-merge 走后者）；② 失败模式 **fail-open WARN vs 硬 exit1**（现役 omn-merge 走前者作前滚迁移）；③ 发布工作流 **六件 readback 清单 vs 单件 sync**（Ln242-249 假六件清单）。三者在 B 路径探索半程暂停状态（audit:268"本轮勘探裁决闭环非实施"），尚未落地，读者引用本件相关结论时须意识此悬决。

*内容由 AI 生成仅供参考*