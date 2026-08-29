"""The individual risk metrics (README §7 essentials). Each is a pure function."""
from __future__ import annotations

import math

TRADING_DAYS = 252
Z_95 = 1.645
Z_99 = 2.326


def clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def simple_returns(prices: list[float]) -> list[float]:
    """Daily simple returns from a price series."""
    return [prices[i] / prices[i - 1] - 1.0 for i in range(1, len(prices))]


def ewma_daily_vol(returns: list[float], lam: float = 0.94) -> float:
    """EWMA (RiskMetrics) daily volatility — recent moves weighted more (§7.4).

    sigma^2_t = lam * sigma^2_{t-1} + (1 - lam) * r^2_{t-1}
    """
    if not returns:
        return 0.0
    var = sum(r * r for r in returns) / len(returns)  # seed with sample variance
    for r in returns:
        var = lam * var + (1.0 - lam) * r * r
    return math.sqrt(var)


def annualize_vol(daily_vol: float, periods: int = TRADING_DAYS) -> float:
    return daily_vol * math.sqrt(periods)


def drawdown_from_peak(equity: float, peak: float) -> float:
    """Fraction below the high-water mark (§7.12)."""
    if peak <= 0:
        return 0.0
    return clip((peak - equity) / peak, 0.0, 1.0)


def regime_signal(price: float, ma: float, full_at: float = 0.05) -> float:
    """0 when comfortably above the moving average (calm) .. 1 when >= `full_at`
    below it (risk-off). SPY vs its 50-day MA (§7.12)."""
    if ma <= 0:
        return 0.0
    gap_below = (ma - price) / ma  # positive when price is under the MA
    return clip(gap_below / full_at, 0.0, 1.0)


def beta_weighted_delta(positions) -> float:
    """Book's index-equivalent dollar exposure: sum(shares * price * beta) (§7.2)."""
    return sum(p.shares * p.price * p.beta for p in positions)


def parametric_var(value: float, daily_vol: float, z: float) -> float:
    """1-day parametric VaR in dollars (§7.4)."""
    return value * daily_vol * z


def expected_move(S: float, iv: float, days: int, periods: int = TRADING_DAYS) -> float:
    """Dollar move the market prices over `days`: S * IV * sqrt(days/252) (§7.6)."""
    return S * iv * math.sqrt(days / periods)


def iv_rank(iv: float, low: float, high: float) -> float:
    """Where current IV sits in its 1-yr range, 0..100 (§7.7)."""
    if high <= low:
        return 50.0
    return clip((iv - low) / (high - low) * 100.0, 0.0, 100.0)


# ---------------------------------------------------------------------------
# Distribution stats — the building blocks for the fat-tail measures below.
# ---------------------------------------------------------------------------


def mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def sample_variance(xs: list[float]) -> float:
    """Unbiased (n-1) variance."""
    n = len(xs)
    if n < 2:
        return 0.0
    m = mean(xs)
    return sum((x - m) ** 2 for x in xs) / (n - 1)


def covariance(xs: list[float], ys: list[float]) -> float:
    """Unbiased covariance over the first min(len) aligned points."""
    n = min(len(xs), len(ys))
    if n < 2:
        return 0.0
    mx, my = mean(xs[:n]), mean(ys[:n])
    return sum((xs[i] - mx) * (ys[i] - my) for i in range(n)) / (n - 1)


def correlation(xs: list[float], ys: list[float]) -> float:
    sx = math.sqrt(sample_variance(xs))
    sy = math.sqrt(sample_variance(ys))
    if sx == 0.0 or sy == 0.0:
        return 0.0
    return covariance(xs, ys) / (sx * sy)


def skewness(xs: list[float]) -> float:
    """Population skewness (third standardized moment). <0 = left tail (equities)."""
    n = len(xs)
    if n < 3:
        return 0.0
    m = mean(xs)
    s = math.sqrt(sum((x - m) ** 2 for x in xs) / n)  # population std
    if s == 0.0:
        return 0.0
    return sum(((x - m) / s) ** 3 for x in xs) / n


def excess_kurtosis(xs: list[float]) -> float:
    """Excess kurtosis (fourth moment - 3). >0 = fatter tails than normal."""
    n = len(xs)
    if n < 4:
        return 0.0
    m = mean(xs)
    s = math.sqrt(sum((x - m) ** 2 for x in xs) / n)
    if s == 0.0:
        return 0.0
    return sum(((x - m) / s) ** 4 for x in xs) / n - 3.0


# ---------------------------------------------------------------------------
# Beta & hedge ratios (§7.2, §7.3)
# ---------------------------------------------------------------------------


def ols_beta(asset_returns: list[float], market_returns: list[float]) -> float:
    """Full-sample OLS beta = Cov(asset, market) / Var(market)."""
    n = min(len(asset_returns), len(market_returns))
    if n < 2:
        return 0.0
    m = market_returns[:n]
    var = sample_variance(m)
    return covariance(asset_returns[:n], m) / var if var else 0.0


