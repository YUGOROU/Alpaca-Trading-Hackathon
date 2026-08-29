"""Data structures the engine consumes and produces.

Inputs (Position / Portfolio / MarketData) are what a data feed (Alpaca, later) fills
in. Outputs (RiskSnapshot / HedgePlan) are what the LLM reads and the executor acts on.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Position:
    """One holding in the base book."""

    symbol: str
    shares: float
    price: float
    beta: float = 1.0  # sensitivity to the index (SPY); 1.0 = moves with it

    @property
    def market_value(self) -> float:
        return self.shares * self.price


@dataclass
class Portfolio:
    """The base book the agent protects, plus cash."""

    positions: list[Position]
    cash: float = 0.0
    peak_equity: float | None = None  # high-water mark, for drawdown (defaults to equity)

    @property
    def market_value(self) -> float:
        return sum(p.market_value for p in self.positions)

    @property
    def equity(self) -> float:
        return self.market_value + self.cash


@dataclass(frozen=True)
class MarketData:
    """Market state for the index we hedge with (e.g. SPY)."""

    index_symbol: str
    index_price: float
    index_ma50: float              # 50-day moving average of the index
    index_returns: list[float]     # recent daily returns of the index (for EWMA vol)
    index_iv: float                # current implied vol (annualized, decimal, e.g. 0.18)
    iv_year_low: float             # 1-yr low of index IV (for IV Rank)
    iv_year_high: float            # 1-yr high of index IV
    risk_free_rate: float = 0.04


@dataclass(frozen=True)
class RiskSnapshot:
    """What the math engine measures each cycle (README §7.0)."""

    equity: float
    beta_weighted_delta: float  # $ SPY-equivalent exposure (§7.2)
    daily_vol: float            # EWMA daily vol, decimal (§7.4)
    annual_vol: float
    drawdown: float             # fraction from peak (§7.12)
    regime_signal: float        # 0 (calm, above MA) .. 1 (risk-off, below MA) (§7.12)
    var_95: float               # $ 1-day 95% VaR (§7.4)
    var_99: float
    iv_rank: float              # 0..100 — is protection cheap? (§7.7)
    expected_move_30d: float    # $ over 30 days (§7.6)
    risk_score: float           # 0..100 composite (§7.12)
    target_coverage: float      # 0..1 — how much of the book to protect

    def as_lines(self) -> list[str]:
        return [
            f"equity            = ${self.equity:,.0f}",
            f"beta_wtd_delta    = ${self.beta_weighted_delta:,.0f} (SPY-equiv)",
            f"EWMA vol          = {self.daily_vol*100:.2f}%/day  ({self.annual_vol*100:.1f}%/yr)",
            f"drawdown          = {self.drawdown*100:.1f}% from peak",
            f"regime_signal     = {self.regime_signal:.2f}  (0 calm .. 1 risk-off)",
            f"95% 1-day VaR     = ${self.var_95:,.0f}   (99%: ${self.var_99:,.0f})",
            f"IV Rank           = {self.iv_rank:.0f}   (0 cheap .. 100 dear)",
            f"expected_move_30d = ${self.expected_move_30d:,.0f}",
            f"RISK SCORE        = {self.risk_score:.0f} / 100",
            f"target_coverage   = {self.target_coverage*100:.0f}%",
        ]

    def to_dict(self) -> dict:
        """JSON-serializable view for the agent/harness to read (§4 DECIDE input)."""
        return {
            "equity": round(self.equity, 2),
            "beta_weighted_delta": round(self.beta_weighted_delta, 2),
            "daily_vol": round(self.daily_vol, 6),
            "annual_vol": round(self.annual_vol, 4),
            "drawdown": round(self.drawdown, 4),
            "regime_signal": round(self.regime_signal, 3),
            "var_95": round(self.var_95, 2),
            "var_99": round(self.var_99, 2),
            "iv_rank": round(self.iv_rank, 1),
            "expected_move_30d": round(self.expected_move_30d, 2),
            "risk_score": round(self.risk_score, 1),
            "target_coverage": round(self.target_coverage, 3),
        }


@dataclass(frozen=True)
class HedgePlan:
    """The protective-put action the engine recommends (README §7.3, §7.8, §7.10)."""

    action: str                  # "increase" | "reduce" | "hold"
    target_coverage: float       # 0..1
    current_coverage: float      # 0..1 (from contracts already held)
    put_strike: float
    put_expiry_days: int
    put_delta: float
    contracts_target: int        # contracts to hold at target coverage
    contracts_delta: int         # +buy / -sell vs. what we hold now
    premium_per_contract: float  # $ (per 100 shares)
    total_cost: float            # $ to reach target
    hedge_cost_drag: float       # total_cost / equity
    theta_per_day: float         # $ decay/day per contract held
    full_hedge_contracts: float  # contracts for 100% delta-neutral

    def as_lines(self) -> list[str]:
        verb = {"increase": "BUY", "reduce": "SELL/CLOSE", "hold": "HOLD"}[self.action]
        return [
            f"action            = {self.action.upper()}  ({verb} {abs(self.contracts_delta)} contracts)",
            f"coverage          = {self.current_coverage*100:.0f}% -> {self.target_coverage*100:.0f}%",
            f"put               = {self.put_expiry_days}d  ${self.put_strike:.0f} strike  (delta {self.put_delta:+.2f})",
            f"contracts_target  = {self.contracts_target}  (full delta-neutral = {self.full_hedge_contracts:.1f})",
            f"premium/contract  = ${self.premium_per_contract:,.0f}",
            f"total_cost        = ${self.total_cost:,.0f}   (drag {self.hedge_cost_drag*100:.2f}% of equity)",
            f"theta/contract    = ${self.theta_per_day:,.0f}/day",
        ]

    def to_dict(self) -> dict:
        """JSON-serializable view of the hedge order (§7.3)."""
        return {
            "action": self.action,
            "target_coverage": round(self.target_coverage, 3),
            "current_coverage": round(self.current_coverage, 3),
            "put_strike": round(self.put_strike, 2),
            "put_expiry_days": self.put_expiry_days,
            "put_delta": round(self.put_delta, 3),
            "contracts_target": self.contracts_target,
            "contracts_delta": self.contracts_delta,
            "premium_per_contract": round(self.premium_per_contract, 2),
            "total_cost": round(self.total_cost, 2),
            "hedge_cost_drag": round(self.hedge_cost_drag, 4),
            "theta_per_day": round(self.theta_per_day, 2),
            "full_hedge_contracts": round(self.full_hedge_contracts, 2),
        }
