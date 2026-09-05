# HF Dataset Hot-Reload Pattern

When an HF Space runs multiple components (e.g., mem0 server + LangGraph worker), the three-file Space repo (Dockerfile/README.md/start.sh) should stay frozen — every push triggers a Docker rebuild, and frequent rebuilds risk free-tier bans. But frequently-changed code (LangGraph workflow nodes, business logic) needs a way to update without rebuilds.

## Solution: HF Dataset as hot-reloadable code store

```
HF Space repo (frozen, three files)           HF Dataset repo (hot, frequent updates)
├── Dockerfile                                ├── graph/
├── README.md                                 │   ├── __init__.py
└── start.sh  ← pulls Dataset on boot         │   ├── nodes.py       ← frequent changes
                                              │   ├── tools.py        ← frequent changes
                                              │   └── workflow.py     ← frequent changes
                                              └── requirements.txt
```

Naming convention: Space can be named anything (e.g., `memg`, `0`). Dataset named after the worker function (e.g., `nworker`). Neither name has to match the GitHub repo name.

## Why HF Dataset (not Bucket)

| | HF Dataset | HF Bucket |
|---|---|---|
| Python SDK | `huggingface_hub.snapshot_download` — native, one line | `hf buckets cp` — lower-level |
| Version history | Commit-based, rollback possible | Object store, no history |
| Upload | `huggingface-cli upload` — file-level granularity | `hf buckets cp` — bulk only |
| start.sh integration | `snapshot_download(repo_type='dataset')` | Manual HTTP GET per file |
| Already installed | `huggingface-cli` ✅ (1.26.0+) | Needs `hf` CLI buckets subcommand |

Dataset is better for code because: version history (rollback), file-level upload (not bulk), and `huggingface_hub` Python SDK integration in start.sh.

## Implementation

### 1. Create Dataset repo

```bash
# On HF, create a Dataset repo (private, not Space, not Model)
# Use the HF account that owns the Space
huggingface-cli repo create <hf-user>/<worker-dataset-name> --type=dataset --private
# Example: huggingface-cli repo create nmem/nworker --type=dataset
```

### 2. start.sh: pull Dataset code on boot (no rebuild)

Add to start.sh, BEFORE uvicorn launch:

```bash
# Pull hot-reloadable worker code from HF Dataset (no Space rebuild)
python3 -c "
from huggingface_hub import snapshot_download
snapshot_download('<hf-user>/<worker-dataset-name>', repo_type='dataset', local_dir='/app/worker')
print('[start] worker code pulled from Dataset')
"
pip install -r /app/worker/requirements.txt 2>/dev/null || true
```

This runs on every container start (including Restart), pulling the latest code. No Docker rebuild needed — only container restart, which HF does for free.

### 3. Update worker code (frequent, no rebuild)

```bash
# Change nodes.py locally, push single file to Dataset
huggingface-cli upload <hf-user>/<worker-dataset-name> graph/nodes.py graph/nodes.py --repo-type=dataset

# Space picks up new code on next restart (cron-job.org ping triggers /health → restart)
```

### 4. Architecture: LangGraph uses Mem0 as node/tool

```python
# In worker/graph/__init__.py
# LangGraph calls mem0 server via HTTP (localhost, same container)
# See templates/langgraph_worker_skeleton.py for full working code
```

LangGraph on free HF = **瘦编排 (thin orchestrator)**: state graphs, conditional edges, retry, and Neon checkpoint all work. Large parallel subgraphs, in-graph heavy coding agents, and hour-long runs do NOT — offload heavy work to external compute (Claude Code, Codex). HF Space orchestrates + calls APIs only.

### 5. Persistence: logs go to Neon, not local files

| Content | Persist? | How |
|---|---|---|
| mem0 vector memory | ✅ | Neon pgvector |
| LangGraph checkpoint | ✅ | Neon (graph state table) |
| LangGraph run logs | ✅ | Neon task_logs (psycopg, NOT local files) |
| SQLite history.db | ❌ | LLM call history; doesn't affect search |
| Worker code | ✅ | HF Dataset (snapshot_download on boot) |

**Rule: ephemeral disk is scratch only.** State goes to Neon or Dataset. No local file is truth.

## Lifecycle comparison

| Action | Three-file change | Dataset code change |
|---|---|---|
| Edit | Edit Dockerfile/start.sh | Edit nodes.py |
| Push | `git push` GitHub → Actions → HF Space git | `huggingface-cli upload` to Dataset |
| Triggers rebuild | ✅ Docker rebuild (slow, risks ban if frequent) | ❌ No rebuild |
| Takes effect | After rebuild completes (~2-5 min) | After container restart (~10-30 sec) |
| Version history | GitHub git (full) | HF Dataset commits (full) |
| Frequency budget | Rare (1-2x per quarter max) | Unlimited |

## When to use this pattern

- Space runs multiple services (mem0 + LangGraph, or any multi-component setup)
- One component changes frequently (workflow logic, business rules)
- Three-file budget is exhausted — can't afford more rebuilds
- User explicitly wants "三文件不动" (three files frozen, dependency on external code store)

## When NOT to use

- Single-service Space (just a mem0 server, no worker) — three files are enough
- Code never changes after initial deploy — no need for hot-reload
- Changes are rare (quarterly) — just push to GitHub repo, let Actions deploy

## Template

For a ready-to-use LangGraph worker skeleton (StateGraph + mem0 HTTP client + FastAPI router), see `templates/langgraph_worker_skeleton.py`. Upload it to the Dataset repo as `graph/__init__.py`.
