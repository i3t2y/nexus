---
name: github-project-recon
description: "Verify a GitHub repo's README vs its real config files."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Research, GitHub, Reconnaissance, Due-Diligence, Dependencies, Config-Verification]
    category: research
    related_skills: [grounded-citations, codebase-inspection, github-repo-management]
---

# GitHub Project Reconnaissance

Turn "what is this GitHub project / can I self-host it / what does it really
depend on?" into an evidence-grounded report. The README market()s the project;
the **config files, deploy templates, and `.env.example`** are where the
load-bearing facts live. Cross-check the two — that is the whole skill.

Use whenever a task says "research / look into / evaluate repo at
github.com/…", especially when the question is about *positioning* (is it X
or Y?), *dependencies* (needs a cloud service or runs standalone?), or
*fitness for a stated purpose* (suitable as a unified layer?). Skip for tasks
that only need a single file read or a clone-and-build.

## When to Use

- A user hands you a GitHub URL (or a partial/wrong one) and asks what the
  project actually is.
- You need to judge self-hostability vs. cloud-binding, or list real
  dependencies, or settle "does it really support X" claims.
- You are comparing projects and need a standardized, evidence-cited profile
  of each.
- A user is considering adopting a repo and wants the unpolished truth, not
  the README's self-marketing.

Not for: cloning to run LOC metrics (use `codebase-inspection`), opening a
PR/branching (use the `github-pr-workflow` skills), or authoring inside the
repo.

## Procedure

### 1. Recover the true repo path

Users hand over partial or wrong URLs (missing the org, a stale redirect, a
name they half-remember). Do not assume the URL resolves — probe it:

```bash
# Does this path exist?
curl -s -o /dev/null -w '%{http_code}' https://api.github.com/repos/<org>/<name>

# 404? Search the API for the name; read full_name + default_branch from the top hit:
curl -s 'https://api.github.com/search/repositories?q=<keywords+from+name>' \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); i=d["items"][0]; print(i["full_name"], i["default_branch"], i["fork"], i.get("license",{}).get("spdx_id"), i["stargazers_count"])'
```

Record `full_name` and `default_branch` — all later raw-file fetches need the
branch, and it is often **not** `main`/`master` (feature branches, `develop`).
Confirm the hit is not a fork: `fork: false` and a verified org identity beat a
random user's mirror.

### 2. Pull the README — but treat it as a *claim set*, not ground truth

```bash
curl -sL "https://raw.githubusercontent.com/<org>/<name>/<branch>/README.md" -o read.md
curl -s "https://api.github.com/repos/<org>/<name>/contents/"   # root listing
```

From the README, extract the project's *claims*: stated positioning, the
feature list, any "what it is / isn't" section, self-comparison tables, the
install snippet, the **acknowledgements** (these name upstream deps the project
built on — high-signal, rarely marketing). Hold each as a claim to verify,
not a fact to repeat.

> A product-noun in the repo name or a README badge is **not evidence** the
> project depends on that product. "TencentDB-Agent-Memory" uses plain SQLite by
> default with zero Tencent-cloud dependency; the name was branding. The proof
> lived in `tdai-gateway.standalone.yaml`, not the README.

### 3. Verify each load-bearing claim against the actual files

This is the move that turns a paraphrase into a report. For every
"X requires Y / stores in Z / supports W / is cloud-bound" claim the report
will make, point at the file that proves it:

| Claim class | Where the truth lives | What to fetch |
|---|---|---|
| Storage backend (SQLite/Postgres/vector DB) | config yamls + store code | `*.gateway*.yaml`, `*.config.*`, `src/**/store*` |
| External dependencies / required services | `.env.example`, `docker-compose*.yml`, install scripts | `deploy/**/.env.example`, `start-*.sh` |
| Self-hostable vs. cloud-bound | deploy templates (standalone vs service) | `README.docker.md`, `Dockerfile`, `deploy/**/*.yaml` |
| Real client/framework support list | `INSTALL.md` per-client config sections | `INSTALL.md` — read past the intro; per-client headers are the evidence |
| License + author/owner | `LICENSE` file directly | fetch `LICENSE`; don't trust the badge |
| Version / recency | `CHANGELOG.md`, tags, API `pushed_at`/`updated_at` | `CHANGELOG.md`, API timestamps |

