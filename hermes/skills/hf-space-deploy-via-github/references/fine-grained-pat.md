# Fine-Grained PAT Permissions

Fine-grained PATs (`github_pat_` prefix) scope to specific repositories with granular permissions. Unlike classic tokens (broad `repo`/`workflow` scopes), each permission must be selected explicitly.

## Required Permissions for Repo + Actions + Secrets Management

| Permission | Level | Purpose |
|---|---|---|
| **Contents** | Read and write | push/pull code |
| **Actions** | Read and write | trigger/view workflows |
| **Secrets** | Read and write | `gh secret set` for CI secrets |
| **Workflows** | Read and write | modify `.github/workflows/*.yml` |
| **Metadata** | Read-only | auto-required, always checked |
| **Administration** | Read and write | only if creating the repo via API |

Permissions NOT needed: Issues, Pull requests, Deployments, Pages, Webhooks, Packages.

## Known Limitations of Fine-Grained PATs

- **`checks:read` is NOT available** on fine-grained PATs. `gh run view --log-failed` returns 403 with annotation: "it is not currently possible to create a fine-grained PAT with the `checks:read` permission." Workaround: use `gh api repos/OWNER/REPO/actions/jobs/JOB_ID/logs --allow-escape-sequences` to fetch raw logs via REST API (bypasses checks:read requirement), or view on GitHub Web UI.
- **`actions/permissions` API also 403s** without `Actions: Read` permission — ensure Actions is set to "Read and write" not just "Read".
- **Private repos use limited Actions minutes** (2,000/month on free). If Actions fail with quota errors, temporarily switch repo to public (unlimited Actions) to debug, then switch back.
- **Token expiration** recommended 90 days. Avoid "No expiration" to limit blast radius.
- **Repository selection**: Select specific repos (e.g., only `n-nmem`) rather than "All repositories" to minimize scope.

## Using with gh CLI

```bash
# Fine-grained PATs work the same as classic tokens
echo "github_pat_..." | gh auth login --with-token
gh auth status  # shows account name + token prefix github_pat_
```

## Storing PAT on HF Space

Store in HF Secrets (Settings → Secrets → `GITHUB_TOKEN`). On HF Space, it's injected into `$HERMES_HOME/.env` and survives restarts:

```bash
export GITHUB_TOKEN=$(grep '^GITHUB_TOKEN=' "${HERMES_HOME:-$HOME/.hermes}/.env" | cut -d= -f2-)
```

## Where to Create

https://github.com/settings/personal-access-tokens (fine-grained)  
https://github.com/settings/tokens (classic, if fine-grained is too restrictive)
