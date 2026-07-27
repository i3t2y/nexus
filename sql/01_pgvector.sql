-- pgvector 扩展（向量搜索用）。如不需要可跳过。
create extension if not exists vector;

-- 示例：长期记忆带 embedding
create table if not exists memory_vectors (
    id          bigserial primary key,
    key         text not null,
    content     text,
    embedding   vector(1536)  -- 维度依模型；OpenAI text-embedding-3-small=1536
);

-- HNSW 索引
create index if not exists memory_vectors_hnsw
on memory_vectors using hnsw (embedding vector_cosine_ops);

-- 邻近检索示例：
-- select key, content
-- from memory_vectors
-- order by embedding <=> '[...]'::vector
-- limit 5;
