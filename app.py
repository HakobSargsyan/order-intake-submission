#!/usr/bin/env python3
"""Order intake review dashboard. Run with: python app.py"""
from __future__ import annotations

import json
import os

from flask import Flask, redirect, render_template, request, url_for

from orderintake import env
from orderintake.catalog import Catalog
from orderintake.claude_client import ClaudeClient
from orderintake.extractor import Extractor
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
    # A fresh sqlite3 connection per request, NOT one shared global
    # connection: Flask's dev server (and any real WSGI server) can
    # dispatch requests on different threads, and sqlite3 connections are
    # not safe to share across threads by default.
    return Storage(os.path.join(APP_ROOT, "storage", "app.sqlite"))


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
