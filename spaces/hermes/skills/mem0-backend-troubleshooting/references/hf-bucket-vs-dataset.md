# HF Storage Bucket vs Dataset — Full Comparison (2026-08-17查证)

Official docs查证: storage-buckets, storage-buckets-access, storage-buckets-integrations, storage-limits, blog/storage-buckets (GA 2026-03-10), GitHub issue #3806 (2026-02-13 @Wauplin "persistent storage slowly deprecated").

## 1. 本质区别

| 维度 | HF Dataset (repo) | HF Storage Bucket |
|---|---|---|
| 底层 | git-based repo, track file history | Xet 后端 S3-like object storage |
| 版本 | Full Git history (每次upload = 1 commit) | 无版本, mutable, overwrite-in-place |
| 操作 | `upload_folder` / `snapshot_download` (git push/pull) | `hf buckets sync` / `hf buckets cp` (rsync-like) |
| Space挂载 | 只读 (官方明文 "always mounted read-only") | rw (官方默认 read-write) |
| 用途 | 发布成品数据集 | 官方原话: "checkpoints, logs, intermediate artifacts, doesn't need version control" |
| 去重 | Xet chunk-level | Xet chunk-level (相同) |
| PR | Yes | No |
| Model/Dataset Cards | Yes | No (但 README rendered) |

## 2. 持久化能力对比

| 维度 | Dataset | Bucket |
|---|---|---|
| Space内rw | ❌ 只读 | ✅ 默认 rw |
| Volume mount | ❌ 不支持 | ✅ `-v hf://buckets/owner/name:/data` |
| 写膨胀 | ✅ 每次同步+1 commit, history无限膨胀 | ✅ 覆写, 无git history膨胀 |
| 删旧文件释放配额 | ❌ git history保留所有版本 | ✅ 删了就没了, 只付当前存储 |
| boot时拉取 | `snapshot_download` (clone git repo) | `hf buckets sync` (rsync-like, 只传changed) |
| 运行时推送 | `upload_folder` (git commit+push) | `hf buckets sync` (增量同步) |
| server-side copy | ❌ | ✅ bucket←repo server-side (Xet hash迁移) |

## 3. 配额 (官方 storage-limits 文档)

| 账号类型 | 公开存储 | 私有存储 |
|---|---|---|
| 免费user/org | Best-effort | 100GB免费 |
| PRO | 10TB+add-on | 1TB+pay-as-you-go |

**Bucket和Dataset共用同一配额池** — 官方明文: "Storage limits apply to all types of repositories (models, datasets, buckets, …)". 配额不是选择因素。

## 4. 三件套实际部署 (2 Space架构)

```
hermes Space (sonoke) — Bucket:
  /data → hf-mount (FUSE) type fuse (rw,nosuid,nodev,relatime)
  ├── app/          ← 逻辑层 (runtime rw)
  ├── scripts/      ← 持久化脚本 + template
  ├── libs/         ← 共享库
  ├── home-backups/ ← 配置快照 (.env, SOUL.md, config.yaml, plugins/, skills/)
  ├── state-backups/← state.db 快照
  └── docs/
  Bucket名: sonoke/logic, env: NEXUS_LOGIC_BUCKET=logic, HF_OWNER=sonoke

memgraph Space (nmem) — Dataset:
  snapshot_download(nmem/nworker) → /app/worker (boot时拉取逻辑层)
  三文件冻结 + Dataset拉逻辑层 = 最简
  不需要运行时rw, 只读拉取够用
```

**两个选择都对**:
- hermes Space需要runtime rw (改逻辑不git push HF repo) → Bucket
- memgraph Space只需boot拉取 → Dataset

## 5. HF旧persistent storage正在弃用 (非Bucket)

GitHub issue #3806 (2026-02-13), HF官方员工 @Wauplin 回复:
> "persistent storage is currently being a slowly deprecated feature (nothing announced yet) so I wouldn't start building on it"

**注意**: 这指的是旧的persistent storage (/data folder as overlay mount), 不是Storage Buckets产品。Bucket GA 2026-03-10, 是新产品, 不在弃用范围。当前hermes Space的/data是Bucket FUSE mount, 不是旧persistent storage。

## 6. CLI 常用命令

```bash
# Bucket CLI
hf buckets create my-bucket [--private]
hf buckets list                          # 返 SIZE/TOTAL_FILES/PRIVATE
hf buckets cp ./file hf://buckets/user/bucket/path
hf buckets sync ./local hf://buckets/user/bucket/data  # rsync-like增量
hf buckets sync ./local hf://buckets/user/bucket/data --delete  # 删远端多余
hf buckets sync ./local hf://buckets/user/bucket/data --dry-run  # 预览

# Python API
from huggingface_hub import sync_bucket, create_bucket
sync_bucket("./data", "hf://buckets/username/my-bucket/data")
sync_bucket("hf://buckets/username/my-bucket/data", "./data")  # 反向

# pandas/DuckDB直接读
import pandas as pd
df = pd.read_parquet("hf://buckets/username/my-bucket/data.parquet")
```

`hf sync` 是 `hf buckets sync` 的alias。huggingface_hub 1.0.1无bucket Python API故用CLI; 新版有 `sync_bucket()` 函数。

