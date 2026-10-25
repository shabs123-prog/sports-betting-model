from __future__ import annotations
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from config import DB_PATH

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


def insert_tip(tip: dict) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO tips
               (created_at, game_date, sport, matchup, market, selection, bookmaker,
                odds_decimal, model_prob, market_fair_prob, edge_pct, suggested_units,
                tip_type, reasoning, status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                now_iso(),
                tip["game_date"],
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
    """Inclusive date range, e.g. for a 'this week' view spanning several game dates."""
    query = "SELECT * FROM tips WHERE game_date >= ? AND game_date <= ?"
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
