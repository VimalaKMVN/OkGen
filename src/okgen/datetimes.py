"""Date/time fields — friendly input, strict output, and random values.

Some fields hold a moment in time rather than a code or free text: the Calgary
JSON ``timestamp`` (RFC 3339 UTC with nanoseconds), and the various date fields
the layouts carry. Two things follow from that.

**Typing them by hand is awful.** ``2026-01-08T11:36:21.944107946Z`` is 30
characters, and one wrong one ships a malformed instant to whatever consumes the
file. So input is forgiving — a plain date, a date and time, or ``now`` — while
what gets WRITTEN is always the field's exact configured format. Precision you
supply is kept; precision you omit is filled with zeros.

**Randomising them needs a range, not a min/max int.** The existing bulk/generate
``random`` op picks a number between two bounds, which is meaningless for a
date. Here a range is two instants and the pick is uniform between them, then
rendered in that field's format.

Formats are per FIELD, because they genuinely differ (``timestamp`` is RFC 3339,
``transmitDate`` is ``2026-01-08``, ``adDate`` is 4 characters) — see
``config/date_fields.yaml``. A fixed-width field would reject a value of the
wrong length anyway, so the format has to be declared, not guessed.
"""

from __future__ import annotations

import random
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

# The Calgary vendor stamp: RFC 3339, UTC, 9 fractional digits.
#   2026-01-08T11:36:21.944107946Z
RFC3339_NANO = "rfc3339_nano"

_NOW_WORDS = {"now", "today"}

# Accepted INPUT shapes, most precise first. Anything matching one of these is
# understood; everything else is refused with a clear message rather than
# written through.
_INPUT_PATTERNS = [
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
]

_FRACTION = re.compile(r"[.,](\d+)")


class DateError(ValueError):
    """An unparseable or out-of-format date value."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def parse_input(value: str) -> "tuple[datetime, Optional[str]]":
    """Understand a user-typed instant -> (datetime, fractional digits or None).

    The fractional part is returned as the literal digits the user typed so that
    real nanosecond precision survives — Python's datetime only carries
    microseconds, so round-tripping through it would silently truncate a
    vendor's 9-digit value to 6.
    """
    raw = (value or "").strip()
    if not raw:
        raise DateError("empty date value")
    if raw.lower() in _NOW_WORDS:
        return _now(), None

    core = raw
    frac = None
    if core.endswith("Z") or core.endswith("z"):
        core = core[:-1]
    m = _FRACTION.search(core)
    if m:
        frac = m.group(1)
        if len(frac) > 9:
            # Refuse rather than drop digits: the caller asked for a precision
            # the format cannot hold, and silently rounding it off is exactly
            # the kind of quiet data loss this module exists to avoid.
            raise DateError(
                f"{value!r} has {len(frac)} fractional digits; this field holds "
                f"at most 9 — shorten it rather than let OkGen round it off")
        core = core[:m.start()] + core[m.end():]
    core = core.strip()

    for pattern in _INPUT_PATTERNS:
        try:
            dt = datetime.strptime(core, pattern)
            return dt.replace(tzinfo=timezone.utc), frac
        except ValueError:
            continue
    raise DateError(
        f"{value!r} is not a date OkGen understands — try 2026-01-08, "
        f"'2026-01-08 14:30', a full 2026-01-08T14:30:22.123456789Z, or 'now'")


def render(dt: datetime, fmt: str, frac: Optional[str] = None) -> str:
    """Render an instant in a field's configured format.

    ``frac`` is the caller's own fractional digits, kept verbatim when given.
    Otherwise the datetime's microseconds are used, zero-padded to the width the
    format wants — a generated "now" therefore has 6 significant digits followed
    by zeros, since that is all the system clock actually knows.
    """
    if fmt == RFC3339_NANO:
        # Always exactly 9 fractional digits: what the caller supplied, padded
        # with trailing zeros to full width. Nothing is ever cut — an
        # over-precise value is refused in parse_input instead. A generated
        # "now" therefore reads .473218000 — 6 digits the clock actually knows,
        # then zeros, at full field length.
        digits = (frac or f"{dt.microsecond:06d}").ljust(9, "0")
        return dt.strftime("%Y-%m-%dT%H:%M:%S") + "." + digits + "Z"
    return dt.strftime(fmt)


# ONE reference instant, rendered through whichever format a field declares.
# The panels show this as the hint in a temporal field's value box, so the hint
# is always the shape THAT field actually stores: `rfc3339_nano` reads
# 2026-01-08T11:36:21.944107946Z, while a field declared "%Y%m%d" reads
# 20260108. A hardcoded stamp in the client would be a lie the day a second
# format is declared — which config/date_fields.yaml explicitly allows.
#
# Fixed rather than `now`, so the hint is identical on every render and can be
# asserted by a test. The value matches the shape of the real Calgary samples.
_EXAMPLE_INSTANT = datetime(2026, 1, 8, 11, 36, 21, tzinfo=timezone.utc)
_EXAMPLE_FRAC = "944107946"


def example(fmt: str) -> str:
    """A specimen value in ``fmt`` — what this field looks like when stored."""
    return render(_EXAMPLE_INSTANT, fmt, _EXAMPLE_FRAC)


def normalize(value: str, fmt: str) -> str:
    """A user-typed value as it should be STORED. Raises :class:`DateError`."""
    dt, frac = parse_input(value)
    return render(dt, fmt, frac)


def is_valid(value: str, fmt: str) -> bool:
    try:
        normalize(value, fmt)
        return True
    except DateError:
        return False


def random_between(start: str, end: str, fmt: str,
                   rng: "random.Random" = None) -> str:
    """A uniformly random instant in [start, end], rendered in ``fmt``.

    Both bounds accept the same friendly shapes as a typed value, so a range can
    be given as two plain dates. Reversed bounds are accepted and swapped rather
    than refused — the intent is unambiguous.
    """
    a, _ = parse_input(start)
    b, _ = parse_input(end)
    if a > b:
        a, b = b, a
    span = int((b - a).total_seconds() * 1_000_000)
    pick = (rng or random).randint(0, max(span, 0))
    return render(a + timedelta(microseconds=pick), fmt)
