#!/usr/bin/env python3
"""Minimum-demonstration check runner. Compares the CURRENT persisted
state (after bin/process.py has run) against checks/reference-cases.json,
whose expected values were hand-calculated independently of this
application (see each case's "verified_by"). This script never treats
the app's own prior output as the answer key. Safe to run repeatedly --
every case asserts a FINAL state exactly once (see README for why that
matters)."""
from __future__ import annotations

import json
import os
import sys

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_ROOT)

from orderintake import env  # noqa: E402
from orderintake.ai.claude_client import ClaudeClient  # noqa: E402
from orderintake.ai.extractor import Extractor  # noqa: E402
from orderintake.domain.catalog import Catalog  # noqa: E402
from orderintake.order_processor import OrderProcessor  # noqa: E402
from orderintake.storage import Storage  # noqa: E402


def main() -> int:
    env.load(os.path.join(APP_ROOT, ".env"))
    catalog = Catalog(os.path.join(APP_ROOT, "data", "catalog.json"))
    client = ClaudeClient("", "n/a", os.path.join(APP_ROOT, "storage", "responses"))
    extractor = Extractor(client)
    storage = Storage(
        host=env.get("DB_HOST", "127.0.0.1"),
        port=int(env.get("DB_PORT", "3306")),
        user=env.get("DB_USER", "root"),
        password=env.get("DB_PASSWORD", ""),
        database=env.get("DB_NAME", "order_intake"),
    )
    processor = OrderProcessor(catalog, extractor, storage)

    with open(os.path.join(APP_ROOT, "checks", "reference-cases.json"), encoding="utf-8") as f:
        cases = json.load(f)

    passed = 0
    failed = 0
    rows = []

    for case in cases:
        if "correction" in case:
            order = storage.get_order(case["order_ref"])
            line_index = case["correction"]["line_index"]
            desired_qty = int(case["correction"]["new_value"])
            if order is None:
                rows.append((case["case"], "FAIL", "order not found - run bin/process.py first"))
                failed += 1
                continue
            if order["lines"][line_index].get("quantity") != desired_qty:
                order = processor.apply_correction(
                    case["order_ref"], line_index, case["correction"]["field"], case["correction"]["new_value"],
                )
        else:
            order = storage.get_order(case["order_ref"])
            if order is None and case["expected_status"] != "duplicate":
                rows.append((case["case"], "FAIL", "order not found - run bin/process.py first"))
                failed += 1
                continue

        mode = order.get("extraction_mode", "?") if order else "?"

        if case.get("expected_status") == "duplicate":
            duplicates = storage.list_duplicates()
            found = any(d["request_id"] == case["request_id"] for d in duplicates)
            ok = found and order is not None and order["source_request_id"] != case["request_id"]
            detail = (
                f"duplicate recorded, no second draft (source order mode={mode})" if ok
                else "duplicate NOT recorded correctly"
            )
            rows.append((case["case"], "PASS" if ok else "FAIL", detail))
            passed += ok
            failed += not ok
            continue

        status_ok = order["status"] == case["expected_status"]
        total_ok = order.get("total_cents") == case["expected_total_cents"]
        ok = status_ok and total_ok

        if ok:
            detail = f"status={order['status']} total={order['total_cents']!r} (mode={mode})"
        else:
            detail = (
                f"expected status={case['expected_status']} total={case['expected_total_cents']!r} "
                f"| got status={order['status']} total={order.get('total_cents')!r} (mode={mode})"
            )

        rows.append((case["case"], "PASS" if ok else "FAIL", detail))
        passed += ok
        failed += not ok

    print("Reference case results")
    print("=" * 90)
    for name, result, detail in rows:
        print(f"[{result}] {name}\n      {detail}")
    print("=" * 90)
    print(f"{passed} passed, {failed} failed")

    return 1 if failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
