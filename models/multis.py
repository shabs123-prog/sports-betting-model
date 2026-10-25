"""Combine several independent single-leg value picks into a multi (parlay).

Only combines legs from DIFFERENT games -- that's what keeps the maths
honest. Combining markets WITHIN one game (a same-game multi) means the legs
are correlated (e.g. "Team A wins" and "Over 220.5" aren't independent), and
treating them as independent would overstate or understate the true combined
probability depending on which way the correlation runs. That needs either
real SGM prices from the bookmaker (checked: not available through this data
source) or a correlation model (a separate, nontrivial build) -- so this
deliberately only builds cross-game multis, where multiplying is correct.

If every leg individually has positive EV, the combination does too:
EV_multi = (p1*odds1) * (p2*odds2) * ... - 1, and since each factor is > 1
on its own, the product is > 1. But win probability drops multiplicatively
even though EV stays positive -- a 3-leg multi of ~55% shots only lands
~17% of the time. Report both, don't just lead with the odds.
"""
from __future__ import annotations

from itertools import combinations

MAX_CANDIDATES = 8  # cap combinatorial blow-up -- only combine the best legs
MAX_RESULTS_PER_SIZE = 5
# Legs longer than this aren't eligible for a multi -- stacking several
# longshots compounds into absurd combined odds (a few hundred-to-one) with
# a single-digit % chance of landing, which is a lottery ticket dressed up
# as a parlay, not the "smooth, simple to follow" bet this is meant to be.
MAX_LEG_ODDS = 4.0


def _one_leg_per_game(legs: list[dict]) -> list[dict]:
    """Keep the single best-edge leg per game (within the odds cap) -- never both sides of the same match."""
    eligible = [leg for leg in legs if leg["odds_decimal"] <= MAX_LEG_ODDS]
    best_per_game: dict[tuple, dict] = {}
    for leg in eligible:
        key = (leg["sport"], leg["matchup"])
        if key not in best_per_game or leg["edge_pct"] > best_per_game[key]["edge_pct"]:
            best_per_game[key] = leg
    return list(best_per_game.values())


def suggest_multis(legs: list[dict], sizes=(2, 3)) -> list[dict]:
    """
    legs: value-tip dicts with at least sport, matchup, selection, bookmaker,
    odds_decimal, market_fair_prob, edge_pct. Legs priced beyond
    MAX_LEG_ODDS are excluded -- see that constant for why.

    Returns combos sorted by combined edge, each:
    {legs, combined_odds, combined_fair_prob, combined_edge_pct, implied_hit_pct}
    """
    candidates = _one_leg_per_game(legs)
    candidates.sort(key=lambda l: -l["edge_pct"])
    candidates = candidates[:MAX_CANDIDATES]

    results = []
    for size in sizes:
        if size > len(candidates):
            continue
        sized_results = []
        for combo in combinations(candidates, size):
            combined_odds = 1.0
            combined_prob = 1.0
            for leg in combo:
                combined_odds *= leg["odds_decimal"]
                combined_prob *= leg["market_fair_prob"]
            combined_edge_pct = combined_prob * combined_odds - 1.0
            sized_results.append({
                "legs": list(combo),
                "size": size,
                "combined_odds": round(combined_odds, 2),
                "combined_fair_prob": combined_prob,
                "combined_edge_pct": combined_edge_pct,
                "implied_hit_pct": combined_prob,
            })
        sized_results.sort(key=lambda r: -r["combined_edge_pct"])
        results.extend(sized_results[:MAX_RESULTS_PER_SIZE])

    results.sort(key=lambda r: -r["combined_edge_pct"])
    return results
