"""Client for The Odds API (https://the-odds-api.com).

Free tier: 500 requests/month. Each call below costs 1 request per market
requested, multiplied by number of regions -- keep markets/regions tight.

Supports more than one API key (config.ODDS_API_KEYS) -- once a key's
monthly quota is exhausted, falls over to the next one automatically.
Confirmed live (2026-10-27) that an exhausted key returns error_code
"OUT_OF_USAGE_CREDITS" -- their own docs say that's a 429, but it actually
came back as a 401 in testing, so this checks the error_code in the
response body rather than trusting a specific status code. Any other
error (bad key, missing key, rate limit) raises immediately instead of
silently trying the next key, so a real problem never gets masked as
"just try key 2."
"""

from __future__ import annotations
import requests

from config import ODDS_API_BASE, ODDS_API_KEYS, ODDS_API_REGION


class OddsApiError(RuntimeError):
    pass


_QUOTA_EXHAUSTED_CODE = "OUT_OF_USAGE_CREDITS"


def _request(url: str, params: dict):
    if not ODDS_API_KEYS:
        raise OddsApiError(
            "No Odds API key set. Sign up at https://the-odds-api.com, "
            "then put your key in .env as ODDS_API_KEY"
        )
    last_error = None
    for i, key in enumerate(ODDS_API_KEYS):
        resp = requests.get(url, params={**params, "apiKey": key}, timeout=15)
        if resp.status_code == 200:
            remaining = resp.headers.get("x-requests-remaining")
            if remaining is not None:
                print(f"[odds_api] key {i + 1}/{len(ODDS_API_KEYS)}: requests remaining this period: {remaining}")
            return resp.json()

        try:
            error_code = resp.json().get("error_code")
        except ValueError:
            error_code = None
        last_error = OddsApiError(f"Odds API error {resp.status_code}: {resp.text[:300]}")

        if error_code == _QUOTA_EXHAUSTED_CODE and i + 1 < len(ODDS_API_KEYS):
            print(f"[odds_api] key {i + 1}/{len(ODDS_API_KEYS)} out of usage credits, trying next key...")
            continue
        raise last_error
    raise last_error


def fetch_events(sport_key: str) -> list[dict]:
    """
    Lightweight event list (id, teams, commence_time) with no odds attached
    -- free, doesn't cost quota. Used to find event ids for markets that
    only exist per-event (e.g. btts), which the bulk /odds endpoint 422s on.
    """
    url = f"{ODDS_API_BASE}/sports/{sport_key}/events"
    return _request(url, {})


def fetch_event_odds(sport_key: str, event_id: str, markets: str, region: str = "au") -> dict:
    """
    Per-event odds for a market not on the bulk endpoint (e.g. btts, player
    props) -- costs quota per event call (markets x regions, same as
    fetch_odds, just once per game instead of once per sport), so use
    sparingly. Defaults to 'au' only (not ODDS_API_REGION's 'au,eu') to
    roughly halve that per-event cost -- these markets don't need Pinnacle
    as a sharp reference as much as h2h/spreads/totals do.
    """
    url = f"{ODDS_API_BASE}/sports/{sport_key}/events/{event_id}/odds"
    params = {
        "regions": region,
        "markets": markets,
        "oddsFormat": "decimal",
        "dateFormat": "iso",
    }
    return _request(url, params)


def fetch_odds(sport_key: str, markets: str = "h2h", region: str = ODDS_API_REGION) -> list[dict]:
    url = f"{ODDS_API_BASE}/sports/{sport_key}/odds"
    params = {
        "regions": region,
        "markets": markets,
        "oddsFormat": "decimal",
        "dateFormat": "iso",
    }
    return _request(url, params)


