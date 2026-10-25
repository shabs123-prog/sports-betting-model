"""Prints every bookmaker name The Odds API returns for the 'au' region,
across every configured sport that currently has events. Use this to check
your config.USER_BOOKMAKERS list still matches the API's naming, or to see
which of your apps are (not) covered.

    python -m scripts.list_bookmakers
"""
from data import odds_api
from config import SPORT_CONFIG

if __name__ == "__main__":
    seen = {}
    for sport, cfg in SPORT_CONFIG.items():
        try:
            events = odds_api.fetch_odds(cfg["odds_key"], markets="h2h")
        except odds_api.OddsApiError as e:
            print(f"[{sport}] skipped: {e}")
            continue
        for event in events:
            for bm in event.get("bookmakers", []):
                name = bm.get("title", bm.get("key"))
                seen.setdefault(name, set()).add(sport)

    print("\nBookmakers seen (au region):")
    for name, sports in sorted(seen.items()):
        print(f"  {name:20} seen in: {sorted(sports)}")
