"""Independent, code-only quantity check used to cross-validate the
model's extracted quantity against the raw text (see
order_processor.resolve_quantity).
"""
from __future__ import annotations

import re

_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20,
}

# Negative lookbehind excludes a digit that's part of a SKU-like token
# (e.g. the "1" in "CAB-1") so a product code is never mistaken for a
# stated quantity.
_DIGIT_RE = re.compile(r"(?<![A-Za-z]-)\b(\d+)\b")


def extract_stated_quantity(text: str) -> int | None:
    """Scans raw text for a standalone digit quantity or a spelled-out
    number word (one..twenty). Returns None when no unambiguous quantity
    is found in the text at all (e.g. "a box of ..." has no item count).
    """
    m = _DIGIT_RE.search(text)
    if m:
        return int(m.group(1))

    lower = text.lower()
    for word, value in _WORDS.items():
        if re.search(r"\b" + word + r"\b", lower):
            return value

    return None
