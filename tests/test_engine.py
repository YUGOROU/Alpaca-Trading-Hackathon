"""Unit tests locking down the deterministic core (risk_engine + feed).

Pure standard-library `unittest` — no extra installs. Run from the repo root:

    python -m unittest discover -s tests
    python -m unittest tests.test_engine

These test the *math* (put-call parity, Greek signs, delta-strike round-trips, tail-risk
ordering, beta recovery, the iron-condor risk identity) and the *adaptive behavior*
(calm -> sit, rich+calm -> harvest, risk-off -> defend). The demo_*.py files are the
integration smoke tests; this is the unit net that freezes the foundation.
"""
from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path

from risk_engine import (
    MarketData,
    Portfolio,
    Position,
    assess,
    plan_hedge,
    plan_strategy,
    validate_plan,
)
from risk_engine import blackscholes as bs
from risk_engine import metrics, payoffs, scoring
from feed import MockDataSource, StateStore, compute_beta, moving_average, observe
from runtime.strategy_api import get_strategy_context


# --- shared fixtures --------------------------------------------------------

def _book(spy=560.0, qqq=480.0, peak=100_000.0, cash=20_000.0) -> Portfolio:
    return Portfolio(
        positions=[
            Position("SPY", shares=300, price=spy, beta=1.0),
            Position("QQQ", shares=200, price=qqq, beta=1.1),
        ],
        cash=cash,
        peak_equity=peak,
    )


CALM = MarketData("SPY", 560.0, 545.0,
                  [0.003, -0.002, 0.004, 0.001, -0.003, 0.002, 0.0, 0.003, -0.001, 0.002],
                  0.12, 0.10, 0.40)
ELEVATED = MarketData("SPY", 555.0, 545.0,
                      [0.006, -0.005, 0.008, -0.004, 0.007, -0.006, 0.005, -0.007, 0.006, -0.005],
                      0.24, 0.10, 0.40)
STRESSED = MarketData("SPY", 520.0, 550.0,
                      [-0.005, 0.002, -0.020, -0.025, -0.018, -0.030, 0.010, -0.022, -0.015, -0.028],
                      0.30, 0.10, 0.40)


class TestBlackScholes(unittest.TestCase):
    S, K, T, r, sig = 100.0, 100.0, 0.5, 0.04, 0.20

    def test_put_call_parity(self):
        c = bs.call_price(self.S, self.K, self.T, self.r, self.sig)
        p = bs.put_price(self.S, self.K, self.T, self.r, self.sig)
        rhs = self.S - self.K * math.exp(-self.r * self.T)
        self.assertAlmostEqual(c - p, rhs, places=6)

    def test_delta_ranges_and_relationship(self):
        cd = bs.call_delta(self.S, self.K, self.T, self.r, self.sig)
        pd = bs.put_delta(self.S, self.K, self.T, self.r, self.sig)
        self.assertTrue(0.0 <= cd <= 1.0)
        self.assertTrue(-1.0 <= pd <= 0.0)
        self.assertAlmostEqual(cd - pd, 1.0, places=6)  # C_delta - P_delta = 1

    def test_gamma_vega_positive(self):
        self.assertGreater(bs.gamma(self.S, self.K, self.T, self.r, self.sig), 0)
        self.assertGreater(bs.vega_per_point(self.S, self.K, self.T, self.r, self.sig), 0)

    def test_strike_for_delta_roundtrip(self):
        T = 7 / 365.0
        kc = bs.strike_for_call_delta(self.S, 0.30, T, self.r, self.sig)
        self.assertAlmostEqual(bs.call_delta(self.S, kc, T, self.r, self.sig), 0.30, places=2)
        kp = bs.strike_for_put_delta(self.S, 0.30, T, self.r, self.sig)
        self.assertAlmostEqual(bs.put_delta(self.S, kp, T, self.r, self.sig), -0.30, places=2)
        self.assertLess(kp, self.S)   # 30-delta put sits below spot
        self.assertGreater(kc, self.S)  # 30-delta call sits above spot

    def test_expiry_intrinsic(self):
        self.assertAlmostEqual(bs.put_price(90, 100, 0, self.r, self.sig), 10.0)
        self.assertAlmostEqual(bs.call_price(110, 100, 0, self.r, self.sig), 10.0)


