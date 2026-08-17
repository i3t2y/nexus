#!/usr/bin/env python3
"""
Patch DEFAULT_CONFIG in /app/main.py:
  - embedder: NIM (nvidia/nemotron-3-embed-1b, integrate.api.nvidia.com)
  - LLM: Zhipu (glm-4.7-flash, api.z.ai)

Why patch source instead of /configure API?
  - /configure needs JWT admin auth (verify_auth line 733: Bearer credentials → JWT → 401)
  - AUTH_DISABLED only works when no Bearer header present (line 755)
  - /configure saves to Neon settings table as config_overrides
  - server_state initialize_state (line 405) deep-merges config_overrides over DEFAULT_CONFIG
  - Issue #4910/#4984: Pydantic strips openai_base_url from overrides → embedder goes to api.openai.com

Environment variables (HF Space Secrets):
  NIM_API_KEY   or OPENAI_API_KEY — NIM embedder key
  ZAI_API_KEY   or OPENAI_API_KEY — Zhipu LLM key
  NIM_BASE_URL  default https://integrate.api.nvidia.com/v1
  ZAI_BASE_URL  default https://api.z.ai/api/paas/v4
"""
import os
from pathlib import Path

MAIN = Path("/app/main.py")
code = MAIN.read_text()

# Insert separate env vars before DEFAULT_CONFIG
extra_vars = '''
# --- Patched: separate keys for embedder (NIM) and LLM (Zhipu) ---
NIM_API_KEY = os.environ.get("NIM_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
ZAI_API_KEY = os.environ.get("ZAI_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
NIM_BASE_URL = os.environ.get("NIM_BASE_URL", "https://integrate.api.nvidia.com/v1")
ZAI_BASE_URL = os.environ.get("ZAI_BASE_URL", "https://api.z.ai/api/paas/v4")
'''

if "NIM_API_KEY" not in code:
    code = code.replace("DEFAULT_CONFIG = {", extra_vars + "\nDEFAULT_CONFIG = {")
    print("[10] Inserted NIM/Zhipu env vars")
else:
    print("[10] NIM/Zhipu env vars already present")

# Replace LLM config
code = code.replace(
    '"config": {"api_key": OPENAI_API_KEY, "temperature": 0.2, "model": DEFAULT_LLM_MODEL},',
    '"config": {"api_key": ZAI_API_KEY, "temperature": 0.1, "model": "glm-4.7-flash", "openai_base_url": ZAI_BASE_URL, "max_tokens": 2000},'
)

# Replace embedder config
code = code.replace(
    '"embedder": {"provider": "openai", "config": {"api_key": OPENAI_API_KEY, "model": DEFAULT_EMBEDDER_MODEL}},',
    '"embedder": {"provider": "openai", "config": {"api_key": NIM_API_KEY, "model": "nvidia/nemotron-3-embed-1b", "openai_base_url": NIM_BASE_URL}},'
)

# Add embedding_model_dims: 2048 + hnsw: false to pgvector vector_store config
# NIM nemotron-3-embed-1b outputs 2048-dim vectors; pgvector defaults to 1536
# HNSW index max 2000 dims → 2048 exceeds limit → disable HNSW, use brute-force scan
if "embedding_model_dims" not in code:
    code = code.replace(
        '"collection_name": POSTGRES_COLLECTION_NAME,\n        },',
        '"collection_name": POSTGRES_COLLECTION_NAME,\n            "embedding_model_dims": 2048,\n            "hnsw": False,\n        },',
    )
    print("[10] Added embedding_model_dims: 2048 + hnsw: False to vector_store config")
else:
    # dims already added — make sure hnsw: False is also there
    if "hnsw" not in code:
        code = code.replace(
            '"embedding_model_dims": 2048,\n        },',
            '"embedding_model_dims": 2048,\n            "hnsw": False,\n        },',
        )
        print("[10] Added hnsw: False to existing vector_store config")
    else:
        print("[10] embedding_model_dims + hnsw already present")

MAIN.write_text(code)
print("[10] DEFAULT_CONFIG patched: embedder→NIM(2048d, no HNSW), LLM→Zhipu")
