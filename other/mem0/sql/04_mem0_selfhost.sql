-- Nexus Mem0 self-host Supabase schema 补丁
-- 2026-08-10 落盘, 2026-08-17 从长期记忆笔记 nexus-mem0-codeside-done L20/L28 重建(Bucket 无副本,git 从未 commit)
-- 在 sql/00_schema.sql + sql/01_pgvector.sql + sql/03_rls_policies.sql 后执行(幂等)。
--
-- 重建依据(nexus-mem0-codeside-done-2026-08-10.md 原文):
--   "仅 CREATE EXTENSION IF NOT EXISTS vector + RLS 兜底(do $$ 块,表存在后挂 anon deny +
--    service_role 全许)。不预建表——mem0 pgvector 后端(mem0/vector_stores/pgvector.py
--    create_col)自建表 id UUID PK + vector(1024) + payload JSONB(列名固定),
--    我早期预建 id text/embedding/metadata/match_mem0 会列名不匹配失败,已改让 mem0 自建。"
--
-- 注意: 此为旧 mem0-as-hermes-plugin 路径(选项A 删之前的激活链配套)。
--       hermes 已重构为 Supabase→Neon, mem0 移到 memgraph 独立 Space 跑 server 模式。
--       本 SQL 仅留作改回 Supabase 旧路径时的复原凭证, 非现役实现。
--       mem0 pgvector 后端自建表 schema 真源在 mem0 库本身(vector_stores/pgvector.py),
--       非本文件定义(本文件仅启 extension + RLS 兜底)。

-- 1. pgvector 扩展(若已由 sql/01_pgvector.sql 启过则跳过) ------------------
create extension if not exists vector;

-- 2. RLS 兜底: mem0 pgvector 后端 create_col 自建 hermes_mem0 表后挂 anon deny --
--    mem0 后端用 service_role 连接(MEM0_PG_URI = SUPABASE_DB_URI), 绕 RLS 全开写权。
--    anon_key 对 hermes_mem0 全禁(与 03_rls_policies.sql 纵深策略一致)。
--    do $$ 块: 表可能未建(mem0 首跑前), 故先判断存在再挂 policy, 幂等可重跑。
do $$
begin
  if exists (select 1 from information_schema.tables where table_name = 'hermes_mem0') then
    -- anon deny (纵深层兜底, 与 7 表策略统一)
    drop policy if exists hermes_mem0_anon_deny on hermes_mem0;
    execute 'create policy hermes_mem0_anon_deny on hermes_mem0
             for all to anon, authenticated
             using (false) with check (false)';
  end if;
end $$;

-- 注: service_role 绕 RLS 全开, 无需显式 policy。
--     真要按行限流须改架构(见 03_rls_policies.sql 风险接受段), 非本文件范围。
