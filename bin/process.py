#!/usr/bin/env python3
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


def main() -> None:
    env.load(os.path.join(APP_ROOT, ".env"))
    mock = "--mock" in sys.argv

    catalog = Catalog(os.path.join(APP_ROOT, "data", "catalog.json"))
    client = ClaudeClient(
        api_key=env.get("ANTHROPIC_API_KEY", "") or "",
        model=env.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001"),
        responses_dir=os.path.join(APP_ROOT, "storage", "responses"),
        mock=mock,
    )
    extractor = Extractor(client)
    storage = Storage(
        host=env.get("DB_HOST", "127.0.0.1"),
        port=int(env.get("DB_PORT", "3306")),
        user=env.get("DB_USER", "root"),
        password=env.get("DB_PASSWORD", ""),
        database=env.get("DB_NAME", "order_intake"),
    )
    processor = OrderProcessor(catalog, extractor, storage)

    with open(os.path.join(APP_ROOT, "data", "requests.json"), encoding="utf-8") as f:
        manifest = json.load(f)

    if mock:
        print("Running in --mock mode (no real model calls; see orderintake/mock_extraction.py)\n")
    else:
        print(f"Running with model: {env.get('CLAUDE_MODEL', 'claude-haiku-4-5-20251001')}\n")

    print(f"{'ID':<5} {'ORDER':<9} {'STATUS':<10} {'TOTAL':<10}")
    print("-" * 45)

    for entry in manifest:
        path = os.path.join(APP_ROOT, "data", "requests", entry["file"])
        if not os.path.isfile(path):
            print(f"Missing request file: {path}", file=sys.stderr)
            continue
        with open(path, encoding="utf-8") as f:
            raw_text = f.read()

        result = processor.process(entry["id"], entry["order_ref"], raw_text)

        total_cents = result.get("total_cents")
        total = f"{total_cents / 100:.2f} USD" if total_cents is not None else "-"

        print(f"{result['request_id']:<5} {result['order_ref']:<9} {result['status']:<10} {total:<10}")

    print(f"\nStats: {json.dumps(storage.stats(), indent=2)}")
    print("\nStart the dashboard with: python app.py")


if __name__ == "__main__":
    main()
