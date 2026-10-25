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

# Odds API region/bookmaker set -- 'au' returns AU-licensed bookmakers
# (Sportsbet, TAB, Neds, Ladbrokes AU, Unibet, PointsBet AU, Betfair AU, etc.)
ODDS_API_REGION = "au"
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
BALLDONTLIE_BASE = "https://api.balldontlie.io/v1"

SPORT_KEYS = {
    "nba": "basketball_nba",
}
