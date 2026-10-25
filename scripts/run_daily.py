"""CLI entrypoint: python -m scripts.run_daily [sport]

Marks the sport as refreshed-for-today in the DB (same bookkeeping
tips.generate_tips.ensure_fresh() does), so a scheduled run of this script
(cron, GitHub Actions) means the dashboard won't redundantly re-run it again
when someone opens the app -- they get the already-fresh data instantly.
"""
import sys
from datetime import date

from db import database as db
from tips.generate_tips import run

if __name__ == "__main__":
    sport = sys.argv[1] if len(sys.argv) > 1 else "nba"
    db.init_db()
    run(sport=sport)
    db.set_setting(f"last_run_date_{sport}", date.today().isoformat())