class TestMetrics(unittest.TestCase):
    def test_simple_returns(self):
        got = metrics.simple_returns([100, 110, 99])
        self.assertAlmostEqual(got[0], 0.1, places=9)
        self.assertAlmostEqual(got[1], -0.1, places=9)

    def test_vol_and_annualize(self):
        rets = [0.01, -0.01, 0.02, -0.02, 0.015]
        v = metrics.ewma_daily_vol(rets)
        self.assertGreater(v, 0)
        self.assertAlmostEqual(metrics.annualize_vol(v), v * math.sqrt(252), places=9)

    def test_drawdown_bounds(self):
        self.assertEqual(metrics.drawdown_from_peak(90, 100), 0.10)
        self.assertEqual(metrics.drawdown_from_peak(110, 100), 0.0)  # above peak -> 0

    def test_regime_monotonic(self):
        below = metrics.regime_signal(95, 100)   # price under MA -> risk-off
        above = metrics.regime_signal(105, 100)  # price over MA -> calm
        self.assertGreater(below, above)
        self.assertEqual(above, 0.0)

    def test_beta_recovery(self):
        mkt = [0.01, -0.02, 0.015, -0.03, 0.005, -0.01, 0.02, -0.025]
        asset = [1.5 * x for x in mkt]
        self.assertAlmostEqual(metrics.ols_beta(asset, mkt), 1.5, places=6)
        self.assertAlmostEqual(metrics.downside_beta(asset, mkt), 1.5, places=6)

    def test_tail_risk_ordering(self):
        # left-skewed, fat-tailed sample -> CF VaR > normal VaR, ES >= historical VaR
        rets = [0.004, -0.002, 0.006, -0.061, 0.005, -0.047, 0.004, -0.021, 0.003, -0.009]
        eq = 100_000.0
        dv = math.sqrt(metrics.sample_variance(rets))
        skew = metrics.skewness(rets)
        kurt = metrics.excess_kurtosis(rets)
        normal = metrics.parametric_var(eq, dv, metrics.Z_95)
        cf = metrics.cornish_fisher_var(eq, dv, skew, kurt, metrics.Z_95)
        hist = metrics.historical_var(eq, rets, 0.05)
        es = metrics.expected_shortfall(eq, rets, 0.05)
        self.assertLess(skew, 0)
        self.assertGreater(cf, normal)
        self.assertGreaterEqual(es, hist)

    def test_iv_rank_bounds(self):
        self.assertAlmostEqual(metrics.iv_rank(0.25, 0.10, 0.40), 50.0, places=6)
        self.assertEqual(metrics.iv_rank(0.05, 0.10, 0.40), 0.0)   # clipped
        self.assertEqual(metrics.iv_rank(0.50, 0.10, 0.40), 100.0)  # clipped

    def test_vrp_sign(self):
        self.assertGreater(metrics.variance_risk_premium(0.30, 0.15, use_variance=False), 0)
        self.assertLess(metrics.variance_risk_premium(0.10, 0.20, use_variance=False), 0)

    def test_liquidity_and_fills(self):
        self.assertTrue(metrics.is_liquid(1.00, 1.05))
        self.assertFalse(metrics.is_liquid(0.01, 5.00))  # wide/penny -> not liquid
        self.assertEqual(metrics.paper_fill_price(1.00, 1.05, "buy"), 1.05)
        self.assertEqual(metrics.paper_fill_price(1.00, 1.05, "sell"), 1.00)


class TestScoring(unittest.TestCase):
    def test_risk_score_monotonic_and_bounded(self):
        base = scoring.risk_score(0.0, 0.10, 0.0, 0.0)
        self.assertGreaterEqual(base, 0.0)
        self.assertLessEqual(scoring.risk_score(0.2, 0.5, 1.0, 0.1), 100.0)
        self.assertLess(base, scoring.risk_score(0.10, 0.10, 0.0, 0.0))  # more DD -> higher
        self.assertLess(base, scoring.risk_score(0.0, 0.40, 0.0, 0.0))   # more vol -> higher

    def test_target_coverage_bands(self):
        self.assertEqual(scoring.target_coverage(10), 0.0)
        self.assertEqual(scoring.target_coverage(80), 1.0)
        mid = scoring.target_coverage(50)
        self.assertTrue(0.0 < mid < 1.0)

    def test_income_aggressiveness(self):
        # risk-off kills selling regardless of how rich premium is
        self.assertEqual(scoring.income_aggressiveness(0.1, regime=1.0), 0.0)
        # positive VRP (premium overpriced vs realized) + calm -> harvest
        self.assertGreater(scoring.income_aggressiveness(0.1, regime=0.0), 0.0)
        # non-positive VRP -> nothing worth selling
        self.assertEqual(scoring.income_aggressiveness(0.0, regime=0.0), 0.0)
        self.assertEqual(scoring.income_aggressiveness(-0.05, regime=0.0), 0.0)
        # richer premium -> more aggressive
        self.assertGreater(
            scoring.income_aggressiveness(0.06, 0.0),
            scoring.income_aggressiveness(0.02, 0.0),
        )


