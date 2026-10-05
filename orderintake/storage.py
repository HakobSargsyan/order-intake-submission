"""SQLite-backed persistence. Everything survives a restart (required by
the brief): requests, draft/clarification orders, duplicates, and the
full correction history per order.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    id TEXT PRIMARY KEY,
    order_ref TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    file_path TEXT,
    received_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    order_ref TEXT PRIMARY KEY,
    source_request_id TEXT NOT NULL,
    status TEXT NOT NULL,
    lines_json TEXT NOT NULL,
    total_cents INTEGER,
    reasons_json TEXT NOT NULL,
    clarification_draft TEXT,
    extraction_mode TEXT,
    extraction_model TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS duplicates (
    request_id TEXT PRIMARY KEY,
    order_ref TEXT NOT NULL,
    duplicate_of_request_id TEXT NOT NULL,
    detected_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_ref TEXT NOT NULL,
    line_index INTEGER NOT NULL,
    field TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    corrected_at TEXT NOT NULL,
    revalidation_json TEXT
);

CREATE TABLE IF NOT EXISTS processing_errors (
    request_id TEXT PRIMARY KEY,
    reason TEXT NOT NULL,
    occurred_at TEXT NOT NULL
);
"""


class Storage:
    def __init__(self, sqlite_path: str) -> None:
        os.makedirs(os.path.dirname(sqlite_path), exist_ok=True)
        self._conn = sqlite3.connect(sqlite_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def request_exists(self, request_id: str) -> bool:
        row = self._conn.execute("SELECT 1 FROM requests WHERE id = ?", (request_id,)).fetchone()
        return row is not None

    def save_request(self, request_id: str, order_ref: str, raw_text: str, file_path: str | None) -> None:
        self._conn.execute(
            "INSERT INTO requests (id, order_ref, raw_text, file_path, received_at) VALUES (?, ?, ?, ?, ?)",
            (request_id, order_ref, raw_text, file_path, _now()),
        )
        self._conn.commit()

    def order_ref_exists(self, order_ref: str) -> bool:
        row = self._conn.execute("SELECT 1 FROM orders WHERE order_ref = ?", (order_ref,)).fetchone()
        return row is not None

    def has_outcome(self, request_id: str) -> bool:
        """True only if this request already produced a real outcome (an
        order it sourced, or a recorded duplicate) -- NOT just a `requests`
        row from a prior failed attempt."""
        if self._conn.execute(
            "SELECT 1 FROM orders WHERE source_request_id = ?", (request_id,)
        ).fetchone():
            return True
        return self._conn.execute(
            "SELECT 1 FROM duplicates WHERE request_id = ?", (request_id,)
        ).fetchone() is not None

    def _hydrate_order(self, row: sqlite3.Row) -> dict:
        order = dict(row)
        order["lines"] = json.loads(order.pop("lines_json"))
        order["reasons"] = json.loads(order.pop("reasons_json"))
        return order

    def get_order(self, order_ref: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM orders WHERE order_ref = ?", (order_ref,)).fetchone()
        return self._hydrate_order(row) if row else None

    def save_order(
        self,
        order_ref: str,
        source_request_id: str,
        status: str,
        lines: list[dict],
        total_cents: int | None,
        reasons: list[str],
        clarification_draft: str | None,
        extraction_mode: str,
        extraction_model: str,
    ) -> None:
        now = _now()
        self._conn.execute(
            """INSERT INTO orders
               (order_ref, source_request_id, status, lines_json, total_cents, reasons_json,
                clarification_draft, extraction_mode, extraction_model, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                order_ref, source_request_id, status, json.dumps(lines), total_cents,
                json.dumps(reasons), clarification_draft, extraction_mode, extraction_model, now, now,
            ),
        )
        self._conn.commit()

    def update_order_lines(self, order_ref: str, lines: list[dict], status: str, total_cents: int | None) -> None:
        self._conn.execute(
            "UPDATE orders SET lines_json = ?, status = ?, total_cents = ?, updated_at = ? WHERE order_ref = ?",
            (json.dumps(lines), status, total_cents, _now(), order_ref),
        )
        self._conn.commit()

    def record_duplicate(self, request_id: str, order_ref: str, duplicate_of_request_id: str) -> None:
        self._conn.execute(
            "INSERT INTO duplicates (request_id, order_ref, duplicate_of_request_id, detected_at) VALUES (?, ?, ?, ?)",
            (request_id, order_ref, duplicate_of_request_id, _now()),
        )
        self._conn.commit()

    def record_error(self, request_id: str, reason: str) -> None:
        self._conn.execute(
            "INSERT INTO processing_errors (request_id, reason, occurred_at) VALUES (?, ?, ?)",
            (request_id, reason, _now()),
        )
        self._conn.commit()

    def record_correction(
        self, order_ref: str, line_index: int, field: str, old_value: str, new_value: str, revalidation: dict
    ) -> None:
        self._conn.execute(
            """INSERT INTO corrections
               (order_ref, line_index, field, old_value, new_value, corrected_at, revalidation_json)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (order_ref, line_index, field, old_value, new_value, _now(), json.dumps(revalidation)),
        )
        self._conn.commit()

    def get_corrections(self, order_ref: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM corrections WHERE order_ref = ? ORDER BY id ASC", (order_ref,)
        ).fetchall()
        return [dict(r) for r in rows]

    def list_orders(self, status_filter: str | None = None) -> list[dict]:
        if status_filter and status_filter != "all":
            rows = self._conn.execute(
                "SELECT * FROM orders WHERE status = ? ORDER BY created_at ASC", (status_filter,)
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM orders ORDER BY created_at ASC").fetchall()
        return [self._hydrate_order(r) for r in rows]

    def list_duplicates(self) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM duplicates ORDER BY detected_at ASC").fetchall()
        return [dict(r) for r in rows]

    def list_errors(self) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM processing_errors ORDER BY occurred_at ASC").fetchall()
        return [dict(r) for r in rows]

    def list_unresolved_errors(self) -> list[dict]:
        """Error rows whose request was later retried successfully (has an
        outcome now) are excluded -- only still-unresolved failures count."""
        return [e for e in self.list_errors() if not self.has_outcome(e["request_id"])]

    def get_request(self, request_id: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM requests WHERE id = ?", (request_id,)).fetchone()
        return dict(row) if row else None

    def stats(self) -> dict:
        orders = self.list_orders()
        by_status = {"draft": 0, "needs_clarification": 0}
        for order in orders:
            by_status[order["status"]] = by_status.get(order["status"], 0) + 1

        reason_counts: dict[str, int] = {}
        for order in orders:
            for reason in order["reasons"]:
                key = reason.split(" - ")[0]
                key = key.split("(")[0].strip()
                if key.startswith("Line "):
                    key = key.split(": ", 1)[1] if ": " in key else key
                reason_counts[key] = reason_counts.get(key, 0) + 1

        return {
            "by_status": by_status,
            "duplicates": len(self.list_duplicates()),
            "errors": len(self.list_unresolved_errors()),
            "exception_reasons": reason_counts,
        }
