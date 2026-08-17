# GitHub Private Repo + Actions → HF Space Deploy Pattern

Decision (2026-08-15): Maintain mem0 server deployment files in a GitHub private repo, not directly in HF Space git. GitHub Actions deploys to HF Space only on explicit trigger (tag/manual), not on every push. This separates code maintenance (GitHub, unlimited pushes, full history) from HF rebuilds (rare, controlled).

## Why GitHub Private Repo over Direct HF Git

| | HF Space Git (direct) | GitHub Private Repo → Actions → HF |
|---|---|---|
| Push frequency | Every push = Docker rebuild (封号 risk) | Push to GitHub = no rebuild |
| Version history | HF git has it but poor UX | Full GitHub history, diff, blame, PR review |
| Multi-device access | HF Web IDE very limited | GitHub any device, Web editor, Codespaces |
| Rebuild control | None (push = rebuild) | Actions only on tag/manual trigger |
| Agent maintenance | Need HF token + HF git push | `git` + GitHub PAT (gh CLI optional) |

**Key insight**: The user's core constraint is "minimize HF Space pushes to avoid rebuild-induced ban". GitHub private repo + Actions solves this completely — code changes accumulate in GitHub with zero HF rebuilds until you explicitly tag a release.

## Architecture

```
Developer/Agent → git push → GitHub Private Repo (mem0-server)
                              │
                              ├─ .github/workflows/deploy-hf.yml
                              │  triggers: workflow_dispatch (manual) or tag push
                              │
                              ↓
                            GitHub Actions runner
                              │  git push -f → HF Space (using HF_TOKEN secret)
                              ↓
                            HF Space rebuild (only on deploy, not on every code change)
```

## GitHub Actions Workflow (deploy-hf.yml)

```yaml
name: Deploy to HF Space

on:
  workflow_dispatch:  # Manual trigger only — no auto-deploy on push
  push:
    tags:
      - 'v*'  # Or deploy on version tags

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Push to HF Space
        run: |
          git config user.name "github-actions"
          git config user.email "actions@github.com"
          git remote add hf https://user:${{ secrets.HF_TOKEN }}@huggingface.co/spaces/<username>/mem0-server
          git push -f hf main
        env:
          HF_TOKEN: ${{ secrets.HF_TOKEN }}
```

**Secrets needed in GitHub repo**: `HF_TOKEN` (HuggingFace access token, write scope).

This workflow only runs when you manually click "Run workflow" in GitHub Actions tab, or when you push a `v*` tag. Normal `git push` to the repo does NOT trigger HF rebuild.

## Agent Maintenance via git + PAT (no gh CLI needed)

`git` is pre-installed on HF Space. `gh` is NOT. To manage the private repo:

```bash
# Clone with PAT embedded in URL (one-time, per-session after Restart)
git clone https://<username>:<PAT>@github.com/<username>/mem0-server.git

# Or configure credential helper (persists within session)
git config --global credential.helper store
echo "https://<username>:<PAT>@github.com" > ~/.git-credentials
```

`gh` CLI is optional — `git` + PAT covers clone/commit/push/PR creation via REST API with curl. If `gh` is needed, see "Installing gh CLI without sudo" below.

## Installing gh CLI without sudo on HF Space

HF Space has no `sudo`. `apt install gh` fails. Workaround: download binary tarball, extract with Python, place in `~/bin` (user-writable, already in PATH).

```bash
# 1. Download (use correct version — check github.com/cli/cli/releases/latest)
curl -fsSL "https://github.com/cli/cli/releases/download/v2.97.0/gh_2.97.0_linux_amd64.tar.gz" -o /tmp/gh.tar.gz

# 2. Extract (no unzip on slim images — use python3 tarfile)
python3 -c "import tarfile; tarfile.open('/tmp/gh.tar.gz','r:gz').extractall('/tmp/gh-extracted')"

# 3. Place in ~/bin (user-writable, already in PATH on HF Space)
mkdir -p ~/bin
cp /tmp/gh-extracted/gh_2.97.0_linux_amd64/bin/gh ~/bin/gh
chmod +x ~/bin/gh

# 4. Verify
gh --version
```

**Persistence caveat**: `~/bin/gh` (40MB binary) lives on ephemeral disk. The hermes `home_files_uploader.py` only backs up `skills/` and `memories/` directories — it does NOT back up `bin/`. So `gh` will be lost on Space Restart. Options:
- Re-download on each session (the commands above are idempotent, ~10 seconds)
- Bake into a Dockerfile if deploying your own Space
- Use `git` + PAT instead (no install needed, `git` is pre-installed)

**Recommendation**: Use `git` + PAT for routine maintenance. Only install `gh` if you need its API features (issue triage, PR review, Actions triggering) within a single session.

## Three-File永続 + GitHub Repo Layout

