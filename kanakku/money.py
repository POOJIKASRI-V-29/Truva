"""Money helpers. Amounts are stored as integer minor units (paise) — never floats."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

_AMOUNT = re.compile(r"^-?\d{1,13}(\.\d{1,2})?$")


class MoneyError(ValueError):
    pass


def to_minor(text: object) -> int:
    """Parse a decimal string like '1499.50' into paise (149950).

    Rejects floats-as-text with more than two decimals, thousands separators and
    anything non-numeric, so a malformed CSV can never silently lose a paisa.
    """
    if text is None:
        raise MoneyError("amount is missing")
    s = str(text).strip()
    if not s:
        raise MoneyError("amount is missing")
    if not _AMOUNT.match(s):
        raise MoneyError(f"amount {s!r} is not a decimal with at most 2 places")
    try:
        value = Decimal(s)
    except InvalidOperation as exc:  # pragma: no cover - regex already guards this
        raise MoneyError(f"amount {s!r} is not numeric") from exc
    return int(value * 100)


def from_minor(minor: int) -> str:
    """Format paise as a plain decimal string: 149950 -> '1499.50'."""
    sign = "-" if minor < 0 else ""
    minor = abs(int(minor))
    return f"{sign}{minor // 100}.{minor % 100:02d}"


def format_inr(minor: int | float | None) -> str:
    """Format paise for display using Indian digit grouping: ₹12,34,567.89."""
    if minor is None:
        return "—"
    minor = int(minor)
    sign = "-" if minor < 0 else ""
    rupees, paise = divmod(abs(minor), 100)
    digits = str(rupees)
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        digits = ",".join(groups) + "," + tail
    return f"{sign}₹{digits}.{paise:02d}"
