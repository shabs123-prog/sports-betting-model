"""Daily pipeline: pull odds + schedule data, run the Elo model, find priced
edges, attach situational context, write everything to the tips table.

Run directly (`python -m tips.generate_tips [sport]`) or call run() from the
dashboard. Markets (h2h/spreads/totals) and whether a sport has a results
history (for Elo + ball-knowledge notes) are driven by config.SPORT_CONFIG --
see that file to add a new sport.
"""
from datetime import date

from config import (
    DEFAULT_MAX_UNITS_PER_BET,
    DEFAULT_MIN_EDGE_PCT,
    DEFAULT_TIER_UNITS_LEAN,
    DEFAULT_TIER_UNITS_MODERATE,
    DEFAULT_TIER_UNITS_STRONG,
    SPORT_CONFIG,
    USER_BOOKMAKERS,
)
from data import espn, odds_api
from db import database as db
from knowledge import situational
from models import edge, elo
from models.risk import risk_label
from models.team_names import build_nickname_index, resolve


def _get_float_setting(key: str, default: float) -> float:
    val = db.get_setting(key)
    return float(val) if val is not None else default


def _load_staking_settings() -> dict:
    return {
        "max_units": _get_float_setting("max_units_per_bet", DEFAULT_MAX_UNITS_PER_BET),
        "min_edge_pct": _get_float_setting("min_edge_pct", DEFAULT_MIN_EDGE_PCT),
        "tier_units": {
            "Strong": _get_float_setting("tier_units_strong", DEFAULT_TIER_UNITS_STRONG),
            "Moderate": _get_float_setting("tier_units_moderate", DEFAULT_TIER_UNITS_MODERATE),
            "Lean": _get_float_setting("tier_units_lean", DEFAULT_TIER_UNITS_LEAN),
        },
    }


def _tier_units(tier: str, settings: dict) -> float:
    base = settings["tier_units"].get(tier, settings["tier_units"]["Lean"])
    return round(min(base, settings["max_units"]), 2)


def _espn_season_years(sport: str) -> list[int]:
    """
    ESPN's "season" param convention differs per league -- empirically
    checked (not guessed) for each of these:
      - nba/nbl: ending-year of a season that spans Oct->Apr (2025-26 = 2026)
      - epl: starting-year of a season that spans Aug->May (2026-27 = 2026)
      - afl/nfl: single calendar year labelled by the year it starts in
        (AFL: Feb->Sep; NFL: Sep->Feb)
    Returns [prior_season_year, current_season_year].
    """
    today = date.today()
    if sport in ("nba", "nbl"):
        current = today.year + 1 if today.month >= 10 else today.year
    elif sport == "epl":
        current = today.year if today.month >= 8 else today.year - 1
    elif sport == "afl":
        current = today.year if today.month >= 2 else today.year - 1
    elif sport == "nfl":
        current = today.year if today.month >= 3 else today.year - 1
    else:
        raise ValueError(f"No ESPN season convention defined for {sport!r}")
    return [current - 1, current]


def _finished_games_for_elo(sport: str, cfg: dict) -> list:
    season_years = _espn_season_years(sport)
    return espn.get_finished_games_for_training(cfg["espn_sport"], cfg["espn_league"], season_years)


# Odds in this range are statistically the hardest to find real edge on
# (short-priced favourites are efficiently priced; this band sits between
# "safe favourite" and "proper longshot") -- so when the market does offer
# something here, it's worth surfacing even at a thinner edge than usual,
# rather than letting it get crowded out by easier-to-find longshot edges.
# Spreads/totals ("Line"/"Handicap") get the same discount even outside that
# price band -- they're priced near-even by construction, so they're
# structurally where 1.7-2.2 value actually lives; moneyline underdogs are
# the ones that drift to longshot odds.
PREFERRED_ODDS_RANGE = (1.7, 2.2)
PREFERRED_MARKETS = {"spreads", "totals"}
DISCOUNTED_EDGE_MULTIPLIER = 0.6  # accept 60% of the usual min-edge when either applies


def _effective_min_edge(decimal_odds: float, market: str, base_threshold: float) -> float:
    lo, hi = PREFERRED_ODDS_RANGE
    if market in PREFERRED_MARKETS or lo <= decimal_odds <= hi:
        return base_threshold * DISCOUNTED_EDGE_MULTIPLIER
    return base_threshold


def _format_selection(outcome_name: str, point, market_key: str) -> str:
    if point is None:
        return outcome_name
    if market_key == "totals":
        return f"{outcome_name} {point:g}"
    return f"{outcome_name} {point:+g}"


