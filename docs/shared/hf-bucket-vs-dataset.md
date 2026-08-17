# HF Storage Bucket vs Dataset — Full-Dimension Comparison

Authoritative查证 (2026-08-17) of when to use HF Storage Bucket vs HF Dataset repo for Space persistence, synthesized from official HF docs (`huggingface.co/docs/hub/storage-buckets`, `/storage-buckets-access`, `/storage-buckets-integrations`, `/en/storage-limits`, blog `huggingface.co/blog/storage-buckets`), GitHub issue #3806 (HF staff @Wauplin comment on persistent storage deprecation), and nexus `docs/ARCHITECTURE.md` (2026-07 查证裁决).

## TL;DR Decision Rule

- Space needs **runtime rw** of files → **Bucket** (Dataset mounts read-only in Spaces)
- Just needs code at **boot time** → **Dataset** (simpler `snapshot_download`)
- Need **version history** of working files → **GitHub repo** (Bucket has no history; use GitHub as source of truth and sync to Bucket for runtime)
- Only needs to pull a finished dataset → **Dataset** (git versioning + community)

## Architecture: Unified Bucket Pattern (User's Choice)

```
GitHub 私库 (版本化真源, full git history)
    ↓ Actions trigger
HF Buckets (运行时存储, 无 git 膨胀, rw 挂载)
    ↓ hf buckets sync (增量)
HF Spaces (ephemeral compute, Bucket = /data)
    ↓ HF Secrets
Application (零文件持久化, config via env vars)
```

**Version separation**: GitHub = git history + diff + blame. Bucket = mutable runtime storage, no history bloat. Deleting files in Bucket frees quota immediately (unlike Dataset where git history retains all versions).

## Full-Dimension Comparison Table

| Dimension | HF Dataset (repo) | HF Storage Bucket |
|---|---|---|
| **底层 (Backend)** | git-based repo, tracks file history | Xet backend, S3-like object storage |
| **版本化 (Versioning)** | Full Git history (every upload = 1 commit) | None — mutable, overwrite-in-place |
| **写入 (Write)** | `api.upload_folder` / `upload_file` (git commit + push) | `hf buckets sync` / `hf buckets cp` (rsync-like) |
| **读取 (Read)** | `snapshot_download(repo_type="dataset")` | Volume mount `hf://buckets/owner/name` rw, or `hf buckets sync remote local` |
| **Space 挂载** | ❌ Read-only (official: "Models, datasets, Spaces are always mounted read-only") | ✅ Read-write (official: "Buckets are mounted read-write by default") |
| **膨胀 (Bloat)** | Every sync = +1 commit, history grows forever | Overwrite, no history. Delete → frees quota |
| **增量同步** | `upload_folder` checks per-file etag | `hf buckets sync` compares source+dest, transfers only changed files |
| **挂载方式** | Not mountable as rw volume in Space | `Volume(type="bucket", source="...", mount_path="/data", read_only=False)` |
| **Server-side copy** | ❌ | ✅ bucket ← repo server-side (Xet hash migration, 0 re-upload) |
| **Dedup** | Xet chunk-level | Xet chunk-level (same) |
| **Primary use case** | Publishing finished artifacts (models/datasets) | Working storage: checkpoints, logs, intermediate state |
| **Pull Requests** | Yes | No |
| **Cards** | Yes (model/dataset cards) | No (plain README rendered) |
| **Private free quota** | 100GB (shared across all repo types) | 100GB (same shared pool) |
| **S3-compatible API** | No | Yes (AWS CLI, boto3, s5cmd work directly) |

Quota is shared — both consume the same 100GB private free tier. Choosing Bucket vs Dataset is NOT a quota decision.

## Bucket Python API (huggingface_hub ≥1.26)

```python
from huggingface_hub import HfApi, create_bucket, Volume

api = HfApi(token=HF_TOKEN)

# Create a bucket (private by default)
create_bucket("nmem/logic", private=True)
# → BucketUrl(url='https://huggingface.co/buckets/nmem/logic', ...)

# List buckets (only for authenticated user's namespace)
buckets = api.list_buckets()
# → [BucketInfo(id='nmem/logic', private=True, size=0, total_files=0)]

# Bucket info
info = api.bucket_info("nmem/logic")

# CRUD: use hf CLI via subprocess (huggingface_hub 1.26 has no Python API for bucket file ops)
# hf buckets sync ./local_dir hf://buckets/nmem/logic/data
# hf buckets cp hf://buckets/nmem/logic/file.txt ./local.txt

# CRITICAL: Mount bucket as Space volume (triggers Space restart)
api.set_space_volumes(
    "nmem/memlg",
    volumes=[Volume(type="bucket", source="nmem/logic", mount_path="/data")]
)
# → Space auto-restarts with new volume mount (~30s downtime)

# Remove all volumes from a Space
api.delete_space_volumes("nmem/memlg")  # also restarts

# Inspect existing volumes
runtime = api.get_space_runtime("sonoke/h")
# runtime.volumes → [Volume(type='bucket', source='sonoke/logic', mount_path='/data', ...)]
```

