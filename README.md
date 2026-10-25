# Sports Betting Model

A value-betting tool: pulls live odds across AU bookmakers, finds prices that
disagree with the rest of the market, suggests a stake in units, and tracks
your results. **NBA** (with Elo model context + ball-knowledge notes) and
**EPL/NBL/NRL/AFL/BBL** (devig only, for now) work end-to-end, across
moneyline, spreads/handicap, and totals markets. NFL and Australian horse
racing slot into the same pattern later (see "Extending to other sports").

**Only recommends bookmakers you can actually bet at.** `config.USER_BOOKMAKERS`
restricts every tip's "best price" to accounts you hold — the fair-probability
consensus still uses every AU bookmaker The Odds API returns (broader = more
accurate), but it'll never tell you to bet somewhere you don't have an
account. **Bet365 and Dabble do not appear in The Odds API's data at all**
(confirmed by testing, not assumed) — this tool literally cannot price or
recommend bets at either. Run `python -m scripts.list_bookmakers` any time to
re-check which bookmakers are currently in the data and update the list in
`config.py` if your accounts change.

## How it finds "value"

Two signals, reported together on every NBA tip (EPL gets the first one
only, for now — see below):

1. **Devig edge (primary signal, drives every tip).** Every bookmaker bakes
   a margin ("vig") into their odds. `models/devig.py` strips that out
   per-bookmaker, then `models/edge.py` averages across every AU bookmaker
   pricing the game to get a no-vig *consensus* probability. If one
   bookmaker's price is better than that consensus implies, that's an edge —
   and it doesn't depend on your own model being right. This is the same
   idea behind tools like OddsJam/Outlier/your friend's "Lineboard": find the
   bookmaker who disagrees with the rest of the market. Works the same way
   for spreads and totals, except the comparison only happens between books
   quoting the *same* line (`odds_api.consensus_points` picks the line most
   books agree on; a -4.5 price is never devigged against a -6.5 one).
2. **Elo model context (NBA only, supporting signal — never sizes a stake).**
   `models/elo.py` is a margin-of-victory Elo rating system (the approach
   FiveThirtyEight popularised for NBA), trained on this season's results.
   It's reported alongside the devig numbers on h2h tips as a sanity check —
   "model roughly agrees" or "model disagrees sharply, treat as noise." It
   does **not** gate or size anything; an earlier version of this code let a
   noisy single-season Elo number override the market consensus, which
   would've pushed max-size bets on model error. Elo doesn't predict spread/
   total outcomes, so it's h2h-only.

Every value tip gets two independent labels:

- **Edge tier** (`models/edge.tier_label`) — Strong / Moderate / Lean, how
  big the devig edge is.
- **Risk** (`models/risk.risk_label`) — Not Risky / Neutral / Risky, based on
  how likely the pick is to actually win (favourite vs underdog), regardless
  of edge size.

The dashboard groups Today's Tips by sport, then by risk — so you can see
"safe anchors" vs "risky longshots" the way a tipping app typically lays it
out. Stake sizing (`models/kelly.py`) uses fractional Kelly — default 30% of
full Kelly — capped at **3 units max per bet**, so weaker edges naturally
size down instead of being hidden entirely.

## "Ball knowledge" tips (NBA only so far)

`knowledge/situational.py` is a separate rules engine, not tied to a priced
edge. From schedule + results alone it flags things a sharp bettor would
notice by hand: back-to-backs, rest advantages, win/loss streaks, and teams
that are unusually bad at home or strong on the road. These get attached as
extra reasoning on value tips, and also show up on their own as a daily
"things to know" feed even when nothing is mispriced. This needs a results
history to work from (currently only wired up for NBA via balldontlie — see
`config.SPORT_CONFIG`'s `has_history` flag), so EPL doesn't have it yet. It's
deliberately simple in v1 — obvious next upgrades are wiring in an
injury-news feed and a results source for EPL (football-data.org is the
usual free option).

## Bonus Converter (matched betting)

A separate tab, unrelated to the value-tip pipeline: turns bonus/free bet
money into cash. Back the bonus bet at the bookmaker offering it (e.g.
Ladbrokes), lay the opposite outcome on an exchange (Betfair) at odds you
enter by hand, and `models/matched_betting.py` works out the lay stake that
equalizes both outcomes — so you end up with close to the same result either
way instead of just betting the bonus outright and hoping. Handles both
qualifying bets (your own money, typically a small cost to unlock the real
bonus) and free/SNR bets (stake not returned, where you're extracting cash
from the bonus itself — usually 60-80% of it depending on the odds and
exchange commission). Pure math, no live odds pulled — you type in the two
prices from your own apps.

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
  month. This is what gives you real AU bookmaker odds (Sportsbet, TAB,
  Neds, Ladbrokes, Unibet, Betfair, etc.) to compare, for every sport.
- **balldontlie** — https://balldontlie.io — free tier, used for NBA
  schedules and final scores to train the Elo ratings and ball-knowledge
  notes (`stats.nba.com` wasn't reachable from this sandboxed environment —
  balldontlie is the workaround). Not needed for EPL.

Then run the dashboard:

```bash
streamlit run dashboard/app.py
```

Open http://localhost:8501, go to **Run Pipeline**, pick a sport, and run
it. Run it once a day per sport (morning is fine — odds move continuously,
but most of the move happens close to kickoff/tip-off).

To run it without the dashboard (e.g. for a cron job):

```bash
python3 -m scripts.run_daily nba
python3 -m scripts.run_daily epl
```

See [DEPLOY.md](DEPLOY.md) for getting this hosted so you can check it from
your phone.

## Using it day to day

1. **Today's Tips** — grouped by sport, then by risk (Not Risky / Neutral /
   Risky). Each tip shows pick, bookmaker, price, edge, suggested units, and
   a reasoning string with the edge tier + risk label. Ball-knowledge notes
   (NBA) listed separately below.
2. **Bet Tracker** — log what you actually bet (can link to a tip or enter
   manually), then mark it Win/Loss/Push once it settles.
3. **Performance** — win rate, ROI, cumulative units over time, broken down
   by sport.
4. **Settings** — bankroll, unit size, Kelly fraction, max units per bet
   (default 3), minimum edge threshold to bother flagging a tip.

## Extending to other sports

The pattern in `tips/generate_tips.py`: pull finished games (if the sport
has a history source) → train Elo → pull live odds for every configured
market in one call → devig + compare at matching lines → write tips. To add
a new sport, add an entry to `config.SPORT_CONFIG`:

```python
"nfl": {"odds_key": "americanfootball_nfl", "has_history": False, "markets": ["h2h", "spreads", "totals"]},
```

That alone gets you devig value tips with zero new accounts (The Odds API
covers most sports). To get Elo context + ball-knowledge notes too, you'd
also need a results-history data source for that sport (NFL: nfl_data_py or
an odds-API-adjacent scores feed) and set `has_history: True`.

Australian horse racing is a different shape of problem — no Elo-style team
rating, instead form/speed-figure based probability per runner, and data
typically comes from the Betfair Exchange API or Racing Australia / Punting
Form rather than The Odds API. Worth treating as its own module
(`models/horses.py` + a dedicated data client) rather than forcing it through
the team-sport pipeline.

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
