-- Nexus 七表 schema (Neon 版, 2026-08-17)
-- 在 Neon Console SQL Editor 执行。幂等 (IF NOT EXISTS)。
-- 从 old/sql/00_schema.sql 迁移，砍掉 RLS (Neon 无 RLS 机制) + backup_snapshots (R2 砍掉)

-- 1. Agent 状态表 ----------------------------------------------------
create table if not exists agent_states (
    thread_id   text primary key,
    state       jsonb   not null default '{}'::jsonb,
    updated_at  timestamptz not null default now()
);
create index if not exists agent_states_updated_at_idx on agent_states (updated_at desc);

-- 自动维护 updated_at
create or replace function touch_updated_at()
returns trigger language plpgsql as $$
begin
    new.updated_at = now();
    return new;
end $$;

drop trigger if exists agent_states_touch on agent_states;
create trigger agent_states_touch before update on agent_states
for each row execute function touch_updated_at();

-- 2. 任务日志 --------------------------------------------------------
create table if not exists task_logs (
    id          bigserial primary key,
    thread_id   text        not null,
    space_name  text        not null,
    action      text        not null,
    status      text        not null,
    request_id  text,
    created_at  timestamptz not null default now()
);
create index if not exists task_logs_thread_idx    on task_logs (thread_id);
create index if not exists task_logs_created_idx   on task_logs (created_at desc);
create index if not exists task_logs_status_idx    on task_logs (status);
create index if not exists task_logs_request_idx   on task_logs (request_id);

-- 3. 长期记忆 --------------------------------------------------------
create table if not exists long_memory (
    key         text primary key,
    value       jsonb   not null default '{}'::jsonb,
    updated_at  timestamptz not null default now()
);
drop trigger if exists long_memory_touch on long_memory;
create trigger long_memory_touch before update on long_memory
for each row execute function touch_updated_at();

-- 4. 异步任务队列 (memlg 专属; hermes 不双写) -------------------------
-- 2026-08-18: 按写端扁平表对齐, 推翻旧 thread_id/payload/queued|claimed 体系
--   (旧体系与 graph/__init__.py 自撜表字段全异, INSERT 列不存在 + status 违 check 报错)
--   统一形状: task_id PK / task 文案兜底 / user_id / status[pending|running|completed|failed]
--   + kind/input/output/attempts/updated_at (Stage A, 供 Stage B 本机桥 WHERE kind='npc')
-- 注意: 若旧库已跑过 thread_id 那套, 先 DROP TABLE task_queue; 再跑本段 (新库直接 CREATE OK)
create table if not exists task_queue (
    task_id      text primary key,
    task         text,                    -- 人读摘要兜底; 正式结构进 input jsonb
    user_id      text,
    status       text not null default 'pending',
    -- pending | running | completed | failed (保留写端 enum, 不迁 queued|claimed)
    kind         text not null default 'generic',
    -- generic | graph | npc | claude_code | pi
    -- (2026-08-18 Gork 裁决: kind=workbuddy_npc 路废, WorkBuddy IM 桌面出口移除;
    --  异地编码走 kind=npc → CNB CodeBuddy 云端 Agent, 非本机桥;
    --  kind=graph 两路并存: 同步 plugin route_langgraph 短图 + 异步 task_queue+SkipLocked poll 长图[Stage B 增强])
    input        jsonb       not null default '{}'::jsonb,
    output       jsonb,
    result       text,                    -- 兼容旧读端; 正式结果优先 output
    attempts     int         not null default 0,
    created_at   timestamptz default now(),
    updated_at   timestamptz default now(),
    completed_at timestamptz
);
create index if not exists idx_task_queue_status_kind on task_queue (status, kind);

drop trigger if exists task_queue_touch on task_queue;
create trigger task_queue_touch before update on task_queue
for each row execute function touch_updated_at();

-- 5. Skills 索引 -----------------------------------------------------
create table if not exists skills_index (
    skill_name   text primary key,
    description  text,
    source       text,
    r2_key       text,
    usage_count  integer   not null default 0,
    last_used    timestamptz
);
create index if not exists skills_index_last_used_idx on skills_index (last_used desc);

-- 6. Space 健康快照 --------------------------------------------------
create table if not exists space_health (
    id          bigserial primary key,
    space       text not null,
    status      text not null,
    detail      text,
    created_at  timestamptz not null default now()
);
create index if not exists space_health_space_idx on space_health (space, created_at desc);

-- 注意: backup_snapshots 表不需要 (R2 副路 manifest-only 不走 DB;
--   2026-08-18 R2 恢复作快照备份层,读源=Neon,元数据 sha256/bytes/rows 全放 R2
--   supabase-snapshot/_manifest.json,不倒退 Neon schema 加回 backup_snapshots)
-- 注意: 不需要 RLS (Neon 没有 Supabase 的 RLS 机制, 靠连接串权限控制)
