"""Daily pipeline: pull odds + schedule data, run the Elo model, find priced
edges, attach situational context, write everything to the tips table.

Run directly (`python -m tips.generate_tips [sport]`) or call run() from the
dashboard. Markets (h2h/spreads/totals) and whether a sport has a results
history (for Elo + ball-knowledge notes) are driven by config.SPORT_CONFIG --
see that file to add a new sport.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from config import (
    AU_BOOKMAKERS,
    DEFAULT_BANKROLL_AMOUNT,
    DEFAULT_MAX_SINGLE_ODDS,
    DEFAULT_MAX_UNITS_PER_BET,
    DEFAULT_MIN_EDGE_PCT,
    DEFAULT_MIN_SINGLE_ODDS,
    DEFAULT_UNIT_SIZE_PCT,
    EXTRA_EVENT_MARKETS_LOOKAHEAD_DAYS,
    SECONDARY_SHARP_REFERENCE_BOOKMAKER,
    SHARP_REFERENCE_BOOKMAKER,
    SPORT_CONFIG,
    TERTIARY_SHARP_REFERENCE_BOOKMAKER,
    USER_BOOKMAKERS,
)
from data import espn, espn_injuries, espn_player_stats, odds_api
from db import database as db
from knowledge import situational
from models import edge, elo
from models.devig import devig_book
from models.kelly import kelly_fraction
from models.scoring import PRIOR_SEASON_SCORING_WEIGHT, build_scoring_averages, injury_adjusted_avg, project_matchup
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
        "bankroll_amount": _get_float_setting("bankroll_amount", DEFAULT_BANKROLL_AMOUNT),
        "unit_size_pct": _get_float_setting("unit_size_pct", DEFAULT_UNIT_SIZE_PCT),
    }


# A longshot can carry a huge raw edge_pct purely because the model/sharp
# fair probability diverges a lot from a steep price -- that's a mispricing
# artifact, not the kind of "I'm confident about this" a Strong tier is
# meant to signal within the preferred odds range. Capped flat regardless
# of tier for anything priced above max_single_odds, per explicit feedback:
# "not gonna do high unit bets on high odd picks." Matches the Lean tier
# default (0.75) -- a longshot shouldn't outsize the lowest-confidence tier.
LONGSHOT_UNIT_CAP = 0.75

# Explicit feedback (2026-10-27): units don't need to land on round
# numbers like 1.5/2.5 any more -- size continuously, as long as it
# reflects both how likely the pick is to hit AND the market edge.
# Fractional Kelly (models/kelly.py) is literally that formula -- a
# function of fair probability (likelihood of hitting) and price (which,
# combined with fair probability, IS the edge) in one continuous number.
#
# This went through two earlier shapes the same day before landing here,
# both via a "fraction of full Kelly, linear, then a soft ceiling above
# some threshold" design -- quarter-Kelly (0.25) was cut to tenth-Kelly
# (0.10) because a normal ~12%-full-Kelly pick was pricing at/near the
# 3.0 ceiling pre-saturation. That fixed the top, but follow-up feedback
# ("you don't have many 2 unit bets, that's my most common size") traced
# to the SAME linear design from the other direction: pulled every real
# in-range value tip's actual numbers (tips.db, all statuses) through
# full-Kelly and found the typical detected edge is only ~1-8% full
# Kelly -- at 0.10 fraction that's 0.1-0.8 raw units, almost all of which
# the 0.5u floor was swallowing flat. Linear-then-saturate can't fix
# both ends with one fraction: whatever makes the top sane crushes the
# middle, and vice versa.
#
# Replaced the fraction+soft-ceiling with a single saturating curve
# across the WHOLE range instead of just above a threshold:
#   units = MIN_UNITS + (cap - MIN_UNITS) * x / (x + K)
# where x is full-Kelly % (always >= 0). Strictly increasing, approaches
# cap asymptotically (never pinned to it except for a genuinely enormous
# edge), and K alone controls where "typical" lands -- no fraction, no
# separate soft-ceiling ratio. CURVE_K = 5 was picked by checking it
# against that same real data: the cluster of ordinary in-range edges
# (~6-7% full Kelly) lands right at 2.0u, a thin edge (~1-2%) lands
# 1.0-1.25u, the friend's "normal" 12% case lands 2.25u (clearly below
# the ceiling, not pinned to it), and the two real historical outliers
# (27%, 37% full Kelly) land 2.5-2.75u -- rare, standout, differentiated,
# never quite hitting 3.0 short of a genuinely extreme edge.
MIN_UNITS = 0.5
CURVE_K = 5


def _round_to_quarter(units: float) -> float:
    """Explicit feedback (2026-10-27, follow-up): units should always end
    in .00/.25/.50/.75, not an arbitrary decimal like 2.66 -- still
    continuous in spirit (driven by the real Kelly/gap-ratio math, not a
    fixed tier table), just rounded to the nearest quarter-unit at the
    very end for a clean, easy-to-read number on the slip."""
    return round(units * 4) / 4


# Explicit feedback (2026-10-27, follow-up): 2.5u+ was showing up too
# often. Checked live: it was almost entirely Predictions (h2h Elo leans
# with no confirmed market edge) landing at exactly the same 3.0 ceiling
# as real value tips -- backwards, since a Prediction is explicitly the
# LOWER-confidence category. The scoring-margin leans already had their
# own lower cap (SCORING_LEAN_MAX_UNITS); this gives the Elo-based h2h
# leans the same discipline, via an optional override on the shared
# sizing function rather than a second copy of it.
PREDICTION_MAX_UNITS = 2.0

# Predictions get their own, higher K (slower to climb) rather than
# sharing CURVE_K -- their probability comes straight from the Elo model
# (model_prob), not from devigging a market consensus price like value
# tips do, and scripts/calibration.py's walk-forward backtest (re-run
# 2026-10-27) found real, confirmed overconfidence in NBL/NFL specifically
# in the 50-70% predicted-probability band -- the exact range most
# routine Predictions fall in. CALIBRATION_SHRINKAGE in models/elo.py
# already pulls these toward 50%, but it's a single constant fit on
# NBA's much larger sample and can only shrink toward "no opinion," not
# fix a case like NBL's where the model's pick wins LESS than half the
# time. Sizing Predictions more conservatively on top of the
# PREDICTION_MAX_UNITS cap above is the honest lever here: it can't
# correct which probability the model outputs, but it keeps a wrong one
# from being staked like a confirmed market edge. 15 was checked against
# real prediction data (tips.db) and keeps the existing, already-sane
# 0.5-2.0u spread rather than retargeting it the way CURVE_K was.
PREDICTION_CURVE_K = 15


def _kelly_units(
    fair_prob: float,
    decimal_odds: float,
    settings: dict,
    max_units_override: float | None = None,
    curve_k_override: float | None = None,
) -> float:
    cap = settings["max_units"] if max_units_override is None else min(settings["max_units"], max_units_override)
    k = CURVE_K if curve_k_override is None else curve_k_override
    full_kelly_pct = kelly_fraction(fair_prob, decimal_odds) * 100
    units = MIN_UNITS + (cap - MIN_UNITS) * full_kelly_pct / (full_kelly_pct + k)
    if decimal_odds > settings["max_single_odds"]:
        units = min(units, LONGSHOT_UNIT_CAP)
    return _round_to_quarter(max(MIN_UNITS, units))


# Model Elo is draw-aware for exactly zero of the sports configured so far --
# it only ever splits probability between home/away. For a 3-way sport
# (EPL has draws, ~25% of results) that overstates both teams' true win
# probability by ignoring the draw entirely. Fine as a soft sanity-check
# comparison (how it's used elsewhere), not fine as the basis for a
# standalone "the model likes this" recommendation -- so Prediction tips
# are restricted to sports where a binary win/loss split is a reasonable
# model of the market in the first place.
PREDICTION_ELIGIBLE_SPORTS = {"nba", "nbl", "nfl", "afl"}
# Both bars raised 2026-10-29 -- explicit feedback: "i dont want too many
# bets unless they are likely to hit or have good value... we dont need
# to do 12 bets a day... only do [it] if theres genuine reasoning."
# Checked the actual board that prompted this (10 NBA scoring-margin
# leans that night): every one of them cleared the OLD 1.5%-of-total gap
# bar, several by a wide margin, so it wasn't that weak picks were
# slipping through a broken threshold -- the bar itself was just set low
# enough to pass most of a big (17-game) slate. Doubling it to 3% was
# checked against that same real board: cuts the 4 weakest (which had
# cleared the old bar by as little as 1.57x) while keeping the 5
# genuinely strongest (2.19x-4.01x clear) -- a real quality cut, not an
# arbitrary number.
PREDICTION_MIN_MODEL_PROB = 0.60  # was 0.55 -- barely above a coin flip isn't "genuine reasoning"
# Real bug found 2026-10-31: the 0.60 bar above was checked against NBA's
# own calibration (CALIBRATION.md, 60-70% band: 63.1% actual, gap only
# -1.9% -- a "60%" NBA pick really does win about 60% of the time) and
# then applied globally to every sport in PREDICTION_ELIGIBLE_SPORTS
# without re-checking the others. They're not the same: NBL's own
# 60-70% band is 59.1% actual (gap -5.9%), NFL's is 55.6% (gap -9.4%),
# AFL's is 59.5% (gap -5.5%) -- a "60-65%" lean on any of these three is,
# empirically, close to a coin flip once you account for how overconfident
# that band already is. Confirmed live: the NBL h2h lean that went 0/3
# this week (New Zealand Breakers, model_prob 59.3%) sat exactly in this
# overconfident band. Each of these three sports' 70-80% band is much
# closer to (NBL: 72.5%, gap -2.5%) or better than (NFL: 85.7%, AFL:
# 88.5% -- small-ish samples, but consistently the safe direction to be
# wrong in) its claimed probability, so 0.70 is the calibration-backed
# bar for them specifically -- NBA keeps 0.60 since its own number holds up.
# Re-check against CALIBRATION.md after any Elo/data change.
PREDICTION_MIN_MODEL_PROB_OVERRIDES = {"nbl": 0.70, "nfl": 0.70, "afl": 0.70}
# How far the points-scored/allowed projection has to clear a spreads/totals
# line (as a fraction of the projected total) before it's worth a lean --
# same idea as a value tip's min-edge bar, just applied to a points gap
# instead of a probability gap. Not sport-specific (NBA totals ~220,
# NFL ~45) since it scales with the projection itself.
PREDICTION_LEAN_MIN_GAP_PCT = 0.03  # was 0.015, see comment above

# Real bug found 2026-10-29 ("why is everything 0.75 units"): the units
# formula reuses PREDICTION_LEAN_MIN_GAP_PCT as both the qualification
# bar AND the sizing scale (ratio = gap / threshold, fed into
# _scoring_lean_units). Doubling the bar above to cut volume silently
# also halved every surviving pick's ratio -- checked live, a genuine
# 10-point gap that would've sized at 2.0u under the old 1.5% threshold
# sized at 0.75u under the new 3% one, same actual edge, just a smaller
# number because the denominator changed. Raising the bar should only
# cut WHICH picks show, not recompute how confident the survivors look.
# This constant decouples the two: qualification still uses the higher
# PREDICTION_LEAN_MIN_GAP_PCT, sizing always scales against this
# original, lower reference regardless of where the bar sits.
SCORING_LEAN_SIZING_REF_PCT = 0.015

# The scoring-margin model (spreads/totals leans) has no clean win
# probability to feed into Kelly -- just a points gap over the min-gap
# bar. This is a continuous proxy for the same idea: scales smoothly
# with how many multiples of that bar the projection clears, rather than
# snapping to a 3-bucket label. Capped below a real value tip's ceiling
# on purpose -- this is explicitly a rougher model (see its own
# reasoning text), shouldn't out-stake a genuine detected market edge.
SCORING_LEAN_MAX_UNITS = 2.0
SCORING_LEAN_RATIO_CEILING = 3.0  # ratio at/above which it maxes out


def _scoring_lean_units(gap: float, threshold: float) -> float:
    if threshold <= 0:
        return MIN_UNITS
    ratio = gap / threshold
    if ratio <= 1.0:
        return MIN_UNITS
    if ratio >= SCORING_LEAN_RATIO_CEILING:
        return SCORING_LEAN_MAX_UNITS
    span = SCORING_LEAN_RATIO_CEILING - 1.0
    units = MIN_UNITS + (ratio - 1.0) / span * (SCORING_LEAN_MAX_UNITS - MIN_UNITS)
    return _round_to_quarter(units)


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


def _local_game_datetime(commence_time_iso: str) -> tuple[str, str]:
    """
    The Odds API's commence_time is UTC. AEDT is UTC+11, so a game at, say,
    9am UTC is already 8pm *tonight* in Sydney -- taking the UTC date
    directly would label it with what's still "today" in UTC but could be
    "tomorrow" locally (or vice versa near local midnight, when UTC is
    still on the previous day). Convert to AU local time before taking the
    date, so "today" in the dashboard means what an AU user means by it.
    Returns (ISO date, local kickoff/tip-off time like "7:30 pm").
    """
    dt_utc = datetime.fromisoformat(commence_time_iso.replace("Z", "+00:00"))
    dt_local = dt_utc.astimezone(AU_TZ)
    return dt_local.date().isoformat(), dt_local.strftime("%-I:%M %p").lower()


def _format_selection(outcome_name: str, point, market_key: str) -> str:
    if point is None:
        return outcome_name
    if market_key == "totals":
        return f"{outcome_name} {point:g}"
    return f"{outcome_name} {point:+g}"


# Added 2026-10-29: only "Out" counts here, not "Doubtful" -- a Doubtful
# player has a real chance of suiting up, and this feeds an actual number
# into the scoring projection (models.scoring.injury_adjusted_avg), unlike
# the notes/why-facts text elsewhere which is purely descriptive. Caches
# by athlete_id across the whole run (cache passed in, same pattern as
# injury_cache in run()) so the same injured player across multiple games
# in one pipeline run only costs one ESPN call.
def _out_player_ppgs(injuries: list[dict], cfg: dict, cache: dict) -> list[float]:
    ppgs = []
    for inj in injuries:
        if inj.get("status") != "Out" or not inj.get("athlete_id"):
            continue
        aid = inj["athlete_id"]
        if aid not in cache:
            cache[aid] = espn_player_stats.get_player_ppg(cfg["espn_sport"], cfg["espn_league"], aid)
        ppg = cache[aid]
        if ppg is not None:
            ppgs.append(ppg)
    return ppgs


# Went through three lengths the same week before landing here: multi-
# paragraph with full injury citations (too long) -> one plain sentence
# only (too short, follow-up feedback: "it can legit be 3-4 sentences...
# just make sure its concise") -> this: up to 2 plain-English facts
# (situational.plain_why_facts, number-free by construction) plus one
# short quantitative detail sentence plus, for Predictions only, a short
# disclaimer -- 2-4 tight sentences depending on tip type, none of them
# a multi-clause run-on. Doesn't fabricate a second fact when there's
# only one real one to report.
def _plain_why(why_facts: list[str], fallback: str, max_facts: int = 2) -> str:
    if why_facts:
        sentences = [f"{f[0].upper()}{f[1:]}." for f in why_facts[:max_facts]]
        return " ".join(sentences)
    return fallback


_VALUE_WHY_FALLBACK = (
    "The price here just doesn't match how these two sides actually stack up -- "
    "gaps like that don't usually last long once enough money notices."
)
_PREDICTION_H2H_WHY_FALLBACK = (
    "Our model simply rates this side a little higher than the market does right now "
    "-- an early read, not a confirmed edge."
)
_PREDICTION_SCORING_WHY_FALLBACK = (
    "Our scoring model sees this game playing out a little differently than the number "
    "on the board suggests."
)

# Quantitative follow-up sentence, one per tip type -- the plain-English
# why above explains it in words, this gives the actual number for
# whoever wants it, same spirit as before the 2026-10-29 "too long" cut,
# just kept to one tight sentence each rather than a longer paragraph.
def _value_detail(fair_prob: float, decimal_odds: float, bookmaker: str) -> str:
    implied_prob = 1.0 / decimal_odds
    return (
        f"About a {fair_prob:.0%} chance this comes in, but {bookmaker} only needs "
        f"{implied_prob:.0%} for it to pay off -- that gap's the edge."
    )


def _h2h_lean_detail(outcome_name: str, model_prob: float) -> str:
    return f"Our model gives {outcome_name} about {model_prob:.0%} to win, a touch better than the price suggests."


def _spreads_lean_detail(selection: str, home_projected: float, away_projected: float) -> str:
    return (
        f"Our scoring model expects this one to land around {home_projected:.0f}-{away_projected:.0f} "
        f"-- enough to cover {selection}."
    )


def _totals_lean_detail(projected_total: float, point: float, wants_over: bool) -> str:
    direction = "clearing" if wants_over else "staying under"
    return f"Our scoring model expects around {projected_total:.0f} total points -- {direction} the {point:g} line here."


# Short on purpose -- the old version of this one sentence was itself a
# big chunk of why descriptions felt long. A Prediction already reads
# differently from a Value tip (its own badge says "Model confidence"
# instead of a tier/edge%), so this just needs to say "not confirmed",
# not re-explain the whole concept every time.
_PREDICTION_DISCLAIMER = "Model lean, not a confirmed market edge."

# Explicit feedback (2026-10-29): "i want the bets to show lines that are
# predicted to change... post those bets too if theres a chance it
# changes... only do if theres value tho." Deliberately NOT a new, separate
# trigger -- that would mean recommending a bet purely because a number
# moved, with no model behind it. Instead this only enriches a
# spreads/totals Prediction that ALREADY cleared the real gap bar: using
# models/line_history (already recorded every refresh, previously only
# used for a since-removed "Line Movement" UI section), check whether the
# market's own number has been drifting the same direction our model
# already leans -- i.e. today's gap against our projection is bigger than
# it was a few days ago at the earliest recorded point. Sign-agnostic by
# design: recompute_gap takes an arbitrary point and reuses the caller's
# own gap formula (spreads and totals have different sign conventions,
# this sidesteps needing to get both right by hand). MIN_MOVE guards
# against noise from a half-point line-shopping wobble that isn't a real
# trend.
_LINE_MOMENTUM_MIN_MOVE = 1.0


def _line_momentum_fact(
    sport: str, matchup: str, game_date: str, market: str, outcome: str,
    current_point: float, current_gap: float, recompute_gap,
) -> str | None:
    history = db.get_line_history(sport, matchup, game_date, market, outcome)
    if len(history) < 2:
        return None
    first_point = history[0]["point"]
    if first_point is None or abs(current_point - first_point) < _LINE_MOMENTUM_MIN_MOVE:
        return None
    historical_gap = recompute_gap(first_point)
    if current_gap <= historical_gap:
        return None
    return f"This line's been drifting our way -- {first_point:g} a few days ago, {current_point:g} now."


def _process_extra_event_markets(sport: str, cfg: dict, settings: dict, log) -> tuple[int, list[dict]]:
    """
    Markets not on the bulk /odds endpoint (e.g. btts) -- one API call per
    event instead of one per sport, so restricted to events within
    EXTRA_EVENT_MARKETS_LOOKAHEAD_DAYS to keep the extra quota cost bounded.
    Same devig/edge/tier/sizing pipeline as the main markets, just driven
    off a per-event fetch instead of the bulk one. Not wired into the
    Ball Knowledge linked_bet mechanism (that's scoped to the main per-
    event loop) -- these just land as their own standalone value tips.
    Returns (value_tips_count, line_history_rows).
    """
    extra_markets = cfg.get("extra_event_markets") or []
    if not extra_markets:
        return 0, []

    events = odds_api.fetch_events(cfg["odds_key"])
    cutoff = datetime.now(timezone.utc) + timedelta(days=EXTRA_EVENT_MARKETS_LOOKAHEAD_DAYS)
    near_term = [
        e for e in events
        if datetime.fromisoformat(e["commence_time"].replace("Z", "+00:00")) <= cutoff
    ]
    log(f"[{sport}] pulling extra markets ({', '.join(extra_markets)}) for "
        f"{len(near_term)} near-term events (of {len(events)} total)...")

    value_tips = 0
    line_history_rows = []
    for event in near_term:
        matchup = f"{event['away_team']} @ {event['home_team']}"
        game_date, game_time_local = _local_game_datetime(event["commence_time"])
        for market in extra_markets:
            # Real bug (2026-10-30): a single transient network error here
            # (a plain ConnectionResetError, not even an Odds API-specific
            # failure) used to propagate all the way up and abort the WHOLE
            # sport's refresh, discarding this function's progress on every
            # other event -- confirmed live, this is exactly what silently
            # left EPL stale for several days despite the scheduled refresh
            # reporting "success" (run_auto_refresh.py's outer per-sport
            # catch only logs it, doesn't fail the job). One flaky per-event
            # call shouldn't cost every other event's BTTS check.
            try:
                data = odds_api.fetch_event_odds(cfg["odds_key"], event["id"], market)
            except Exception as e:
                log(f"[{sport}] {market} fetch failed for {matchup}, skipping this one: {e}")
                continue
            outcome_points = odds_api.consensus_points(data, market, allowed_bookmakers=AU_BOOKMAKERS)
            books = odds_api.bookmaker_books(data, market, outcome_points, allowed_bookmakers=AU_BOOKMAKERS)
            if not books:
                continue
            au_fair_probs = edge.consensus_fair_probs(books)
            best_prices = odds_api.best_price_per_outcome(
                data, market, outcome_points, allowed_bookmakers=USER_BOOKMAKERS
            )
            for outcome_name, price_info in best_prices.items():
                decimal_odds = price_info["price"]
                bookmaker = price_info["bookmaker"]
                point = price_info.get("point")
                fair_prob = au_fair_probs.get(outcome_name)
                if fair_prob is None:
                    continue

                line_history_rows.append({
                    "sport": sport, "matchup": matchup, "game_date": game_date,
                    "market": market, "outcome": outcome_name, "point": point,
                    "fair_prob": fair_prob, "fair_prob_source": "AU consensus",
                })

                devig_edge_pct = edge.edge_pct(fair_prob, decimal_odds)
                if devig_edge_pct < _effective_min_edge(decimal_odds, market, settings["min_edge_pct"]):
                    continue
                units = _kelly_units(fair_prob, decimal_odds, settings)
                reasoning = f"{_VALUE_WHY_FALLBACK} {_value_detail(fair_prob, decimal_odds, bookmaker)}"
                db.insert_tip({
                    "game_date": game_date,
                    "game_time_local": game_time_local,
                    "sport": sport,
                    "matchup": matchup,
                    "market": market,
                    "selection": _format_selection(outcome_name, point, market),
                    "bookmaker": bookmaker,
                    "odds_decimal": decimal_odds,
                    "model_prob": None,
                    "market_fair_prob": fair_prob,
                    "edge_pct": devig_edge_pct,
                    "suggested_units": units,
                    "tip_type": "value",
                    "reasoning": reasoning,
                })
                value_tips += 1
    return value_tips, line_history_rows


def run(sport: str = "nba", log=print) -> dict:
    db.init_db()
    settings = _load_staking_settings()
    cfg = SPORT_CONFIG[sport]

    ratings, team_logs, nickname_index, canonical_names, scoring_averages = {}, {}, {}, [], {}
    team_id_map: dict[str, str] = {}
    injury_cache: dict[str, list] = {}  # team_id -> injuries, reused across every event this run
    player_ppg_cache: dict[str, float | None] = {}  # athlete_id -> season PPG, reused across every event this run
    if cfg["has_history"]:
        log(f"[{sport}] pulling finished games (ESPN) to train Elo ratings...")
        finished_games = _finished_games_for_elo(sport, cfg)
        ratings = elo.train_from_games(finished_games)
        db.save_elo_ratings(sport, ratings)
        team_logs = situational.build_team_game_log(finished_games)
        canonical_names = list(ratings.keys())
        nickname_index = build_nickname_index(canonical_names)
        # Same finished_games Elo already trains on -- no extra ESPN call.
        # Points-scored/allowed averages, used to project a likely margin/
        # total for spreads/totals model leans (Elo only predicts win/loss).
        # Prior season discounted (see PRIOR_SEASON_SCORING_WEIGHT) so a
        # team's current-season scoring isn't drowned out by a full season
        # of stale games -- the exact bug that sized two bad totals leans
        # this week (Heat/Hawks, Kings/Pelicans both missed high).
        prior_season_year = _espn_season_years(sport)[0]
        scoring_averages = build_scoring_averages(
            finished_games, season_weights={prior_season_year: PRIOR_SEASON_SCORING_WEIGHT}
        )
        team_id_map = espn.get_team_id_map(cfg["espn_sport"], cfg["espn_league"])
        log(f"[{sport}] trained Elo for {len(ratings)} teams from {len(finished_games)} games.")
    else:
        log(f"[{sport}] no results history wired up yet -- devig value tips only, "
            f"no Elo context or ball-knowledge notes for this sport.")

    log(f"[{sport}] pulling live odds ({', '.join(cfg['markets'])})...")
    events = odds_api.fetch_odds(cfg["odds_key"], markets=",".join(cfg["markets"]))
    log(f"[{sport}] {len(events)} upcoming games with odds.")

    # Only supersede this sport's previously-open tips once fresh odds are
    # actually in hand -- not at the top of run(), before fetch_odds() can
    # still raise (quota exhausted, API down). That ordering bug cleared
    # every sport's standing tips on a quota-exhausted run with nothing to
    # replace them, contradicting run_auto_refresh.py's own "leaving its
    # existing tips as-is" comment on a caught failure. Every upcoming game
    # still gets re-evaluated on each daily refresh (and each manual
    # Refresh click) -- insert_tip only ever adds, so the same game would
    # pile up duplicate/stale rows without this. Already-played games are
    # untouched either way (history).
    db.supersede_open_tips(sport, datetime.now(AU_TZ).date().isoformat())

    value_tips, prediction_tips, knowledge_notes_logged = 0, 0, 0
    line_history_rows = []  # every outcome's fair prob/point this run, for future line-movement detection

    for event in events:
        home_odds_name = event["home_team"]
        away_odds_name = event["away_team"]
        game_date, game_time_local = _local_game_datetime(event["commence_time"])
        matchup = f"{away_odds_name} @ {home_odds_name}"

        model_probs = {}
        notes = []
        why_facts = []
        scoring_projection = None
        linked_bet = None  # best priced tip found for this game, attached to its Ball Knowledge note
        if cfg["has_history"]:
            home_team = resolve(home_odds_name, canonical_names, nickname_index) or home_odds_name
            away_team = resolve(away_odds_name, canonical_names, nickname_index) or away_odds_name
            home_elo = ratings.get(home_team, elo.BASE_RATING)
            away_elo = ratings.get(away_team, elo.BASE_RATING)
            model_prob_home = elo.win_probability(home_elo, away_elo)
            model_probs = {home_odds_name: model_prob_home, away_odds_name: 1 - model_prob_home}
            game_date_obj = date.fromisoformat(game_date)
            # notes feeds Ball Knowledge cards only (knowledge_notes below) --
            # explicit feedback (2026-10-29) that Value/Prediction card
            # descriptions were "too complicated" led to dropping the full
            # notes dump (every situational note plus every injury's full
            # citation text) from their reasoning. _plain_why already surfaces
            # the single most relevant fact in one plain sentence; the rest was
            # redundant clutter on a card meant to be acted on, not read like
            # a news digest. Don't re-add `reasoning_parts.extend(notes)` to
            # those paths without re-raising this with the user first.
            notes = situational.generate_notes(home_team, away_team, game_date_obj, team_logs)
            home_injuries, away_injuries = [], []
            for team_name in (home_team, away_team):
                team_id = team_id_map.get(team_name)
                if not team_id:
                    continue
                if team_id not in injury_cache:
                    injury_cache[team_id] = espn_injuries.get_team_injuries(
                        cfg["espn_sport"], cfg["espn_league"], team_id
                    )
                notes.extend(situational.injury_notes(team_name, injury_cache[team_id]))
                if team_name == home_team:
                    home_injuries = injury_cache[team_id]
                else:
                    away_injuries = injury_cache[team_id]
            why_facts = situational.plain_why_facts(
                home_team, away_team, game_date_obj, team_logs, home_injuries, away_injuries
            )

            home_avg_adj = injury_adjusted_avg(
                scoring_averages.get(home_team),
                _out_player_ppgs(home_injuries, cfg, player_ppg_cache),
            )
            away_avg_adj = injury_adjusted_avg(
                scoring_averages.get(away_team),
                _out_player_ppgs(away_injuries, cfg, player_ppg_cache),
            )
            scoring_projection = project_matchup(home_avg_adj, away_avg_adj)
            # Real bug found 2026-10-31: 9 of 10 NBA totals leans on one
            # slate came out Under, every one citing injuries, with gaps
            # (6.5-12.5pts) matching almost exactly what the injury math
            # alone would subtract. The problem: a market line is itself
            # set (and moves) with full knowledge of who's out -- if this
            # adjustment is the ONLY reason a gap clears the bar, the
            # "edge" is just re-deriving public injury news the book has
            # already priced in, not a genuine model disagreement.
            # scoring_projection_raw (no injury subtraction) lets the gate
            # below require the gap to be real on its own terms too --
            # injury adjustment can still SUPPRESS a pick (its original,
            # validated use: Kings missing 4 players, 2026-10-28), it just
            # can no longer be the sole source of one qualifying.
            scoring_projection_raw = project_matchup(
                scoring_averages.get(home_team), scoring_averages.get(away_team)
            )

        h2h_best_prices, h2h_value_outcomes = {}, set()
        spreads_best_prices, totals_best_prices = {}, {}
        spreads_value_outcomes, totals_value_outcomes = set(), set()
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
            # standard benchmark) first, then Betfair Exchange, then
            # Matchbook (both two-sided markets, sharper than a fixed-odds
            # book even though AU/EU-based) when Pinnacle doesn't cover this
            # market/point. A cluster of recreational AU books mostly just
            # copies each other, which can converge tightly on itself
            # without being especially accurate -- only fall all the way
            # back to that average if none of the three sharp sources quote
            # the exact same point as the AU consensus.
            sharp_fair_probs, sharp_source = {}, None
            for ref_bookmaker in (
                SHARP_REFERENCE_BOOKMAKER,
                SECONDARY_SHARP_REFERENCE_BOOKMAKER,
                TERTIARY_SHARP_REFERENCE_BOOKMAKER,
            ):
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
            elif market == "spreads":
                spreads_best_prices = best_prices
            elif market == "totals":
                totals_best_prices = best_prices

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
                    units = _kelly_units(fair_prob, decimal_odds, settings)
                    reasoning_parts = [
                        _plain_why(why_facts, _VALUE_WHY_FALLBACK),
                        _value_detail(fair_prob, decimal_odds, bookmaker),
                    ]

                    # Highest-edge value tip on this game becomes the bet shown
                    # alongside its Ball Knowledge note below -- so the card with
                    # the situational context also tells you what to actually bet.
                    # Restricted to the same single-odds range as everywhere else
                    # (1.6-2.2 by default): a longshot value tip still gets
                    # generated and shown in its own tab's collapsed "outside
                    # range" expander, but shouldn't become the headline bet on
                    # a Ball Knowledge card, which has no such expander to hide it in.
                    in_preferred_range = settings["min_single_odds"] <= decimal_odds <= settings["max_single_odds"]
                    if in_preferred_range and (linked_bet is None or devig_edge_pct > linked_bet["edge_pct"]):
                        linked_bet = {
                            "market": market,
                            "selection": _format_selection(outcome_name, point, market),
                            "bookmaker": bookmaker,
                            "odds_decimal": decimal_odds,
                            "suggested_units": units,
                            "edge_pct": devig_edge_pct,
                        }

                    db.insert_tip({
                        "game_date": game_date,
                        "game_time_local": game_time_local,
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
                    if market == "h2h":
                        h2h_value_outcomes.add(outcome_name)
                    elif market == "spreads":
                        spreads_value_outcomes.add(outcome_name)
                    elif market == "totals":
                        totals_value_outcomes.add(outcome_name)

        # Prediction tips: no confirmed market edge, just "the model likes
        # this side" -- clearly a different, lower-confidence claim than a
        # value tip, so it's a separate tip_type with its own flat (smaller)
        # stake, never mixed into the value-tip count or sizing.
        if sport in PREDICTION_ELIGIBLE_SPORTS and model_probs:
            min_model_prob = PREDICTION_MIN_MODEL_PROB_OVERRIDES.get(sport, PREDICTION_MIN_MODEL_PROB)
            for outcome_name, model_prob in model_probs.items():
                if model_prob < min_model_prob or outcome_name in h2h_value_outcomes:
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
                pred_units = _kelly_units(
                    model_prob, decimal_odds, settings,
                    max_units_override=PREDICTION_MAX_UNITS,
                    curve_k_override=PREDICTION_CURVE_K,
                )
                reasoning_parts = [
                    _plain_why(why_facts, _PREDICTION_H2H_WHY_FALLBACK),
                    _h2h_lean_detail(outcome_name, model_prob),
                    _PREDICTION_DISCLAIMER,
                ]
                db.insert_tip({
                    "game_date": game_date,
                    "game_time_local": game_time_local,
                    "sport": sport,
                    "matchup": matchup,
                    "market": "h2h",
                    "selection": outcome_name,
                    "bookmaker": price_info["bookmaker"],
                    "odds_decimal": decimal_odds,
                    "model_prob": model_prob,
                    "market_fair_prob": None,
                    "edge_pct": None,
                    "suggested_units": pred_units,
                    "tip_type": "prediction",
                    "reasoning": " ".join(reasoning_parts),
                })
                prediction_tips += 1
                # Only fall back to a prediction as the linked bet when no
                # actual value tip was found on this game -- a verified edge
                # always outranks a model lean as "the bet to show".
                if linked_bet is None:
                    linked_bet = {
                        "market": "h2h",
                        "selection": outcome_name,
                        "bookmaker": price_info["bookmaker"],
                        "odds_decimal": decimal_odds,
                        "suggested_units": pred_units,
                        "edge_pct": -1.0,
                    }

        # Scoring-projection leans (spreads/totals): same "model lean, no
        # confirmed market edge" idea as the h2h Predictions above, but
        # using the points-scored/allowed projection instead of Elo -- Elo
        # only outputs win probability, it has nothing to say about margin
        # or total points, so this is the spreads/totals equivalent.
        if scoring_projection:
            for outcome_name, price_info in spreads_best_prices.items():
                if outcome_name in spreads_value_outcomes:
                    continue
                point = price_info.get("point")
                if point is None:
                    continue
                if outcome_name == home_odds_name:
                    team_margin = scoring_projection["projected_margin"]
                    team_margin_raw = scoring_projection_raw["projected_margin"] if scoring_projection_raw else None
                elif outcome_name == away_odds_name:
                    team_margin = -scoring_projection["projected_margin"]
                    team_margin_raw = -scoring_projection_raw["projected_margin"] if scoring_projection_raw else None
                else:
                    continue
                gap = team_margin + point  # how much the model has this side covering by
                threshold = scoring_projection["projected_total"] * PREDICTION_LEAN_MIN_GAP_PCT
                if gap < threshold:
                    continue
                # See scoring_projection_raw's comment above -- injury
                # adjustment can suppress, not manufacture, a qualifying gap.
                if team_margin_raw is None or (team_margin_raw + point) < threshold:
                    continue
                decimal_odds = price_info["price"]
                if not (settings["min_single_odds"] <= decimal_odds <= settings["max_single_odds"]):
                    continue
                sizing_threshold = scoring_projection["projected_total"] * SCORING_LEAN_SIZING_REF_PCT
                pred_units = _scoring_lean_units(gap, sizing_threshold)
                if decimal_odds > settings["max_single_odds"]:
                    pred_units = min(pred_units, LONGSHOT_UNIT_CAP)
                selection = _format_selection(outcome_name, point, "spreads")
                momentum_fact = _line_momentum_fact(
                    sport, matchup, game_date, "spreads", outcome_name,
                    point, gap, lambda p: team_margin + p,
                )
                reasoning_parts = [
                    _plain_why(why_facts, _PREDICTION_SCORING_WHY_FALLBACK),
                    _spreads_lean_detail(
                        selection,
                        scoring_projection["home_projected"], scoring_projection["away_projected"],
                    ),
                ]
                if momentum_fact:
                    reasoning_parts.append(momentum_fact)
                reasoning_parts.append(_PREDICTION_DISCLAIMER)
                db.insert_tip({
                    "game_date": game_date,
                    "game_time_local": game_time_local,
                    "sport": sport,
                    "matchup": matchup,
                    "market": "spreads",
                    "selection": selection,
                    "bookmaker": price_info["bookmaker"],
                    "odds_decimal": decimal_odds,
                    "model_prob": None,
                    "market_fair_prob": None,
                    "edge_pct": None,
                    "suggested_units": pred_units,
                    "tip_type": "prediction",
                    "reasoning": " ".join(reasoning_parts),
                })
                prediction_tips += 1
                if linked_bet is None:
                    linked_bet = {
                        "market": "spreads", "selection": selection,
                        "bookmaker": price_info["bookmaker"], "odds_decimal": decimal_odds,
                        "suggested_units": pred_units, "edge_pct": -1.0,
                    }

            for outcome_name, price_info in totals_best_prices.items():
                if outcome_name in totals_value_outcomes or outcome_name.lower() not in ("over", "under"):
                    continue
                point = price_info.get("point")
                if point is None:
                    continue
                gap_over = scoring_projection["projected_total"] - point
                threshold = scoring_projection["projected_total"] * PREDICTION_LEAN_MIN_GAP_PCT
                wants_over = outcome_name.lower() == "over"
                gap = gap_over if wants_over else -gap_over
                if gap < threshold:
                    continue
                # See scoring_projection_raw's comment above -- injury
                # adjustment can suppress, not manufacture, a qualifying gap.
                if scoring_projection_raw is None:
                    continue
                gap_over_raw = scoring_projection_raw["projected_total"] - point
                gap_raw = gap_over_raw if wants_over else -gap_over_raw
                if gap_raw < threshold:
                    continue
                decimal_odds = price_info["price"]
                if not (settings["min_single_odds"] <= decimal_odds <= settings["max_single_odds"]):
                    continue
                sizing_threshold = scoring_projection["projected_total"] * SCORING_LEAN_SIZING_REF_PCT
                pred_units = _scoring_lean_units(gap, sizing_threshold)
                if decimal_odds > settings["max_single_odds"]:
                    pred_units = min(pred_units, LONGSHOT_UNIT_CAP)
                selection = _format_selection(outcome_name, point, "totals")
                projected_total = scoring_projection["projected_total"]
                momentum_fact = _line_momentum_fact(
                    sport, matchup, game_date, "totals", outcome_name,
                    point, gap,
                    lambda p: (projected_total - p) if wants_over else (p - projected_total),
                )
                reasoning_parts = [
                    _plain_why(why_facts, _PREDICTION_SCORING_WHY_FALLBACK),
                    _totals_lean_detail(scoring_projection["projected_total"], point, wants_over),
                ]
                if momentum_fact:
                    reasoning_parts.append(momentum_fact)
                reasoning_parts.append(_PREDICTION_DISCLAIMER)
                db.insert_tip({
                    "game_date": game_date,
                    "game_time_local": game_time_local,
                    "sport": sport,
                    "matchup": matchup,
                    "market": "totals",
                    "selection": selection,
                    "bookmaker": price_info["bookmaker"],
                    "odds_decimal": decimal_odds,
                    "model_prob": None,
                    "market_fair_prob": None,
                    "edge_pct": None,
                    "suggested_units": pred_units,
                    "tip_type": "prediction",
                    "reasoning": " ".join(reasoning_parts),
                })
                prediction_tips += 1
                if linked_bet is None:
                    linked_bet = {
                        "market": "totals", "selection": selection,
                        "bookmaker": price_info["bookmaker"], "odds_decimal": decimal_odds,
                        "suggested_units": pred_units, "edge_pct": -1.0,
                    }

        # Always log situational notes as their own Ball Knowledge entry, even
        # when this game also produced a Value/Prediction tip (whose reasoning
        # already includes the same notes) -- Ball Knowledge is meant to be a
        # complete feed of context, not just the leftover games that didn't
        # get a priced pick. An earlier version skipped this whenever
        # game_had_value was True, which made the tab look far sparser than
        # the actual situational-note coverage really was.
        #
        # Odds/units/bookmaker: attach linked_bet (the best value tip on this
        # game, or failing that the prediction tip) when one exists, so the
        # card tells you what to actually bet alongside the context -- not
        # just description.
        #
        # Final fallback when neither exists (routine for EPL: h2h favourites
        # price close to efficient, and AU bookmakers don't post Line/Totals
        # for EPL through this data source at all -- see README): still show
        # the home side's current best market price as plain reference, so
        # the card isn't left with no odds/unit/app just because nothing
        # cleared the value/prediction bar. Flagged in the note text itself
        # (not a schema change) so it reads as context, not a recommendation
        # -- and kept to a flat, small unit, same longshot cap as real picks.
        reference_price_used = False
        if linked_bet is None:
            ref_price = h2h_best_prices.get(home_odds_name)
            if ref_price:
                linked_bet = {
                    "market": "h2h",
                    "selection": home_odds_name,
                    "bookmaker": ref_price["bookmaker"],
                    "odds_decimal": ref_price["price"],
                    "suggested_units": LONGSHOT_UNIT_CAP,
                }
                reference_price_used = True

        if notes:
            knowledge_notes = list(notes)
            if reference_price_used:
                knowledge_notes.append(
                    "No pick here -- just showing the current best price for context."
                )
            db.insert_tip({
                "game_date": game_date,
                "game_time_local": game_time_local,
                "sport": sport,
                "matchup": matchup,
                "market": linked_bet["market"] if linked_bet else "h2h",
                "selection": linked_bet["selection"] if linked_bet else "—",
                "bookmaker": linked_bet["bookmaker"] if linked_bet else None,
                "odds_decimal": linked_bet["odds_decimal"] if linked_bet else None,
                "model_prob": model_probs.get(home_odds_name),
                "market_fair_prob": None,
                "edge_pct": None,
                "suggested_units": linked_bet["suggested_units"] if linked_bet else 0,
                "tip_type": "knowledge",
                "reasoning": " ".join(knowledge_notes),
            })
            knowledge_notes_logged += 1

    extra_value_tips, extra_line_history_rows = _process_extra_event_markets(sport, cfg, settings, log)
    value_tips += extra_value_tips
    line_history_rows.extend(extra_line_history_rows)

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
