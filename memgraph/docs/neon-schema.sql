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

-- 4. 异步任务队列 ----------------------------------------------------
create table if not exists task_queue (
    thread_id       text primary key,
    space           text        not null,
    payload         jsonb       not null default '{}'::jsonb,
    status          text        not null default 'queued',
    result          jsonb,
    idempotency_key text        unique,
    created_at      timestamptz not null default now(),
    claimed_at      timestamptz,
    constraint task_queue_status_chk check (status in ('queued','claimed','done','error'))
);
create index if not exists task_queue_status_idx on task_queue (status, created_at);

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

-- 注意: backup_snapshots 表不需要了 (R2 砍掉, Neon 本身就是持久化)
-- 注意: 不需要 RLS (Neon 没有 Supabase 的 RLS 机制, 靠连接串权限控制)