class TestPayoffs(unittest.TestCase):
    def test_spread_cheaper_than_outright(self):
        pput = payoffs.protective_put_payoff(560, 540, 8.0)
        spread = payoffs.put_spread_payoff(540, 520, 8.0, 4.0)
        self.assertLess(spread.net_debit, pput.cost)

    def test_credit_spreads_defined_risk(self):
        bps = payoffs.bull_put_spread_payoff(540, 520, 8.0, 4.0)
        bcs = payoffs.bear_call_spread_payoff(580, 600, 6.0, 3.0)
        for s in (bps, bcs):
            self.assertGreater(s.net_credit, 0)
            self.assertGreater(s.max_loss, 0)  # defined, finite
            self.assertAlmostEqual(s.max_loss, 20 * 100 - s.net_credit, places=6)

    def test_covered_call_and_csp(self):
        cc = payoffs.covered_call_payoff(560, 580, 6.0)
        self.assertGreater(cc.credit, 0)
        csp = payoffs.cash_secured_put_payoff(540, 8.0)
        self.assertEqual(csp.capital_reserved, 540 * 100)
        self.assertAlmostEqual(csp.max_loss, 540 * 100 - csp.credit, places=6)

    def test_stress_hedge_cushions(self):
        s = payoffs.stress_pnl(112_000, -0.05, 560, hedge_contracts=5, put_delta=-0.40)
        self.assertGreater(s.net_pnl, s.unhedged_pnl)  # hedge reduces the loss


class TestEngine(unittest.TestCase):
    def test_snapshot_adaptivity(self):
        calm = assess(_book(560, 480, 100_000), CALM)
        stress = assess(_book(520, 440, 106_000), STRESSED)
        self.assertLess(calm.risk_score, stress.risk_score)
        self.assertLessEqual(calm.target_coverage, stress.target_coverage)
        self.assertGreater(stress.iv_rank, calm.iv_rank)

    def test_hedge_steps_in_under_stress(self):
        calm_book, stress_book = _book(560, 480, 100_000), _book(520, 440, 106_000)
        calm = plan_hedge(calm_book, CALM, assess(calm_book, CALM))
        stress = plan_hedge(stress_book, STRESSED, assess(stress_book, STRESSED))
        self.assertEqual(calm.contracts_target, 0)
        self.assertGreater(stress.contracts_target, 0)
        self.assertEqual(stress.action, "increase")

    def test_strategy_three_regimes(self):
        calm_b = _book(560, 480, 304_000, cash=40_000)
        elev_b = _book(555, 475, 301_500, cash=40_000)
        strs_b = _book(520, 440, 310_000, cash=40_000)
        calm = plan_strategy(calm_b, CALM, assess(calm_b, CALM))
        elev = plan_strategy(elev_b, ELEVATED, assess(elev_b, ELEVATED))
        strs = plan_strategy(strs_b, STRESSED, assess(strs_b, STRESSED))

        # calm but premium is rich vs realized -> harvest, and no hedge (low risk)
        self.assertTrue(calm.income.legs)
        self.assertEqual(calm.hedge.contracts_target, 0)

        self.assertTrue(elev.income.legs)               # harvest
        self.assertGreater(elev.income.total_credit, 0)
        self.assertGreater(elev.income.net_theta_per_day, 0)  # positive carry from decay
        self.assertGreater(elev.income.total_credit, strs.income.total_credit)
        # richer IV -> more premium collected than the calm cycle
        self.assertGreater(elev.income.total_credit, calm.income.total_credit)

        self.assertGreater(strs.hedge.contracts_target, elev.hedge.contracts_target)

    def test_iron_condor_structure(self):
        b = _book(555, 475, 301_500, cash=40_000)
        plan = plan_strategy(b, ELEVATED, assess(b, ELEVATED), income_dte=7)
        condors = [l for l in plan.income.legs if l.kind == "iron_condor"]
        self.assertEqual(len(condors), 1)
        c = condors[0]
        self.assertEqual(c.expiry_days, 7)  # weekly, for fast theta
        # strikes bracket spot: put_long < put_short < spot < call_short < call_long
        self.assertLess(c.long_strike, c.short_strike)
        self.assertLess(c.short_strike, ELEVATED.index_price)
        self.assertLess(ELEVATED.index_price, c.call_short_strike)
        self.assertLess(c.call_short_strike, c.call_long_strike)
        # defined risk: capital reserved == max loss, and both exceed the credit collected
        self.assertGreater(c.max_loss, 0)
        self.assertEqual(c.capital_reserved, c.max_loss)
        self.assertGreater(c.theta_per_day, 0)  # net short -> collects decay

    def test_covered_call_capacity(self):
        b = _book(555, 475, 301_500, cash=40_000)  # SPY 300 sh -> <=3 contracts
        plan = plan_strategy(b, ELEVATED, assess(b, ELEVATED))
        for leg in plan.income.legs:
            if leg.kind == "covered_call" and leg.symbol == "SPY":
                self.assertLessEqual(leg.contracts, 3)


