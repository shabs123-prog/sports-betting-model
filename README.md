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

Each sport tab groups bets by risk — so you can see "safe anchors" vs
"risky longshots" the way a tipping app typically lays it out. Stake sizing
is a fixed unit count per edge tier (Strong/Moderate/Lean → configurable
units, default 2.5/1.75/1.0), capped by a hard max-units-per-bet setting —
rounder, more intuitive numbers than a continuously-scaled Kelly fraction,
while still scaling with how confident the edge is.
`models/kelly.py`'s actual Kelly-criterion math still exists as a tested
utility if you want to switch back to it, just isn't wired into the default
pipeline.

## "Ball knowledge" tips (NBA, EPL, NBL, AFL)

`knowledge/situational.py` is a separate rules engine, not tied to a priced
edge. From schedule + results alone it flags things a sharp bettor would
notice by hand: back-to-backs, rest advantages, win/loss streaks, and teams
that are unusually bad at home or strong on the road. These get attached as
extra reasoning on value tips, and also show up on their own as a daily
"things to know" feed even when nothing is mispriced. Needs a results-history
source to work from -- `data/espn.py` (ESPN's public site API, no key
needed) covers NBA/EPL/NBL/AFL (see `config.SPORT_CONFIG`'s `has_history`
flag). NRL isn't on ESPN's public API (checked, 404s); cricket uses a
different ESPN API shape not wired up yet. It's deliberately simple in v1 —
the obvious next upgrade is wiring in an injury-news feed.

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

You need one free API key — sign up yourself (not something I can do for
you), then paste it into `.env`:

- **The Odds API** — https://the-odds-api.com — free tier is 500 requests/
  month. This is what gives you real AU bookmaker odds (Sportsbet, TAB,
  Neds, Ladbrokes, Unibet, Betfair, etc.) to compare, for every sport.

That's it for keys. Results history (for Elo + ball-knowledge notes) comes
from ESPN's public site API (`data/espn.py`) — no signup, no key.

Then run the dashboard:

```bash
streamlit run dashboard/app.py
```

Open http://localhost:8501 — NBA/EPL/NFL refresh themselves automatically
(once a day, on first load). For other sports, open that sport's tab and hit
**Refresh**.

To run it without the dashboard (e.g. for a cron job):

```bash
python3 -m scripts.run_daily nba
python3 -m scripts.run_daily epl
```

See [DEPLOY.md](DEPLOY.md) for getting this hosted so you can check it from
your phone.

## Using it day to day

1. **This Week** — best picks across every sport for the next 7 days, not
   just today/tonight — a few "standout" Line/Handicap picks in the 1.7-2.2
   range pulled out up top, everything else below.
2. **One tab per sport** (NBA, EPL, NBL, NFL, NRL, AFL, BBL) — that sport's
   Value Bets (pick, bookmaker, price, edge, units, risk) and Ball Knowledge
   notes together, looking ~2 weeks ahead, with a manual Refresh button.
3. **Horses** — placeholder for now, see "Extending to other sports."
4. **Bonus Converter** — see above.
5. **Bet Tracker** — log what you actually bet (can link to a tip or enter
   manually), then mark it Win/Loss/Push once it settles.
6. **Performance** — win rate, ROI, cumulative units over time, broken down
   by sport.
7. **Settings** — bankroll, unit size, per-tier unit counts, max units per
   bet (default 3), minimum edge threshold to bother flagging a tip.

## Extending to other sports

The pattern in `tips/generate_tips.py`: pull finished games (if the sport
has a history source) → train Elo → pull live odds for every configured
market in one call → devig + compare at matching lines → write tips. To add
a new sport, add an entry to `config.SPORT_CONFIG`:

```python
"nfl": {"odds_key": "americanfootball_nfl", "has_history": False, "markets": ["h2h", "spreads", "totals"]},
```

That alone gets you devig value tips with zero new accounts (The Odds API
covers most sports). To get Elo context + ball-knowledge notes too, check if
ESPN's public site API covers it first (`data/espn.py` -- try
`site.api.espn.com/apis/site/v2/sports/<espn_sport>/<espn_league>/teams` for
the sport/league slug, same pattern as NBA/EPL/NBL/AFL), add
`espn_sport`/`espn_league` to the config entry, work out that league's
season-param convention (see `_espn_season_years` in
`tips/generate_tips.py` -- it differs per league, verify empirically, don't
guess), and set `has_history: True`. NFL is almost certainly on ESPN's API
(`espn_sport="football"`, `espn_league="nfl"`) but untested here.

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