**Cross-account gotcha**: `hf buckets list` returns 404 for buckets owned by a different account than the token's `whoami`. Token's `whoami` must match the bucket namespace. Use `api.whoami()` to verify before listing.

## Volume Mount in Spaces

Official docs (`storage-buckets-access`): "Volume mounts in Jobs and Spaces are the same idea as hf-mount, managed for you by the platform — no extra setup needed. Buckets are mounted read-write by default."

Two access methods for Spaces:
1. **Volume mount** (managed by HF platform) — configured via `set_space_volumes` API or HF Dashboard → Settings → Volumes. Bucket appears at mount path (e.g., `/data`), rw.
2. **hf:// paths (fsspec)** — access via Python data libraries (pandas, DuckDB) without mount. Read-write.

`hf-mount` CLI (separate tool, for local dev) can also mount buckets as local filesystem via NFS or FUSE. Not needed in Spaces (platform handles it).

## HF Persistent Storage Deprecation (2026-02)

HF staff @Wauplin in GitHub issue #3806 (2026-02-13):
> "persistent storage is currently being a slowly deprecated feature (nothing announced yet) so I wouldn't start building on it"

**This refers to the old `/data` folder mounted as overlay (ephemeral persistent disk)** — NOT to Storage Buckets. Storage Buckets (GA 2026-03-10) are the replacement product and are NOT deprecated. The session's hermes Space `/data` is a Bucket FUSE mount, confirmed by `df -T /data` returning `fuse` type, not overlay.

If you see Spaces with `storage: None` in `SpaceRuntime` but `/data` is mounted as `fuse` — it's a Bucket volume mount, not old persistent storage.

## Reading Live Space Volume Config

```python
from huggingface_hub import HfApi
api = HfApi(token=HF_TOKEN)
info = api.space_info("sonoke/h")
# info.runtime.volumes → [Volume(type='bucket', source='sonoke/logic', mount_path='/data', ...)]
# info.runtime.raw.get("volumes") → [{'type': 'bucket', 'source': 'sonoke/logic', 'mountPath': '/data', 'readOnly': False, 'configuredByUserId': '...'}]
# info.runtime.storage → None (old persistent storage, deprecated)
```

## Nexus 7-Dimension Audit (Why逐文件, Not 整目录)

The nexus repo (`i3t2y/nexus`) did a 7-dimension persistence audit comparing its逐文件 (per-file) Bucket sync approach to HermesFace/HuggingMes's 整目录 (whole-directory) Dataset sync. Nexus scored 7/7 parity, 6/7 superior. Rationale for逐文件:

1. **state.db needs WAL safety** — can't raw-cp live SQLite; must `PRAGMA wal_checkpoint(TRUNCATE)` + `sqlite3 backup API` for consistent snapshot. Whole-directory cp risks copying WAL-inconsistent state.
2. **config.yaml needs template coverage** — `cmp template!=runtime then cp` prevents dashboard writes from corrupting provider config. Whole-directory can't do per-file template logic.
3. **Business state in external DB** — `agent_states`/`task_logs`/`long_memory`/`skills_index` live in Postgres, not files. File sync only handles ephemeral-container-local state.
4. **Different files, different strategies** — state.db = WAL-safe snapshot; config = template-coverage; business data = Postgres table; large blobs = R2. Whole-directory sync can't apply per-file strategies.

**However** — the gap that caused `mem0.json` loss is BECAUSE it wasn't in the逐文件 `_FILES` list. The逐文件 approach requires manual list maintenance; every new config file must be added. The env-var-only fix (see `references/hermes-persistence-architecture.md` § "Env-Var-Only Fix") sidesteps this entirely by eliminating the need for `mem0.json` persistence.

## Practical Pattern for Multi-Space Deployments

When deploying services across multiple HF Spaces (e.g., hermes on `sonoke/h`, mem0+worker on `nmem/memlg`):

| Space | Volume | Why |
|---|---|---|
| hermes (`sonoke/h`) | Bucket `sonoke/logic` rw `/data` | Logic layer rw + state.db WAL-safe snapshots |
| worker (`nmem/memlg`) | Bucket `nmem/logic` rw `/data` (new) | Worker code hot-reload via `hf buckets sync` (replaces `snapshot_download` of Dataset) |

Three-file budget (Dockerfile + README.md + start.sh) stays in HF Space git repo, frozen — never bucketed. Bucket only holds logic layer (`nworker/`) and runtime state. This separation ensures changing logic never triggers Docker rebuild.
