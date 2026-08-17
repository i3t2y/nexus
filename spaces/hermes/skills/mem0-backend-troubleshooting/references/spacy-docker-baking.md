# Baking spaCy into the Hermes Docker Image

mem0 logs `WARNING: Failed to load spaCy lemma model: spaCy is not installed. Install it with: pip install mem0ai[nlp]`. This is optional — vector semantic search works without it. spaCy only adds lemmatization to the GIN full-text index (`to_tsvector('simple', ...)` fallback vs spaCy-powered lemmatized index).

## Current Architecture Constraint

mem0 is a **lazy-install dependency** in Hermes — deliberately excluded from the Docker image's `uv sync --extra all` chain (see `pyproject.toml` L205-211: "Cloud memory providers — opt-in, lazy-installed at first use"). mem0 installs to `/opt/data/lazy-packages` (ephemeral, wiped on restart). So spaCy needs to be baked separately — not via `mem0ai[nlp]` — to avoid breaking the quarantine policy.

## Three Approaches

### Approach A (recommended): Add spaCy as a standalone extra

**pyproject.toml** — add a new extra alongside `mem0`:
```toml
spacy-lemma = ["spacy>=3.7.0"]
```

**Dockerfile** L267 — add `--extra spacy-lemma` to the `uv sync` command:
```dockerfile
RUN uv sync --frozen --no-install-project --extra all --extra messaging --extra otlp --extra anthropic --extra bedrock --extra azure-identity --extra hindsight --extra matrix --extra spacy-lemma
```

**Also install the language model** (spaCy package alone is useless — you need `en_core_web_sm`):
```dockerfile
RUN uv pip install https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.7.0/en_core_web_sm-3.7.0-py3-none-any.whl
```

**Pros:** Cleanest. Doesn't break mem0's quarantine. spaCy available on Python path → mem0's `spacy.load("en_core_web_sm")` succeeds automatically.

**Cons:** Adds ~50MB (spacy) + ~50MB (model) to image. Upstream Dockerfile changes need manual rebase.

### Approach B: Bake mem0ai[nlp] into the image

Change `pyproject.toml` L211:
```toml
mem0 = ["mem0ai==2.0.10", "mem0ai[nlp]==2.0.10"]
```
Add `--extra mem0` to Dockerfile `uv sync`.

**Cons:** Violates the quarantine policy — a mem0 upstream release could break the Docker build. Not recommended.

### Approach C: Lazy-install mem0ai[nlp] at runtime

Change `tools/lazy_deps.py` to install `mem0ai[nlp]==2.0.10` instead of `mem0ai==2.0.10`.

**Cons:** Not persistent — installs to ephemeral `/opt/data/lazy-packages`, re-downloaded every restart (~100MB). Model binary download may fail in restricted networks.

## When to Actually Install spaCy

**Don't install preemptively.** Install only when you observe mem0_search missing semantically-related memories that share word stems (e.g. "running" doesn't recall a memory containing "ran"). The primary recall path is vector embedding similarity, which already works well without spaCy (verified score 0.689 in end-to-end testing).

## Dockerfile Location

```
/opt/hermes-agent/Dockerfile  (L267 = the uv sync command)
/opt/hermes-agent/pyproject.toml  (L210-211 = mem0 extra declaration)
```

Note: These are upstream Hermes files. Changes require maintaining a fork patch. If you're on HF Space (no custom Docker build), the only persistent path is to convince the upstream to include spaCy, or bake it into a custom Docker image.
