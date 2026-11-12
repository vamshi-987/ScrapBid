from decimal import Decimal

import pytest

from orders.settlement import (
    ACCEPT_SHORT,
    DECLINE_EXCESS,
    DENY,
    TAKE_EXCESS,
    LineInput,
    SettlementError,
    settle,
)

TOLERANCE = 5  # percent
RATE = 1400  # ₹14 per kg


def kg(declared, verified, choice=None, rate=RATE, category=1):
    return LineInput(category, True, Decimal(declared), Decimal(verified), rate, choice)


def items(declared, verified, choice=None, rate=150000, category=2):
    return LineInput(category, False, Decimal(declared), Decimal(verified), rate, choice)


def one(line):
    return settle([line], TOLERANCE)


# --- The table from the rules ----------------------------------------------


@pytest.mark.parametrize(
    ("line", "taken", "paise", "comparison"),
    [
        # Verified = declared.
        (kg("20", "20"), "20", 28000, "equal"),
        # Within 5% either way: equal, pays verified.
        (kg("20", "20.6"), "20.6", 28840, "equal"),
        (kg("20", "19.4"), "19.4", 27160, "equal"),
        # Exactly on the 5% limit still counts as equal.
        (kg("20", "21"), "21", 29400, "equal"),
        (kg("20", "19"), "19", 26600, "equal"),
        # More than 5% over: take the excess (verified) or decline it (declared).
        (kg("20", "21.001", TAKE_EXCESS), "21.001", 29401, "over"),
        (kg("20", "25", TAKE_EXCESS), "25", 35000, "over"),
        (kg("20", "25", DECLINE_EXCESS), "20", 28000, "over"),
        # More than 5% under: accept pays verified.
        (kg("20", "18.999", ACCEPT_SHORT), "18.999", 26599, "short"),
        (kg("20", "15", ACCEPT_SHORT), "15", 21000, "short"),
    ],
)
def test_each_case(line, taken, paise, comparison):
    result = one(line)

    assert result.denied is False
    assert result.lines[0].taken == Decimal(taken)
    assert result.lines[0].comparison == comparison
    assert result.lines[0].amount_paise == paise
    assert result.total_paise == paise
    assert isinstance(result.total_paise, int)


def test_short_and_denied_cancels_the_whole_order():
    result = settle([kg("20", "15", DENY), items("2", "2", category=2)], TOLERANCE)

    assert result.denied is True
    assert result.reason == "WEIGHT_SHORT"
    assert result.total_paise == 0


# --- Choices that aren't allowed -------------------------------------------


@pytest.mark.parametrize(
    ("line", "code"),
    [
        (kg("20", "19.5", DENY), "invalid_choice"),  # can't deny inside the tolerance
        (kg("20", "19", DENY), "invalid_choice"),  # exactly on the limit: still can't deny
        (kg("20", "20", TAKE_EXCESS), "invalid_choice"),  # no choice when equal
        (kg("20", "20.5", DECLINE_EXCESS), "invalid_choice"),  # within tolerance: no decline
        (kg("20", "25", DENY), "invalid_choice"),  # can't deny an excess
        (kg("20", "25", ACCEPT_SHORT), "invalid_choice"),
        (kg("20", "15", TAKE_EXCESS), "invalid_choice"),  # nothing to take
        (kg("20", "15", DECLINE_EXCESS), "invalid_choice"),
        (kg("20", "25"), "choice_required"),
        (kg("20", "15"), "choice_required"),
        (kg("20", "25", "pay_double"), "invalid_choice"),
    ],
)
def test_choices_must_fit_the_case(line, code):
    with pytest.raises(SettlementError) as error:
        one(line)

    assert error.value.code == code


@pytest.mark.parametrize(
    "line",
    [
        kg("0", "5"),
        kg("-1", "5"),
        kg("20", "-1"),
        kg("20", "20", rate=0),
        items("2.5", "2"),
        items("2", "1.5"),
    ],
)
def test_invalid_amounts_are_refused(line):
    with pytest.raises(SettlementError) as error:
        one(line)

    assert error.value.code == "invalid_amount"


# --- Counts (per-item goods) are exact -------------------------------------


@pytest.mark.parametrize(
    ("line", "taken", "paise"),
    [
        (items("2", "2"), "2", 300000),
        (items("2", "3", TAKE_EXCESS), "3", 450000),
        (items("2", "3", DECLINE_EXCESS), "2", 300000),
        (items("2", "1", ACCEPT_SHORT), "1", 150000),
    ],
)
def test_counts(line, taken, paise):
    result = one(line)

    assert result.lines[0].taken == Decimal(taken)
    assert result.total_paise == paise


def test_counts_have_no_tolerance():
    # 20 items vs 21 is 5%, but counts are exact: it's "over", so a choice is needed.
    with pytest.raises(SettlementError) as error:
        one(items("20", "21"))

    assert error.value.code == "choice_required"
    assert one(items("20", "19", DENY)).denied is True


# --- The money rules -------------------------------------------------------


def test_never_pays_for_more_than_was_verified():
    for line in [
        kg("20", "25", TAKE_EXCESS),
        kg("20", "25", DECLINE_EXCESS),
        kg("20", "15", ACCEPT_SHORT),
        kg("20", "20.9"),
        items("2", "3", DECLINE_EXCESS),
    ]:
        result = one(line).lines[0]
        assert result.taken <= result.verified


def test_the_rate_is_always_the_agreed_one():
    result = one(kg("10", "10", rate=1234))

    assert result.total_paise == 12340


@pytest.mark.parametrize(
    ("verified", "rate", "paise"),
    [
        ("0.001", 1500, 2),  # 1.5 → 2 (half up)
        ("0.001", 1400, 1),  # 1.4 → 1
        ("0.002", 1250, 3),  # 2.5 → 3
    ],
)
def test_lines_round_half_up_to_the_paisa(verified, rate, paise):
    # Declared = verified so the line is "equal" and pays the verified amount.
    assert one(kg(verified, verified, rate=rate)).total_paise == paise


def test_total_is_the_sum_of_rounded_lines():
    lines = [
        kg("0.001", "0.001", rate=1500, category=1),
        kg("0.001", "0.001", rate=1500, category=3),
    ]

    assert settle(lines, TOLERANCE).total_paise == 4  # 2 + 2, not round(3.0)


def test_mixed_and_bulk_order():
    lines = [
        kg("350.5", "352", rate=72000, category=1),  # within 5%: pays 352 kg
        kg("40", "44", TAKE_EXCESS, rate=1000, category=3),  # 10% over, taken
        items("2", "2", rate=150000, category=2),
    ]

    result = settle(lines, TOLERANCE)

    assert [line.amount_paise for line in result.lines] == [25344000, 44000, 300000]
    assert result.total_paise == 25688000  # ₹2,56,880


def test_tolerance_comes_from_the_caller():
    # 19.5 kg against 20 kg declared is 2.5% short.
    assert settle([kg("20", "19.5")], 5).denied is False  # within 5%: equal, no choice
    assert settle([kg("20", "19.5", DENY)], 1).denied is True  # beyond 1%: may deny
    with pytest.raises(SettlementError):
        settle([kg("20", "19.5", DENY)], 5)  # within 5%: can't deny


def test_result_records_everything_for_the_order():
    line = one(kg("20", "25", DECLINE_EXCESS)).lines[0]

    assert (line.declared, line.verified, line.taken, line.choice) == (
        Decimal("20"),
        Decimal("25"),
        Decimal("20"),
        DECLINE_EXCESS,
    )