```
mem0-server/                    (GitHub private repo)
├── Dockerfile                  (→ templates/mem0-server.Dockerfile)
├── README.md                   (→ templates/mem0-server.README.md)
├── start.sh                    (→ templates/mem0-server.start.sh)
├── DEPLOY.md                   (full 5-step deployment guide)
├── .github/
│   └── workflows/
│       └── deploy-hf.yml       (Actions workflow, manual/tag trigger only)
└── .env.example                (reference, NOT real secrets)
```

All real secrets (Neon connection, NIM key, 智谱 key, ADMIN_API_KEY) live in:
- **HF Space Secrets** (for the running container)
- **GitHub repo Secrets** (for Actions to pass to HF, if needed)

Never commit secrets to the repo.

### HF Space Secrets for mem0 server (14 total)

| Key | Purpose |
|---|---|
| `POSTGRES_HOST` | Neon endpoint host |
| `POSTGRES_PORT` | 5432 |
| `POSTGRES_USER` | Neon username |
| `POSTGRES_PASSWORD` | Neon password |
| `APP_DB_NAME` | Neon database name (default: neondb) |
| `POSTGRES_COLLECTION_NAME` | mem0 vector collection (default: memories) |
| `AUTH_DISABLED` | true (skip mem0 JWT, use X-API-Key instead) |
| `ADMIN_API_KEY` | random string for X-API-Key auth |
| `MEM0_DEFAULT_LLM_MODEL` | glm-4.7-flash |
| `MEM0_DEFAULT_EMBEDDER_MODEL` | nvidia/nemotron-3-embed-1b |
| `OPENAI_API_KEY` | Placeholder for startup DEFAULT_CONFIG (can be empty if /configure runs before first use) |
| `NIM_API_KEY` | NIM embedder key (passed via /configure `embedder.config.api_key`) |
| `ZAI_API_KEY` | 智谱 LLM key (passed via /configure `llm.config.api_key`) |
| `MEM0_TELEMETRY` | false |

**Key isolation**: `OPENAI_API_KEY` is only read by `DEFAULT_CONFIG` at import time. After `POST /configure`, the config (with separate NIM/ZAI keys) is persisted to Neon's settings table. `OPENAI_API_KEY` is never read again. It can be set to the NIM key (so the initial DEFAULT_CONFIG is functional) or left empty (if /configure runs before the first /memories or /search call).

### /configure payload (one-time, post-deploy)

```bash
curl -X POST https://<space-url>/configure \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <ADMIN_API_KEY>" \
  -d '{
    "llm": {
      "provider": "openai",
      "model": "glm-4.7-flash",
      "openai_base_url": "https://api.z.ai/api/paas/v4",
      "api_key": "'"${ZAI_API_KEY}"'"
    },
    "embedder": {
      "provider": "openai",
      "model": "nvidia/nemotron-3-embed-1b",
      "openai_base_url": "https://integrate.api.nvidia.com/v1",
      "api_key": "'"${NIM_API_KEY}"'"
    }
  }'
```

The `provider: "openai"` + custom `openai_base_url` pattern bypasses mem0's `_validate_bundled_providers` check (which only allows openai/anthropic/gemini). The actual NIM and 智谱 endpoints are OpenAI-compatible, so this works without source changes.

## docs/ Directory for Agent Context Continuity

Include `docs/` in the GitHub repo with:

- `docs/STATUS.md` — deployment progress checklist (✅/❌ per step), architecture diagram, environment ledger, key technical decisions, hermes switch config, maintenance instructions. **Update on every state change.**
- `docs/SECRETS.md` — all secret key names + descriptions + which service holds them (HF Space Secrets, GitHub Secrets, Neon Console, cron-job.org). **Never include actual values.**
- `docs/DEPLOY.md` — full step-by-step deployment guide with exact commands.

**Why docs/ over mem0 for project continuity**: mem0 compresses and loses detail across sessions. Ephemeral files wipe on Restart. The GitHub repo is the only durable, full-fidelity store. Any agent in any session can `git clone` + read `docs/STATUS.md` to resume from the exact point of interruption.

## HF Space Anti-Risk-Control Naming

To avoid HF content scanning/risk-control on the mem0 server Space:
- **Space name**: use a minimal name like `0` (single character) — gives URL `https://<user>-0.hf.space`
- **README.md**: only YAML frontmatter, no descriptive text:

```yaml
---
title: nmem
emoji: 🧠
colorFrom: blue
colorTo: purple
sdk: docker
app_port: 7860
pinned: false
---
```

- Move any descriptive README content to `docs/README_original.md` in the GitHub repo (not deployed to HF).
- Use a separate HF account (e.g. `nmem`) for the mem0 Space, not the main account.

## Neon Region Selection

Choose **AWS us-east-1** for Neon when deploying on HF Space. HF free Spaces run in Ashburn, Virginia (AWS us-east-1) — confirmed via IP geolocation. Same-region Neon = <1ms latency for database queries. Don't choose Neon Auth or any Backend Services add-on — mem0 server has its own auth (AUTH_DISABLED + X-API-Key), Neon Auth adds unnecessary tables and complexity.
