# hermes Space — 三件套之一

## 定位
- **HF Space**: `sonoke/h` (云上大脑, 入口/路由/调度)
- **后端**: Neon Postgres (mem0 记忆 + 结构化四表, via memlg Space)
- **mem0 模式**: self_hosted → MEM0_HOST=https://nmem-memlg.hf.space

## 文件结构 (2026-08-17 重构)
- `space/` — 三文件 (Dockerfile + README.md + start.sh), 推 HF Space git repo
- `scripts/` — real-start.sh + persist_to_neon.py + restore_home_files.py + restore_state.py + home_files_uploader.py + state_db_uploader.py
- `app/` + `libs/` + `mcp/` + `skills/` — 逻辑层 (Bucket 挂载)
- `skills/` — 自定义 skills 备份

## 持久化架构
```
本地盘 /opt/data (ephemeral, 重启清盘)
  ├── .env / SOUL.md / memories/ / config.yaml  →  restore_home_files.py 启动从 Bucket 拉回
  ├── state.db (会话历史)                        →  restore_state.py 启动从 Bucket 拉回
  └── mem0.json (mem0 配置)                      →  ❌ 不在 home-backups 列表! 重启丢!
                                                   →  当前已改 mode=self_hosted, 重启后 MEM0_HOST 接管

Bucket sonoke/logic (rw 挂载 /data)
  ├── home-backups/    →  home_files_uploader.py 周期推 (600s)
  ├── state-backups/   →  state_db_uploader.py 周期推 (300s)
  └── scripts/ + app/ + libs/ + plugins/  →  逻辑层真源

Neon Postgres (持久化主路, 替代 Supabase+R2 旧链)
  ├── memories 表 (mem0 记忆, 通过 memlg Space)
  ├── agent_states / task_logs / long_memory / skills_index (persist_to_neon.py 主路 600s)
  └── task_queue / space_health (辅助)

Cloudflare R2 (灾备快照副路, 2026-08-18 恢复)
  └── supabase-snapshot/{四表}.json + _manifest.json (persist_to_r2.py 读 Neon 1800s)
```

## Supabase → Neon 迁移 (2026-08-17) + R2 副路恢复 (2026-08-18)
### 已完成
- ✅ mem0.json mode: oss → self_hosted (本地改, 重启后 MEM0_HOST 接管)
- ✅ persist_to_neon.py 写好 (替代旧 Supabase 主路, 直连 Neon 主路; persist_to_r2.py 2026-08-18 恢复作 Neon 读源 R2 副路快照, 见下条)
- ✅ real-start.sh 门控: SUPABASE_URL → POSTGRES_HOST
- ✅ neon-schema.sql 写好 (七表 DDL, 幂等, 无 backup_snapshots)
- ✅ 代码改动已推 nexus (commit f035a48)
- ✅ 2026-08-17 Neon Free 保活反策略 (persist_to_neon httpx /sql 短请求 + /health 不碰 Neon, commit 3fbd846)
- ✅ 2026-08-18 R2 副路恢复: persist_to_r2.py 读源 Supabase→Neon (HTTP /sql),
  与 Neon 主路双写, manifest-only 不进 DB; restore_from_r2.py 反向闭环改 Neon 写回

### 待执行 (用户手动)
1. **Neon Console**: 执行 `memgraph/docs/neon-schema.sql` (建七表)
2. **hermes Space Secrets**: 加 POSTGRES_HOST/PORT/USER/PASSWORD/DB (Neon 连接信息, 主路+R2 副路共用)
3. **hermes Space Secrets**: 加 R2_ENDPOINT/R2_ACCESS_KEY_ID/R2_SECRET_ACCESS_KEY/R2_BUCKET (副路灾备)
4. **hermes Space Secrets**: 删 SUPABASE_URL/SERVICE_ROLE_KEY/ANON_KEY/DB_URI + MEM0_PG_URI
5. **hermes Space Restart**: 让 mem0.json self_hosted + Neon 主路 + R2 副路全部生效

### 旧数据
- Supabase hermes_mem0 表 (~54行) 不迁移, 留在 Supabase 不删, 以后需要再导
- Supabase 结构化四表 (agent_states 等) 如有数据也留 Supabase

## 关键发现
- **mem0.json mode=oss 之前一直覆盖 MEM0_HOST** → hermes mem0 实际走 Supabase pgvector
  而非预期的 memlg Space → Neon。原因是 _load_config 的 update filter: `v is not None and v != ""`
  让 file 的 mode=oss 覆盖了 env 的 MEM0_MODE=platform, 路由 oss > host 优先。
- **mem0.json 不在 home-backups _FILES 列表** → 重启清盘后丢 → 回退到默认配置。
  但正因如此, 改成 self_hosted 后重启反而正确 (不会再被 oss 覆盖)。
