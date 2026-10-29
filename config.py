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
ODDS_API_KEY_3 = _get_secret("ODDS_API_KEY_3", "")
ODDS_API_KEY_4 = _get_secret("ODDS_API_KEY_4", "")
ODDS_API_KEYS = [k for k in (ODDS_API_KEY, ODDS_API_KEY_2, ODDS_API_KEY_3, ODDS_API_KEY_4) if k]

DEFAULT_BANKROLL_AMOUNT = float(_get_secret("BANKROLL_AMOUNT", "1000"))
DEFAULT_UNIT_SIZE_PCT = float(_get_secret("UNIT_SIZE_PCT", "0.01"))
DEFAULT_MAX_UNITS_PER_BET = float(_get_secret("MAX_UNITS_PER_BET", "3"))
DEFAULT_MIN_EDGE_PCT = float(_get_secret("MIN_EDGE_PCT", "0.0075"))

# Stake size: a round, fixed unit count per edge tier used to live here
# (DEFAULT_TIER_UNITS_STRONG/MODERATE/LEAN) -- replaced 2026-10-27 by
# quarter-Kelly stake sizing (tips/generate_tips._kelly_units,
# models/kelly.py), driven by the bankroll/unit-size settings below
# instead of fixed per-tier numbers. Explicit feedback: units shouldn't
# be snapped to round values at all, should continuously reflect both
# likelihood of hitting and the market edge -- which is literally what
# the Kelly formula already is.
#
# Ceiling briefly dropped to 2.5 same day (second round of feedback, via
# a friend's sense check: "3 units is also way too much"), then put back
# to 3 right after on explicit follow-up -- "even 2.5 or 3 if super
# confident... js do it based off confidence" clarified the actual ask
# was never "lower the ceiling," it was "stop ordinary bets from reaching
# it." KELLY_FRACTION (see tips/generate_tips.py) is what fixed that --
# cut hard enough that a run-of-the-mill pick lands well below half this
# cap, so 3u stays reachable but genuinely rare, reserved for the
# standout cases the saturating curve in _kelly_units lets through.

# Hard range on single-bet odds shown in the main "Value Bets"/"Predictions"
# views -- not a soft discount zone, an actual floor and ceiling. Tips
# outside this range still get generated and stored (useful data, e.g. for
# Performance history), they just don't show in the default feed -- tucked
# into a collapsed, clearly-labelled section instead.
DEFAULT_MIN_SINGLE_ODDS = float(_get_secret("MIN_SINGLE_ODDS", "1.6"))
DEFAULT_MAX_SINGLE_ODDS = float(_get_secret("MAX_SINGLE_ODDS", "2.2"))

# Markets only available per-event (not the bulk /odds endpoint, e.g. btts)
# cost quota per game, not once per sport -- restricting how far ahead we
# bother pulling them keeps that cost bounded. Was 7, cut to 3 on
# 2026-10-27 when the monthly budget was tight (two keys, 1000/month, a
# full daily refresh already needed ~1020). Raised back to 5 the same day
# once a third key landed (1500/month, real headroom even with this back
# up) and explicit feedback asked for value found further out ("4-5 days
# in advance"). The same near-term game gets re-pulled once per day it
# sits inside this window, not just once, so moving 3->5 roughly doubles
# BTTS's daily cost (~4-5 events/day -> ~8-9) -- affordable now, wasn't
# at 1000/month.
EXTRA_EVENT_MARKETS_LOOKAHEAD_DAYS = 5

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
# Betfair removed 2026-10-27 (explicit feedback: "i dont use it") -- this
# only stops it being offered as a bookmaker to actually BACK a bet at.
# It stays as SECONDARY_SHARP_REFERENCE_BOOKMAKER above (a different role:
# its exchange price is used as a sharp benchmark for fair-probability
# calculation, which doesn't require holding an account there) and stays
# wired into the Bonus Converter (which specifically needs an exchange to
# LAY against, not back at -- a different mechanic from this list).
USER_BOOKMAKERS = {"SportsBet", "PointsBet (AU)", "TAB", "Ladbrokes"}

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