def run(sport: str = "nba", log=print) -> dict:
    db.init_db()
    settings = _load_staking_settings()
    cfg = SPORT_CONFIG[sport]

    ratings, team_logs, nickname_index, canonical_names = {}, {}, {}, []
    if cfg["has_history"]:
        log(f"[{sport}] pulling finished games (ESPN) to train Elo ratings...")
        finished_games = _finished_games_for_elo(sport, cfg)
        ratings = elo.train_from_games(finished_games)
        db.save_elo_ratings(sport, ratings)
        team_logs = situational.build_team_game_log(finished_games)
        canonical_names = list(ratings.keys())
        nickname_index = build_nickname_index(canonical_names)
        log(f"[{sport}] trained Elo for {len(ratings)} teams from {len(finished_games)} games.")
    else:
        log(f"[{sport}] no results history wired up yet -- devig value tips only, "
            f"no Elo context or ball-knowledge notes for this sport.")

    log(f"[{sport}] pulling live odds ({', '.join(cfg['markets'])})...")
    events = odds_api.fetch_odds(cfg["odds_key"], markets=",".join(cfg["markets"]))
    log(f"[{sport}] {len(events)} upcoming games with odds.")

    value_tips, knowledge_notes_logged = 0, 0

    for event in events:
        home_odds_name = event["home_team"]
        away_odds_name = event["away_team"]
        game_date = event["commence_time"][:10]
        matchup = f"{away_odds_name} @ {home_odds_name}"

        model_probs = {}
        notes = []
        if cfg["has_history"]:
            home_team = resolve(home_odds_name, canonical_names, nickname_index) or home_odds_name
            away_team = resolve(away_odds_name, canonical_names, nickname_index) or away_odds_name
            home_elo = ratings.get(home_team, elo.BASE_RATING)
            away_elo = ratings.get(away_team, elo.BASE_RATING)
            model_prob_home = elo.win_probability(home_elo, away_elo)
            model_probs = {home_odds_name: model_prob_home, away_odds_name: 1 - model_prob_home}
            game_date_obj = date.fromisoformat(game_date)
            notes = situational.generate_notes(home_team, away_team, game_date_obj, team_logs)

        game_had_value = False
        for market in cfg["markets"]:
            outcome_points = odds_api.consensus_points(event, market)
            books = odds_api.bookmaker_books(event, market, outcome_points)
            if not books:
                continue
            fair_probs = edge.consensus_fair_probs(books)
            # Fair probability uses every bookmaker (broader = more accurate
            # consensus); the actual recommendation is restricted to accounts
            # you hold, so every tip is one you can actually go place.
            best_prices = odds_api.best_price_per_outcome(
                event, market, outcome_points, allowed_bookmakers=USER_BOOKMAKERS
            )

            for outcome_name, price_info in best_prices.items():
                decimal_odds = price_info["price"]
                bookmaker = price_info["bookmaker"]
                point = price_info.get("point")
                fair_prob = fair_probs.get(outcome_name)
                if fair_prob is None:
                    continue

                devig_edge_pct = edge.edge_pct(fair_prob, decimal_odds)
                # Elo only predicts moneyline win probability -- it says nothing
                # about covering a spread or clearing a total, so only attach it
                # as context on h2h picks.
                model_prob = model_probs.get(outcome_name) if market == "h2h" else None

                # Devig (cross-bookmaker consensus) is the trustworthy signal in
                # an efficient market -- it doesn't depend on our own model being
                # right. The Elo model is secondary/contextual only: it's trained
                # on a single season with no preseason prior, so large
                # disagreements with the market are almost always model noise,
                # not real edge. It must NEVER be the thing that triggers a tip
                # or sizes a stake -- that was a bug in an earlier version.
                if devig_edge_pct >= _effective_min_edge(decimal_odds, market, settings["min_edge_pct"]):
                    tier = edge.tier_label(devig_edge_pct)
                    units = _tier_units(tier, settings)
                    risk = risk_label(fair_prob)
                    reasoning_parts = [
                        f"[{tier} edge · {risk}] No-vig market-consensus probability: {fair_prob:.1%} vs "
                        f"price-implied {1/decimal_odds:.1%} ({devig_edge_pct:+.1%} edge) at {bookmaker}.",
                    ]
                    if model_prob is not None:
                        disagreement = abs(model_prob - fair_prob)
                        if disagreement >= 0.08:
                            reasoning_parts.append(
                                f"Elo model says {model_prob:.1%}, which disagrees sharply with the "
                                f"market here -- treat that gap as model noise, not extra confidence."
                            )
                        else:
                            reasoning_parts.append(f"Elo model roughly agrees ({model_prob:.1%}).")
                    if outcome_name in (home_odds_name, away_odds_name) and notes:
                        reasoning_parts.extend(notes)

                    db.insert_tip({
                        "game_date": game_date,
                        "sport": sport,
                        "matchup": matchup,
                        "market": market,
                        "selection": _format_selection(outcome_name, point, market),
                        "bookmaker": bookmaker,
                        "odds_decimal": decimal_odds,
                        "model_prob": model_prob,
                        "market_fair_prob": fair_prob,
                        "edge_pct": devig_edge_pct,
                        "suggested_units": units,
                        "tip_type": "value",
                        "reasoning": " ".join(reasoning_parts),
                    })
                    value_tips += 1
                    game_had_value = True

        if notes and not game_had_value:
            db.insert_tip({
                "game_date": game_date,
                "sport": sport,
                "matchup": matchup,
                "market": "h2h",
                "selection": "—",
                "bookmaker": None,
                "odds_decimal": None,
                "model_prob": model_probs.get(home_odds_name),
                "market_fair_prob": None,
                "edge_pct": None,
                "suggested_units": 0,
                "tip_type": "knowledge",
                "reasoning": " ".join(notes),
            })
            knowledge_notes_logged += 1

    log(f"[{sport}] done: {value_tips} value tips, {knowledge_notes_logged} standalone knowledge notes.")
    return {"value_tips": value_tips, "knowledge_notes": knowledge_notes_logged}


def ensure_fresh(sport: str, log=print) -> dict:
    """
    Run the pipeline for this sport only if it hasn't already run today --
    so opening the dashboard is instant after the first check of the day,
    instead of re-pulling odds and re-training Elo on every single page load.
    """
    today = date.today().isoformat()
    last_run = db.get_setting(f"last_run_date_{sport}")
    if last_run == today:
        return {"skipped": True}
    result = run(sport=sport, log=log)
    db.set_setting(f"last_run_date_{sport}", today)
    return {"skipped": False, **result}


if __name__ == "__main__":
    import sys

    run(sport=sys.argv[1] if len(sys.argv) > 1 else "nba")
