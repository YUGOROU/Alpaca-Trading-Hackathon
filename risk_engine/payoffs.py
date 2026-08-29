"""Option-structure payoffs and scenario stress (README §7.5, §7.8, §7.9).

Pure arithmetic on strikes, premiums, and a shock size — the cost / max-loss /
break-even of each hedge structure the agent can put on, plus the hedged-vs-unhedged
loss under a market shock. Premiums are quoted **per share**; each US-listed contract
covers **100 shares** (`SHARES` below). No pricing model and no broker here — feed these
premiums from Black-Scholes (`blackscholes.py`) or a live Alpaca option snapshot.
"""
from __future__ import annotations

from dataclasses import dataclass

SHARES = 100  # one US option contract = 100 shares


@dataclass(frozen=True)
class ProtectivePutPayoff:
    """Own the stock + buy a put — a floor under the position (§7.8 baseline)."""

    cost: float          # $ premium paid
    max_loss: float      # $ worst case (down to the strike + premium)
    breakeven: float     # stock must reach this on the upside to recoup premium

    def as_lines(self) -> list[str]:
        return [
            f"cost              = ${self.cost:,.0f}",
            f"max_loss          = ${self.max_loss:,.0f}",
            f"breakeven (up)    = ${self.breakeven:,.2f}",
        ]


@dataclass(frozen=True)
class CollarPayoff:
    """Own the stock + buy a put + sell a call — protection that pays for itself (§7.8)."""

    net_cost: float      # $ put premium - call premium (often ~0 or a credit)
    max_loss: float      # $ capped downside (below the put strike)
    max_gain: float      # $ capped upside (above the call strike)
    breakeven: float     # stock level where the structure nets to zero

    def as_lines(self) -> list[str]:
        return [
            f"net_cost          = ${self.net_cost:,.0f}",
            f"max_loss          = ${self.max_loss:,.0f}",
            f"max_gain          = ${self.max_gain:,.0f}",
            f"breakeven         = ${self.breakeven:,.2f}",
        ]


@dataclass(frozen=True)
class PutSpreadPayoff:
    """Buy a put + sell a further-OTM put — cheaper, capped protection (§7.9)."""

    net_debit: float         # $ paid (< the outright put)
    max_protection: float    # $ most it can pay off (between the strikes)
    lower_strike: float
    upper_strike: float

    def as_lines(self) -> list[str]:
        return [
            f"net_debit         = ${self.net_debit:,.0f}",
            f"max_protection    = ${self.max_protection:,.0f}",
            f"protected_band    = ${self.lower_strike:.0f} .. ${self.upper_strike:.0f}",
        ]


@dataclass(frozen=True)
class StressResult:
    """Hedged-vs-unhedged P&L under a market shock (§7.5). Losses are negative."""

    shock_pct: float
    unhedged_pnl: float
    hedge_pnl: float
    net_pnl: float

    def as_lines(self) -> list[str]:
        return [
            f"shock             = {self.shock_pct*100:+.0f}%",
            f"unhedged_pnl      = ${self.unhedged_pnl:,.0f}",
            f"hedge_pnl         = ${self.hedge_pnl:,.0f}",
            f"net_pnl           = ${self.net_pnl:,.0f}",
        ]


def protective_put_payoff(
    stock_price: float, put_strike: float, put_premium: float, shares: int = SHARES
) -> ProtectivePutPayoff:
    """Cost, max loss and upside break-even of owning stock + one long put."""
    cost = put_premium * shares
    max_loss = (stock_price - put_strike) * shares + cost
    return ProtectivePutPayoff(
        cost=cost,
        max_loss=max_loss,
        breakeven=stock_price + put_premium,
    )


def collar_payoff(
    stock_price: float,
    put_strike: float,
    call_strike: float,
    put_premium: float,
    call_premium: float,
    shares: int = SHARES,
) -> CollarPayoff:
    """Net cost, capped loss/gain and break-even of a collar (§7.8)."""
    net_cost = (put_premium - call_premium) * shares
    return CollarPayoff(
        net_cost=net_cost,
        max_loss=(stock_price - put_strike) * shares + net_cost,
        max_gain=(call_strike - stock_price) * shares - net_cost,
        breakeven=stock_price + net_cost / shares,
    )


def put_spread_payoff(
    long_strike: float,
    short_strike: float,
    long_premium: float,
    short_premium: float,
    shares: int = SHARES,
) -> PutSpreadPayoff:
    """Net debit and max protection of a bought/sold put spread (§7.9).

    `long_strike` is the higher (bought) strike; `short_strike` the lower (sold) one.
    """
    net_debit = (long_premium - short_premium) * shares
    return PutSpreadPayoff(
        net_debit=net_debit,
        max_protection=(long_strike - short_strike) * shares - net_debit,
        lower_strike=short_strike,
        upper_strike=long_strike,
    )


