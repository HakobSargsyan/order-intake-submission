"""MySQL-backed persistence. Everything survives a restart (required by
the brief): requests, draft/clarification orders, duplicates, and the
full correction history per order.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pymysql
import pymysql.cursors


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


SCHEMA_STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS requests (
        id VARCHAR(64) PRIMARY KEY,
        order_ref VARCHAR(64) NOT NULL,
        raw_text TEXT NOT NULL,
        file_path TEXT,
        received_at VARCHAR(64) NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS orders (
        order_ref VARCHAR(64) PRIMARY KEY,
        source_request_id VARCHAR(64) NOT NULL,
        status VARCHAR(32) NOT NULL,
        lines_json TEXT NOT NULL,
        total_cents INTEGER,
        reasons_json TEXT NOT NULL,
        clarification_draft TEXT,
        extraction_mode VARCHAR(32),
        extraction_model VARCHAR(128),
        created_at VARCHAR(64) NOT NULL,
        updated_at VARCHAR(64) NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS duplicates (
        request_id VARCHAR(64) PRIMARY KEY,
        order_ref VARCHAR(64) NOT NULL,
        duplicate_of_request_id VARCHAR(64) NOT NULL,
        detected_at VARCHAR(64) NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS corrections (
        id INTEGER PRIMARY KEY AUTO_INCREMENT,
        order_ref VARCHAR(64) NOT NULL,
        line_index INTEGER NOT NULL,
        field VARCHAR(32) NOT NULL,
        old_value TEXT,
        new_value TEXT,
        corrected_at VARCHAR(64) NOT NULL,
        revalidation_json TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS processing_errors (
        request_id VARCHAR(64) PRIMARY KEY,
        reason TEXT NOT NULL,
        occurred_at VARCHAR(64) NOT NULL
    )""",
]


