import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _get_secret(key: str, default: str = "") -> str:
    """Env var locally (.env); Streamlit Cloud's Secrets manager when deployed there."""
    try:
        import streamlit as st

        if key in st.secrets:
            return str(st.secrets[key])
    except Exception:
        pass
    return os.getenv(key, default)


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "tips.db"

ODDS_API_KEY = _get_secret("ODDS_API_KEY", "")
# Optional second (third, ...) free-tier key -- data/odds_api.py falls over
# to the next one automatically once a key's monthly quota is exhausted.
# Unset by default; harmless if never configured (ODDS_API_KEYS below just
# ends up a single-key list, identical to the old behaviour).
ODDS_API_KEY_2 = _get_secret("ODDS_API_KEY_2", "")
ODDS_API_KEYS = [k for k in (ODDS_API_KEY, ODDS_API_KEY_2) if k]

DEFAULT_BANKROLL_AMOUNT = float(_get_secret("BANKROLL_AMOUNT", "1000"))
DEFAULT_UNIT_SIZE_PCT = float(_get_secret("UNIT_SIZE_PCT", "0.01"))
DEFAULT_MAX_UNITS_PER_BET = float(_get_secret("MAX_UNITS_PER_BET", "3"))
DEFAULT_MIN_EDGE_PCT = float(_get_secret("MIN_EDGE_PCT", "0.0075"))

# Stake size by edge tier (see models.edge.tier_label) -- a round, fixed
# unit count per tier rather than a continuously-scaled Kelly fraction.
# Bigger and more intuitive than "0.39 units", still grounded in how
# confident the edge is. Each is capped by max_units_per_bet regardless.
# These three are the tier's "Neutral" risk-band midpoint, not the final
# number shown -- tips/generate_tips.RISK_UNIT_MULTIPLIER scales each one
# up or down from here based on win-probability band too (see
# models.risk.risk_label), spreading the real range to roughly 0.5-3.0.
DEFAULT_TIER_UNITS_STRONG = float(_get_secret("TIER_UNITS_STRONG", "2.5"))
DEFAULT_TIER_UNITS_MODERATE = float(_get_secret("TIER_UNITS_MODERATE", "1.75"))
# Explicit feedback: usually prefers 1-2u for most bets (Moderate=1.75 already
# fits), 2.5-3u only for very confident/big-edge picks (Strong=2.5 already
# fits) -- 0.75 is fine for Lean specifically ("0.5 and 0.75 i dont mind"),
# since that's the lowest-confidence tier and should sit below the 1-2u norm.
DEFAULT_TIER_UNITS_LEAN = float(_get_secret("TIER_UNITS_LEAN", "0.75"))

# Hard range on single-bet odds shown in the main "Value Bets"/"Predictions"
# views -- not a soft discount zone, an actual floor and ceiling. Tips
# outside this range still get generated and stored (useful data, e.g. for
# Performance history), they just don't show in the default feed -- tucked
# into a collapsed, clearly-labelled section instead.
DEFAULT_MIN_SINGLE_ODDS = float(_get_secret("MIN_SINGLE_ODDS", "1.6"))
DEFAULT_MAX_SINGLE_ODDS = float(_get_secret("MAX_SINGLE_ODDS", "2.2"))

# Markets only available per-event (not the bulk /odds endpoint, e.g. btts)
# cost quota per game, not once per sport -- restricting how far ahead we
# bother pulling them keeps that cost bounded. Lowered from 7 to 3
# (2026-10-27) after measuring the real daily cost of a full refresh
# across NBA/EPL/NBL/NFL: 34 credits/day confirmed from live credit
# counts (6 each for the four bulk pulls + 10 for EPL BTTS at a 7-day
# window, ~10 games/week since the same game gets re-pulled on every day
# it sits inside the window, not just once). 34/day x 30 = 1020/month,
# which is OVER the 1000/month two free-tier keys provide -- no safety
# margin at all. A 3-day window cuts BTTS to roughly 4-5 events/day
# instead of ~10 (games are spread across the week), bringing the daily
# total down to ~28-29 and the monthly total comfortably under 1000.
# Costs a bit of lead time on BTTS specifically (found 3 days out instead
# of a full week), not the other markets.
EXTRA_EVENT_MARKETS_LOOKAHEAD_DAYS = 3