def consensus_points(event: dict, market_key: str, allowed_bookmakers: set | None = None) -> dict:
    """
    {outcome_name: most_common_point} across bookmakers, for markets that
    carry a handicap/total line ("spreads", "totals"). h2h outcomes have no
    point (always None), so this degenerates to a trivial no-op there.

    Point matters because -4.5 and -6.5 are different bets -- devigging a
    -4.5 price against a -6.5 price as if they were the same market would be
    meaningless. We only compare prices at whichever line most bookmakers
    agree on. Pass allowed_bookmakers to keep this to AU books specifically,
    so a sharp reference book quoting a different point doesn't skew it.
    """
    from collections import Counter

    points_by_outcome: dict[str, list] = {}
    for bm in event.get("bookmakers", []):
        name = bm.get("title", bm.get("key"))
        if allowed_bookmakers is not None and name not in allowed_bookmakers:
            continue
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


def best_price_per_outcome(
    event: dict,
    market_key: str = "h2h",
    outcome_points: dict | None = None,
    allowed_bookmakers: set | None = None,
) -> dict:
    """
    Given one event from fetch_odds(), return:
    {outcome_name: {"price": best_decimal_price, "bookmaker": title, "point": point}}
    scanning across every bookmaker returned for that region. If outcome_points
    is given, only prices quoted at that exact point are considered. If
    allowed_bookmakers is given, only those bookmakers are considered -- use
    this to restrict "best price" to accounts you actually hold, so every tip
    is one you can actually place.
    """
    best: dict[str, dict] = {}
    for bm in event.get("bookmakers", []):
        name = bm.get("title", bm.get("key"))
        if allowed_bookmakers is not None and name not in allowed_bookmakers:
            continue
        for market in bm.get("markets", []):
            if market.get("key") != market_key:
                continue
            for outcome in market.get("outcomes", []):
                oname = outcome["name"]
                point = outcome.get("point")
                if outcome_points is not None and outcome_points.get(oname) != point:
                    continue
                price = outcome["price"]
                if oname not in best or price > best[oname]["price"]:
                    best[oname] = {"price": price, "bookmaker": name, "point": point}
    return best


def bookmaker_books(
    event: dict,
    market_key: str = "h2h",
    outcome_points: dict | None = None,
    allowed_bookmakers: set | None = None,
) -> list[dict]:
    """
    One entry per bookmaker that priced this market:
    [{"bookmaker": title, "prices": {outcome_name: decimal_price, ...}}, ...]
    Used for devigging (need one bookmaker's full, self-consistent market at a
    time). If outcome_points is given, only includes a bookmaker's quote for
    an outcome when it matches that exact line. If allowed_bookmakers is
    given, only those bookmakers are considered.
    """
    books = []
    for bm in event.get("bookmakers", []):
        name = bm.get("title", bm.get("key"))
        if allowed_bookmakers is not None and name not in allowed_bookmakers:
            continue
        for market in bm.get("markets", []):
            if market.get("key") != market_key:
                continue
            prices = {}
            for o in market.get("outcomes", []):
                if outcome_points is not None and outcome_points.get(o["name"]) != o.get("point"):
                    continue
                prices[o["name"]] = o["price"]
            if len(prices) >= 2:
                books.append({"bookmaker": name, "prices": prices})
    return books


def single_bookmaker_prices(
    event: dict, market_key: str, bookmaker_name: str, outcome_points: dict | None = None
) -> dict:
    """
    One named bookmaker's prices for this market: {outcome_name: decimal_price}.
    Used to pull a sharp reference book's (e.g. Pinnacle) own line rather than
    an average -- their price alone, lightly devigged, is a stronger fair-
    probability estimate than averaging a cluster of recreational AU books
    that mostly just copy each other.
    """
    for bm in event.get("bookmakers", []):
        if bm.get("title", bm.get("key")) != bookmaker_name:
            continue
        for market in bm.get("markets", []):
            if market.get("key") != market_key:
                continue
            prices = {}
            for o in market.get("outcomes", []):
                if outcome_points is not None and outcome_points.get(o["name"]) != o.get("point"):
                    continue
                prices[o["name"]] = o["price"]
            return prices
    return {}
