"""HTTP Retry-After parsing; no I/O or equipment protocol assumptions."""

from __future__ import annotations

import math
from datetime import timezone
from email.utils import parsedate_to_datetime


def retry_after_seconds(value: str | None, *, now: float) -> float | None:
    """Parse RFC 9110 delay-seconds/HTTP-date, relative to response receipt.

    Unrepresentably large integer delays fail closed (infinite cooldown for this
    client session), never fall back to a shorter local delay. Invalid headers use
    the caller's ordinary exponential backoff instead.
    """
    if value is None:
        return None
    value = value.strip()
    if value and value.isascii() and value.isdigit():
        seconds = float(value)
        # Above the exact-integer range, rounding must not shorten a server delay.
        return math.nextafter(seconds, math.inf) if seconds >= 2**53 else seconds
    try:
        target = parsedate_to_datetime(value)
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        return max(0.0, target.timestamp() - now)
    except (ValueError, TypeError, OverflowError, OSError):
        return None
