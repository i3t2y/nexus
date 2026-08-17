#!/usr/bin/env python3
"""
Patch mem0 pgvector.py — two fixes for Neon compatibility:

1. ConnectionPool check callback:
   Neon scale-to-zero severs pooled connections silently. Without a check
   callback, psycopg3 hands out dead connections → "the connection is lost"
   OperationalError on first use. The check callback runs before each checkout;
   if it fails the pool discards the connection and creates a new one.
   Per psycopg3 docs (v3.2+, ticket #790): use ConnectionPool.check_connection
   static method which sends an empty query (lighter than SELECT 1).

2. CREATE EXTENSION → pass:
   Neon neondb_owner inherits neon_superuser but is NOT a true Postgres
   superuser. CREATE EXTENSION requires superuser even if the extension
   already exists. pgvector is pre-installed via Neon Console (cloud_admin).
"""
import sys
import re
from pathlib import Path

PV = Path("/usr/local/lib/python3.12/site-packages/mem0/vector_stores/pgvector.py")

if not PV.exists():
    print(f"[20] WARNING: {PV} not found, skipping")
    sys.exit(0)

code = PV.read_text()
changed = False

# ── Fix 1: Add check= callback to ConnectionPool ──────────────────
POOL_OLD = """self.connection_pool = ConnectionPool(
                    conninfo=connection_string,
                    min_size=minconn,
                    max_size=maxconn,
                    open=False,
                )"""

POOL_NEW = """self.connection_pool = ConnectionPool(
                    conninfo=connection_string,
                    min_size=minconn,
                    max_size=maxconn,
                    open=False,
                    check=ConnectionPool.check_connection,
                )"""

# check_connection is a static method — no additional code needed.

if "check=ConnectionPool.check_connection" in code:
    print("[20] ConnectionPool check= already present")
elif POOL_OLD in code:
    code = code.replace(POOL_OLD, POOL_NEW)
    changed = True
    print("[20] ConnectionPool check= callback added")
else:
    print("[20] WARNING: ConnectionPool creation site not found (mem0 may have updated)")

# ── Fix 2: CREATE EXTENSION → pass ────────────────────────────────
EXT_OLD = 'cur.execute("CREATE EXTENSION IF NOT EXISTS vector")'
EXT_NEW = 'pass  # CREATE EXTENSION removed — pre-installed via Neon Console'

if EXT_NEW in code:
    print("[20] CREATE EXTENSION already patched")
elif EXT_OLD in code:
    code = code.replace(EXT_OLD, EXT_NEW)
    changed = True
    print("[20] CREATE EXTENSION → pass")
else:
    pattern = r'cur\.execute\(\s*["\']CREATE\s+EXTENSION\s+IF\s+NOT\s+EXISTS\s+vector["\']\s*\)'
    if re.search(pattern, code, re.IGNORECASE):
        code = re.sub(pattern, EXT_NEW, code, flags=re.IGNORECASE)
        changed = True
        print("[20] CREATE EXTENSION → pass (regex fallback)")
    else:
        print("[20] WARNING: CREATE EXTENSION line not found (non-fatal)")

if changed:
    PV.write_text(code)
    print("[20] pgvector.py written")
else:
    print("[20] no changes needed")
