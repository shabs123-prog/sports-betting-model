"""Client for ESPN's public site API (site.api.espn.com) -- undocumented but
widely used, no key/auth required, no login wall, same data their own
site/app reads. Used as the results-history source: pull every team's
season schedule, dedupe by game id (each game appears in both teams'
schedules), keep only finished games.

Unofficial and could change shape without notice -- this is read-only,
public JSON, not scraping behind any login or defeating any protection, but
treat it as a bit fragile (wrap calls, don't assume it's forever stable).
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import requests

BASE = "https://site.api.espn.com/apis/site/v2/sports"

# ESPN's event "date" is UTC. AEDT is UTC+11, so a game late UTC evening is
# already "tomorrow" locally -- taking the UTC date directly would file a
# back-to-back check (and Elo's chronological ordering) under the wrong
# calendar day. Same issue, same fix, as tips/generate_tips._local_game_date.
AU_TZ = ZoneInfo("Australia/Sydney")


class EspnError(RuntimeError):
    pass


def _local_date(iso_timestamp: str) -> str:
    dt_utc = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
    return dt_utc.astimezone(AU_TZ).date().isoformat()


def _get(url: str, params: dict | None = None) -> dict:
    resp = requests.get(url, params=params or {}, timeout=15)
    if resp.status_code != 200:
        raise EspnError(f"ESPN error {resp.status_code} for {url}: {resp.text[:200]}")
    return resp.json()


def get_teams(espn_sport: str, espn_league: str) -> list[dict]:
    data = _get(f"{BASE}/{espn_sport}/{espn_league}/teams")
    try:
        return data["sports"][0]["leagues"][0]["teams"]
    except (KeyError, IndexError) as e:
        raise EspnError(f"Unexpected teams response shape for {espn_sport}/{espn_league}") from e


def get_team_id_map(espn_sport: str, espn_league: str) -> dict[str, str]:
    """{team displayName: espn team id} -- used to look up injuries per team
    (data/espn_injuries.py), which is keyed by id, not name."""
    try:
        teams = get_teams(espn_sport, espn_league)
    except EspnError:
        return {}
    return {t["team"]["displayName"]: t["team"]["id"] for t in teams}


def get_team_schedule(espn_sport: str, espn_league: str, team_id: str, season: int | None = None) -> list[dict]:
    params = {"season": season} if season else {}
    data = _get(f"{BASE}/{espn_sport}/{espn_league}/teams/{team_id}/schedule", params)
    return data.get("events", [])


def _score_value(competitor: dict):
    score = competitor.get("score")
    if isinstance(score, dict):
        return score.get("value")
    return score


def get_finished_games_for_training(
    espn_sport: str, espn_league: str, season_years: list[int | None]
) -> list[dict]:
    """
    season_years: which season param values to pull (e.g. [2026, 2027] for
    NBA to cover prior + current season -- conventions differ per league,
    see tips/generate_tips.py for the per-sport year math).

    Returns normalized, deduped, chronologically sorted finished games:
    [{date, season, home_team, away_team, home_score, away_score}, ...]
    """
    teams = get_teams(espn_sport, espn_league)
    seen_ids = set()
    games = []

    for team in teams:
        team_id = team["team"]["id"]
        for season in season_years:
            events = get_team_schedule(espn_sport, espn_league, team_id, season)
            for event in events:
                if event["id"] in seen_ids:
                    continue
                comp = event["competitions"][0]
                # "completed" is a sport-agnostic boolean -- the status *name*
                # that means "finished" differs per sport (NBA: STATUS_FINAL,
                # soccer: STATUS_FULL_TIME, etc.), so don't match on the name.
                if not comp.get("status", {}).get("type", {}).get("completed"):
                    continue
                home = next((c for c in comp["competitors"] if c["homeAway"] == "home"), None)
                away = next((c for c in comp["competitors"] if c["homeAway"] == "away"), None)
                if not home or not away:
                    continue
                home_score = _score_value(home)
                away_score = _score_value(away)
                if home_score is None or away_score is None:
                    continue
                seen_ids.add(event["id"])
                games.append({
                    "date": _local_date(event["date"]),
                    "season": event.get("season", {}).get("year"),
                    "home_team": home["team"]["displayName"],
                    "away_team": away["team"]["displayName"],
                    "home_score": home_score,
                    "away_score": away_score,
                })

    games.sort(key=lambda g: g["date"])
    return games
