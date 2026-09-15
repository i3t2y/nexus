> [ARCHIVED 2026-09-05] 本文档记载的自托管 mem0 server / MCP server 方案已废弃，仅作冷备恢复参考。现行方案见 docs/shared/cron-memory-evolution.md。

# mem0 Server Native Auth Mechanism

mem0 server (`server/auth.py`) has a built-in 4-branch auth chain in `verify_auth()`. Understanding it is critical for securing a public Space without middleware or source changes.

## verify_auth Priority Chain (auth.py L147–173)

```
1. Authorization: Bearer *** → JWT decode → resolve User (401 if invalid)
2. X-API-Key: ***
   ├─ matches ADMIN_API_KEY env var → admin (no DB lookup, no users table needed)
   └─ else → query api_keys table (bcrypt verify) → resolve User
3. (no Bearer, no X-API-Key) → AUTH_DISABLED env var?
   ├─ True → pass (auth_type="disabled", user=None)
   └─ False → 401 "Authentication required"
```

**Key insight**: `X-API-Key` branch (2) runs BEFORE `AUTH_DISABLED` branch (3). So `ADMIN_API_KEY` + `AUTH_DISABLED=false` = proper API-key gate **without any middleware**.

## Endpoint Auth Levels

| Endpoint | Auth dependency | Notes |
|---|---|---|
| `POST /memories`, `GET /memories`, `POST /search`, `PUT/DELETE /memories/{id}` | `verify_auth` | Read/write — X-API-Key sufficient |
| `POST /configure`, `DELETE /memories` (all), `POST /reset` | `require_admin` | Admin only — ADMIN_API_KEY works |
| `GET /health` (patch-injected) | **none** | Bypasses verify_auth entirely — cron keepalive works without any header |

## Recommended Auth Configurations

### Option A: AUTH_DISABLED (simplest, insecure on public Space)

- Set `AUTH_DISABLED=true` via `exec env AUTH_DISABLED=*** uvicorn` in start.sh (NOT as HF Secret — collision with HF reserved name)
- No `ADMIN_API_KEY`, no `JWT_SECRET`
- All endpoints open to anyone → **only safe for private Spaces or testing**

### Option B: ADMIN_API_KEY (recommended for public Space)

- Set `ADMIN_API_KEY=<random-secret>` as HF Space Secret
- Do NOT set `AUTH_DISABLED` (remove from `exec env` in start.sh)
- Do NOT set `JWT_SECRET` (enables JWT auth on ALL endpoints, blocks X-API-Key callers)
- Callers send `X-API-Key: <secret>` header
- Anonymous requests → 401 ✅
- `/health` → 200 (no auth dependency) ✅ cron-job.org keepalive works
- hermes `SelfHostedBackend` natively sends `X-API-Key` header (`_backend.py` L99)

**Why not JWT?** `JWT_SECRET` enables JWT parsing on ALL `verify_auth` endpoints. HF private Space auto-injects `Authorization: Bearer <HF_TOKEN>` → mem0 tries JWT decode → 401 (HF token is not a mem0 JWT). This is the fundamental incompatibility between HF private Space and mem0 JWT auth.

## HF Private Space Bearer Passthrough (verified)

HF private Space edge proxy requires `Authorization: Bearer <HF_TOKEN>` for ALL access (GET, POST, everything). After the proxy validates the HF token, it **passes the `Authorization` header through to the application** unchanged. mem0 `verify_auth` sees the Bearer header first → tries JWT decode → fails → 401. This is why:

- **HF private Space + AUTH_DISABLED are fundamentally incompatible** (Bearer branch runs first)
- **HF private Space + ADMIN_API_KEY also fails** (Bearer branch runs before X-API-Key branch)
- **HF public Space + ADMIN_API_KEY** = works (no Bearer injected, X-API-Key matched)

Sources: Medium "Deploying FastAPI on HF Spaces — Handling All Its Restrictions" § "Handling Dual Bearer Tokens"; HF Forums "Private Space authentication for external API calls" (confirms `Authorization: Bearer` required for private Spaces); mem0 `server/auth.py` L147–173.

## hermes SelfHostedBackend (client side)

hermes `SelfHostedBackend` (`_backend.py` L83–155) uses `httpx.Client` with:
- `X-API-Key` header if `api_key` is provided (omitted for AUTH_DISABLED servers)
- Does NOT send `Authorization: Bearer` — avoids HF proxy Bearer/JWT conflict
- Base URL = Space URL (e.g., `https://nmem-memgraph.hf.space`)
- Routes: `POST /memories`, `POST /search`, `GET /memories`, `PUT/DELETE /memories/{id}`

