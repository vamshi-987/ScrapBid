import pytest

from accounts.phone import mask_phone, normalize_phone


@pytest.mark.parametrize(
    "raw",
    [
        "9876543210",
        "+919876543210",
        "919876543210",
        "09876543210",
        "+91 98765 43210",
        "98765-43210",
    ],
)
def test_valid_indian_mobiles_are_normalized(raw):
    assert normalize_phone(raw) == "+919876543210"


@pytest.mark.parametrize(
    "raw",
    ["", "12345", "5876543210", "+19876543210", "98765432100", "abcdefghij", None, 9876543210],
)
def test_invalid_numbers_are_rejected(raw):
    assert normalize_phone(raw) is None


def test_mask_hides_all_but_last_four_digits():
    assert mask_phone("+919876543210") == "+91XXXXXX3210"
