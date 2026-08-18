# HF Free Tier Hermes Capability Matrix

Assessed 2026-08-14 on a live HF Space (Debian 13, 2 vCPU, 16 GB RAM, no GPU). Use this matrix when advising on HF vs local deployment.

## Hardware Limits (cgroup-verified)

| Resource | Limit | How to verify |
|----------|-------|---------------|
| CPU | 2 vCPU (`cpu.max: 200000 100000`) | `cat /sys/fs/cgroup/cpu.max` |
| RAM | 16 GB (`memory.max: 15258 MB`) | `cat /sys/fs/cgroup/memory.max` |
| GPU | None | `nvidia-smi` → NO GPU |
| Disk | 1.7 TB overlay (ephemeral) | `df -h /` |
| IPv6 | No egress | Supabase direct PG → "Network is unreachable" |
| Processes | No hard cap (6+ Python procs running) | `ls /proc/[0-9]*/cmdline` |
| Network | Public internet outbound OK | `curl -s https://api.openai.com` → 421 |

## Feature Availability Matrix

### Core (100% functional)
- terminal + file ops + code execution
- skills (install/manage; restored from HF Bucket after restart)
- mem0 memory (NIM → 智谱 → pgvector Supabase; external storage survives restart)
- delegate_task (subagents)
- cronjob (scheduled tasks)
- session_search (history FTS5)
- web_search (via anysearch/anysearch API)

### Disabled by check_fn (0% functional)
| Tool | Check function | Reason |
|------|---------------|--------|
| browser/CDP | `check_browser_requirements` | No Chrome installed |
| browser_vision | `check_browser_vision_requirements` | No Chrome |
| BFL video | `check_bfl_requirements` | No API key |
| image_generation | `check_image_generation_requirements` | No GPU |
| TTS | `check_tts_requirements` | No audio deps |
| computer_use | `check_computer_use_requirements` | No desktop |
| terminal close/read | `check_close/read_terminal_requirements` | Container restrictions |
| kanban | `_check_kanban_mode` | Not configured |

### Partial (~80%)
- **vision_analyze**: works via fallback (auxiliary vision model), not native GPU vision
- **persistence**: 3-layer external backup (HF Bucket + R2/Supabase + pgvector) — survives restart but each restore has failure points

## Process Footprint (typical)

| PID | Process | Purpose |
|-----|---------|---------|
| 128 | persist_to_r2.py | R2/Supabase data sync |
| 129 | state_db_uploader.py | State DB upload |
| 130 | home_files_uploader.py | HF Bucket file sync |
| 131 | keepalive.py | Heartbeat |
| 132 | app.main boot | HF Space entry point |
| 135 | hermes gateway | Main agent (RSS ~236 MB) |
| 136 | hermes dashboard | Web UI (port 7860) |

Total RSS: ~236 MB (hermes) + overhead ≈ 400-500 MB of 16 GB = **3% memory usage**.

loadavg ~7.93 on 2 vCPU → already near saturation when agent is active.

## Resource Cost of Adding mem0 FastAPI Server (Path 2)

| Resource | Additional cost | Total after | % of limit |
|----------|----------------|-------------|------------|
| Memory | +60-100 MB | ~336 MB | 2.1% of 16 GB |
| CPU (idle) | ~0% | same | 0% |
| CPU (during add/search) | <3s spike (NIM/智谱 calls) | brief | no sustained load |
| Disk | ~25 MB (mem0 SDK + deps) | minimal | |
| HF风控 risk | **very low** — no OOM, no sustained CPU | | |

## Deployment Decision Framework

| Scenario | HF free OK? | Better local? |
|----------|-------------|---------------|
| Telegram bot / remote chat assistant | ✅ Core 100% | Equal — HF gives remote access |
| Agent orchestration (delegate + cron) | ✅ | Equal |
| Browser automation | ❌ | ✅ (Chrome + CDP) |
| Image/video generation | ❌ | ✅ (GPU) |
| Desktop control | ❌ | ✅ (desktop) |
| Heavy persistent file ops | ❌ (ephemeral) | ✅ |
| High concurrency | ⚠️ (2 vCPU ceiling) | ✅ |
| 24/7 always-on reliability | ⚠️ (Restart roulette) | ✅ |

**Recommendation:** Local as primary (persistent + full features + no restart risk). HF free as lightweight remote entry (chat + memory + orchestration). Don't stack production features on HF free tier.

## Installing Tools Without sudo (HF Space)

HF Space containers have no `sudo` — `apt install` fails. For binary tools (e.g. `gh` CLI), download the tarball and extract to `~/bin` (user-writable, already in PATH on hermes HF Space):

```bash
curl -fsSL "https://github.com/cli/cli/releases/download/v2.97.0/gh_2.97.0_linux_amd64.tar.gz" -o /tmp/gh.tar.gz
python3 -c "import tarfile; tarfile.open('/tmp/gh.tar.gz','r:gz').extractall('/tmp/gh-extracted')"
mkdir -p ~/bin && cp /tmp/gh-extracted/gh_2.97.0_linux_amd64/bin/gh ~/bin/gh
chmod +x ~/bin/gh && gh --version
```

**Persistence**: `~/bin/` (= `/opt/data/.hermes/home/bin/`) is on ephemeral disk. The `home_files_uploader.py` only backs up `skills/` and `memories/` — NOT `bin/`. Any binary installed this way is **lost on Restart**. Re-download each session or prefer tools that are pre-installed (`git`, `python3`, `curl`, `pip`).

For `gh` specifically: `git` + PAT covers clone/commit/push — `gh` is only needed for API features (issues, PRs, Actions). See `references/github-actions-hf-deploy.md` § "Installing gh CLI without sudo" for details.
