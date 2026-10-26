"""Margin-of-victory Elo, in the style popularised by FiveThirtyEight's NBA model.

Not sport-specific beyond the constants below -- the same engine can be reused
for EPL/NFL later by swapping K, HOME_ADVANTAGE and the MOV multiplier.
"""

from __future__ import annotations
import math

BASE_RATING = 1500.0
K_FACTOR = 20.0
HOME_ADVANTAGE = 100.0  # elo points added to the home team for win-prob purposes only
SEASON_REGRESSION = 0.25  # fraction reverted to BASE_RATING at the start of a new season

# Walk-forward backtested (scripts/calibration.py, 1258 out-of-sample NBA
# predictions): the raw sigmoid is overconfident -- a weighted fit across
# the 60-100% buckets (biggest, most reliable sample sizes; the near-50%
# buckets were too noisy at this n to fit precisely, see the script's
# output) came out to ~0.68. Rounded to a flatter, deliberately
# conservative 0.7 rather than the exact fitted value -- this is a rough
# first-pass correction based on one sport's backtest, not a precisely
# optimized constant, and erring conservative (less shrinkage, not more)
# is the safer direction to be wrong in.
#
# Applied globally (not just NBA) since NBL/NFL/AFL show the same
# direction of overconfidence on their own backtests, just with samples
# too small (233-452 games vs NBA's 1258) to fit a reliable per-sport
# number without overfitting noise. EPL is the outlier: its picked-
# winner accuracy is BELOW a coin flip (47.4%) because a single
# "P(home wins)" number can't represent a 3-outcome sport with draws --
# shrinkage can't fix that, it's why EPL is correctly left out of
# PREDICTION_ELIGIBLE_SPORTS in tips/generate_tips.py and Elo stays
# commentary-only there. Re-run scripts/calibration.py after any Elo
# constant change or once more seasons of data exist.
CALIBRATION_SHRINKAGE = 0.7


def win_probability(home_elo: float, away_elo: float) -> float:
    """
    P(home wins), given raw (non-home-adjusted) Elo ratings -- shrunk
    toward 50% by CALIBRATION_SHRINKAGE, since the raw sigmoid is
    backtested-overconfident (see the constant's comment). Only affects
    the probability shown/used for Predictions and "model agrees/
    disagrees" commentary -- update_ratings() has its own separate,
    unshrunk formula for how ratings move after a game, which is a
    different concern and must stay untouched by this.
    """
    diff = (home_elo + HOME_ADVANTAGE) - away_elo
    raw = 1.0 / (1.0 + 10 ** (-diff / 400.0))
    return 0.5 + (raw - 0.5) * CALIBRATION_SHRINKAGE


def _mov_multiplier(margin: int, elo_diff: float) -> float:
    return math.log(abs(margin) + 1) * (2.2 / (0.001 * abs(elo_diff) + 2.2))


def update_ratings(home_elo: float, away_elo: float, home_score: int, away_score: int):
    """Returns (new_home_elo, new_away_elo) after one game."""
    margin = home_score - away_score
    home_elo_eff = home_elo + HOME_ADVANTAGE
    expected_home = 1.0 / (1.0 + 10 ** (-(home_elo_eff - away_elo) / 400.0))
    actual_home = 1.0 if margin > 0 else 0.0 if margin < 0 else 0.5

    mult = _mov_multiplier(margin, home_elo_eff - away_elo)
    shift = K_FACTOR * mult * (actual_home - expected_home)

    return home_elo + shift, away_elo - shift


def regress_to_mean(ratings: dict) -> dict:
    return {
        team: BASE_RATING + (rating - BASE_RATING) * (1 - SEASON_REGRESSION)
        for team, rating in ratings.items()
    }


def train_from_games(games: list[dict]) -> dict:
    """
    games: list of dicts sorted chronologically, each with:
        home_team, away_team, home_score, away_score, season (int)
    Returns {team_name: current_elo_rating}.
    """
    ratings: dict[str, float] = {}
    current_season = None

    for g in games:
        season = g["season"]
        if current_season is None:
            current_season = season
        elif season != current_season:
            ratings = regress_to_mean(ratings)
            current_season = season

        home, away = g["home_team"], g["away_team"]
        ratings.setdefault(home, BASE_RATING)
        ratings.setdefault(away, BASE_RATING)

        new_home, new_away = update_ratings(
            ratings[home], ratings[away], g["home_score"], g["away_score"]
        )
        ratings[home], ratings[away] = new_home, new_away

    return ratings


def backtest(games: list[dict]) -> list[dict]:
    """
    Walk-forward calibration check: for each game, records the win
    probability predicted from ratings as they stood *before* that game
    (i.e. never trained on it), then updates ratings from the result, same
    as train_from_games. Using the final trained ratings to "predict" games
    that already went into training them would be circular -- this is the
    only honest way to ask "when Elo says 65%, does that side actually win
    about 65% of the time," since every prediction here is genuinely out-
    of-sample at the moment it's made.

    Returns [{predicted_home_prob, home_won, matchup, date}, ...] -- skips
    a team's first game each run, since there's no prior rating yet to
    predict from (same cold-start as the live pipeline).
    """
    ratings: dict[str, float] = {}
    current_season = None
    results = []

    for g in games:
        season = g["season"]
        if current_season is None:
            current_season = season
        elif season != current_season:
            ratings = regress_to_mean(ratings)
            current_season = season

        home, away = g["home_team"], g["away_team"]
        has_prior = home in ratings and away in ratings
        ratings.setdefault(home, BASE_RATING)
        ratings.setdefault(away, BASE_RATING)

        if has_prior:
            predicted_home_prob = win_probability(ratings[home], ratings[away])
            results.append({
                "date": g.get("date"),
                "matchup": f"{away} @ {home}",
                "predicted_home_prob": predicted_home_prob,
                "home_won": g["home_score"] > g["away_score"],
            })

        new_home, new_away = update_ratings(
            ratings[home], ratings[away], g["home_score"], g["away_score"]
        )
        ratings[home], ratings[away] = new_home, new_away

    return results
