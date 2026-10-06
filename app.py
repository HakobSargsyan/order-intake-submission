#!/usr/bin/env python3
"""Order intake review dashboard. Run with: python app.py"""
from __future__ import annotations

import csv
import io
import json
import os

from flask import Flask, Response, redirect, render_template, request, url_for

from orderintake import env
from orderintake.ai.claude_client import ClaudeClient
from orderintake.ai.extractor import Extractor
from orderintake.domain.catalog import Catalog
from orderintake.export import build_export_rows
from orderintake.order_processor import OrderProcessor
from orderintake.storage import Storage

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
env.load(os.path.join(APP_ROOT, ".env"))

app = Flask(__name__)

catalog = Catalog(os.path.join(APP_ROOT, "data", "catalog.json"))
# No new model calls happen from the dashboard itself -- it only reads
# already-persisted orders and re-runs LOCAL validation/pricing on a
# correction, so an empty client (no key, no mock) is intentional here.
client = ClaudeClient("", "n/a", os.path.join(APP_ROOT, "storage", "responses"))
extractor = Extractor(client)


def get_storage() -> Storage:
    # A fresh MySQL connection per request, NOT one shared global
    # connection: Flask's dev server (and any real WSGI server) can
    # dispatch requests on different threads, and DB-API connections are
    # not safe to share across threads by default.
    return Storage(
        host=env.get("DB_HOST", "127.0.0.1"),
        port=int(env.get("DB_PORT", "3306")),
        user=env.get("DB_USER", "root"),
        password=env.get("DB_PASSWORD", ""),
        database=env.get("DB_NAME", "order_intake"),
    )


def money(cents: int | None) -> str:
    return "-" if cents is None else f"{cents / 100:,.2f} USD"


app.jinja_env.filters["money"] = money
app.jinja_env.filters["from_json"] = json.loads


@app.route("/")
def index():
    storage = get_storage()
    status_filter = request.args.get("status", "all")
    orders = storage.list_orders(status_filter)
    stats = storage.stats()

    corrections_count = {o["order_ref"]: len(storage.get_corrections(o["order_ref"])) for o in orders}

    return render_template(
        "index.html",
        orders=orders,
        stats=stats,
        filter=status_filter,
        corrections_count=corrections_count,
    )


@app.route("/export")
def export():
    """Optional enhancement: download reviewed/ready draft orders as CSV
    (default) or JSON. ?reviewed_only=1 limits to reviewer-confirmed
    orders only. Row-building logic is shared with bin/export.py via
    orderintake/export.py so the CLI and the dashboard never drift."""
    storage = get_storage()
    as_json = request.args.get("format") == "json"
    reviewed_only = request.args.get("reviewed_only") == "1"

    orders = storage.list_orders("draft")
    corrections_count = {o["order_ref"]: len(storage.get_corrections(o["order_ref"])) for o in orders}
    rows = build_export_rows(orders, corrections_count, reviewed_only=reviewed_only)

    if as_json:
        return Response(
            json.dumps(rows, indent=2),
            mimetype="application/json",
            headers={"Content-Disposition": "attachment; filename=orders_export.json"},
        )

    fieldnames = list(rows[0].keys()) if rows else [
        "order_ref", "source_request_id", "status", "reviewed",
        "lines", "total_cents", "total_usd", "created_at", "updated_at",
    ]
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)

    return Response(
        buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=orders_export.csv"},
    )


@app.route("/order/<order_ref>", methods=["GET", "POST"])
def order_detail(order_ref: str):
    error = None
    storage = get_storage()
    processor = OrderProcessor(catalog, extractor, storage)

    if request.method == "POST" and request.form.get("action") == "correct":
        try:
            processor.apply_correction(
                order_ref,
                int(request.form["line_index"]),
                request.form["field"],
                request.form["new_value"],
            )
        except Exception as e:  # noqa: BLE001 - surfaced to the reviewer, not swallowed
            error = str(e)
        target = url_for("order_detail", order_ref=order_ref, error=error) if error else url_for("order_detail", order_ref=order_ref)
        return redirect(target)

    error = error or request.args.get("error")

    order = storage.get_order(order_ref)
    source_request = storage.get_request(order["source_request_id"]) if order else None
    corrections = storage.get_corrections(order_ref) if order else []

    return render_template(
        "order.html",
        order_ref=order_ref,
        order=order,
        request_row=source_request,
        corrections=corrections,
        error=error,
    )


if __name__ == "__main__":
    app.run(host="localhost", port=8787, debug=False)
