# HF Fine-Grained Token: UI Walkthrough

Creating a fine-grained HF token for deploying to a different account's Space via GitHub Actions. The HF token UI has specific options that must be selected correctly or deployment silently fails.

## When You Need This

- Deploying to a Space owned by a **different HF account** than the agent's
- GitHub Actions needs to `git clone` + `git push` to that Space
- Uploading files to a Dataset on that account
- API calls to verify Space status fail with "Repository not found" even though the Space exists

## Step-by-Step (HF Web UI)

1. Log into the **Space owner's HF account** (not the agent's account)
2. Go to https://huggingface.co/settings/tokens
3. Click **New token**

### Token Settings

| Field | Value |
|---|---|
| Token name | `<purpose>-deploy` (e.g., `n-nmem-deploy`) |
| Token type | **Fine-grained** |
| Permission level | **Write** (NOT Read — cannot push to Space git with Read) |

### User Permissions (account-level)

Under the Repositories section, check:

- ✅ **Read contents of your repos**
- ✅ **Write contents/settings of your repos**

Do NOT check: Inference, Webhooks, Collections, Billing, Jobs, Notifications, Discussions, Posts.

### Repository Permissions (repo-level)

If the UI shows a repo selector for `spaces/<account>/<space-name>`, check:

- ✅ **Write contents/settings of selected repos**

This grants per-Space write access without full-account access.

## Common Failure Modes

| Symptom | Cause | Fix |
|---|---|---|
| `git clone` returns "Repository not found" | Token has no Spaces read permission | Regenerate with Write permission level |
| `curl /api/spaces/<account>/<space>` returns 404 | Token is read-only or belongs to a different account | Use the Space owner's token, verify with `whoami-v2` |
| `"You don't have the rights to create a space under the namespace"` | Token permission level is Read, not Write | Re-create token with Write level |
| Dataset upload works but Space clone fails | Token has Datasets:Write but NOT Spaces:Write (fragmented fine-grained) | Check both repo-type permissions in token settings |
| Actions deploy succeeds but "No changes, skipping push" | Space files already match repo files (idempotent) | Change a file or add empty commit to force deploy |

## Verifying Which Account a Token Belongs To

```bash
curl -sL -H "Authorization: Bearer $HF_TOKEN" "https://huggingface.co/api/whoami-v2" | python3 -c "
import json, sys
d = json.load(sys.stdin)
print(f'type={d.get(\"type\")} name={d.get(\"name\")}')"
```

Critical when multiple HF accounts are in play and tokens are stored in different env locations:
- `/proc/1/environ` (daemon) — typically the agent's primary account (e.g., sonoke)
- `$HERMES_HOME/.env` — may contain a secondary account's token (e.g., nmem)

Always verify before using. Wrong token → "Repository not found" on private repos.
