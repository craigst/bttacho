"""Field decoders for card data.

Two conventions run through every EF:

* Timestamps are 4-byte big-endian Unix seconds, interpreted as UTC.
* Text is codepage-prefixed: the first byte selects iso-8859-<n>, the remainder
  is the string, right-padded with NUL or spaces.
"""

from datetime import datetime, timezone
from typing import Optional


def decode_timestamp(data: bytes) -> Optional[datetime]:
    """4-byte BE Unix seconds -> aware UTC datetime. Zero means 'unset'."""
    if len(data) < 4:
        return None
    ts = int.from_bytes(data[:4], "big")
    if ts == 0:
        return None
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def decode_odometer(data: bytes) -> int:
    """3-byte BE kilometre reading."""
    if len(data) < 3:
        return 0
    return int.from_bytes(data[:3], "big")


def decode_text(data: bytes) -> str:
    """Codepage-prefixed string. Codepage 0 means plain ASCII."""
    if len(data) < 2:
        return ""
    codepage = data[0]
    body = data[1:].rstrip(b"\x00").rstrip(b" ")
    if codepage == 0:
        return body.decode("ascii", errors="ignore")
    try:
        return body.decode(f"iso-8859-{codepage}", errors="ignore")
    except LookupError:
        return body.decode("ascii", errors="ignore")


def decode_ascii(data: bytes) -> str:
    """Fixed-width ASCII field with NUL/space padding (e.g. card number)."""
    return data.rstrip(b"\x00").rstrip(b" ").decode("ascii", errors="ignore")
