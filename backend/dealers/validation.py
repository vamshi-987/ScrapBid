"""Input checks for dealer onboarding."""

import re

from dealers.models import IdType

_ID_PATTERNS = {
    IdType.AADHAAR: re.compile(r"[2-9]\d{11}"),
    IdType.PAN: re.compile(r"[A-Z]{5}\d{4}[A-Z]"),
    IdType.VOTER_ID: re.compile(r"[A-Z]{3}\d{7}"),
    IdType.DRIVING_LICENCE: re.compile(r"[A-Z]{2}\d{2}[A-Z0-9]{6,16}"),
}

PINCODE = re.compile(r"[1-9]\d{5}")

# Rough bounding box for India, to catch swapped or missing coordinates.
INDIA_LATITUDE = (6.0, 38.0)
INDIA_LONGITUDE = (68.0, 98.0)


def normalize_id_number(id_type, raw):
    """Return the ID number without spaces or dashes, upper-cased, or None if invalid."""
    if not isinstance(raw, str) or id_type not in _ID_PATTERNS:
        return None
    value = re.sub(r"[\s\-]", "", raw).upper()
    return value if _ID_PATTERNS[id_type].fullmatch(value) else None


def is_in_india(latitude, longitude):
    return (
        INDIA_LATITUDE[0] <= latitude <= INDIA_LATITUDE[1]
        and INDIA_LONGITUDE[0] <= longitude <= INDIA_LONGITUDE[1]
    )


# File signatures for the allowed document formats.
_SIGNATURES = [
    ("application/pdf", ".pdf", lambda head: head.startswith(b"%PDF-")),
    ("image/jpeg", ".jpg", lambda head: head.startswith(b"\xff\xd8\xff")),
    ("image/png", ".png", lambda head: head.startswith(b"\x89PNG\r\n\x1a\n")),
    ("image/webp", ".webp", lambda head: head[:4] == b"RIFF" and head[8:12] == b"WEBP"),
]


def detect_document_type(head):
    """Identify a document from its first bytes. Returns (content_type, extension) or None."""
    for content_type, extension, matches in _SIGNATURES:
        if matches(head):
            return content_type, extension
    return None
