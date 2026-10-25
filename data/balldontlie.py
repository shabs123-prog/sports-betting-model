"""Client for the balldontlie NBA API (https://balldontlie.io).

Free tier covers teams/games/players, which is all the Elo model needs
(final scores + dates). Requires a free API key.
"""

from __future__ import annotations
import time
from datetime import date, timedelta

import requests

from config import BALLDONTLIE_API_KEY, BALLDONTLIE_BASE

# Free tier is rate-limited (observed 429s mid-pagination) -- be a polite
# client: small delay between pages, and back off on 429 using Retry-After
# if the API sends one, otherwise exponential backoff.
REQUEST_DELAY_SECONDS = 1.0
MAX_RETRIES = 5


class BallDontLieError(RuntimeError):
    pass


def _headers():
    if not BALLDONTLIE_API_KEY:
        raise BallDontLieError(
            "BALLDONTLIE_API_KEY is not set. Get a free key at https://balldontlie.io, "
            "then put it in .env"
        )
    return {"Authorization": BALLDONTLIE_API_KEY}


def _get_with_retry(url: str, params: dict) -> dict:
    for attempt in range(MAX_RETRIES):
        resp = requests.get(url, headers=_headers(), params=params, timeout=15)
        if resp.status_code == 429:
            wait = float(resp.headers.get("Retry-After", 2 ** attempt * 2))
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()
    raise BallDontLieError(f"Gave up after {MAX_RETRIES} retries (still rate-limited): {url}")


def get_teams() -> list[dict]:
    return _get_with_retry(f"{BALLDONTLIE_BASE}/teams", {})["data"]


def get_games(start_date: str, end_date: str) -> list[dict]:
    """All games (any status) between two ISO dates, paginated."""
    games = []
    cursor = None
    while True:
        params = {
            "start_date": start_date,
            "end_date": end_date,
            "per_page": 100,
        }
        if cursor:
            params["cursor"] = cursor
        payload = _get_with_retry(f"{BALLDONTLIE_BASE}/games", params)
        games.extend(payload["data"])
        cursor = payload.get("meta", {}).get("next_cursor")
        if not cursor:
            break
        time.sleep(REQUEST_DELAY_SECONDS)
    return games


def get_finished_games_for_training(seasons_back: int = 1) -> list[dict]:
    """Pull finished games since last season to seed Elo ratings."""
    today = date.today()
    start = date(today.year - seasons_back, 7, 1)  # NBA season starts ~Oct, pad back to July
    games = get_games(start.isoformat(), today.isoformat())
    return [g for g in games if g.get("status") == "Final"]


def get_upcoming_games(days_ahead: int = 1) -> list[dict]:
    today = date.today()
    end = today + timedelta(days=days_ahead)
    games = get_games(today.isoformat(), end.isoformat())
    return [g for g in games if g.get("status") != "Final"]
