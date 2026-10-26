"""Simple points-scored/points-allowed projection, for generating model
leans on spreads/totals markets -- Elo only ever outputs a win probability,
it has nothing to say about margin or total points, so straight-win
Predictions (tips/generate_tips.py) can't extend to these markets on their
own. This doesn't need anything new: it's built from the exact same
finished-games data (ESPN, free) already pulled for Elo training.

Deliberately simple (team averages, not a regression or pace model) --
a first pass. The point isn't to be clever, it's to have *some* independent
read on a likely total/margin to compare against the market, the same way
Elo gives an independent read on who's likely to win. Treat it with the
same caution: noisiest early in a season, context not causation.
"""
from __future__ import annotations

MIN_GAMES_FOR_PROJECTION = 5  # too few games = too noisy an average to trust


def build_scoring_averages(games: list[dict]) -> dict[str, dict]:
    """
    games: [{home_team, away_team, home_score, away_score}, ...] (same shape
    data/espn.get_finished_games_for_training returns).
    Returns {team: {"scored": avg_points_for, "allowed": avg_points_against, "games": n}}.
    """
    totals: dict[str, dict] = {}
    for g in games:
        for team, scored, allowed in (
            (g["home_team"], g["home_score"], g["away_score"]),
            (g["away_team"], g["away_score"], g["home_score"]),
        ):
            d = totals.setdefault(team, {"scored_sum": 0.0, "allowed_sum": 0.0, "games": 0})
            d["scored_sum"] += scored
            d["allowed_sum"] += allowed
            d["games"] += 1
    return {
        team: {
            "scored": d["scored_sum"] / d["games"],
            "allowed": d["allowed_sum"] / d["games"],
            "games": d["games"],
        }
        for team, d in totals.items()
    }


def project_matchup(home_avg: dict | None, away_avg: dict | None) -> dict | None:
    """
    Blended projection: each team's expected output is the average of their
    own scoring rate and the opponent's rate of conceding -- a team that
    scores a lot against a defense that concedes a lot is projected high,
    and vice versa. Returns None if either team doesn't have enough games
    played yet to trust the average (see MIN_GAMES_FOR_PROJECTION).
    """
    if not home_avg or not away_avg:
        return None
    if home_avg["games"] < MIN_GAMES_FOR_PROJECTION or away_avg["games"] < MIN_GAMES_FOR_PROJECTION:
        return None
    home_proj = (home_avg["scored"] + away_avg["allowed"]) / 2
    away_proj = (away_avg["scored"] + home_avg["allowed"]) / 2
    return {
        "home_projected": home_proj,
        "away_projected": away_proj,
        "projected_total": home_proj + away_proj,
        "projected_margin": home_proj - away_proj,  # positive = home favoured
    }
