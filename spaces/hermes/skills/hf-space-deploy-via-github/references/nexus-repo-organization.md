# Nexus Private Repo Organization Pattern

When multiple HF Spaces share a single GitHub private repo as version-controlled source of truth, organize by `spaces/<name>/` directories. Each Space's directory is self-contained: frozen three files + hot-reloadable logic layer.

## Structure

```
i3t2y/nexus/                        # GitHub private repo (version control)
├── docs/                           # cross-Space architecture docs
│   ├── ARCHITECTURE.md
│   └── DEPLOYMENT.md
├── scripts/                        # shared ops scripts
│   ├── sync-logic-bucket.sh
│   └── sync-spaces.sh
├── spaces/                         # per-Space directories
│   ├── hermes/                     # hermes Space (sonoke/h)
│   │   ├── start.sh                # frozen three files
│   │   ├── scripts/                # hermes persistence scripts
│   │   └── ...
│   ├── memgraph/                      # mem0+lg Space (nmem/memgraph)
│   │   ├── Dockerfile              # frozen three files
│   │   ├── README.md               # HF frontmatter
│   │   ├── start.sh                # thin bootstrap (Dataset or Bucket pull)
│   │   ├── STATUS.md               # Space定位 + deployment chain
│   │   └── nworker/                # hot-reloadable logic layer
│   │       ├── entrypoint.sh       # real startup logic
│   │       ├── run.py              # patch orchestrator
│   │       ├── graph/__init__.py   # LangGraph worker code
│   │       ├── patches/            # runtime patches (10,20,30,40)
│   │       └── requirements.txt
│   └── langgraph/                  # (legacy/unused in三件套)
├── workers/                        # Cloudflare Workers (legacy)
├── docker/                         # base Dockerfile
└── .github/workflows/              # Actions: deploy-hf.yml, sync-check.yml
```

## Unified Bucket Deployment Chain

```
GitHub push (i3t2y/nexus/spaces/memgraph/nworker/)
  → GitHub Actions trigger
  → hf buckets sync ./spaces/memgraph/nworker hf://buckets/nmem/logic/nworker
  → memgraph Space restart (not rebuild)
  → start.sh: hf buckets sync hf://buckets/nmem/logic/nworker /app/worker
  → entrypoint.sh runs patched code
```

Version history lives in GitHub (git diff, blame, PRs). Runtime storage is Bucket (rw, no bloat, delete frees quota). Three files stay in HF Space git repo (frozen, never touched except rare base-image upgrades).

## Bucket Boot Pull (replacing snapshot_download)

When migrating a Space's logic-layer pull from Dataset to Bucket:

```bash
# OLD: Dataset pull (snapshot_download in start.sh)
python3 -c "
from huggingface_hub import snapshot_download
snapshot_download('nmem/nworker', repo_type='dataset', local_dir='/app/worker')
"

# NEW: Bucket pull (hf buckets sync in start.sh)
# Requires Bucket volume mounted at /data first
hf buckets sync hf://buckets/nmem/logic/nworker /app/worker
```

**Caveat**: Changing `start.sh` triggers a Space **rebuild** (not just restart), because start.sh is in the HF Space git repo (Dockerfile COPY). This is a one-time migration cost. After migration, all future logic-layer changes go via `hf buckets sync` (restart only, no rebuild).

**Alternative**: If avoiding rebuild entirely, add a wrapper in entrypoint.sh (also in logic layer) that pulls from Bucket before running. But entrypoint.sh itself still needs to come from somewhere on first boot — chicken-and-egg unless start.sh pulls it. In practice, the one-time rebuild is the cleaner path.

## STATUS.md Per-Space Doc

Each `spaces/<name>/` directory should include a `STATUS.md` with:
- Space定位 (what role, what URL, what backend)
- Three件套关系 (how it fits the triad)
- File structure map
- Persistence strategy (Bucket, Secrets, Neon)
- Deployment chain diagram

This serves as session-resume context: next agent clones repo, reads STATUS.md, understands the Space's full setup without reconstructing from memory.

## Docs Organization by Space Name

Organize `docs/` using the same Space-name pattern as `spaces/`:

```
docs/
├── hermes/          # sonoke/h Space docs (persistence, deploy, hot-reload)
├── memgraph/           # nmem/memgraph Space docs (mem0 auth/config, worker, keepalive)
├── shared/          # cross-Space docs (architecture, credentials, Bucket-vs-Dataset, PAT)
└── archive/         # outdated/legacy docs (old Nexus multi-Space designs, prior framework versions)
```

**Classification rule**: A document belongs in `<space>/` if it's specific to that Space's deployment. It belongs in `shared/` if it covers cross-cutting concerns (HF API, token management, storage comparison, architecture overview). Everything outdated goes to `archive/` — never delete (git history preserves, but archive signals "don't rely on this for current architecture").

**Skill references migration**: When a skill's `references/` directory contains documents relevant to a Space, copy them into the nexus repo's `docs/<space>/` as well. The skill references stay in place for runtime use; the nexus repo copies serve as version-controlled backup. Example: `mem0-server-auth.md` → both `skill/references/mem0-server-auth.md` AND `nexus/docs/memgraph/mem0-server-auth.md`.

## Bucket Python API (Verified 2026-08-17)

All operations verified working with `huggingface_hub` 1.26.0:

```python
from huggingface_hub import HfApi, Volume, create_bucket

api = HfApi(token=HF_TOKEN)

# 1. Create Bucket (private by default)
create_bucket("nmem/logic", private=True)
# → BucketUrl(url='https://huggingface.co/buckets/nmem/logic', ...)

# 2. List Buckets (only shows buckets owned by token's account)
buckets = api.list_buckets()
# → [BucketInfo(id='nmem/logic', private=True, ...)]

# 3. Mount Bucket to Space as rw Volume (triggers Space restart ~30s)
api.set_space_volumes(
    "nmem/memgraph",
    volumes=[Volume(type="bucket", source="nmem/logic", mount_path="/data")]
)
# → Space enters RUNNING_BUILDING, returns to RUNNING in ~30s

# 4. Verify volume mount
info = api.space_info("nmem/memgraph")
# info.runtime.volumes → [Volume(type='bucket', source='nmem/logic', mount_path='/data', read_only=False)]

# 5. Check current Space volumes (None = no volume mounted)
info = api.space_info("nmem/memgraph")
# info.runtime.volumes → None means no Bucket mounted
```

**Pitfall**: `api.upload_file()` does NOT support `repo_type='bucket'` — it only accepts `['model', 'dataset', 'space', None]`. For Bucket uploads, use `hf buckets cp` (CLI) or `hf buckets sync` (CLI for directories). There is no Python API equivalent for bucket file upload in huggingface_hub 1.26.

**Pitfall**: `hf buckets list` returns 404/empty when the token's `whoami` account differs from the Bucket namespace. Cross-account Bucket access requires the Bucket owner's token. Verify with `api.whoami()` first.

**CLI patterns** (verified):
```bash
# Single file upload
hf buckets cp /tmp/file.txt hf://buckets/nmem/logic/path/to/file.txt

# Directory sync (rsync-like, incremental)
hf buckets sync ./local-dir hf://buckets/nmem/logic/remote-dir

# List files in Bucket
hf buckets list hf://buckets/nmem/logic/ --recursive

# Sync with deletion of extraneous files
hf buckets sync ./data hf://buckets/nmem/logic/data --delete
```
