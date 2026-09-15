# mem0 server_state.py — Config Loading and Override Logic

Source: `server/server_state.py` (main branch, Aug 2026)

This module manages mem0 server's runtime config. Understanding it is critical for debugging `openai_base_url` loss and `datastore_unavailable` errors.

## Key Functions

### `initialize_state(default_config)`
Called once at startup (main.py line 147) with `DEFAULT_CONFIG`:
```python
def initialize_state(default_config):
    _current_config = deepcopy(default_config)       # ← your patched DEFAULT_CONFIG
    overrides = _load_overrides()                      # ← reads Neon settings table
    if overrides:
        _current_config = _merge_config(_current_config, overrides)
    _memory_instance = Memory.from_config(_current_config)
```

### `_load_overrides()`
Reads the `settings` table from Neon, looking for a row with `key='config_overrides'`:
```python
def _load_overrides():
    session = _session_factory()
    row = session.get(Settings, "config_overrides")
    if row is None:
        return {}
    return json.loads(row.value)   # ← JSON dict of partial config
```
Returns `{}` if no overrides stored. This is the normal state when using the DEFAULT_CONFIG patch approach (never calling `/configure`).

### `_save_overrides(overrides)`
Called by `update_config()` (i.e., `POST /configure`):
```python
def _save_overrides(overrides):
    serialized = json.dumps(overrides)
    stmt = insert(Settings).values(key="config_overrides", value=serialized)
        .on_conflict_do_update(...)   # upsert
    session.execute(stmt)
```
**This is where Issue #4910 strikes**: `update_config()` receives the config through Pydantic models that strip `openai_base_url`, then saves the stripped version to DB. On next restart, `_load_overrides()` returns config WITHOUT `openai_base_url`.

### `_merge_config(base, updates)`
Deep merge — for each key in `updates`, if both base[key] and updates[key] are dicts, recurse; otherwise replace:
```python
def _merge_config(base, updates):
    merged = deepcopy(base)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_config(merged[key], value)
        else:
            merged[key] = value
    return merged
```
This means overrides replace field-by-field, not whole-section. If override has `{"llm": {"config": {"api_key": "xxx"}}}` but no `openai_base_url`, the merge keeps DEFAULT_CONFIG's `openai_base_url` — UNLESS the override was saved through Pydantic which strips the field entirely (the field is absent from the JSON, so merge keeps base value). Actually, if the key is ABSENT from overrides, merge keeps base. The problem is when `/configure` is called with an LLM config Pydantic model that rejects `openai_base_url` as an extra field — the save fails or strips it.

## verify_auth Flow (auth.py)

```python
async def verify_auth(request, credentials=Depends(bearer_scheme), x_api_key=Depends(api_key_header)):
    if credentials is not None:          # line 158 — Bearer header?
        return _resolve_user_from_jwt()  # → RAISES 401 if not valid JWT
    if x_api_key is not None:            # line 163 — X-API-Key?
        if ADMIN_API_KEY and match: return None
        return _resolve_user_from_api_key()
    if AUTH_DISABLED:                     # line 171 — only if NO auth headers
        return None
    raise HTTPException(401)             # line 175
```

Key: Bearer (line 158) is checked BEFORE AUTH_DISABLED (line 171). If `Authorization` header is present, AUTH_DISABLED is never reached.

## BUNDLED_PROVIDERS (main.py)

```python
BUNDLED_LLM_PROVIDERS = ("openai", "anthropic", "gemini")
BUNDLED_EMBEDDER_PROVIDERS = ("openai", "gemini")
```
Using `provider: "openai"` with a custom `openai_base_url` is allowed — no validation error. This is how we point "openai" provider to NIM/智谱.

## psycopg3 ConnectionPool — No Health Check by Default

The `pgvector.py` `__init__` creates `ConnectionPool(conninfo=..., min_size=minconn, max_size=maxconn, open=False)` with **no `check` parameter**. This means:

- Neon scale-to-zero severs the TCP connection silently
- The pool doesn't know — it keeps the dead connection in its pool
- Next checkout hands out the dead connection
- First query fails with `psycopg.OperationalError: the connection is lost`
- The `_get_cursor` context manager catches this, tries `conn.rollback()` — which ALSO fails (connection is lost)
- `errors.py` `_classify_one` sees `OperationalError` → returns `("datastore_unavailable", "The memory database is unreachable.")`

**Fix**: Add `check=self._check_conn` to the `ConnectionPool(...)` call and define the `_check_conn` method. See `references/mem0-server-config.md` § "Neon scale-to-zero + psycopg3 ConnectionPool" for the full patch.
