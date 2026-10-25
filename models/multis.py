"""Combine several independent single-leg value picks into a multi (parlay).

Two hard rules, both from direct feedback on an earlier version that got
this wrong:

1. Every leg must be at the SAME bookmaker. A multi is one bet slip placed
   at one app -- combining the best-priced leg from Sportsbet with another
   from Betfair isn't a bet you can actually place. So this groups
   candidate legs by bookmaker first, and only combines within a group.
2. Combined odds stay modest: each leg capped at MAX_LEG_ODDS, and the
   combined price itself capped at MAX_COMBINED_ODDS. An earlier version
   let three longshots compound into 400-to-1 combined odds at a ~4% hit
   chance -- technically +EV, but a lottery ticket, not a sensible multi.

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
even though EV stays positive -- report both, don't just lead with the odds.
"""
from __future__ import annotations

from itertools import combinations

MAX_CANDIDATES = 8  # cap combinatorial blow-up per bookmaker group
MAX_RESULTS_PER_SIZE = 5
MAX_LEG_ODDS = 2.2  # matches the single-bet ceiling -- no longshot legs
MAX_COMBINED_ODDS = 4.0  # hard cap on the combined price, typically ~2x


def _legs_by_bookmaker(legs: list[dict]) -> dict[str, list[dict]]:
    """
    {bookmaker: [one best-edge leg per game, odds <= MAX_LEG_ODDS]} --
    grouping by bookmaker first is what makes every resulting combo
    something you could actually place as one bet slip.
    """
    eligible = [leg for leg in legs if leg["odds_decimal"] <= MAX_LEG_ODDS]
    by_bookmaker: dict[str, dict[tuple, dict]] = {}
    for leg in eligible:
        games = by_bookmaker.setdefault(leg["bookmaker"], {})
        game_key = (leg["sport"], leg["matchup"])
        if game_key not in games or leg["edge_pct"] > games[game_key]["edge_pct"]:
            games[game_key] = leg
    return {bm: list(games.values()) for bm, games in by_bookmaker.items()}


def suggest_multis(legs: list[dict], sizes=(2, 3)) -> list[dict]:
    """
    legs: value-tip dicts with at least sport, matchup, bookmaker, selection,
    odds_decimal, market_fair_prob, edge_pct.

    Returns combos sorted by combined edge, each:
    {bookmaker, legs, size, combined_odds, combined_fair_prob,
     combined_edge_pct, implied_hit_pct}
    """
    all_results = []
    for bookmaker, candidates in _legs_by_bookmaker(legs).items():
        if len(candidates) < 2:
            continue
        candidates = sorted(candidates, key=lambda l: -l["edge_pct"])[:MAX_CANDIDATES]
        for size in sizes:
            if size > len(candidates):
                continue
            for combo in combinations(candidates, size):
                combined_odds = 1.0
                combined_prob = 1.0
                for leg in combo:
                    combined_odds *= leg["odds_decimal"]
                    combined_prob *= leg["market_fair_prob"]
                if combined_odds > MAX_COMBINED_ODDS:
                    continue
                combined_edge_pct = combined_prob * combined_odds - 1.0
                all_results.append({
                    "bookmaker": bookmaker,
                    "legs": list(combo),
                    "size": size,
                    "combined_odds": round(combined_odds, 2),
                    "combined_fair_prob": combined_prob,
                    "combined_edge_pct": combined_edge_pct,
                    "implied_hit_pct": combined_prob,
                })

    results = []
    for size in sizes:
        sized = sorted((r for r in all_results if r["size"] == size), key=lambda r: -r["combined_edge_pct"])
        results.extend(sized[:MAX_RESULTS_PER_SIZE])

    results.sort(key=lambda r: -r["combined_edge_pct"])
    return results
