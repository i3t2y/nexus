# Free-Tier Vector Database Alternatives for mem0 (No VPS)

When Supabase free quota is a concern (500MB, 2 projects, 7-day inactivity pause), evaluate alternative free-tier vector DB providers for the mem0 server deployment.

## Supabase Free Tier Limits (2026)

| Resource | Limit | Actual usage (verified 2026-08-15) | Usage % |
|----------|-------|-----------------------------------|---------|
| Database storage | 500MB | **11 MB** (8 tables total) | **2.2%** |
| Projects | 2 active | 1 shared with hermes | 50% |
| Connections | 60 direct / 200 pooler | ~5-10 per server instance | 2.5% |
| Bandwidth | 5GB/month | <1GB typical | <20% |
| Inactivity pause | 7 days no activity → auto-pause | hermes daily use = never pauses | 0% risk |
| pgvector | ✅ included | Required by mem0 | — |

**Per-memory cost (verified)**: 12.7 KB average (8 KB 2048-dim vector + ~4.7 KB payload/index overhead). At this rate:
- 1,000 memories ≈ 12.4 MB (2.5% of 500MB)
- 10,000 memories ≈ 124 MB (25% of 500MB)
- Capacity ceiling ≈ **~40,000 memories** before hitting 500MB

**At ~10 memories/day, that's 11 years of headroom.** Storage is not the bottleneck; the **7-day inactivity pause** is the only real risk — and hermes's daily use keeps Supabase active (天然保活).

**Verdict**: Supabase free tier is **massively sufficient for mem0 in terms of storage** (11 MB / 500 MB = 2.2%). However, user decided (2026-08-15) to **switch to Neon** anyway to eliminate the 7-day inactivity pause risk entirely. Neon's scale-to-zero auto-wakes in ~500ms (no manual Restore) vs Supabase's full project pause requiring dashboard login. See `references/neon-keepalive-architecture.md` for the Neon + cron-job.org keepalive design. Supabase's 11 MB of existing data can stay or migrate.

## Alternative Free-Tier Vector DBs

### 1. Neon (neon.tech) — Postgres + pgvector
- **Free**: 0.5GB storage, 1 project, pgvector native
- **Pause**: Scale-to-zero on inactivity (similar to Supabase)
- **pgvector**: ✅ native
- **Connection pooler**: Built-in
- **Compatibility**: mem0 `pgvector` provider works directly (same protocol)
- **Verdict**: Direct drop-in replacement for Supabase. Same pgvector, same mem0 config, just different connection string. Not better than Supabase for this use case.

### 2. Qdrant Cloud (qdrant.tech)
- **Free**: 1GB storage, 1 cluster, no credit card
- **Pause**: Does not auto-pause (always-on free tier)
- **pgvector**: ❌ Qdrant is its own vector DB, not Postgres
- **mem0 compatibility**: ✅ mem0 has `qdrant` vector_store provider (`mem0/vector_stores/qdrant.py`)
- **Advantage**: 1GB > Supabase 500MB, no inactivity pause
- **Trade-off**: mem0 server's app-state tables (users, api_keys, settings, request_logs) still need Postgres — Qdrant only handles vector storage. Would need Postgres (Neon/Supabase) for app state + Qdrant for vectors = two services.

### 3. Pinecone (pinecone.io)
- **Free**: 2GB storage, 1 index
- **Pause**: Does not auto-pause
- **pgvector**: ❌ proprietary vector DB
- **mem0 compatibility**: ✅ mem0 has `pinecone` vector_store provider
- **Advantage**: 2GB, no pause, no Postgres dependency
- **Trade-off**: Same as Qdrant — app-state tables still need Postgres. Also, Pinecone free tier is "Starter" plan (serverless, limited regions).

### 4. Zilliz Cloud (Milvus)
- **Free**: 5GB storage, 1 cluster
- **Pause**: Does not auto-pause
- **pgvector**: ❌ Milvus is its own vector DB
- **mem0 compatibility**: ✅ mem0 has `milvus` vector_store provider
- **Advantage**: 5GB >> Supabase 500MB
- **Trade-off**: Same as Qdrant — app-state tables still need Postgres.

