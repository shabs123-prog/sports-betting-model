"""Exercises every module that doesn't require live API keys. Not a pytest
suite -- just a fast sanity check you can delete once you trust the pipeline."""
from datetime import date

from db import database as db
from knowledge import situational
from models import edge, elo, kelly
from models.team_names import build_nickname_index, resolve

print("== devig / edge ==")
books = [
    {"bookmaker": "Sportsbet", "prices": {"Lakers": 1.90, "Celtics": 2.00}},
    {"bookmaker": "TAB", "prices": {"Lakers": 2.05, "Celtics": 1.87}},
    {"bookmaker": "Neds", "prices": {"Lakers": 1.95, "Celtics": 1.95}},
]
fair = edge.consensus_fair_probs(books)
print("fair probs:", {k: round(v, 3) for k, v in fair.items()})
assert abs(sum(fair.values()) - 1.0) < 0.01
e = edge.edge_pct(fair["Lakers"], 2.05)
print(f"TAB Lakers @ 2.05 edge: {e:+.1%}")
assert e > 0, "TAB's 2.05 on Lakers should look like value vs the ~1.94 consensus price"

print("\n== kelly ==")
stake = kelly.suggested_stake(prob=0.55, decimal_odds=2.05, bankroll=1000, unit_size_pct=0.01, kelly_frac_multiplier=0.3, max_units=5)
print(stake)
assert stake["units"] > 0

print("\n== elo ==")
games = [
    {"date": "2025-10-01", "season": 2025, "home_team": "Lakers", "away_team": "Celtics", "home_score": 110, "away_score": 100},
    {"date": "2025-10-03", "season": 2025, "home_team": "Celtics", "away_team": "Lakers", "home_score": 105, "away_score": 108},
    {"date": "2025-10-05", "season": 2025, "home_team": "Lakers", "away_team": "Warriors", "home_score": 120, "away_score": 95},
]
ratings = elo.train_from_games(games)
print("ratings:", {k: round(v, 1) for k, v in ratings.items()})
assert ratings["Lakers"] > elo.BASE_RATING, "Lakers won 2 of 3, rating should rise"
p = elo.win_probability(ratings["Lakers"], ratings["Warriors"])
print(f"Lakers win prob vs Warriors: {p:.1%}")

print("\n== team_names ==")
idx = build_nickname_index(["Los Angeles Lakers", "LA Clippers", "Boston Celtics"])
assert resolve("Los Angeles Clippers", idx) == "LA Clippers"
print("nickname resolution OK")

print("\n== situational ==")
finished = [
    {"date": "2025-10-20", "home_team": "Lakers", "away_team": "Celtics", "home_score": 100, "away_score": 90},
    {"date": "2025-10-21", "home_team": "Nuggets", "away_team": "Lakers", "home_score": 95, "away_score": 92},
]
logs = situational.build_team_game_log(finished)
notes = situational.generate_notes("Celtics", "Lakers", date(2025, 10, 22), logs)
print("notes:", notes)
assert any("back-to-back" in n for n in notes), "Lakers played the 21st, so the 22nd should flag a back-to-back"

print("\n== db ==")
import config
db.DB_PATH = config.BASE_DIR / "smoke_test.db"
db.init_db()
tip_id = db.insert_tip({
    "game_date": "2025-10-22", "sport": "nba", "matchup": "Lakers @ Celtics",
    "market": "h2h", "selection": "Celtics", "bookmaker": "TAB", "odds_decimal": 2.05,
    "model_prob": 0.55, "market_fair_prob": 0.50, "edge_pct": 0.05,
    "suggested_units": 1.5, "tip_type": "value", "reasoning": "test",
})
assert db.get_tips_for_date("2025-10-22")[0]["id"] == tip_id
db.DB_PATH.unlink()
print("db OK")

print("\nALL SMOKE TESTS PASSED")
