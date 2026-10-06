"""Builds export rows for reviewed/ready draft orders. Pure function, no
I/O -- shared by bin/export.py (file output) and app.py's /export route
(HTTP download) so the two never drift.
"""
from __future__ import annotations


def build_export_rows(orders: list[dict], corrections_by_ref: dict[str, int], reviewed_only: bool = False) -> list[dict]:
    """One row per `draft`-status order. `reviewed` is true once at least
    one correction exists for that order_ref (the same "reviewer-confirmed"
    vs "auto-draft, unreviewed" distinction shown on the dashboard).
    `reviewed_only` filters to just the reviewer-confirmed orders.
    """
    rows = []
    for order in orders:
        if order["status"] != "draft":
            continue

        reviewed = corrections_by_ref.get(order["order_ref"], 0) > 0
        if reviewed_only and not reviewed:
            continue

        lines_summary = "; ".join(
            f'{line["sku"]} x{line["quantity"]}'
            for line in order["lines"]
            if line.get("sku") and line.get("quantity") is not None
        )

        rows.append({
            "order_ref": order["order_ref"],
            "source_request_id": order["source_request_id"],
            "status": order["status"],
            "reviewed": "yes" if reviewed else "no",
            "lines": lines_summary,
            "total_cents": order["total_cents"],
            "total_usd": f'{order["total_cents"] / 100:.2f}' if order["total_cents"] is not None else "",
            "created_at": order["created_at"],
            "updated_at": order["updated_at"],
        })
    return rows
