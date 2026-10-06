"""Deterministic stand-in for the model, used only with `--mock`, so the
rest of the pipeline (catalog matching, pricing, dedup, persistence,
dashboard) can be exercised and demoed without API cost or network
access. Never used for the actual submission checks -- those require a
real cached response. It intentionally does the *minimum* a model would
do (segment the text into excerpts); it does not pick SKUs or prices.
"""
from __future__ import annotations

import json
import re

from orderintake.domain.number_words import extract_stated_quantity

_REQUEST_TEXT_RE = re.compile(r'REQUEST TEXT:\s*"""(.*)"""', re.DOTALL)
_KEYWORD_RE = re.compile(r"\bone\b|\btwo\b|\bthree\b|\bfour\b|box|cable|hub|mice|adapter", re.IGNORECASE)
_BOX_RE = re.compile(r"\bbox(es)?\b", re.IGNORECASE)


def mock_respond(user_message: str) -> str:
    m = _REQUEST_TEXT_RE.search(user_message)
    text = m.group(1) if m else user_message

    lines = []
    clauses = re.split(r"\b and \b|[\r\n]+", text, flags=re.IGNORECASE)
    for clause in clauses:
        clause = clause.strip()
        if not clause or clause.lower().startswith("subject:"):
            continue
        if not _KEYWORD_RE.search(clause):
            continue

        quantity_value = extract_stated_quantity(clause)
        ambiguous = bool(_BOX_RE.search(clause)) and quantity_value is None

        lines.append({
            "raw_excerpt": clause,
            "quantity_value": quantity_value,
            "quantity_ambiguous": ambiguous,
            "quantity_notes": 'unit is "box", no individual item count stated' if ambiguous else None,
        })

    if not lines:
        lines.append({
            "raw_excerpt": text.strip(),
            "quantity_value": None,
            "quantity_ambiguous": True,
            "quantity_notes": "mock extractor found no recognizable product/quantity clause",
        })

    return json.dumps({"lines": lines})
