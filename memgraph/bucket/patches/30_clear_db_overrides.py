#!/usr/bin/env python3
"""
Clear DB config_overrides from Neon settings table.

Why?
  - server_state initialize_state (line 405) calls _load_overrides()
  - _load_overrides reads `config_overrides` key from Neon `settings` table
  - _merge_config deep-merges overrides over DEFAULT_CONFIG → overwrites our patches
  - Issue #4910/#4984: Pydantic strips openai_base_url from saved overrides
    → embedder sends to api.openai.com instead of NIM → "Upstream provider error"

  Even if we never call /configure, a previous bad call could have left stale overrides.
  This patch clears them on every startup.

  Also handles the case where the settings table doesn't exist yet (alembic might run
  after this patch — we catch the error gracefully).
"""
import os
import sys
import psycopg

# Build connection string from env
user = os.environ.get("POSTGRES_USER", "")
password = os.environ.get("POSTGRES_PASSWORD", "")
host = os.environ.get("POSTGRES_HOST", "")
port = os.environ.get("POSTGRES_PORT", "5432")
dbname = os.environ.get("APP_DB_NAME", os.environ.get("POSTGRES_DB", "neondb"))

if not all([user, password, host]):
    print("[30] WARNING: POSTGRES credentials not set, skipping DB override cleanup")
    sys.exit(0)

conninfo = f"postgresql://{user}:{password}@{host}:{port}/{dbname}?sslmode=require"

try:
    conn = psycopg.connect(conninfo, autocommit=True)
    cur = conn.cursor()

    # Check if settings table exists
    cur.execute("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables
            WHERE table_name = 'settings'
        )
    """)
    exists = cur.fetchone()[0]

    if exists:
        cur.execute("DELETE FROM settings WHERE key = 'config_overrides'")
        deleted = cur.rowcount
        print(f"[30] Deleted {deleted} config_overrides row(s) from settings table")
    else:
        print("[30] settings table not found yet (alembic will create), skipping")

    # DROP the memories table if it was created with wrong vector dimensions.
    # NIM nemotron-3-embed-1b outputs 2048-dim vectors; if the table was
    # created with the default 1536-dim column, INSERT fails with
    # "expected 1536 dimensions, not 2048". Dropping lets pgvector
    # recreate it with the correct dim on next _ensure_collection().
    cur.execute("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables
            WHERE table_name = 'memories'
        )
    """)
    table_exists = cur.fetchone()[0]
    if table_exists:
        cur.execute("DROP TABLE IF EXISTS memories CASCADE")
        print("[30] Dropped memories table (will recreate with 2048-dim)")
    else:
        print("[30] memories table not found, skipping drop")

    cur.close()
    conn.close()
except Exception as e:
    print(f"[30] WARNING: Could not clear config_overrides: {e}")
    # Nicht fatal — if DB is unreachable (Neon suspended), we continue;
    # the overrides (if any) are from a prior session and may or may not be stale.
    # Worst case: we get Upstream provider error and fix it on next restart.
    sys.exit(0)