class TestFeed(unittest.TestCase):
    def test_builders(self):
        self.assertEqual(moving_average([1, 2, 3, 4], 2), 3.5)
        self.assertAlmostEqual(compute_beta([100, 101, 100, 99], [100, 101, 100, 99]), 1.0)

    def test_observe_roundtrip_and_state(self):
        tmp = Path(tempfile.mkdtemp(prefix="feedtest_")) / "state.json"
        src = MockDataSource()
        state = StateStore(tmp)
        portfolio, market = observe(src, state, index_symbol="SPY")

        self.assertEqual({p.symbol for p in portfolio.positions}, {"SPY", "QQQ", "AAPL"})
        betas = {p.symbol: p.beta for p in portfolio.positions}
        self.assertAlmostEqual(betas["SPY"], 1.0, places=6)     # beta to itself
        self.assertTrue(0.9 < betas["QQQ"] < 1.35)               # ~1.1 recovered
        self.assertEqual(market.index_price, src.latest_price("SPY"))
        self.assertLessEqual(market.iv_year_low, market.index_iv)
        self.assertLessEqual(market.index_iv, market.iv_year_high)

        # peak ratchets up on a new high and never falls back
        reloaded = StateStore(tmp)
        self.assertEqual(reloaded._data["peak_equity"], portfolio.peak_equity)
        richer = MockDataSource()
        richer._closes["SPY"] = [c + 20 for c in richer._closes["SPY"]]
        p2, _ = observe(richer, reloaded, index_symbol="SPY")
        self.assertGreaterEqual(p2.peak_equity, portfolio.peak_equity)

    def test_iv_range_seeds_then_uses_history(self):
        tmp = Path(tempfile.mkdtemp(prefix="ivtest_")) / "s.json"
        state = StateStore(tmp)
        r0 = state.iv_range()
        self.assertTrue(r0.seeded)  # no history yet -> default range
        for i in range(25):
            state.record_iv(0.15 + 0.001 * i, day=f"2026-01-{i+1:02d}")
        r1 = state.iv_range()
        self.assertFalse(r1.seeded)  # enough samples -> observed range
        self.assertLess(r1.low, r1.high)


class TestContract(unittest.TestCase):
    """The JSON contract + risk-cap bouncer the harness relies on."""

    def _elev_plan(self):
        b = _book(555, 475, 301_500, cash=40_000)
        return b, plan_strategy(b, ELEVATED, assess(b, ELEVATED))

    def test_to_dict_is_json_serializable(self):
        b, plan = self._elev_plan()
        snap = assess(b, ELEVATED)
        for obj in (snap.to_dict(), plan.to_dict(), plan.income.to_dict(), plan.hedge.to_dict()):
            json.dumps(obj)  # raises if any value isn't JSON-serializable
        d = plan.to_dict()
        self.assertIn("posture", d)
        self.assertIn("legs", d["income"])

    def test_validate_daily_halt(self):
        b, plan = self._elev_plan()
        v = validate_plan(plan, b.equity, day_pnl_pct=-0.06)  # -6% day
        self.assertFalse(v.ok)
        self.assertFalse(v.plan.income.legs)  # new premium halted
        self.assertTrue(any("halt" in x.lower() for x in v.violations))

    def test_validate_clamps_oversized_risk(self):
        _, plan = self._elev_plan()
        tiny_equity = 30_000.0  # forces per-underlying + total caps to bite
        v = validate_plan(plan, tiny_equity, day_pnl_pct=0.0)
        self.assertFalse(v.ok)
        # after clamping, total option risk must fit under the total cap
        total_risk = sum(l.max_loss for l in v.plan.income.legs) + v.plan.hedge.total_cost
        self.assertLessEqual(total_risk, 0.30 * tiny_equity + 1.0)

    def test_validate_passes_clean_when_within_caps(self):
        b, plan = self._elev_plan()
        v = validate_plan(plan, b.equity, day_pnl_pct=0.0)
        self.assertTrue(v.ok, msg=f"unexpected violations: {v.violations}")

    def test_get_strategy_context_roundtrip(self):
        tmp = Path(tempfile.mkdtemp(prefix="ctxtest_")) / "s.json"
        ctx = get_strategy_context(MockDataSource(), StateStore(tmp))
        json.dumps(ctx)  # the whole context must be JSON-serializable
        for key in ("portfolio", "market", "snapshot", "plan", "validation"):
            self.assertIn(key, ctx)
        self.assertIn("posture", ctx["plan"])
        self.assertIsInstance(ctx["validation"]["ok"], bool)


if __name__ == "__main__":
    unittest.main(verbosity=2)
