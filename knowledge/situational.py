"""Rule-based 'ball knowledge' notes: schedule fatigue, form, home/away splits.

These are NOT edge calculations -- they're context a sharp bettor would
factor in by hand (rest, streaks, situational spots). They get attached to
value tips as supporting reasoning, and also surface on their own as a
daily "things to know" feed even when there's no priced edge.

Started with only what's derivable from final scores + schedule (ESPN's
public site API -- see data/espn.py); injury_notes() below adds the
current Out/Doubtful list from data/espn_injuries.py as the same kind of
plain-text context, same house rule as everything else here -- colour
attached to a tip's reasoning, never something that triggers or sizes one.
"""

from __future__ import annotations
from datetime import date, datetime


def _parse_date(d: str) -> date:
    return datetime.fromisoformat(d.replace("Z", "+00:00")).date()


def build_team_game_log(finished_games: list[dict]) -> dict:
    """{team_name: [{date, opponent, is_home, team_score, opp_score}, ...]} sorted by date."""
    log: dict[str, list[dict]] = {}
    for g in finished_games:
        home, away = g["home_team"], g["away_team"]
        pairs = [(home, away, True, g["home_score"], g["away_score"]),
                 (away, home, False, g["away_score"], g["home_score"])]
        for team, opp, is_home, team_score, opp_score in pairs:
            log.setdefault(team, []).append({
                "date": g["date"],
                "opponent": opp,
                "is_home": is_home,
                "team_score": team_score,
                "opp_score": opp_score,
            })
    for team in log:
        log[team].sort(key=lambda x: x["date"])
    return log


def rest_days(team_log: list[dict], game_date: date) -> int | None:
    prior = [g for g in team_log if _parse_date(g["date"]) < game_date]
    if not prior:
        return None
    last_game_date = _parse_date(prior[-1]["date"])
    return (game_date - last_game_date).days


def recent_form(team_log: list[dict], game_date: date, n: int = 10) -> dict:
    prior = [g for g in team_log if _parse_date(g["date"]) < game_date]
    last_n = prior[-n:]
    wins = sum(1 for g in last_n if g["team_score"] > g["opp_score"])
    losses = len(last_n) - wins
    streak_type, streak_len = None, 0
    for g in reversed(prior):
        is_win = g["team_score"] > g["opp_score"]
        if streak_type is None:
            streak_type = is_win
            streak_len = 1
        elif is_win == streak_type:
            streak_len += 1
        else:
            break
    return {
        "record_last_n": f"{wins}-{losses}",
        "games_counted": len(last_n),
        "streak": streak_len if streak_type else -streak_len if streak_type is False else 0,
    }


def home_away_split(team_log: list[dict], game_date: date) -> dict:
    prior = [g for g in team_log if _parse_date(g["date"]) < game_date]
    home_games = [g for g in prior if g["is_home"]]
    away_games = [g for g in prior if not g["is_home"]]

    def win_pct(games):
        if not games:
            return None
        return sum(1 for g in games if g["team_score"] > g["opp_score"]) / len(games)

    return {"home_win_pct": win_pct(home_games), "away_win_pct": win_pct(away_games)}


def injury_notes(team_name: str, injuries: list[dict]) -> list[str]:
    """injuries: data.espn_injuries.get_team_injuries() output for one team."""
    notes = []
    for inj in injuries:
        note = f"{team_name}: {inj['player']} is {inj['status'].upper()}"
        if inj.get("note"):
            note += f" -- {inj['note']}"
        notes.append(note)
    return notes


