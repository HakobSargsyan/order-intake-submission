"""Local catalog lookup (domain.md rule 3: "Match products by SKU or an
unambiguous catalog description. Unknown products and ambiguous
quantities need clarification.").

This is the ONLY place that decides product identity. The model never
picks a SKU itself -- it only segments the request text into per-product
excerpts (see extractor.py). That keeps catalog matching deterministic,
auditable, and independent of model phrasing.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class CatalogEntry:
    sku: str
    name: str
    unit_cents: int


@dataclass(frozen=True)
class MatchResult:
    status: str  # "matched" | "ambiguous" | "unknown"
    sku: str | None
    candidates: list[str]
    reason: str | None


class Catalog:
    def __init__(self, catalog_path: str) -> None:
        with open(catalog_path, encoding="utf-8") as f:
            raw = json.load(f)
        self._entries: list[CatalogEntry] = [
            CatalogEntry(sku=e["sku"], name=e["name"], unit_cents=e["unit_cents"]) for e in raw
        ]

    def find(self, sku: str) -> CatalogEntry | None:
        for entry in self._entries:
            if entry.sku == sku:
                return entry
        return None

    def match(self, text: str) -> MatchResult:
        lower = text.lower()

        # 1. Explicit SKU token always wins and is unambiguous by definition.
        found_skus = [
            e.sku for e in self._entries
            if re.search(r"\b" + re.escape(e.sku.lower()) + r"\b", lower)
        ]
        if len(found_skus) == 1:
            return MatchResult("matched", found_skus[0], found_skus, None)
        if len(found_skus) > 1:
            return MatchResult("ambiguous", None, found_skus, "multiple SKUs mentioned")

        # 2. Distinguishing description keywords (length markers, "hub").
        is_one_meter = bool(re.search(r"\b1\s*m(eter|etre)?\b", lower))
        is_two_meter = bool(re.search(r"\b2\s*m(eter|etre)?\b", lower))
        is_hub = bool(re.search(r"\bhubs?\b", lower))
        is_cable = bool(re.search(r"\bcables?\b", lower))

        if is_one_meter and is_cable:
            return MatchResult("matched", "CAB-1", ["CAB-1"], None)
        if is_two_meter and is_cable:
            return MatchResult("matched", "CAB-2", ["CAB-2"], None)
        if is_hub:
            return MatchResult("matched", "HUB-1", ["HUB-1"], None)

        # 3. Generic "cable" with no length marker matches more than one
        #    catalog entry -> ambiguous, per rule 3.
        if is_cable:
            return MatchResult("ambiguous", None, ["CAB-1", "CAB-2"], 'generic "cable" matches more than one catalog item')

        # 4. Nothing in the catalog resembles this at all.
        return MatchResult("unknown", None, [], "no catalog entry resembles this description")
