"""Generates docs/data.json for the installable PWA (served free via GitHub
Pages) from tips.db -- the same This Week / per-sport data the Streamlit
dashboard shows, serialized instead of rendered. docs/index.html, app.js,
sw.js etc are static and don't need regenerating; only this data file
changes. Run after the daily pipeline refresh -- see
.github/workflows/daily-refresh.yml, which calls this right after
tips.db is rebuilt.

Mirrors dashboard/app.py's This Week tab and render_sport_tab logic
exactly (same constants, same filtering) so the PWA and the Streamlit
Cloud dashboard never disagree about which bets are Must-Bet/Recommended.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DEFAULT_MAX_SINGLE_ODDS, DEFAULT_MIN_SINGLE_ODDS, SPORT_CONFIG, USER_BOOKMAKERS
from db import database as db
from models import multis
from models.risk import risk_label
from models.team_names import build_nickname_index, resolve
from tips.generate_tips import _finished_games_for_elo

WEEK_AHEAD_DAYS = 14
RECOMMENDED_COUNT = 5
MUST_BET_MIN_EDGE = 0.015
MUST_BET_COUNT = 4
RESULTS_LOOKBACK_DAYS = 21  # generous window for grading a logged bet against
MODEL_LEANS_COUNT = 6  # across all sports combined -- the volume lever when value-tip edge is thin this week
MARKET_LABEL = {"h2h": "Win", "spreads": "Handicap", "totals": "Line", "btts": "BTTS"}

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(REPO_ROOT, "docs", "data.json")


def _odds_range() -> tuple[float, float]:
    lo = float(db.get_setting("min_single_odds", str(DEFAULT_MIN_SINGLE_ODDS)))
    hi = float(db.get_setting("max_single_odds", str(DEFAULT_MAX_SINGLE_ODDS)))
    return lo, hi


def _with_risk(tips: list[dict]) -> list[dict]:
    for t in tips:
        t["risk"] = risk_label(t["market_fair_prob"]) if t["market_fair_prob"] is not None else None
    return tips


def _priority(t: dict) -> float:
    is_line_handicap = 1 if t["market"] in ("spreads", "totals") else 0
    return is_line_handicap + (t["edge_pct"] or 0)


def build_this_week() -> dict:
    lo, hi = _odds_range()
    today = date.today().isoformat()
    # WEEK_AHEAD_DAYS, not 7 -- Line/Handicap/Totals value routinely posts
    # days to a week+ before kickoff (catching it at 192.5 before it drifts
    # to 190.5), so this has to look as far out as the per-sport tabs do or
    # it'd find the value today but drop it from Must-Bet/Recommended for
    # being "too far away."
    week_end = (date.today() + timedelta(days=WEEK_AHEAD_DAYS)).isoformat()
    week_tips = _with_risk(db.get_tips_in_range(today, week_end, tip_type="value"))

    moved = db.get_moved_lines(today, week_end)

    def _movement_size(m):
        first, latest = m["first"], m["latest"]
        if first["point"] is not None and latest["point"] is not None:
            return abs(latest["point"] - first["point"])
        return abs(latest["fair_prob"] - first["fair_prob"])

    week_predictions = db.get_tips_in_range(today, week_end, tip_type="prediction")
    model_leans = sorted(week_predictions, key=lambda t: -t["model_prob"])[:MODEL_LEANS_COUNT]

    result = {
        "must_bet": [],
        "recommended": [],
        "model_leans": model_leans,
        "multis": [],
        "sgms": [],
        "line_movement": sorted(moved, key=_movement_size, reverse=True)[:10],
        "everything_else": [],
    }
    if not week_tips:
        return result

    within_cap = [t for t in week_tips if lo <= t["odds_decimal"] <= hi]
    for t in within_cap:
        t["priority"] = _priority(t)

    must_bet = sorted(
        (t for t in within_cap if t["edge_pct"] >= MUST_BET_MIN_EDGE and t["risk"] != "Risky"),
        key=lambda t: -t["priority"],
    )[:MUST_BET_COUNT]
    must_bet_ids = {t["id"] for t in must_bet}
    result["must_bet"] = must_bet

    remaining = [t for t in within_cap if t["id"] not in must_bet_ids]
    recommended = sorted(remaining, key=lambda t: -t["priority"])[:RECOMMENDED_COUNT]
    recommended_ids = {t["id"] for t in recommended}
    result["recommended"] = recommended

    result["multis"] = multis.suggest_multis(week_tips)
    result["sgms"] = multis.suggest_sgms(week_tips)

    shown_ids = must_bet_ids | recommended_ids
    result["everything_else"] = [t for t in week_tips if t["id"] not in shown_ids]
    return result


def build_results(sport: str, cfg: dict) -> list[dict]:
    """
    Recent finished games with final scores, for the PWA to auto-grade
    logged bets (My Bets) against -- entirely free (ESPN's public API, same
    source Elo already uses, no Odds API quota involved), and doesn't need
    a fresh odds pipeline run. The tricky part: bets are logged using The
    Odds API's team names (whatever a tip's matchup string says), but ESPN
    uses its own naming that doesn't always agree ("LA Clippers" vs
    "Los Angeles Clippers") -- reuses models/team_names.resolve() (same
    logic Elo training uses) built from every team name this sport's tips
    have ever used, so results ship under Odds-API-style names and a
    client-side exact string match on "matchup" is reliable rather than
    needing fuzzy matching in JS.
    """
    if not cfg.get("has_history"):
        return []
    games = _finished_games_for_elo(sport, cfg)
    cutoff = (date.today() - timedelta(days=RESULTS_LOOKBACK_DAYS)).isoformat()
    recent = [g for g in games if g["date"] >= cutoff]
    if not recent:
        return []

    espn_names = list({g["home_team"] for g in recent} | {g["away_team"] for g in recent})
    nickname_idx = build_nickname_index(espn_names)
    espn_to_odds = {}
    for odds_name in db.get_distinct_matchup_teams(sport):
        espn_name = resolve(odds_name, espn_names, nickname_idx)
        if espn_name:
            espn_to_odds[espn_name] = odds_name

    results = []
    for g in recent:
        home = espn_to_odds.get(g["home_team"], g["home_team"])
        away = espn_to_odds.get(g["away_team"], g["away_team"])
        results.append({
            "matchup": f"{away} @ {home}",
            "date": g["date"],
            "home_team": home,
            "away_team": away,
            "home_score": g["home_score"],
            "away_score": g["away_score"],
        })
    return results


def build_sport(sport: str, cfg: dict) -> dict:
    lo, hi = _odds_range()
    today = date.today().isoformat()
    end = (date.today() + timedelta(days=WEEK_AHEAD_DAYS)).isoformat()

    value = _with_risk(db.get_tips_in_range(today, end, tip_type="value", sport=sport))
    in_range = [t for t in value if lo <= t["odds_decimal"] <= hi]
    out_range = [t for t in value if not (lo <= t["odds_decimal"] <= hi)]

    predictions, knowledge = [], []
    if cfg["has_history"]:
        predictions = db.get_tips_in_range(today, end, tip_type="prediction", sport=sport)
        if cfg.get("show_knowledge", True):
            knowledge = db.get_tips_in_range(today, end, tip_type="knowledge", sport=sport)

    return {
        "has_history": cfg["has_history"],
        "auto_refresh": cfg.get("auto_refresh", False),
        "show_knowledge": cfg.get("show_knowledge", True),
        "value_in_range": in_range,
        "value_outside_range": out_range,
        "predictions": predictions,
        "knowledge": knowledge,
    }


def main():
    db.init_db()
    # Pure local-clock check, no API call -- runs on every build regardless
    # of whether the pipeline refresh succeeded, so a game that already
    # started doesn't keep showing as a live pick just because quota
    # blocked today's run.
    db.supersede_started_games()
    lo, hi = _odds_range()
    data = {
        "generated_at": db.now_iso(),
        "odds_range": [lo, hi],
        "market_label": MARKET_LABEL,
        "this_week": build_this_week(),
        "sports": {sport: build_sport(sport, cfg) for sport, cfg in SPORT_CONFIG.items()},
        "results": {sport: build_results(sport, cfg) for sport, cfg in SPORT_CONFIG.items()},
        "user_bookmakers": sorted(USER_BOOKMAKERS),
    }
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(data, f, default=str)
    print(f"[pwa] wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
