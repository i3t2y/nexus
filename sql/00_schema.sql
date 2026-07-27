-- Nexus Supabase 基础 schema
-- 在 Supabase SQL Editor 执行。幂等（IF NOT EXISTS）。

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
    space_name  text        not null,   -- hermes / langgraph / claude / codex
    action      text        not null,
    status      text        not null,   -- pending / running / done / error
    created_at  timestamptz not null default now()
);
create index if not exists task_logs_thread_idx    on task_logs (thread_id);
create index if not exists task_logs_created_idx   on task_logs (created_at desc);
create index if not exists task_logs_status_idx   on task_logs (status);

-- 3. 长期记忆 --------------------------------------------------------
create table if not exists long_memory (
    key         text primary key,
    value       jsonb   not null default '{}'::jsonb,
    updated_at  timestamptz not null default now()
);
drop trigger if exists long_memory_touch on long_memory;
create trigger long_memory_touch before update on long_memory
for each row execute function touch_updated_at();

-- 4. 异步任务队列（供长任务轮询，可选）--------------------------------
create table if not exists task_queue (
    thread_id   text primary key,
    space       text        not null,
    payload     jsonb       not null default '{}'::jsonb,
    status      text        not null default 'queued',  -- queued/claimed/done/error
    result      jsonb,
    created_at  timestamptz not null default now(),
    claimed_at  timestamptz
);
create index if not exists task_queue_status_idx on task_queue (status, created_at);

-- RLS：服务端用 service_role 绕过；以下为 honestdefault，按需收紧
alter table agent_states enable row level security;
alter table task_logs   enable row level security;
alter table long_memory enable row level security;
alter table task_queue  enable row level security;

-- 5. Skills 索引（借鉴 Hermes Skills 备份思路）-----------------------
-- 记录可复用 Skill 的元数据；Skill 内容本身存 R2（nexus-skills 桶）。
create table if not exists skills_index (
    skill_name   text primary key,
    description  text,
    source       text,            -- hermes-auto / manual 等
    r2_key       text,            -- 对应 R2 对象 key
    usage_count  integer   not null default 0,
    last_used    timestamptz
);
create index if not exists skills_index_last_used_idx on skills_index (last_used desc);

-- 6. R2 备份快照登记（persist_to_r2.py 写入的元数据）------------------
create table if not exists backup_snapshots (
    id          bigserial primary key,
    table_name  text not null,
    r2_key      text not null,
    row_count   integer,
    created_at  timestamptz not null default now()
);
create index if not exists backup_snapshots_table_idx on backup_snapshots (table_name, created_at desc);

-- 7. Space 健康快照（保活探测结果留痕）-------------------------------
create table if not exists space_health (
    id          bigserial primary key,
    space       text not null,        -- hermes/langgraph/claude/codex/gateway
    status      text not null,        -- ok / down
    detail      text,
    created_at  timestamptz not null default now()
);
create index if not exists space_health_space_idx on space_health (space, created_at desc);

alter table skills_index     enable row level security;
alter table backup_snapshots enable row level security;
alter table space_health     enable row level security;
