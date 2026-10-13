"""Absolute valuation helpers.

``simplified_dcf`` runs a 2-stage free cash flow model: a high-growth
``years`` horizon followed by perpetual terminal growth.

Usage:
    python3 harness/fundamentals/valuation.py --reverse --target <value> --fcf <cash flow> [--discount 0.10]
"""

from __future__ import annotations


def simplified_dcf(
    fcf: float | None,
    growth_rate: float,
    discount_rate: float,
    *,
    years: int = 5,
    terminal_growth: float = 0.025,
) -> float | None:
    """Two-stage DCF intrinsic value from ``fcf``.

    Projects ``fcf`` forward at ``growth_rate`` for ``years`` years, then
    capitalises the final year's cash flow with a Gordon terminal value at
    ``terminal_growth``. Requires ``discount_rate > terminal_growth``.
    """
    if fcf is None:
        return None
    try:
        cash = float(fcf)
    except (TypeError, ValueError):
        return None
    if cash <= 0.0 or discount_rate <= terminal_growth or years < 1:
        return None

    present_value = 0.0
    projected = cash
    for year in range(1, years + 1):
        projected = projected * (1.0 + growth_rate)
        present_value += projected / (1.0 + discount_rate) ** year

    terminal_fcf = projected * (1.0 + terminal_growth)
    terminal_value = terminal_fcf / (discount_rate - terminal_growth)
    present_value += terminal_value / (1.0 + discount_rate) ** years
    return present_value


def reverse_dcf(
    target_value: float,
    fcf: float,
    discount_rate: float = 0.10,
    *,
    years: int = 10,
    terminal_growth: float = 0.025,
) -> float | None:
    """The yearly growth over ``years`` that makes ``simplified_dcf`` equal ``target_value``.

    A plain DCF called Palantir and NVIDIA overvalued for years. Asking what the price already
    assumes ("35% a year for 10 years") turns that into a claim a thesis can argue with evidence.
    Bisection between -50% and +200%; outside that range the answer is None, not a clamp.
    """
    low, high = -0.5, 2.0
    if fcf is None or fcf <= 0 or target_value is None or target_value <= 0:
        return None

    def value(g: float) -> float | None:
        return simplified_dcf(fcf, g, discount_rate, years=years, terminal_growth=terminal_growth)

    v_low, v_high = value(low), value(high)
    if v_low is None or v_high is None or not v_low <= target_value <= v_high:
        return None
    for _ in range(200):
        mid = (low + high) / 2
        if value(mid) < target_value:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Implied growth from a value and a cash flow.")
    ap.add_argument("--reverse", action="store_true", required=True)
    ap.add_argument("--target", type=float, required=True, help="market cap or enterprise value")
    ap.add_argument("--fcf", type=float, required=True, help="trailing free (or operating) cash flow")
    ap.add_argument("--discount", type=float, default=0.10)
    a = ap.parse_args(argv)
    g = reverse_dcf(a.target, a.fcf, a.discount)
    if g is None:
        print("FAIL: no growth rate between -50% and +200% a year explains this value")
        return 1
    print(f"OK implied growth {g:.1%} a year for 10 years, then 2.5%, at a {a.discount:.0%} discount rate")
    return 0


if __name__ == "__main__":
    import pathlib
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
    import scoutpath  # noqa: F401
    import oscompat
    oscompat.utf8_stdio()
    raise SystemExit(main(sys.argv[1:]))