# Odds API regions -- 'au' is the AU-licensed bookmakers you can actually bet
# at. 'eu' is added to also pull Pinnacle -- a sharp, low-margin reference
# book used industry-wide as a "true odds" benchmark, precisely because
# averaging a cluster of recreational AU books (which mostly just copy each
# other) tends to converge tightly on itself without being especially
# accurate. Comparing AU prices against Pinnacle's line instead of (or
# alongside) the AU consensus surfaces edge that a same-region-only
# comparison misses. Cost note: billing is markets x regions, so adding 'eu'
# roughly doubles API quota use per call -- see README for the budget math.
# As of testing, 'au' bookmakers are: SportsBet, PointsBet (AU), TAB,
# TABtouch, Ladbrokes, Neds, Unibet, Betfair, PlayUp, Betr, Bet Right,
# Picklebet (AU). Note: Bet365 and Dabble do NOT appear in this API's data
# at all -- they're not available to price or recommend through this tool.
ODDS_API_REGION = "au,eu"
ODDS_API_BASE = "https://api.the-odds-api.com/v4"

AU_BOOKMAKERS = {
    "SportsBet", "PointsBet (AU)", "TAB", "TABtouch", "Ladbrokes", "Neds",
    "Unibet", "Betfair", "PlayUp", "Betr", "Bet Right", "Picklebet (AU)",
}
SHARP_REFERENCE_BOOKMAKER = "Pinnacle"
# Betfair Exchange is a two-sided market (you can back or lay), which makes
# it sharper than a fixed-odds recreational book even though it's AU-based --
# used as a second sharp opinion when Pinnacle doesn't cover a market/point.
SECONDARY_SHARP_REFERENCE_BOOKMAKER = "Betfair"
# Matchbook is the other two-sided exchange in the 'eu' region pull we're
# already paying for (same reasoning as Betfair, just smaller/less liquid --
# hence third in line, not second). Checked live: currently Pinnacle alone
# already covers ~90% of open tips and Betfair covers the rest, so this
# mostly matters as a safety net for whatever edge case neither covers,
# not a guaranteed source of bigger edges -- genuinely large (5%+) edges
# are rare by nature of markets being efficient, not a methodology gap.
TERTIARY_SHARP_REFERENCE_BOOKMAKER = "Matchbook"

# Bookmakers you actually hold accounts with. "Best price" recommendations
# are restricted to this list so every tip is one you can actually place --
# the fair-probability consensus still uses every AU bookmaker The Odds API
# returns (AU_BOOKMAKERS), since more books = a more accurate consensus, and
# Pinnacle's own line is used as a sharper benchmark when it's available at
# the same point. Must match the "title" strings above exactly; run
# `python -m scripts.list_bookmakers` if the API's naming ever changes.
USER_BOOKMAKERS = {"SportsBet", "PointsBet (AU)", "TAB", "Ladbrokes", "Betfair"}

# Separate from USER_BOOKMAKERS on purpose: this is "apps you hold an
# account at," used only for the Log Bet dropdown (your own manually-placed
# bets, which don't need to match anything in The Odds API's data) --
# USER_BOOKMAKERS is "apps The Odds API actually has live prices for,"
# used to restrict tip pricing so every recommendation is placeable.
# Bet365 isn't in The Odds API's AU data at all (confirmed early in this
# project), so it can never drive a tip, but you can still log a bet you
# placed there yourself.
LOG_BET_BOOKMAKERS = USER_BOOKMAKERS | {"Bet365"}

