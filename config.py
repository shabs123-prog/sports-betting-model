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
BALLDONTLIE_API_KEY = _get_secret("BALLDONTLIE_API_KEY", "")

DEFAULT_BANKROLL_AMOUNT = float(_get_secret("BANKROLL_AMOUNT", "1000"))
DEFAULT_UNIT_SIZE_PCT = float(_get_secret("UNIT_SIZE_PCT", "0.01"))
DEFAULT_KELLY_FRACTION = float(_get_secret("KELLY_FRACTION", "0.3"))
DEFAULT_MAX_UNITS_PER_BET = float(_get_secret("MAX_UNITS_PER_BET", "3"))
DEFAULT_MIN_EDGE_PCT = float(_get_secret("MIN_EDGE_PCT", "0.005"))

# Odds API region -- 'au' returns AU-licensed bookmakers. As of testing,
# that's: SportsBet, PointsBet (AU), TAB, TABtouch, Ladbrokes, Neds, Unibet,
# Betfair, PlayUp, Betr, Bet Right, Picklebet (AU). Note: Bet365 and Dabble
# do NOT appear in this API's data at all -- they're not available to price
# or recommend through this tool, full stop.
ODDS_API_REGION = "au"
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
BALLDONTLIE_BASE = "https://api.balldontlie.io/v1"

# Bookmakers you actually hold accounts with. "Best price" recommendations
# are restricted to this list so every tip is one you can actually place --
# the fair-probability consensus still uses every AU bookmaker The Odds API
# returns, since more books = a more accurate consensus. Must match the
# "title" strings above exactly; run `python -m scripts.list_bookmakers` if
# the API's naming ever changes.
USER_BOOKMAKERS = {"SportsBet", "PointsBet (AU)", "TAB", "Ladbrokes", "Betfair"}

# "has_history": True means we have a results data source (currently just
# balldontlie, NBA-only) to train the Elo model and generate ball-knowledge
# notes. Without it, a sport still gets devig value tips -- just no Elo
# context and no situational notes.
SPORT_CONFIG = {
    "nba": {
        "odds_key": "basketball_nba",
        "has_history": True,
        "markets": ["h2h", "spreads", "totals"],
    },
    "epl": {
        "odds_key": "soccer_epl",
        "has_history": False,
        "markets": ["h2h", "spreads", "totals"],
    },
    "nbl": {
        "odds_key": "basketball_nbl",
        "has_history": False,
        "markets": ["h2h", "spreads", "totals"],
    },
    # NRL/AFL are off-season for part of the year -- that's not a bug, the
    # pipeline just returns 0 events until the season starts. Worth running
    # anyway once it's close (check theoddsapi.com or just try it).
    "nrl": {
        "odds_key": "rugbyleague_nrl",
        "has_history": False,
        "markets": ["h2h", "spreads", "totals"],
    },
    "afl": {
        "odds_key": "aussierules_afl",
        "has_history": False,
        "markets": ["h2h", "spreads", "totals"],
    },
    # Cricket markets on The Odds API are structured differently (run lines,
    # not point spreads) -- starting with h2h only until that's tested against
    # live Big Bash data (season starts December).
    "bbl": {
        "odds_key": "cricket_big_bash",
        "has_history": False,
        "markets": ["h2h"],
    },
}
