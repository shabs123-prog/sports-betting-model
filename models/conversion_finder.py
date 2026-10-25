"""Scans live odds to find the best matched-betting (bonus conversion)
opportunity for a given bookmaker: the game/outcome where that bookmaker's
back price and Betfair Exchange's lay price are closest together, which
maximizes how much of the bonus you keep as cash.

Needs both 'h2h' (back prices) and 'h2h_lay' (Betfair's lay side) markets in
the same API call -- Betfair Exchange is the only AU bookmaker on The Odds
API that posts a lay price at all.
"""
from __future__ import annotations

from data import odds_api
from models import matched_betting

EXCHANGE_BOOKMAKER = "Betfair"


def find_opportunities(
    sport_configs: dict,
    back_bookmaker: str,
    stake: float,
    is_free_bet: bool,
    commission: float = 0.05,
    top_n: int = 10,
    log=print,
) -> list[dict]:
    fn = matched_betting.free_bet if is_free_bet else matched_betting.qualifying_bet
    opportunities = []

    for sport, cfg in sport_configs.items():
        try:
            events = odds_api.fetch_odds(cfg["odds_key"], markets="h2h,h2h_lay")
        except odds_api.OddsApiError as e:
            log(f"[{sport}] skipped: {e}")
            continue

        for event in events:
            back_prices, lay_prices = {}, {}
            for bm in event.get("bookmakers", []):
                name = bm.get("title", bm.get("key"))
                for market in bm.get("markets", []):
                    if name == back_bookmaker and market.get("key") == "h2h":
                        for o in market.get("outcomes", []):
                            back_prices[o["name"]] = o["price"]
                    if name == EXCHANGE_BOOKMAKER and market.get("key") == "h2h_lay":
                        for o in market.get("outcomes", []):
                            lay_prices[o["name"]] = o["price"]

            for outcome_name, back_odds in back_prices.items():
                lay_odds = lay_prices.get(outcome_name)
                if lay_odds is None or lay_odds <= commission:
                    continue
                result = fn(stake, back_odds, lay_odds, commission)
                opportunities.append({
                    "sport": sport,
                    "matchup": f"{event['away_team']} @ {event['home_team']}",
                    "selection": outcome_name,
                    "back_odds": back_odds,
                    "lay_odds": lay_odds,
                    **result,
                })

    opportunities.sort(key=lambda o: -o["retention_pct"])
    return opportunities[:top_n]