class Storage:
    def __init__(self, host: str, port: int, user: str, password: str, database: str) -> None:
        self._conn = pymysql.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=False,
        )
        with self._conn.cursor() as cursor:
            for statement in SCHEMA_STATEMENTS:
                cursor.execute(statement)
        self._conn.commit()

    def request_exists(self, request_id: str) -> bool:
        with self._conn.cursor() as cursor:
            cursor.execute("SELECT 1 FROM requests WHERE id = %s", (request_id,))
            return cursor.fetchone() is not None

    def save_request(self, request_id: str, order_ref: str, raw_text: str, file_path: str | None) -> None:
        with self._conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO requests (id, order_ref, raw_text, file_path, received_at) VALUES (%s, %s, %s, %s, %s)",
                (request_id, order_ref, raw_text, file_path, _now()),
            )
        self._conn.commit()

    def order_ref_exists(self, order_ref: str) -> bool:
        with self._conn.cursor() as cursor:
            cursor.execute("SELECT 1 FROM orders WHERE order_ref = %s", (order_ref,))
            return cursor.fetchone() is not None

    def has_outcome(self, request_id: str) -> bool:
        """True only if this request already produced a real outcome (an
        order it sourced, or a recorded duplicate) -- NOT just a `requests`
        row from a prior failed attempt."""
        with self._conn.cursor() as cursor:
            cursor.execute("SELECT 1 FROM orders WHERE source_request_id = %s", (request_id,))
            if cursor.fetchone():
                return True
            cursor.execute("SELECT 1 FROM duplicates WHERE request_id = %s", (request_id,))
            return cursor.fetchone() is not None

    def _hydrate_order(self, row: dict) -> dict:
        order = dict(row)
        order["lines"] = json.loads(order.pop("lines_json"))
        order["reasons"] = json.loads(order.pop("reasons_json"))
        return order

    def get_order(self, order_ref: str) -> dict | None:
        with self._conn.cursor() as cursor:
            cursor.execute("SELECT * FROM orders WHERE order_ref = %s", (order_ref,))
            row = cursor.fetchone()
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
        with self._conn.cursor() as cursor:
            cursor.execute(
                """INSERT INTO orders
                   (order_ref, source_request_id, status, lines_json, total_cents, reasons_json,
                    clarification_draft, extraction_mode, extraction_model, created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    order_ref, source_request_id, status, json.dumps(lines), total_cents,
                    json.dumps(reasons), clarification_draft, extraction_mode, extraction_model, now, now,
                ),
            )
        self._conn.commit()

    def update_order_lines(self, order_ref: str, lines: list[dict], status: str, total_cents: int | None) -> None:
        with self._conn.cursor() as cursor:
            cursor.execute(
                "UPDATE orders SET lines_json = %s, status = %s, total_cents = %s, updated_at = %s WHERE order_ref = %s",
                (json.dumps(lines), status, total_cents, _now(), order_ref),
            )
        self._conn.commit()

    def record_duplicate(self, request_id: str, order_ref: str, duplicate_of_request_id: str) -> None:
        with self._conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO duplicates (request_id, order_ref, duplicate_of_request_id, detected_at) VALUES (%s, %s, %s, %s)",
                (request_id, order_ref, duplicate_of_request_id, _now()),
            )
        self._conn.commit()

    def record_error(self, request_id: str, reason: str) -> None:
        # request_id is the PRIMARY KEY, and a request that keeps failing is
        # by design retried on every run (see has_outcome()) -- so the same
        # request_id can fail more than once across runs. ON DUPLICATE KEY
        # UPDATE keeps the latest reason/timestamp instead of crashing on a
        # second failure for the same request.
        now = _now()
        with self._conn.cursor() as cursor:
            cursor.execute(
                """INSERT INTO processing_errors (request_id, reason, occurred_at) VALUES (%s, %s, %s)
                   ON DUPLICATE KEY UPDATE reason = %s, occurred_at = %s""",
                (request_id, reason, now, reason, now),
            )
        self._conn.commit()

    def record_correction(
        self, order_ref: str, line_index: int, field: str, old_value: str, new_value: str, revalidation: dict
    ) -> None:
        with self._conn.cursor() as cursor:
            cursor.execute(
                """INSERT INTO corrections
                   (order_ref, line_index, field, old_value, new_value, corrected_at, revalidation_json)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (order_ref, line_index, field, old_value, new_value, _now(), json.dumps(revalidation)),
            )
        self._conn.commit()

    def get_corrections(self, order_ref: str) -> list[dict]:
        with self._conn.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM corrections WHERE order_ref = %s ORDER BY id ASC", (order_ref,)
            )
            rows = cursor.fetchall()
        return [dict(r) for r in rows]

    def list_orders(self, status_filter: str | None = None) -> list[dict]:
        with self._conn.cursor() as cursor:
            if status_filter and status_filter != "all":
                cursor.execute(
                    "SELECT * FROM orders WHERE status = %s ORDER BY created_at ASC", (status_filter,)
                )
            else:
                cursor.execute("SELECT * FROM orders ORDER BY created_at ASC")
            rows = cursor.fetchall()
        return [self._hydrate_order(r) for r in rows]

    def list_duplicates(self) -> list[dict]:
        with self._conn.cursor() as cursor:
            cursor.execute("SELECT * FROM duplicates ORDER BY detected_at ASC")
            rows = cursor.fetchall()
        return [dict(r) for r in rows]

    def list_errors(self) -> list[dict]:
        with self._conn.cursor() as cursor:
            cursor.execute("SELECT * FROM processing_errors ORDER BY occurred_at ASC")
            rows = cursor.fetchall()
        return [dict(r) for r in rows]

    def list_unresolved_errors(self) -> list[dict]:
        """Error rows whose request was later retried successfully (has an
        outcome now) are excluded -- only still-unresolved failures count."""
        return [e for e in self.list_errors() if not self.has_outcome(e["request_id"])]

    def get_request(self, request_id: str) -> dict | None:
        with self._conn.cursor() as cursor:
            cursor.execute("SELECT * FROM requests WHERE id = %s", (request_id,))
            row = cursor.fetchone()
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