`mem0.json` config for self-hosted:
```json
{"mode": "self_hosted", "self_hosted": {"host": "https://<space>.hf.space", "api_key": "<ADMIN_API_KEY value>"}}
```

Router priority in hermes `__init__.py`: `oss > host > platform`. If `oss` config block exists, it takes precedence over `host` — remove or omit `oss` to activate `SelfHostedBackend`.

## Switching from AUTH_DISABLED to ADMIN_API_KEY

1. Add HF Space Secret: `ADMIN_API_KEY=<random-secret>` (e.g., `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`)
2. **Delete the `AUTH_DISABLED` Space Variable** (if it was previously set as one): `api.delete_space_variable("<owner>/<space>", key="AUTH_DISABLED")` or remove it in HF Space Settings → Variables. ⚠️ **This is a separate step from editing `entrypoint.sh`** — even after removing `exec env AUTH_DISABLED=true` from `entrypoint.sh`, if `AUTH_DISABLED=true` persists as a Space Variable, mem0's `os.environ.get("AUTH_DISABLED", "")` still reads it → auth remains bypassed. The Space Variable lives in HF's environment injection layer, independent of the container's entrypoint.
3. Edit `entrypoint.sh` (**on the HF Dataset**, NOT the frozen `start.sh`): change `exec env AUTH_DISABLED=true uvicorn main:app --host 0.0.0.0 --port 7860` → `exec uvicorn main:app --host 0.0.0.0 --port 7860` (remove `env AUTH_DISABLED=true` injection). Upload to HF Dataset, then restart Space (container re-pulls Dataset on boot).
4. Do NOT add `JWT_SECRET` (would enable JWT on all endpoints, blocking X-API-Key callers)
5. Update hermes `mem0.json`: add `api_key` field to `self_hosted` block — `"self_hosted": {"host": "https://<space>.hf.space", "api_key": "<ADMIN_API_KEY value>"}`
6. Restart Space, verify: `curl -X POST https://<space>.hf.space/memories` → 401; with `-H "X-API-Key: <key>"` → 200
7. `/health` continues to work without header (cron-job.org unaffected)

### Verification Results (2026-08-16)

After switching to ADMIN_API_KEY on `nmem/memgraph` (public Space):

| Test | Result | Expected |
|---|---|---|
| `GET /health` (no auth) | 200 `{"status":"ok","db":"connected"}` | 200 (cron keepalive) |
| `POST /memories` (no auth) | 401 | 401 (anonymous rejected) |
| `POST /memories` (wrong X-API-Key) | 401 | 401 (bad key rejected) |
| hermes `mem0_search("pizza")` | 5 results with scores | ✅ search works |
| hermes `mem0_add(fact)` | "Fact stored." | ✅ write works |

**Full architecture verified:**
```
hermes (SelfHostedBackend + X-API-Key header)
  → https://nmem-memgraph.hf.space (public HF Space)
  → ADMIN_API_KEY match (mem0 verify_auth branch 2)
  → NIM embedder 2048-dim
  → Neon Postgres pgvector (neondb)
  → results returned to hermes
```

**Where AUTH_DISABLED lives**: `start.sh` (frozen, in GitHub repo) is a thin bootstrap that `exec`s into `entrypoint.sh` (on HF Dataset, hot-reloadable). The `exec env AUTH_DISABLED=true uvicorn` line is in `entrypoint.sh` Phase 4, NOT in `start.sh` itself. This is by design — auth config changes go through the hot-reloadable Dataset layer, not the frozen three-file layer.

## HF_TOKEN Cross-Account Pitfall

When uploading to a HF Dataset/Space owned by account **B** (e.g., `nmem`) from a hermes instance whose `.env` `HF_TOKEN` is labeled "Inference Providers token" — **verify the token's actual owner before assuming it belongs to account A**. The hermes UI description is just a label; the token itself may belong to any HF account.

**Verify token owner**:
```python
from huggingface_hub import HfApi
api = HfApi(token="<token>")
me = api.whoami()  # → {"name": "nmem", "fullname": "nexus"} or {"name": "i3t2y", ...}
```

If `whoami()` returns `401 Invalid user token` or `Repository Not Found` for a private repo you own, the token is either:
- Expired or revoked — regenerate at huggingface.co/settings/tokens
- From a different HF account — check which account created the Space/Dataset

**Read full token from `.env`**: `execute_code` and `os.environ.get()` may truncate long env values (37-char HF tokens get cut to ~13 chars when passed as string literals). Read the `.env` file directly:
```python
from pathlib import Path
env = Path("/opt/data/.hermes/.env").read_text()
for line in env.splitlines():
    if line.startswith("HF_TOKEN="):
        token = line.split("=", 1)[1].strip()  # full 37-char token
```

