"""Year-over-year growth.

Returns ``None`` for anything it cannot compute cleanly (missing data,
non-positive base).
"""

from __future__ import annotations


def yoy_growth(current: float | None, previous: float | None) -> float | None:
    """Year-over-year growth as a decimal fraction.

    Returns ``None`` when ``previous`` is non-positive (the ratio would
    flip sign in misleading ways).
    """
    if current is None or previous is None:
        return None
    try:
        c = float(current)
        p = float(previous)
    except (TypeError, ValueError):
        return None
    if p <= 0.0:
        return None
    return c / p - 1.0
