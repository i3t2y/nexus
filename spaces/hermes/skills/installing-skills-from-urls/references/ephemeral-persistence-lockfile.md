# Ephemeral persistence: `.hub/lock.json` + uploader loop

## The problem, stated

On a Space-managed Hermes deployment, `$HERMES_HOME` (often `/opt/data`)
lives on an **ephemeral container disk**. A manual `curl + extract + mv`
install places the skill in memory, but on the next Space Restart
`/opt/data` is cleared and the skill is gone — *even though it was working
seconds ago.*

The fix is not part of the skill's own install doc. It's a Hermes
deployment-mechanism gap the installer must close manually.

## How Hermes persists user-installed skills

Two systems cooperate:

1. **`skills/.hub/lock.json`** — the single source of truth for
   *user-installed* skills (bundled ones live in `.bundled_manifest`, which is
   rebuilt every boot — irrelevant here). `HubLockFile.record_install`
   (`tools/skills_hub.py` ~L3320) writes one entry per skill into
   `installed[<name>]`. The uploader reads this file via
   `HubLockFile.list_installed()` and persists only the listed `install_path`
   subtrees + the three `.hub` "sigils" (`lock.json`, `audit.log`, `taps.json`).
2. **`home_files_uploader.py`** (`/data/scripts/`) — a daemon `start.sh`
   launches on boot. Every `HOME_FILES_UPLOAD_INTERVAL` (default 600 s) it
   rsync-precise-syncs `skills/` (route B: based on `lock.json`'s
   `install_path` list) and the single-file `_FILES` list (`config.yaml`,
   `.env`, `SOUL.md`, memories, `.no-bundled-skills`) into a private Bucket
   at `hf://buckets/${HF_OWNER}/${NEXUS_LOGIC_BUCKET}/home-backups/`.

On the next boot, `restore_home_files.py` pulls the whole `skills/` subtree
back from that Bucket — but only what the uploader pushed, and the uploader
only pushes what's listed in `lock.json`. **No lock entry → no push →
gone on restart.**

## How to register a manually-installed skill

`hermes skills install` would write the lock entry automatically. A manual
URL install must do it by hand. Mirror `HubLockFile.record_install` exactly:

1. `mkdir -p $HERMES_HOME/skills/.hub`
2. Write `lock.json` (indent=2, `ensure_ascii=False`, trailing newline — same
   serialization `HubLockFile.save` uses). Shape:
   ```json
   {
     "version": 1,
     "installed": {
       "<skill-name>": {
         "source": "url",
         "identifier": "<download-url-or-ref>",
         "trust_level": "community",
         "scan_verdict": "manual",
         "content_hash": "",
         "install_path": "<category>/<skill-name>",
         "files": [],
         "metadata": { "url": "<install-doc-url>" },
         "scan_provenance": {},
         "installed_at": "<ISO8601 UTC>",
         "updated_at": "<ISO8601 UTC>"
       }
     }
   }
   ```
   Also create `.hub/audit.log` (append a `timestamp install <name>
   url:community manual <url>` line) and `.hub/taps.json` (`{"taps": []}`)
   so restore sees a canonical `.hub` layout.
3. **Validate before walking away.** Two cheap checks:
   - `_normalize_lock_install_path(install_path, name)` must succeed —
     the path's final component MUST equal the skill name, and the path
     must be relative under `skills/` (no `..`, no absolute). A poisoned
     entry is the precondition for an `rmtree` escape on uninstall.
   - `HubLockFile().list_installed()` must return the entry — this is the
     exact call the uploader makes. If it reads empty, the uploader
     silently skips it.

## What gets persisted along with the skill

- The skill subtree itself (everything under `install_path`).
- `.hub/{lock.json,audit.log,taps.json}` (the sigils).
- Separately, via the `_FILES` single-file path: `$HERMES_HOME/.env` —
  so any `*_API_KEY` the user added to the Hermes master `.env` rides along
  and survives restart. **This means the API key's real value lands in the
  private Bucket `home-backups/.env` object.** Confirm the user accepts that
  before treating "key in `.env`" as the persistence path; the alternative is
  injecting the secret as an HF Space Secret (env var at container start),
  which never touches object storage.

## Confirming the deployment runs the loop

Before assuming "next cycle it'll push," confirm the loop is actually wired:

- `HF_TOKEN` + `HF_OWNER` + `NEXUS_LOGIC_BUCKET` must all be set in the
  *host boot process* (HF Space Secrets inject them). The agent's own shell
  usually does NOT inherit `HF_TOKEN`, so running
  `python3 /data/scripts/home_files_uploader.py` from the agent shell will
  print `WARN: missing ... — uploader daemon no-op` and do nothing. That is
  an env-visibility artifact, NOT proof that the daemon is down — the boot
  process has the token and the daemon it spawned is fine.
- Cheap indirect proof the daemon is running: check the boot/startup log
  for a line like `[start] home-files upload daemon up (→ bucket …)`.
- The only verification paths that need the token (and thus can only be run
  from the boot process or by the user on the HF console) are
  `hf buckets ls hf://buckets/<owner>/<bucket>/home-backups/skills/<cat>/<name>/`
  and the HF Restart → boot-log `[restore-home-files] skills/: ok: restored
  skills/ (created~N updated~0)` line.

## Diagnosing why the loop isn't pushing

If a registered skill isn't appearing in the Bucket after the upload
interval, the daemon is almost always **alive but crashing** — not dead.
Don't conclude "daemon died" from "no new pushes"; a crash loop keeps the
process up but never reaches the push code path. The cheap decisive checks:

