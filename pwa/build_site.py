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
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DEFAULT_MAX_SINGLE_ODDS, DEFAULT_MIN_SINGLE_ODDS, LOG_BET_BOOKMAKERS, SPORT_CONFIG
from db import database as db
from models import multis
from models.risk import risk_label
from models.team_names import build_nickname_index, resolve
from tips.generate_tips import _finished_games_for_elo

# This build runs on a GitHub Actions runner, whose system clock is UTC --
# date.today() there returns UTC's calendar date, not AU's. At the main
# ~7am AEDT cron (20:00 UTC) those two are already a full day apart (AEDT
# has crossed midnight ~9 hours earlier), so "today" used naively would
# anchor every date-range filter (This Week, a sport's own tab, results
# lookback) one day too early relative to what "today" means to an AU
# user -- explicit feedback flagged this as part of why a narrow window
# (e.g. NFL's day-before tab) was coming up emptier than it should.
AU_TZ = ZoneInfo("Australia/Sydney")


def _au_today():
    return datetime.now(AU_TZ).date()


WEEK_AHEAD_DAYS = 14

# Superseded 2026-10-31 by an explicit, sport-split follow-up: "for all
# bets excluding epl i wanna only see the day after slate... epl just
# show the weekly games and tips whenever possible. nbl same." EPL and
# NBL play infrequently enough per team (EPL ~1 match/week, NBL ~2/week)
# that a week's fixtures don't bombard the screen -- same reasoning that
# originally kept them on the wide window below. Everything else (NBA,
# NFL, AFL) now shows ONLY the next calendar day's slate: a Monday-night
# update shows Tuesday's games, not today's or a whole week's -- this is
# the same "bombard my screen" complaint that was previously NFL-only
# (too many slips at once), now extended to NBA/AFL on the same logic.
WEEKLY_SPORTS = {"epl", "nbl"}
SPORT_WEEK_AHEAD_DAYS = 7
RECOMMENDED_COUNT = 5
MUST_BET_MIN_EDGE = 0.015
MUST_BET_COUNT = 4
RESULTS_LOOKBACK_DAYS = 21  # generous window for grading a logged bet against
MODEL_LEANS_COUNT = 6  # across all sports combined -- the volume lever when value-tip edge is thin this week
MARKET_LABEL = {"h2h": "Win", "spreads": "Handicap", "totals": "Line", "btts": "BTTS"}

# Explicit request 2026-10-31: "make a new section for bets over @3 odds
# and call it long shots, this is good for sports mainly." A reversal of
# the earlier "no longshots, ever" stance -- that was about longshots
# being presented AS a main recommendation or buried ambiguously among
# normal value bets, not about a clearly-labelled, separate section for
# them, which is what this is. Literal odds-decimal cutoff, independent
# of the user's own configurable min/max_single_odds range (which is
# usually tighter, e.g. 1.6-2.2) -- a long shot stays a long shot
# regardless of where that range happens to be set.
LONGSHOT_ODDS_THRESHOLD = 3.0

