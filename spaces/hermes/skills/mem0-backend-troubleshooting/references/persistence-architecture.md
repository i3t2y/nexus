# Persistence Architecture: Four-Layer Model

When a user's pain is "stuff scattered everywhere, messy, hard to organize" and they're looking for "one thing that stores everything" — there is no single silver bullet. The solution is a layered architecture, each layer with the right tool for its data type.

## The Four Layers

| Layer | What lives here | Right tool | Wrong tool |
|-------|-----------------|------------|------------|
- **Credentials**: API keys, secrets, tokens, TOTP → Bitwarden-compatible vault (NodeWarden on CF Workers, Vaultwarden). For agent access to the vault, use **warden-mcp** (`icoretech/warden-mcp`) — a Bitwarden-compatible MCP server supporting stdio + HTTP/SSE modes. Agents search/read/create vault items via MCP tool calls (search, read password, read TOTP, manage attachments). Default redacted (secrets not exposed unless `reveal: true`). Per-profile bw state isolation. Works with NodeWarden/Vaultwarden because it uses the official `bw` CLI under the hood. **Limitation**: hermes doesn't currently support MCP client (uses its own plugin system), so warden-mcp only works with MCP-capable agents (Claude Code, OpenClaw, Codex). Hermes would need a custom plugin or `bw` CLI shell pipe in startup scripts.
| **Agent memory** | Agent-learned SOPs, user preferences, conversation context, facts | mem0 (OSS Supabase pgvector) or TencentDB-Agent-Memory (local SQLite, layered L0→L3) | Raw database tables (no embedding/extraction pipeline) |
| **Program data** | Structured experiment metrics, task queues, agent states, configs | SQL database (Supabase/Postgres) | Key-value vault (wrong shape for structured query) |
| **Knowledge base** | Personal notes, project docs, experiment writeups, technical specs | Obsidian (local markdown + bidirectional links) | Agent memory (agent-learned ≠ human-authored knowledge) |

## Key Distinctions (to avoid misassigning)

- **Credentials vs configs**: API keys → credential vault. Config YAML templates → git repo (with secrets redacted, pulled from vault at deploy). Don't put config structure in the vault — it's not designed for that.
- **Agent memory vs program data**: mem0 stores *what the agent remembers* (extracted facts, preferences, SOPs — semantic, unstructured, embedding-indexed). Supabase stores *what the program produces* (metrics, state, queues — structured, queryable, schema'd). Don't use mem0 as a metrics database; don't use Supabase as a fact store.
- **Agent memory vs knowledge base**: Agent memory is what the agent *learned* through conversation. Knowledge base is what the human *authored* through research/writing. The agent cannot organize your knowledge base for you — that requires human curation (directory structure, naming conventions, bidirectional links).

## Tool Comparison: Agent Memory Layer

| | mem0 (OSS) | TencentDB-Agent-Memory |
|---|---|---|
| **Architecture** | Flat vector store (all memories in one pgvector table) | Layered: L0 Conversation → L1 Atom → L2 Scenario → L3 Persona |
| **Storage** | Supabase pgvector (remote, networked, multi-agent native) | Local SQLite + sqlite-vec (zero-config, not network-shared) |
| **Retrieval** | Pure vector semantic search | BM25 + vector + RRF hybrid (jieba tokenization for Chinese) |
| **Debuggability** | Black box (vector scores only) | White-box (L2 scenes as Markdown, L3 persona as persona.md — human-readable) |
| **Token compression** | None (long-term memory only) | Mermaid symbolic memory for short-term context → −61% tokens |
| **Traceability** | Cannot drill back to original text | Full drill-down: L3 → L2 → L1 → L0 |
| **hermes integration** | Built-in plugin (OSS mode) | Official Hermes Gateway adapter + OpenClaw plugin |
| **Maturity** | Large community, many providers | Newer, roadmap incomplete (cross-agent migration pending) |
| **Remote sharing** | Native (Supabase is networked) | Not native (SQLite is local file) |

## Decision Framework: Where to Deploy

For HF free Space ephemeral-disk environments:
- **Credential layer**: Not on HF — vault should be persistent (NodeWarden on Cloudflare Workers is fine, it's not ephemeral)
- **Agent memory**: mem0 with Supabase (external storage survives restart). TencentDB-Agent-Memory's SQLite is local-only → better suited for a persistent local deployment, NOT HF ephemeral
- **Program data**: Supabase already external → survives restart
- **Knowledge base**: Local Obsidian only — never on HF ephemeral disk

For local deployments:
- All four layers can run locally. SQLite-based tools (TencentDB-Agent-Memory) are viable since disk is persistent.

## User Profile Insight

When a user says "I need one thing that connects and stores everything" — they're usually conflating 3-4 different data types that have different shapes, different access patterns, and different tools. The first step is always helping them separate the layers, not finding a single tool. No single tool covers all four layers well.
