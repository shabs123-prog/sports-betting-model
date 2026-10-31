CREATE TABLE IF NOT EXISTS tips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    game_date TEXT NOT NULL,
    game_time_local TEXT,            -- e.g. "7:30 pm", AU-local kickoff/tip-off time
    sport TEXT NOT NULL,
    matchup TEXT NOT NULL,
    market TEXT NOT NULL,
    selection TEXT NOT NULL,
    bookmaker TEXT,
    odds_decimal REAL,
    model_prob REAL,
    market_fair_prob REAL,
    edge_pct REAL,
    suggested_units REAL,
    tip_type TEXT NOT NULL,          -- 'value' or 'knowledge'
    reasoning TEXT,
    status TEXT NOT NULL DEFAULT 'open'  -- open / stale / superseded
);

CREATE TABLE IF NOT EXISTS bets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tip_id INTEGER REFERENCES tips(id),
    placed_at TEXT NOT NULL,
    sport TEXT NOT NULL,
    matchup TEXT NOT NULL,
    selection TEXT NOT NULL,
    bookmaker TEXT,
    odds_decimal REAL NOT NULL,
    stake_units REAL NOT NULL,
    stake_amount REAL NOT NULL,
    result TEXT NOT NULL DEFAULT 'pending',  -- pending / win / loss / push
    profit_units REAL,
    profit_amount REAL,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS elo_ratings (
    sport TEXT NOT NULL,
    team TEXT NOT NULL,
    rating REAL NOT NULL,
    last_updated TEXT NOT NULL,
    PRIMARY KEY (sport, team)
);

-- One row per (game, market, outcome) per pipeline run -- the consensus
-- point/fair-probability as it stood at that moment, regardless of whether
-- it was tip-worthy that day. Accumulating these over days/weeks is what
-- lets a future run detect a line actually moving (e.g. a total posted at
-- 192.5 drifting to 190.5), rather than just comparing prices at one
-- instant. Empty for the first couple of weeks after this table existed --
-- that's expected, not a bug, there's no history to show yet.
CREATE TABLE IF NOT EXISTS line_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at TEXT NOT NULL,
    sport TEXT NOT NULL,
    matchup TEXT NOT NULL,
    game_date TEXT NOT NULL,
    market TEXT NOT NULL,
    outcome TEXT NOT NULL,
    point REAL,
    fair_prob REAL NOT NULL,
    fair_prob_source TEXT NOT NULL,
    -- Added 2026-10-31 for closing-line-value tracking ("are we beating
    -- the closing line by ~6%+ on ~55%+ of bets") -- fair_prob/point alone
    -- can't answer that, CLV is about the actual PRICE you could get vs
    -- the price right before the game, at the bookmakers you actually
    -- hold. NULL on rows recorded before this column existed.
    best_price REAL,
    best_bookmaker TEXT
);
CREATE INDEX IF NOT EXISTS idx_line_history_line
    ON line_history (sport, matchup, game_date, market, outcome);