# See the "next game day" fallback in build_sport() below.
NEXT_GAME_DAY_MAX_FALLBACK_DAYS = 14

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
    today = _au_today().isoformat()
    # WEEK_AHEAD_DAYS, not 7 -- Line/Handicap/Totals value routinely posts
    # days to a week+ before kickoff (catching it at 192.5 before it drifts
    # to 190.5), so this has to look as far out as the per-sport tabs do or
    # it'd find the value today but drop it from Must-Bet/Recommended for
    # being "too far away."
    week_end = (_au_today() + timedelta(days=WEEK_AHEAD_DAYS)).isoformat()
    week_tips = _with_risk(db.get_tips_in_range(today, week_end, tip_type="value"))

    week_predictions = db.get_tips_in_range(today, week_end, tip_type="prediction")
    # Only Elo-based h2h predictions carry a model_prob -- the scoring-margin
    # spreads/totals leans (models/scoring.py) project a point margin/total
    # instead, which isn't on the same 0-1 scale and can't be ranked
    # alongside a probability. They're not lost from the app: each sport's
    # own Predictions tab still shows them (see render_sport_tab below) --
    # just excluded from this specific cross-sport "most confident" ranking,
    # which only makes sense for a genuine probability.
    leanable = [t for t in week_predictions if t["model_prob"] is not None]
    model_leans = sorted(leanable, key=lambda t: -t["model_prob"])[:MODEL_LEANS_COUNT]

    result = {
        "must_bet": [],
        "recommended": [],
        "model_leans": model_leans,
        "multis": [],
        "sgms": [],
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


def build_longshots() -> list[dict]:
    """
    Cross-sport, standalone -- explicit feedback 2026-10-31: "i dont see a
    long shot tab." It existed as a per-sport section (one per sport's own
    tab), but that's exactly the "buried" pattern this project has hit
    before (the old tiny Edit icon) -- easy to miss unless you happen to
    check a sport that has one right now. A single dedicated tab, same
    idea as This Week, means there's one place to look regardless of which
    sport the long shot is in. Also closes a real gap: a longshot value
    tip outside a sport's own (now narrower) window used to land only in
    This Week's internal "everything_else" list, which nothing in app.js
    actually renders -- it was reachable by no UI path at all. Uses the
    same wide WEEK_AHEAD_DAYS window as This Week so nothing here depends
    on a sport's own narrower day-after/weekly window.
    """
    today = _au_today().isoformat()
    week_end = (_au_today() + timedelta(days=WEEK_AHEAD_DAYS)).isoformat()
    value = _with_risk(db.get_tips_in_range(today, week_end, tip_type="value"))
    longshots = [t for t in value if t["odds_decimal"] >= LONGSHOT_ODDS_THRESHOLD]
    return sorted(longshots, key=lambda t: t["game_date"])


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
    cutoff = (_au_today() - timedelta(days=RESULTS_LOOKBACK_DAYS)).isoformat()
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


def _sport_tips_for_window(sport: str, cfg: dict, start: str, end: str, lo: float, hi: float):
    value = _with_risk(db.get_tips_in_range(start, end, tip_type="value", sport=sport))
    longshots = [t for t in value if t["odds_decimal"] >= LONGSHOT_ODDS_THRESHOLD]
    rest = [t for t in value if t["odds_decimal"] < LONGSHOT_ODDS_THRESHOLD]
    in_range = [t for t in rest if lo <= t["odds_decimal"] <= hi]
    out_range = [t for t in rest if not (lo <= t["odds_decimal"] <= hi)]

    predictions, knowledge = [], []
    if cfg["has_history"]:
        predictions = db.get_tips_in_range(start, end, tip_type="prediction", sport=sport)
        if cfg.get("show_knowledge", True):
            knowledge = db.get_tips_in_range(start, end, tip_type="knowledge", sport=sport)
    return in_range, out_range, longshots, predictions, knowledge


def build_sport(sport: str, cfg: dict) -> dict:
    lo, hi = _odds_range()
    today = _au_today()
    if sport in WEEKLY_SPORTS:
        start = today.isoformat()
        end = (today + timedelta(days=SPORT_WEEK_AHEAD_DAYS)).isoformat()
    else:
        # Target is "day after" (a Monday-night update should feature
        # Tuesday's slate), but a single-day window is too brittle against
        # odds-posting timing: real case found 2026-10-31, NBA books simply
        # hadn't posted tomorrow's lines yet at refresh time, and a tip that
        # can't exist in the db (no odds to price it against) can't be
        # "fallen forward" to either -- so the tab went completely blank
        # despite today's own slate having a real, live, un-shown value bet
        # sitting right there (Chicago Bulls @ 8.25, 1.9% edge). Today's
        # already-started games don't leak in here -- db.supersede_started_
        # games() (called in main() below) already clears those out of
        # "open" regardless of this window, so including today just adds a
        # safety net, not stale picks.
        start = today.isoformat()
        end = (today + timedelta(days=1)).isoformat()

    in_range, out_range, longshots, predictions, knowledge = _sport_tips_for_window(sport, cfg, start, end, lo, hi)

    # Explicit feedback: a narrow per-sport window going empty (tomorrow's
    # off night, or any sport genuinely quiet right now) should fall
    # forward to whatever the next game day actually is, rather than just
    # showing nothing until that window happens to land on it. Only kicks
    # in when the whole window came up empty -- a window with SOME
    # content never falls forward, so this never hides or reorders what's
    # already showing.
    next_game_day = None
    if not (in_range or out_range or longshots or predictions or knowledge):
        next_date = db.get_next_tip_date(sport, end)
        # Real case found 2026-10-31: NBA books had posted odds for today
        # and for the Christmas Day showcase slate (priced up ~2 months
        # ahead, standard for that marquee date) but nothing in between --
        # a genuine gap in what's currently priced, not a quiet sport.
        # Falling forward across that gap would show "here's the next
        # game day: Dec 25" on a sport that plays nearly every night, which
        # reads as broken rather than honest. Capped at NEXT_GAME_DAY_MAX_
        # FALLBACK_DAYS: short gaps (AFL/NFL/NBL's real multi-day gaps
        # between rounds) still fall forward same as before; a gap this
        # wide instead falls through to the plain "nothing right now"
        # message, which is the more honest thing to show.
        if next_date and (date.fromisoformat(next_date) - date.fromisoformat(end)).days <= NEXT_GAME_DAY_MAX_FALLBACK_DAYS:
            in_range, out_range, longshots, predictions, knowledge = _sport_tips_for_window(
                sport, cfg, next_date, next_date, lo, hi
            )
            next_game_day = next_date

    return {
        "has_history": cfg["has_history"],
        "auto_refresh": cfg.get("auto_refresh", False),
        "show_knowledge": cfg.get("show_knowledge", True),
        "next_game_day": next_game_day,
        "value_in_range": in_range,
        "value_outside_range": out_range,
        "longshots": longshots,
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
        "longshots": build_longshots(),
        "sports": {sport: build_sport(sport, cfg) for sport, cfg in SPORT_CONFIG.items()},
        "results": {sport: build_results(sport, cfg) for sport, cfg in SPORT_CONFIG.items()},
        "user_bookmakers": sorted(LOG_BET_BOOKMAKERS),
    }
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(data, f, default=str)
    print(f"[pwa] wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
