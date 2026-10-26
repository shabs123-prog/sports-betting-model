from __future__ import annotations
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from config import DB_PATH

AU_TZ = ZoneInfo("Australia/Sydney")

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA_PATH.read_text())
        _migrate(conn)


def _migrate(conn):
    """CREATE TABLE IF NOT EXISTS in schema.sql is a no-op on an existing
    DB, so a new column added there never reaches an already-committed
    tips.db on its own -- this applies ALTER TABLEs for columns added after
    the initial schema, idempotently, so the daily-refreshed tips.db (with
    its accumulated line_history) never needs to be thrown away."""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(tips)")}
    if "game_time_local" not in cols:
        conn.execute("ALTER TABLE tips ADD COLUMN game_time_local TEXT")


def insert_tip(tip: dict) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO tips
               (created_at, game_date, game_time_local, sport, matchup, market, selection,
                bookmaker, odds_decimal, model_prob, market_fair_prob, edge_pct,
                suggested_units, tip_type, reasoning, status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                now_iso(),
                tip["game_date"],
                tip.get("game_time_local"),
                tip["sport"],
                tip["matchup"],
                tip["market"],
                tip["selection"],
                tip.get("bookmaker"),
                tip.get("odds_decimal"),
                tip.get("model_prob"),
                tip.get("market_fair_prob"),
                tip.get("edge_pct"),
                tip.get("suggested_units"),
                tip["tip_type"],
                tip.get("reasoning", ""),
                tip.get("status", "open"),
            ),
        )
        return cur.lastrowid


def supersede_open_tips(sport: str, from_date: str):
    """
    Marks this sport's still-open tips for games on/after from_date as
    'superseded', right before a pipeline run generates fresh ones for the
    same games -- without this, a game re-evaluated on each of several days
    leading up to it (every daily refresh re-prices every upcoming game) or
    a repeated manual Refresh click just piles up duplicate/stale rows,
    since insert_tip only ever adds, never replaces. Already-played games
    (game_date before from_date) are left alone -- those are history, not
    something this run is re-evaluating.
    """
    with get_conn() as conn:
        conn.execute(
            "UPDATE tips SET status = 'superseded' "
            "WHERE sport = ? AND status = 'open' AND game_date >= ?",
            (sport, from_date),
        )


def supersede_started_games():
    """
    Marks any still-open tip whose game has already started (AU-local now)
    as superseded, independent of the odds pipeline -- supersede_open_tips
    only clears a sport's old tips right before a fresh pipeline run, so if
    quota runs out (or a scheduled refresh just doesn't happen) a game that
    already kicked off keeps showing as a live pick indefinitely, since
    nothing else ever marks it stale. Call this on every dashboard page
    load and every PWA data.json build -- it's a pure local-clock check,
    costs no API quota, and runs regardless of whether a refresh succeeded.
    Rows from before game_time_local existed (or with no linked bet) have
    no time to compare -- those just get cleared once the date itself has
    passed, same as before this existed.
    """
    au_now = datetime.now(AU_TZ)
    today = au_now.date().isoformat()

    with get_conn() as conn:
        conn.execute(
            "UPDATE tips SET status = 'superseded' WHERE status = 'open' AND game_date < ?",
            (today,),
        )
        rows = conn.execute(
            "SELECT id, game_time_local FROM tips "
            "WHERE status = 'open' AND game_date = ? AND game_time_local IS NOT NULL",
            (today,),
        ).fetchall()
        stale_ids = []
        for row in rows:
            try:
                game_time = datetime.strptime(row["game_time_local"].upper(), "%I:%M %p").time()
            except (ValueError, AttributeError):
                continue
            if game_time <= au_now.time():
                stale_ids.append(row["id"])
        if stale_ids:
            conn.executemany(
                "UPDATE tips SET status = 'superseded' WHERE id = ?",
                [(i,) for i in stale_ids],
            )


def get_tips_for_date(game_date: str, tip_type: str | None = None):
    query = "SELECT * FROM tips WHERE game_date = ?"
    params = [game_date]
    if tip_type:
        query += " AND tip_type = ?"
        params.append(tip_type)
    query += " ORDER BY edge_pct DESC"
    with get_conn() as conn:
        return [dict(row) for row in conn.execute(query, params).fetchall()]


def get_tips_in_range(start_date: str, end_date: str, tip_type: str | None = None, sport: str | None = None):
    """Inclusive date range, e.g. for a 'this week' view spanning several game dates.
    status='open' only -- see supersede_open_tips: every pipeline run supersedes
    that sport's prior not-yet-played tips before generating new ones, so a game
    re-evaluated on each of several days leading up to it (or a repeated manual
    Refresh) doesn't pile up as duplicate/stale rows here."""
    query = "SELECT * FROM tips WHERE status = 'open' AND game_date >= ? AND game_date <= ?"
    params = [start_date, end_date]
    if tip_type:
        query += " AND tip_type = ?"
        params.append(tip_type)
    if sport:
        query += " AND sport = ?"
        params.append(sport)
    query += " ORDER BY game_date ASC, edge_pct DESC"
    with get_conn() as conn:
        return [dict(row) for row in conn.execute(query, params).fetchall()]


