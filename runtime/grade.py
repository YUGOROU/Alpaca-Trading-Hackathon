"""Self-grading - the agent marks its own homework after trades expire (README §8).

Once a cycle's options have expired, we can score what actually happened: compute the
realized P&L of that cycle's structures at the expiry price and write a plain-English
verdict back to the ledger. Reuses the same intrinsic-value math the backtest uses; the
only new input is the underlying price at expiry (looked up live, or supplied in tests).

    from runtime.grade import grade_ledger
    grade_ledger(ledger, price_lookup=source.latest_price)   # grade expired cycles
"""
from __future__ import annotations

from datetime import date, datetime, timedelta


def _intrinsic(right: str, strike: float, spot: float) -> float:
    return max(0.0, strike - spot) if right == "P" else max(0.0, spot - strike)


def grade_entry(entry: dict, price_lookup) -> dict:
    """Realized option-overlay P&L + a verdict for one cycle, at expiry.

    `price_lookup(symbol) -> price` gives the underlying price at expiry. Realized P&L =
    income credit collected - hedge premium paid + the intrinsic settlement of every leg
    (we owe intrinsic on shorts, receive it on longs). Grades the *option decisions*, not
    the book's stock move.
    """
    credit = entry.get("credit", 0.0)
    hedge_cost = entry.get("hedge_cost", 0.0)
    settle = 0.0
    hedge_settle = 0.0
    for o in entry.get("orders", []):
        spot = price_lookup(o["symbol"])
        for leg in o["legs"]:
            sign = 1.0 if leg["action"] == "buy" else -1.0
            val = sign * _intrinsic(leg["right"], leg["strike"], spot) * 100.0 * o["contracts"]
            settle += val
            if o["structure"] == "protective_put":
                hedge_settle += val
    realized = credit - hedge_cost + settle
    return {
        "realized_pnl": round(realized, 2),
        "verdict": _verdict(entry, realized, hedge_settle, hedge_cost),
        "status": "graded",
    }


def _verdict(entry: dict, realized: float, hedge_settle: float, hedge_cost: float) -> str:
    if hedge_cost > 0 and hedge_settle > hedge_cost:
        return f"hedge paid off (+${hedge_settle - hedge_cost:,.0f}) - protection worked"
    if hedge_cost > 0:
        return (f"protection cost ${hedge_cost - hedge_settle:,.0f}, market held - "
                "correct discipline")
    if realized > 0:
        return f"clean harvest - kept +${realized:,.0f} in premium"
    if realized < 0:
        return f"trade went against us - -${abs(realized):,.0f}"
    return "flat"


def _entry_date(entry: dict) -> date:
    ts = entry.get("ts", "")
    try:
        return datetime.fromisoformat(ts).date()
    except ValueError:
        return date.today()


def grade_ledger(ledger, price_lookup, now_date: date | None = None) -> int:
    """Grade every un-graded cycle whose options have expired. Returns count graded.

    `now_date` (defaults to today) decides what's expired; a cycle expires
    max(order expiry_days) after it was opened. SIT cycles (no orders) grade as flat.
    """
    now = now_date or date.today()
    graded = 0
    for e in ledger.entries(newest_first=False):
        if e.get("grade"):
            continue
        orders = e.get("orders", [])
        if not orders:
            ledger.grade(e["id"], {"realized_pnl": 0.0, "verdict": "sat out - no trades",
                                   "status": "flat"})
            graded += 1
            continue
        exp_days = max(o.get("expiry_days", 0) for o in orders)
        if now < _entry_date(e) + timedelta(days=exp_days):
            continue  # not expired yet
        ledger.grade(e["id"], grade_entry(e, price_lookup))
        graded += 1
    return graded
