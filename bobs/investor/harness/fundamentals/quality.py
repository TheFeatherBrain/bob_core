"""Profitability, return metrics, and quality scores.

Piotroski F and Altman Z take a pair of ``dict[str, float | None]`` snapshots
(current + previous year) and return deterministic scores. Missing inputs
skip the offending sub-test rather than disqualifying the whole score.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict


def _safe_div(num: float | None, den: float | None) -> float | None:
    if num is None or den is None:
        return None
    try:
        n = float(num)
        d = float(den)
    except (TypeError, ValueError):
        return None
    if d == 0.0:
        return None
    return n / d


def gross_margin(gross_profit: float | None, revenue: float | None) -> float | None:
    return _safe_div(gross_profit, revenue)


def operating_margin(
    operating_income: float | None, revenue: float | None
) -> float | None:
    return _safe_div(operating_income, revenue)


def net_margin(net_income: float | None, revenue: float | None) -> float | None:
    return _safe_div(net_income, revenue)


def fcf_margin(
    free_cash_flow: float | None, revenue: float | None
) -> float | None:
    return _safe_div(free_cash_flow, revenue)


def roe(net_income: float | None, total_equity: float | None) -> float | None:
    """Return on Equity. Requires positive equity."""
    if total_equity is None:
        return None
    try:
        eq = float(total_equity)
    except (TypeError, ValueError):
        return None
    if eq <= 0.0:
        return None
    return _safe_div(net_income, eq)


def roic(
    nopat: float | None,
    invested_capital: float | None,
) -> float | None:
    """Return on Invested Capital = NOPAT / Invested Capital.

    Callers supply pre-computed NOPAT (``EBIT * (1 - tax_rate)``) and
    invested capital (``total_debt + total_equity - cash``) so the engine
    stays free of yfinance field-name lookups.
    """
    if invested_capital is None:
        return None
    try:
        ic = float(invested_capital)
    except (TypeError, ValueError):
        return None
    if ic <= 0.0:
        return None
    return _safe_div(nopat, ic)


@dataclass(frozen=True, slots=True)
class AnnualFacts:
    """Line items used by Piotroski / Altman scores.

    Every field is ``float | None`` so missing data degrades gracefully.
    """

    net_income: float | None = None
    operating_cash_flow: float | None = None
    total_assets: float | None = None
    current_assets: float | None = None
    current_liabilities: float | None = None
    long_term_debt: float | None = None
    shares_outstanding: float | None = None
    revenue: float | None = None
    gross_profit: float | None = None
    retained_earnings: float | None = None
    ebit: float | None = None
    market_cap: float | None = None
    total_liabilities: float | None = None


class PiotroskiResult(TypedDict):
    score: int
    max_possible: int
    components: dict[str, bool | None]


def piotroski_f_score(
    current: AnnualFacts,
    previous: AnnualFacts,
) -> PiotroskiResult:
    """Compute the Piotroski 9-point F-Score.

    Any sub-test whose inputs are missing is skipped (component = ``None``)
    and does not contribute to the score. The ``max_possible`` field reports
    how many sub-tests could be evaluated, so callers can normalise.
    """
    components: dict[str, bool | None] = {}

    def add(name: str, passed: bool | None) -> None:
        components[name] = passed

    # Profitability
    add("positive_net_income", _positive_or_none(current.net_income))
    add("positive_ocf", _positive_or_none(current.operating_cash_flow))
    roa_curr = _safe_div(current.net_income, current.total_assets)
    roa_prev = _safe_div(previous.net_income, previous.total_assets)
    add("roa_growth", _gt_or_none(roa_curr, roa_prev))
    if current.operating_cash_flow is not None and current.net_income is not None:
        add("ocf_gt_net_income", current.operating_cash_flow > current.net_income)
    else:
        add("ocf_gt_net_income", None)

    # Leverage / liquidity / source of funds
    ltd_ratio_curr = _safe_div(current.long_term_debt, current.total_assets)
    ltd_ratio_prev = _safe_div(previous.long_term_debt, previous.total_assets)
    add("leverage_decrease", _lt_or_none(ltd_ratio_curr, ltd_ratio_prev))
    cr_curr = _safe_div(current.current_assets, current.current_liabilities)
    cr_prev = _safe_div(previous.current_assets, previous.current_liabilities)
    add("liquidity_improvement", _gt_or_none(cr_curr, cr_prev))
    if current.shares_outstanding is not None and previous.shares_outstanding is not None:
        add(
            "no_dilution",
            current.shares_outstanding <= previous.shares_outstanding,
        )
    else:
        add("no_dilution", None)

    # Operating efficiency
    gm_curr = _safe_div(current.gross_profit, current.revenue)
    gm_prev = _safe_div(previous.gross_profit, previous.revenue)
    add("margin_improvement", _gt_or_none(gm_curr, gm_prev))
    at_curr = _safe_div(current.revenue, current.total_assets)
    at_prev = _safe_div(previous.revenue, previous.total_assets)
    add("asset_turnover_improvement", _gt_or_none(at_curr, at_prev))

    score = sum(1 for v in components.values() if v is True)
    max_possible = sum(1 for v in components.values() if v is not None)
    return PiotroskiResult(
        score=score,
        max_possible=max_possible,
        components=components,
    )


def altman_z_score(facts: AnnualFacts) -> float | None:
    """Classic Altman Z-Score for manufacturing / public firms.

    ``Z = 1.2*A + 1.4*B + 3.3*C + 0.6*D + 1.0*E`` where:
      - A = working capital / total assets
      - B = retained earnings / total assets
      - C = EBIT / total assets
      - D = market cap / total liabilities
      - E = revenue / total assets

    Returns ``None`` if any of A-E cannot be computed.
    """
    if facts.total_assets is None:
        return None
    try:
        ta = float(facts.total_assets)
    except (TypeError, ValueError):
        return None
    if ta <= 0.0:
        return None

    if facts.current_assets is None or facts.current_liabilities is None:
        return None
    working_capital = float(facts.current_assets) - float(facts.current_liabilities)

    a = working_capital / ta
    b = _safe_div(facts.retained_earnings, ta)
    c = _safe_div(facts.ebit, ta)
    d = _safe_div(facts.market_cap, facts.total_liabilities)
    e = _safe_div(facts.revenue, ta)
    if None in (b, c, d, e):
        return None
    assert b is not None and c is not None and d is not None and e is not None
    return 1.2 * a + 1.4 * b + 3.3 * c + 0.6 * d + 1.0 * e


def _positive_or_none(value: float | None) -> bool | None:
    if value is None:
        return None
    return float(value) > 0.0


def _gt_or_none(a: float | None, b: float | None) -> bool | None:
    if a is None or b is None:
        return None
    return float(a) > float(b)


def _lt_or_none(a: float | None, b: float | None) -> bool | None:
    if a is None or b is None:
        return None
    return float(a) < float(b)
