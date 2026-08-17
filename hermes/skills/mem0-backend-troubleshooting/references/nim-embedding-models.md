# NIM Embedding Model Probe Results

Probed 2026-08-14 with a NVIDIA API key (`nvapi-...`). All 13 embedding models listed by NIM `/v1/models` endpoint, tested via `POST /v1/embeddings` with `{"input": ["test"], "model": "<id>", "encoding_format": "float"}` (no `input_type` param — symmetric call shape).

## Results Table

| Model | Status | Dims | Notes |
|-------|---------|------|-------|
| **nvidia/nemotron-3-embed-1b** | ✅ 200 | 2048 | **Only symmetric working model.** No `input_type` required. |
| nvidia/nv-embedqa-e5-v5 | 400 | — | Asymmetric: requires `input_type=query\|passage` (mem0 doesn't send this) |
| nvidia/llama-nemotron-embed-1b-v2 | 400 | — | Asymmetric: requires `input_type` |
| nvidia/llama-nemotron-embed-vl-1b-v2 | 400 | — | Asymmetric: requires `input_type` |
| nvidia/nv-embedcode-7b-v1 | 400 | — | Asymmetric: requires `input_type` |
| baai/bge-m3 | 500 | — | Account-level unavailable (NIM returns 500, not 404) |
| nvidia/embed-qa-4 (and 5 other 404 models) | 404 | — | "Function not found for account" — key tier doesn't include these |
| nvidia/nv-embed-v1 | timeout | — | Possibly needs retry or account tier |

## Key Findings

1. **NIM uses 500 (not 404) for account-level model unavailability.** `baai/bge-m3` returns 500 "Something went wrong" even though `/v1/models` lists it. This is the same error mem0 surfaces in `errors.log` — it looks like a server error but is actually a permission/tier issue.

2. **symmetric vs asymmetric is the critical distinction.** mem0's `OpenAIEmbedder.embed()` does not send `input_type` — OpenAI's own embeddings don't need it. But NIM's asymmetric models (E5 family, nemotron-embed-v2) reject calls without it with 400. Only symmetric models work with mem0 out of the box.

3. **Only one model works with this key tier:** `nvidia/nemotron-3-embed-1b` (2048 dims). If this model gets deprecated or the key changes, re-probe with the script in SKILL.md Step 3.

## Reproduction

```python
import httpx
nv = "<your nvapi key>"
r = httpx.get("https://integrate.api.nvidia.com/v1/models",
              headers={"Authorization": f"Bearer {nv}"}, timeout=15)
emb_models = [m["id"] for m in r.json()["data"] if "embed" in m["id"].lower()]
for model in emb_models:
    body = {"input": ["test"], "model": model, "encoding_format": "float"}
    r = httpx.post("https://integrate.api.nvidia.com/v1/embeddings",
                   headers={"Authorization": f"Bearer {nv}"}, json=body, timeout=30)
    status = r.status_code
    dim = len(r.json()["data"][0]["embedding"]) if status == 200 else "?"
    print(f"{model}: {status} dims={dim}")
```

## Why Not Use input_type for Asymmetric Models?

mem0's `OpenAIEmbedder.embed()` source (mem0ai 2.0.10):

```python
def embed(self, text, memory_action=None):
    text = text.replace("\n", " ")
    kwargs = {"input": [text], "model": self.config.model, "encoding_format": "float"}
    if self._pass_dimensions_to_api:
        kwargs["dimensions"] = self.config.embedding_dims
    return self.client.embeddings.create(**kwargs).data[0].embedding
```

No `input_type` is ever added to kwargs. To support asymmetric NIM models, you'd need to either:
- Monkey-patch `OpenAIEmbedder.embed()` to inject `input_type` based on `memory_action`
- Write a custom embedder class subclassing `EmbeddingBase`
- File a feature request with mem0 to support provider-specific extra params

All three are more invasive than simply using the one symmetric model that works.
