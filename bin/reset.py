#!/usr/bin/env python3
"""Dev convenience only: wipes local storage so `process.py` can be
re-run from a clean slate. Does NOT touch tasks/, data/, or checks/."""
from __future__ import annotations

import glob
import os
import sys

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_ROOT)

from orderintake import env  # noqa: E402
from orderintake.storage import SCHEMA_STATEMENTS, Storage  # noqa: E402


def main() -> None:
    env.load(os.path.join(APP_ROOT, ".env"))
    storage = Storage(
        host=env.get("DB_HOST", "127.0.0.1"),
        port=int(env.get("DB_PORT", "3306")),
        user=env.get("DB_USER", "root"),
        password=env.get("DB_PASSWORD", ""),
        database=env.get("DB_NAME", "order_intake"),
    )
    table_names = [
        statement.split("CREATE TABLE IF NOT EXISTS ", 1)[1].split(" ", 1)[0]
        for statement in SCHEMA_STATEMENTS
    ]
    with storage._conn.cursor() as cursor:
        for name in table_names:
            cursor.execute(f"TRUNCATE TABLE {name}")
    storage._conn.commit()
    print(f"Truncated tables: {', '.join(table_names)}")

    keep_cache = "--keep-responses" in sys.argv
    if not keep_cache:
        responses = glob.glob(os.path.join(APP_ROOT, "storage", "responses", "*.json"))
        for f in responses:
            os.remove(f)
        print(f"Removed {len(responses)} cached model responses")
    else:
        print("Kept cached model responses (pass without --keep-responses to clear them too)")


if __name__ == "__main__":
    main()
