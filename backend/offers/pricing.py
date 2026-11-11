"""Estimated amounts in integer paise. Exact: Decimal and integers only, never floats.

Each line is weight (kg, 3 decimals) × rate (paise per kg), or quantity × rate
(paise per item), rounded half-up to the nearest paisa. Lines are then summed.
"""

from decimal import ROUND_HALF_UP, Decimal


def line_paise(item, rate_paise):
    """Estimated paise for one listing item at a rate. Returns an int."""
    if item.declared_weight_kg is not None:
        amount = Decimal(item.declared_weight_kg) * Decimal(rate_paise)
        return int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return item.declared_quantity * rate_paise


def estimated_total_paise(items, rates_by_category):
    """Sum of line amounts. `rates_by_category` maps category id -> rate in paise."""
    return sum(line_paise(item, rates_by_category[item.category_id]) for item in items)
