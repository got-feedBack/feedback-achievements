"""One-shot migration tool: copy the wall from SQLite into managed Postgres.

Provided per the operability must-fix (a real migration path if managed Postgres
is chosen later). Reads the SQLite `unlocks` table and upserts every row into a
Postgres table of the same shape. No IP data exists to migrate — by design.

Usage:
    DATABASE_URL=postgres://... python migrate.py [--sqlite data/wall.db]

Requires `psycopg[binary]` when run (not a base dependency so the SQLite-only
deploy stays lean).
"""

import argparse
import os
import sqlite3
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sqlite", default=os.environ.get("SQLITE_PATH", "data/wall.db"))
    ap.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    args = ap.parse_args()
    if not args.database_url:
        sys.exit("DATABASE_URL is required (managed Postgres target).")

    try:
        import psycopg
    except ImportError:
        sys.exit("Install psycopg first:  pip install 'psycopg[binary]'")

    src = sqlite3.connect(args.sqlite)
    src.row_factory = sqlite3.Row
    rows = src.execute(
        "SELECT player_hash, achievement_id, display_name, unlocked_at FROM unlocks").fetchall()
    src.close()

    with psycopg.connect(args.database_url) as pg, pg.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS unlocks (
                player_hash    TEXT NOT NULL,
                achievement_id TEXT NOT NULL,
                display_name   TEXT NOT NULL,
                unlocked_at    TEXT,
                PRIMARY KEY (player_hash, achievement_id)
            )
            """
        )
        for r in rows:
            cur.execute(
                """
                INSERT INTO unlocks(player_hash, achievement_id, display_name, unlocked_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (player_hash, achievement_id) DO UPDATE SET
                    display_name = EXCLUDED.display_name,
                    unlocked_at = COALESCE(unlocks.unlocked_at, EXCLUDED.unlocked_at)
                """,
                (r["player_hash"], r["achievement_id"], r["display_name"], r["unlocked_at"]),
            )
        pg.commit()
    print(f"migrated {len(rows)} unlock rows to Postgres")


if __name__ == "__main__":
    main()