def downside_beta(
    asset_returns: list[float], market_returns: list[float], threshold: float = 0.0
) -> float:
    """Beta measured only on down-market days (market return < threshold) (§7.2).

    For equities this is usually *higher* than plain beta, so plain beta understates
    the exposure we most need to hedge. Falls back to full-sample beta if there aren't
    enough down days to regress on.
    """
    n = min(len(asset_returns), len(market_returns))
    a = [asset_returns[i] for i in range(n) if market_returns[i] < threshold]
    m = [market_returns[i] for i in range(n) if market_returns[i] < threshold]
    if len(m) < 2:
        return ols_beta(asset_returns, market_returns)
    var = sample_variance(m)
    return covariance(a, m) / var if var else 0.0


def min_variance_hedge_ratio(
    book_returns: list[float], hedge_returns: list[float]
) -> float:
    """Statistically optimal hedge size h* = Cov(book, hedge) / Var(hedge) (§7.3).

    The slope of book returns on hedge returns — the hedge quantity that minimises the
    variance of the combined position. Scales the delta-based contract count; the LLM
    still sets what fraction of h* to actually deploy.
    """
    n = min(len(book_returns), len(hedge_returns))
    if n < 2:
        return 0.0
    h = hedge_returns[:n]
    var = sample_variance(h)
    return covariance(book_returns[:n], h) / var if var else 0.0


# ---------------------------------------------------------------------------
# Tail risk — a real crash model, not just normal VaR (§7.4)
# ---------------------------------------------------------------------------


def _quantile(sorted_xs: list[float], q: float) -> float:
    """Linear-interpolated quantile of an already-sorted series, q in [0, 1]."""
    n = len(sorted_xs)
    if n == 0:
        return 0.0
    if n == 1:
        return sorted_xs[0]
    pos = clip(q, 0.0, 1.0) * (n - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    return sorted_xs[lo] * (1.0 - frac) + sorted_xs[hi] * frac


def historical_var(value: float, returns: list[float], alpha: float = 0.05) -> float:
    """Empirical VaR ($): the alpha-quantile of real returns, as a positive loss (§7.4)."""
    if not returns:
        return 0.0
    q = _quantile(sorted(returns), alpha)  # lower-tail return (usually negative)
    return max(0.0, -value * q)


def expected_shortfall(value: float, returns: list[float], alpha: float = 0.05) -> float:
    """ES / CVaR ($): the *average* loss once the VaR threshold is breached (§7.4).

    The coherent, Basel-standard tail measure — the right thing for a hedger to target.
    Always >= the historical VaR at the same alpha.
    """
    if not returns:
        return 0.0
    s = sorted(returns)
    q = _quantile(s, alpha)
    tail = [r for r in s if r <= q] or [q]
    return max(0.0, -value * (sum(tail) / len(tail)))


def cornish_fisher_var(
    value: float,
    daily_vol: float,
    skew: float,
    excess_kurt: float,
    z: float,
    mean_ret: float = 0.0,
) -> float:
    """Fat-tail VaR ($): normal VaR with the z-score bumped for skew + kurtosis (§7.4).

    Equity returns are left-skewed and fat-tailed, so normal VaR understates crash
    risk; Cornish-Fisher pushes the number toward honesty. `z` is the (positive)
    normal quantile, e.g. Z_95 or Z_99.
    """
    zc = -abs(z)  # lower-tail quantile
    w = (
        zc
        + (zc ** 2 - 1.0) / 6.0 * skew
        + (zc ** 3 - 3.0 * zc) / 24.0 * excess_kurt
        - (2.0 * zc ** 3 - 5.0 * zc) / 36.0 * skew ** 2
    )
    q = mean_ret + daily_vol * w
    return max(0.0, -value * q)


# ---------------------------------------------------------------------------
# Pricing the edge — the premium-selling signals (§7.7)
# ---------------------------------------------------------------------------


def variance_risk_premium(
    implied_vol: float, realized_vol: float, use_variance: bool = True
) -> float:
    """implied - realized: the premium-selling edge as a number (§7.7).

    Positive = options priced richer than reality delivered = selling premium pays.
    `use_variance` returns the variance form (IV^2 - RV^2); False the vol form.
    """
    if use_variance:
        return implied_vol ** 2 - realized_vol ** 2
    return implied_vol - realized_vol


def put_skew(put_iv: float, atm_iv: float) -> float:
    """Put-side IV skew: 25-delta put IV minus ATM IV (§7.7).

    How expensive crash protection is right now. Steep (large positive) skew ->
    protection is dear -> prefer spreads/collars over outright puts.
    """
    return put_iv - atm_iv


# ---------------------------------------------------------------------------
# Fills & liquidity — paper-trading reality (§7.13)
# ---------------------------------------------------------------------------


def relative_spread(bid: float, ask: float) -> float:
    """(ask - bid) / mid — the width of the quote as a fraction of price."""
    mid = 0.5 * (bid + ask)
    if mid <= 0.0:
        return 1.0
    return (ask - bid) / mid


def is_liquid(
    bid: float, ask: float, min_bid: float = 0.05, max_rel_spread: float = 0.10
) -> bool:
    """The liquidity gate: real, tight, two-sided quote we can trust to fill (§7.13)."""
    return bid >= min_bid and ask > bid and relative_spread(bid, ask) <= max_rel_spread


def paper_fill_price(bid: float, ask: float, side: str) -> float:
    """Marketable fill vs the NBBO: buyers cross to the ask, sellers hit the bid (§7.13)."""
    return ask if side == "buy" else bid