# ---------------------------------------------------------------------------
# Income structures — premium we *sell* to generate P&L (README §3, §7.7).
# Credits are positive $ collected; "max_loss" is the position's defined risk.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CoveredCallPayoff:
    """Own the stock + sell a call — income + a small cushion, capped upside (§3)."""

    credit: float          # $ premium collected
    capped_gain: float     # $ max gain if called away (appreciation to strike + credit)
    downside_cushion: float  # $ the credit softens a drop by this much
    call_strike: float

    def as_lines(self) -> list[str]:
        return [
            f"credit            = ${self.credit:,.0f}",
            f"capped_gain       = ${self.capped_gain:,.0f}  (called away at ${self.call_strike:.0f})",
            f"downside_cushion  = ${self.downside_cushion:,.0f}",
        ]


@dataclass(frozen=True)
class CashSecuredPutPayoff:
    """Sell a put, set aside cash — get paid to (maybe) buy the stock lower (§7.7)."""

    credit: float           # $ premium collected
    capital_reserved: float  # $ cash set aside (strike * 100 * contracts)
    max_loss: float         # $ worst case (stock -> 0), net of the credit
    breakeven: float        # effective purchase price if assigned
    put_strike: float

    def as_lines(self) -> list[str]:
        return [
            f"credit            = ${self.credit:,.0f}",
            f"capital_reserved  = ${self.capital_reserved:,.0f}",
            f"max_loss          = ${self.max_loss:,.0f}  (if assigned, to $0)",
            f"breakeven         = ${self.breakeven:,.2f}",
        ]


@dataclass(frozen=True)
class CreditSpreadPayoff:
    """Sell a put, buy a further-OTM put — defined-risk income (§7.9, credit side)."""

    net_credit: float      # $ collected (= max gain)
    max_loss: float        # $ defined worst case (width - credit)
    breakeven: float       # underlying level where the spread nets to zero
    short_strike: float
    long_strike: float

    def as_lines(self) -> list[str]:
        return [
            f"net_credit        = ${self.net_credit:,.0f}  (= max gain)",
            f"max_loss          = ${self.max_loss:,.0f}  (defined)",
            f"breakeven         = ${self.breakeven:,.2f}",
            f"strikes           = sell ${self.short_strike:.0f} / buy ${self.long_strike:.0f}",
        ]


def covered_call_payoff(
    stock_price: float, call_strike: float, call_premium: float, shares: int = SHARES
) -> CoveredCallPayoff:
    """Credit, capped upside, and downside cushion of selling a call on owned stock."""
    credit = call_premium * shares
    return CoveredCallPayoff(
        credit=credit,
        capped_gain=(call_strike - stock_price) * shares + credit,
        downside_cushion=credit,
        call_strike=call_strike,
    )


def cash_secured_put_payoff(
    put_strike: float, put_premium: float, shares: int = SHARES
) -> CashSecuredPutPayoff:
    """Credit, cash reserved, and worst case of a cash-secured short put."""
    credit = put_premium * shares
    return CashSecuredPutPayoff(
        credit=credit,
        capital_reserved=put_strike * shares,
        max_loss=put_strike * shares - credit,
        breakeven=put_strike - put_premium,
        put_strike=put_strike,
    )


def bull_put_spread_payoff(
    short_strike: float,
    long_strike: float,
    short_premium: float,
    long_premium: float,
    shares: int = SHARES,
) -> CreditSpreadPayoff:
    """Net credit and defined max loss of a bull put (credit) spread (§7.9).

    Sell the higher `short_strike` put, buy the lower `long_strike` put for protection.
    """
    net_credit = (short_premium - long_premium) * shares
    width = (short_strike - long_strike) * shares
    return CreditSpreadPayoff(
        net_credit=net_credit,
        max_loss=width - net_credit,
        breakeven=short_strike - net_credit / shares,
        short_strike=short_strike,
        long_strike=long_strike,
    )


def bear_call_spread_payoff(
    short_strike: float,
    long_strike: float,
    short_premium: float,
    long_premium: float,
    shares: int = SHARES,
) -> CreditSpreadPayoff:
    """Net credit and defined max loss of a bear call (credit) spread — the call side
    of an iron condor. Sell the lower `short_strike` call, buy the higher `long_strike`
    call for protection.
    """
    net_credit = (short_premium - long_premium) * shares
    width = (long_strike - short_strike) * shares
    return CreditSpreadPayoff(
        net_credit=net_credit,
        max_loss=width - net_credit,
        breakeven=short_strike + net_credit / shares,
        short_strike=short_strike,
        long_strike=long_strike,
    )


def stress_pnl(
    beta_weighted_delta: float,
    shock_pct: float,
    index_price: float,
    hedge_contracts: int = 0,
    put_delta: float = 0.0,
    shares: int = SHARES,
) -> StressResult:
    """P&L under an index shock, with and without the put hedge (§7.5).

    `beta_weighted_delta` is the book's $ SPY-equivalent exposure (§7.2); `put_delta`
    is negative for a long put. The linear estimate ignores the vega bonus a real crash
    gives the puts (IV spikes), so the true hedged outcome is a touch *better* than this.
    """
    unhedged = beta_weighted_delta * shock_pct
    index_move = index_price * shock_pct
    hedge = hedge_contracts * shares * put_delta * index_move
    return StressResult(
        shock_pct=shock_pct,
        unhedged_pnl=unhedged,
        hedge_pnl=hedge,
        net_pnl=unhedged + hedge,
    )
