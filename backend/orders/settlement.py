"""The pickup money rules. Pure: no database, no clock; exact Decimal and integer maths.

For each category on the order the dealer has measured a verified amount (weight in kg,
or a count for per-item goods). The rate is always the accepted offer's rate.

    Within tolerance (|verified − declared| ≤ tolerance): counts as equal, no choice;
        the dealer pays verified × rate and can't deny.
    More than declared (beyond tolerance): the dealer takes the excess (pays verified)
        or declines it (pays declared; the seller keeps the rest).
    Less than declared (beyond tolerance): the dealer accepts (pays verified) or denies,
        which cancels the whole order as WEIGHT_SHORT with nothing paid.

Weights use the tolerance as a percentage of the declared weight. Counts are exact.
The dealer never pays for more than the verified amount. Each line is rounded half up
to the paisa; the total is the sum of the rounded lines.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

TAKE_EXCESS = "take_excess"
DECLINE_EXCESS = "decline_excess"
ACCEPT_SHORT = "accept_short"
DENY = "deny"

EQUAL = "equal"
OVER = "over"
SHORT = "short"

CHOICES_FOR = {EQUAL: {None}, OVER: {TAKE_EXCESS, DECLINE_EXCESS}, SHORT: {ACCEPT_SHORT, DENY}}

WEIGHT_SHORT = "WEIGHT_SHORT"


class SettlementError(ValueError):
    """The verified amounts or choices don't fit the rules."""

    def __init__(self, code, category=None):
        super().__init__(code)
        self.code = code
        self.category = category


@dataclass(frozen=True)
class LineInput:
    category: int
    per_kg: bool
    declared: Decimal  # kg (3 decimals) or a count
    verified: Decimal
    rate_paise: int
    choice: str | None = None


@dataclass(frozen=True)
class LineResult:
    category: int
    per_kg: bool
    declared: Decimal
    verified: Decimal
    taken: Decimal
    comparison: str  # equal | over | short
    choice: str | None
    amount_paise: int


@dataclass(frozen=True)
class Settlement:
    denied: bool
    reason: str  # WEIGHT_SHORT when denied, otherwise ""
    lines: tuple[LineResult, ...]
    total_paise: int


def _compare(line, tolerance_percent):
    difference = line.verified - line.declared
    allowed = (
        line.declared * Decimal(tolerance_percent) / Decimal(100) if line.per_kg else Decimal(0)
    )
    if abs(difference) <= allowed:
        return EQUAL
    return OVER if difference > 0 else SHORT


def _amount(quantity, rate_paise):
    return int((quantity * Decimal(rate_paise)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def settle(lines, tolerance_percent):
    """Apply the rules to every line. Returns a Settlement, or raises SettlementError."""
    results = []
    denied = False
    for line in lines:
        if line.declared <= 0 or line.verified < 0 or line.rate_paise <= 0:
            raise SettlementError("invalid_amount", line.category)
        if not line.per_kg and (line.declared % 1 or line.verified % 1):
            raise SettlementError("invalid_amount", line.category)
        comparison = _compare(line, tolerance_percent)
        if line.choice not in CHOICES_FOR[comparison]:
            code = "choice_required" if line.choice is None else "invalid_choice"
            raise SettlementError(code, line.category)
        if line.choice == DENY:
            denied = True
        taken = line.declared if line.choice == DECLINE_EXCESS else line.verified
        if taken > line.verified:  # never pay for more than was verified
            raise SettlementError("over_verified", line.category)
        results.append(
            LineResult(
                category=line.category,
                per_kg=line.per_kg,
                declared=line.declared,
                verified=line.verified,
                taken=taken,
                comparison=comparison,
                choice=line.choice,
                amount_paise=_amount(taken, line.rate_paise),
            )
        )
    if denied:
        return Settlement(denied=True, reason=WEIGHT_SHORT, lines=tuple(results), total_paise=0)
    return Settlement(
        denied=False,
        reason="",
        lines=tuple(results),
        total_paise=sum(result.amount_paise for result in results),
    )
