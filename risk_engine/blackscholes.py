"""Minimal Black-Scholes for European puts — price, delta, theta.

Lets the engine price protection and read its greeks from (S, K, T, r, IV) alone, so
it runs offline with no live option chain. In production these come from Alpaca's live
option snapshot instead; this is the self-contained fallback (README §7.1, §7.8).
"""
from __future__ import annotations

import math

SQRT_2 = math.sqrt(2.0)
SQRT_2PI = math.sqrt(2.0 * math.pi)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / SQRT_2))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / SQRT_2PI


def _d1(S: float, K: float, T: float, r: float, sigma: float) -> float:
    return (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))


def _d2(S: float, K: float, T: float, r: float, sigma: float) -> float:
    return _d1(S, K, T, r, sigma) - sigma * math.sqrt(T)


def put_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Per-share price of a European put."""
    if T <= 0 or sigma <= 0:
        return max(K - S, 0.0)  # intrinsic
    d1 = _d1(S, K, T, r, sigma)
    d2 = d1 - sigma * math.sqrt(T)
    return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def put_delta(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Put delta, in [-1, 0]."""
    if T <= 0 or sigma <= 0:
        return -1.0 if S < K else 0.0
    return _norm_cdf(_d1(S, K, T, r, sigma)) - 1.0  # = -N(-d1)


def put_theta_per_day(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Put time decay per calendar day (usually negative for a long put)."""
    if T <= 0 or sigma <= 0:
        return 0.0
    d1 = _d1(S, K, T, r, sigma)
    d2 = d1 - sigma * math.sqrt(T)
    annual = (
        -(S * _norm_pdf(d1) * sigma) / (2.0 * math.sqrt(T))
        + r * K * math.exp(-r * T) * _norm_cdf(-d2)
    )
    return annual / 365.0


# --- Call side (the short leg of a collar) (README §7.8) -------------------


def call_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Per-share price of a European call."""
    if T <= 0 or sigma <= 0:
        return max(S - K, 0.0)  # intrinsic
    d1 = _d1(S, K, T, r, sigma)
    d2 = d1 - sigma * math.sqrt(T)
    return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)


def call_delta(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Call delta, in [0, 1]."""
    if T <= 0 or sigma <= 0:
        return 1.0 if S > K else 0.0
    return _norm_cdf(_d1(S, K, T, r, sigma))


def call_theta_per_day(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Call time decay per calendar day (the income when we *sell* it)."""
    if T <= 0 or sigma <= 0:
        return 0.0
    d1 = _d1(S, K, T, r, sigma)
    d2 = d1 - sigma * math.sqrt(T)
    annual = (
        -(S * _norm_pdf(d1) * sigma) / (2.0 * math.sqrt(T))
        - r * K * math.exp(-r * T) * _norm_cdf(d2)
    )
    return annual / 365.0


# --- Second-order Greeks (same for calls and puts) (README §7.1) -----------


def gamma(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Gamma: how fast delta itself moves. `N'(d1) / (S*sigma*sqrt(T))`.

    Identical for a call and a put on the same contract. High gamma = the hedge
    drifts fast = rebalance more often.
    """
    if T <= 0 or sigma <= 0 or S <= 0:
        return 0.0
    return _norm_pdf(_d1(S, K, T, r, sigma)) / (S * sigma * math.sqrt(T))


def vega_per_point(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Vega per **1 IV percentage-point** (per share): `S*N'(d1)*sqrt(T) / 100`.

    Same for a call and a put. Long options are vega-positive — protection gains
    value when fear (IV) spikes, the bonus a linear stress test ignores (§7.5).
    """
    if T <= 0 or sigma <= 0:
        return 0.0
    return S * _norm_pdf(_d1(S, K, T, r, sigma)) * math.sqrt(T) / 100.0


# --- Strike selection by target delta (how a desk actually picks strikes) ---
#
# Income desks don't quote a strike, they quote a *delta*: "sell the 30-delta call."
# Both option deltas are monotonic in the strike (call delta falls as K rises; put
# delta also falls — more negative — as K rises), so a bisection inverts them cleanly.


def strike_for_call_delta(
    S: float, target_delta: float, T: float, r: float, sigma: float, iters: int = 64
) -> float:
    """Strike whose call has delta ≈ `target_delta` (0..1). Used to sell N-delta calls."""
    target = abs(target_delta)
    lo, hi = S * 0.5, S * 1.5  # call delta is decreasing in K
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        if call_delta(S, mid, T, r, sigma) > target:
            lo = mid  # delta too high -> need a higher (further-OTM) strike
        else:
            hi = mid
    return 0.5 * (lo + hi)


def strike_for_put_delta(
    S: float, target_delta: float, T: float, r: float, sigma: float, iters: int = 64
) -> float:
    """Strike whose put has delta ≈ `-|target_delta|`. Used to sell N-delta puts."""
    target = -abs(target_delta)
    lo, hi = S * 0.5, S * 1.5  # put delta is decreasing in K
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        if put_delta(S, mid, T, r, sigma) > target:
            lo = mid  # delta not negative enough -> need a higher strike (closer to spot)
        else:
            hi = mid
    return 0.5 * (lo + hi)
