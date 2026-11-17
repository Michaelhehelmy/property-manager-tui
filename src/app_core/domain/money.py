"""Decimal-based money helpers (currency-safe, rounding-aware).

Additive primitives only; no existing behavior is modified.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, getcontext
from typing import Any

getcontext().prec = 28

ZERO = Decimal("0")
CENT = Decimal("0.01")


def to_decimal(value: Any, default: Decimal | None = ZERO) -> Decimal | None:
    """Parse numeric-like input into Decimal without float binary artifacts.

    Returns ``default`` when value is None/blank/unparseable.
    """
    if value is None:
        return default
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        return Decimal(int(value))
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    s = str(value).strip()
    if s == "":
        return default
    s = s.replace(",", "").replace(" ", "")
    for prefix in ("EGP", "USD", "EUR", "GBP", "$", "€", "£"):
        if s.upper().startswith(prefix):
            s = s[len(prefix):].strip()
            break
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return default


def quantize_money(value: Any, places: Decimal = CENT, rounding=ROUND_HALF_UP) -> Decimal:
    """Round a monetary value to the given quantum (default: 0.01)."""
    d = to_decimal(value, ZERO) or ZERO
    return d.quantize(places, rounding=rounding)


def money_add(*values: Any) -> Decimal:
    total = ZERO
    for v in values:
        total += to_decimal(v, ZERO) or ZERO
    return quantize_money(total)


def money_sub(left: Any, right: Any) -> Decimal:
    return quantize_money((to_decimal(left, ZERO) or ZERO) - (to_decimal(right, ZERO) or ZERO))


def money_mul(value: Any, factor: Any) -> Decimal:
    return quantize_money((to_decimal(value, ZERO) or ZERO) * (to_decimal(factor, ZERO) or ZERO))


def money_div(value: Any, divisor: Any, default: Decimal = ZERO) -> Decimal:
    d = to_decimal(divisor, None)
    if d is None or d == 0:
        return quantize_money(default)
    return quantize_money((to_decimal(value, ZERO) or ZERO) / d)


def percent_of(amount: Any, rate_percent: Any) -> Decimal:
    """Calculate amount * rate_percent / 100 with money rounding."""
    amt = to_decimal(amount, ZERO) or ZERO
    rate = to_decimal(rate_percent, ZERO) or ZERO
    return quantize_money(amt * rate / Decimal("100"))


def allocate_proportionally(total: Any, weights: list[Any]) -> list[Decimal]:
    """Allocate total across weights, preserving rounded sum == quantized total."""
    t = quantize_money(total)
    dec_weights = [(to_decimal(w, ZERO) or ZERO) for w in weights]
    positive = [w if w > 0 else ZERO for w in dec_weights]
    s = sum(positive, ZERO)
    if not weights or s == 0:
        return [ZERO for _ in weights]
    raw = [t * w / s for w in positive]
    rounded = [quantize_money(x) for x in raw]
    drift = t - sum(rounded, ZERO)
    if drift != ZERO:
        idx = max(range(len(positive)), key=lambda i: positive[i])
        rounded[idx] = quantize_money(rounded[idx] + drift)
    return rounded


def format_money(value: Any, currency: str = "", decimals: int = 2) -> str:
    """Format a monetary value; currency prefix optional (presentation helper)."""
    q = quantize_money(value, Decimal("1." + ("0" * max(0, decimals))))
    s = f"{q:.{max(0, decimals)}f}"
    return f"{currency} {s}".strip() if currency else s


def parse_money(value: Any, default: Decimal = ZERO) -> Decimal:
    """Parse and quantize money-like input, returning a Decimal."""
    return quantize_money(to_decimal(value, default) or default)
