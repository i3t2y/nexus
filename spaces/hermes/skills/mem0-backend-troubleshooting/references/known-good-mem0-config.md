# Known-Good mem0.json Configuration

Verified working 2026-08-14: `mem0_add` + `mem0_search` both succeeded end-to-end (score 0.689). This is the config that was running when the full NIM → 智谱 → pgvector chain was confirmed operational.

## Working Configuration (field-level)

```
oss.embedder:
  provider: openai
  config:
    model: nvidia/nemotron-3-embed-1b                  # NIM's only symmetric working model
    api_key: <nvapi-...>                               # NVIDIA API key (70 chars)
    openai_base_url: https://integrate.api.nvidia.com/v1  # NO trailing slash
    # embedding_dims: DO NOT SET                       # NIM rejects the dimensions param
    # encoding_format handled by mem0 internally

oss.llm:
  provider: openai
  config:
    model: glm-4.7-flash                               # 智谱 ZAI
    api_key: <zai-...>                                 # 智谱 API key
    openai_base_url: https://api.z.ai/api/paas/v4      # NO trailing slash

oss.vector_store:
  provider: pgvector
  config:
    collection_name: hermes_mem0                        # pgvector table name
    connection_string: postgresql://postgres.<project-ref>:<pwd>@aws-0-us-west-1.pooler.supabase.com:5432/postgres?sslmode=require
                                                        # ↑ IPv4 session pooler, NOT db.<project>.supabase.co:6543 (IPv6)
    embedding_model_dims: 2048                          # matches nemotron-3-embed-1b output
    hnsw: false                                         # HNSW 2000-dim limit, nemotron is 2048
    sslmode: require

top-level:
  mode: oss
  agent_id: <your agent id>
```

## Critical Do-Nots

| Field | Wrong value | Symptom |
|-------|-------------|---------|
| `embedder.config.embedding_dims` | Any int (e.g. 2048) | mem0 passes `dimensions=2048` to NIM → 400/500 |
| `embedder.config.model` | `baai/bge-m3` | 500 (account unavailable) |
| `embedder.config.model` | `nvidia/nv-embedqa-e5-v5` | 400 (requires `input_type`, mem0 doesn't send it) |
| `vector_store.config.hnsw` | `true` | `ProgramLimitExceeded: >2000 dimensions for hnsw index` |
| `vector_store.config.connection_string` | `db.<project>.supabase.co:6543` | Network is unreachable (IPv6) |
| `llm.config.openai_base_url` | trailing `/` (`.../v4/`) | Not fatal but can cause double-slash 404 on some SDKs |

## Template File (for restart persistence)

`/data/scripts/mem0.json.template` is envsubst'd on boot if `mem0.json` absent. The template uses these vars:
- `$MEM0_PG_URI` — aliased from `$SUPABASE_DB_URI` in `real-start.sh` L130-178
- `$NVIDIA_API_KEY` — NIM embedder key
- `$ZAI_API_KEY` — 智谱 LLM key

**Any fix to the live `mem0.json` must be backported to the template**, or it's lost on Space Restart.

## How to Verify (post-fix)

1. Check `agent.log` for `Memory provider 'mem0' activated` (no errors after)
2. Check `errors.log` for absence of `500`, `extra_forbidden`, `Network is unreachable`
3. Run `mem0_add` with a test string → expect `{"result": "Fact stored."}`
4. Run `mem0_search` for semantically related query → expect `{"results": [...], "count": N}`
5. Check Supabase: `SELECT count(*) FROM hermes_mem0` → should be > 0
