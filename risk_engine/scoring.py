"""The Risk Score (0-100) and its mapping to hedge coverage (README §7.12).

The score blends the stress signals into one dial; the coverage curve turns that dial
into "how much of the book to protect." These weights/bands are the tunable knobs the
team calibrates on the dev account — deliberately simple and transparent for the MVP.
"""
from __future__ import annotations

from .metrics import clip

# How much each signal contributes to the 0-100 score.
DEFAULT_WEIGHTS = {
    "drawdown": 0.30,
    "vol": 0.30,
    "regime": 0.20,
    "var": 0.20,
}

# Normalization anchors: the value at which each signal is considered "maxed" (=100).
DRAWDOWN_MAX = 0.10   # 10% drawdown -> full stress on this axis
VOL_LOW, VOL_HIGH = 0.10, 0.40  # annual vol band: 10% calm .. 40% max
VAR_PCT_MAX = 0.03    # 3% of equity daily VaR -> maxed

# Coverage bands: below COVER_MIN score -> 0%; ramps to 100% by COVER_FULL.
COVER_MIN, COVER_FULL = 25.0, 75.0


def risk_score(
    drawdown: float,
    annual_vol: float,
    regime: float,
    var_pct: float,
    weights: dict[str, float] | None = None,
) -> float:
    """Blend the four stress signals into a single 0-100 score."""
    w = weights or DEFAULT_WEIGHTS
    dd = clip(drawdown / DRAWDOWN_MAX, 0.0, 1.0) * 100.0
    vol = clip((annual_vol - VOL_LOW) / (VOL_HIGH - VOL_LOW), 0.0, 1.0) * 100.0
    reg = clip(regime, 0.0, 1.0) * 100.0
    var = clip(var_pct / VAR_PCT_MAX, 0.0, 1.0) * 100.0
    score = w["drawdown"] * dd + w["vol"] * vol + w["regime"] * reg + w["var"] * var
    return clip(score, 0.0, 100.0)


def target_coverage(score: float) -> float:
    """Map the Risk Score to a target hedge coverage fraction (0..1).

    score < 25 -> 0% (calm, sit unhedged, zero drag)
    25 .. 75   -> ramp linearly
    score > 75 -> 100% (full protection)
    """
    if score < COVER_MIN:
        return 0.0
    return clip((score - COVER_MIN) / (COVER_FULL - COVER_MIN), 0.0, 1.0)


# ---------------------------------------------------------------------------
# Income posture — the *inverse* dial to the hedge (README §3 profit engine).
# Sell premium when it is genuinely overpriced vs. what the market actually delivers
# (positive variance risk premium), damped to zero as the regime turns risk-off (you
# don't want to be short premium into a crash).
#
# Gated on the VRP (implied − realized vol), NOT IV Rank. IV Rank compares IV to its
# own past year, which needs a year of history and, from a seeded range, mis-fires at
# yearly-low IV — declining to sell even when premium is genuinely rich vs. reality.
# The VRP is the honest, history-free "is premium overpriced right now" signal, and it
# fires correctly inside a short (~5-day) window where IV-Rank history never accrues.
# ---------------------------------------------------------------------------

VRP_FLOOR, VRP_CEIL = 0.0, 0.08  # vol-point band over which selling ramps 0 -> full


def income_aggressiveness(vrp: float, regime: float) -> float:
    """How hard to harvest premium this cycle, 0..1 (§7.7).

    Rises with the variance risk premium (implied richer than realized — premium is
    overpriced vs. reality, so selling pays) and is damped to zero as the regime turns
    risk-off. The mirror image of `target_coverage`: rich+calm -> harvest; stress ->
    stand down and let the hedge take over. Non-positive VRP -> nothing worth selling.
    """
    richness = clip((vrp - VRP_FLOOR) / (VRP_CEIL - VRP_FLOOR), 0.0, 1.0)
    regime_damp = clip(1.0 - regime, 0.0, 1.0)
    return clip(richness * regime_damp, 0.0, 1.0)
