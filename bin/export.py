#!/usr/bin/env python3
"""Optional enhancement: export reviewed/ready draft orders to a simple
structured file. CSV by default; --json for JSON. --reviewed-only limits
the export to orders with at least one reviewer correction (see
orderintake/export.py for the row-building logic shared with app.py's
/export route)."""
from __future__ import annotations

import csv
import json
import os
import sys

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_ROOT)

from orderintake import env  # noqa: E402
from orderintake.export import build_export_rows  # noqa: E402
from orderintake.storage import Storage  # noqa: E402


def main() -> None:
    env.load(os.path.join(APP_ROOT, ".env"))
    storage = Storage(os.path.join(APP_ROOT, "storage", "app.sqlite"))

    as_json = "--json" in sys.argv
    reviewed_only = "--reviewed-only" in sys.argv

    orders = storage.list_orders("draft")
    corrections_by_ref = {o["order_ref"]: len(storage.get_corrections(o["order_ref"])) for o in orders}
    rows = build_export_rows(orders, corrections_by_ref, reviewed_only=reviewed_only)

    out_dir = os.path.join(APP_ROOT, "storage", "export")
    os.makedirs(out_dir, exist_ok=True)

    if as_json:
        out_path = os.path.join(out_dir, "orders.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=2)
    else:
        out_path = os.path.join(out_dir, "orders.csv")
        fieldnames = list(rows[0].keys()) if rows else [
            "order_ref", "source_request_id", "status", "reviewed",
            "lines", "total_cents", "total_usd", "created_at", "updated_at",
        ]
        with open(out_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    scope = "reviewer-confirmed" if reviewed_only else "draft"
    print(f"Exported {len(rows)} {scope} order(s) to {out_path}")


if __name__ == "__main__":
    main()
