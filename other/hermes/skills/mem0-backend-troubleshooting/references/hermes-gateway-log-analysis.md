# Hermes Gateway Log Analysis Methodology

How to analyze hermes runtime logs when the user reports issues or asks "分析下hermes的运行日志". Logs live at `$HERMES_HOME/logs/` — four files: `errors.log`, `gateway.log`, `gateway-starts.log`, `gateway-exit-diag.log`.

## Quick Analysis Procedure

1. **Find the log files**: `find /opt/data/.hermes -name "*.log"` or check `~/.hermes/logs/`
2. **Read errors.log tail**: This is the structured error/warning log with timestamps
3. **Grep gateway.log for INFO lines**: Shows actual request/response flow, startup, inbound messages
4. **Check gateway-starts.log**: Unix timestamps of every gateway start (detect restart loops)
5. **Check gateway-exit-diag.log**: JSON exit diagnostics (clean exit vs crash)

## Classification Pattern (execute_code)

Use Python to parse and categorize log lines by type. This gives a quick severity-ranked summary:

```python
from pathlib import Path
import collections

lines = Path('/home/user/hmlog.txt').read_text().splitlines()  # or errors.log path

counts = collections.Counter()
for line in lines:
    if '429' in line: counts['429-RATE-LIMIT'] += 1
    elif 'Broken pipe' in line or 'Connection reset' in line: counts['STREAM-ERROR'] += 1
    elif 'Stream stale' in line: counts['STREAM-STALE'] += 1
    elif 'scope handle' in line: counts['RELAY-BUG'] += 1
    elif 'Compacting' in line or 'compressed' in line: counts['COMPRESS'] += 1
    elif 'openrouter unhealthy' in line or 'Nous client unavailable' in line: counts['AUX-DOWN'] += 1
    elif 'Self-improvement' in line: counts['SELF-IMPROVE'] += 1
    elif 'Pre-call sanitizer' in line: counts['SANITIZER'] += 1
    elif 'transcript lagged' in line: counts['TRANSCRIPT-LAG'] += 1
    elif 'Forbidden' in line: counts['TELEGRAM-403'] += 1

for k, v in counts.most_common():
    print(f"  {k}: {v}")
```

## Known Hermes Gateway Failure Patterns

### 1. Pre-call sanitizer: empty message (HIGH FREQUENCY, HARMLESS)
- **Log**: `Pre-call sanitizer: healed 1 empty non-final message(s) by substituting placeholder content`
- **Root cause**: Tool calls generate empty intermediate messages; LLM API requires non-empty content
- **Impact**: Zero — hermes auto-heals by inserting placeholder text
- **Action**: None needed. This is a self-recovery mechanism working as designed.

### 2. Persisted transcript lagged (LOW RISK)
- **Log**: `Persisted transcript lagged live cached history for session ... (disk=N, memory=N+1)`
- **Root cause**: FTS5 full-text search write lags behind in-memory cache by 1 message
- **Impact**: None — hermes preserves the live (newer) memory version
- **Action**: None. Comment says "possible FTS write corruption" but it's normal write-ordering.

### 3. Stream stale / Broken pipe (SERIOUS — LLM provider issue)
- **Log**: `Stream stale for 240s (threshold 240s) — no chunks received. model=... context=~74,139 tokens. Killing connection.`
- **Followed by**: `httpx.ReadError: [Errno 32] Broken pipe` or `[Errno 104] Connection reset by peer`
- **Root cause**: LLM provider (via HF Space proxy) takes >240s to start streaming. Happens when context >70K tokens — large request bodies take longer to process.
- **Impact**: API call fails, hermes retries (up to 3 attempts). If all 3 fail, the turn fails.
- **Pattern**: `Stream stale 240s → Broken pipe → retry → Stream stale 240s → Connection reset → retry → ...`
- **Action**: Reduce context size (run /compress or /new), or use a more reliable LLM provider.

### 4. LLM 429 rate limiting (SERIOUS — provider throughput)
- **Log**: `Error code: 429 — {'error': {'code': '1302', 'message': 'Rate limit reached for requests'}}`
- **Or**: `429 — code:1305 — service may be temporarily overloaded`
- **Root cause**: 智谱 GLM free tier RPM/RPS limit. Larger context requests are more likely to trigger 429.
- **Terminal state**: `💀 Final error: HTTP 429` after 3 failed retry attempts
- **Impact**: Complete turn failure when all retries exhausted
- **Action**: Same as Fix G in main SKILL.md — `infer=False` for mem0, or upgrade LLM tier, or use a different provider.

