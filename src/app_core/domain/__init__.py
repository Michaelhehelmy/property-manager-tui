"""Domain primitives for SPMS (Phase 0 foundations).

This package contains additive, side-effect-free helpers (money/date/tax/
availability/metrics primitives) intended for reuse by future business logic.
Existing application behavior and APIs are unchanged.
"""

from . import availability, dates, metrics, money, tax

__all__ = ["availability", "dates", "metrics", "money", "tax"]
