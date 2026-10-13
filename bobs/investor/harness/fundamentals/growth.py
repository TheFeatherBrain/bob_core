"""Growth computations over time series of fundamentals.

Inputs are ordinary lists or sequences of floats, **ordered chronologically**
(oldest first). Functions return ``None`` for anything they cannot compute
cleanly (missing data, non-positive base, insufficient observations).
"""

from __future__ import annotations

from collections.abc import Sequence


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


def cagr(start: float | None, end: float | None, years: int) -> float | None:
    """Compound Annual Growth Rate between ``start`` and ``end`` over
    ``years`` years.

    Both endpoints must be positive; ``years`` must be ≥ 1.
    """
    if start is None or end is None:
        return None
    try:
        s = float(start)
        e = float(end)
    except (TypeError, ValueError):
        return None
    if s <= 0.0 or e <= 0.0 or years < 1:
        return None
    result: float = (e / s) ** (1.0 / years) - 1.0
    return result


def series_yoy(series: Sequence[float | None]) -> float | None:
    """YoY using the last two observations of ``series`` (oldest first)."""
    if len(series) < 2:
        return None
    return yoy_growth(series[-1], series[-2])


def series_cagr(
    series: Sequence[float | None], years: int
) -> float | None:
    """CAGR across the last ``years + 1`` observations."""
    if len(series) < years + 1:
        return None
    end = series[-1]
    start = series[-1 - years]
    return cagr(start, end, years)
