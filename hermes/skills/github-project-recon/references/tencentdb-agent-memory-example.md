# Reference example: TencentDB-Agent-Memory reconnaissance

Worked example for the parent skill. Captures the *evidence trail* of one real
session so the next agent can see what "verify each claim against the actual
file" looks like in practice, and reuse the endpoints/paths that worked.

> Repo: `github.com/TencentCloud/TencentDB-Agent-Memory`
> Branch (recorded): `feat/server_team` (NOT main — fetched wrong branch first)
> License: MIT (read from LICENSE file, not the badge)
> Asked: is it a "connect-everything, save-everything unified memory layer"?
>       is it bound to Tencent-Cloud DB, or self-hostable?

## Step 1 — recovered the path

The URL as given (`github.com/TencentDB-Agent-Memory` — no org) returned 404.
Search recovered the canonical path:

```
GET https://api.github.com/search/repositories?q=TencentDB+Agent+Memory
→ items[0].full_name  = "TencentCloud/TencentDB-Agent-Memory"
→ items[0].default_branch = "feat/server_team"
→ items[0].fork = false   (real, not a mirror)
→ items[0].license.spdx_id = "NOASSERTION"  → read LICENSE → MIT
→ items[0].stargazers_count = 21765
```

## Step 2 — claims extracted from README (held as claims, not facts)

- "team-level memory hub … four reusable memory assets (Chat Memory, Skill,
  LLM-Wiki, Code-Graph)"
- L0 Conversation → L1 Atom → L2 Scenario → L3 Persona four-layer distillation
- "Portable & multi-Agent compatible" — cross-framework, multi-agent shared
- Visibility: private / team / restricted / agent (ACL model)
- Acknowledgements (high-signal, not marketing): CodeGraph module reuses code
  from `colbymchenry/codegraph`; Skill management reuses Hermes Agent skill
  code + extends it; Wiki inspired by Karpathy's LLM Wiki
- README name/badge "TencentDB" → CLAIM that it binds Tencent-Cloud DB (unverified)
- Self-comparison table is vs Chat-History and Standard RAG (no mem0 mention)

## Step 3 — claims verified against actual files

| Claim | Proven by | Verdict |
|---|---|---|
| Storage backend is SQLite, zero external DB | `MemoryCore/tdai-gateway.standalone.yaml`: `storeBackend: "sqlite"`, `stateBackend: "local"`, `data.baseDir: ~/.memory-tencentdb/memory-tdai`; MemoryCore/README.md: "Uses SQLite, local files, and in-process state. Requires no external service other than an LLM API." | CONFIRMED |
| Optional cloud deps (Redis/TCVDB/COS/Mongo) | same yaml's *commented* `tcvdb:` block + `README.docker.md` "Service 模式(需要 Redis)" + `tdai-gateway.yaml` env `mongoUri`/`COS_*`/`VDB_*` | OPTIONAL, not required |
| L0 stored in SQLite | `INSTALL.md`: "L0 (raw dialogue) is captured into memory-core's SQLite" | CONFIRMED |
| Multi-agent sharing core feature | README "Portable & multi-Agent compatible" + ACL table | CONFIRMED, README claim corroborated by README body |
| "TencentDB" name = binds Tencent-Cloud DB | NO config file forces Tencent-Cloud; the only Tencent-cloud deps are inside commented blocks / service-mode template | REFUTED — name is branding |
| Distinct from mem0 | README has NO mem0 mention; positioned vs RAG/Chat-History | README provides no direct mem0 comparison |
| Deps reduced to bundles | `.env.example`: only two REQUIRED groups — `MEMORY_LLM_*` (internal) and `PROXY_UPSTREAM_*` (proxy forwards to) | CONFIRMED — one OpenAI-compat LLM API is the sole hard dep |

## Commands that worked

```bash
# Default-branch WARN: it was feat/server_team, not main. Get it from API first:
curl -s 'https://api.github.com/repos/TencentCloud/TencentDB-Agent-Memory' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["default_branch"])'

# Raw file fetches against that branch:
curl -sL "https://raw.githubusercontent.com/TencentCloud/TencentDB-Agent-Memory/feat/server_team/README.md"
curl -sL "https://raw.githubusercontent.com/TencentCloud/TencentDB-Agent-Memory/feat/server_team/MemoryCore/tdai-gateway.standalone.yaml" | grep -ivE '^#|^\s*$'
curl -sL "https://raw.githubusercontent.com/TencentCloud/TencentDB-Agent-Memory/feat/server_team/deploy/global-images/.env.example"
curl -sL "https://raw.githubusercontent.com/TencentCloud/TencentDB-Agent-Memory/feat/server_team/LICENSE"           # don't trust the badge
curl -sL "https://raw.githubusercontent.com/TencentCloud/TencentDB-Agent-Memory/feat/server_team/CHANGELOG.md" | head -10   # version / recency

# Directory listings:
curl -s "https://api.github.com/repos/TencentCloud/TencentDB-Agent-Memory/contents/"
curl -s "https://api.github.com/repos/TencentCloud/TencentDB-Agent-Memory/contents/MemoryCore"
curl -s "https://api.github.com/repos/TencentCloud/TencentDB-Agent-Memory/contents/deploy"
```

## One-line verdict the recon produced

MIT, self-hostable, SQLite by default (one OpenAI-compatible LLM API is the only
hard dependency); Tencent-cloud VDB/COS/Redis/Mongo are *optional* service-mode
extras, not required — the "TencentDB" name is branding, not a binding. Explicit
multi-agent sharing + L0–L3 distillation + Skill/Wiki/CodeGraph assets. Not a
general data lake; biased to coding-agent + team-asset-governance use.
