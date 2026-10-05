"""The model's ONLY job: segment a free-text customer request into one or
more verbatim product excerpts, and make a best-effort read of the
stated quantity per excerpt. It must NOT pick a SKU, price anything, or
decide what is or isn't in the catalog -- that is Catalog + Pricing +
OrderProcessor, entirely in code, so it is auditable and independent of
model phrasing.
"""
from __future__ import annotations

import json
import re

from orderintake.claude_client import ClaudeClient

SYSTEM_PROMPT = """\
You segment a short customer order email into one entry per distinct
product request. For each entry return the VERBATIM excerpt of the
text describing that product (do not paraphrase, do not normalize
wording, copy the exact words used), plus your best reading of the
requested quantity of individual items.

Rules:
- "quantity_value": the number of individual items as an integer, only
  if the text states a specific count (digits or number words). Do not
  guess how many items are in a "box", "pack", or similar container.
- "quantity_ambiguous": true if the text does not state a specific
  individual-item count (e.g. "a box of X", "some Y").
- Do not invent products, SKUs, or prices. Do not resolve the product
  against any catalog -- just copy what the customer wrote.

Respond with ONLY a JSON object of this exact shape, no prose, no code
fences:
{"lines": [{"raw_excerpt": string, "quantity_value": number|null, "quantity_ambiguous": boolean, "quantity_notes": string|null}]}
"""

_CODE_FENCE_RE = re.compile(r"^```(json)?|```$", re.MULTILINE)


class Extractor:
    def __init__(self, client: ClaudeClient) -> None:
        self._client = client

    def extract(self, request_id: str, raw_text: str) -> dict:
        user_message = f'REQUEST TEXT:\n"""{raw_text}"""'

        response = self._client.complete(request_id, SYSTEM_PROMPT, user_message)

        text = (response.get("text") or "").strip()
        text = _CODE_FENCE_RE.sub("", text).strip()

        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            decoded = None

        if not isinstance(decoded, dict) or not isinstance(decoded.get("lines"), list):
            return {
                "mode": response["mode"],
                "model": response["model"],
                "lines": [],
                "parse_error": f"model response was not valid JSON in the expected shape: {text[:300]}",
            }

        lines = []
        for line in decoded["lines"]:
            qv = line.get("quantity_value")
            lines.append({
                "raw_excerpt": str(line.get("raw_excerpt", "")),
                "quantity_value": int(qv) if qv is not None else None,
                "quantity_ambiguous": bool(line.get("quantity_ambiguous", False)),
                "quantity_notes": line.get("quantity_notes"),
            })

        return {
            "mode": response["mode"],
            "model": response["model"],
            "lines": lines,
            "parse_error": None,
        }
