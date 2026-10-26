"""CLI entrypoint: python -m scripts.run_auto_refresh

Runs every sport flagged auto_refresh=True in config.SPORT_CONFIG, in one
process. Used by .github/workflows/daily-refresh.yml instead of a
hardcoded per-sport step list -- that list silently went stale before
(NFL/NBL were auto_refresh=True in config but had no workflow step), so
this reads the same config the dashboard and pipeline already use instead
of needing to be kept in sync by hand whenever a sport's season starts.
"""
from datetime import date

from config import SPORT_CONFIG
from db import database as db
from tips.generate_tips import run

if __name__ == "__main__":
    db.init_db()
    for sport, cfg in SPORT_CONFIG.items():
        if not cfg.get("auto_refresh"):
            continue
        try:
            run(sport=sport)
            db.set_setting(f"last_run_date_{sport}", date.today().isoformat())
        except Exception as e:
            # Most likely cause: API quota exhausted for the period. One
            # sport failing (or the quota running out partway through this
            # loop) shouldn't block the rest -- without this, a single
            # failure here crashed the whole script, which meant the GitHub
            # Action step failed and the commit step after it never ran at
            # all, even for sports that succeeded before the failure.
            print(f"[{sport}] refresh failed, leaving its existing tips as-is: {e}")