def get_all_tips(limit: int = 500):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM tips ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(row) for row in rows]


def get_distinct_matchup_teams(sport: str) -> set[str]:
    """
    Every team name that's appeared in this sport's tip matchups (The Odds
    API's naming), across all history -- used to build an ESPN-name <->
    Odds-API-name mapping for auto-grading logged bets against ESPN results
    (see pwa/build_site.py), without needing a fresh pipeline run.
    """
    with get_conn() as conn:
        rows = conn.execute("SELECT DISTINCT matchup FROM tips WHERE sport = ?", (sport,)).fetchall()
    teams = set()
    for row in rows:
        parts = row["matchup"].split(" @ ")
        if len(parts) == 2:
            teams.update(p.strip() for p in parts)
    return teams


def insert_bet(bet: dict) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO bets
               (tip_id, placed_at, sport, matchup, selection, bookmaker, odds_decimal,
                stake_units, stake_amount, result, profit_units, profit_amount, notes)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                bet.get("tip_id"),
                now_iso(),
                bet["sport"],
                bet["matchup"],
                bet["selection"],
                bet.get("bookmaker"),
                bet["odds_decimal"],
                bet["stake_units"],
                bet["stake_amount"],
                bet.get("result", "pending"),
                bet.get("profit_units"),
                bet.get("profit_amount"),
                bet.get("notes", ""),
            ),
        )
        return cur.lastrowid


def update_bet_result(bet_id: int, result: str):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM bets WHERE id = ?", (bet_id,)).fetchone()
        if row is None:
            return
        odds = row["odds_decimal"]
        stake_units = row["stake_units"]
        stake_amount = row["stake_amount"]
        if result == "win":
            profit_units = stake_units * (odds - 1)
            profit_amount = stake_amount * (odds - 1)
        elif result == "loss":
            profit_units = -stake_units
            profit_amount = -stake_amount
        else:  # push / void
            profit_units = 0.0
            profit_amount = 0.0
        conn.execute(
            "UPDATE bets SET result = ?, profit_units = ?, profit_amount = ? WHERE id = ?",
            (result, profit_units, profit_amount, bet_id),
        )


def get_all_bets():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM bets ORDER BY placed_at DESC").fetchall()
        return [dict(row) for row in rows]


def get_setting(key: str, default: str | None = None):
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )


def get_elo_ratings(sport: str) -> dict:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT team, rating FROM elo_ratings WHERE sport = ?", (sport,)
        ).fetchall()
        return {row["team"]: row["rating"] for row in rows}


def save_elo_ratings(sport: str, ratings: dict):
    with get_conn() as conn:
        ts = now_iso()
        conn.executemany(
            "INSERT INTO elo_ratings (sport, team, rating, last_updated) VALUES (?,?,?,?) "
            "ON CONFLICT(sport, team) DO UPDATE SET rating = excluded.rating, last_updated = excluded.last_updated",
            [(sport, team, rating, ts) for team, rating in ratings.items()],
        )


def insert_line_history_batch(rows: list[dict]):
    """rows: [{sport, matchup, game_date, market, outcome, point, fair_prob, fair_prob_source}, ...]"""
    if not rows:
        return
    with get_conn() as conn:
        ts = now_iso()
        conn.executemany(
            """INSERT INTO line_history
               (recorded_at, sport, matchup, game_date, market, outcome, point, fair_prob, fair_prob_source)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            [
                (ts, r["sport"], r["matchup"], r["game_date"], r["market"], r["outcome"],
                 r.get("point"), r["fair_prob"], r["fair_prob_source"])
                for r in rows
            ],
        )


def get_line_history(sport: str, matchup: str, game_date: str, market: str, outcome: str):
    """All recorded snapshots for one specific line, oldest first."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM line_history
               WHERE sport=? AND matchup=? AND game_date=? AND market=? AND outcome=?
               ORDER BY recorded_at ASC""",
            (sport, matchup, game_date, market, outcome),
        ).fetchall()
        return [dict(r) for r in rows]


def get_moved_lines(min_game_date: str, max_game_date: str):
    """
    Every (sport, matchup, game_date, market, outcome) with line_history
    rows spanning 2+ distinct calendar days, for games between the given
    dates -- the earliest and latest snapshot of each. Empty until enough
    days of history have accumulated; that's expected, not a bug.
    """
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM line_history
               WHERE game_date >= ? AND game_date <= ?
               ORDER BY sport, matchup, game_date, market, outcome, recorded_at ASC""",
            (min_game_date, max_game_date),
        ).fetchall()
    grouped: dict[tuple, list[dict]] = {}
    for row in rows:
        key = (row["sport"], row["matchup"], row["game_date"], row["market"], row["outcome"])
        grouped.setdefault(key, []).append(dict(row))

    moved = []
    for key, snaps in grouped.items():
        distinct_days = {s["recorded_at"][:10] for s in snaps}
        if len(distinct_days) < 2:
            continue
        sport, matchup, game_date, market, outcome = key
        moved.append({
            "sport": sport, "matchup": matchup, "game_date": game_date,
            "market": market, "outcome": outcome,
            "first": snaps[0], "latest": snaps[-1], "snapshot_count": len(snaps),
            "days_tracked": len(distinct_days),
        })
    return moved
