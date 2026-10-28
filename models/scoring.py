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


# How much of a missing player's own scoring just doesn't happen (not
# absorbed by a teammate taking the extra shots/minutes) -- real but
# genuinely uncertain without play-by-play/on-off data this project
# doesn't have access to; 0.35 is a conservative, clearly-approximate
# estimate (teammates typically pick up the bulk, not all, of a missing
# player's box-score production), not a precisely fitted number. Checked
# against a real case (2026-10-28, Sacramento Kings missing 4 rotation
# players incl. Ben Simmons, ~37 combined PPG) before picking it: at 0.35
# the projected margin moved from the market's own direction by a
# meaningful amount (-9.0 -> -15.4 points) and the gap that had made
# "Kings +18.5" clear the lean threshold at all (9.5 pts of headroom)
# shrank to -0.3 -- i.e. that exact bad pick would have been suppressed
# outright, not just sized down. Actual result: Kings lost by 27.
PLAYER_ABSENCE_RETENTION = 0.35

# Two safeguards added the same day, after checking a full real slate
# (not just the one validated case): ESPN's free injuries feed is noisier
# than expected at this granularity -- one team showed 7 "Out" players in
# a single game including two genuine stars, which just isn't realistic
# (that team wouldn't have a legal rotation left), and a separate case
# had a player attributed to the wrong team entirely. Both are data-
# quality problems in the free source itself, not something resolvable
# from here (no independent roster source to cross-check against) -- so
# instead of trusting an arbitrarily long/wrong list at face value, this
# bounds how much damage it can do:
#   - only the top MAX_OUT_PLAYERS_COUNTED by PPG count at all (a 5th+
#     "Out" name is either a deep-bench player whose absence barely
#     matters anyway, or more likely noise in the feed -- both reasons
#     to ignore it)
#   - the total adjustment is hard-capped at MAX_TEAM_POINTS_ADJUSTMENT
#     regardless of how many qualify, as a backstop against any single
#     wrong high-PPG entry (e.g. a misattributed star) swinging things
#     implausibly
# Re-checked both don't touch the one validated real case (2026-10-28
# Kings: 4 players, uncapped) -- these only bite on the noisier cases.
MAX_OUT_PLAYERS_COUNTED = 4
MAX_TEAM_POINTS_ADJUSTMENT = 15.0


def injury_adjusted_avg(team_avg: dict | None, out_player_ppgs: list[float]) -> dict | None:
    """
    Returns a copy of team_avg with "scored" reduced by the estimated
    points its own confirmed-OUT players would otherwise have contributed.
    Deliberately scoped to this team's OFFENSE only -- there's no
    comparably simple, reliable free stat for how a specific player's
    absence changes what the OPPOSING team scores (on/off defensive
    rating isn't in this data source), so this doesn't invent one.
    No-ops (returns team_avg unchanged) when there's nothing to adjust.
    """
    if not team_avg or not out_player_ppgs:
        return team_avg
    top_ppgs = sorted(out_player_ppgs, reverse=True)[:MAX_OUT_PLAYERS_COUNTED]
    lost_points = min(sum(top_ppgs) * PLAYER_ABSENCE_RETENTION, MAX_TEAM_POINTS_ADJUSTMENT)
    adjusted = dict(team_avg)
    adjusted["scored"] = max(team_avg["scored"] - lost_points, 0.0)
    return adjusted
