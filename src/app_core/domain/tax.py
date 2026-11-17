"""Basic tax calculation helpers (additive, reusable primitives).

These helpers are pure and do not alter existing pricing/report behavior.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from .money import ZERO, money_mul, percent_of, quantize_money, to_decimal


def tax_amount(net_amount: Any, rate_percent: Any) -> Decimal:
    """Tax for a net amount at a percentage rate (e.g. 14 -> 14%)."""
    return percent_of(net_amount, rate_percent)


def gross_from_net(net_amount: Any, rate_percent: Any) -> Decimal:
    net = to_decimal(net_amount, ZERO) or ZERO
    return quantize_money(net + tax_amount(net, rate_percent))


def net_from_gross(gross_amount: Any, rate_percent: Any) -> Decimal:
    gross = to_decimal(gross_amount, ZERO) or ZERO
    rate = to_decimal(rate_percent, ZERO) or ZERO
    denom = Decimal("1") + (rate / Decimal("100"))
    if denom == 0:
        return quantize_money(gross)
    return quantize_money(gross / denom)


def split_gross(gross_amount: Any, rate_percent: Any) -> dict[str, Decimal]:
    """Split gross into net/tax components (tax-inclusive extraction)."""
    gross = quantize_money(gross_amount)
    net = net_from_gross(gross, rate_percent)
    tax = quantize_money(gross - net)
    return {"gross": gross, "net": net, "tax": tax, "rate_percent": to_decimal(rate_percent, ZERO) or ZERO}


def apply_discount(amount: Any, discount_percent: Any = 0, discount_amount: Any = 0) -> Decimal:
    """Apply percent discount then fixed discount, floored at zero."""
    base = to_decimal(amount, ZERO) or ZERO
    pct = to_decimal(discount_percent, ZERO) or ZERO
    fixed = to_decimal(discount_amount, ZERO) or ZERO
    out = base - (base * pct / Decimal("100")) - fixed
    if out < ZERO:
        out = ZERO
    return quantize_money(out)


def line_totals(quantity: Any, unit_price: Any, tax_rate_percent: Any = 0,
                discount_percent: Any = 0, discount_amount: Any = 0) -> dict[str, Decimal]:
    qty = to_decimal(quantity, ZERO) or ZERO
    unit = to_decimal(unit_price, ZERO) or ZERO
    subtotal = quantize_money(qty * unit)
    discounted = apply_discount(subtotal, discount_percent, discount_amount)
    tax = tax_amount(discounted, tax_rate_percent)
    gross = quantize_money(discounted + tax)
    return {
        "quantity": qty,
        "unit_price": quantize_money(unit),
        "subtotal": subtotal,
        "net": discounted,
        "tax": tax,
        "gross": gross,
    }
