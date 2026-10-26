"""Current injury status from ESPN's hidden "core" API
(sports.core.api.espn.com) -- a different host/shape than the public site
API in data/espn.py, checked live and confirmed working the same
no-key/no-login way. Unofficial and could change without notice, same as
the rest of the ESPN integration -- every call here is wrapped so a
broken/changed response just means no injury note that day, not a crashed
pipeline.

This is the signal knowledge/situational.py's original docstring flagged
as deliberately out of scope ("plug a news/injury feed into generate_notes
later") -- a star player being OUT is real context no score-based model
can see. Same house rule as every other model/context signal here: this
is colour attached to a tip's reasoning, never something that triggers or
sizes a tip on its own.

Checked live per sport (2026-10-26): real, populated data for NBA and
NFL. EPL came back empty across all 20 teams -- ESPN's coverage gap, not
a bug (confirmed via the raw endpoint directly, not just this module).
AFL/NBL came back empty too but both were off-season/preseason at the
time of checking, so that could just mean no current injuries rather
than no coverage -- worth re-checking once those seasons are live. Either
way this fails to an empty list, never raises, so a gap here just means
no extra note that day, nothing breaks.
"""
from __future__ import annotations

import re

import requests

CORE_BASE = "https://sports.core.api.espn.com/v2/sports"

_ATHLETE_ID_RE = re.compile(r"/athletes/(\d+)")

# "Day-To-Day"/"Probable" is too routine to be worth a note (most rosters
# have one most days) -- Out/Doubtful are the statuses that can actually
# swing a line.
NOTEWORTHY_STATUSES = {"Out", "Doubtful"}


def _get(url: str) -> dict | None:
    try:
        resp = requests.get(url, timeout=10)
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None
    try:
        return resp.json()
    except ValueError:
        return None


def _team_roster_names(espn_sport: str, espn_league: str, team_id: str) -> dict[str, str]:
    from data.espn import BASE as SITE_BASE  # local import: avoid a hard circular dep at module load

    data = _get(f"{SITE_BASE}/{espn_sport}/{espn_league}/teams/{team_id}/roster")
    if not data:
        return {}
    # Soccer rosters group athletes into position buckets (each with its own
    # "items" list) instead of a flat list -- normalize both shapes.
    flat = []
    for entry in data.get("athletes", []):
        if isinstance(entry, dict) and "items" in entry:
            flat.extend(entry["items"])
        else:
            flat.append(entry)
    return {str(a["id"]): a.get("displayName", "") for a in flat if a.get("id")}


def get_team_injuries(espn_sport: str, espn_league: str, team_id: str) -> list[dict]:
    """Returns [{player, status, note}, ...] for this team's current
    Out/Doubtful list -- empty on any API hiccup or if nothing's noteworthy,
    never raises."""
    listing = _get(f"{CORE_BASE}/{espn_sport}/leagues/{espn_league}/teams/{team_id}/injuries")
    items = (listing or {}).get("items") or []
    if not items:
        return []

    names: dict[str, str] | None = None  # lazy: only fetch the roster if there's something to report
    results = []
    for item in items[:10]:  # a real injury list is never this deep; caps a pathological response
        ref = item.get("$ref")
        if not ref:
            continue
        detail = _get(ref)
        if not detail:
            continue
        status = detail.get("status", "")
        if status not in NOTEWORTHY_STATUSES:
            continue
        if names is None:
            names = _team_roster_names(espn_sport, espn_league, team_id)
        athlete_ref = (detail.get("athlete") or {}).get("$ref", "")
        match = _ATHLETE_ID_RE.search(athlete_ref)
        player = names.get(match.group(1), "") if match else ""
        if not player:
            continue
        results.append({
            "player": player,
            "status": status,
            "note": detail.get("shortComment", ""),
        })
    return results
