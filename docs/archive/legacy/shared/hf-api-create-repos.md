# Creating HF Spaces and Datasets via API

When deploying to HF, Spaces and Datasets can be created via API (curl) or web UI. API creation requires a token with the right permissions.

## Creating a Space

```bash
# Token must have Spaces: Write permission
curl -sL -X POST "https://huggingface.co/api/repos/create" \
  -H "Authorization: Bearer $HF_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"type":"space","name":"memg","private":true,"sdk":"docker"}'
```

**Key**: `name` field is just the repo name WITHOUT namespace prefix. The namespace is determined by the token's owner. So `name: "memg"` creates `<token-owner>/memg`, NOT `nmem/memg` unless the token belongs to nmem.

## Creating a Dataset

```bash
curl -sL -X POST "https://huggingface.co/api/repos/create" \
  -H "Authorization: Bearer $HF_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"type":"dataset","name":"nworker","private":true}'
```

Same rule: `name` is just the repo name, namespace comes from the token owner.

## Token Permission Check

Before creating, verify the token has the right permissions:

```bash
# Check which account the token belongs to
curl -sL -H "Authorization: Bearer $HF_TOKEN" "https://huggingface.co/api/whoami-v2" | python3 -c "
import json, sys
d = json.load(sys.stdin)
print(f'type={d.get(\"type\")} name={d.get(\"name\")}')
"

# List existing Spaces for the account
curl -sL -H "Authorization: Bearer $HF_TOKEN" "https://huggingface.co/api/spaces?author=$(whoami)"
```

## Uploading Files to a Dataset

```python
from huggingface_hub import HfApi
import inspect

api = HfApi(token=HF_TOKEN)

# Check API signature first — param names change across versions
sig = inspect.signature(api.upload_file)
print(list(sig.parameters.keys()))
# v1.26: path_or_fileobj, path_in_repo, repo_id, token, repo_type, ...

# Upload single file
api.upload_file(
    path_or_fileobj='/local/path/nodes.py',
    path_in_repo='graph/nodes.py',
    repo_id='nmem/nworker',
    repo_type='dataset',
)

# Upload entire folder
api.upload_folder(
    folder_path='/local/worker/',
    repo_id='nmem/nworker',
    repo_type='dataset',
    commit_message='initial: worker skeleton',
)
```

## Token from a Different HF Account

When the agent (e.g., sonoke) needs to manage repos on another HF account (e.g., nmem):

1. **Cannot use agent's token** — sonoke's token has no visibility into nmem's private repos
2. **Store the other account's token in `.env`** — add as a separate HF_TOKEN in HF Secrets
3. **Read the right token** — daemon environ `/proc/1/environ` has one token, `.env` may have another; use `whoami-v2` to identify which is which
4. **Token must have the right scopes** — read-only tokens can't create Spaces; need `Spaces: Write` or equivalent

## HF Token Permission Levels

| Token Type | Create Space | Upload to Dataset | Push to Space git | Read private repos |
|---|---|---|---|---|
| Read-only | ❌ | ❌ | ❌ | ✅ (own repos) |
| Fine-grained (Spaces: Write) | ✅ | ❌ | ✅ | ✅ (own Spaces) |
| Fine-grained (Datasets: Write) | ❌ | ✅ | ❌ | ✅ (own Datasets) |
| Full (classic `write`) | ✅ | ✅ | ✅ | ✅ |

For deployment automation, use a **classic `write` token** from the Space owner's account to avoid permission fragmentation.