def plain_why_facts(
    home_team: str, away_team: str, game_date: date, team_logs: dict,
    home_injuries: list[dict] | None = None, away_injuries: list[dict] | None = None,
) -> list[str]:
    """Same underlying signals as generate_notes()/injury_notes(), but phrased
    with zero numbers -- explicit feedback (2026-10-27): the card description
    should lead with a plain-English reason, not a stat. Ordered most-to-
    least concrete (a named injury beats a vague streak), so the caller can
    just take facts[0] as "the" reason. Recomputes these from the same
    already-fetched team_logs/injuries rather than reusing generate_notes()'s
    rendered strings, so there's no number to strip out after the fact."""
    facts = []
    if home_injuries:
        facts.append(f"{home_team} are without {home_injuries[0]['player']}")
    if away_injuries:
        facts.append(f"{away_team} are without {away_injuries[0]['player']}")

    home_log = team_logs.get(home_team, [])
    away_log = team_logs.get(away_team, [])
    home_rest = rest_days(home_log, game_date)
    away_rest = rest_days(away_log, game_date)
    if home_rest == 1:
        facts.append(f"{home_team} are playing on zero rest")
    if away_rest == 1:
        facts.append(f"{away_team} are playing on zero rest")
    if home_rest is not None and away_rest is not None and abs(home_rest - away_rest) >= 2:
        rested, tired = (home_team, away_team) if home_rest > away_rest else (away_team, home_team)
        facts.append(f"{rested} have had more time to recover than {tired}")

    home_form = recent_form(home_log, game_date)
    away_form = recent_form(away_log, game_date)
    if home_form["streak"] >= 5:
        facts.append(f"{home_team} have been red-hot lately")
    elif home_form["streak"] <= -5:
        facts.append(f"{home_team} have been cold lately")
    if away_form["streak"] >= 5:
        facts.append(f"{away_team} have been red-hot lately")
    elif away_form["streak"] <= -5:
        facts.append(f"{away_team} have been cold lately")

    home_split = home_away_split(home_log, game_date)
    away_split = home_away_split(away_log, game_date)
    if home_split["home_win_pct"] is not None and home_split["home_win_pct"] < 0.35:
        facts.append(f"{home_team} haven't been good at home this season")
    if away_split["away_win_pct"] is not None and away_split["away_win_pct"] > 0.65:
        facts.append(f"{away_team} have been strong on the road this season")

    return facts


def generate_notes(home_team: str, away_team: str, game_date: date, team_logs: dict) -> list[str]:
    notes = []
    home_log = team_logs.get(home_team, [])
    away_log = team_logs.get(away_team, [])

    home_rest = rest_days(home_log, game_date)
    away_rest = rest_days(away_log, game_date)

    if home_rest == 1:
        notes.append(f"{home_team} are on the second night of a back-to-back.")
    if away_rest == 1:
        notes.append(f"{away_team} are on the second night of a back-to-back.")
    if home_rest is not None and away_rest is not None and abs(home_rest - away_rest) >= 2:
        rested, tired = (home_team, away_team) if home_rest > away_rest else (away_team, home_team)
        notes.append(f"Rest advantage: {rested} had more days off than {tired} coming into this game.")

    home_form = recent_form(home_log, game_date)
    away_form = recent_form(away_log, game_date)
    if home_form["streak"] >= 5:
        notes.append(f"{home_team} are on a {home_form['streak']}-game win streak.")
    elif home_form["streak"] <= -5:
        notes.append(f"{home_team} are on a {-home_form['streak']}-game losing streak.")
    if away_form["streak"] >= 5:
        notes.append(f"{away_team} are on a {away_form['streak']}-game win streak.")
    elif away_form["streak"] <= -5:
        notes.append(f"{away_team} are on a {-away_form['streak']}-game losing streak.")

    home_split = home_away_split(home_log, game_date)
    away_split = home_away_split(away_log, game_date)
    if home_split["home_win_pct"] is not None and home_split["home_win_pct"] < 0.35:
        notes.append(f"{home_team} have struggled at home this season (win% {home_split['home_win_pct']:.0%}).")
    if away_split["away_win_pct"] is not None and away_split["away_win_pct"] > 0.65:
        notes.append(f"{away_team} have been strong on the road this season (win% {away_split['away_win_pct']:.0%}).")

    return notes