Strip comments and read the live values:

```bash
curl -sL 'https://raw.githubusercontent.com/<o>/<n>/<b>/<path>.yaml' \
  | grep -ivE '^#|^\s*$' | head -50
```

When a config exposes modes (e.g. `storeBackend: "sqlite"` with a commented
`tcvdb` block, or a `standalone.yaml` vs `service.yaml` pair), that split IS
the answer to "self-host or cloud": **standalone = zero-external-dep, service =
the optional cloud deps are listed but not forced**. Report both modes; do not
collapse to one.

### 4. Cite the file, not the README paragraph

In the deliverable, cite the specific file each fact came from (config path +
key/value, or README section + sentence). For anything you could NOT confirm
from a file, say so explicitly ("README claims P; no config corroborates
this"). Same `[unverified]` posture as `grounded-citations`.

### 5. Optional — corroborate context from the tree

If the project's *intent* matters (roadmap direction, governance model,
whether features are aspirational vs. shipped), grab `ROADMAP.md`,
`CONTRIBUTING.md`, `CHANGELOG.md`, and recent issue counts from the repo
metadata. These color the report but don't substitute for config evidence on
the "what does it need today" questions.

## API endpoints

All GitHub REST endpoints used here are unauthenticated and rate-limited
(~60 req/h per IP); fine for one repo, throttle if batching. Alternatives if
rate-limited: `gh api` (uses an authenticated token if `gh auth login` was
done — see the `github-auth` skill), or `git clone --depth 1` and read locally.
A shallow clone is worth it only when you need to grep across `src/` or run a
build; for top-level recon the raw-file fetches are faster.

- `GET /repos/<o>/<n>` — repo metadata (default branch, license, stars, dates)
- `GET /search/repositories?q=...` — recover a repo from a partial name
- `GET /repos/<o>/<n>/contents/<path>` — directory listing (JSON)
- `raw.githubusercontent.com/<o>/<n>/<branch>/<path>` — raw file contents

## Pitfalls

- **Paraphrasing the README as the report.** It market()s the project; its nouns
  are brand, its tables are positioning. The config's keys are fact. Do not
  ship until each "requires/uses/stores" claim has a file behind it.
- **Assuming `default_branch` is `main`.** It frequently isn't (feature
  branches, `develop`). Get it from the API; otherwise raw-file fetches 404
  silently and you report a wrong/empty file as "the project doesn't document
  X" — when you just fetched the wrong branch.
- **Reading commented yaml blocks as enabled config.** Comment-wise-out blocks
  are not active; reading them as enabled inflates the dependency list.
  Conversely, deps that appear only in a `service` template or only inside a
  commented opt-in are **optional** — never report them as required. This is
  the single most common recon error.
- **Collapsing "supports X" to the client-name list in the README.** The
  authoritative support list lives in `INSTALL.md`'s per-client config
  sections — count those headers, not the badge row.
- **Reporting a fork/mirror as canonical.** The search hit's `fork: false` and
  the org's verified identity beat a random user's mirror. Re-run the search if
  the top hit is a fork and scan for the parent.
- **Citing a search snippet as if you read the page.** The repo description field
  says what the page literally says; cite the fetched README/INSTALL/config when
  the claim needs the body.

## Verification

A report passes when, for each load-bearing claim ("stores in X", "requires
Y", "supports Z", "is self-hostable"), there is a `path:<file>` citation the
reader can reopen and confirm — and every claim with no backing file carries an
explicit "not corroborated by config" note. Re-verify by re-fetching the cited
file at the branch you recorded; if it has moved or changed, the report needs
an update.

A worked end-to-end example (TencentDB-Agent-Memory, with every claim → file
citation) is in `references/tencentdb-agent-memory-example.md`.

## See also

- `grounded-citations` — the general citation/ledger discipline; pair this skill's
  evidence-gathering with that skill's numbering and verbatim-quote machinery for
  the strongest deliverable.
- `codebase-inspection` — LOC/language metrics on a cloned repo (different class:
  static analysis after clone, not remote recon before clone).
- `github-repo-management` — clone/fork/manage remotes and releases.
