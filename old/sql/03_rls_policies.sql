-- Nexus Supabase RLS 显式策略补丁
-- 2026-08-02 深核盲区 2 结论。在 00_schema.sql 后执行(幂等)。
--
-- 实证(严核):
--   - 7 表全 ENABLE ROW LEVEL SECURITY 但 0 CREATE POLICY → anon_key deny-by-default 全锁
--   - 现役靠 service_role 绕 RLS 全开,RLS 形同虚设
--   - nexus 4 Space 互调用 NEXUS_API_KEY 自建网关认证(不映射 Supabase JWT 行身份),
--     且 anon_key 是项目全局密钥(非按行身份)→ RLS 行身份(auth.uid())无适用对象,
--     "anon_key+RLS 按 space_name 列限流"无安全收益(space_name 可伪造)。
--   - 故不采 plan K2 "下游 anon_key+RLS 行级"路(不可行)。保留: 全 Space 共享 service_role
--     绕 RLS 全开(模型 A),RLS 仅作纵深兜底(anon_key 彻底禁写,垄断全表写权于 service_role)。
--
-- 策略(纵深): anon_key = 公共只读角色(真公共表留口,业务表全禁)。service_role 绕 RLS 全权。
--   - 业务表(agent_states/task_logs/task_queue/long_memory): anon 全禁(读+写皆 false)
--   - 元数据表(skills_index/backup_snapshots): anon 全禁
--   - 探活表(space_health): anon 只读(便于外部探活查公开状态，写仅 service_role)
--
-- 风险接受: 单 service_role 泄漏 → 全表坏。纵深靠 Space 级 RBAC env 开关 + NEXUS_API_KEY 网关认证拦外。
-- 真要按行限流须改架构: 4 Space 各发独立 Supabase JWT(ISS)或各自 anon_key+RLS row-claim,
--   但 HF Space 间机器对外部调用无 ISO 身份——非本计划范围,留将来。

-- 业务表: anon 全禁(读+写皆 false)-------------------------------------
drop policy if exists agent_states_anon_deny on agent_states;
create policy agent_states_anon_deny on agent_states
    for all to anon, authenticated
    using (false) with check (false);

drop policy if exists task_logs_anon_deny on task_logs;
create policy task_logs_anon_deny on task_logs
    for all to anon, authenticated
    using (false) with check (false);

drop policy if exists task_queue_anon_deny on task_queue;
create policy task_queue_anon_deny on task_queue
    for all to anon, authenticated
    using (false) with check (false);

drop policy if exists long_memory_anon_deny on long_memory;
create policy long_memory_anon_deny on long_memory
    for all to anon, authenticated
    using (false) with check (false);

-- 元数据表: anon 全禁---------------------------------------------
drop policy if exists skills_index_anon_deny on skills_index;
create policy skills_index_anon_deny on skills_index
    for all to anon, authenticated
    using (false) with check (false);

drop policy if exists backup_snapshots_anon_deny on backup_snapshots;
create policy backup_snapshots_anon_deny on backup_snapshots
    for all to anon, authenticated
    using (false) with check (false);

-- 探活表: anon 只读(允许史探活),写仅 service_role-----------------------
drop policy if exists space_health_anon_read on space_health;
create policy space_health_anon_read on space_health
    for select to anon, authenticated
    using (true);

drop policy if exists space_health_anon_no_write on space_health;
create policy space_health_anon_no_write on space_health
    for insert to anon, authenticated
    with check (false);