## 7. nexus选择Bucket的7维查证 (from ARCHITECTURE.md, 2026-08-06查证)

| 维度 | HermesFace/HuggingMes (Dataset) | nexus A方案 (Bucket) | 谁优 |
|---|---|---|---|
| WAL一致性 | 直接cp state.db (可能拷到WAL未落盘不一致) | `PRAGMA wal_checkpoint(TRUNCATE)` + `sqlite3 backup API` 一致快照 | nexus |
| Git膨胀 | upload_folder推Dataset, 每次同步多一个commit | hf buckets cp覆写, 无git history | nexus |
| Restore覆盖保护 | 无条件覆盖 (旧快照盖新数据) | 本地已有且非FORCE则跳过 | nexus |
| Shutdown半态 | 无处理 (推半写文件) | shutdown时不推 | nexus |
| Staging位置 | /tmp tmpfs (bug #35376) | /opt/data ext4 | nexus |
| FUSE写主库 | 本地盘 (对) | 本地盘移出/opt/data (同对) | 平 |
| 持久化粒度 | 整目录 (简单但无分层) | 分层: state.db→Bucket/业务表→Supabase+R2双写 | nexus |

7/7对标, 6优于双项目。逐文件不是因为"没想到整目录", 是因为三种文件三种持久化需求: state.db要WAL安全, config.yaml走template永覆盖, 业务表走Supabase结构化查询。

## 8. nexus设计动机: HF 2026-07平台锁死

| 雷区 | 操作 | 后果 |
|---|---|---|
| 雷区1 | git push/Factory reboot → rebuild | 付费墙, 免费号过不去 |
| 雷区2 | 改hardware | 收费且不可逆 |
| 雷区3 | pause后restart | 可能403永锁 |

唯一安全操作=Restart (用缓存镜像不触发rebuild) → 推导出"绝对静态化": HF repo内文件成"墓碑"永不改, 改逻辑只走Bucket+Restart。四层分离: 镜像层(GHCR)→环境层(HF repo三文件墓碑)→逻辑层(Bucket rw)→配置层(HF Secrets)。

## 9. state.db malformed根因与治本

**根因**: /data实为HF Bucket mount(FUSE/Xet) + litestream旁路进程并发读state.db WAL → SQLite corruption (官方雷: Tropy/OneDrive同步夹层SQLite不许他进程并发改文件)。

**治本**: ① HERMES_HOME移出bucket FUSE → /opt/data/.hermes (本地盘ext4/overlay无FUSE无旁路进程, WAL稳); ② litestream全段弃; ③ state.db本地盘写, Bucket纯当离线快照仓库(周期推/boot前cp拉), 两盘分开无并发改。

**代价**: 重启丢dashboard会话历史 (state.db ephemeral). 核心四表(agent_states/task_logs/long_memory/skills_index)在Supabase+R2双写不丢。

## 10. nexus方案 vs 三件套现状

| 概念 | nexus设计 | 三件套实际 | 变了吗 |
|---|---|---|---|
| hermes=入口/路由/调度 | ✅ | ✅ | 没变 |
| 统一记忆层 | Supabase表 | Mem0+Neon | 没变(换后端) |
| 逻辑层与镜像分离 | Bucket rw挂载 | HF Dataset snapshot | 没变(换载体) |
| 状态持久化 | Supabase+R2双写 | Neon+Bucket state.db | 没变(简化) |
| 永续铁律(三文件不动) | ✅ | ✅ | 没变 |
| HF rebuild付费墙规避 | ✅ | ✅ | 没变 |
| 多Space编排 | 4Space | 2Space | 缩了但概念同 |

概念保留, 基础设施该砍的砍了: R2(没用)、Supabase(换Neon)、GHCR(没用)、4Space编排(变2Space)。唯一遗留=hermes内置持久化脚本(非nexus加的, 是hermes镜像自带的)。

## 相关源码路径

- `/data/scripts/real-start.sh` — boot逻辑, mem0.json template生成
- `/data/scripts/home_files_uploader.py` — 周期推home文件到Bucket
- `/data/scripts/restore_home_files.py` — boot拉home文件从Bucket
- `/data/scripts/state_db_uploader.py` — 周期推state.db到Bucket (WAL checkpoint+backup API)
- `/data/scripts/restore_state.py` — boot拉state.db从Bucket
- `/opt/data/.hermes/mem0.json` — mem0配置 (ephemeral, 不在_FILES列表)
- `/opt/data/.hermes/state.db` — SQLite会话历史 (本地盘, WAL稳)

## 官方文档URL

- Storage Buckets: https://huggingface.co/docs/hub/storage-buckets
- Access Patterns: https://huggingface.co/docs/hub/storage-buckets-access
- Bucket Integrations: https://huggingface.co/docs/hub/storage-buckets-integrations
- Storage Limits: https://huggingface.co/docs/hub/en/storage-limits
- Blog (GA 2026-03-10): https://huggingface.co/blog/storage-buckets
- GitHub issue #3806 (persistent storage弃用): https://github.com/huggingface/huggingface_hub/issues/3806
