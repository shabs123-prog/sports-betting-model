"""Edge/EV calculations: compare a price against a fair probability.

Two ways a tip can show "edge" here, and both get reported when present:
  - market_edge: your model's win probability vs the best price in the market
    (only as good as your model -- Elo on a new season starts weak).
  - devig_edge: the no-vig *consensus* probability (averaged across every AU
    bookmaker that prices the game) vs the single best price available. This
    doesn't need your model to be right -- it just finds the bookmaker whose
    line disagrees with the rest of the market, which is the more reliable
    signal for efficient markets like NBA/EPL moneylines.
"""

from __future__ import annotations
from models.devig import devig_book


def consensus_fair_probs(bookmaker_books: list[dict]) -> dict:
    """
    bookmaker_books: [{"bookmaker": ..., "prices": {name: decimal_odds}}, ...]
    Devigs each bookmaker's own market, then averages per outcome.
    """
    if not bookmaker_books:
        return {}
    per_outcome_sums: dict[str, float] = {}
    per_outcome_counts: dict[str, int] = {}
    for book in bookmaker_books:
        fair = devig_book(book["prices"])
        for name, prob in fair.items():
            per_outcome_sums[name] = per_outcome_sums.get(name, 0.0) + prob
            per_outcome_counts[name] = per_outcome_counts.get(name, 0) + 1
    return {name: per_outcome_sums[name] / per_outcome_counts[name] for name in per_outcome_sums}


# Real case found 2026-11-01: an EPL match where one side was priced
# ~1.00-1.03 EVERYWHERE (a near-certain favourite -- the other side was
# clearly a severely weakened lineup, not a normal competitive match) had
# bookmaker prices for the underdog/draw outcomes ranging wildly --
# Manchester United quoted anywhere from 41 to 1000+ across books, even
# between two near-simultaneous listings on the SAME exchange. Averaging
# that naively (consensus_fair_probs above) still produces A number, but
# it's really just an artifact of how much the books happen to disagree,
# not a genuine shared view -- exactly the "mispricing artifact, not real
# confidence" risk already called out for longshot edge_pct elsewhere in
# this project, just showing up from the agreement side instead of the
# probability-level side. A tight market (every book within a small
# multiple of each other) is a real consensus; a scattered one isn't,
# regardless of what the average comes out to.
MAX_RELATIVE_DISPERSION = 2.5  # highest devigged prob / lowest, among books pricing this outcome


def fair_prob_is_reliable(bookmaker_books: list[dict], outcome_name: str) -> bool:
    """
    False when the books pricing this outcome disagree too much with each
    other for their average to mean anything -- see MAX_RELATIVE_DISPERSION's
    comment. True (not blocked) when fewer than 2 books price it at all;
    can't judge agreement from a single source, and that's a separate,
    already-handled concern (fewer books just means a thinner consensus,
    not a provably bad one).
    """
    probs = []
    for book in bookmaker_books:
        fair = devig_book(book["prices"])
        if outcome_name in fair:
            probs.append(fair[outcome_name])
    if len(probs) < 2:
        return True
    lo, hi = min(probs), max(probs)
    if lo <= 0:
        return False
    return (hi / lo) <= MAX_RELATIVE_DISPERSION


def ev_pct(prob: float, decimal_odds: float) -> float:
    """Expected value per $1 staked, as a fraction. >0 means positive EV."""
    return prob * decimal_odds - 1.0


def edge_pct(fair_prob: float, offered_decimal_odds: float) -> float:
    """Difference between fair probability and the probability implied by the offered price."""
    return fair_prob - (1.0 / offered_decimal_odds)


# Tier drives both the label shown and the stake size (see
# tips/generate_tips.TIER_UNITS) -- a bigger edge gets a bigger, rounder
# unit size rather than a continuously-scaled fraction.
EDGE_TIERS = [
    (0.03, "Strong"),
    (0.015, "Moderate"),
    (0.0, "Lean"),
]


def tier_label(edge: float) -> str:
    for threshold, label in EDGE_TIERS:
        if edge >= threshold:
            return label
    return "Marginal"


# Edge tier (size of the mispricing) and risk (win-probability band) answer
# different questions -- how good is the price vs. how likely is it to land
# -- and showing them as two separate badges left people mentally combining
# them anyway. This is that combination, done once and shown as a single
# label. Edge is the dominant axis (it's the actual +EV signal; risk is a
# secondary variance flag) -- same tier always reads as "better" than a
# lower tier regardless of risk, and risk only distinguishes within a tier.
COMBINED_RATING = {
    ("Strong", "Not Risky"): "Elite",
    ("Strong", "Neutral"): "Strong Value",
    ("Strong", "Risky"): "High-Upside Swing",
    ("Moderate", "Not Risky"): "Safe Value",
    ("Moderate", "Neutral"): "Good Value",
    ("Moderate", "Risky"): "Speculative Value",
    ("Lean", "Not Risky"): "Safe Lean",
    ("Lean", "Neutral"): "Lean Value",
    ("Lean", "Risky"): "Longshot Lean",
}


def combined_rating(tier: str, risk: str) -> str:
    return COMBINED_RATING.get((tier, risk), f"{tier} / {risk}")