### 5. Relay scope handle bug (HERMES INTERNAL BUG)
- **Log**: `RuntimeError: invalid argument: scope handle is not at the top of the stack`
- **Traceback**: `relay_runtime.py line 644 → nemo_relay/scope.py line 144`
- **Root cause**: Bug in `nemo_relay` library — scope stack inconsistency during turn finalization
- **Impact**: Self-improvement review turn finalization fails, but the review itself completes. Non-fatal.
- **Action**: None — this is a hermes/nemo_relay bug, not user-fixable. Report upstream if persistent.

### 6. Context compression cascade (SEVERE — compaction failure)
- **Log sequence**: `Context length exceeded → Compacting context → compression made no progress for 120s → Context compression timed out → Session compressed N times — accuracy may degrade`
- **Root cause**: When context exceeds ~96K tokens, hermes triggers compression. Compression needs an auxiliary model (OpenRouter/Nous). If both are down, compression fails → context keeps growing → more 429s → more stream drops → vicious cycle.
- **Chain**: `context grows → 429 limit → stream drops → compression attempted → auxiliary down → compression fails → context keeps growing → more 429s`
- **Impact**: Progressive degradation — more compressions = more accuracy loss. 6+ compressions means the session is unreliable.
- **Action**: Run `/new` to start fresh. Long-term: configure a working auxiliary provider (`hermes config set auxiliary.compression.provider ...`) or set `OPENROUTER_API_KEY` / run `hermes auth` for Nous.

### 7. Auxiliary providers unavailable (COMPRESSION BLOCKED)
- **Log**: `Auxiliary: marking openrouter unhealthy for 60s (payment / credit error)` + `Auxiliary Nous client unavailable: no Nous authentication found (run: hermes auth)`
- **Root cause**: OpenRouter has no credits; Nous is not authenticated
- **Impact**: Compression, vision, and other auxiliary tasks have no backend → compression cascade (see #6)
- **Action**: `hermes auth` for Nous, or add `OPENROUTER_API_KEY` to `.env`, or set `auxiliary.compression.provider` to a working provider.

### 8. Telegram 403 Forbidden (FILE DOWNLOAD)
- **Log**: `Failed to cache document: Forbidden (403). Parsing the server response b'forbidden' failed`
- **Traceback**: `telegram/request/_baserequest.py → parse_json_payload → JSONDecodeError`
- **Root cause**: Telegram Bot API returns `forbidden` (not JSON) when the bot lacks permission to download a file. Happens with large files or when the file_id has expired.
- **Impact**: Bot cannot download user-sent attachments
- **Workaround**: Ask the user to save the file to a local path on the machine, then read it via `read_file` or `search_files`.

## Root Cause Chain (the vicious cycle)

The most common multi-symptom scenario is a cascade:

```
context accumulates to 70K+ tokens
  → request body large → 智谱 429 rate limit (16+ times)
    → 240s no response → Broken pipe / Connection reset (9+ times)
      → hermes triggers compression → OpenRouter/Nous both down (339+ times)
        → compression fails or times out → context keeps growing
          → more 429s → more stream drops → more failed compressions
            → Session compressed 6-10 times → accuracy degraded → /new needed
```

**Break the cycle**: `/new` to start fresh. **Prevent recurrence**: configure a working auxiliary provider so compression has a backend.

## Gateway Restart History

`gateway-starts.log` contains Unix timestamps (one per line). `gateway-exit-diag.log` contains JSON with PID, Python version, and exit tag (`gateway.start` / `gateway.exit_clean` / `asyncio.run.returned`).

Clean restart pattern: `gateway.start (new PID) → asyncio.run.returned (old PID, success=true) → gateway.exit_clean (old PID)`. If old PID exits with error, check for crash loops.

## Self-Improvement Review (normal operation)

`💾 Self-improvement review: Patched SKILL.md in skill '...' (N replacements)` is hermes's background curator automatically maintaining skills. This is **normal operation**, not an error. 40+ occurrences in a multi-day log is typical. The curator can fail on individual patches (e.g. "Description is 195 chars — new skills must fit the 60-char budget") — these are skill-authoring constraint violations, harmless.
