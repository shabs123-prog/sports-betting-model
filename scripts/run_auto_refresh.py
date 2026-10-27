"""CLI entrypoint: python -m scripts.run_auto_refresh [sport|all]

With no argument (or "all"): runs every sport flagged auto_refresh=True in
config.SPORT_CONFIG, in one process -- the scheduled/default behaviour.

With a specific sport key (e.g. "nba"): runs ONLY that sport, bypassing
its auto_refresh flag -- lets you manually top up one sport (right before
you actually want to look at picks, say) without spending quota
re-checking every other configured sport too. Also lets you manually
check a sport that's currently off (auto_refresh=False) -- worth knowing
that's not an API-cost concern either way: checked live, a sport with
zero events costs 0 quota regardless of markets/regions requested, so the
auto_refresh flag is purely about not bothering with a guaranteed-empty
check automatically, not about saving money on off-season sports.

Used by .github/workflows/daily-refresh.yml -- the scheduled trigger
passes nothing (defaults to "all"), its workflow_dispatch "sport" input
passes a specific key when run manually from the Actions tab.
"""
import sys
from datetime import date

from config import SPORT_CONFIG
from db import database as db
from tips.generate_tips import run


def _sports_to_run(requested: str) -> list[str]:
    if requested == "all":
        return [s for s, cfg in SPORT_CONFIG.items() if cfg.get("auto_refresh")]
    if requested not in SPORT_CONFIG:
        print(f"Unknown sport {requested!r} -- valid keys: {', '.join(SPORT_CONFIG)}")
        sys.exit(1)
    return [requested]


if __name__ == "__main__":
    requested = sys.argv[1] if len(sys.argv) > 1 else "all"
    db.init_db()
    for sport in _sports_to_run(requested):
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
