"""Daily pipeline: pull odds + schedule data, run the Elo model, find priced
edges, attach situational context, write everything to the tips table.

Run directly (`python -m tips.generate_tips [sport]`) or call run() from the
dashboard. Markets (h2h/spreads/totals) and whether a sport has a results
history (for Elo + ball-knowledge notes) are driven by config.SPORT_CONFIG --
see that file to add a new sport.
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo

from config import (
    AU_BOOKMAKERS,
    DEFAULT_MAX_SINGLE_ODDS,
    DEFAULT_MAX_UNITS_PER_BET,
    DEFAULT_MIN_EDGE_PCT,
    DEFAULT_MIN_SINGLE_ODDS,
    DEFAULT_TIER_UNITS_LEAN,
    DEFAULT_TIER_UNITS_MODERATE,
    DEFAULT_TIER_UNITS_STRONG,
    SECONDARY_SHARP_REFERENCE_BOOKMAKER,
    SHARP_REFERENCE_BOOKMAKER,
    SPORT_CONFIG,
    USER_BOOKMAKERS,
)
from data import espn, odds_api
from db import database as db
from knowledge import situational
from models import edge, elo
from models.devig import devig_book
from models.risk import risk_label
from models.team_names import build_nickname_index, resolve


def _get_float_setting(key: str, default: float) -> float:
    val = db.get_setting(key)
    return float(val) if val is not None else default


def _load_staking_settings() -> dict:
    return {
        "max_units": _get_float_setting("max_units_per_bet", DEFAULT_MAX_UNITS_PER_BET),
        "min_edge_pct": _get_float_setting("min_edge_pct", DEFAULT_MIN_EDGE_PCT),
        "max_single_odds": _get_float_setting("max_single_odds", DEFAULT_MAX_SINGLE_ODDS),
        "min_single_odds": _get_float_setting("min_single_odds", DEFAULT_MIN_SINGLE_ODDS),
        "tier_units": {
            "Strong": _get_float_setting("tier_units_strong", DEFAULT_TIER_UNITS_STRONG),
            "Moderate": _get_float_setting("tier_units_moderate", DEFAULT_TIER_UNITS_MODERATE),
            "Lean": _get_float_setting("tier_units_lean", DEFAULT_TIER_UNITS_LEAN),
        },
    }


def _tier_units(tier: str, settings: dict) -> float:
    base = settings["tier_units"].get(tier, settings["tier_units"]["Lean"])
    return round(min(base, settings["max_units"]), 2)


# Model Elo is draw-aware for exactly zero of the sports configured so far --
# it only ever splits probability between home/away. For a 3-way sport
# (EPL has draws, ~25% of results) that overstates both teams' true win
# probability by ignoring the draw entirely. Fine as a soft sanity-check
# comparison (how it's used elsewhere), not fine as the basis for a
# standalone "the model likes this" recommendation -- so Prediction tips
# are restricted to sports where a binary win/loss split is a reasonable
# model of the market in the first place.
PREDICTION_ELIGIBLE_SPORTS = {"nba", "nbl", "nfl", "afl"}
PREDICTION_MIN_MODEL_PROB = 0.55
PREDICTION_UNITS = 1.0


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


AU_TZ = ZoneInfo("Australia/Sydney")  # handles AEST/AEDT automatically


def _local_game_date(commence_time_iso: str) -> str:
    """
    The Odds API's commence_time is UTC. AEDT is UTC+11, so a game at, say,
    9am UTC is already 8pm *tonight* in Sydney -- taking the UTC date
    directly would label it with what's still "today" in UTC but could be
    "tomorrow" locally (or vice versa near local midnight, when UTC is
    still on the previous day). Convert to AU local time before taking the
    date, so "today" in the dashboard means what an AU user means by it.
    """
    dt_utc = datetime.fromisoformat(commence_time_iso.replace("Z", "+00:00"))
    return dt_utc.astimezone(AU_TZ).date().isoformat()


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

    value_tips, prediction_tips, knowledge_notes_logged = 0, 0, 0
    line_history_rows = []  # every outcome's fair prob/point this run, for future line-movement detection

    for event in events:
        home_odds_name = event["home_team"]
        away_odds_name = event["away_team"]
        game_date = _local_game_date(event["commence_time"])
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
        h2h_best_prices, h2h_value_outcomes = {}, set()
        for market in cfg["markets"]:
            # Point consensus and the "AU consensus" fair prob both stay
            # AU-only -- Pinnacle quoting a different point, or being blended
            # into the average, would dilute rather than sharpen the signal.
            outcome_points = odds_api.consensus_points(event, market, allowed_bookmakers=AU_BOOKMAKERS)
            books = odds_api.bookmaker_books(event, market, outcome_points, allowed_bookmakers=AU_BOOKMAKERS)
            if not books:
                continue
            au_fair_probs = edge.consensus_fair_probs(books)

            # Sharp reference chain: Pinnacle (lowest margin, industry
            # standard benchmark) first, then Betfair Exchange (two-sided
            # market, sharper than a fixed-odds book even though it's AU)
            # when Pinnacle doesn't cover this market/point. A cluster of
            # recreational AU books mostly just copies each other, which can
            # converge tightly on itself without being especially accurate --
            # only fall all the way back to that average if neither sharp
            # source quotes the exact same point as the AU consensus.
            sharp_fair_probs, sharp_source = {}, None
            for ref_bookmaker in (SHARP_REFERENCE_BOOKMAKER, SECONDARY_SHARP_REFERENCE_BOOKMAKER):
                prices = odds_api.single_bookmaker_prices(event, market, ref_bookmaker, outcome_points)
                if len(prices) >= 2:
                    sharp_fair_probs = devig_book(prices)
                    sharp_source = ref_bookmaker
                    break

            # Fair probability uses whichever sharp reference covered this
            # market/point, falling back to the AU bookmaker consensus; the
            # actual recommendation is restricted to accounts you hold, so
            # every tip is one you can actually go place.
            best_prices = odds_api.best_price_per_outcome(
                event, market, outcome_points, allowed_bookmakers=USER_BOOKMAKERS
            )
            if market == "h2h":
                h2h_best_prices = best_prices

            for outcome_name, price_info in best_prices.items():
                decimal_odds = price_info["price"]
                bookmaker = price_info["bookmaker"]
                point = price_info.get("point")
                fair_prob = sharp_fair_probs.get(outcome_name)
                fair_prob_source = sharp_source
                if fair_prob is None:
                    fair_prob = au_fair_probs.get(outcome_name)
                    fair_prob_source = "AU consensus"
                if fair_prob is None:
                    continue

                line_history_rows.append({
                    "sport": sport, "matchup": matchup, "game_date": game_date,
                    "market": market, "outcome": outcome_name, "point": point,
                    "fair_prob": fair_prob, "fair_prob_source": fair_prob_source,
                })

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
                        f"[{tier} edge · {risk}] No-vig fair probability ({fair_prob_source}): {fair_prob:.1%} "
                        f"vs price-implied {1/decimal_odds:.1%} ({devig_edge_pct:+.1%} edge) at {bookmaker}.",
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
                    if market == "h2h":
                        h2h_value_outcomes.add(outcome_name)

        # Prediction tips: no confirmed market edge, just "the model likes
        # this side" -- clearly a different, lower-confidence claim than a
        # value tip, so it's a separate tip_type with its own flat (smaller)
        # stake, never mixed into the value-tip count or sizing.
        if sport in PREDICTION_ELIGIBLE_SPORTS and model_probs:
            for outcome_name, model_prob in model_probs.items():
                if model_prob < PREDICTION_MIN_MODEL_PROB or outcome_name in h2h_value_outcomes:
                    continue
                price_info = h2h_best_prices.get(outcome_name)
                if not price_info:
                    continue
                decimal_odds = price_info["price"]
                if not (settings["min_single_odds"] <= decimal_odds <= settings["max_single_odds"]):
                    continue
                implied_prob = 1.0 / decimal_odds
                if model_prob < implied_prob:
                    continue  # model doesn't even think this price is fair, let alone a lean
                reasoning_parts = [
                    f"[Model prediction — no confirmed market edge] Elo gives {outcome_name} a "
                    f"{model_prob:.1%} chance, above the {implied_prob:.1%} the price implies, "
                    f"at {price_info['bookmaker']}. No cross-bookmaker price discrepancy was "
                    f"found here — this is a model lean, not detected market value, and Elo is "
                    f"noisiest early in a season.",
                ]
                if outcome_name in (home_odds_name, away_odds_name) and notes:
                    reasoning_parts.extend(notes)
                db.insert_tip({
                    "game_date": game_date,
                    "sport": sport,
                    "matchup": matchup,
                    "market": "h2h",
                    "selection": outcome_name,
                    "bookmaker": price_info["bookmaker"],
                    "odds_decimal": decimal_odds,
                    "model_prob": model_prob,
                    "market_fair_prob": None,
                    "edge_pct": None,
                    "suggested_units": PREDICTION_UNITS,
                    "tip_type": "prediction",
                    "reasoning": " ".join(reasoning_parts),
                })
                prediction_tips += 1
                game_had_value = True  # a prediction still counts as "something to show" for this game

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

    db.insert_line_history_batch(line_history_rows)

    log(
        f"[{sport}] done: {value_tips} value tips, {prediction_tips} prediction tips, "
        f"{knowledge_notes_logged} standalone knowledge notes, {len(line_history_rows)} "
        f"line snapshots recorded."
    )
    return {"value_tips": value_tips, "prediction_tips": prediction_tips, "knowledge_notes": knowledge_notes_logged}


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
