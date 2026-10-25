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


def ev_pct(prob: float, decimal_odds: float) -> float:
    """Expected value per $1 staked, as a fraction. >0 means positive EV."""
    return prob * decimal_odds - 1.0


def edge_pct(fair_prob: float, offered_decimal_odds: float) -> float:
    """Difference between fair probability and the probability implied by the offered price."""
    return fair_prob - (1.0 / offered_decimal_odds)


# Tiering for display only -- stake size already does the real work of
# treating a small edge as a small bet (via Kelly). This just labels what
# the number means so a 0.6% edge doesn't read the same as a 4% one.
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
