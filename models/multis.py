"""Combine several single-leg value picks into a multi (parlay) or SGM
(same-game multi).

Two hard rules on both, from direct feedback on an earlier version that got
this wrong:

1. Every leg must be at the SAME bookmaker. A multi is one bet slip placed
   at one app -- combining the best-priced leg from Sportsbet with another
   from Betfair isn't a bet you can actually place. So this groups
   candidate legs by bookmaker first, and only combines within a group.
2. Combined odds stay modest: each leg capped at MAX_LEG_ODDS, and the
   combined price itself capped at MAX_COMBINED_ODDS (same ceiling as a
   single bet). An earlier version let three longshots compound into
   400-to-1 combined odds at a ~4% hit chance -- technically +EV, but a
   lottery ticket, not a sensible multi.

`suggest_multis` combines legs from DIFFERENT games -- multiplying
independent probabilities together is mathematically correct there.

`suggest_sgms` combines legs from DIFFERENT MARKETS within the SAME game
(e.g. a Win pick + a Line pick on the same match). This is explicitly an
approximation: those legs are correlated (a team winning and the game going
under both lean the same way), so multiplying their probabilities as if
independent overstates or understates the true combined chance -- getting
it exactly right needs real bookmaker-priced SGM odds (checked: not
available through this data source) or a correlation model (a separate,
nontrivial build). Shipped anyway on the basis that an approximate "this is
the combo I'd lean towards" is still useful as a tip, as long as it's
labelled as a rough estimate rather than a verified edge.
"""
from __future__ import annotations

from itertools import combinations

MAX_CANDIDATES = 8  # cap combinatorial blow-up per bookmaker group
MAX_RESULTS_PER_SIZE = 5
MAX_LEG_ODDS = 2.2  # matches the single-bet ceiling -- no longshot legs
MAX_COMBINED_ODDS = 2.2  # the COMBINED price, same ceiling as a single bet


def _combo_result(bookmaker: str, combo: tuple[dict, ...], approximate: bool) -> dict | None:
    combined_odds = 1.0
    combined_prob = 1.0
    for leg in combo:
        combined_odds *= leg["odds_decimal"]
        combined_prob *= leg["market_fair_prob"]
    if combined_odds > MAX_COMBINED_ODDS:
        return None
    return {
        "bookmaker": bookmaker,
        "legs": list(combo),
        "size": len(combo),
        "combined_odds": round(combined_odds, 2),
        "combined_fair_prob": combined_prob,
        "combined_edge_pct": combined_prob * combined_odds - 1.0,
        "implied_hit_pct": combined_prob,
        "approximate": approximate,
    }


def _top_per_size(results: list[dict], sizes) -> list[dict]:
    out = []
    for size in sizes:
        sized = sorted((r for r in results if r["size"] == size), key=lambda r: -r["combined_edge_pct"])
        out.extend(sized[:MAX_RESULTS_PER_SIZE])
    out.sort(key=lambda r: -r["combined_edge_pct"])
    return out


def suggest_multis(legs: list[dict], sizes=(2, 3)) -> list[dict]:
    """
    Cross-game multis: legs: value-tip dicts with at least sport, matchup,
    bookmaker, selection, odds_decimal, market_fair_prob, edge_pct.

    Returns combos sorted by combined edge, each:
    {bookmaker, legs, size, combined_odds, combined_fair_prob,
     combined_edge_pct, implied_hit_pct, approximate=False}
    """
    # Needs real edge_pct/market_fair_prob -- these are value-tip-shaped legs
    # specifically (prediction tips have neither, and shouldn't be combined
    # in here: mixing a verified price discrepancy with a model opinion into
    # one "combined edge" number would blur which part is actually verified).
    eligible = [
        leg for leg in legs
        if leg.get("edge_pct") is not None and leg.get("market_fair_prob") is not None
        and leg["odds_decimal"] <= MAX_LEG_ODDS
    ]
    by_bookmaker: dict[str, dict[tuple, dict]] = {}
    for leg in eligible:
        games = by_bookmaker.setdefault(leg["bookmaker"], {})
        game_key = (leg["sport"], leg["matchup"])
        if game_key not in games or leg["edge_pct"] > games[game_key]["edge_pct"]:
            games[game_key] = leg

    results = []
    for bookmaker, games in by_bookmaker.items():
        candidates = sorted(games.values(), key=lambda l: -l["edge_pct"])[:MAX_CANDIDATES]
        for size in sizes:
            if size > len(candidates):
                continue
            for combo in combinations(candidates, size):
                r = _combo_result(bookmaker, combo, approximate=False)
                if r:
                    results.append(r)
    return _top_per_size(results, sizes)


def suggest_sgms(legs: list[dict], sizes=(2, 3)) -> list[dict]:
    """
    Same-game multis: combines legs from different MARKETS (Win/Handicap/
    Line) within the same game, at the same bookmaker -- at most one leg per
    market (can't combine both sides of the same market). See module
    docstring: this is an approximation, correlation isn't modeled.

    Returns combos in the same shape as suggest_multis, with approximate=True.
    """
    eligible = [
        leg for leg in legs
        if leg.get("edge_pct") is not None and leg.get("market_fair_prob") is not None
        and leg["odds_decimal"] <= MAX_LEG_ODDS
    ]
    by_bookmaker: dict[str, dict[tuple, dict[str, dict]]] = {}
    for leg in eligible:
        bm_games = by_bookmaker.setdefault(leg["bookmaker"], {})
        game_key = (leg["sport"], leg["matchup"])
        markets = bm_games.setdefault(game_key, {})
        market = leg["market"]
        if market not in markets or leg["edge_pct"] > markets[market]["edge_pct"]:
            markets[market] = leg

    results = []
    for bookmaker, games in by_bookmaker.items():
        for markets in games.values():
            candidates = list(markets.values())
            for size in sizes:
                if size > len(candidates):
                    continue
                for combo in combinations(candidates, size):
                    r = _combo_result(bookmaker, combo, approximate=True)
                    if r:
                        results.append(r)
    return _top_per_size(results, sizes)
