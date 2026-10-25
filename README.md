# Sports Betting Model

A value-betting tool: pulls live odds across AU bookmakers, runs a model to
estimate "true" win probability, flags bets where the price is better than
fair, suggests a stake in units, and tracks your results. v1 covers **NBA**
end-to-end. EPL, NFL and Australian horse racing slot into the same pattern
later (see "Extending to other sports" below).

## How it finds "value"

Two independent signals, both reported on every tip:

1. **Devig edge (primary signal).** Every bookmaker bakes a margin ("vig")
   into their odds. `models/devig.py` strips that out per-bookmaker, then
   `models/edge.py` averages across every AU bookmaker pricing the game to
   get a no-vig *consensus* probability. If one bookmaker's price is better
   than that consensus implies, that's an edge — and it doesn't depend on
   your own model being right. This is the same idea behind tools like
   OddsJam/Outlier: find the bookmaker who disagrees with the market.
2. **Elo model edge (secondary signal).** `models/elo.py` is a margin-of-
   victory Elo rating system (the approach FiveThirtyEight popularised for
   NBA), trained on the last ~2 seasons of results. It gives a second,
   independent probability estimate. Expect this to be noisy early in a
   season — it needs games to calibrate.

Stake sizing (`models/kelly.py`) uses fractional Kelly — default 30% of full
Kelly — capped at a max units-per-bet, so one or two aggressive numbers can't
blow up your bankroll. All of this is configurable in the dashboard's
Settings tab.

## "Ball knowledge" tips

`knowledge/situational.py` is a separate rules engine, not tied to a priced
edge. From schedule + results alone it flags things a sharp bettor would
notice by hand: back-to-backs, rest advantages, win/loss streaks, and teams
that are unusually bad at home or strong on the road. These get attached as
extra reasoning on value tips, and also show up on their own as a daily
"things to know" feed even when nothing is mispriced. It's deliberately
simple in v1 — the obvious next upgrade is wiring in an injury-news feed
(there's a comment marking where to add it).

## Setup

```bash
cd ~/sports-betting-model
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

You need two free API keys — sign up yourself (not something I can do for
you), then paste them into `.env`:

- **The Odds API** — https://the-odds-api.com — free tier is 500 requests/
  month, which is plenty for one sport once a day. This is what gives you
  real AU bookmaker odds (Sportsbet, TAB, Neds, Ladbrokes, Unibet, Betfair,
  etc.) to compare.
- **balldontlie** — https://balldontlie.io — free tier, used for NBA
  schedules and final scores to train the Elo ratings (`stats.nba.com`
  wasn't reachable from this sandboxed environment — balldontlie is the
  workaround).

Then run the dashboard:

```bash
streamlit run dashboard/app.py
```

Open http://localhost:8501, go to **Run Pipeline** → Run pipeline now. It'll
pull odds + schedule data and populate **Today's Tips**. Run it once a day
(morning is fine — The Odds API updates lines continuously, but NBA lines
don't move dramatically until close to tip-off).

To run it without the dashboard (e.g. for a cron job):

```bash
python3 -m scripts.run_daily nba
```

## Using it day to day

1. **Today's Tips** — value tips (pick, bookmaker, price, edge %, suggested
   units, reasoning) plus standalone ball-knowledge notes.
2. **Bet Tracker** — log what you actually bet (can link to a tip or enter
   manually), then mark it Win/Loss/Push once it settles.
3. **Performance** — win rate, ROI, cumulative units over time, broken down
   by sport.
4. **Settings** — bankroll, unit size, Kelly fraction, max units per bet,
   minimum edge threshold to bother flagging a tip.

## Extending to other sports

The pattern in `tips/generate_tips.py` is: pull finished games → train a
model → pull live odds → devig + compare → write tips. To add EPL or NFL:

- Add the sport key to `config.SPORT_KEYS` (The Odds API uses
  `soccer_epl`, `americanfootball_nfl`).
- Add a data source for finished games (EPL: football-data.org is free and
  simple; NFL: nfl_data_py or an odds-API-adjacent results feed).
- EPL has draws, so the Elo model needs a draw-aware win/draw/loss
  probability function instead of binary win probability — the devig side
  (`models/edge.py`) already handles 3-way markets fine as-is.
- Everything else (devig, Kelly, dashboard, bet tracking) is sport-agnostic
  and will work unchanged once `sport` is passed through.

Australian horse racing is a different shape of problem — no Elo-style team
rating, instead form/speed-figure based probability per runner, and data
typically comes from the Betfair Exchange API or Racing Australia / Punting
Form rather than The Odds API. Worth treating as its own module
(`models/horses.py` + a dedicated data client) rather than forcing it through
the team-sport pipeline — happy to build that next once NBA is proven out.

## Responsible gambling

This is decision support, not a guarantee — "value" here means positive
expected value over a long run of similarly-priced bets, not that any single
bet will win. The Elo model is especially unreliable early in a season.
Only bet what you can afford to lose, keep the unit size honest relative to
a bankroll you've actually set aside for this, and don't chase losses by
overriding the suggested stake. If betting stops being fun or you're
struggling to stick to your own limits, Gambling Help Online
(https://www.gamblinghelponline.org.au, 1800 858 858) is free and
confidential.
