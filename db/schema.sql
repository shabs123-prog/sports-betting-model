CREATE TABLE IF NOT EXISTS tips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    game_date TEXT NOT NULL,
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
