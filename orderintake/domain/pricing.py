"""domain.md rule 2: catalog unit price; >=10 items on a line gets a 10%
line-only discount; round the discount to the nearest cent, halves up.
No other charges.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class LineTotal:
    subtotal_cents: int
    discount_cents: int
    total_cents: int


def round_half_up(cents: float) -> int:
    """Round-half-up to the nearest integer cent (NOT Python's default
    banker's rounding, which `round()` uses)."""
    return math.floor(cents + 0.5)


def line_total(unit_cents: int, quantity: int) -> LineTotal:
    if quantity < 1:
        raise ValueError("quantity must be a positive integer")

    subtotal = unit_cents * quantity
    discount = round_half_up(subtotal * 0.10) if quantity >= 10 else 0

    return LineTotal(subtotal_cents=subtotal, discount_cents=discount, total_cents=subtotal - discount)