### 5. Turso (turso.tech)
- **Free**: 9GB, 500 databases, based on libSQL (SQLite fork)
- **pgvector**: ❌ not supported. Has experimental vector search.
- **mem0 compatibility**: ❌ No direct mem0 provider for Turso/libSQL
- **Verdict**: Not suitable for mem0.

### 6. ElephantSQL
- **Free**: 20MB Tiny Turtle, 5 connections
- **pgvector**: ❌ not supported on free plan
- **Verdict**: Not suitable.

### 7. Railway / Render / Aiven
- **Railway**: $5 credit/month, pgvector ✅ — but credit runs out and pauses
- **Render**: PostgreSQL 90-day trial only, not permanent
- **Aiven**: No free Postgres
- **Verdict**: All either temporary or credit-limited, not reliable long-term free.

### 8. CockroachDB Cloud
- **Free**: 10GB, 1 cluster, pgvector support (24.1+)
- **Compatibility**: Uncertain — mem0 uses psycopg + pgvector extension, CockroachDB has partial Postgres compat. May need testing.
- **Verdict**: Promising but unverified. Needs `pip install psycopg` + connection string test.

## Decision Framework

| Need | Recommended |
|------|-------------|
| Simplest, maintain current setup | **Stay on Supabase** — 500MB is 50x more than needed, 7-day pause is the only risk |
| More storage, no inactivity pause | **Qdrant Cloud** (1GB, always-on) + keep Supabase for app-state tables (Postgres still needed) |
| Maximum free storage | **Zilliz/Milvus** (5GB) + Supabase for app-state |
| All-in-one (vectors + app state) | **Neon** (0.5GB, pgvector) — same as Supabase, no clear advantage |
| Avoid Postgres entirely | **Pinecone** (2GB) — but mem0 server still needs Postgres for auth/settings tables |

**Bottom line**: mem0 server architecture requires Postgres regardless (for auth/api_key/settings/request_logs tables via Alembic migrations), even if you use a separate vector DB for the memory vectors. This means **Supabase/Neon stays necessary** and adding Qdrant/Pinecone only helps if you hit the 500MB storage ceiling (unlikely — 1000 memories = ~10MB).

**Final decision (2026-08-15)**: Switch to Neon (multi-project for额度叠加) with cron-job.org keepalive. Supabase storage was sufficient (2.2% usage) but the 7-day pause risk was the deciding factor. Neon's scale-to-zero + cron-job.org every-4-min ping = zero manual intervention ever. Dedicated cron-job.org account for keepalive only (not mixed with other uses). See `references/neon-keepalive-architecture.md` for implementation.

### Keepalive options (if 7-day pause becomes real)

| Method | Cost | Complexity | Notes |
|--------|------|------------|-------|
| hermes daily use | 0 | None | 天然保活 — hermes读写 mem0 每天都连 Supabase |
| Supabase dashboard Restore | 0 | Manual | One click, data safe, only needed after 7+ days idle |
| Cron `SELECT 1` every 5 days | 0 | Low | `0 */120 * * * curl -s <supabase-rest>?select=1` or a hermes cronjob calling `mem0_search` |
| UptimeRobot ping | 0 | Low | External HTTP monitor hitting a Supabase Edge Function or REST endpoint |

## mem0 Supported Vector Store Providers (verified 2026-08-15)

mem0 pip package includes these vector store providers (in `mem0/vector_stores/`):

```
azure_ai_search, azure_mysql, baidu, cassandra, chroma, databricks,
elasticsearch, faiss, langchain, milvus, mongodb, neptune_analytics,
opensearch, pgvector, pinecone, qdrant, redis, s3_vectors, supabase,
turbopuffer, upstash_vector, valkey, vertex_ai_vector_search, weaviate
```

Key ones with free cloud tiers: pgvector (Supabase/Neon), qdrant, pinecone, milvus, chroma, weaviate, upstash_vector, supabase.