1. **Confirm the 4 boot daemons are alive** via `/proc` (no `ps` on slim
   containers — Amazon Linux 2023 / minimal images ship no `ps`):
   ```bash
   for pid in $(ls /proc 2>/dev/null | grep -E '^[0-9]+$'); do
     cmd=$(tr '\0' ' ' < /proc/$pid/cmdline 2>/dev/null)
     case "$cmd" in
       *home_files_uploader*|*persist_to_r2*|*state_db_uploader*|*keepalive*) echo "PID $pid: $cmd" ;;
     esac
   done
   ```
   All four (`persist_to_r2`, `state_db_uploader`, `home_files_uploader`,
   `keepalive`) should be listed. Present → daemon is up.

2. **Read `/proc/<uploader-pid>/environ` for the real daemon env.** The agent
   shell lacks `HF_TOKEN` (it's injected into the *host boot process* only,
   not subprocesses), but `/proc/<pid>/environ` of the daemon IS readable
   from the agent shell and reveals whether `HF_TOKEN` / `HF_OWNER` /
   `NEXUS_LOGIC_BUCKET` are actually set in the daemon's environment:
   ```bash
   tr '\0' '\n' < /proc/82/environ | grep -E '^(HF_TOKEN|HF_OWNER|NEXUS_LOGIC_BUCKET|HOME_FILES_UPLOAD_INTERVAL|HERMES_HOME)=' | sed -E 's/=(.{4}).*/=\1.../'
   ```
   (redact the token in your output — never print the full value). Token
   present + daemon alive ⇒ the loop is wired; absence of pushes is a code
   crash, not a cred gap.

3. **Tally the daemon log** (`/opt/data/logs/home-upload.log`) to classify
   the failure:
   ```bash
   grep -c "fatal"   /opt/data/logs/home-upload.log   # crash count
   grep -c "skills/ ok" /opt/data/logs/home-upload.log # successful skills pushes
   grep -c "\.env ok\|SOUL\.md ok" /opt/data/logs/home-upload.log  # single-file pushes
   ```
   - **`fatal` > 0 AND `skills/ ok` = 0** → skills segment crashes every
     cycle, single-file segment runs fine. The skill will *never* reach the
     Bucket; the uploader code's skills-segment path has a bug (an upstream
     `hf` / `huggingface_hub` API mismatch, e.g.
     `'str' object has no attribute 'read_text'` is the classic signature
     of a `Path`-vs-`str` contract violation passed into `huggingface_hub`).
     This is an **environment/code bug**, not a lock.json or registration
     problem — re-checking the lock entry won't fix it. Report the crash
     signature verbatim to the user so they can fix `/data/scripts/home_files_uploader.py`
     (or roll back the offending `huggingface_hub` version).
   - `skills/ ok` > 0 but your skill still absent → the lock entry isn't
     being read (re-run `HubLockFile().list_installed()` against the file
     the daemon sees — the daemon's `$HERMES_HOME` may differ from yours).
   - All counts 0 and no `start` line → daemon never launched (boot cred gap).
   - Single-file (`\.env ok` / `SOUL\.md ok`) > 0 but `skills/ ok` = 0 →
     confirms the default `_FILES` path runs but skills route dies, i.e. the
     crash is specifically in `_skills_local_sig()` or `_upload_skills()`.

4. **`_STATE_FILE` location.** The uploader's incremental state file is at
   `os.path.dirname($HERMES_HOME)/.home-upload-state.json` — i.e.
   `/opt/data/.home-upload-state.json` (NOT under `$HERMES_HOME/` itself).
   On a fresh Space **this file is absent and that's expected** — it's only
   written at the END of a successful `sync_once()` that reaches
   `_save_state`. A skills-segment crash aborts before `_save_state`, so
   the state file stays absent cycle after cycle. Treat absence as
   "no cycle has completed" not "state is corrupted."

When the diagnosis is "uploader skills segment crashes," the lock.json +
restore path is correct and will work *once the uploader is fixed.* Do not
mistake the uploader bug for a registration bug — re-writing lock.json
won't help. Surface the exact `fatal <err>` line to the user so they (or
the maintainer of `home_files_uploader.py`) can patch it; once fixed, the
next cycle pushes automatically and the Restart-restore path unlocks.

## Failure modes worth noting

- **Searching the wrong path for `lock.json`.** It lives at
  `$HERMES_HOME/skills/.hub/lock.json`, NOT `~/.hub/`, `$HERMES_HOME/.hub/`,
  or `$HERMES_HOME/.hub/lock.json`. Searching clichéd paths and reporting
  "no lock mechanism on this host" is a classic false-negative; the
  mechanism is there, the search was wrong.
- **`.hub/` doesn't exist yet on a fresh Space.** Until the first user skill
  is registered, the directory isn't created. "It didn't exist" therefore
  doesn't mean "the mechanism doesn't run" — it means "nothing has been
  installed through it yet."
- **`content_hash` empty is fine.** The uploader ignores `content_hash`,
  `scan_verdict`, and `trust_level` — they exist for the hub UI / audit trail.
  Only `install_path` (per entry) drives what gets pushed.
- **Mistaking a daemon crash loop for "daemon dead."** When no new pushes
  appear, the daemon is almost always up but crashing every cycle (see
  *Diagnosing why the loop isn't pushing* above). Reach for the `/proc`
  scan + log tally before concluding the process is gone.
- **Mistaking an uploader code bug for a registration bug.** A repeating
  `[home-upload] fatal <err>` line with `skills/ ok` = 0 means the skills
  segment of `home_files_uploader.py` has a bug. Re-writing `lock.json`
  won't fix it — the lock is read *after* the crash point. Report the
  crash signature; only a fix to the uploader (or its `huggingface_hub`
  dependency) unblocks the push.
