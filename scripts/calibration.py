"""CLI entrypoint: python -m scripts.calibration [sport]

Walk-forward calibration check for the Elo win-probability model and a
directional sanity check for the points-scored/allowed margin model
(models/scoring.py) -- both against real ESPN results, zero Odds API
quota involved. Answers "when the model says 65%, does that side actually
win about 65% of the time historically" -- the actual meaning of
"calibration," not just a feeling that the model seems reasonable.

This is a research/validation tool, not part of the daily pipeline --
run it on demand to sanity-check the models, especially after any change
to the Elo constants or the scoring projection.
"""
import sys
from collections import defaultdict

from config import SPORT_CONFIG
from data import espn
from models import elo
from models.scoring import MIN_GAMES_FOR_PROJECTION, build_scoring_averages, project_matchup
from tips.generate_tips import _espn_season_years


def _games(sport: str, cfg: dict) -> list[dict]:
    season_years = _espn_season_years(sport)
    return espn.get_finished_games_for_training(cfg["espn_sport"], cfg["espn_league"], season_years)


def elo_calibration(games: list[dict]) -> None:
    results = elo.backtest(games)
    if not results:
        print("  Not enough games for a walk-forward backtest yet.")
        return

    # Bucket by predicted probability (always from the home side's
    # perspective, so a bucket like 60-70% genuinely means "home side
    # favoured 60-70%" -- not mixing favourite/underdog together).
    buckets = defaultdict(lambda: {"n": 0, "wins": 0})
    for r in results:
        p = r["predicted_home_prob"]
        bucket = int(p * 10) * 10  # 0, 10, 20 ... 90
        buckets[bucket]["n"] += 1
        buckets[bucket]["wins"] += 1 if r["home_won"] else 0

    print(f"  {len(results)} out-of-sample predictions (first game per team excluded, no prior rating yet)")
    print(f"  {'Predicted range':<18}{'Games':<8}{'Actual win%':<14}{'Gap':<8}")
    for bucket in sorted(buckets):
        b = buckets[bucket]
        actual = b["wins"] / b["n"]
        predicted_mid = (bucket + 5) / 100
        gap = actual - predicted_mid
        print(f"  {bucket}-{bucket+10}%{'':<10}{b['n']:<8}{actual:.1%}{'':<8}{gap:+.1%}")

    # Overall accuracy (picking the side given >50%) and Brier score (lower
    # is better-calibrated; 0.25 is what a coin-flip model scores).
    correct = sum(
        1 for r in results
        if (r["predicted_home_prob"] >= 0.5) == r["home_won"]
    )
    brier = sum((r["predicted_home_prob"] - (1 if r["home_won"] else 0)) ** 2 for r in results) / len(results)
    print(f"  Overall: {correct}/{len(results)} ({correct/len(results):.1%}) picked-winner accuracy, Brier score {brier:.3f} (0.25 = coin flip, lower is better)")


def scoring_model_check(games: list[dict]) -> None:
    """
    No clean probability to bucket here (it's a point projection, not a
    win%), so this checks the two things that actually matter for a
    margin/total model: did it pick the right side more often than not,
    and how far off was the number on average (MAE) -- also walk-forward,
    using only games before the one being checked to build the averages.
    """
    if len(games) < 20:
        print("  Not enough games yet.")
        return
    correct_side, total_checked = 0, 0
    margin_errors, total_errors = [], []
    # Simple expanding window: check the back half of the season using
    # only the games before each point -- not a full walk-forward per
    # game (expensive), a representative sample instead.
    checkpoints = range(len(games) // 2, len(games), max(1, len(games) // 200))
    for i in checkpoints:
        prior_games = games[:i]
        g = games[i]
        avgs = build_scoring_averages(prior_games)
        proj = project_matchup(avgs.get(g["home_team"]), avgs.get(g["away_team"]))
        if not proj:
            continue
        actual_margin = g["home_score"] - g["away_score"]
        actual_total = g["home_score"] + g["away_score"]
        total_checked += 1
        if (proj["projected_margin"] > 0) == (actual_margin > 0):
            correct_side += 1
        margin_errors.append(abs(proj["projected_margin"] - actual_margin))
        total_errors.append(abs(proj["projected_total"] - actual_total))

    if not total_checked:
        print(f"  No checkpoints had both teams past {MIN_GAMES_FOR_PROJECTION} games yet.")
        return
    print(f"  {total_checked} sampled checkpoints (expanding window, not a full walk-forward -- representative, not exhaustive)")
    print(f"  Picked correct side: {correct_side}/{total_checked} ({correct_side/total_checked:.1%})")
    print(f"  Mean absolute error: margin {sum(margin_errors)/len(margin_errors):.1f} pts, total {sum(total_errors)/len(total_errors):.1f} pts")


if __name__ == "__main__":
    sports = [sys.argv[1]] if len(sys.argv) > 1 else [s for s, c in SPORT_CONFIG.items() if c.get("has_history")]
    for sport in sports:
        cfg = SPORT_CONFIG[sport]
        if not cfg.get("has_history"):
            print(f"\n=== {sport.upper()}: no results history source, skipping ===")
            continue
        print(f"\n=== {sport.upper()} ===")
        games = _games(sport, cfg)
        print(f"Elo win-probability calibration ({len(games)} games pulled):")
        elo_calibration(games)
        print(f"\nScoring-margin model check:")
        scoring_model_check(games)
