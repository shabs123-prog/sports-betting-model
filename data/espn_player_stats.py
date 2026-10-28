"""Per-player season scoring average from ESPN's site API -- used to
estimate how much a team's own projected scoring should actually drop
when a specific named player is confirmed OUT (models/scoring.py), rather
than having the scoring-margin projection stay completely blind to
injuries it has already fetched for the card's own reasoning text.

Added 2026-10-29 after a genuinely bad day: Sacramento Kings were missing
four rotation players (including Ben Simmons) against a Spurs team
missing one, and the scoring projection -- built from season-long team
averages -- had no way to know that and backed Kings +18.5 for 1.75
units. They lost by 27.

Same house caution as the rest of this ESPN integration (data/espn.py,
data/espn_injuries.py): unofficial, undocumented, could change shape
without notice -- every call here is wrapped so a hiccup just means no
adjustment for that player, never a crashed pipeline.
"""
from __future__ import annotations

import requests

STATS_BASE = "https://site.web.api.espn.com/apis/common/v3/sports"

# Same reasoning as models/scoring.MIN_GAMES_FOR_PROJECTION -- a season
# with fewer games than this is too thin to trust as "what this player
# normally contributes".
MIN_GAMES_FOR_PPG = 5


def get_player_ppg(espn_sport: str, espn_league: str, athlete_id: str) -> float | None:
    """
    Most recent season's points-per-game with at least MIN_GAMES_FOR_PPG
    games played -- the stats endpoint returns a player's full season-by-
    season history, not just the current one (a player out all season so
    far, e.g. recovering from a prior injury, simply has no current-season
    entry yet, so this correctly falls back to their last real season
    rather than returning 0). A player with no season anywhere meeting the
    games threshold (rookie with a handful of games, a decade-old journeyman
    entry, etc.) returns None -- "no adjustment", not "contributes zero".
    """
    try:
        resp = requests.get(
            f"{STATS_BASE}/{espn_sport}/{espn_league}/athletes/{athlete_id}/stats", timeout=10
        )
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except ValueError:
        return None

    categories = {c.get("name"): c for c in data.get("categories", [])}
    averages = categories.get("averages")
    if not averages or not averages.get("statistics"):
        return None
    names = averages.get("names", [])
    try:
        pts_idx = names.index("avgPoints")
        gp_idx = names.index("gamesPlayed")
    except ValueError:
        return None

    best = None  # (season_year, games_played, ppg) -- most recent, ties broken by games played
    for stat in averages["statistics"]:
        try:
            games_played = float(stat["stats"][gp_idx])
            ppg = float(stat["stats"][pts_idx])
            year = stat["season"]["year"]
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        if games_played < MIN_GAMES_FOR_PPG:
            continue
        if best is None or year > best[0] or (year == best[0] and games_played > best[1]):
            best = (year, games_played, ppg)
    return best[2] if best else None
