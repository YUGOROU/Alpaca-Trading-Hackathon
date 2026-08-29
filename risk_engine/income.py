"""The income overlay — short-dated theta harvesting (README §3 profit engine, §7.7-7.9).

Tuned for a short (~1 week) trading window: the P&L comes from *time decay*, which is
fastest in the last days of an option's life. So the overlay sells **weekly (~7 DTE)**,
**defined-risk** premium and lets the clock pay us:

  * covered calls   — sell weekly calls against stock we own (income + a cushion)
  * iron condors    — sell an OTM put spread AND an OTM call spread on the index; profit
                      if it stays in a range while both sides decay. Defined risk both
                      sides, capital-light — the core theta engine.

How much to deploy scales with `scoring.income_aggressiveness` (rich IV + calm market
-> harvest; risk-off -> stop selling). Everything is priced with Black-Scholes and
sized against real capital/position limits, so it runs offline and the executor just
places what it returns. No broker, no LLM here.

Pricing note: Alpaca gives per-name IV only as a live snapshot, so offline we price each
leg with the *index* IV as a proxy (documented approximation, same spirit as §7.1's
Black-Scholes fallback). Production swaps in the live per-contract quote.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import floor

from . import blackscholes as bs
from . import payoffs, scoring
from .metrics import variance_risk_premium
from .types import MarketData, Portfolio, RiskSnapshot

# --- Tunable knobs (calibrated on the dev account, kept simple for the MVP) ---
DEFAULT_EXPIRY_DAYS = 5       # ~1-week expiries that decay AND expire inside a 5-day window
TARGET_CALL_DELTA = 0.30      # sell ~30-delta calls (OTM, ~70% chance kept)
TARGET_PUT_DELTA = 0.30       # sell ~30-delta puts
SPREAD_WIDTH_PCT = 0.03       # each spread's width as a fraction of spot (defined risk)
MAX_DEFINED_RISK_FRAC = 0.10  # cap total credit-spread/condor max-loss at 10% of equity


@dataclass(frozen=True)
class IncomeLeg:
    """One premium-selling position the agent proposes.

    Single-leg (covered call) uses `short_strike` only. A spread adds `long_strike`. An
    iron condor uses `short_strike`/`long_strike` for the PUT side and
    `call_short_strike`/`call_long_strike` for the CALL side.
    """

    kind: str                    # "covered_call" | "iron_condor"
    symbol: str
    short_strike: float
    long_strike: float | None    # None for a single-leg covered call
    expiry_days: int
    short_delta: float
    contracts: int
    credit: float                # $ premium collected (positive)
    max_loss: float              # $ defined risk this leg adds (0 for a covered call)
    capital_reserved: float      # $ tied up (margin ~ defined risk)
    theta_per_day: float         # $ income/day from decay (positive — we are short)
    note: str = ""
    call_short_strike: float | None = None  # iron condor call side
    call_long_strike: float | None = None

    def as_lines(self) -> list[str]:
        if self.kind == "iron_condor":
            desc = (
                f"put {self.long_strike:.0f}/{self.short_strike:.0f}  "
                f"call {self.call_short_strike:.0f}/{self.call_long_strike:.0f}"
            )
        elif self.long_strike is None:
            desc = f"${self.short_strike:.0f}"
        else:
            desc = f"sell ${self.short_strike:.0f} / buy ${self.long_strike:.0f}"
        return [
            f"{self.kind:16s} {self.symbol}  x{self.contracts}  ({self.expiry_days}d, {desc})",
            f"    credit ${self.credit:,.0f}   max_loss ${self.max_loss:,.0f}   "
            f"reserved ${self.capital_reserved:,.0f}   theta +${self.theta_per_day:,.0f}/day",
        ]

    def to_dict(self) -> dict:
        """JSON-serializable view of one premium leg (an order for the executor)."""
        return {
            "kind": self.kind,
            "symbol": self.symbol,
            "short_strike": self.short_strike,
            "long_strike": self.long_strike,
            "call_short_strike": self.call_short_strike,
            "call_long_strike": self.call_long_strike,
            "expiry_days": self.expiry_days,
            "short_delta": round(self.short_delta, 3),
            "contracts": self.contracts,
            "credit": round(self.credit, 2),
            "max_loss": round(self.max_loss, 2),
            "capital_reserved": round(self.capital_reserved, 2),
            "theta_per_day": round(self.theta_per_day, 2),
            "note": self.note,
        }


@dataclass(frozen=True)
class IncomePlan:
    """The full income overlay for one cycle."""

    legs: list[IncomeLeg]
    total_credit: float
    total_max_loss: float
    capital_reserved: float
    net_theta_per_day: float     # premium decay we collect each day (positive)
    aggressiveness: float        # 0..1 posture that produced this
    annualized_yield: float      # total_credit/equity, annualized over the expiry

    def as_lines(self) -> list[str]:
        head = [
            f"aggressiveness    = {self.aggressiveness:.2f}  ({len(self.legs)} legs)",
            f"total_credit      = ${self.total_credit:,.0f}",
            f"net_theta/day     = +${self.net_theta_per_day:,.0f}",
            f"capital_reserved  = ${self.capital_reserved:,.0f}   defined_risk ${self.total_max_loss:,.0f}",
            f"annualized_yield  = {self.annualized_yield*100:.0f}%  (credit vs equity, run-rate)",
        ]
        for leg in self.legs:
            head.extend(leg.as_lines())
        return head

    def to_dict(self) -> dict:
        """JSON-serializable view of the whole income overlay."""
        return {
            "legs": [leg.to_dict() for leg in self.legs],
            "total_credit": round(self.total_credit, 2),
            "total_max_loss": round(self.total_max_loss, 2),
            "capital_reserved": round(self.capital_reserved, 2),
            "net_theta_per_day": round(self.net_theta_per_day, 2),
            "aggressiveness": round(self.aggressiveness, 3),
            "annualized_yield": round(self.annualized_yield, 4),
        }


def _empty_plan(aggressiveness: float = 0.0) -> IncomePlan:
    return IncomePlan(
        legs=[],
        total_credit=0.0,
        total_max_loss=0.0,
        capital_reserved=0.0,
        net_theta_per_day=0.0,
        aggressiveness=aggressiveness,
        annualized_yield=0.0,
    )


def plan_income(
    portfolio: Portfolio,
    market: MarketData,
    snapshot: RiskSnapshot,
    *,
    expiry_days: int = DEFAULT_EXPIRY_DAYS,
    target_call_delta: float = TARGET_CALL_DELTA,
    target_put_delta: float = TARGET_PUT_DELTA,
) -> IncomePlan:
    """Build the weekly premium-selling overlay sized to the current IV/regime posture.

    Returns an empty plan when premium isn't worth selling (cheap IV or risk-off).
    """
    equity = portfolio.equity
    S = market.index_price
    r = market.risk_free_rate
    iv = market.index_iv  # index IV as the per-name proxy (see module docstring)
    T = expiry_days / 365.0

    vrp = variance_risk_premium(iv, snapshot.annual_vol, use_variance=False)
    agg = scoring.income_aggressiveness(vrp, snapshot.regime_signal)
    if agg <= 0.0 or T <= 0.0 or iv <= 0.0:
        return _empty_plan(agg)

    legs: list[IncomeLeg] = []

    # --- Covered calls on stock we already own (income + a small cushion) ------
    for pos in portfolio.positions:
        capacity = int(floor(pos.shares / 100))
        n = int(round(agg * capacity))
        if n <= 0:
            continue
        Ps = pos.price
        Kc = round(bs.strike_for_call_delta(Ps, target_call_delta, T, r, iv))
        prem = bs.call_price(Ps, Kc, T, r, iv)
        if prem <= 0.0:
            continue
        pay = payoffs.covered_call_payoff(Ps, Kc, prem)
        theta = -bs.call_theta_per_day(Ps, Kc, T, r, iv) * 100.0 * n  # short -> income
        legs.append(
            IncomeLeg(
                kind="covered_call",
                symbol=pos.symbol,
                short_strike=float(Kc),
                long_strike=None,
                expiry_days=expiry_days,
                short_delta=bs.call_delta(Ps, Kc, T, r, iv),
                contracts=n,
                credit=pay.credit * n,
                max_loss=0.0,  # covered by stock we hold; only caps upside
                capital_reserved=0.0,
                theta_per_day=theta,
                note=f"caps upside above ${Kc:.0f}",
            )
        )

    # --- Iron condor on the index: sell a put spread + a call spread ----------
    # Defined risk on both sides; profits if the index stays between the short strikes
    # while every leg decays. The capital-light core of the weekly theta engine.
    width = max(1.0, round(S * SPREAD_WIDTH_PCT))

    put_short = round(bs.strike_for_put_delta(S, target_put_delta, T, r, iv))
    put_long = put_short - width
    call_short = round(bs.strike_for_call_delta(S, target_call_delta, T, r, iv))
    call_long = call_short + width

    put_s_prem = bs.put_price(S, put_short, T, r, iv)
    put_l_prem = bs.put_price(S, put_long, T, r, iv)
    call_s_prem = bs.call_price(S, call_short, T, r, iv)
    call_l_prem = bs.call_price(S, call_long, T, r, iv)

    put_credit_share = put_s_prem - put_l_prem
    call_credit_share = call_s_prem - call_l_prem
    total_credit_share = put_credit_share + call_credit_share

    # One side of a condor can be breached at expiry, not both -> max loss uses one width.
    condor_risk_share = width - total_credit_share
    if total_credit_share > 0.0 and condor_risk_share > 0.0:
        per_contract_risk = condor_risk_share * 100.0
        budget = equity * MAX_DEFINED_RISK_FRAC
        max_n = int(floor(budget / per_contract_risk)) if per_contract_risk > 0 else 0
        n = min(max_n, max(1, int(round(agg * max_n)))) if max_n > 0 else 0
        if n > 0:
            put_theta = (
                -bs.put_theta_per_day(S, put_short, T, r, iv)
                + bs.put_theta_per_day(S, put_long, T, r, iv)
            )
            call_theta = (
                -bs.call_theta_per_day(S, call_short, T, r, iv)
                + bs.call_theta_per_day(S, call_long, T, r, iv)
            )
            theta = (put_theta + call_theta) * 100.0 * n
            legs.append(
                IncomeLeg(
                    kind="iron_condor",
                    symbol=market.index_symbol,
                    short_strike=float(put_short),
                    long_strike=float(put_long),
                    expiry_days=expiry_days,
                    short_delta=bs.put_delta(S, put_short, T, r, iv),
                    contracts=n,
                    credit=total_credit_share * 100.0 * n,
                    max_loss=per_contract_risk * n,
                    capital_reserved=per_contract_risk * n,  # margin ~ one side's risk
                    theta_per_day=theta,
                    note="profit if range-bound; defined risk both sides",
                    call_short_strike=float(call_short),
                    call_long_strike=float(call_long),
                )
            )

    total_credit = sum(leg.credit for leg in legs)
    total_max_loss = sum(leg.max_loss for leg in legs)
    capital_reserved = sum(leg.capital_reserved for leg in legs)
    net_theta = sum(leg.theta_per_day for leg in legs)
    # Annualize the collected credit as a rough run-rate yield headline.
    ann_yield = (total_credit / equity) * (365.0 / expiry_days) if equity else 0.0

    return IncomePlan(
        legs=legs,
        total_credit=total_credit,
        total_max_loss=total_max_loss,
        capital_reserved=capital_reserved,
        net_theta_per_day=net_theta,
        aggressiveness=agg,
        annualized_yield=ann_yield,
    )
