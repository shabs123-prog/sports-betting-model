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


def win_probability(home_elo: float, away_elo: float) -> float:
    """P(home wins), given raw (non-home-adjusted) Elo ratings."""
    diff = (home_elo + HOME_ADVANTAGE) - away_elo
    return 1.0 / (1.0 + 10 ** (-diff / 400.0))


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