# "has_history": True means we have a results data source to train the Elo
# model and generate ball-knowledge notes (ESPN's public site API -- see
# data/espn.py -- no key needed, just an espn_sport/espn_league pair below).
# Without it, a sport still gets devig value tips -- just no Elo context and
# no situational notes. NRL isn't on ESPN's public API (checked -- 404s);
# BBL/cricket uses a different ESPN API shape not wired up yet.
# "auto_refresh": True means the dashboard refreshes this sport automatically
# (once per day) when you open Today's Tips -- no manual button press needed.
# Off for sports confirmed off-season right now so the daily auto-refresh
# doesn't burn API quota on guaranteed-empty results; flip to True (or just
# use the Run Pipeline tab manually) once a season starts.
SPORT_CONFIG = {
    "nba": {
        "odds_key": "basketball_nba",
        "has_history": True,
        "espn_sport": "basketball",
        "espn_league": "nba",
        "markets": ["h2h", "spreads", "totals"],
        "auto_refresh": True,
    },
    "epl": {
        "odds_key": "soccer_epl",
        "has_history": True,
        "espn_sport": "soccer",
        "espn_league": "eng.1",
        "markets": ["h2h", "spreads", "totals"],
        "auto_refresh": True,
        # BTTS isn't on the bulk /odds endpoint (confirmed: 422s there) --
        # it needs a separate per-event call, which costs quota per game
        # rather than once per sport. Real coverage though: 4 of 5 held
        # bookmakers post it (SportsBet/Ladbrokes/PointsBet/Betfair),
        # unlike EPL's totals/spreads which have zero coverage from any of
        # them. Restricted to EXTRA_EVENT_MARKETS_LOOKAHEAD_DAYS, not the
        # full window, to keep the extra per-event cost manageable.
        "extra_event_markets": ["btts"],
    },
    "nbl": {
        "odds_key": "basketball_nbl",
        "has_history": True,
        "espn_sport": "basketball",
        "espn_league": "nbl",
        "markets": ["h2h", "spreads", "totals"],
        # 2026-10-26 check: season is live (games scheduled from Oct 28,
        # Elo trains fine off 240 played games) but The Odds API's
        # basketball_nbl returns 0 events across every region (au/us/uk/eu),
        # and is flagged inactive on its own /sports listing -- no bookmaker
        # coverage configured for this competition on this data source right
        # now. Not simply early/no-lines-yet: Euroleague games a similar ~1
        # day out already carry 24-25 bookmakers each, for comparison. A
        # zero-event request doesn't cost API quota, so auto_refresh is on
        # (checked daily, free) to pick it up the moment coverage appears --
        # this doesn't mean coverage is expected soon, just that this will
        # keep catching it automatically rather than relying on manual checks.
        "auto_refresh": True,
    },
    "nfl": {
        "odds_key": "americanfootball_nfl",
        "has_history": True,
        "espn_sport": "football",
        "espn_league": "nfl",
        "markets": ["h2h", "spreads", "totals"],
        "auto_refresh": True,
        # Doesn't cost any extra API calls either way -- notes are built
        # from data already pulled for Value/Predictions, same as every
        # other sport. This is purely "don't show it," per explicit ask.
        "show_knowledge": False,
    },
    # NRL is off-season for part of the year -- that's not a bug, the
    # pipeline just returns 0 events until the season starts. Flip
    # auto_refresh to True (or just run manually) once it's close.
    "nrl": {
        "odds_key": "rugbyleague_nrl",
        "has_history": False,
        "markets": ["h2h", "spreads", "totals"],
        "auto_refresh": False,
    },
    "afl": {
        "odds_key": "aussierules_afl",
        "has_history": True,
        "espn_sport": "australian-football",
        "espn_league": "afl",
        "markets": ["h2h", "spreads", "totals"],
        "auto_refresh": False,
    },
    # Cricket markets on The Odds API are structured differently (run lines,
    # not point spreads) -- starting with h2h only until that's tested against
    # live Big Bash data (season starts December).
    "bbl": {
        "odds_key": "cricket_big_bash",
        "has_history": False,
        "markets": ["h2h"],
        "auto_refresh": False,
    },
}
