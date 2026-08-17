---
name: installing-skills-from-urls
description: "Install a third-party Hermes skill from a URL install doc."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [skills, installation, third-party, hermes-agent, setup]
    related_skills: [hermes-agent-skill-authoring, hermes-agent]
---

# Installing Third-Party Skills from a URL / Install Doc

Use when a user gives a URL — often `.../skill-install.md` or a GitHub README —
for a third-party AI-agent skill and asks to install it into Hermes. Distinct
from *authoring* a skill (covered by `hermes-agent-skill-authoring`), this is
about **fetching upstream install instructions, downloading a pinned release,
placing it in the Hermes skills tree, and verifying it loads** — without
assuming the local environment matches the doc's expectations.

## When to Use

- User pastes a URL ending in `skill-install.md`, `/README.md`, or a GitHub
  `/releases` page and asks to install the skill it documents.
- User says "install the X skill" and gives a link to its install instructions.
- A third-party skill's install doc tells you to download a tagged release zip
  and move it into a skills directory.
- User reports that a previously-installed third-party skill **didn't survive a
  restart**, or that a registered skill **isn't reaching the backup Bucket**
  after the upload interval. The persistence + uploader-loop diagnostics in
  `references/ephemeral-persistence-lockfile.md` (→ *Diagnosing why the loop
  isn't pushing*) cover this — diagnosing a stalled persistence loop is the
  same class of task as the install, not a separate one.

Don't use for:
- Authoring a brand-new skill from scratch → `hermes-agent-skill-authoring`.
- Installing a skill from the Hermes hub/store → `hermes skills install`.
- Editing a third-party skill you just installed → it's user-owned; see
  *Protected-skill awareness* below.

## Workflow

1. **Fetch the install doc.** `curl -fsSL --max-time 30 <url>`. Read the entire
   doc before acting. It usually contains: the pinned release tag to download
   (prefer a tag over `main`), the target directory name (often the skill's
   declared `name`, sometimes with the version suffix stripped), a post-install
   verification step (entry test or `doc` command), and an **optional**
   API-key / credentials section. **Completion criterion:** you can name the
   release tag, the target dir, and the verification command before downloading.

2. **Resolve the Hermes skills root.** Use `$HERMES_HOME` — never hardcode
   `~/.hermes`. Skills live at `$HERMES_HOME/skills/<category>/<name>/`.
   Confirm the tree exists (`search_files(target='files', path='$HERMES_HOME/skills')`
   or `ls`). **Completion criterion:** you have the absolute base path.

3. **Pick the category.** Match the skill's declared domain to an existing
   category directory (`research`, `productivity`, `software-development`, …).
   Don't invent a new top-level category. **Completion criterion:** the chosen
   `<category>` dir already exists under `$HERMES_HOME/skills/`.

4. **Download + extract.**
   ```bash
   cd /tmp
   curl -L -o skill.zip https://github.com/<org>/<repo>/archive/refs/tags/<tag>.zip
   python3 -c "import zipfile; zipfile.ZipFile('skill.zip').extractall('.')"
   ```
   ⚠ `unzip` is **not** guaranteed on minimal hosts (Amazon Linux 2023, slim
   containers). Prefer Python's stdlib `zipfile` — no extra dep, no dead-end.
   **Completion criterion:** `ls -d <repo>-<tag>/` shows the extracted top dir.

5. **Move into place.** Rename the extracted top dir to the skill's `name`:
   ```bash
   SK=$HERMES_HOME/skills/<category>/<skill-name>
   rm -rf "$SK" 2>/dev/null   # only if re-installing
   mv /tmp/<repo>-<tag> "$SK"
   ```
   **Completion criterion:** `$SK/SKILL.md` exists.

6. **Verify `SKILL.md` parses.** `head -20` it; confirm it starts with `---`,
   has `name` + `description` frontmatter, and looks well-formed.

7. **Run the doc's post-install verification.** Most skills ship multi-runtime
   CLIs (Python / Node / Bash / PowerShell). Follow the doc's priority order
   (commonly **Python > Node.js > Shell**) and probe each available runtime
   against the doc's entry command (usually `doc`). Record which runs cleanest
   (exit 0, no warnings). **Completion criterion:** at least one runtime passes
   the entry command cleanly.

8. **Persist the runtime choice.** Write `<skill_dir>/runtime.conf` (or
   whatever the doc names it) with the chosen `Runtime:` and `Command:` lines:
   ```
   Runtime: Python
   Command: python3 $HERMES_HOME/skills/<category>/<name>/scripts/<cli>.py
   ```
   This file is read on skill load so the agent doesn't re-probe every session.
   If `runtime.conf` already exists, replace — don't append. **Completion
   criterion:** the file exists with both lines, using an absolute path.

9. **Smoke-test a real call.** Run one real `search` / `extract` / equivalent
   against the live endpoint to confirm the API connection + auth path work
   end-to-end, not just that the CLI parses. **Completion criterion:** a real
   result comes back (e.g. JSON/markdown with actual content), exit 0.

10. **Surface credentials (but don't register unprompted).** Most third-party
    skills work anonymously at lower rate limits. Mention the API-key option at
    the end and ask before registering — registration often emails a real
    password to a real address. **Completion criterion:** the user knows the key
    is optional and how to opt in.

11. **Register the skill in `.hub/lock.json` for persistence.**
    Smoke-test success proves the skill works *now*; it says nothing about
    whether it survives a restart. On Space-managed / ephemeral-disk
    deployments, `$HERMES_HOME` is wiped on Restart and only skills listed in
    `$HERMES_HOME/skills/.hub/lock.json` are restored from the Bucket by
    `restore_home_files.py` on the next boot. A manual `curl + extract + mv`
    install is **not** registered — it must be added by hand (mirroring what
    `hermes skills install` would have written via
    `HubLockFile.record_install`). Full step-by-step, the exact JSON shape,
    the two cheap validation checks, and the `.env` / API-key-in-Bucket
    trade-off are in **`references/ephemeral-persistence-lockfile.md`** —
    **read it before performing this step.** Completion criterion: the lock
    entry validates (`_normalize_lock_install_path` + `HubLockFile().list_installed()`
    both succeed) and the user has accepted (or avoided) the Bucket-`.env`
    exposure.

    **Skip this step only on** a non-ephemeral host (laptop, persistent
    server) where `/opt/data` is not wiped on restart. If unsure, do the
    registration anyway — on a persistent host it's a harmless no-op
    footprint, whereas skipping on an ephemeral host loses the install.

## Credentials (when the user opts in)

Follow the install doc's registration call verbatim (usually a single API POST,
no verification code). Branch on the error `message` field per the doc's own
table. On success:

- Write the returned key to the skill's `.env` (or `$HERMES_HOME/.env`),
  **not** `config.yaml` — keys are secrets, settings are config.
- Tell the user the username (= email), the login URL, and that a random
  password was emailed. Relay any spam-folder note the doc provides.

Key priority (typical): `--api_key` flag > `.env` > env var > anonymous.

## Protected-skill awareness

A skill you install from a URL is **user-owned** — curator writes to it will be
refused. Don't try to patch its bundled `SKILL.md` if it's wrong or outdated;
instead tell the user and recommend `hermes curator adopt <name>` so it becomes
curator-managed.

## Common Pitfalls

1. **Hardcoding `~/.hermes`.** On multi-profile or container installs the real
   home is `$HERMES_HOME`. Always resolve from the env var before any path.
2. **Assuming `unzip` exists.** It's missing on minimal Linux images
   (Amazon Linux 2023, slim containers). Use `python3 -c "import zipfile; …"`.
3. **Re-probing the runtime every session.** Persist it to `runtime.conf` once;
   subsequent loads read the file instead of re-running the entry command.
4. **Registering for an API key unprompted.** It sends a real email with a
   password. Always ask first; anonymous access is usually fine to start.
5. **Wrong category.** A search tool → `research/`; a docx tool →
   `productivity/`. Mismatched placement still loads but breaks the class-level
   organization the skill index assumes.
6. **Not smoke-testing the live endpoint.** The entry/`doc` command only
   proves the CLI parses — a real `search`/`extract` proves the API + auth path.
7. **Downloading `main` instead of a tag.** Unreleased `main` can break
   silently. Prefer the latest pinned release tag (check `releases/latest`).
8. **Leaving the version-suffixed dir name.** Extracted release dirs are often
   `<repo>-<tag>`; rename to the skill's `name` per the doc.
9. **Reporting success without confirming restart persistence.** "Smoke-test
   passes" ≠ "survives next Restart." On Space-managed / ephemeral-disk
   deployments, a manual install is gone after Restart unless it's registered
   in `$HERMES_HOME/skills/.hub/lock.json` (Step 11). Don't close the task as
   complete on the live-API smoke test alone — verify the persistence path
   too, or explicitly flag to the user that the install will not survive a
   restart.
10. **Searching clichéd paths for `lock.json` and reporting "no mechanism."**
    The lock file lives at `$HERMES_HOME/skills/.hub/lock.json` — NOT
    `~/.hub/`, `$HERMES_HOME/.hub/`, or `$HERMES_HOME/.hub/lock.json`.
    Searching the wrong three paths and concluding "no uploader / no lock
    mechanism on this host" is a false negative; the mechanism is there,
    the search was wrong. Also note `.hub/` does not exist until a skill is
    registered through it — absence of the directory on a fresh Space is
    expected and says nothing about whether the loop runs.

## Verification Checklist

- [ ] Install doc fetched and read in full before downloading
- [ ] `$HERMES_HOME` resolved, not hardcoded to `~/.hermes`
- [ ] Category chosen from existing dirs; skill moved to
      `$HERMES_HOME/skills/<category>/<name>/`
- [ ] Release extracted (Python `zipfile` if no `unzip`); version-suffix dir
      renamed to the skill `name`
- [ ] `SKILL.md` present and parses (frontmatter starts with `---`)
- [ ] Entry/doc command run against at least one available runtime; cleanest
      runtime identified
- [ ] `runtime.conf` written with absolute `Command:` path
- [ ] Real `search`/`extract` (or equivalent) returned live data, exit 0
- [ ] User told API key is optional; not registered without explicit consent
- [ ] Temp download (`/tmp/*.zip`, `/tmp/<repo>-*`) cleaned up
- [ ] **Persistence:** `$HERMES_HOME/skills/.hub/lock.json` entry written for
      the skill, mirroring `HubLockFile.record_install` shape; `install_path`
      ends with the skill `name` and is relative under `skills/`
- [ ] **Persistence validated:** `_normalize_lock_install_path` succeeds AND
      `HubLockFile().list_installed()` returns the entry (the exact call the
      uploader makes)
- [ ] **If API key written to `$HERMES_HOME/.env`:** user has accepted that
      the key's real value rides into the private Bucket `home-backups/.env`
      (or key is injected as an HF Space Secret instead, in which case it
      never touches `.env`)
- [ ] **On an ephemeral-disk host:** confirmed the uploader loop is wired
      (boot log shows the daemon-up line, or user restarts and boot log
      shows `[restore-home-files] skills/: ok: restored skills/ …`)
