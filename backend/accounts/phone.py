import re

# Indian mobile numbers: 10 digits starting with 6-9, optionally prefixed by +91, 91 or 0.
_MOBILE = re.compile(r"(?:\+?91|0)?([6-9]\d{9})")
_SEPARATORS = re.compile(r"[\s\-()]")


def normalize_phone(raw):
    """Return the number as +91XXXXXXXXXX, or None if it isn't a valid Indian mobile."""
    if not isinstance(raw, str):
        return None
    match = _MOBILE.fullmatch(_SEPARATORS.sub("", raw))
    return f"+91{match.group(1)}" if match else None


def mask_phone(phone):
    """Hide all but the last 4 digits, e.g. +919876543210 -> +91XXXXXX3210."""
    if phone.startswith("deleted-"):
        return phone  # A deleted account's placeholder, not a number.
    return f"{phone[:3]}XXXXXX{phone[-4:]}"
