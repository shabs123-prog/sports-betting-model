"""Client for The Odds API (https://the-odds-api.com).

Free tier: 500 requests/month. Each call below costs 1 request per market
requested, multiplied by number of regions -- keep markets/regions tight.
"""

from __future__ import annotations
import requests

from config import ODDS_API_BASE, ODDS_API_KEY, ODDS_API_REGION


class OddsApiError(RuntimeError):
    pass


def fetch_odds(sport_key: str, markets: str = "h2h", region: str = ODDS_API_REGION) -> list[dict]:
    if not ODDS_API_KEY:
        raise OddsApiError(
            "ODDS_API_KEY is not set. Sign up at https://the-odds-api.com, "
            "then put your key in .env"
        )
    url = f"{ODDS_API_BASE}/sports/{sport_key}/odds"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": region,
        "markets": markets,
        "oddsFormat": "decimal",
        "dateFormat": "iso",
    }
    resp = requests.get(url, params=params, timeout=15)
    if resp.status_code != 200:
        raise OddsApiError(f"Odds API error {resp.status_code}: {resp.text[:300]}")
    remaining = resp.headers.get("x-requests-remaining")
    if remaining is not None:
        print(f"[odds_api] requests remaining this period: {remaining}")
    return resp.json()


def consensus_points(event: dict, market_key: str) -> dict:
    """
    {outcome_name: most_common_point} across every bookmaker, for markets that
    carry a handicap/total line ("spreads", "totals"). h2h outcomes have no
    point (always None), so this degenerates to a trivial no-op there.

    Point matters because -4.5 and -6.5 are different bets -- devigging a
    -4.5 price against a -6.5 price as if they were the same market would be
    meaningless. We only compare prices at whichever line most bookmakers
    agree on.
    """
    from collections import Counter

    points_by_outcome: dict[str, list] = {}
    for bm in event.get("bookmakers", []):
        for market in bm.get("markets", []):
            if market.get("key") != market_key:
                continue
            for outcome in market.get("outcomes", []):
                points_by_outcome.setdefault(outcome["name"], []).append(outcome.get("point"))
    return {
        name: Counter(points).most_common(1)[0][0]
        for name, points in points_by_outcome.items()
        if points
    }


def best_price_per_outcome(event: dict, market_key: str = "h2h", outcome_points: dict | None = None) -> dict:
    """
    Given one event from fetch_odds(), return:
    {outcome_name: {"price": best_decimal_price, "bookmaker": title, "point": point}}
    scanning across every bookmaker returned for that region. If outcome_points
    is given, only prices quoted at that exact point are considered.
    """
    best: dict[str, dict] = {}
    for bm in event.get("bookmakers", []):
        for market in bm.get("markets", []):
            if market.get("key") != market_key:
                continue
            for outcome in market.get("outcomes", []):
                name = outcome["name"]
                point = outcome.get("point")
                if outcome_points is not None and outcome_points.get(name) != point:
                    continue
                price = outcome["price"]
                if name not in best or price > best[name]["price"]:
                    best[name] = {"price": price, "bookmaker": bm.get("title", bm.get("key")), "point": point}
    return best


def bookmaker_books(event: dict, market_key: str = "h2h", outcome_points: dict | None = None) -> list[dict]:
    """
    One entry per bookmaker that priced this market:
    [{"bookmaker": title, "prices": {outcome_name: decimal_price, ...}}, ...]
    Used for devigging (need one bookmaker's full, self-consistent market at a
    time). If outcome_points is given, only includes a bookmaker's quote for
    an outcome when it matches that exact line.
    """
    books = []
    for bm in event.get("bookmakers", []):
        for market in bm.get("markets", []):
            if market.get("key") != market_key:
                continue
            prices = {}
            for o in market.get("outcomes", []):
                if outcome_points is not None and outcome_points.get(o["name"]) != o.get("point"):
                    continue
                prices[o["name"]] = o["price"]
            if len(prices) >= 2:
                books.append({"bookmaker": bm.get("title", bm.get("key")), "prices": prices})
    return books
