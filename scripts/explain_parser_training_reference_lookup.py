"""Run EXPLAIN ANALYZE for parser_training_rows reference lookups.

Requires a direct Postgres connection (Supabase SQL editor or pooler URI).
PostgREST/Supabase REST cannot execute EXPLAIN.

Usage:
  set SUPABASE_DB_CONNECTION_URI=postgresql://...
  python scripts/explain_parser_training_reference_lookup.py 5524G
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database import PARSER_TRAINING_REFERENCE_ROW_COLUMNS, parser_training_reference_lookup_key

EXPLAIN_SQL = """
EXPLAIN (ANALYZE, BUFFERS)
SELECT {columns}
FROM parser_training_rows
WHERE normalized_reference = %s
ORDER BY id
LIMIT 50 OFFSET 0;
""".format(columns=PARSER_TRAINING_REFERENCE_ROW_COLUMNS)


def main(argv: list[str]) -> int:
    reference = argv[1] if len(argv) > 1 else "5524G"
    key = parser_training_reference_lookup_key(reference)
    if not key:
        print("Reference is required.")
        return 1

    connection_uri = (
        os.getenv("SUPABASE_DB_CONNECTION_URI")
        or os.getenv("DATABASE_URL")
        or os.getenv("DATABASE_CONNECTION_URI")
    )
    if not connection_uri or "supabase" not in connection_uri.lower():
        print(
            "Set SUPABASE_DB_CONNECTION_URI to your Supabase pooler/direct Postgres URI."
        )
        print("Then apply docs/migrations/sprint_50_4_parser_training_reference_indexes.sql")
        print(f"Lookup key for {reference!r}: {key}")
        return 1

    try:
        import psycopg2  # type: ignore[import-untyped]
    except ImportError:
        print("Install psycopg2-binary to run EXPLAIN ANALYZE locally.")
        return 1

    with psycopg2.connect(connection_uri) as conn:
        with conn.cursor() as cur:
            cur.execute(EXPLAIN_SQL, (key,))
            for row in cur.fetchall():
                print(row[0])

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
