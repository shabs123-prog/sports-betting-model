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

DEFAULT_BANKROLL_AMOUNT = float(_get_secret("BANKROLL_AMOUNT", "1000"))
DEFAULT_UNIT_SIZE_PCT = float(_get_secret("UNIT_SIZE_PCT", "0.01"))
DEFAULT_MAX_UNITS_PER_BET = float(_get_secret("MAX_UNITS_PER_BET", "3"))
DEFAULT_MIN_EDGE_PCT = float(_get_secret("MIN_EDGE_PCT", "0.0075"))

# Stake size by edge tier (see models.edge.tier_label) -- a round, fixed
# unit count per tier rather than a continuously-scaled Kelly fraction.
# Bigger and more intuitive than "0.39 units", still grounded in how
# confident the edge is. Each is capped by max_units_per_bet regardless.
DEFAULT_TIER_UNITS_STRONG = float(_get_secret("TIER_UNITS_STRONG", "2.5"))
DEFAULT_TIER_UNITS_MODERATE = float(_get_secret("TIER_UNITS_MODERATE", "1.75"))
DEFAULT_TIER_UNITS_LEAN = float(_get_secret("TIER_UNITS_LEAN", "1.0"))

# Hard range on single-bet odds shown in the main "Value Bets"/"Predictions"
# views -- not a soft discount zone, an actual floor and ceiling. Tips
# outside this range still get generated and stored (useful data, e.g. for
# Performance history), they just don't show in the default feed -- tucked
# into a collapsed, clearly-labelled section instead.
DEFAULT_MIN_SINGLE_ODDS = float(_get_secret("MIN_SINGLE_ODDS", "1.6"))
DEFAULT_MAX_SINGLE_ODDS = float(_get_secret("MAX_SINGLE_ODDS", "2.2"))

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

# Bookmakers you actually hold accounts with. "Best price" recommendations
# are restricted to this list so every tip is one you can actually place --
# the fair-probability consensus still uses every AU bookmaker The Odds API
# returns (AU_BOOKMAKERS), since more books = a more accurate consensus, and
# Pinnacle's own line is used as a sharper benchmark when it's available at
# the same point. Must match the "title" strings above exactly; run
# `python -m scripts.list_bookmakers` if the API's naming ever changes.
USER_BOOKMAKERS = {"SportsBet", "PointsBet (AU)", "TAB", "Ladbrokes", "Betfair"}

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
    },
    "nbl": {
        "odds_key": "basketball_nbl",
        "has_history": True,
        "espn_sport": "basketball",
        "espn_league": "nbl",
        "markets": ["h2h", "spreads", "totals"],
        # Games are being played (Elo trains fine, 240 games) but AU
        # bookmakers aren't posting betting markets on it yet as of testing
        # -- flip this once `python -m scripts.run_daily nbl` returns events.
        "auto_refresh": False,
    },
    "nfl": {
        "odds_key": "americanfootball_nfl",
        "has_history": True,
        "espn_sport": "football",
        "espn_league": "nfl",
        "markets": ["h2h", "spreads", "totals"],
        "auto_refresh": True,
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
