"""Orchestrates one request: dedupe by order_ref -> extract (model) ->
catalog match + quantity validation (code) -> price (code) -> persist.
"""
from __future__ import annotations

from orderintake.ai.extractor import Extractor
from orderintake.domain.catalog import Catalog
from orderintake.domain.number_words import extract_stated_quantity
from orderintake.domain.pricing import line_total
from orderintake.storage import Storage


class OrderProcessor:
    def __init__(self, catalog: Catalog, extractor: Extractor, storage: Storage) -> None:
        self._catalog = catalog
        self._extractor = extractor
        self._storage = storage

    def process(self, request_id: str, order_ref: str, raw_text: str) -> dict:
        # Only skip a request that already produced a real outcome (an
        # order or a recorded duplicate). A request that merely has a row
        # in `requests` because a PREVIOUS attempt errored (e.g. missing
        # API key) must be retried, not silently treated as done.
        if self._storage.has_outcome(request_id):
            return {"request_id": request_id, "order_ref": order_ref, "status": "already_processed"}

        if not self._storage.request_exists(request_id):
            self._storage.save_request(request_id, order_ref, raw_text, None)

        # Rule 4: same order_ref => same order. Checked BEFORE calling the
        # model, so a duplicate never costs a model call.
        if self._storage.order_ref_exists(order_ref):
            existing = self._storage.get_order(order_ref)
            self._storage.record_duplicate(request_id, order_ref, existing["source_request_id"])
            return {
                "request_id": request_id,
                "order_ref": order_ref,
                "status": "duplicate",
                "same_order_as": existing["source_request_id"],
            }

        try:
            extraction = self._extractor.extract(request_id, raw_text)
        except Exception as e:  # noqa: BLE001 - deliberately broad: any model/network failure must not crash the batch
            self._storage.record_error(request_id, f"model call failed: {e}")
            return {"request_id": request_id, "order_ref": order_ref, "status": "failed", "reason": str(e)}

        if extraction["parse_error"] is not None or not extraction["lines"]:
            reason = extraction["parse_error"] or "model returned no product lines for this request"
            self._storage.save_order(
                order_ref, request_id, "needs_clarification", [], None, [reason],
                "We could not automatically read this request. Could you resend the product(s) and quantity needed?",
                extraction["mode"], extraction["model"],
            )
            return {"request_id": request_id, "order_ref": order_ref, "status": "needs_clarification", "reason": reason}

        lines, reasons, all_resolved, total = self._resolve_lines(extraction["lines"])

        status = "draft" if all_resolved else "needs_clarification"
        clarification_draft = None if all_resolved else self._build_clarification_draft(reasons)

        self._storage.save_order(
            order_ref, request_id, status, lines, total if all_resolved else None,
            reasons, clarification_draft, extraction["mode"], extraction["model"],
        )

        return {
            "request_id": request_id,
            "order_ref": order_ref,
            "status": status,
            "total_cents": total if all_resolved else None,
        }

    def _resolve_lines(self, extracted_lines: list[dict]) -> tuple[list[dict], list[str], bool, int]:
        lines: list[dict] = []
        reasons: list[str] = []
        all_resolved = True
        total = 0

        #[{'raw_excerpt': '12 CAB-2 cables', 'quantity_value': 12, 'quantity_ambiguous': False, 'quantity_notes': None}]
        #print("12345", extracted_lines)

        for i, line in enumerate(extracted_lines):
            match = self._catalog.match(line["raw_excerpt"])
            quantity = self._resolve_quantity(line)

            line_result = {
                "raw_excerpt": line["raw_excerpt"],
                "product_match_status": match.status,
                "sku": match.sku,
                "candidates": match.candidates,
                "quantity": quantity["quantity"],
                "quantity_status": quantity["status"],
                "quantity_reason": quantity["reason"],
                "unit_cents": None,
                "subtotal_cents": None,
                "discount_cents": None,
                "line_total_cents": None,
            }

            line_ok = match.status == "matched" and quantity["status"] == "resolved"

            if not line_ok:
                all_resolved = False
                if match.status != "matched":
                    label = "unknown product" if match.status == "unknown" else "ambiguous product"
                    reason_suffix = f" - {match.reason}" if match.reason else ""
                    reasons.append(f'Line {i + 1}: {label} ("{line["raw_excerpt"]}"){reason_suffix}')
                if quantity["status"] != "resolved":
                    reasons.append(f'Line {i + 1}: ambiguous quantity ("{line["raw_excerpt"]}") - {quantity["reason"]}')
            else:
                entry = self._catalog.find(match.sku)
                pricing = line_total(entry.unit_cents, quantity["quantity"])
                line_result["unit_cents"] = entry.unit_cents
                line_result["subtotal_cents"] = pricing.subtotal_cents
                line_result["discount_cents"] = pricing.discount_cents
                line_result["line_total_cents"] = pricing.total_cents
                total += pricing.total_cents

            lines.append(line_result)

        return lines, reasons, all_resolved, total

    def _resolve_quantity(self, line: dict) -> dict:
        """Cross-checks the model's quantity_value against an independent
        code-only scan of the raw excerpt (number_words). Disagreement or
        absence of a stated count both fall back to "ambiguous" rather
        than guessing -- per the brief's "visible statuses instead of
        invented values"."""
        model_qty = line["quantity_value"]
        model_ambiguous = line["quantity_ambiguous"]
        code_qty = extract_stated_quantity(line["raw_excerpt"])

        if model_ambiguous:
            return {"status": "ambiguous", "quantity": None, "reason": line.get("quantity_notes") or "model flagged the quantity as ambiguous"}
        if model_qty is None:
            return {"status": "ambiguous", "quantity": None, "reason": "no explicit individual-item quantity found"}
        if model_qty < 1:
            return {"status": "ambiguous", "quantity": None, "reason": "extracted quantity is not a positive integer"}
        if code_qty is not None and code_qty != model_qty:
            return {
                "status": "ambiguous",
                "quantity": None,
                "reason": f"model read quantity {model_qty} but an independent text scan found {code_qty} -- needs human confirmation",
            }

        return {"status": "resolved", "quantity": model_qty, "reason": None}

    def _build_clarification_draft(self, reasons: list[str]) -> str:
        bullets = "\n".join(f"- {r}" for r in reasons)
        return (
            "We could not fully process this order automatically:\n"
            f"{bullets}\n\nCould you confirm the exact product(s) and quantity needed?"
        )

    def apply_correction(self, order_ref: str, line_index: int, field: str, new_value: str) -> dict:
        """Reviewer correction: a human overrides one field on one line,
        the line (and the whole order) is revalidated/repriced in code,
        and the change is recorded in corrections history."""
        order = self._storage.get_order(order_ref)
        if order is None:
            raise RuntimeError(f"no such order {order_ref}")
        lines = order["lines"]
        if line_index < 0 or line_index >= len(lines):
            raise RuntimeError(f"order {order_ref} has no line {line_index}")

        old_value = str(lines[line_index].get(field, ""))

        if field == "quantity":
            qty = int(new_value)
            if qty < 1:
                raise RuntimeError("corrected quantity must be a positive integer")
            lines[line_index]["quantity"] = qty
            lines[line_index]["quantity_status"] = "resolved"
            lines[line_index]["quantity_reason"] = None
        elif field == "sku":
            entry = self._catalog.find(new_value)
            if entry is None:
                raise RuntimeError(f"'{new_value}' is not a catalog SKU")
            lines[line_index]["sku"] = new_value
            lines[line_index]["product_match_status"] = "matched"
            lines[line_index]["candidates"] = [new_value]
        else:
            raise RuntimeError(f"unsupported correction field '{field}'")

        resolved = (
            lines[line_index]["product_match_status"] == "matched"
            and lines[line_index]["quantity_status"] == "resolved"
            and lines[line_index]["quantity"] is not None
        )

        if resolved:
            entry = self._catalog.find(lines[line_index]["sku"])
            pricing = line_total(entry.unit_cents, lines[line_index]["quantity"])
            lines[line_index]["unit_cents"] = entry.unit_cents
            lines[line_index]["subtotal_cents"] = pricing.subtotal_cents
            lines[line_index]["discount_cents"] = pricing.discount_cents
            lines[line_index]["line_total_cents"] = pricing.total_cents

        all_resolved = True
        total = 0
        for l in lines:
            if l.get("product_match_status") != "matched" or l.get("quantity_status") != "resolved":
                all_resolved = False
            else:
                total += l["line_total_cents"]

        status = "draft" if all_resolved else "needs_clarification"
        total_cents = total if all_resolved else None

        self._storage.update_order_lines(order_ref, lines, status, total_cents)
        self._storage.record_correction(order_ref, line_index, field, old_value, new_value, {
            "status": status,
            "total_cents": total_cents,
            "previous_status": order["status"],
            "previous_total_cents": order["total_cents"],
        })

        return self._storage.get_order(order_ref)
